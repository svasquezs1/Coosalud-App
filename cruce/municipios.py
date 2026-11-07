"""Archivos por régimen, municipio e IPS (HU-28 y HU-29). Funciones puras: reciben la base final y las
reglas de IPS (solo para el nombre del municipio) y devuelven tablas; no leen ni escriben archivos."""
from __future__ import annotations
import re
import pandas as pd

from .proceso import _pad, _t, _u

SIN_REGIMEN, SIN_IPS = "SIN REGIMEN", "SIN IPS"


def _claves(final: pd.DataFrame) -> pd.DataFrame:
    """Régimen, departamento y municipio (los de portabilidad, como en la revisión de IPS) y la IPS de cada afiliado."""
    nom = _t(final["NOM_IPS"]) if "NOM_IPS" in final.columns else pd.Series("", index=final.index)
    return pd.DataFrame({
        "REGIMEN": _u(final["REGIMEN"]).replace("", SIN_REGIMEN),
        "DEPARTAMENTO": _pad(final["DEPARTAMENTO_PORTABILIDAD"], 2),
        "MUNICIPIO": _pad(final["CIUDAD_PORTABILIDAD"], 3),
        "IPS": _t(final["IPS_ASIGNADA"]).replace("", SIN_IPS),
        "NOM_IPS": nom})


def limpiar_nombre(texto: str) -> str:
    """Quita lo que no sirve en un nombre de archivo o carpeta y deja un solo espacio entre palabras."""
    return re.sub(r"\s+", " ", re.sub(r'[\\/:*?"<>|\r\n\t]', " ", str(texto))).strip()


def _nombres_municipio(reglas: list[dict]) -> dict[tuple[str, str], str]:
    return {(str(r.get("dep", "")).strip().zfill(2), str(r.get("city", "")).strip().zfill(3)): str(r.get("nombre", "")).strip()
            for r in reglas if str(r.get("nombre", "")).strip()}


def cubiertos_por_prestadores(prestadores: list[dict] | None) -> set[tuple[str, str, str]]:
    """(departamento, municipio, régimen) que ya salen por prestador, salvo los archivos «adicionales» (p. ej. Promedan).
    El régimen puede ser «*» (los dos)."""
    return {(str(d.get("dep", "")).strip().zfill(2), str(d.get("mun", "")).strip().zfill(3), (str(d.get("reg", "*")).strip().upper() or "*"))
            for d in (prestadores or []) if not d.get("adicional")}


def agrupar(final: pd.DataFrame, reglas: list[dict], prestadores: list[dict] | None = None) -> pd.DataFrame:
    """Una fila por archivo: régimen + municipio + IPS, con el número de afiliados y el nombre del archivo.
    Nombre: «BD <RÉGIMEN> - <MUNICIPIO> <nombre de la IPS>» (si la base no trae el nombre, se usa el código de la IPS)."""
    cols = ["REGIMEN", "DEPARTAMENTO", "MUNICIPIO", "IPS", "NOM_IPS", "NOMBRE_MUNICIPIO", "AFILIADOS", "ARCHIVO"]
    if len(final) == 0:
        return pd.DataFrame(columns=cols)
    k = _claves(final)
    cubiertos = cubiertos_por_prestadores(prestadores)          # esos régimen+municipio se arman por prestador (ver más abajo)
    if cubiertos:
        k = k[[(d, m, r) not in cubiertos and (d, m, "*") not in cubiertos for d, m, r in zip(k["DEPARTAMENTO"], k["MUNICIPIO"], k["REGIMEN"])]]
        if k.empty:
            return pd.DataFrame(columns=cols)
    g = (k.assign(_n=1).groupby(["REGIMEN", "DEPARTAMENTO", "MUNICIPIO", "IPS"], sort=True)
         .agg(NOM_IPS=("NOM_IPS", lambda x: next((v for v in x if v), "")), AFILIADOS=("_n", "sum")).reset_index())
    nombres = _nombres_municipio(reglas)
    g["NOMBRE_MUNICIPIO"] = [nombres.get((d, m), f"{d}-{m}") for d, m in zip(g["DEPARTAMENTO"], g["MUNICIPIO"])]
    g["ARCHIVO"] = [limpiar_nombre(f"BD {r} - {mun.upper()} {nom or ips}")
                    for r, mun, nom, ips in zip(g["REGIMEN"], g["NOMBRE_MUNICIPIO"], g["NOM_IPS"], g["IPS"])]
    repetido = g["ARCHIVO"].duplicated(keep=False)          # dos IPS con el mismo nombre: se distinguen por el código
    g.loc[repetido, "ARCHIVO"] = [limpiar_nombre(f"{a} ({ips})") for a, ips in zip(g.loc[repetido, "ARCHIVO"], g.loc[repetido, "IPS"])]
    g["AFILIADOS"] = g["AFILIADOS"].astype(int)
    return g[cols]


def _lista(texto) -> list[str]:
    return [x for x in re.split(r"[;,\s]+", str(texto or "").strip()) if x]


def _filas_de_regla(final: pd.DataFrame, k: pd.DataFrame, ips_cruda: pd.Series, d: dict) -> pd.Series:
    """Quiénes entran por una regla de prestador: municipio y régimen, y la IPS (cualquiera, una lista, o sin IPS)."""
    m = (k["DEPARTAMENTO"] == str(d["dep"]).strip().zfill(2)) & (k["MUNICIPIO"] == str(d["mun"]).strip().zfill(3))
    reg = str(d.get("reg", "*")).strip().upper() or "*"
    if reg != "*":
        m &= k["REGIMEN"] == reg
    codigos = _lista(d.get("ips", "*"))
    if codigos and "*" not in codigos:
        dentro = ips_cruda.isin(codigos)
        m &= (dentro | (ips_cruda == "")) if d.get("sin_ips") else dentro
    return m


def prestadores(final: pd.DataFrame, prestadores_: list[dict], reglas: list[dict]) -> pd.DataFrame:
    """Una fila por archivo de prestador: nombre, carpeta (municipio, o «VARIOS MUNICIPIOS»), afiliados y municipios.
    Un afiliado puede estar en varios archivos (como en las consultas del área), pero nunca dos veces en el mismo."""
    cols = ["ARCHIVO", "CARPETA", "AFILIADOS", "MUNICIPIOS"]
    if len(final) == 0 or not prestadores_:
        return pd.DataFrame(columns=cols)
    nombres, filas = _nombres_municipio(reglas), []
    for archivo in dict.fromkeys(d["archivo"] for d in prestadores_):
        reglas_a = [d for d in prestadores_ if d["archivo"] == archivo]
        n = len(filas_del_prestador(final, prestadores_, archivo))
        muns = sorted({(str(d["dep"]).zfill(2), str(d["mun"]).zfill(3)) for d in reglas_a})
        carpeta = nombres.get(muns[0], f"{muns[0][0]}-{muns[0][1]}") if len(muns) == 1 else "VARIOS MUNICIPIOS"
        filas.append(dict(ARCHIVO=limpiar_nombre(archivo), CARPETA=carpeta, AFILIADOS=n, MUNICIPIOS=len(muns)))
    return pd.DataFrame(filas, columns=cols)


def filas_del_prestador(final: pd.DataFrame, prestadores_: list[dict], archivo: str) -> pd.DataFrame:
    """Afiliados de un archivo de prestador. Si una regla cambia la IPS (IPS_FORZADA), el archivo sale con esa IPS."""
    k = _claves(final); ips_cruda = _t(final["IPS_ASIGNADA"])
    quita = limpiar_nombre
    reglas_a = [d for d in prestadores_ if quita(d["archivo"]) == quita(archivo)]
    entra = pd.Series(False, index=final.index); forzada = pd.Series("", index=final.index)
    for d in reglas_a:
        m = _filas_de_regla(final, k, ips_cruda, d)
        entra |= m
        if str(d.get("forzada", "")).strip():
            forzada = forzada.mask(m, str(d["forzada"]).strip())
    out = final[entra.to_numpy()].copy()
    cambia = forzada[entra].to_numpy() != ""
    if cambia.any():
        out.loc[cambia, "IPS_ASIGNADA"] = forzada[entra].to_numpy()[cambia]
    return out.reset_index(drop=True)


def filas_del_archivo(final: pd.DataFrame, regimen: str, departamento: str, municipio: str, ips: str) -> pd.DataFrame:
    """Afiliados de un archivo (los mismos criterios con que se agrupó)."""
    k = _claves(final)
    mascara = ((k["REGIMEN"] == regimen) & (k["DEPARTAMENTO"] == departamento) & (k["MUNICIPIO"] == municipio) & (k["IPS"] == ips))
    return final[mascara.to_numpy()].reset_index(drop=True)
