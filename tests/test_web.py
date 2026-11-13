"""Flujo completo por la parte web, con datos ficticios."""
import io
import os
import re
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
    r = cli.post("/paso/2/ejecutar", data={"llave": "tipo_numero"}, follow_redirects=True); h = r.get_data(as_text=True)
    assert ">522<" in h and ">492<" in h and "no cruzan (5,7%)" in h
    assert "••" in h                                                                      # documentos ocultos por defecto
    cli.post("/mascara"); assert "••" not in cli.get("/paso/2").get_data(as_text=True); cli.post("/mascara")
    r = cli.post("/paso/3/accion", data={"accion": "ejecutar", "aplicar": "1"}, follow_redirects=True); h = r.get_data(as_text=True)
    assert ">441<" in h and ">65<" in h and ">16<" in h
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
    subir(cli); cli.post("/paso/1/continuar", data={"estado": EST, "deps": "05,17,27"}); cli.post("/paso/2/ejecutar", data={"llave": "tipo_numero"})
    cli.post("/paso/3/accion", data={"accion": "ejecutar", "aplicar": "1"})
    h = cli.post("/paso/3/accion", data={"accion": "crear:0", "aplicar": "1"}, follow_redirects=True).get_data(as_text=True)
    assert "Revisar IPS" in h and "resultados" not in h.split("Revisar IPS")[-1][:200]       # resultados borrados
    assert h.count('name="r_dep"') == 68                                                       # la regla nueva quedó en la tabla (67 + 1)
    h = cli.post("/paso/3/accion", data={"accion": "ejecutar", "aplicar": "1"}, follow_redirects=True).get_data(as_text=True)
    assert ">16<" not in h                                                                     # ya no están todos sin regla
    assert cli.get("/reglas_ips.csv").data.startswith(b"\xef\xbb\xbfDEPARTAMENTO,MUNICIPIO")


def test_importar_reglas_conserva_ceros(cli):
    subir(cli); cli.post("/paso/1/continuar", data={"estado": EST, "deps": "05"}); cli.post("/paso/2/ejecutar", data={"llave": "tipo_numero"})
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
    cli.post("/paso/1/continuar", data={"estado": EST, "deps": "05,17,27"}); a = cli.post("/paso/2/ejecutar", data={"llave": "tipo_numero"}, follow_redirects=True).get_data(as_text=True)
    assert ">522<" in a
    cli.post("/paso/1/continuar", data={"estado": EST + ["NO VIGENTE"], "deps": "05,17,27"})  # cambiar la configuración borra el resultado
    assert "Ejecutar cruce" in cli.get("/paso/2").get_data(as_text=True) and ">522<" not in cli.get("/paso/2").get_data(as_text=True)
    b = cli.post("/paso/2/ejecutar", data={"llave": "solo_numero"}, follow_redirects=True).get_data(as_text=True)
    assert 'value="solo_numero" checked' in b and "Cruzado por: Solo el número" in b


def test_quitar_y_editar_reglas(cli):
    subir(cli); cli.post("/paso/1/continuar", data={"estado": EST, "deps": "05,17,27"}); cli.post("/paso/2/ejecutar", data={"llave": "tipo_numero"})
    cli.post("/paso/3/accion", data={"accion": "ejecutar", "aplicar": "1"})
    h = cli.get("/paso/3").get_data(as_text=True); n0 = h.count('name="r_dep"'); assert n0 == 67
    h = cli.post("/paso/3/accion", data={"accion": "quitar:0", "aplicar": "1"}, follow_redirects=True).get_data(as_text=True)
    assert h.count('name="r_dep"') == 66 and ">441<" not in h                                    # al editar reglas el resultado se borra
    h = cli.post("/paso/3/accion", data={"accion": "restablecer"}, follow_redirects=True).get_data(as_text=True)
    assert h.count('name="r_dep"') == 67


def _al_paso_2(cli, nac_bytes=None, nombre="BD_SINERGIA_DEMO.xlsx", bdua=None):
    nac_bytes = nac_bytes or (D / "BD_SINERGIA_DEMO.xlsx").read_bytes()
    bdua = bdua or [((D / f"{n}.xlsx").read_bytes(), f"{n}.xlsx") for n in BDUA]
    return cli.post("/paso/1/subir", data={"nacional": (io.BytesIO(nac_bytes), nombre),
                                           "bdua": [(io.BytesIO(b), n) for b, n in bdua]},
                    content_type="multipart/form-data", follow_redirects=True)


def _renombrado(columnas: dict) -> bytes:
    """Base nacional ficticia con los nombres de columna del archivo crudo del nivel nacional."""
    nac = pd.read_excel(D / "BD_SINERGIA_DEMO.xlsx", dtype=str).rename(columns=columnas)
    b = io.BytesIO(); nac.to_excel(b, index=False); return b.getvalue()


def test_alias_por_defecto_aceptan_el_archivo_crudo_del_nivel_nacional(cli):
    crudo = _renombrado({"IPS_ASIGNADA": "CODIGOIPS", "PORTABILIDAD": "PORTABILIDAD_VIGENTE", "TIPO_POBLACION": "COD_TIPO_POBLACION"})
    h = _al_paso_2(cli, crudo, "crudo.xlsx").get_data(as_text=True)
    assert "654 filas" in h and "Columnas requeridas completas" in h


def test_columna_desconocida_se_asigna_y_se_actualiza_lo_cargado(cli):
    crudo = _renombrado({"REGIMEN": "Tipo de Régimen"})
    h = _al_paso_2(cli, crudo, "crudo.xlsx").get_data(as_text=True)
    assert "Faltan columnas: REGIMEN" in h and "¿Cuál columna de su archivo es?" in h
    assert h.index("Tipo de Régimen") < h.index("Usar esta columna")                     # se ofrece como opción
    h = cli.post("/paso/1/alias", data={"accion": "asignar", "falta": "REGIMEN", "columna": "Tipo de Régimen"}, follow_redirects=True).get_data(as_text=True)
    assert "Columnas requeridas completas" in h and "Faltan columnas" not in h
    assert 'value="Tipo de Régimen"' in h and 'value="REGIMEN"' in h                   # quedó en la tabla de alias
    # quitar el alias vuelve a marcar la falta, sin volver a subir el archivo
    i = h.count('name="al_origen"') - 1
    h = cli.post("/paso/1/alias", data={"accion": f"quitar:{i}"}, follow_redirects=True).get_data(as_text=True)
    assert "Faltan columnas: REGIMEN" in h


def test_alias_no_duplica_columna_si_el_archivo_ya_la_trae():
    from cruce import lectura as L
    csv = b"CODIGOIPS,IPS_ASIGNADA,otra\n1,2,3\n"
    df, orig = L.leer_tabla("x.csv", csv, [dict(origen="codigoips", destino="ips asignada")])
    assert list(df.columns) == ["CODIGOIPS", "IPS_ASIGNADA", "OTRA"] and orig == ["CODIGOIPS", "IPS_ASIGNADA", "otra"]


def test_alias_importar_y_exportar(cli):
    csv = "NOMBRE_EN_EL_ARCHIVO,NOMBRE_EN_LA_APLICACION\nMi Regimen,REGIMEN\n"
    h = cli.post("/paso/1/alias", data={"accion": "importar", "csv_alias": (io.BytesIO(csv.encode()), "a.csv")},
                 content_type="multipart/form-data", follow_redirects=True).get_data(as_text=True)
    assert h.count('name="al_origen"') == 1 and 'value="Mi Regimen"' in h
    assert cli.get("/alias_columnas.csv").data.startswith(b"\xef\xbb\xbfNOMBRE_EN_EL_ARCHIVO")
    h = cli.post("/paso/1/alias", data={"accion": "importar", "csv_alias": (io.BytesIO(b"a,b\n1,2\n"), "a.csv")},
                 content_type="multipart/form-data", follow_redirects=True).get_data(as_text=True)
    assert "No se pudieron cargar los nombres de columnas" in h


def _con_afl_bdua() -> list:
    """Las tablas BDUA ficticias no traen AFL_ID_BDUA: se les agrega con el mismo id que la base nacional."""
    nac = pd.read_excel(D / "BD_SINERGIA_DEMO.xlsx", dtype=str)
    ids = dict(zip(nac["TIPO_IDENTIFICACION"] + "|" + nac["NUMERO_IDENTIFICACION"], nac["AFL_ID_BDUA"]))
    res = []
    for n in BDUA:
        t = pd.read_excel(D / f"{n}.xlsx", dtype=str)
        t["AFL_ID_BDUA"] = (t["TIPO_IDENTIFICACION"] + "|" + t["NUMERO_IDENTIFICACION"]).map(ids).fillna("")
        b = io.BytesIO(); t.to_excel(b, index=False); res.append((b.getvalue(), f"{n}.xlsx"))
    return res


def test_llave_afl_id_bdua_avisa_si_falta_y_cruza_igual_si_esta(cli):
    _al_paso_2(cli); cli.post("/paso/1/continuar", data={"estado": EST, "deps": "05,17,27"})
    h = cli.post("/paso/2/ejecutar", data={"llave": "afl_id"}, follow_redirects=True).get_data(as_text=True)
    assert "AFL_ID_BDUA no está en" in h and "AFI_BDUA_DEMO" in h and "Ejecutar cruce" in h and ">522<" not in h
    cli.post("/limpiar"); _al_paso_2(cli, bdua=_con_afl_bdua()); cli.post("/paso/1/continuar", data={"estado": EST, "deps": "05,17,27"})
    h = cli.post("/paso/2/ejecutar", data={"llave": "afl_id"}, follow_redirects=True).get_data(as_text=True)
    assert ">522<" in h and ">492<" in h and "Cruzado por: AFL_ID_BDUA" in h          # mismo resultado que tipo+número


def test_llave_vacia_no_cruza_con_otra_vacia():
    from cruce import proceso as P, config as C
    nac = pd.DataFrame({"TIPO_IDENTIFICACION": ["CC", "CC"], "NUMERO_IDENTIFICACION": ["1", "2"], "ESTADO": ["VIGENTE"] * 2,
                        "DEPARTAMENTO": ["05"] * 2, "PORTABILIDAD": ["NO"] * 2, "DEPARTAMENTO_PORTABILIDAD": ["05"] * 2,
                        "CIUDAD_PORTABILIDAD": ["001"] * 2, "REGIMEN": ["SUBSIDIADO"] * 2, "IPS_ASIGNADA": ["x"] * 2,
                        "AFL_ID_BDUA": ["77", ""]})
    bd = {"B": pd.DataFrame({"TIPO_IDENTIFICACION": ["CC", "CC"], "NUMERO_IDENTIFICACION": ["1", "9"], "ESTADO": ["AC"] * 2,
                             "AFL_ID_BDUA": ["77", ""]})}
    r = P.cruzar(nac, bd, {"VIGENTE"}, {"05"}, llave="afl_id")
    assert (r.cruzan, len(r.no_cruzan), r.sin_llave) == (1, 1, 1)


def test_archivos_por_municipio_e_ips(cli):
    import zipfile
    _al_paso_2(cli); cli.post("/paso/1/continuar", data={"estado": EST, "deps": "05,17,27"}); cli.post("/paso/2/ejecutar", data={"llave": "tipo_numero"})
    cli.post("/paso/3/accion", data={"accion": "ejecutar", "aplicar": "1"})
    cli.post("/paso/4/accion", data={"accion": "ejecutar", "fecha_ref": "2026-10-15", "excluir_cn": "1", "d_tipo": ["CC", "TI", "RC", "CN"],
                                     "d_min": ["18", "7", "0", ""], "d_max": ["120", "17", "6", ""], "d_dias": ["", "", "", "90"]})
    h = cli.get("/paso/5").get_data(as_text=True)
    assert "Archivos por municipio e IPS" in h and "BD SUBSIDIADO - " in h and "Descargar todo (ZIP)" in h
    z = cli.get("/descargar_zip?formato=csv"); assert z.status_code == 200 and z.mimetype == "application/zip"
    zf = zipfile.ZipFile(io.BytesIO(z.data)); nombres = zf.namelist()
    assert all(n.endswith(".csv") and "/" in n and n.split("/")[-1].startswith("BD ") for n in nombres)
    import cruce.config as C
    de_prestador = {d["archivo"] for d in C.prestadores_por_defecto()}
    total = sum(len(pd.read_csv(io.BytesIO(zf.read(n)), dtype=str)) for n in nombres if n.split("/")[-1][:-4] not in de_prestador)
    assert 0 < total <= 517                                                                # por municipio e IPS nadie se repite
    g = [x for x in re.findall(r'href="(/descargar_municipio\?[^"]*formato=xlsx[^"]*)"', h)][0].replace("&amp;", "&")
    d = cli.get(g); assert d.status_code == 200 and "filename*=UTF-8''BD" in d.headers["Content-Disposition"]
    assert cli.get("/descargar_municipio?reg=X&dep=05&mun=001&ips=1").status_code == 404
    assert cli.get("/descargar_zip").status_code == 200


def test_tabla_de_prestadores_se_descarga_y_se_recarga(cli):
    _al_paso_2(cli); cli.post("/paso/1/continuar", data={"estado": EST, "deps": "05,17,27"}); cli.post("/paso/2/ejecutar", data={"llave": "tipo_numero"})
    cli.post("/paso/3/accion", data={"accion": "ejecutar", "aplicar": "1"})
    cli.post("/paso/4/accion", data={"accion": "ejecutar", "fecha_ref": "2026-10-15", "excluir_cn": "1", "d_tipo": ["CC"],
                                     "d_min": ["18"], "d_max": ["120"], "d_dias": [""]})
    h = cli.get("/paso/5").get_data(as_text=True)
    assert 'id="prestadores"' in h and "/archivos_prestadores.csv" in h
    csv_ = cli.get("/archivos_prestadores.csv"); assert csv_.status_code == 200
    t = pd.read_csv(io.BytesIO(csv_.data), dtype=str); assert {"ARCHIVO", "IPS_INCLUIDAS", "INCLUIR_SIN_IPS", "IPS_FORZADA"} <= set(t.columns)
    mini = "ARCHIVO,DEPARTAMENTO,MUNICIPIO,REGIMEN,IPS_INCLUIDAS\nBD PRUEBA,05,1,*,*\n"
    r = cli.post("/paso/5/prestadores", data={"accion": "importar", "csv_prestadores": (io.BytesIO(mini.encode()), "p.csv")},
                 content_type="multipart/form-data", follow_redirects=True)
    assert "Se cargaron 1 reglas" in r.get_data(as_text=True) and "BD PRUEBA" in r.get_data(as_text=True)
    assert cli.get("/descargar_prestador?archivo=BD+PRUEBA&formato=csv").status_code == 200
    assert cli.get("/descargar_prestador?archivo=NO+EXISTE").status_code == 404
    r = cli.post("/paso/5/prestadores", data={"accion": "importar", "csv_prestadores": (io.BytesIO(b"X,Y\n1,2\n"), "m.csv")},
                 content_type="multipart/form-data", follow_redirects=True)
    assert "faltan las columnas" in r.get_data(as_text=True)
    cli.post("/paso/5/prestadores", data={"accion": "restablecer"})
    assert b"BD CAUCASIA" in cli.get("/archivos_prestadores.csv").data and b"BD PROMEDAN" in cli.get("/archivos_prestadores.csv").data


def test_varias_llaves_en_pantalla(cli):
    _al_paso_2(cli); cli.post("/paso/1/continuar", data={"estado": EST, "deps": "05,17,27"})
    h = cli.get("/paso/2").get_data(as_text=True)
    assert h.count('type="checkbox" name="llave"') == 3 and 'value="tipo_numero" checked' in h        # tres casillas, la recomendada marcada
    h = cli.post("/paso/2/ejecutar", data={}, follow_redirects=True).get_data(as_text=True)
    assert "Marque al menos una" in h and ">522<" not in h                                            # sin casillas no se ejecuta
    h = cli.post("/paso/2/ejecutar", data={"llave": ["tipo_numero", "solo_numero"]}, follow_redirects=True).get_data(as_text=True)
    assert 'value="solo_numero" checked' in h and 'value="tipo_numero" checked' in h and 'value="afl_id" checked' not in h
    assert "Cruzado por: Tipo y número de documento (recomendada); Solo el número" in h and "Coinciden por tipo y número de documento:" in h


def test_falta_estado_permite_asignarlo_sin_error_500(cli):
    crudo = _renombrado({"ESTADO": "Situacion del afiliado"})
    respuesta = _al_paso_2(cli, crudo, "sin_estado.xlsx")
    assert respuesta.status_code == 200
    h = respuesta.get_data(as_text=True)
    assert "Faltan columnas: ESTADO" in h
    assert "Falta la columna ESTADO en la tabla nacional" in h
    assert "disabled" in h.split("Continuar al cruce")[0][-120:]
    respuesta = cli.post("/paso/1/continuar", data={"estado": EST, "deps": "05"}, follow_redirects=True)
    assert respuesta.status_code == 200 and respuesta.request.path == "/paso/1"
    respuesta = cli.post("/paso/1/alias", data={"accion": "asignar", "falta": "ESTADO", "columna": "Situacion del afiliado"}, follow_redirects=True)
    h = respuesta.get_data(as_text=True)
    assert respuesta.status_code == 200 and "Columnas requeridas completas" in h
    assert 'name="estado"' in h and "Falta la columna ESTADO" not in h
    respuesta = cli.post("/paso/1/continuar", data={"estado": EST, "deps": "05"}, follow_redirects=True)
    assert respuesta.request.path == "/paso/2"
