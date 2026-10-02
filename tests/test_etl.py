import datetime as dt
import sqlite3

import pandas as pd
import pytest

from src import etl, generar_exceles


# ----------------------------------------------------------------------------- limpiadores
@pytest.mark.parametrize("crudo,esperado", [
    ("TR-2026-00045", "TR-2026-00045"),
    ("TR2026-45", "TR-2026-00045"),
    ("tr-2026-45 ", "TR-2026-00045"),
    (None, None),
    ("sin numero", None),
])
def test_limpiar_folio(crudo, esperado):
    assert etl.limpiar_folio(crudo) == esperado


@pytest.mark.parametrize("valor,kwargs,esperado", [
    (dt.datetime(2026, 3, 4), {}, dt.date(2026, 3, 4)),
    (pd.Timestamp("2026-03-04"), {}, dt.date(2026, 3, 4)),
    ("04/03/2026", {}, dt.date(2026, 3, 4)),          # día/mes/año, NO mes/día
    ("12-abr-2026", {}, dt.date(2026, 4, 12)),
    ("12 de abril de 2026", {}, dt.date(2026, 4, 12)),
    ("2026-03-04", {}, dt.date(2026, 3, 4)),
    (46113, {}, dt.date(2026, 4, 1)),                   # serial de Excel
    (None, dict(dia=5, mes=1, anio=2026), dt.date(2026, 1, 5)),
    ("31/02/2026", {}, None),                           # fecha imposible
    (None, {}, None),
    ("hola", {}, None),
])
def test_parsear_fecha(valor, kwargs, esperado):
    assert etl.parsear_fecha(valor, **kwargs) == esperado


@pytest.mark.parametrize("crudo,canon", [
    ("ACTA DE NACIMIENTO", "Acta de nacimiento"),
    ("Acta nacimiento.", "Acta de nacimiento"),
    ("Const. de residencia", "Constancia de residencia"),
    ("Permiso de construccion", "Permiso de construcción"),
    ("Predial", "Pago de predial"),
])
def test_tramite_alias(crudo, canon):
    assert etl.canonizar_tramite(crudo) == (canon, False)


def test_tramite_typo_se_corrige_por_similitud():
    assert etl.canonizar_tramite("Pago de predal") == ("Pago de predial", True)


def test_tramite_desconocido_no_se_inventa():
    assert etl.canonizar_tramite("Cambio de nombre de calle")[0] is None


@pytest.mark.parametrize("crudo,canon", [
    ("ANA LOPEZ RUIZ", "Ana López Ruiz"),
    ("A. López", "Ana López Ruiz"),
    ("lucia hernandez", "Lucía Hernández Soto"),
    (None, etl.SIN_ASIGNAR),
    ("Pedro Desconocido", etl.SIN_ASIGNAR),
])
def test_encargado(crudo, canon):
    assert etl.canonizar_encargado(crudo) == canon


def test_estatus():
    assert etl.canonizar_estatus("Concluído") == "Concluido"
    assert etl.canonizar_estatus("Cancelado") == "Rechazado"
    assert etl.canonizar_estatus(None) == etl.SIN_ESTATUS


def test_seudonimo_estable_y_sin_nombre():
    a, b = etl.seudonimizar("María García Pérez"), etl.seudonimizar("MARIA GARCIA PEREZ ")
    assert a == b and "garcia" not in a.lower()
    assert etl.seudonimizar(None) is None


def test_oficina_por_archivo():
    assert etl.oficina_de_archivo("registro_oficina_norte.xlsx") == "Norte"
    assert etl.oficina_de_archivo("Oficina_Sur_ventanilla.xlsx") == "Sur"
    assert etl.oficina_de_archivo("Tramites_Ene-Mar.xlsx") == "Centro"


def test_duplicado_gana_el_mas_completo():
    base = dict(folio="TR-2026-00001", fecha="2026-01-05", tramite="Pago de predial", oficina="Centro",
                archivo_origen="a.xlsx", hoja_origen="H", fila=2, corregido=False)
    pobre = dict(base, encargado=etl.SIN_ASIGNAR, estatus=etl.SIN_ESTATUS, ciudadano_hash=None)
    rico = dict(base, encargado="Ana López Ruiz", estatus="Concluido", ciudadano_hash="abc", archivo_origen="b.xlsx")
    ganadores, descartes = etl.resolver_duplicados([pobre, rico])
    assert len(ganadores) == 1 and ganadores[0]["archivo_origen"] == "b.xlsx"
    assert len(descartes) == 1 and "duplicado" in descartes[0]["motivo"]


def test_duplicado_con_datos_distintos_avisa():
    base = dict(folio="TR-2026-00001", tramite="Pago de predial", oficina="Centro", encargado="Ana López Ruiz",
                estatus="Concluido", ciudadano_hash="x", archivo_origen="a.xlsx", hoja_origen="H", fila=2,
                corregido=False)
    _, descartes = etl.resolver_duplicados([dict(base, fecha="2026-01-05"), dict(base, fecha="2026-01-06")])
    assert "OJO" in descartes[0]["motivo"]


# ----------------------------------------------------------------------------- integración
@pytest.fixture(scope="module")
def base(tmp_path_factory):
    raw = tmp_path_factory.mktemp("raw")
    db = tmp_path_factory.mktemp("db") / "t.db"
    _, verdaderos = generar_exceles.generar(raw)
    resumen = etl.ejecutar(raw, db, verbose=False)
    con = sqlite3.connect(db)
    yield con, resumen, verdaderos
    con.close()


def test_se_recuperan_todos_los_registros_verdaderos(base):
    con, resumen, verdaderos = base
    assert resumen["cargadas"] == verdaderos  # sin pérdidas ni duplicados colados


def test_folios_unicos(base):
    con, *_ = base
    total, distintos = con.execute("SELECT COUNT(*), COUNT(DISTINCT folio) FROM registro").fetchone()
    assert total == distintos


def test_leidas_igual_cargadas_mas_rechazadas(base):
    _, resumen, _ = base
    assert resumen["leidas"] == resumen["cargadas"] + resumen["rechazadas"]


def test_catalogos_limpios(base):
    con, *_ = base
    tramites = {r[0] for r in con.execute("SELECT nombre FROM tramite_tipo")}
    assert tramites == set(etl.CATALOGO_TRAMITES)
    encargados = {r[0] for r in con.execute("SELECT nombre FROM encargado")}
    assert encargados <= set(etl.CATALOGO_ENCARGADOS) | {etl.SIN_ASIGNAR}


def test_fechas_dentro_de_rango(base):
    con, *_ = base
    minimo, maximo = con.execute("SELECT MIN(fecha), MAX(fecha) FROM registro").fetchone()
    assert dt.date.fromisoformat(minimo) >= etl.FECHA_MIN and dt.date.fromisoformat(maximo) <= etl.FECHA_MAX


def test_no_se_guardan_nombres_de_ciudadanos(base):
    con, *_ = base
    hashes = [r[0] for r in con.execute("SELECT ciudadano_hash FROM registro WHERE ciudadano_hash IS NOT NULL")]
    assert all(len(h) == 12 and h.isalnum() for h in hashes)


def test_rechazados_tienen_motivo(base):
    con, *_ = base
    assert con.execute("SELECT COUNT(*) FROM rechazado WHERE motivo IS NULL OR motivo = ''").fetchone()[0] == 0
    assert con.execute("SELECT COUNT(*) FROM rechazado WHERE motivo LIKE '%fuera de rango%'").fetchone()[0] >= 1
