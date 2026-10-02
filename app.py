"""Interfaz de consulta del centralizador de trámites.   Ejecutar:  streamlit run app.py"""
import io
import sqlite3
from pathlib import Path

import pandas as pd
import streamlit as st

from src import estadistica as est

DB = Path(__file__).resolve().parent / "data" / "tramites.db"

st.set_page_config(page_title="Centralizador de trámites", page_icon="📋", layout="wide")


@st.cache_resource
def conexion():
    return sqlite3.connect(DB, check_same_thread=False)


@st.cache_data
def opciones(columna: str):
    return pd.read_sql(f"SELECT DISTINCT {columna} AS v FROM v_registro ORDER BY 1", conexion())["v"].tolist()


def consultar(filtros: dict) -> pd.DataFrame:
    """Arma el WHERE con parámetros (nunca concatenando texto del usuario -> sin inyección SQL)."""
    where, params = ["fecha BETWEEN ? AND ?"], [filtros["desde"], filtros["hasta"]]
    for col in ("oficina", "tramite", "encargado", "estatus"):
        if filtros[col]:
            where.append(f"{col} IN ({','.join('?' * len(filtros[col]))})")
            params += filtros[col]
    sql = f"SELECT * FROM v_registro WHERE {' AND '.join(where)} ORDER BY fecha, folio"
    return pd.read_sql(sql, conexion(), params=params)


def a_excel(df: pd.DataFrame) -> bytes:
    buf = io.BytesIO()
    with pd.ExcelWriter(buf, engine="openpyxl") as w:
        df.to_excel(w, index=False, sheet_name="Consulta")
    return buf.getvalue()


from src import etl

if not DB.exists():
    with st.spinner("Generando base de datos por primera vez..."):
        etl.ejecutar(verbose=False)
    st.rerun()

st.title("📋 Centralizador de trámites")
st.caption("Datos de demostración 100% sintéticos. Los ciudadanos aparecen seudonimizados.")

# ------------------------------------------------------------------ filtros
rango = pd.read_sql("SELECT MIN(fecha) AS a, MAX(fecha) AS b FROM registro", conexion()).iloc[0]
with st.sidebar:
    st.header("Filtros")
    desde, hasta = st.date_input("Rango de fechas",
                                 value=(pd.to_datetime(rango["a"]).date(), pd.to_datetime(rango["b"]).date()))
    f_ofi = st.multiselect("Oficina", opciones("oficina"))
    f_tra = st.multiselect("Trámite", opciones("tramite"))
    f_enc = st.multiselect("Encargado", opciones("encargado"))
    f_est = st.multiselect("Estatus", opciones("estatus"))

df = consultar(dict(desde=str(desde), hasta=str(hasta), oficina=f_ofi, tramite=f_tra,
                    encargado=f_enc, estatus=f_est))

tab1, tab2, tab3 = st.tabs(["🔎 Consulta", "📈 Estadística", "🧹 Calidad de datos"])

# ------------------------------------------------------------------ consulta
with tab1:
    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Trámites", f"{len(df):,}")
    c2.metric("Personas distintas", f"{df['ciudadano_hash'].nunique():,}")
    c3.metric("% concluidos", f"{(100 * (df['estatus'] == 'Concluido').mean()) if len(df) else 0:.1f}%")
    c4.metric("Sin encargado", f"{(df['encargado'] == 'Sin asignar').sum():,}")

    if df.empty:
        st.info("Ningún trámite coincide con los filtros.")
    else:
        izq, der = st.columns(2)
        izq.subheader("Trámites por tipo")
        izq.bar_chart(df["tramite"].value_counts())
        der.subheader("Trámites por día")
        der.line_chart(df.groupby("fecha").size())

        st.subheader("Detalle")
        st.dataframe(df, width="stretch", hide_index=True)
        d1, d2 = st.columns(2)
        d1.download_button("⬇️ Descargar CSV", df.to_csv(index=False).encode("utf-8-sig"),
                           "consulta_tramites.csv", "text/csv")
        d2.download_button("⬇️ Descargar Excel", a_excel(df), "consulta_tramites.xlsx",
                           "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")

# ------------------------------------------------------------------ estadística
with tab2:
    diario = est.volumen_diario(conexion())
    st.subheader("Volumen diario")
    r = est.resumen_descriptivo(diario["total"])
    m1, m2, m3, m4 = st.columns(4)
    m1.metric("Promedio diario", r["media"])
    m2.metric("Mediana", r["mediana"])
    m3.metric("Desv. estándar", r["desv_est"])
    m4.metric("Máximo en un día", int(r["maximo"]))
    st.line_chart(diario.set_index("fecha")["total"])

    st.subheader("Días atípicos (método IQR)")
    at = est.dias_atipicos(diario)
    if at.empty:
        st.success("Sin días atípicos.")
    else:
        st.dataframe(at, width="stretch", hide_index=True)
        dia = st.selectbox("Ver qué lo explica", at["fecha"].dt.strftime("%Y-%m-%d"))
        st.dataframe(est.causa_probable(conexion(), dia), width="stretch", hide_index=True)

    a, b = st.columns(2)
    a.subheader("Promedio por día de la semana")
    a.dataframe(est.volumen_por_dia_semana(diario), width="stretch", hide_index=True)
    b.subheader("Carga por encargado")
    b.dataframe(est.carga_por_encargado(conexion()), width="stretch", hide_index=True)

# ------------------------------------------------------------------ calidad
with tab3:
    st.subheader("¿Qué pasó con cada archivo?")
    st.dataframe(pd.read_sql("SELECT * FROM calidad_etl", conexion()), width="stretch", hide_index=True)
    st.subheader("Filas rechazadas y por qué")
    rech = pd.read_sql("SELECT * FROM rechazado ORDER BY archivo, hoja, fila", conexion())
    st.dataframe(rech, width="stretch", hide_index=True)
    st.caption("Nada se descarta en silencio: cada fila que no entró queda aquí con su motivo para corregirla en el origen.")
