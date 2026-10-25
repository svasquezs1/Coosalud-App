"""Valores por defecto y constantes. Lo que cambia con frecuencia (reglas de IPS y de documento)
vive en archivos CSV dentro de la carpeta ``datos`` y también se edita desde la pantalla."""
from __future__ import annotations
import csv
from pathlib import Path

DATOS = Path(__file__).resolve().parent.parent / "datos"

# Estados del nivel nacional que se consideran "vigentes" de entrada (se confirman en pantalla).
ESTADOS_VIGENTES_CONOCIDOS = [
    "VIGENTE", "VIGENTE CS", "COBERTURA INICIAL",
    "ACTIVO EN PROCESO DE TRASLADO DE SALIDA",
    "VIGENTE - PENDIENTE INICIO COBERTURA RC", "AC",
]
DEPARTAMENTOS_POR_DEFECTO = "05,17,27"

REQ_NAC = ["TIPO_IDENTIFICACION", "NUMERO_IDENTIFICACION", "ESTADO", "DEPARTAMENTO", "PORTABILIDAD",
           "DEPARTAMENTO_PORTABILIDAD", "CIUDAD_PORTABILIDAD", "REGIMEN", "IPS_ASIGNADA", "FECHA_NACIMIENTO"]
OPT_NAC = ["CELULAR", "CORREO_ELECTRONICO"]
REQ_BDUA = ["TIPO_IDENTIFICACION", "NUMERO_IDENTIFICACION", "ESTADO"]


def _leer_csv(nombre: str) -> list[dict]:
    ruta = DATOS / nombre
    if not ruta.exists():
        return []
    with open(ruta, newline="", encoding="utf-8-sig") as f:
        return list(csv.DictReader(f))


def reglas_ips_por_defecto() -> list[dict]:
    """Regla = departamento, municipio, régimen (CONTRIBUTIVO|SUBSIDIADO|*), IPS por defecto,
    otras IPS permitidas (separadas por coma) y un nombre de referencia."""
    return [dict(dep=r["DEPARTAMENTO"].strip(), city=r["MUNICIPIO"].strip(), reg=(r["REGIMEN"].strip().upper() or "*"),
                 ips=r["IPS_POR_DEFECTO"].strip(), alt=r["OTRAS_IPS_PERMITIDAS"].strip(), nombre=r["NOMBRE"].strip())
            for r in _leer_csv("reglas_ips.csv")]


def reglas_documento_por_defecto() -> list[dict]:
    """Edad mínima/máxima en años por tipo de documento, o días máximos de vida (CN). Por confirmar con el área."""
    return [dict(tipo=r["TIPO"].strip().upper(), min=r["EDAD_MINIMA"].strip(), max=r["EDAD_MAXIMA"].strip(),
                 dias=r["DIAS_MAXIMOS"].strip()) for r in _leer_csv("reglas_documento.csv")]
