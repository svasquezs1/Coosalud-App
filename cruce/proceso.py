"""Los tres procesos de la Entrega 1, en orden: 1) cruce contra BDUA, 2) IPS por municipio,
3) documentos y contactos. Son funciones puras: reciben tablas y devuelven resultados."""
from __future__ import annotations
import re
from collections import Counter
from dataclasses import dataclass, field
import pandas as pd

from .config import OPT_NAC
from .lectura import columnas_faltantes

# ---------------------------------------------------------------- utilidades
def _t(s: pd.Series) -> pd.Series:
    return s.fillna("").astype(str).str.strip()

def _u(s: pd.Series) -> pd.Series:
    return _t(s).str.upper()

def _llave(df: pd.DataFrame, solo_numero: bool) -> pd.Series:
    num = _t(df["NUMERO_IDENTIFICACION"])
    return num if solo_numero else _u(df["TIPO_IDENTIFICACION"]) + "|" + num

def _pad(s: pd.Series, n: int) -> pd.Series:
    return _t(s).str.zfill(n)

def _cuenta(serie: pd.Series) -> dict:
    return {(k if k != "" else "(vacío)"): int(v) for k, v in serie.value_counts(sort=True).items()}


# ---------------------------------------------------------------- paso 2: cruce
@dataclass
class ResultadoCruce:
    total: int; vigentes: int; no_vigentes: int; fuera: int; duplicados: int; base_n: int
    cruzan: int; no_cruzan: pd.DataFrame; nv_filas: pd.DataFrame; nv_encontrados: int
    nv_por_tabla: dict; nv_por_estado: dict; ok_por_estado: dict; bdua_total: int; solo_numero: bool
    base: pd.DataFrame = field(repr=False, default=None)


def cruzar(nac: pd.DataFrame, bdua: dict[str, pd.DataFrame], estados_vigentes: set[str],
           departamentos: set[str], solo_numero: bool = False) -> ResultadoCruce:
    deps = {d.strip().zfill(2) for d in departamentos if d.strip()}
    # Las columnas internas empiezan por «_» (minúscula) para no chocar con columnas de la base nacional
    # (los encabezados de los archivos se pasan a MAYÚSCULAS al leerlos).
    # Índice BDUA: todas las filas de todas las tablas, con la tabla de origen.
    partes = []
    for nombre, t in bdua.items():
        partes.append(pd.DataFrame({"_k": _llave(t, solo_numero), "_tabla": nombre, "_estado": _t(t["ESTADO"])}))
    idx = pd.concat(partes, ignore_index=True) if partes else pd.DataFrame({"_k": [], "_tabla": [], "_estado": []})
    llaves_bdua = set(idx["_k"])
    primer_estado = idx.drop_duplicates("_k").set_index("_k")["_estado"]

    est = _u(nac["ESTADO"])
    es_vig = est.isin(estados_vigentes)
    vigentes, no_vig = nac[es_vig], nac[~es_vig]

    port = (_u(vigentes["PORTABILIDAD"]) == "SI") & _pad(vigentes["DEPARTAMENTO_PORTABILIDAD"], 2).isin(deps)
    en_alcance = _pad(vigentes["DEPARTAMENTO"], 2).isin(deps) | port
    alc = vigentes[en_alcance].copy()
    alc["_p"] = port[en_alcance]
    alc["_k"] = _u(alc["TIPO_IDENTIFICACION"]) + "|" + _t(alc["NUMERO_IDENTIFICACION"])
    # Un registro por persona (tipo|número); si está repetido, se prefiere el de portabilidad hacia la zona.
    orden = alc["_k"].drop_duplicates().reset_index().set_index("_k")["index"]
    elegido = alc.sort_values("_p", ascending=False, kind="stable").drop_duplicates("_k")
    elegido = elegido.assign(_o=elegido["_k"].map(orden)).sort_values("_o").drop(columns=["_p", "_k", "_o"])
    base = elegido.reset_index(drop=True)

    # A. Estados distintos de vigente que aparecen en BDUA
    k_nv = _llave(no_vig, solo_numero)
    nv_aux = no_vig.assign(_k=k_nv)
    nv_aux = nv_aux[nv_aux["_k"].isin(llaves_bdua)]
    nv = nv_aux.merge(idx, on="_k", how="inner")
    nv_filas = pd.DataFrame({
        "TIPO_IDENTIFICACION": _t(nv["TIPO_IDENTIFICACION"]), "NUMERO_IDENTIFICACION": _t(nv["NUMERO_IDENTIFICACION"]),
        "ESTADO_NACIONAL": _t(nv["ESTADO"]), "DEPARTAMENTO": _t(nv["DEPARTAMENTO"]),
        "CIUDAD": _t(nv["CIUDAD"]) if "CIUDAD" in nv else "", "TABLA_BDUA": nv["_tabla"], "ESTADO_BDUA": nv["_estado"]})

    # B. Vigentes de la base depurada que no aparecen en BDUA
    k_base = _llave(base, solo_numero)
    cruza = k_base.isin(llaves_bdua)
    nc = base[~cruza]
    no_cruzan = pd.DataFrame({
        "TIPO_IDENTIFICACION": _t(nc["TIPO_IDENTIFICACION"]), "NUMERO_IDENTIFICACION": _t(nc["NUMERO_IDENTIFICACION"]),
        "ESTADO": _t(nc["ESTADO"]), "REGIMEN": _t(nc["REGIMEN"]),
        "DEPARTAMENTO_PORTABILIDAD": _t(nc["DEPARTAMENTO_PORTABILIDAD"]),
        "CIUDAD_PORTABILIDAD": _t(nc["CIUDAD_PORTABILIDAD"]), "IPS_ASIGNADA": _t(nc["IPS_ASIGNADA"])})
    return ResultadoCruce(
        total=len(nac), vigentes=len(vigentes), no_vigentes=len(no_vig), fuera=len(vigentes) - len(alc),
        duplicados=len(alc) - len(base), base_n=len(base), cruzan=int(cruza.sum()), no_cruzan=no_cruzan.reset_index(drop=True),
        nv_filas=nv_filas.reset_index(drop=True), nv_encontrados=len(nv_aux),
        nv_por_tabla=_cuenta(nv_filas["TABLA_BDUA"]), nv_por_estado=_cuenta(nv_filas["ESTADO_BDUA"]),
        ok_por_estado=_cuenta(k_base[cruza].map(primer_estado)), bdua_total=sum(len(t) for t in bdua.values()),
        solo_numero=solo_numero, base=base)


# ---------------------------------------------------------------- paso 3: IPS por municipio
@dataclass
class ResultadoIPS:
    total: int; correctas: int; por_corregir: pd.DataFrame; sin_regla: pd.DataFrame; sin_regla_total: int
    aplicado: bool; base: pd.DataFrame = field(repr=False, default=None)


def _mapa_reglas(reglas: list[dict]) -> dict:
    m = {}
    for r in reglas:
        ips = str(r.get("ips", "")).strip()
        permitidas = [x for x in [ips] + [p.strip() for p in re.split(r"[,;|\s]+", str(r.get("alt", "") or ""))] if x]
        clave = (str(r.get("dep", "")).strip().zfill(2), str(r.get("city", "")).strip().zfill(3),
                 (str(r.get("reg", "") or "*").strip().upper() or "*"))
        m[clave] = (ips, permitidas, r.get("nombre", ""))
    return m


def revisar_ips(base: pd.DataFrame, reglas: list[dict], aplicar: bool) -> ResultadoIPS:
    rm = _mapa_reglas(reglas)
    dep = _pad(base["DEPARTAMENTO_PORTABILIDAD"], 2).tolist()
    mun = _pad(base["CIUDAD_PORTABILIDAD"], 3).tolist()
    reg = _u(base["REGIMEN"]).tolist()
    act = _t(base["IPS_ASIGNADA"]).tolist()
    ok = 0
    corregir_idx, corregir = [], []
    sin_regla: dict[tuple, Counter] = {}
    for i, (d, c, r, a) in enumerate(zip(dep, mun, reg, act)):
        regla = rm.get((d, c, r)) or rm.get((d, c, "*"))
        if regla is None:
            sin_regla.setdefault((d, c), Counter())[a or "(vacía)"] += 1
        elif a in regla[1]:
            ok += 1
        else:
            corregir_idx.append(i)
            corregir.append((regla[0], regla[2]))
    b = base.iloc[corregir_idx]
    por_corregir = pd.DataFrame({
        "TIPO_IDENTIFICACION": _t(b["TIPO_IDENTIFICACION"]), "NUMERO_IDENTIFICACION": _t(b["NUMERO_IDENTIFICACION"]),
        "REGIMEN": _t(b["REGIMEN"]), "DEPARTAMENTO_PORTABILIDAD": [dep[i] for i in corregir_idx],
        "CIUDAD_PORTABILIDAD": [mun[i] for i in corregir_idx], "MUNICIPIO": [x[1] for x in corregir],
        "IPS_ACTUAL": [act[i] or "(vacía)" for i in corregir_idx], "IPS_PROPUESTA": [x[0] for x in corregir]}).reset_index(drop=True)
    filas = [{"DEPARTAMENTO": d, "MUNICIPIO": c, "AFILIADOS": sum(cnt.values()), "IPS_MAS_FRECUENTE": cnt.most_common(1)[0][0]}
             for (d, c), cnt in sin_regla.items()]
    filas.sort(key=lambda x: -x["AFILIADOS"])
    sr = pd.DataFrame(filas, columns=["DEPARTAMENTO", "MUNICIPIO", "AFILIADOS", "IPS_MAS_FRECUENTE"])
    base2 = base.copy()
    if aplicar:
        base2["IPS_ORIGINAL"] = ""
        if corregir_idx:
            pos = base2.index[corregir_idx]
            base2.loc[pos, "IPS_ORIGINAL"] = [act[i] for i in corregir_idx]
            base2.loc[pos, "IPS_ASIGNADA"] = [x[0] for x in corregir]
    return ResultadoIPS(total=len(base), correctas=ok, por_corregir=por_corregir, sin_regla=sr,
                        sin_regla_total=int(sr["AFILIADOS"].sum()) if len(sr) else 0, aplicado=aplicar, base=base2)


# ---------------------------------------------------------------- paso 4: documentos y contactos
@dataclass
class ResultadoDocs:
    total: int; final_n: int; docs: pd.DataFrame; contactables: pd.DataFrame; sin_contacto: pd.DataFrame
    tiene_contacto: bool; cat: dict; celulares_compartidos: int; afiliados_cel_compartido: int
    cn_vencidos: int; excluir_cn: bool; final: pd.DataFrame = field(repr=False, default=None)


def _num(v):
    v = str(v).strip()
    try:
        return float(v) if v != "" else None
    except ValueError:
        return None


def _parse_fecha(s: pd.Series) -> pd.Series:
    """Acepta aaaa/mm/dd, aaaa-mm-dd y dd/mm/aaaa. Lo que no se pueda leer queda en NaT."""
    def una(v: str):
        v = v.strip()
        m = re.match(r"^(\d{4})[/-](\d{1,2})[/-](\d{1,2})", v)
        if m:
            y, mo, d = map(int, m.groups())
        else:
            m = re.match(r"^(\d{1,2})[/-](\d{1,2})[/-](\d{4})", v)
            if not m:
                return pd.NaT
            d, mo, y = map(int, m.groups())
        try:
            return pd.Timestamp(year=y, month=mo, day=d)
        except ValueError:
            return pd.NaT
    cache = {v: una(v) for v in s.unique()}
    return pd.to_datetime(s.map(cache))


def _limpiar_celular(s: pd.Series) -> pd.Series:
    d = s.fillna("").astype(str).str.replace(r"\D", "", regex=True)
    return d.where(~((d.str.len() == 12) & d.str.startswith("57")), d.str[2:])


_RE_CEL = r"^3\d{9}$"
_RE_MAIL = r"^[^\s@]+@[^\s@]+\.[^\s@]+$"


def revisar_documentos_y_contactos(base2: pd.DataFrame, reglas_doc: list[dict], fecha_ref: pd.Timestamp,
                                   excluir_cn: bool, encabezados_nac: list[str]) -> ResultadoDocs:
    reglas = {str(r["tipo"]).strip().upper(): r for r in reglas_doc if str(r.get("tipo", "")).strip()}
    tipo = _u(base2["TIPO_IDENTIFICACION"])
    nac = _parse_fecha(_t(base2["FECHA_NACIMIENTO"]))
    n = len(base2)
    motivo = pd.Series([""] * n, index=base2.index, dtype=object)
    excluir = pd.Series(False, index=base2.index)
    cn_venc = 0
    dias_vida = (fecha_ref - nac).dt.days
    antes_cumple = (nac.dt.month > fecha_ref.month) | ((nac.dt.month == fecha_ref.month) & (nac.dt.day > fecha_ref.day))
    edad = fecha_ref.year - nac.dt.year - antes_cumple.astype(int)
    for t, r in reglas.items():
        en = tipo == t
        if not en.any():
            continue
        invalida = en & nac.isna()
        motivo[invalida] = "Fecha de nacimiento vacía o inválida"
        valida = en & nac.notna()
        dmax = _num(r.get("dias", ""))
        if dmax is not None:
            vencido = valida & (dias_vida > dmax)
            negativo = valida & ~vencido & (dias_vida < 0)
            cn_venc += int(vencido.sum())
            for i in base2.index[vencido]:
                motivo[i] = f"{t} con {int(dias_vida[i])} días (máximo {int(dmax) if float(dmax).is_integer() else dmax})"
            motivo[negativo] = "Fecha de nacimiento posterior a la fecha de referencia"
            if excluir_cn:
                excluir[vencido] = True
        else:
            mn, mx = _num(r.get("min", "")), _num(r.get("max", ""))
            fuera = pd.Series(False, index=base2.index)
            if mn is not None: fuera |= valida & (edad < mn)
            if mx is not None: fuera |= valida & (edad > mx)
            for i in base2.index[fuera]:
                a = f"{int(mn) if mn is not None else 0}"
                b = f"{int(mx)}" if mx is not None else "más"
                motivo[i] = f"{t} con {int(edad[i])} años (esperado {a} a {b})"
    hay = motivo != ""
    docs = pd.DataFrame({
        "TIPO_IDENTIFICACION": _t(base2.loc[hay, "TIPO_IDENTIFICACION"]), "NUMERO_IDENTIFICACION": _t(base2.loc[hay, "NUMERO_IDENTIFICACION"]),
        "FECHA_NACIMIENTO": _t(base2.loc[hay, "FECHA_NACIMIENTO"]), "MOTIVO": motivo[hay],
        "EXCLUIDO_DE_BASE_FINAL": excluir[hay].map({True: "SI", False: "NO"})}).reset_index(drop=True)
    final = base2[~excluir].reset_index(drop=True)

    tiene = not columnas_faltantes(encabezados_nac, OPT_NAC)
    cat = dict(ambos=0, solo_cel=0, solo_mail=0, ninguno=0, cel_invalido=0, mail_invalido=0)
    contactables = sin_contacto = pd.DataFrame()
    dup_n = dup_afi = 0
    if tiene:
        cel = _limpiar_celular(final["CELULAR"]); mail = _t(final["CORREO_ELECTRONICO"])
        c_ok = cel.str.match(_RE_CEL); m_ok = mail.str.match(_RE_MAIL)
        cat.update(ambos=int((c_ok & m_ok).sum()), solo_cel=int((c_ok & ~m_ok).sum()), solo_mail=int((~c_ok & m_ok).sum()),
                   ninguno=int((~c_ok & ~m_ok).sum()), cel_invalido=int(((cel != "") & ~c_ok).sum()), mail_invalido=int(((mail != "") & ~m_ok).sum()))
        veces = cel[c_ok].value_counts()
        dup_n, dup_afi = int((veces > 1).sum()), int(veces[veces > 1].sum())
        con = c_ok | m_ok
        f = final[con]
        contactables = pd.DataFrame({
            "TIPO_IDENTIFICACION": _t(f["TIPO_IDENTIFICACION"]), "NUMERO_IDENTIFICACION": _t(f["NUMERO_IDENTIFICACION"]),
            "CELULAR": cel[con].where(c_ok[con], ""), "CORREO_ELECTRONICO": mail[con].where(m_ok[con], ""),
            "DEPARTAMENTO_PORTABILIDAD": _t(f["DEPARTAMENTO_PORTABILIDAD"]), "CIUDAD_PORTABILIDAD": _t(f["CIUDAD_PORTABILIDAD"]),
            "IPS_ASIGNADA": _t(f["IPS_ASIGNADA"])}).reset_index(drop=True)
        s = final[~con]
        vacio = (_t(s["CELULAR"]) == "") & (_t(s["CORREO_ELECTRONICO"]) == "")
        sin_contacto = pd.DataFrame({
            "TIPO_IDENTIFICACION": _t(s["TIPO_IDENTIFICACION"]), "NUMERO_IDENTIFICACION": _t(s["NUMERO_IDENTIFICACION"]),
            "CELULAR_REGISTRADO": _t(s["CELULAR"]), "CORREO_REGISTRADO": _t(s["CORREO_ELECTRONICO"]),
            "MOTIVO": vacio.map({True: "Sin celular ni correo", False: "Celular o correo con formato inválido"}),
            "DEPARTAMENTO_PORTABILIDAD": _t(s["DEPARTAMENTO_PORTABILIDAD"]), "CIUDAD_PORTABILIDAD": _t(s["CIUDAD_PORTABILIDAD"])}).reset_index(drop=True)
    return ResultadoDocs(total=n, final_n=len(final), docs=docs, contactables=contactables, sin_contacto=sin_contacto,
                         tiene_contacto=tiene, cat=cat, celulares_compartidos=dup_n, afiliados_cel_compartido=dup_afi,
                         cn_vencidos=cn_venc, excluir_cn=excluir_cn, final=final)
