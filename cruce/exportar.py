"""Genera los archivos de descarga en memoria (no se escribe nada en el disco del servidor)."""
from __future__ import annotations
import io
import pandas as pd


def a_bytes(df: pd.DataFrame, formato: str = "xlsx") -> tuple[bytes, str]:
    if formato == "csv":
        return df.to_csv(index=False).encode("utf-8-sig"), "text/csv; charset=utf-8"
    buf = io.BytesIO()
    try:
        import xlsxwriter  # noqa: F401  (más rápido para tablas grandes)
        motor = "xlsxwriter"
        opciones = {"engine_kwargs": {"options": {"strings_to_numbers": False, "in_memory": True}}}
    except ImportError:
        motor, opciones = "openpyxl", {}
    with pd.ExcelWriter(buf, engine=motor, **opciones) as w:
        (df if len(df.columns) else pd.DataFrame({"": []})).to_excel(w, sheet_name="Datos", index=False)
    return buf.getvalue(), "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
