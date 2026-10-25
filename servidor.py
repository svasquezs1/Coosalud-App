"""Arranque del servidor. Por defecto solo escucha en este equipo (127.0.0.1).
Para abrirlo a la red de la empresa: CRUCE_HOST=0.0.0.0 y, por seguridad, defina también CRUCE_CLAVE."""
import os


def iniciar(app):
    host = os.environ.get("CRUCE_HOST", "127.0.0.1")
    puerto = int(os.environ.get("CRUCE_PUERTO", "8080"))
    if host != "127.0.0.1" and not os.environ.get("CRUCE_CLAVE"):
        print("ATENCIÓN: el servidor está abierto a la red y no tiene clave. Defina CRUCE_CLAVE.")
    print(f"Cruce de afiliados en http://{host if host != '0.0.0.0' else 'localhost'}:{puerto}  (Ctrl+C para cerrar)")
    try:
        from waitress import serve
        # Los umbrales altos evitan que waitress use archivos temporales en disco para envíos grandes.
        serve(app, host=host, port=puerto, threads=4, max_request_body_size=1024 ** 3,
              inbuf_overflow=1024 ** 3, outbuf_overflow=1024 ** 3)
    except ImportError:
        print("(waitress no está instalado: se usa el servidor de desarrollo de Flask)")
        app.run(host=host, port=puerto)
