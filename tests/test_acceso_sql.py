"""Acceso por usuario de Windows y lectura desde SQL Server (con una base SQLite en memoria que hace de servidor)."""
import io
import re
import sqlite3
import sys
import types
from pathlib import Path
import pandas as pd
import pytest

from cruce import sqlserver as S
from cruce.acceso import Acceso, normalizar_usuario, usuario_remoto
from cruce.config import ESTADOS_VIGENTES_CONOCIDOS as EST

D = Path(__file__).resolve().parent.parent / "datos_prueba_ficticios"
BDUA = ["AFI_BDUA_DEMO", "AFI_EPSS42_DEMO", "AFI_ESSC24_DEMO", "AFI_EPS042_DEMO"]


@pytest.fixture()
def m():
    import app
    app.CLAVE = ""
    app.app.config["TESTING"] = True
    return app


# ------------------------------------------------------------------ acceso
def test_normalizar_usuario():
    assert normalizar_usuario("EMPRESA\\Sandra") == "Sandra" and normalizar_usuario("sandra@empresa.com") == "sandra"
    assert normalizar_usuario("  ") == ""
    a = Acceso.desde_entorno({"CRUCE_AUTH_WINDOWS": "1", "CRUCE_USUARIOS_PERMITIDOS": "EMP\\Sandra; edwin"})
    assert a.windows and a.permite("SANDRA") and a.permite("Edwin") and not a.permite("otro")
    assert Acceso.desde_entorno({}).windows is False and Acceso().permite("cualquiera")


def test_usuario_remoto_solo_de_un_servidor_de_confianza():
    a = Acceso(windows=True)
    assert usuario_remoto(a, "127.0.0.1", {"X-Remote-User": "EMP\\sandra"}, {}) == "sandra"
    assert usuario_remoto(a, "10.1.1.5", {"X-Remote-User": "EMP\\sandra"}, {}) is None          # alguien de la red no puede decir quién es
    assert usuario_remoto(a, "127.0.0.1", {}, {"REMOTE_USER": "EMP\\edwin"}) == "edwin"
    assert usuario_remoto(a, "127.0.0.1", {}, {}) is None
    assert usuario_remoto(Acceso(windows=True, cabecera="X-Usuario"), "127.0.0.1", {"X-Usuario": "ana"}, {}) == "ana"


def test_acceso_windows_en_la_aplicacion(m, monkeypatch):
    monkeypatch.setattr(m, "ACCESO", Acceso(windows=True, permitidos=frozenset({"sandra"})))
    c = m.app.test_client()
    assert c.get("/paso/1").status_code == 401                                                   # sin usuario
    r = c.get("/paso/1", headers={"X-Remote-User": "EMPRESA\\Sandra"}); h = r.get_data(as_text=True)
    assert r.status_code == 200 and "Usuario: Sandra" in h
    assert c.get("/paso/1", headers={"X-Remote-User": "EMPRESA\\otro"}).status_code == 403       # no está en la lista
    r = c.get("/paso/1", headers={"X-Remote-User": "EMPRESA\\sandra"}, environ_overrides={"REMOTE_ADDR": "10.1.1.5"})
    assert r.status_code == 401                                                                  # cabecera falsificada desde la red


def test_una_sesion_no_la_comparten_dos_personas(m, monkeypatch):
    monkeypatch.setattr(m, "ACCESO", Acceso(windows=True))
    c = m.app.test_client(); c.environ_base["HTTP_X_REMOTE_USER"] = "EMP\\sandra"
    c.post("/paso/1/subir", data={"nacional": (io.BytesIO((D / "BD_SINERGIA_DEMO.xlsx").read_bytes()), "BD_SINERGIA_DEMO.xlsx")},
           content_type="multipart/form-data")
    assert "BD_SINERGIA_DEMO.xlsx" in c.get("/paso/1").get_data(as_text=True)
    c.environ_base["HTTP_X_REMOTE_USER"] = "EMP\\otro"                                          # mismo navegador (misma cookie), otra persona
    h = c.get("/paso/1").get_data(as_text=True)
    assert "BD_SINERGIA_DEMO.xlsx" not in h and "Usuario: otro" in h


def test_en_el_propio_equipo_no_pide_nada_y_muestra_el_usuario_del_equipo(m, monkeypatch):
    monkeypatch.setattr(m, "ACCESO", Acceso()); monkeypatch.setattr(m, "USUARIO_LOCAL", "sebas")
    r = m.app.test_client().get("/paso/1", headers={"X-Remote-User": "EMP\\intruso"})           # sin modo Windows la cabecera no cuenta
    assert r.status_code == 200 and "Usuario: sebas" in r.get_data(as_text=True) and "intruso" not in r.get_data(as_text=True)


# ------------------------------------------------------------------ SQL Server: piezas
def test_cadena_de_conexion():
    integrada = S.cadena(S.Conexion(servidor="srv"))
    assert "Trusted_Connection=yes" in integrada and "UID=" not in integrada and "PWD=" not in integrada and "ApplicationIntent=ReadOnly" in integrada
    con_clave = S.cadena(S.Conexion(servidor="srv", usuario="u", clave="a;b}c"))
    assert "UID={u}" in con_clave and "PWD={a;b}}c}" in con_clave and "Trusted_Connection" not in con_clave
    with pytest.raises(S.ErrorSQL):
        S.cadena(S.Conexion(servidor=" "))
    assert S.desde_entorno({"CRUCE_SQL_SERVIDOR": "x", "CRUCE_SQL_CONFIAR_CERTIFICADO": "1"}).confiar_certificado


def test_nombres_de_tablas_se_validan():
    assert S.referencia("SALUS", "dbo.BD_SINERGIA_09102026") == "[SALUS].[dbo].[BD_SINERGIA_09102026]"
    for malo in ("dbo.x]; DROP TABLE y;--", "dbo.a b", "solo", "dbo."):
        with pytest.raises(S.ErrorSQL):
            S.referencia("SALUS", malo)
    with pytest.raises(S.ErrorSQL):
        S.referencia("SALUS]--", "dbo.t")


def test_tablas_mas_recientes_primero():
    t = ["dbo.AFI_BDUA_2026_07", "dbo.AFI_BDUA_2026_08", "dbo.AFI_EPSS42_2026_08", "dbo.BD_SINERGIA_07092026", "dbo.BD_SINERGIA_09102026"]
    assert S.ordenar(t)[0] == "dbo.BD_SINERGIA_09102026" and S.ordenar(t).index("dbo.AFI_BDUA_2026_08") < S.ordenar(t).index("dbo.AFI_BDUA_2026_07")
    assert S.sugeridas(t) == ["dbo.AFI_BDUA_2026_08", "dbo.AFI_EPSS42_2026_08", "dbo.BD_SINERGIA_09102026"]


def test_sin_pyodbc_y_errores_sin_clave(monkeypatch):
    monkeypatch.setitem(sys.modules, "pyodbc", None)                                            # como si no estuviera instalado
    assert not S.disponible()
    with pytest.raises(S.ErrorSQL, match="Falta instalar pyodbc"):
        S.abrir(S.Conexion(servidor="srv"))
    falso = types.ModuleType("pyodbc")
    def conectar(*a, **k): raise RuntimeError("Login failed for user 'u' password=secreta123")
    falso.connect = conectar
    monkeypatch.setitem(sys.modules, "pyodbc", falso)
    with pytest.raises(S.ErrorSQL) as e:
        S.abrir(S.Conexion(servidor="srv", usuario="u", clave="secreta123"))
    assert "secreta123" not in str(e.value) and "***" in str(e.value) and "usuario y la clave" in str(e.value)
    with pytest.raises(S.ErrorSQL) as e:
        S.abrir(S.Conexion(servidor="srv"))
    assert "usuario de Windows" in str(e.value)


# ------------------------------------------------------------------ SQL Server: flujo completo con una base de mentira
class _Cursor:
    def __init__(self, falsa): self.f, self.description, self._filas, self._c = falsa, None, [], None
    def execute(self, sql, params=()):
        if "INFORMATION_SCHEMA" in sql:
            base = re.search(r"FROM \[(\w+)\]\.INFORMATION_SCHEMA", sql).group(1)
            self._filas = [("dbo", t) for t in self.f.listado[base]]; self.description = [("TABLE_SCHEMA",), ("TABLE_NAME",)]
        else:
            self._c = self.f.con.execute(re.sub(r"\[\w+\]\.\[\w+\]\.", "", sql)); self.description = self._c.description   # sqlite no tiene base.esquema.tabla
    def fetchall(self): return self._filas
    def fetchmany(self, n): return self._c.fetchmany(n)


class FalsaSQL:
    """Hace de conexión pyodbc: el catálogo sale de ``listado`` y las tablas de una base SQLite en memoria."""
    def __init__(self):
        self.con = sqlite3.connect(":memory:", check_same_thread=False)
        crudo = lambda n: pd.read_excel(D / f"{n}.xlsx", dtype=str, keep_default_na=False)
        crudo("BD_SINERGIA_DEMO").to_sql("BD_SINERGIA_09102026", self.con, index=False)
        crudo("BD_SINERGIA_DEMO").head(5).to_sql("BD_SINERGIA_07092026", self.con, index=False)
        for n, nombre in zip(BDUA, ["AFI_BDUA", "AFI_EPSS42", "AFI_ESSC24", "AFI_EPS042"]):
            crudo(n).to_sql(f"{nombre}_2026_08", self.con, index=False)
        crudo(BDUA[0]).head(3).to_sql("AFI_BDUA_2026_07", self.con, index=False)
        self.listado = {"SALUS": ["BD_SINERGIA_07092026", "BD_SINERGIA_09102026"],
                        "BDUA": ["AFI_BDUA_2026_07", "AFI_BDUA_2026_08", "AFI_EPSS42_2026_08", "AFI_ESSC24_2026_08", "AFI_EPS042_2026_08"]}
    def cursor(self): return _Cursor(self)
    def close(self): pass


@pytest.fixture()
def cli(m, monkeypatch):
    falsa = FalsaSQL()
    monkeypatch.setattr(S, "abrir", lambda c: falsa); monkeypatch.setattr(S, "disponible", lambda: True)
    return m.app.test_client()


def test_leer_desde_sql_server_da_el_mismo_resultado_que_los_archivos(cli):
    h = cli.post("/paso/1/sql", data={"accion": "buscar", "servidor": "srv"}, follow_redirects=True).get_data(as_text=True)
    assert "Se encontraron 2 tablas de la base nacional y 5 tablas BDUA" in h and "BD_SINERGIA_09102026" in h
    assert re.search(r'value="dbo.BD_SINERGIA_09102026"\s+selected', h)                         # propone la más reciente
    assert 'value="dbo.AFI_BDUA_2026_08" checked' in h and 'value="dbo.AFI_BDUA_2026_07" checked' not in h
    h = cli.post("/paso/1/sql", data={"accion": "leer", "tabla_nac": "dbo.BD_SINERGIA_09102026",
                                      "tabla_bdua": ["dbo.AFI_BDUA_2026_08", "dbo.AFI_EPSS42_2026_08", "dbo.AFI_ESSC24_2026_08", "dbo.AFI_EPS042_2026_08"]},
                 follow_redirects=True).get_data(as_text=True)
    assert "Se leyeron 654 filas" in h and "BD_SINERGIA_09102026 (SQL Server)" in h and "Columnas requeridas completas" in h
    cli.post("/paso/1/continuar", data={"estado": EST, "deps": "05,17,27"})
    h = cli.post("/paso/2/ejecutar", data={"llave": "tipo_numero"}, follow_redirects=True).get_data(as_text=True)
    assert ">522<" in h and ">492<" in h                                                        # igual que con los archivos Excel


def test_sql_no_acepta_tablas_que_no_se_listaron(cli):
    cli.post("/paso/1/sql", data={"accion": "buscar", "servidor": "srv"})
    h = cli.post("/paso/1/sql", data={"accion": "leer", "tabla_nac": "dbo.x]; DROP TABLE y;--", "tabla_bdua": ["dbo.AFI_BDUA_2026_08"]},
                 follow_redirects=True).get_data(as_text=True)
    assert "Elija la tabla de la base nacional" in h and "filas" not in h.split("Elija la tabla")[1][:300]
    h = cli.post("/paso/1/sql", data={"accion": "leer", "tabla_nac": "dbo.BD_SINERGIA_09102026", "tabla_bdua": []}, follow_redirects=True).get_data(as_text=True)
    assert "al menos una tabla BDUA" in h


def test_la_clave_de_sql_no_se_guarda_ni_se_muestra(cli, m):
    h = cli.post("/paso/1/sql", data={"accion": "buscar", "servidor": "srv", "usuario": "lector", "clave": "secreta123"}, follow_redirects=True).get_data(as_text=True)
    assert "secreta123" not in h and "usuario de SQL lector" in h and "Clave de SQL (no se guarda)" in h
    assert all("secreta123" not in repr(s.sql) for s in m.almacen._d.values())
    assert "secreta123" not in cli.get("/paso/1").get_data(as_text=True)


def test_error_de_conexion_y_cambiar_conexion(m, monkeypatch):
    monkeypatch.setattr(S, "disponible", lambda: True)
    def falla(c): raise S.ErrorSQL("No se pudo conectar a SQL Server. Revise el usuario y la clave.")
    monkeypatch.setattr(S, "abrir", falla)
    c = m.app.test_client()
    h = c.post("/paso/1/sql", data={"accion": "buscar", "servidor": "srv"}, follow_redirects=True).get_data(as_text=True)
    assert "No se pudo conectar a SQL Server" in h and 'name="accion" value="buscar"' in h        # sigue en el formulario de búsqueda
    h = c.post("/paso/1/sql", data={"accion": "buscar", "servidor": ""}, follow_redirects=True).get_data(as_text=True)
    assert "No se pudo conectar" in h
    monkeypatch.setattr(S, "abrir", lambda c_: FalsaSQL())
    c.post("/paso/1/sql", data={"accion": "buscar", "servidor": "srv"})
    h = c.post("/paso/1/sql", data={"accion": "restablecer"}, follow_redirects=True).get_data(as_text=True)
    assert 'name="accion" value="buscar"' in h and 'value="leer"' not in h


def test_sin_pyodbc_la_pantalla_avisa_y_no_rompe(m, monkeypatch):
    monkeypatch.setattr(S, "disponible", lambda: False)
    h = m.app.test_client().get("/paso/1").get_data(as_text=True)
    assert "no tiene el componente de conexión" in h and "Subir archivos" in h
