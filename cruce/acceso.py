"""Quién usa la aplicación. Tres situaciones, de la más simple a la más completa:
1. En el propio equipo (127.0.0.1): no se pide nada; el usuario es el del equipo (getpass).
2. Con clave compartida (CRUCE_CLAVE): usuario y clave de la ventana del navegador.
3. Usuario de Windows (CRUCE_AUTH_WINDOWS=1): un servidor web de la empresa (p. ej. IIS con «Autenticación de Windows»)
   va delante de la aplicación y le pasa el usuario en una cabecera. La aplicación solo confía en esa cabecera si la
   petición viene de ese servidor (CRUCE_PROXY_IPS, por defecto este mismo equipo); si no, la ignora."""
from __future__ import annotations
import getpass
import os
from dataclasses import dataclass, field


def normalizar_usuario(texto: str) -> str:
    """«EMPRESA\\scarvajal» y «scarvajal@empresa.com» -> «scarvajal»."""
    t = (texto or "").strip()
    if "\\" in t:
        t = t.rsplit("\\", 1)[1]
    if "@" in t:
        t = t.split("@", 1)[0]
    return t.strip()


def _lista(texto: str) -> frozenset:
    return frozenset(x.strip() for x in (texto or "").replace(";", ",").split(",") if x.strip())


@dataclass(frozen=True)
class Acceso:
    windows: bool = False
    cabecera: str = "X-Remote-User"
    proxies: frozenset = field(default_factory=lambda: frozenset({"127.0.0.1", "::1"}))
    permitidos: frozenset = field(default_factory=frozenset)      # vacío = cualquier usuario autenticado por Windows

    @classmethod
    def desde_entorno(cls, env=None) -> "Acceso":
        env = os.environ if env is None else env
        return cls(windows=env.get("CRUCE_AUTH_WINDOWS", "").strip().lower() in ("1", "si", "sí", "true", "yes"),
                   cabecera=env.get("CRUCE_CABECERA_USUARIO", "X-Remote-User").strip() or "X-Remote-User",
                   proxies=_lista(env.get("CRUCE_PROXY_IPS", "127.0.0.1,::1")),
                   permitidos=frozenset(normalizar_usuario(u).lower() for u in _lista(env.get("CRUCE_USUARIOS_PERMITIDOS", ""))))

    def permite(self, usuario: str) -> bool:
        return not self.permitidos or usuario.lower() in self.permitidos


def usuario_remoto(acceso: Acceso, remote_addr: str | None, cabeceras, environ) -> str | None:
    """Usuario que informa el servidor web de adelante, o None si no viene de un servidor de confianza o no lo trae."""
    if (remote_addr or "") not in acceso.proxies:
        return None
    crudo = cabeceras.get(acceso.cabecera) or environ.get("REMOTE_USER") or ""
    return normalizar_usuario(crudo) or None


def usuario_del_equipo() -> str | None:
    try:
        return getpass.getuser() or None
    except Exception:
        return None
