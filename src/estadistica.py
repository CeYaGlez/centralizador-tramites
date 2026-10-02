"""Estadística descriptiva y detección de días atípicos sobre la base ya limpia.

Se usa IQR (rango intercuartil) y no desviación estándar porque el volumen diario es sesgado a la
derecha y un solo pico inflaría la desviación y se "escondería" a sí mismo.

Uso:  python -m src.estadistica
"""
import sqlite3
from pathlib import Path

import pandas as pd

DB = Path(__file__).resolve().parent.parent / "data" / "tramites.db"


def conectar(db: Path = DB) -> sqlite3.Connection:
    return sqlite3.connect(db, check_same_thread=False)


def volumen_diario(con) -> pd.DataFrame:
    df = pd.read_sql("SELECT fecha, total FROM v_tramites_por_dia ORDER BY fecha", con)
    df["fecha"] = pd.to_datetime(df["fecha"])
    return df


def resumen_descriptivo(serie: pd.Series) -> pd.Series:
    """Media, mediana, desviación, cuartiles, mín/máx y sesgo de una serie numérica."""
    q1, q3 = serie.quantile(.25), serie.quantile(.75)
    return pd.Series({
        "dias": int(serie.count()),
        "media": round(serie.mean(), 2),
        "mediana": serie.median(),
        "desv_est": round(serie.std(), 2),
        "q1": q1, "q3": q3,
        "minimo": serie.min(), "maximo": serie.max(),
        "sesgo": round(serie.skew(), 2),
    })


def limites_iqr(serie: pd.Series, k: float = 1.5):
    q1, q3 = serie.quantile(.25), serie.quantile(.75)
    iqr = q3 - q1
    return q1 - k * iqr, q3 + k * iqr


def dias_atipicos(df_diario: pd.DataFrame, k: float = 1.5) -> pd.DataFrame:
    """Días cuyo volumen cae fuera de [Q1 - k*IQR, Q3 + k*IQR]."""
    inf, sup = limites_iqr(df_diario["total"], k)
    out = df_diario[(df_diario["total"] < inf) | (df_diario["total"] > sup)].copy()
    out["limite_inferior"], out["limite_superior"] = round(inf, 1), round(sup, 1)
    out["tipo"] = out["total"].apply(lambda t: "alto" if t > sup else "bajo")
    return out.sort_values("total", ascending=False).reset_index(drop=True)


def causa_probable(con, fecha: str) -> pd.DataFrame:
    """Para un día atípico: qué trámite y qué oficina concentran el volumen."""
    return pd.read_sql(
        """SELECT oficina, tramite, COUNT(*) AS total
           FROM v_registro WHERE fecha = ? GROUP BY oficina, tramite ORDER BY total DESC LIMIT 5""",
        con, params=(fecha,))


def volumen_por_dia_semana(df_diario: pd.DataFrame) -> pd.DataFrame:
    nombres = ["lunes", "martes", "miércoles", "jueves", "viernes", "sábado", "domingo"]
    d = df_diario.copy()
    d["dia_semana"] = d["fecha"].dt.dayofweek
    r = d.groupby("dia_semana")["total"].agg(["mean", "median", "count"]).round(2).reset_index()
    r["dia"] = r["dia_semana"].map(lambda i: nombres[i])
    return r[["dia", "mean", "median", "count"]].rename(columns={"mean": "promedio", "median": "mediana",
                                                               "count": "dias"})


def carga_por_encargado(con) -> pd.DataFrame:
    """Total por encargado y su z-score respecto al promedio del equipo (sin 'Sin asignar')."""
    df = pd.read_sql("SELECT encargado, SUM(total) AS total, SUM(concluidos) AS concluidos "
                     "FROM v_carga_por_encargado WHERE encargado <> 'Sin asignar' GROUP BY encargado", con)
    df["pct_concluido"] = (100 * df["concluidos"] / df["total"]).round(1)
    df["z_score"] = ((df["total"] - df["total"].mean()) / df["total"].std()).round(2)
    return df.sort_values("total", ascending=False).reset_index(drop=True)


if __name__ == "__main__":
    con = conectar()
    diario = volumen_diario(con)
    print("== Resumen del volumen diario ==")
    print(resumen_descriptivo(diario["total"]).to_string())
    print("\n== Días atípicos (IQR) ==")
    at = dias_atipicos(diario)
    print(at.to_string(index=False))
    if not at.empty:
        f = at.iloc[0]["fecha"].strftime("%Y-%m-%d")
        print(f"\n== ¿Qué pasó el {f}? ==")
        print(causa_probable(con, f).to_string(index=False))
    print("\n== Carga por encargado (z-score vs equipo) ==")
    print(carga_por_encargado(con).to_string(index=False))
