"""Control de repetición de avisos, guardado en un fichero versionado.

Problema que resuelve: si un usuario se equivoca al escribir un nombre, eToro
devuelve "no existe" en cada ejecución. Sin control, eso son decenas de
mensajes idénticos al día. Y con el fichero volátil de antes no bastaba,
porque en GitHub Actions cada ejecución arranca de un repositorio limpio y la
marca de tiempo se perdía.

Solución: `state/cooldown.json`, que sí se versiona, con una marca de tiempo
por cada aviso y **por usuario**, de modo que un problema en una persona no
silencie el de otra.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Optional

from .state import _write_if_changed

log = logging.getLogger(__name__)

COOLDOWN_VERSION = 1
# Los avisos más viejos que esto se olvidan, para que el fichero no crezca.
MAX_AGE_DAYS = 30


@dataclass
class CooldownStore:
    path: Path
    sent: dict[str, str] = field(default_factory=dict)
    version: int = COOLDOWN_VERSION

    # -------------------------------------------------------------- #
    @classmethod
    def load(cls, path: str | Path) -> "CooldownStore":
        store_path = Path(path)
        if not store_path.exists():
            return cls(path=store_path)
        try:
            raw = json.loads(store_path.read_text(encoding="utf-8") or "{}")
        except (ValueError, OSError) as exc:
            # Un cooldown ilegible no debe impedir el funcionamiento: se
            # empieza de cero (peor caso, se repite algún aviso).
            log.warning("No puedo leer %s (%s); empiezo de cero", store_path, exc)
            return cls(path=store_path)
        if not isinstance(raw, dict):
            return cls(path=store_path)
        sent = {
            str(k): str(v)
            for k, v in (raw.get("sent") or {}).items()
            if isinstance(v, str)
        }
        return cls(
            path=store_path,
            sent=sent,
            version=int(raw.get("version", COOLDOWN_VERSION)),
        )

    # -------------------------------------------------------------- #
    def is_ready(self, key: str, *, minutes: int, now: Optional[datetime] = None) -> bool:
        """¿Toca avisar de `key`, o se avisó hace menos de `minutes`?"""
        if minutes <= 0:
            return True
        last = self._timestamp(key, now=now)
        if last is None:
            return True
        return (self._now(now) - last) >= timedelta(minutes=minutes)

    def mark(self, key: str, *, now: Optional[datetime] = None) -> None:
        self.sent[key] = self._now(now).isoformat()

    def forget(self, key: str) -> None:
        self.sent.pop(key, None)

    def prune(self, *, now: Optional[datetime] = None) -> int:
        """Olvida los avisos antiguos. Devuelve cuántos ha borrado."""
        limite = self._now(now) - timedelta(days=MAX_AGE_DAYS)
        viejos = []
        for key, value in self.sent.items():
            try:
                if datetime.fromisoformat(value) < limite:
                    viejos.append(key)
            except ValueError:
                viejos.append(key)
        for key in viejos:
            del self.sent[key]
        return len(viejos)

    def save(self) -> bool:
        """Guarda el fichero. Devuelve True si el contenido ha cambiado."""
        payload = (
            json.dumps(
                {"version": self.version, "sent": self.sent},
                indent=2,
                sort_keys=True,
                ensure_ascii=False,
            )
            + "\n"
        )
        return _write_if_changed(self.path, payload)

    # -------------------------------------------------------------- #
    def _timestamp(self, key: str, *, now: Optional[datetime]) -> Optional[datetime]:
        raw = self.sent.get(key)
        if not raw:
            return None
        try:
            parsed = datetime.fromisoformat(raw)
        except ValueError:
            return None
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return parsed

    @staticmethod
    def _now(now: Optional[datetime]) -> datetime:
        momento = now or datetime.now(timezone.utc)
        if momento.tzinfo is None:
            momento = momento.replace(tzinfo=timezone.utc)
        return momento


def error_key(slug: str) -> str:
    """Clave de cooldown para los errores de un usuario concreto."""
    return f"error:{slug.lower()}"


#: El fichero de estado no se puede leer.
STATE_ERROR_KEY = "error:state"

#: eToro ha bloqueado las peticiones (captcha o límite de frecuencia). Es un
#: problema del sitio entero, así que se avisa una vez, no una por usuario.
BLOCKED_KEY = "error:blocked"
