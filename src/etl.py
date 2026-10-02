"""ETL: lee exceles desordenados -> normaliza -> valida -> carga a SQLite.

Reglas de negocio (todas documentadas aquí para que se puedan discutir con quien usa los exceles):
  * Fechas ambiguas se interpretan como DÍA/MES/AÑO (formato México).
  * Solo se aceptan fechas dentro de [FECHA_MIN, FECHA_MAX]; fuera de rango se rechaza (típico typo 2062).
  * Folio duplicado: se conserva la versión MÁS COMPLETA (encargado, estatus y ciudadano presentes);
    en empate gana la primera (orden alfabético de archivo y hoja). Las demás van a `rechazado` con
    motivo, y si difieren en fecha o trámite se avisa. Nada se borra en silencio.
  * Encargado: se identifica por inicial del nombre + primer apellido ("A. López" == "ANA LOPEZ RUIZ").
    Si no hay coincidencia o viene vacío -> 'Sin asignar' (el trámite SÍ se cuenta, pero queda marcado).
  * Trámite: alias exactos y, si no hay, similitud de texto (difflib, umbral 0.8). Si no se reconoce -> rechazo.
  * Ciudadano: se guarda solo un hash con sal (seudonimización); el nombre nunca llega a la base.
  * La oficina se deduce del nombre del archivo (ver OFICINAS_POR_ARCHIVO).

Uso:  python -m src.etl
"""
import datetime as dt
import difflib
import hashlib
import os
import re
import sqlite3
import unicodedata
from pathlib import Path

import pandas as pd

RAIZ = Path(__file__).resolve().parent.parent
RAW = RAIZ / "data" / "raw"
DB = RAIZ / "data" / "tramites.db"
SQL_DIR = RAIZ / "sql"

FECHA_MIN = dt.date(2026, 1, 1)
FECHA_MAX = dt.date(2026, 12, 31)
SAL = os.environ.get("TRAMITES_SAL", "demo-cambiar-en-produccion")

OFICINAS_POR_ARCHIVO = {"norte": "Norte", "sur": "Sur"}  # por defecto: Centro

# ----------------------------------------------------------------------------- catálogos
CATALOGO_TRAMITES = {
    "Acta de nacimiento": ["acta de nacimiento", "acta nacimiento"],
    "Constancia de residencia": ["constancia de residencia", "constancia residencia", "const de residencia"],
    "Licencia de funcionamiento": ["licencia de funcionamiento", "licencia funcionamiento", "licencia func"],
    "Pago de predial": ["pago de predial", "pago predial", "predial"],
    "Permiso de construcción": ["permiso de construccion", "permiso construccion"],
    "Registro de matrimonio": ["registro de matrimonio", "registro matrimonio", "reg matrimonio"],
}
CATALOGO_ENCARGADOS = [
    "Ana López Ruiz", "Carlos Mendoza Vega", "Lucía Hernández Soto",
    "Jorge Ramírez Cruz", "Marisol Torres Díaz", "Roberto Salinas Peña",
]
SIN_ASIGNAR = "Sin asignar"
CATALOGO_ESTATUS = {
    "Concluido": ["concluido", "finalizado", "terminado"],
    "En proceso": ["en proceso", "proceso"],
    "Pendiente": ["pendiente", "por revisar"],
    "Rechazado": ["rechazado", "cancelado"],
}
SIN_ESTATUS = "Sin estatus"

# alias de columnas (ya normalizados) -> nombre canónico
ALIAS_COLUMNAS = {
    "folio": {"folio", "no folio", "num folio", "numero de folio"},
    "fecha": {"fecha", "fecha de tramite"},
    "dia": {"dia"},
    "mes": {"mes"},
    "anio": {"ano", "anio"},
    "tramite": {"tramite", "tipo de tramite"},
    "encargado": {"encargado", "responsable", "atendio"},
    "ciudadano": {"ciudadano", "solicitante", "nombre del solicitante"},
    "estatus": {"estatus", "estado", "status", "resultado"},
}
MESES_ES = {"ene": 1, "feb": 2, "mar": 3, "abr": 4, "may": 5, "jun": 6,
            "jul": 7, "ago": 8, "sep": 9, "oct": 10, "nov": 11, "dic": 12}


# ----------------------------------------------------------------------------- normalización de texto
def normalizar(valor) -> str:
    """minúsculas, sin acentos, sin puntuación, espacios colapsados."""
    if valor is None or (not isinstance(valor, str) and pd.isna(valor)):
        return ""
    s = unicodedata.normalize("NFD", str(valor))
    s = "".join(c for c in s if unicodedata.category(c) != "Mn").lower()
    s = re.sub(r"[^a-z0-9]+", " ", s)
    return s.strip()


def _vacio(v) -> bool:
    return v is None or (not isinstance(v, str) and pd.isna(v)) or (isinstance(v, str) and not v.strip())


# ----------------------------------------------------------------------------- limpiadores por campo
def limpiar_folio(valor):
    """'tr-2026-45 ', 'TR2026-45', 'TR-2026-00045' -> 'TR-2026-00045'. None si no se puede."""
    if _vacio(valor):
        return None
    m = re.search(r"(\d{4})\D*?(\d+)\s*$", str(valor).upper().strip())
    if not m:
        return None
    return f"TR-{m.group(1)}-{int(m.group(2)):05d}"


def parsear_fecha(valor=None, dia=None, mes=None, anio=None):
    """Devuelve datetime.date o None. Acepta fecha real, serial de Excel, 'dd/mm/aaaa',
    '12-abr-2026', 'aaaa-mm-dd' o columnas separadas día/mes/año."""
    try:
        if not (_vacio(dia) or _vacio(mes) or _vacio(anio)):
            return dt.date(int(anio), int(mes), int(dia))
        if _vacio(valor):
            return None
        if isinstance(valor, pd.Timestamp):
            return valor.date()
        if isinstance(valor, dt.datetime):
            return valor.date()
        if isinstance(valor, dt.date):
            return valor
        if isinstance(valor, (int, float)) and not isinstance(valor, bool):
            if 30000 < valor < 80000:  # serial de Excel
                return dt.date(1899, 12, 30) + dt.timedelta(days=int(valor))
            return None
        s = str(valor).strip().lower()
        m = re.fullmatch(r"(\d{4})-(\d{1,2})-(\d{1,2})(?:[ t].*)?", s)
        if m:
            return dt.date(int(m[1]), int(m[2]), int(m[3]))
        m = re.fullmatch(r"(\d{1,2})[/-](\d{1,2})[/-](\d{4})", s)  # día/mes/año
        if m:
            return dt.date(int(m[3]), int(m[2]), int(m[1]))
        m = re.fullmatch(r"(\d{1,2})[\s/-]+(?:de\s+)?([a-záéíóú]{3,})\.?[\s/-]+(?:de\s+)?(\d{4})", s)
        if m and normalizar(m[2])[:3] in MESES_ES:
            return dt.date(int(m[3]), MESES_ES[normalizar(m[2])[:3]], int(m[1]))
    except (ValueError, OverflowError):
        return None
    return None


_ALIAS_TRAMITE = {alias: canon for canon, als in CATALOGO_TRAMITES.items() for alias in als}


def canonizar_tramite(valor):
    """Devuelve (nombre_canónico | None, fue_corregido_por_similitud)."""
    n = normalizar(valor)
    if not n:
        return None, False
    if n in _ALIAS_TRAMITE:
        return _ALIAS_TRAMITE[n], False
    cercano = difflib.get_close_matches(n, list(_ALIAS_TRAMITE), n=1, cutoff=0.8)
    if cercano:
        return _ALIAS_TRAMITE[cercano[0]], True
    return None, False


def _clave_encargado(nombre) -> str:
    t = normalizar(nombre).split()
    return f"{t[0][0]} {t[1]}" if len(t) >= 2 else ""


_INDICE_ENCARGADOS = {_clave_encargado(n): n for n in CATALOGO_ENCARGADOS}


def canonizar_encargado(valor) -> str:
    return _INDICE_ENCARGADOS.get(_clave_encargado(valor), SIN_ASIGNAR)


_ALIAS_ESTATUS = {a: c for c, als in CATALOGO_ESTATUS.items() for a in als}


def canonizar_estatus(valor) -> str:
    return _ALIAS_ESTATUS.get(normalizar(valor), SIN_ESTATUS)


def seudonimizar(nombre):
    n = normalizar(nombre)
    if not n:
        return None
    return hashlib.sha256((SAL + n).encode()).hexdigest()[:12]


def oficina_de_archivo(nombre_archivo: str) -> str:
    n = normalizar(nombre_archivo)
    for clave, oficina in OFICINAS_POR_ARCHIVO.items():
        if clave in n.split():
            return oficina
    return "Centro"


# ----------------------------------------------------------------------------- lectura
def _mapear_columna(texto):
    n = normalizar(texto)
    for canon, alias in ALIAS_COLUMNAS.items():
        if n in alias:
            return canon
    return None


def leer_hoja(ruta: Path, hoja: str) -> pd.DataFrame:
    """Detecta la fila de encabezado (entre las primeras 15) y devuelve columnas canónicas
    + `fila_excel`. Ignora filas vacías y filas de TOTAL."""
    crudo = pd.read_excel(ruta, sheet_name=hoja, header=None, dtype=object)
    fila_enc = None
    for i in range(min(15, len(crudo))):
        if sum(_mapear_columna(v) is not None for v in crudo.iloc[i]) >= 4:
            fila_enc = i
            break
    if fila_enc is None:
        return pd.DataFrame()
    cols = [_mapear_columna(v) or f"_extra{j}" for j, v in enumerate(crudo.iloc[fila_enc])]
    df = crudo.iloc[fila_enc + 1:].copy()
    df.columns = cols
    df["fila_excel"] = df.index + 1
    df = df.dropna(how="all", subset=[c for c in cols if not c.startswith("_extra")])
    if "folio" in df.columns:
        df = df[~df["folio"].astype(str).str.strip().str.upper().isin(["TOTAL", "TOTALES"])]
    return df.reset_index(drop=True)


# ----------------------------------------------------------------------------- pipeline
def procesar_hoja(df, archivo, hoja):
    """Limpia una hoja. Devuelve (candidatos, rechazados). Los duplicados se resuelven después,
    cuando ya se leyeron TODOS los archivos."""
    candidatos, rechazados = [], []
    oficina = oficina_de_archivo(archivo)

    def rechazar(fila, motivo, crudo):
        rechazados.append(dict(archivo=archivo, hoja=hoja, fila=int(fila["fila_excel"]),
                               motivo=motivo, dato_crudo=str(crudo)))

    for _, f in df.iterrows():
        folio = limpiar_folio(f.get("folio"))
        if folio is None:
            rechazar(f, "folio inválido o vacío", f.get("folio")); continue

        fecha = parsear_fecha(f.get("fecha"), f.get("dia"), f.get("mes"), f.get("anio"))
        if fecha is None:
            rechazar(f, "fecha vacía o ilegible", f.get("fecha")); continue
        if not (FECHA_MIN <= fecha <= FECHA_MAX):
            rechazar(f, f"fecha fuera de rango ({fecha.isoformat()})", f.get("fecha")); continue

        tramite, corregido = canonizar_tramite(f.get("tramite"))
        if tramite is None:
            rechazar(f, "trámite no reconocido", f.get("tramite")); continue

        candidatos.append(dict(
            folio=folio, fecha=fecha.isoformat(), tramite=tramite, corregido=corregido,
            encargado=canonizar_encargado(f.get("encargado")), oficina=oficina,
            ciudadano_hash=seudonimizar(f.get("ciudadano")), estatus=canonizar_estatus(f.get("estatus")),
            archivo_origen=archivo, hoja_origen=hoja, fila=int(f["fila_excel"])))
    return candidatos, rechazados


def _completitud(r) -> int:
    return (r["encargado"] != SIN_ASIGNAR) + (r["estatus"] != SIN_ESTATUS) + (r["ciudadano_hash"] is not None)


def resolver_duplicados(candidatos):
    """Un registro por folio. Devuelve (ganadores, rechazados_por_duplicado)."""
    por_folio = {}
    for r in candidatos:  # el orden de llegada sirve de desempate
        por_folio.setdefault(r["folio"], []).append(r)
    ganadores, descartes = [], []
    for folio, grupo in por_folio.items():
        mejor = max(grupo, key=lambda r: (_completitud(r), -grupo.index(r)))
        ganadores.append(mejor)
        for r in grupo:
            if r is mejor:
                continue
            aviso = ""
            if (r["fecha"], r["tramite"]) != (mejor["fecha"], mejor["tramite"]):
                aviso = " | OJO: difiere en fecha o trámite, revisar"
            descartes.append(dict(
                archivo=r["archivo_origen"], hoja=r["hoja_origen"], fila=r["fila"],
                motivo=(f"folio duplicado (se conservó {mejor['archivo_origen']} / {mejor['hoja_origen']}, "
                        f"fila {mejor['fila']}){aviso}"),
                dato_crudo=folio))
    return ganadores, descartes


def _id_catalogo(con, tabla, nombre, cache):
    if nombre not in cache:
        cur = con.execute(f"INSERT INTO {tabla}(nombre) VALUES (?)", (nombre,))
        cache[nombre] = cur.lastrowid
    return cache[nombre]


def ejecutar(raw: Path = RAW, db: Path = DB, verbose: bool = True) -> dict:
    """Recarga completa e idempotente: borra la base y la reconstruye desde los exceles."""
    archivos = sorted(p for p in Path(raw).glob("*.xlsx") if not p.name.startswith("~$"))
    if not archivos:
        raise FileNotFoundError(f"No hay .xlsx en {raw}. Corre primero: python -m src.generar_exceles")

    candidatos, rechazados, leidas = [], [], {}
    for ruta in archivos:
        for hoja in pd.ExcelFile(ruta).sheet_names:
            df = leer_hoja(ruta, hoja)
            if df.empty:
                if verbose:
                    print(f"  (saltada, sin encabezado reconocible) {ruta.name} / {hoja}")
                continue
            leidas[(ruta.name, hoja)] = len(df)
            cand, rech = procesar_hoja(df, ruta.name, hoja)
            candidatos += cand
            rechazados += rech

    ganadores, duplicados = resolver_duplicados(candidatos)
    rechazados += duplicados
    ganadores.sort(key=lambda r: (r["fecha"], r["folio"]))

    db = Path(db)
    db.parent.mkdir(parents=True, exist_ok=True)
    if db.exists():
        db.unlink()
    con = sqlite3.connect(db)
    con.executescript((SQL_DIR / "schema.sql").read_text(encoding="utf-8"))

    caches = {"oficina": {}, "tramite_tipo": {}, "encargado": {}}
    for r in ganadores:
        con.execute(
            "INSERT INTO registro VALUES (?,?,?,?,?,?,?,?,?)",
            (r["folio"], r["fecha"],
             _id_catalogo(con, "tramite_tipo", r["tramite"], caches["tramite_tipo"]),
             _id_catalogo(con, "encargado", r["encargado"], caches["encargado"]),
             _id_catalogo(con, "oficina", r["oficina"], caches["oficina"]),
             r["ciudadano_hash"], r["estatus"], r["archivo_origen"], r["hoja_origen"]))
    con.executemany("INSERT INTO rechazado VALUES (?,?,?,?,?)",
                    [(x["archivo"], x["hoja"], x["fila"], x["motivo"], x["dato_crudo"]) for x in rechazados])

    # calidad por hoja (los conteos de corrección/vacíos son sobre lo que SÍ se cargó)
    for (archivo, hoja), n_leidas in leidas.items():
        gan = [r for r in ganadores if (r["archivo_origen"], r["hoja_origen"]) == (archivo, hoja)]
        rech = [x for x in rechazados if (x["archivo"], x["hoja"]) == (archivo, hoja)]
        dups = [x for x in rech if x["motivo"].startswith("folio duplicado")]
        con.execute("INSERT INTO calidad_etl VALUES (?,?,?,?,?,?,?,?,?)",
                    (archivo, hoja, n_leidas, len(gan), len(rech), len(dups),
                     sum(r["corregido"] for r in gan),
                     sum(r["encargado"] == SIN_ASIGNAR for r in gan),
                     sum(r["estatus"] == SIN_ESTATUS for r in gan)))
        if verbose:
            print(f"  {archivo} / {hoja}: {n_leidas} leídas -> {len(gan)} cargadas, {len(rech)} rechazadas")

    con.executescript((SQL_DIR / "vistas.sql").read_text(encoding="utf-8"))
    con.commit()
    con.close()
    return dict(leidas=sum(leidas.values()), cargadas=len(ganadores), rechazadas=len(rechazados))


if __name__ == "__main__":
    print("Cargando exceles...")
    r = ejecutar()
    print(f"\nTotal: {r['leidas']} leídas | {r['cargadas']} cargadas | {r['rechazadas']} rechazadas")
    print(f"Base lista en {DB}")
