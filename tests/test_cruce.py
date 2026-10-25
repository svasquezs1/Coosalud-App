"""Pruebas con los datos de prueba 100 % ficticios (carpeta datos_prueba_ficticios)."""
from pathlib import Path
import pandas as pd
import pytest
from cruce import config as C, lectura as L, proceso as P

D = Path(__file__).resolve().parent.parent / "datos_prueba_ficticios"
BDUA = ["AFI_BDUA_DEMO", "AFI_EPSS42_DEMO", "AFI_ESSC24_DEMO", "AFI_EPS042_DEMO"]


@pytest.fixture(scope="module")
def datos():
    nac, h = L.leer_tabla("BD_SINERGIA_DEMO.xlsx", (D / "BD_SINERGIA_DEMO.xlsx").read_bytes())
    bd = {n: L.leer_tabla(n + ".xlsx", (D / f"{n}.xlsx").read_bytes())[0] for n in BDUA}
    return nac, h, bd


@pytest.fixture(scope="module")
def r2(datos):
    nac, _, bd = datos
    return P.cruzar(nac, bd, set(C.ESTADOS_VIGENTES_CONOCIDOS), {"05", "17", "27"})


def test_cruce(r2):
    assert (r2.total, r2.vigentes, r2.no_vigentes, r2.fuera, r2.duplicados, r2.base_n) == (654, 543, 111, 7, 14, 522)
    assert (r2.cruzan, len(r2.no_cruzan), r2.bdua_total) == (492, 30, 735)
    assert (r2.nv_encontrados, r2.no_vigentes - r2.nv_encontrados) == (73, 38)
    assert r2.base["NUMERO_IDENTIFICACION"].is_unique or r2.base[["TIPO_IDENTIFICACION", "NUMERO_IDENTIFICACION"]].duplicated().sum() == 0


def test_solo_numero_cruza_igual_o_mas(datos, r2):
    nac, _, bd = datos
    r = P.cruzar(nac, bd, set(C.ESTADOS_VIGENTES_CONOCIDOS), {"05", "17", "27"}, solo_numero=True)
    assert r.cruzan >= r2.cruzan


def test_ips(r2):
    r3 = P.revisar_ips(r2.base, C.reglas_ips_por_defecto(), True)
    assert (r3.total, r3.correctas, len(r3.por_corregir), r3.sin_regla_total) == (522, 350, 65, 107)
    assert r3.base is not r2.base and "IPS_ORIGINAL" in r3.base and "IPS_ORIGINAL" not in r2.base   # no toca la base original
    # lo corregido queda con la IPS propuesta
    r3b = P.revisar_ips(r3.base.drop(columns="IPS_ORIGINAL"), C.reglas_ips_por_defecto(), False)
    assert len(r3b.por_corregir) == 0


def test_regla_nueva_para_municipio_sin_regla(r2):
    reglas = C.reglas_ips_por_defecto() + [dict(dep="05", city="042", reg="*", ips="050420547801", alt="", nombre="Santa Fe de Antioquia")]
    r3 = P.revisar_ips(r2.base, reglas, False)
    assert r3.sin_regla_total == 107 - 91 and not r3.aplicado


def test_documentos_y_contactos(r2, datos):
    _, h, _ = datos
    r3 = P.revisar_ips(r2.base, C.reglas_ips_por_defecto(), True)
    r4 = P.revisar_documentos_y_contactos(r3.base, C.reglas_documento_por_defecto(), pd.Timestamp("2026-10-15"), True, h)
    assert (len(r4.docs), r4.cn_vencidos, r4.final_n) == (20, 5, 517)
    assert (r4.cat["ambos"], r4.cat["solo_cel"], r4.cat["solo_mail"], r4.cat["ninguno"]) == (179, 260, 40, 38)
    assert len(r4.contactables) + len(r4.sin_contacto) == r4.final_n
    r4b = P.revisar_documentos_y_contactos(r3.base, C.reglas_documento_por_defecto(), pd.Timestamp("2026-10-15"), False, h)
    assert r4b.final_n == 522 and r4b.cn_vencidos == 5


def test_celulares():
    s = pd.Series(["3001234567", "+57 300 123 4567", "573001234567", "2001234567", "abc", ""])
    ok = P._limpiar_celular(s).str.match(P._RE_CEL).tolist()
    assert ok == [True, True, True, False, False, False]


def test_municipio_con_dos_ips_conserva_la_permitida():
    base = pd.DataFrame({"TIPO_IDENTIFICACION": ["CC"] * 3, "NUMERO_IDENTIFICACION": ["1", "2", "3"], "REGIMEN": ["SUBSIDIADO"] * 3,
                         "DEPARTAMENTO_PORTABILIDAD": ["05"] * 3, "CIUDAD_PORTABILIDAD": ["154"] * 3,
                         "IPS_ASIGNADA": ["A", "B", "Z"]})
    regla = [dict(dep="05", city="154", reg="SUBSIDIADO", ips="A", alt="B", nombre="Municipio de prueba")]
    r = P.revisar_ips(base, regla, True)
    assert r.correctas == 2 and list(r.por_corregir["NUMERO_IDENTIFICACION"]) == ["3"]
    assert r.base.loc[2, "IPS_ASIGNADA"] == "A" and r.base.loc[2, "IPS_ORIGINAL"] == "Z"


def test_sesiones_vencen_por_inactividad(monkeypatch):
    from cruce import sesion
    alm = sesion.Almacen(); sid, s = alm.obtener(None); s.nac = pd.DataFrame({"x": [1]})
    monkeypatch.setattr(sesion, "INACTIVIDAD_SEGUNDOS", -1)
    sid2, s2 = alm.obtener(sid)
    assert sid2 != sid and s2.nac is None


def test_base_nacional_con_columnas_que_se_llaman_como_las_de_salida(datos, r2):
    """La base real puede traer columnas como ESTADO_BDUA o TABLA_BDUA; no deben chocar con el cruce."""
    nac, _, bd = datos
    nac2 = nac.copy()
    nac2["ESTADO_BDUA"] = "X"
    nac2["TABLA_BDUA"] = "Y"
    nac2["_K"] = "Z"
    r = P.cruzar(nac2, bd, set(C.ESTADOS_VIGENTES_CONOCIDOS), {"05", "17", "27"})
    assert r.cruzan == r2.cruzan and r.no_cruzan.shape == r2.no_cruzan.shape
    assert list(r.nv_filas["ESTADO_BDUA"]) == list(r2.nv_filas["ESTADO_BDUA"])
    assert "X" not in set(r.nv_filas["ESTADO_BDUA"])
