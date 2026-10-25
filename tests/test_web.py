"""Flujo completo por la parte web, con datos ficticios."""
import io
import os
import tempfile
from pathlib import Path
import pandas as pd
import pytest

D = Path(__file__).resolve().parent.parent / "datos_prueba_ficticios"
from cruce.config import ESTADOS_VIGENTES_CONOCIDOS as EST
BDUA = ["AFI_BDUA_DEMO", "AFI_EPSS42_DEMO", "AFI_ESSC24_DEMO", "AFI_EPS042_DEMO"]


@pytest.fixture()
def cli():
    import app as m
    m.CLAVE = ""
    m.app.config["TESTING"] = True
    return m.app.test_client()


def subir(c):
    return c.post("/paso/1/subir", data={"nacional": (io.BytesIO((D / "BD_SINERGIA_DEMO.xlsx").read_bytes()), "BD_SINERGIA_DEMO.xlsx"),
                                         "bdua": [(io.BytesIO((D / f"{n}.xlsx").read_bytes()), f"{n}.xlsx") for n in BDUA]},
                  content_type="multipart/form-data", follow_redirects=True)


def test_flujo_completo_sin_archivos_temporales(cli):
    antes = set(os.listdir(tempfile.gettempdir()))
    assert cli.get("/paso/3", follow_redirects=True).request.path == "/paso/1"        # pasos bloqueados en orden
    r = subir(cli); assert "654 filas" in r.get_data(as_text=True)
    r = cli.post("/paso/1/continuar", data={"estado": EST, "deps": "05,17,27"}, follow_redirects=True)
    assert r.request.path == "/paso/2"
    r = cli.post("/paso/2/ejecutar", data={}, follow_redirects=True); h = r.get_data(as_text=True)
    assert ">522<" in h and ">492<" in h and "no cruzan (5,7%)" in h
    assert "••" in h                                                                      # documentos ocultos por defecto
    cli.post("/mascara"); assert "••" not in cli.get("/paso/2").get_data(as_text=True); cli.post("/mascara")
    r = cli.post("/paso/3/accion", data={"accion": "ejecutar", "aplicar": "1"}, follow_redirects=True); h = r.get_data(as_text=True)
    assert ">350<" in h and ">65<" in h and ">107<" in h
    r = cli.post("/paso/4/accion", data={"accion": "ejecutar", "fecha_ref": "2026-10-15", "excluir_cn": "1", "d_tipo": ["CC", "TI", "RC", "CN"],
                                         "d_min": ["18", "7", "0", ""], "d_max": ["120", "17", "6", ""], "d_dias": ["", "", "", "90"]}, follow_redirects=True)
    h = r.get_data(as_text=True); assert ">517<" in h and ">179<" in h and ">38<" in h
    assert "Content-Security-Policy" in r.headers and r.headers["Cache-Control"] == "no-store"
    r = cli.get("/paso/5"); assert r.status_code == 200
    for clave, n in [("base_final_depurada", 517), ("contactos_para_envio", 479), ("IPS_por_corregir", 65), ("resumen_del_ciclo", 12)]:
        for fmt in ("xlsx", "csv"):
            d = cli.get(f"/descargar?clave={clave}&formato={fmt}")
            assert d.status_code == 200
            df = pd.read_excel(io.BytesIO(d.data), dtype=str) if fmt == "xlsx" else pd.read_csv(io.BytesIO(d.data), dtype=str)
            assert len(df) == n
    base = pd.read_excel(io.BytesIO(cli.get("/descargar?clave=base_final_depurada&formato=xlsx").data), dtype=str)
    # 65 IPS corregidas en el paso 3; 4 de ellas eran de recién nacidos (CN) vencidos que salen de la base final -> 61
    assert "IPS_ORIGINAL" in base.columns and (base["IPS_ORIGINAL"].fillna("") != "").sum() == 61
    assert cli.get("/descargar?clave=../../etc/passwd").status_code == 404
    assert set(os.listdir(tempfile.gettempdir())) - antes == set()                        # nada se escribió en el disco temporal


def test_editar_reglas_invalida_y_crear_regla(cli):
    subir(cli); cli.post("/paso/1/continuar", data={"estado": EST, "deps": "05,17,27"}); cli.post("/paso/2/ejecutar", data={})
    cli.post("/paso/3/accion", data={"accion": "ejecutar", "aplicar": "1"})
    h = cli.post("/paso/3/accion", data={"accion": "crear:0", "aplicar": "1"}, follow_redirects=True).get_data(as_text=True)
    assert "Revisar IPS" in h and "resultados" not in h.split("Revisar IPS")[-1][:200]       # resultados borrados
    assert 'value="042"' in h                                                                  # la regla nueva quedó en la tabla
    h = cli.post("/paso/3/accion", data={"accion": "ejecutar", "aplicar": "1"}, follow_redirects=True).get_data(as_text=True)
    assert ">16<" in h                                                                         # 107 - 91 sin regla
    assert cli.get("/reglas_ips.csv").data.startswith(b"\xef\xbb\xbfDEPARTAMENTO,MUNICIPIO")


def test_importar_reglas_conserva_ceros(cli):
    subir(cli); cli.post("/paso/1/continuar", data={"estado": EST, "deps": "05"}); cli.post("/paso/2/ejecutar", data={})
    csv = "DEPARTAMENTO,MUNICIPIO,REGIMEN,IPS_POR_DEFECTO,OTRAS_IPS_PERMITIDAS,NOMBRE\n05,001,*,050010909901,,Medellín\n"
    h = cli.post("/paso/3/accion", data={"accion": "importar", "csv_reglas": (io.BytesIO(csv.encode()), "r.csv")},
                 content_type="multipart/form-data", follow_redirects=True).get_data(as_text=True)
    assert 'value="001"' in h and h.count('name="r_dep"') == 1


def test_limpiar_borra_todo(cli):
    subir(cli); cli.post("/limpiar")
    assert "654" not in cli.get("/paso/1").get_data(as_text=True)


def test_clave_de_acceso():
    import app as m
    m.CLAVE = "secreta"; c = m.app.test_client()
    assert c.get("/").status_code == 401
    import base64
    ok = {"Authorization": "Basic " + base64.b64encode(b"cruce:secreta").decode()}
    assert c.get("/", headers=ok).status_code == 302
    m.CLAVE = ""


def test_archivo_invalido(cli):
    h = cli.post("/paso/1/subir", data={"nacional": (io.BytesIO(b"no es un excel"), "malo.xlsx")}, content_type="multipart/form-data",
                 follow_redirects=True).get_data(as_text=True)
    assert "No se pudo leer el archivo" in h


def test_csv_con_ceros_y_columnas_faltantes(cli):
    nac = pd.read_excel(D / "BD_SINERGIA_DEMO.xlsx", dtype=str)
    csv = nac.to_csv(index=False).encode("utf-8-sig")
    h = cli.post("/paso/1/subir", data={"nacional": (io.BytesIO(csv), "nacional.csv")}, content_type="multipart/form-data", follow_redirects=True).get_data(as_text=True)
    assert "654 filas" in h and "Columnas requeridas completas" in h
    malo = nac.drop(columns=["REGIMEN"]).to_csv(index=False).encode()
    h = cli.post("/paso/1/subir", data={"nacional": (io.BytesIO(malo), "malo.csv"),
                                         "bdua": [(io.BytesIO((D / "AFI_BDUA_DEMO.xlsx").read_bytes()), "AFI_BDUA_DEMO.xlsx")]},
                 content_type="multipart/form-data", follow_redirects=True).get_data(as_text=True)
    assert "Faltan columnas: REGIMEN" in h and "disabled" in h.split("Continuar al cruce")[0][-120:]
    h = cli.post("/paso/1/continuar", data={"estado": EST, "deps": "05"}, follow_redirects=True)
    assert h.request.path == "/paso/1"                                                         # no deja avanzar


def test_estados_y_solo_numero_cambian_el_resultado(cli):
    subir(cli)
    cli.post("/paso/1/continuar", data={"estado": EST, "deps": "05,17,27"}); a = cli.post("/paso/2/ejecutar", data={}, follow_redirects=True).get_data(as_text=True)
    assert ">522<" in a
    cli.post("/paso/1/continuar", data={"estado": EST + ["NO VIGENTE"], "deps": "05,17,27"})  # cambiar la configuración borra el resultado
    assert "Ejecutar cruce" in cli.get("/paso/2").get_data(as_text=True) and ">522<" not in cli.get("/paso/2").get_data(as_text=True)
    b = cli.post("/paso/2/ejecutar", data={"solo_numero": "1"}, follow_redirects=True).get_data(as_text=True)
    assert "checked" in b.split("Cruzar solo por número")[0][-80:]


def test_quitar_y_editar_reglas(cli):
    subir(cli); cli.post("/paso/1/continuar", data={"estado": EST, "deps": "05,17,27"}); cli.post("/paso/2/ejecutar", data={})
    cli.post("/paso/3/accion", data={"accion": "ejecutar", "aplicar": "1"})
    h = cli.get("/paso/3").get_data(as_text=True); n0 = h.count('name="r_dep"'); assert n0 == 66
    h = cli.post("/paso/3/accion", data={"accion": "quitar:0", "aplicar": "1"}, follow_redirects=True).get_data(as_text=True)
    assert h.count('name="r_dep"') == 65 and ">350<" not in h                                    # al editar reglas el resultado se borra
    h = cli.post("/paso/3/accion", data={"accion": "restablecer"}, follow_redirects=True).get_data(as_text=True)
    assert h.count('name="r_dep"') == 66
