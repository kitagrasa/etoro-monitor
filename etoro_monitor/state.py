"""Estado persistente entre ejecuciones.

`state/state.json` guarda **solo lo imprescindible** para detectar operaciones:
por cada activo, sus unidades totales, su dirección (largo/corto) y su peso como
contexto. No guarda identificadores de posición, ni fechas, ni precios, ni
ganancias.

Hay tres ficheros, con papeles distintos:

* `state.json`   -> la memoria del monitor. Versionado: el workflow lo commitea.
* `cooldown.json`-> cuándo se avisó por última vez de cada problema. Versionado,
                    para que el "no repetir avisos" funcione de verdad en
                    GitHub Actions (donde cada ejecución empieza de cero).
* `*.runtime.json`-> datos volátiles de una ejecución. NO versionado.
"""

from __future__ import annotations

import json
import logging
import os
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable, Optional

from .models import Asset, EtoroUser, Instrument

STATE_VERSION = 2
COOLDOWN_FILE = "cooldown.json"

log = logging.getLogger(__name__)


class StateCorruptError(RuntimeError):
    """El fichero de estado no se puede leer.

    Se deja intacto a propósito: quien lo edite a mano puede repararlo, y
    regenerarlo desde cero dispararía avisos falsos de "lo ha vendido todo".
    """

    def __init__(self, path: Path, causa: Exception) -> None:
        self.path = path
        self.causa = causa
        super().__init__(f"El fichero de estado {path} está dañado: {causa}")


@dataclass
class UserState:
    """La memoria de un usuario: sus activos y si ya se le mandó la línea base.

    No se guarda nada más. En particular, no hay lista de "instrumentos
    conocidos": se tuvo una vez y era peso muerto que crecía sin límite y no
    se leía en ningún sitio.
    """

    user: EtoroUser
    assets: dict[str, Asset] = field(default_factory=dict)
    baseline_sent: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "username": self.user.username,
            "slug": self.user.slug,
            "gcid": self.user.gcid,
            "real_cid": self.user.real_cid,
            "demo_cid": self.user.demo_cid,
            "display_name": self.user.display_name,
            "allow_display_full_name": self.user.allow_display_full_name,
            "avatar_url": self.user.avatar_url,
            "baseline_sent": self.baseline_sent,
            "assets": {key: asset.to_dict() for key, asset in sorted(self.assets.items())},
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "UserState":
        user = EtoroUser(
            username=data["username"],
            gcid=int(data["gcid"]),
            real_cid=int(data["real_cid"]),
            demo_cid=data.get("demo_cid"),
            first_name="",
            last_name="",
            allow_display_full_name=bool(data.get("allow_display_full_name")),
            avatar_url=data.get("avatar_url", ""),
        )
        raw_assets = data.get("assets") or {}
        assets = {
            str(key): Asset.from_dict(payload) for key, payload in raw_assets.items()
        }
        return cls(
            user=user,
            assets=assets,
            baseline_sent=bool(data.get("baseline_sent")),
        )


@dataclass
class State:
    path: Path
    instruments: dict[int, Instrument] = field(default_factory=dict)
    users: dict[str, UserState] = field(default_factory=dict)
    version: int = STATE_VERSION

    # -------------------------------------------------------------- #
    @classmethod
    def load(cls, path: str | os.PathLike[str]) -> "State":
        state_path = Path(path)
        if not state_path.exists():
            return cls(path=state_path)

        raw_text = state_path.read_text(encoding="utf-8").strip()
        if not raw_text:
            return cls(path=state_path)
        try:
            raw = json.loads(raw_text)
        except ValueError as exc:
            raise StateCorruptError(state_path, exc) from exc
        if not isinstance(raw, dict):
            raise StateCorruptError(
                state_path, ValueError("el contenido no es un objeto JSON")
            )

        instruments = {
            int(iid): Instrument.from_dict(payload)
            for iid, payload in (raw.get("instruments") or {}).items()
        }
        users = {
            name: UserState.from_dict(payload)
            for name, payload in (raw.get("users") or {}).items()
        }
        return cls(
            path=state_path,
            instruments=instruments,
            users=users,
            version=int(raw.get("version", STATE_VERSION)),
        )

    # -------------------------------------------------------------- #
    def user(self, username: str) -> Optional[UserState]:
        """Busca el estado de un usuario por su nombre, sin distinguir mayúsculas."""
        key = username.lower()
        if key in self.users:
            return self.users[key]
        for name, user_state in self.users.items():
            if name.lower() == key:
                return user_state
        return None

    def upsert_user(self, user: EtoroUser) -> UserState:
        """Crea o actualiza el estado de un usuario.

        La clave es siempre el nombre en minúsculas, de modo que si en
        watchlist.md escribes "UsuarioEjemplo" en vez de "usuarioejemplo" no
        se pierde la memoria acumulada ni recibes una línea base de más.
        """
        key = user.slug
        existing = self.user(user.username)
        if existing is None:
            existing = UserState(user=user)
            self.users[key] = existing
        elif key not in self.users:
            self.users.pop(existing.user.username, None)
            self.users[key] = existing
        existing.user = user
        return existing

    def prune_users(self, keep: Iterable[str]) -> list[str]:
        """Olvida a los usuarios que ya no están en la lista de seguimiento.

        Así no se acumulan datos de gente a la que has dejado de seguir, y al
        volver a añadirla empieza limpia (con su línea base) en vez de avisarte
        de todas las operaciones que hizo mientras no la mirabas.
        """
        wanted = {name.lower() for name in keep}
        sobrantes = [
            name
            for name in self.users
            if name.lower() not in wanted
        ]
        for name in sobrantes:
            del self.users[name]
        return sobrantes

    def instrument_label(self, instrument_id: int) -> str:
        instrument = self.instruments.get(instrument_id)
        if instrument is None:
            return f"Instrumento {instrument_id}"
        return instrument.label

    def to_dict(self) -> dict[str, Any]:
        return {
            "version": self.version,
            "instruments": {
                str(iid): inst.to_dict() for iid, inst in sorted(self.instruments.items())
            },
            "users": {
                name: user_state.to_dict() for name, user_state in sorted(self.users.items())
            },
        }

    def save(self) -> bool:
        """Guarda el estado. Devuelve True si el contenido ha cambiado."""
        return _write_if_changed(
            self.path,
            json.dumps(self.to_dict(), indent=2, sort_keys=True, ensure_ascii=False) + "\n",
        )


class RuntimeState:
    """Datos volátiles que no queremos versionar (evita commits innecesarios)."""

    def __init__(self, path: str | os.PathLike[str]) -> None:
        self.path = Path(path)
        self.data: dict[str, Any] = {}
        if self.path.exists():
            try:
                self.data = json.loads(self.path.read_text(encoding="utf-8"))
            except (ValueError, OSError):
                self.data = {}

    def get(self, key: str, default: Any = None) -> Any:
        return self.data.get(key, default)

    def set(self, key: str, value: Any) -> None:
        self.data[key] = value

    def save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(
            json.dumps(self.data, indent=2, sort_keys=True, ensure_ascii=False) + "\n",
            encoding="utf-8",
        )


def cooldown_path(state_path: str | os.PathLike[str]) -> Path:
    """Dónde vive cooldown.json, junto al fichero de estado."""
    return Path(state_path).parent / COOLDOWN_FILE


def _write_if_changed(path: Path, payload: str) -> bool:
    if path.exists():
        try:
            if path.read_text(encoding="utf-8") == payload:
                return False
        except OSError:
            pass
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(
        "w", encoding="utf-8", dir=path.parent, delete=False
    ) as handle:
        handle.write(payload)
        tmp_name = handle.name
    os.replace(tmp_name, path)
    return True
