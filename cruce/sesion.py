"""Estado de trabajo de cada persona, guardado SOLO en la memoria del servidor.
No hay base de datos ni archivos temporales: al cerrar el servidor, pulsar «Limpiar» o pasar el
tiempo de inactividad, todo desaparece."""
from __future__ import annotations
import secrets
import threading
import time
from dataclasses import dataclass, field
from typing import Optional
import pandas as pd

from . import config as C

INACTIVIDAD_SEGUNDOS = 30 * 60


@dataclass
class Sesion:
    paso_max: int = 1
    ocultar_docs: bool = True
    nac: Optional[pd.DataFrame] = None
    nac_nombre: str = ""
    nac_orig: list = field(default_factory=list)      # encabezados tal como vienen en el archivo
    nac_headers: list = field(default_factory=list)   # encabezados ya con los alias aplicados
    bdua: dict = field(default_factory=dict)          # nombre -> DataFrame
    bdua_orig: dict = field(default_factory=dict)
    bdua_headers: dict = field(default_factory=dict)
    alias: list = field(default_factory=C.alias_columnas_por_defecto)
    estados: Optional[list] = None                    # estados que se marcaron como vigentes
    deps: str = C.DEPARTAMENTOS_POR_DEFECTO
    llaves: list = field(default_factory=lambda: ["tipo_numero"])
    reglas_ips: list = field(default_factory=C.reglas_ips_por_defecto)
    aplicar_ips: bool = True
    prestadores: list = field(default_factory=C.prestadores_por_defecto)
    reglas_doc: list = field(default_factory=C.reglas_documento_por_defecto)
    fecha_ref: str = ""
    excluir_cn: bool = True
    formato: str = "xlsx"
    r2: object = None
    r3: object = None
    r4: object = None
    ultimo: float = field(default_factory=time.time)

    def invalidar_desde(self, n: int) -> None:
        """Borra los resultados del paso n en adelante (cada paso depende del anterior)."""
        self.paso_max = min(self.paso_max, 1 if n <= 2 else n)
        if n <= 2: self.r2 = None
        if n <= 3: self.r3 = None
        if n <= 4: self.r4 = None


class Almacen:
    def __init__(self) -> None:
        self._d: dict[str, Sesion] = {}
        self._lock = threading.Lock()

    def obtener(self, sid: Optional[str]) -> tuple[str, Sesion]:
        ahora = time.time()
        with self._lock:
            for k in [k for k, s in self._d.items() if ahora - s.ultimo > INACTIVIDAD_SEGUNDOS]:
                del self._d[k]
            if not sid or sid not in self._d:
                sid = secrets.token_urlsafe(24)
                self._d[sid] = Sesion()
            s = self._d[sid]
            s.ultimo = ahora
            return sid, s

    def borrar(self, sid: str) -> None:
        with self._lock:
            self._d.pop(sid, None)
