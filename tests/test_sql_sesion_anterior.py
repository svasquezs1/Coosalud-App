"""Compatibilidad al actualizar el formulario SQL con sesiones abiertas."""
import app


def test_paso_uno_con_catalogo_del_formulario_anterior(monkeypatch):
    monkeypatch.setattr(app, "CLAVE", "")
    from cruce.acceso import Acceso
    monkeypatch.setattr(app, "ACCESO", Acceso())
    monkeypatch.setitem(app.app.config, "TESTING", True)
    sid, sesion = app.almacen.obtener(None)
    sesion.sql = dict(servidor="srv", base_nac="SALUS", base_bdua="BDUA", usuario="", nac=["dbo.nacional"], bdua=["dbo.bdua"])
    cliente = app.app.test_client()
    cliente.set_cookie("sid", sid)
    respuesta = cliente.get("/paso/1")
    assert respuesta.status_code == 200
    assert 'name="base"' in respuesta.get_data(as_text=True)
