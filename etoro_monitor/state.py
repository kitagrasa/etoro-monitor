"""Estado persistente entre ejecuciones.

`state.json` guarda **solo datos estables**: el CID de cada usuario y la
lista de PositionID abiertos con sus unidades. A propósito NO guardamos
precios, valor de la cartera ni porcentajes, porque cambian en cada
ejecución: así el fichero solo se modifica cuando alguien compra o vende
de verdad, y el workflow de GitHub Actions solo hace commit cuando hay
un cambio real (nada de 288 commits al día).

Los datos volátiles (última ejecución, hora del último aviso de error)
van a un fichero aparte que no se versiona.
"""

from __future__ import annotations

import json
import os
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional

from .models import EtoroUser, Instrument, Position

STATE_VERSION = 1


@dataclass
class UserState:
    user: EtoroUser
    positions: dict[int, Position] = field(default_factory=dict)
    baseline_sent: bool = False
    # Activos que ya teníamos vistos (para saber qué instrumentos vigilar
    # aunque ahora mismo el usuario no tenga posiciones en ellos).
    known_instruments: set[int] = field(default_factory=set)

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
            "known_instruments": sorted(self.known_instruments),
            "positions": {
                str(pid): pos.to_dict() for pid, pos in sorted(self.positions.items())
            },
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
        positions = {
            int(pid): Position.from_dict(payload)
            for pid, payload in (data.get("positions") or {}).items()
        }
        return cls(
            user=user,
            positions=positions,
            baseline_sent=bool(data.get("baseline_sent")),
            known_instruments={int(i) for i in data.get("known_instruments") or []},
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
        with state_path.open("r", encoding="utf-8") as handle:
            raw = json.load(handle)
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

    def user(self, username: str) -> Optional[UserState]:
        """Busca el estado de un usuario por su nombre, sin distinguir mayúsculas."""
        key = username.lower()
        if key in self.users:
            return self.users[key]
        # Estados antiguos guardados con la capitalización original.
        for name, user_state in self.users.items():
            if name.lower() == key:
                return user_state
        return None

    def upsert_user(self, user: EtoroUser) -> UserState:
        """Crea o actualiza el estado del usuario.

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
            # Migramos la clave antigua a la canónica.
            self.users.pop(existing.user.username, None)
            self.users[key] = existing
        existing.user = user
        return existing

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
        payload = json.dumps(self.to_dict(), indent=2, sort_keys=True, ensure_ascii=False) + "\n"
        if self.path.exists():
            previous = self.path.read_text(encoding="utf-8")
            if previous == payload:
                return False
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with tempfile.NamedTemporaryFile(
            "w", encoding="utf-8", dir=self.path.parent, delete=False
        ) as handle:
            handle.write(payload)
            tmp_name = handle.name
        os.replace(tmp_name, self.path)
        return True


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
