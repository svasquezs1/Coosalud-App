"""Lectura directa desde SQL Server (solo SELECT). Sin usuario y clave se usa la autenticación integrada de Windows:
la conexión es la del usuario con el que corre la aplicación, así que nadie escribe credenciales.
Las bases y tablas de la EPS cambian cada mes (BD_SINERGIA_ddmmaaaa, AFI_BDUA_aaaa_mm), por eso la aplicación
consulta el catálogo del servidor y lista todas las tablas visibles para su selección manual.
Necesita el paquete pyodbc (incluido en requirements.txt) y el controlador «ODBC Driver 18 for SQL Server»; si no están,
el resto de la aplicación funciona igual con archivos."""
from __future__ import annotations
import os
import re
from dataclasses import dataclass, field

import pandas as pd

from .lectura import preparar_tabla

PARTE = re.compile(r"^[A-Za-z0-9_]+$")
SUFIJO_FECHA = re.compile(r"_(\d{2})(\d{2})(\d{4})$")      # BD_SINERGIA_09102026 (ddmmaaaa)
SUFIJO_MES = re.compile(r"_(\d{4})_(\d{2})$")              # AFI_BDUA_2026_08 (aaaa_mm)


class ErrorSQL(Exception):
    """Error pensado para mostrarse a la persona que usa la aplicación (nunca incluye la clave)."""


@dataclass
class Conexion:
    servidor: str = ""
    base: str = ""
    driver: str = "ODBC Driver 18 for SQL Server"
    usuario: str = ""                                   # vacío = autenticación integrada de Windows
    clave: str = field(default="", repr=False)
    confiar_certificado: bool = False
    segundos: int = 0                                # 0 = sin límite de tiempo en pyodbc


def desde_entorno(env=None) -> Conexion:
    """Valores iniciales de la pantalla (los define TI con variables de entorno CRUCE_SQL_*)."""
    env = os.environ if env is None else env
    g = lambda k, d="": env.get(k, d).strip()
    return Conexion(servidor=g("CRUCE_SQL_SERVIDOR"), base=g("CRUCE_SQL_BASE", g("CRUCE_SQL_BASE_NACIONAL", "SALUS")), driver=g("CRUCE_SQL_DRIVER", "ODBC Driver 18 for SQL Server"),
                    usuario=g("CRUCE_SQL_USUARIO"), clave=env.get("CRUCE_SQL_CLAVE", ""),
                    confiar_certificado=g("CRUCE_SQL_CONFIAR_CERTIFICADO").lower() in ("1", "si", "sí", "true", "yes"))


def _valor(v: str) -> str:
    return "{" + str(v).replace("}", "}}") + "}"        # escapa ; y demás caracteres especiales del texto de conexión


def cadena(c: Conexion) -> str:
    if not c.servidor.strip():
        raise ErrorSQL("Falta el nombre del servidor.")
    partes = [f"DRIVER={_valor(c.driver)}", f"SERVER={_valor(c.servidor.strip())}", f"DATABASE={_valor(c.base)}"]
    if c.usuario.strip():
        partes += [f"UID={_valor(c.usuario.strip())}", f"PWD={_valor(c.clave)}"]
    else:
        partes.append("Trusted_Connection=yes")
    partes += ["Encrypt=yes", f"TrustServerCertificate={'yes' if c.confiar_certificado else 'no'}", "ApplicationIntent=ReadOnly"]
    return ";".join(partes)


def disponible() -> bool:
    try:
        import pyodbc  # noqa: F401
        return True
    except Exception:
        return False


def _sin_clave(texto: str, c: Conexion) -> str:
    return texto.replace(c.clave, "***") if c.clave else texto


def abrir(c: Conexion):
    """Abre la conexión sin límite de tiempo. Los errores salen en español y sin datos sensibles."""
    try:
        import pyodbc
    except Exception:
        raise ErrorSQL("Falta instalar pyodbc y el controlador ODBC de SQL Server en este equipo (ver «Lectura desde SQL Server» en el README).")
    texto = cadena(c)
    try:
        return pyodbc.connect(texto, timeout=c.segundos, autocommit=True)
    except Exception as e:
        detalle = _sin_clave(str(e), c)
        if not c.usuario.strip():
            ayuda = "Se usó su usuario de Windows: confirme con TI que tiene permiso de lectura."
        else:
            ayuda = "Revise el usuario y la clave."
        raise ErrorSQL(f"No se pudo conectar a SQL Server. {ayuda} Detalle técnico: {detalle[:300]}")


def parte(nombre: str) -> str:
    if not PARTE.match(nombre or ""):
        raise ErrorSQL(f"Nombre no válido: «{nombre}». Solo letras, números y guion bajo.")
    return f"[{nombre}]"


def referencia(base: str, tabla: str) -> str:
    """«[base].[esquema].[tabla]» a partir de «esquema.tabla». Cada parte se valida: nada de lo escrito llega a la consulta sin revisar."""
    esquema, _, nombre = tabla.partition(".")
    return f"{parte(base)}.{parte(esquema)}.{parte(nombre)}"


def clave_reciente(nombre: str) -> tuple:
    m = SUFIJO_FECHA.search(nombre)
    if m:
        return (int(m.group(3)), int(m.group(2)), int(m.group(1)))
    m = SUFIJO_MES.search(nombre)
    return (int(m.group(1)), int(m.group(2)), 0) if m else (0, 0, 0)


def _familia(nombre: str) -> str:
    return SUFIJO_MES.sub("", SUFIJO_FECHA.sub("", nombre))


def ordenar(tablas: list[str]) -> list[str]:
    """De la más reciente a la más antigua (por la fecha que lleva el nombre)."""
    return sorted(tablas, key=lambda t: (tuple(-x for x in clave_reciente(t.partition(".")[2])), t))


def sugeridas(tablas: list[str]) -> list[str]:
    """La más reciente de cada familia (AFI_BDUA, AFI_EPSS42, ...): lo que casi siempre se quiere leer."""
    vistas: dict[str, str] = {}
    for t in ordenar(tablas):
        vistas.setdefault(_familia(t.partition(".")[2]), t)
    return sorted(vistas.values())


def listar_tablas(conn, base: str) -> list[str]:
    """Lista todas las tablas visibles en la base, sin filtros de nombre o fecha."""
    sql = f"""SELECT
    s.name AS Esquema,
    t.name AS Tabla,
    t.create_date AS FechaCreacion
FROM {parte(base)}.sys.tables AS t
JOIN {parte(base)}.sys.schemas AS s
    ON s.schema_id = t.schema_id
ORDER BY t.create_date, s.name, t.name"""
    try:
        cur = conn.cursor(); cur.execute(sql)
        filas = cur.fetchall()
    except Exception as e:
        raise ErrorSQL(f"No se pudieron listar las tablas de {base}: {str(e)[:300]}")
    return [f"{r[0]}.{r[1]}" for r in filas]


def leer_tabla_sql(conn, ref: str, alias: list[dict] | None = None, trozo: int = 50_000) -> tuple[pd.DataFrame, list[str]]:
    """SELECT * de una tabla, por trozos, dejado en la misma forma que un archivo cargado."""
    try:
        cur = conn.cursor(); cur.execute(f"SELECT * FROM {ref}")
        columnas = [d[0] for d in cur.description]
        partes = []
        while True:
            filas = cur.fetchmany(trozo)
            if not filas:
                break
            partes.append(pd.DataFrame([tuple(f) for f in filas], columns=columnas))
    except ErrorSQL:
        raise
    except Exception as e:
        raise ErrorSQL(f"No se pudo leer {ref}: {str(e)[:300]}")
    df = pd.concat(partes, ignore_index=True) if partes else pd.DataFrame(columns=columnas)
    return preparar_tabla(df, alias)
