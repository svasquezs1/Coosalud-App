"""Servidor local del cruce de afiliados. Todo se procesa en la memoria de este proceso."""
from __future__ import annotations
import datetime as dt
import io
import os
import re
import secrets
from functools import wraps

import pandas as pd
from flask import (Flask, Request, Response, abort, g, redirect, render_template, request, url_for)

from cruce import config as C
from cruce import proceso as P
from cruce.exportar import a_bytes
from cruce.lectura import columnas_faltantes, leer_tabla
from cruce.sesion import Almacen, Sesion


class PeticionEnMemoria(Request):
    """Los archivos subidos se mantienen en memoria: nunca se crean archivos temporales en disco."""
    max_form_memory_size = 50 * 1024 * 1024
    max_form_parts = 50_000

    def _get_file_stream(self, total_content_length, content_type, filename=None, content_length=None):
        return io.BytesIO()


app = Flask(__name__)
app.request_class = PeticionEnMemoria
app.config["MAX_CONTENT_LENGTH"] = 1024 * 1024 * 1024        # 1 GB por envío
almacen = Almacen()
USUARIO, CLAVE = os.environ.get("CRUCE_USUARIO", "cruce"), os.environ.get("CRUCE_CLAVE", "")

PASOS = [(1, "Cargar archivos"), (2, "Cruce con BDUA"), (3, "IPS por municipio"), (4, "Documentos y contactos"), (5, "Resumen y descargas")]


# ------------------------------------------------------------------ sesión, acceso y cabeceras
@app.before_request
def antes():
    if CLAVE:  # acceso opcional con usuario y clave (recomendado si el servidor se abre a la red de la empresa)
        a = request.authorization
        if not a or a.username != USUARIO or not secrets.compare_digest(a.password or "", CLAVE):
            return Response("Acceso restringido", 401, {"WWW-Authenticate": 'Basic realm="Cruce de afiliados"'})
    g.sid, g.s = almacen.obtener(request.cookies.get("sid"))


@app.after_request
def despues(resp):
    if hasattr(g, "sid"):
        resp.set_cookie("sid", g.sid, httponly=True, samesite="Strict", secure=request.is_secure)
    resp.headers["Cache-Control"] = "no-store"
    resp.headers["Content-Security-Policy"] = ("default-src 'none'; style-src 'self'; style-src-attr 'unsafe-inline'; "
                                               "img-src 'self' data:; form-action 'self'; base-uri 'none'; frame-ancestors 'none'")
    resp.headers["X-Content-Type-Options"] = "nosniff"
    resp.headers["Referrer-Policy"] = "no-referrer"
    return resp


def fmt(n) -> str:
    return f"{int(n):,}".replace(",", ".")

def pct(a, b) -> str:
    return (f"{100 * a / b:.1f}".replace(".", ",") + "%") if b else "0%"

def mascara(v: str, activa: bool) -> str:
    v = str(v)
    return v if (not activa or len(v) < 5) else v[:2] + "•" * (len(v) - 4) + v[-2:]

def vista(df: pd.DataFrame | None, columnas: list[tuple[str, str]], limite: int = 100) -> dict:
    """Primeras filas de una tabla listas para mostrar (con los documentos ocultos si corresponde)."""
    if df is None or not len(df):
        return {"vacia": True}
    ocultar = g.s.ocultar_docs
    cab = [c for c, _ in columnas]
    filas = [[mascara(v, ocultar) if k == "NUMERO_IDENTIFICACION" else v for k, v in zip([k for _, k in columnas], fila)]
             for fila in df[[k for _, k in columnas]].head(limite).itertuples(index=False, name=None)]
    return {"vacia": False, "cab": cab, "filas": filas, "total": len(df), "limite": limite}

app.jinja_env.globals.update(fmt=fmt, pct=pct, vista=vista, PASOS=PASOS)


@app.context_processor
def contexto():
    return {"s": getattr(g, "s", None), "mensaje": _pop_mensaje()}

def _pop_mensaje():
    if not hasattr(g, "s"):
        return None
    m = getattr(g.s, "_mensaje", None)
    g.s._mensaje = None
    return m

def avisar(tipo: str, texto: str) -> None:
    g.s._mensaje = (tipo, texto)


def requiere_paso(n: int):
    def deco(f):
        @wraps(f)
        def interno(*a, **k):
            if g.s.paso_max < n:
                return redirect(url_for("paso", n=g.s.paso_max))
            return f(*a, **k)
        return interno
    return deco


# ------------------------------------------------------------------ navegación
@app.get("/")
def inicio():
    return redirect(url_for("paso", n=1))

@app.get("/paso/<int:n>")
def paso(n: int):
    if n < 1 or n > 5:
        abort(404)
    s: Sesion = g.s
    if n > s.paso_max:
        return redirect(url_for("paso", n=s.paso_max))
    ctx = dict(n=n)
    if n == 1:
        ctx.update(nac_falt=columnas_faltantes(s.nac_headers, C.REQ_NAC) if s.nac is not None else [],
                   nac_opc=columnas_faltantes(s.nac_headers, C.OPT_NAC) if s.nac is not None else [],
                   bdua_falt={k: columnas_faltantes(h, C.REQ_BDUA) for k, h in s.bdua_headers.items()},
                   estados=_estados(s), listo=_listo(s))
    elif n == 2:
        r = s.r2
        if r is not None:
            ctx.update(
                tabla_no_cruzan=vista(r.no_cruzan, [("Tipo", "TIPO_IDENTIFICACION"), ("Número", "NUMERO_IDENTIFICACION"), ("Estado", "ESTADO"),
                                                    ("Régimen", "REGIMEN"), ("Depto.", "DEPARTAMENTO_PORTABILIDAD"), ("Municipio", "CIUDAD_PORTABILIDAD"), ("IPS", "IPS_ASIGNADA")]),
                tabla_nv=vista(r.nv_filas, [("Tipo", "TIPO_IDENTIFICACION"), ("Número", "NUMERO_IDENTIFICACION"), ("Estado nacional", "ESTADO_NACIONAL"),
                                            ("Depto.", "DEPARTAMENTO"), ("Municipio", "CIUDAD"), ("Tabla BDUA", "TABLA_BDUA"), ("Estado BDUA", "ESTADO_BDUA")], 50))
    elif n == 3:
        r = s.r3
        if r is not None:
            ctx.update(tabla_corregir=vista(r.por_corregir, [("Tipo", "TIPO_IDENTIFICACION"), ("Número", "NUMERO_IDENTIFICACION"), ("Régimen", "REGIMEN"),
                                                              ("Depto.", "DEPARTAMENTO_PORTABILIDAD"), ("Municipio", "CIUDAD_PORTABILIDAD"), ("Nombre", "MUNICIPIO"),
                                                              ("IPS actual", "IPS_ACTUAL"), ("IPS propuesta", "IPS_PROPUESTA")]),
                       sin_regla=r.sin_regla.to_dict("records"))
    elif n == 4:
        r = s.r4
        if not s.fecha_ref:
            s.fecha_ref = dt.date.today().isoformat()
        if r is not None:
            tipos = {}
            for m in r.docs["MOTIVO"] if len(r.docs) else []:
                k = m.split(" con ")[0]; tipos[k] = tipos.get(k, 0) + 1
            ctx.update(por_tipo=sorted(tipos.items(), key=lambda x: -x[1]),
                       tabla_docs=vista(r.docs, [("Tipo", "TIPO_IDENTIFICACION"), ("Número", "NUMERO_IDENTIFICACION"), ("Nacimiento", "FECHA_NACIMIENTO"),
                                                 ("Motivo", "MOTIVO"), ("Fuera de base final", "EXCLUIDO_DE_BASE_FINAL")]),
                       tabla_sin=vista(r.sin_contacto, [("Tipo", "TIPO_IDENTIFICACION"), ("Número", "NUMERO_IDENTIFICACION"), ("Motivo", "MOTIVO"),
                                                        ("Depto.", "DEPARTAMENTO_PORTABILIDAD"), ("Municipio", "CIUDAD_PORTABILIDAD")], 50))
    elif n == 5:
        r4 = s.r4
        top = r4.final["IPS_ASIGNADA"].replace("", "(vacía)").value_counts().head(10)
        ctx.update(top=[(k, int(v)) for k, v in top.items()], tope=int(top.iloc[0]) if len(top) else 1, descargas=_descargas(s))
    return render_template(f"paso{n}.html", **ctx)


def _estados(s: Sesion) -> list[tuple[str, int, bool]]:
    if s.nac is None:
        return []
    cuentas = s.nac["ESTADO"].str.strip().str.upper().replace("", "(VACÍO)").value_counts()
    marcados = set(s.estados) if s.estados is not None else set(C.ESTADOS_VIGENTES_CONOCIDOS)
    return [(k, int(v), k in marcados) for k, v in cuentas.items()]

def _listo(s: Sesion) -> bool:
    return (s.nac is not None and not columnas_faltantes(s.nac_headers, C.REQ_NAC) and bool(s.bdua)
            and all(not columnas_faltantes(h, C.REQ_BDUA) for h in s.bdua_headers.values()))


# ------------------------------------------------------------------ paso 1
@app.post("/paso/1/subir")
def subir():
    s: Sesion = g.s
    try:
        f = request.files.get("nacional")
        if f and f.filename:
            s.nac, s.nac_headers = leer_tabla(f.filename, f.read()); s.nac_nombre = f.filename; s.estados = None
            s.invalidar_desde(2)
        for f in request.files.getlist("bdua"):
            if f and f.filename:
                nombre = os.path.splitext(f.filename)[0]
                s.bdua[nombre], s.bdua_headers[nombre] = leer_tabla(f.filename, f.read())
                s.invalidar_desde(2)
    except Exception as e:  # archivo dañado, formato no válido, etc.
        avisar("err", f"No se pudo leer el archivo: {e}")
    return redirect(url_for("paso", n=1))

@app.post("/paso/1/quitar")
def quitar():
    s: Sesion = g.s; que = request.form.get("que", "")
    if que == "nac":
        s.nac, s.nac_nombre, s.nac_headers, s.estados = None, "", [], None
    elif que.startswith("bdua:"):
        s.bdua.pop(que[5:], None); s.bdua_headers.pop(que[5:], None)
    s.invalidar_desde(2)
    return redirect(url_for("paso", n=1))

@app.post("/paso/1/continuar")
def continuar_1():
    s: Sesion = g.s
    estados = request.form.getlist("estado"); deps = request.form.get("deps", "").strip()
    if not _listo(s):
        avisar("err", "Faltan archivos o columnas requeridas."); return redirect(url_for("paso", n=1))
    if not estados:
        avisar("err", "Marque al menos un estado vigente."); return redirect(url_for("paso", n=1))
    if estados != s.estados or deps != s.deps:
        s.invalidar_desde(2)
    s.estados, s.deps = estados, deps
    s.paso_max = max(s.paso_max, 2)
    return redirect(url_for("paso", n=2))


# ------------------------------------------------------------------ paso 2
@app.post("/paso/2/ejecutar")
@requiere_paso(2)
def ejecutar_2():
    s: Sesion = g.s
    s.solo_numero = request.form.get("solo_numero") == "1"
    deps = {x for x in re.split(r"[,\s;]+", s.deps) if x}
    s.invalidar_desde(3)
    s.r2 = P.cruzar(s.nac, s.bdua, {e.upper() for e in s.estados}, deps, s.solo_numero)
    s.paso_max = max(s.paso_max, 3)
    return redirect(url_for("paso", n=2))


# ------------------------------------------------------------------ paso 3 (reglas de IPS editables)
def _reglas_desde_form() -> list[dict]:
    f = request.form
    dep, city, reg, ips, alt, nom = (f.getlist(k) for k in ("r_dep", "r_city", "r_reg", "r_ips", "r_alt", "r_nombre"))
    return [dict(dep=a.strip(), city=b.strip(), reg=(c.strip().upper() or "*"), ips=d.strip(), alt=e.strip(), nombre=h.strip())
            for a, b, c, d, e, h in zip(dep, city, reg, ips, alt, nom)]

@app.post("/paso/3/accion")
@requiere_paso(3)
def accion_3():
    s: Sesion = g.s; accion = request.form.get("accion", "guardar")
    if "r_dep" in request.form or "r_ips" in request.form:
        nuevas = _reglas_desde_form()
        if nuevas != s.reglas_ips:
            s.reglas_ips = nuevas; s.invalidar_desde(3)
    s.aplicar_ips = request.form.get("aplicar") == "1"
    if accion == "agregar":
        s.reglas_ips.append(dict(dep="05", city="", reg="*", ips="", alt="", nombre="")); s.invalidar_desde(3)
    elif accion.startswith("quitar:"):
        i = int(accion[7:])
        if 0 <= i < len(s.reglas_ips): s.reglas_ips.pop(i); s.invalidar_desde(3)
    elif accion == "restablecer":
        s.reglas_ips = C.reglas_ips_por_defecto(); s.invalidar_desde(3)
    elif accion.startswith("crear:") and s.r3 is not None:
        fila = s.r3.sin_regla.iloc[int(accion[6:])]
        ips = "" if fila["IPS_MAS_FRECUENTE"] == "(vacía)" else fila["IPS_MAS_FRECUENTE"]
        s.reglas_ips.append(dict(dep=fila["DEPARTAMENTO"], city=fila["MUNICIPIO"], reg="*", ips=ips, alt="", nombre="")); s.invalidar_desde(3)
        return redirect(url_for("paso", n=3) + "#reglas")
    elif accion == "importar":
        f = request.files.get("csv_reglas")
        try:
            t, _ = leer_tabla(f.filename, f.read())
            g_ = lambda r, *ks: next((str(r[k]).strip() for k in ks if k in r), "")
            nuevas = [dict(dep=g_(r, "DEPARTAMENTO", "DEP").zfill(2), city=g_(r, "MUNICIPIO", "CIUDAD").zfill(3),
                           reg=(g_(r, "REGIMEN", "REG").upper() or "*"), ips=g_(r, "IPS_POR_DEFECTO", "IPS"),
                           alt=g_(r, "OTRAS_IPS_PERMITIDAS", "ALT"), nombre=g_(r, "NOMBRE")) for r in t.to_dict("records")]
            nuevas = [r for r in nuevas if r["city"].strip("0") and r["ips"]]
            if not nuevas: raise ValueError("el CSV no tiene reglas con municipio e IPS")
            s.reglas_ips = nuevas; s.invalidar_desde(3)
        except Exception as e:
            avisar("err", f"No se pudieron cargar las reglas: {e}")
    elif accion == "ejecutar":
        s.invalidar_desde(4)
        s.r3 = P.revisar_ips(s.r2.base, s.reglas_ips, s.aplicar_ips)
        s.paso_max = max(s.paso_max, 4)
    return redirect(url_for("paso", n=3) + ("#resultados" if accion == "ejecutar" else "#reglas"))

@app.get("/reglas_ips.csv")
def reglas_csv():
    df = pd.DataFrame([{"DEPARTAMENTO": r["dep"], "MUNICIPIO": r["city"], "REGIMEN": r["reg"], "IPS_POR_DEFECTO": r["ips"],
                        "OTRAS_IPS_PERMITIDAS": r["alt"], "NOMBRE": r["nombre"]} for r in g.s.reglas_ips])
    datos, mime = a_bytes(df, "csv")
    return Response(datos, mimetype=mime, headers={"Content-Disposition": "attachment; filename=reglas_IPS.csv"})


# ------------------------------------------------------------------ paso 4
@app.post("/paso/4/accion")
@requiere_paso(4)
def accion_4():
    s: Sesion = g.s; accion = request.form.get("accion", "guardar")
    f = request.form
    nuevas = [dict(tipo=a.strip().upper(), min=b.strip(), max=c.strip(), dias=d.strip())
              for a, b, c, d in zip(f.getlist("d_tipo"), f.getlist("d_min"), f.getlist("d_max"), f.getlist("d_dias"))]
    if nuevas != s.reglas_doc:
        s.reglas_doc = nuevas; s.invalidar_desde(4)
    if f.get("fecha_ref", "") != s.fecha_ref or (f.get("excluir_cn") == "1") != s.excluir_cn:
        s.invalidar_desde(4)
    s.fecha_ref = f.get("fecha_ref", s.fecha_ref); s.excluir_cn = f.get("excluir_cn") == "1"
    if accion == "agregar":
        s.reglas_doc.append(dict(tipo="", min="", max="", dias="")); s.invalidar_desde(4)
    elif accion.startswith("quitar:"):
        i = int(accion[7:])
        if 0 <= i < len(s.reglas_doc): s.reglas_doc.pop(i); s.invalidar_desde(4)
    elif accion == "ejecutar":
        try:
            ref = pd.Timestamp(s.fecha_ref)
            if pd.isna(ref): raise ValueError
        except Exception:
            avisar("err", "Elija la fecha de referencia."); return redirect(url_for("paso", n=4))
        s.invalidar_desde(5)
        s.r4 = P.revisar_documentos_y_contactos(s.r3.base, s.reglas_doc, ref, s.excluir_cn, s.nac_headers)
        s.paso_max = max(s.paso_max, 5)
    return redirect(url_for("paso", n=4) + ("#resultados" if accion == "ejecutar" else ""))


# ------------------------------------------------------------------ paso 5: descargas
def _resumen(s: Sesion) -> pd.DataFrame:
    a, b, c = s.r2, s.r3, s.r4
    filas = [("Filas recibidas del nivel nacional", a.total), ("Con estado vigente", a.vigentes), ("Con otro estado", a.no_vigentes),
             ("Vigentes fuera de los departamentos", a.fuera), ("Repetidos descartados", a.duplicados), ("Afiliados en la base depurada", a.base_n),
             ("Vigentes que cruzan con BDUA", a.cruzan), ("Vigentes que no cruzan con BDUA", len(a.no_cruzan)), ("IPS por corregir", len(b.por_corregir)),
             ("Afiliados en municipios sin regla", b.sin_regla_total), ("Documentos por revisar", len(c.docs)), ("Sin ningún contacto válido", c.cat["ninguno"])]
    return pd.DataFrame(filas, columns=["INDICADOR", "VALOR"])

def _base_final(s: Sesion) -> pd.DataFrame:
    df = s.r4.final
    orig = {h.upper(): h for h in s.nac_headers}
    return df.rename(columns=lambda c: orig.get(c, c))

def _descargas(s: Sesion) -> dict:
    return {
        "base_final_depurada": ("Base final depurada", lambda: _base_final(s), s.r4.final_n),
        "vigentes_que_no_cruzan_con_BDUA": ("Vigentes que no cruzan con BDUA", lambda: s.r2.no_cruzan, len(s.r2.no_cruzan)),
        "no_vigentes_encontrados_en_BDUA": ("No vigentes encontrados en BDUA", lambda: s.r2.nv_filas, len(s.r2.nv_filas)),
        "IPS_por_corregir": ("IPS por corregir", lambda: s.r3.por_corregir, len(s.r3.por_corregir)),
        "municipios_sin_regla_de_IPS": ("Municipios sin regla de IPS", lambda: s.r3.sin_regla, len(s.r3.sin_regla)),
        "documentos_por_revisar": ("Documentos por revisar", lambda: s.r4.docs, len(s.r4.docs)),
        "contactos_para_envio": ("Contactos para envío masivo", lambda: s.r4.contactables, len(s.r4.contactables)),
        "sin_contacto_valido": ("Afiliados sin contacto válido", lambda: s.r4.sin_contacto, len(s.r4.sin_contacto)),
        "resumen_del_ciclo": ("Resumen del ciclo", lambda: _resumen(s), 12),
    }

@app.get("/descargar")
@requiere_paso(5)
def descargar():
    s: Sesion = g.s; clave = request.args.get("clave", ""); formato = "csv" if request.args.get("formato") == "csv" else "xlsx"
    d = _descargas(s)
    if clave not in d:
        abort(404)
    datos, mime = a_bytes(d[clave][1](), formato)
    nombre = f"{clave}_{dt.date.today().isoformat()}.{formato}"
    return Response(datos, mimetype=mime, headers={"Content-Disposition": f'attachment; filename="{nombre}"'})


# ------------------------------------------------------------------ varios
@app.post("/mascara")
def alternar_mascara():
    g.s.ocultar_docs = not g.s.ocultar_docs
    return redirect(request.referrer or url_for("inicio"))

@app.post("/limpiar")
def limpiar():
    almacen.borrar(g.sid)
    g.sid, g.s = almacen.obtener(None)
    return redirect(url_for("paso", n=1))


if __name__ == "__main__":
    from servidor import iniciar
    iniciar(app)
