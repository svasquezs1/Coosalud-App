"""Lectura de archivos Excel/CSV directamente desde la memoria (nada se escribe en disco)."""
from __future__ import annotations
import io
import re
import pandas as pd

_FECHA_HORA = r"^(\d{4})-(\d{2})-(\d{2}) 00:00:00$"


def leer_tabla(nombre: str, contenido: bytes) -> tuple[pd.DataFrame, list[str]]:
    """Devuelve (tabla con columnas en MAYÚSCULAS y todo como texto, encabezados originales)."""
    bruto = io.BytesIO(contenido)
    if re.search(r"\.(csv|txt)$", nombre, re.I):
        texto = contenido.decode("utf-8-sig", errors="replace")
        primera = texto.split("\n", 1)[0]
        sep = ";" if primera.count(";") > primera.count(",") else ","
        df = pd.read_csv(io.StringIO(texto), dtype=str, keep_default_na=False, sep=sep)
    else:
        df = pd.read_excel(bruto, dtype=str, keep_default_na=False, na_filter=False)
    originales = [str(c).strip() for c in df.columns]
    df.columns = [c.upper() for c in originales]
    df = df.loc[:, ~pd.Index(df.columns).duplicated()]          # por si hay columnas repetidas
    for c in df.columns:
        s = df[c].astype(str).str.strip()
        s = s.str.replace(_FECHA_HORA, r"\1/\2/\3", regex=True)        # fechas de Excel -> aaaa/mm/dd
        s = s.str.replace(r"^(\d+)\.0$", r"\1", regex=True)             # 3001234567.0 -> 3001234567
        df[c] = s.replace({"nan": "", "NaT": "", "None": ""})
    return df.reset_index(drop=True), originales


def columnas_faltantes(encabezados: list[str], requeridas: list[str]) -> list[str]:
    tiene = {h.upper() for h in encabezados}
    return [c for c in requeridas if c not in tiene]
