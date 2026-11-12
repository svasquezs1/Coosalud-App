"""Lectura directa desde SQL Server (solo SELECT). Sin usuario y clave se usa la autenticación integrada de Windows:
la conexión es la del usuario con el que corre la aplicación, así que nadie escribe credenciales.
Las bases y tablas de la EPS cambian cada mes (BD_SINERGIA_ddmmaaaa, AFI_BDUA_aaaa_mm), por eso la aplicación
consulta el catálogo del servidor, lista lo que hay y propone lo más reciente.
Necesita el paquete pyodbc y el controlador «ODBC Driver 18 for SQL Server» (ver requirements-sql.txt); si no están,
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
    base_nac: str = "SALUS"
    base_bdua: str = "BDUA"
    driver: str = "ODBC Driver 18 for SQL Server"
    usuario: str = ""                                   # vacío = autenticación integrada de Windows
    clave: str = field(default="", repr=False)
    confiar_certificado: bool = False
    segundos: int = 30
    patron_nac: str = "BD[_]SINERGIA%"
    patron_bdua: str = "AFI[_]%"


def desde_entorno(env=None) -> Conexion:
    """Valores iniciales de la pantalla (los define TI con variables de entorno CRUCE_SQL_*)."""
    env = os.environ if env is None else env
    g = lambda k, d="": env.get(k, d).strip()
    return Conexion(servidor=g("CRUCE_SQL_SERVIDOR"), base_nac=g("CRUCE_SQL_BASE_NACIONAL", "SALUS"),
                    base_bdua=g("CRUCE_SQL_BASE_BDUA", "BDUA"), driver=g("CRUCE_SQL_DRIVER", "ODBC Driver 18 for SQL Server"),
                    usuario=g("CRUCE_SQL_USUARIO"), clave=env.get("CRUCE_SQL_CLAVE", ""),
                    confiar_certificado=g("CRUCE_SQL_CONFIAR_CERTIFICADO").lower() in ("1", "si", "sí", "true", "yes"),
                    patron_nac=g("CRUCE_SQL_PATRON_NACIONAL", "BD[_]SINERGIA%"), patron_bdua=g("CRUCE_SQL_PATRON_BDUA", "AFI[_]%"))


def _valor(v: str) -> str:
    return "{" + str(v).replace("}", "}}") + "}"        # escapa ; y demás caracteres especiales del texto de conexión


def cadena(c: Conexion) -> str:
    if not c.servidor.strip():
        raise ErrorSQL("Falta el nombre del servidor.")
    partes = [f"DRIVER={_valor(c.driver)}", f"SERVER={_valor(c.servidor.strip())}", f"DATABASE={_valor(c.base_nac)}"]
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
    """Abre la conexión. Los errores salen en español y sin datos sensibles."""
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


def listar_tablas(conn, base: str, patron: str) -> list[str]:
    sql = f"SELECT TABLE_SCHEMA, TABLE_NAME FROM {parte(base)}.INFORMATION_SCHEMA.TABLES WHERE TABLE_TYPE = 'BASE TABLE' AND TABLE_NAME LIKE ?"
    try:
        cur = conn.cursor(); cur.execute(sql, (patron,))
        filas = cur.fetchall()
    except Exception as e:
        raise ErrorSQL(f"No se pudieron listar las tablas de {base}: {str(e)[:300]}")
    return ordenar([f"{r[0]}.{r[1]}" for r in filas])


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
