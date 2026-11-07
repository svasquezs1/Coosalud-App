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


def alias_columnas_por_defecto() -> list[dict]:
    """Nombre que trae el archivo -> nombre que usa la aplicación. Es una tabla editable (datos/alias_columnas.csv
    y pantalla del paso 1) para cuando el nivel nacional cambie un nombre de columna."""
    return [dict(origen=r["NOMBRE_EN_EL_ARCHIVO"].strip(), destino=r["NOMBRE_EN_LA_APLICACION"].strip())
            for r in _leer_csv("alias_columnas.csv") if r.get("NOMBRE_EN_EL_ARCHIVO", "").strip()]


def prestadores_por_defecto() -> list[dict]:
    """Archivos por prestador y servicio (recuperación, P Y M, droguería...) tomados de las consultas del área.
    Cada fila dice qué afiliados entran al archivo; varias filas con el mismo ARCHIVO se suman.
    ADICIONAL = el archivo se entrega además de los de municipio e IPS (no los reemplaza)."""
    return [dict(archivo=r["ARCHIVO"].strip(), dep=r["DEPARTAMENTO"].strip(), mun=r["MUNICIPIO"].strip(),
                 reg=(r["REGIMEN"].strip().upper() or "*"), ips=r["IPS_INCLUIDAS"].strip() or "*",
                 sin_ips=r["INCLUIR_SIN_IPS"].strip().upper() in ("SI", "SÍ", "S", "1", "X"),
                 forzada=r["IPS_FORZADA"].strip(), adicional=r.get("ADICIONAL", "").strip().upper() in ("SI", "SÍ", "S", "1", "X"),
                 nota=r.get("NOTA", "").strip())
            for r in _leer_csv("archivos_prestadores.csv") if r.get("ARCHIVO", "").strip()]


# Llaves con las que se puede hacer el cruce contra BDUA (HU-24).
LLAVES = {"tipo_numero": "Tipo y número de documento (recomendada)",
          "solo_numero": "Solo el número de documento (como las consultas actuales)",
          "afl_id": "AFL_ID_BDUA"}
COLUMNA_AFL = "AFL_ID_BDUA"


def reglas_documento_por_defecto() -> list[dict]:
    """Edad mínima/máxima en años por tipo de documento, o días máximos de vida (CN). Por confirmar con el área."""
    return [dict(tipo=r["TIPO"].strip().upper(), min=r["EDAD_MINIMA"].strip(), max=r["EDAD_MAXIMA"].strip(),
                 dias=r["DIAS_MAXIMOS"].strip()) for r in _leer_csv("reglas_documento.csv")]
