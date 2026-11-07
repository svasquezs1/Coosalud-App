"""Lectura de archivos Excel/CSV directamente desde la memoria (nada se escribe en disco)."""
from __future__ import annotations
import io
import re
import pandas as pd

_FECHA_HORA = r"^(\d{4})-(\d{2})-(\d{2}) 00:00:00$"


def clave_columna(nombre: str) -> str:
    """Forma comparable de un encabezado: MAYÚSCULAS y espacios/guiones bajos repetidos como uno solo."""
    return re.sub(r"[\s_]+", "_", str(nombre).strip().upper())


def nombres_efectivos(originales: list[str], alias: list[dict]) -> list[str]:
    """Aplica la tabla de alias (nombre en el archivo -> nombre que usa la aplicación) a los encabezados.
    Si el archivo ya trae una columna con el nombre de destino, no se renombra (evita columnas repetidas)."""
    mapa: dict[str, str] = {}
    for a in alias or []:
        o, d = clave_columna(a.get("origen", "")), clave_columna(a.get("destino", ""))
        if o and d and o != d:
            mapa.setdefault(o, d)
    usados = {clave_columna(h) for h in originales}
    res = []
    for h in originales:
        d = mapa.get(clave_columna(h))
        if d and d not in usados:
            usados.add(d)
            res.append(d)
        else:
            res.append(h)
    return res


def renombrar(df: pd.DataFrame, originales: list[str], alias: list[dict]) -> list[str]:
    """Vuelve a poner los nombres de columna a partir de los encabezados originales (cuando cambian los alias)."""
    efectivos = nombres_efectivos(originales, alias)
    df.columns = [e.upper() for e in efectivos]
    return efectivos


def leer_tabla(nombre: str, contenido: bytes, alias: list[dict] | None = None) -> tuple[pd.DataFrame, list[str]]:
    """Devuelve (tabla con columnas en MAYÚSCULAS y todo como texto, encabezados originales del archivo).
    Los encabezados originales quedan alineados con las columnas de la tabla."""
    bruto = io.BytesIO(contenido)
    if re.search(r"\.(csv|txt)$", nombre, re.I):
        texto = contenido.decode("utf-8-sig", errors="replace")
        primera = texto.split("\n", 1)[0]
        sep = ";" if primera.count(";") > primera.count(",") else ","
        df = pd.read_csv(io.StringIO(texto), dtype=str, keep_default_na=False, sep=sep)
    else:
        df = pd.read_excel(bruto, dtype=str, keep_default_na=False, na_filter=False)
    originales = [str(c).strip() for c in df.columns]
    nuevo = ~pd.Index([c.upper() for c in originales]).duplicated()     # por si hay columnas repetidas
    df = df.loc[:, nuevo]
    originales = [o for o, k in zip(originales, nuevo) if k]
    renombrar(df, originales, alias or [])
    for c in df.columns:
        s = df[c].astype(str).str.strip()
        s = s.str.replace(_FECHA_HORA, r"\1/\2/\3", regex=True)        # fechas de Excel -> aaaa/mm/dd
        s = s.str.replace(r"^(\d+)\.0$", r"\1", regex=True)             # 3001234567.0 -> 3001234567
        df[c] = s.replace({"nan": "", "NaT": "", "None": ""})
    return df.reset_index(drop=True), originales


def columnas_faltantes(encabezados: list[str], requeridas: list[str]) -> list[str]:
    tiene = {h.upper() for h in encabezados}
    return [c for c in requeridas if c not in tiene]
