"""Modelos de datos del monitor.

La pieza clave es `Asset`: lo mínimo que hay que recordar de un activo para
detectar operaciones sin falsos positivos. Ver el comentario de esa clase.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Optional

# Cuántos decimales se conservan de las unidades. eToro publica las unidades
# con 6 decimales; 8 da margen de sobra y hace que la comparación sea exacta
# (evita diferencias de 1e-15 al sumar muchas posiciones en distinto orden).
UNITS_DECIMALS = 8


@dataclass(frozen=True)
class EtoroUser:
    """Usuario de eToro resuelto a partir de su nombre de usuario."""

    username: str
    gcid: int
    real_cid: int
    demo_cid: Optional[int] = None
    first_name: str = ""
    last_name: str = ""
    allow_display_full_name: bool = False
    avatar_url: str = ""

    @property
    def slug(self) -> str:
        """Forma canónica del usuario: minúsculas.

        eToro redirige /people/UsuarioEjemplo a /people/usuarioejemplo, así
        que usamos siempre minúsculas tanto en los enlaces como en el estado.
        """
        return self.username.lower()

    @property
    def display_name(self) -> str:
        if self.allow_display_full_name:
            full = f"{self.first_name} {self.last_name}".strip()
            if full:
                return full
        return self.username

    @property
    def portfolio_url(self) -> str:
        return f"https://www.etoro.com/people/{self.slug}/portfolio"

    def to_dict(self) -> dict[str, Any]:
        return {
            "username": self.username,
            "slug": self.slug,
            "gcid": self.gcid,
            "real_cid": self.real_cid,
            "demo_cid": self.demo_cid,
            "first_name": self.first_name,
            "last_name": self.last_name,
            "allow_display_full_name": self.allow_display_full_name,
            "avatar_url": self.avatar_url,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "EtoroUser":
        return cls(
            username=data["username"],
            gcid=int(data["gcid"]),
            real_cid=int(data["real_cid"]),
            demo_cid=data.get("demo_cid"),
            first_name=data.get("first_name", ""),
            last_name=data.get("last_name", ""),
            allow_display_full_name=bool(data.get("allow_display_full_name")),
            avatar_url=data.get("avatar_url", ""),
        )


@dataclass(frozen=True)
class Instrument:
    """Activo (instrumento) de eToro, solo para ponerle nombre a los avisos."""

    instrument_id: int
    name: str
    symbol: str = ""

    @property
    def label(self) -> str:
        if self.symbol and self.symbol.lower() != self.name.lower():
            return f"{self.name} ({self.symbol})"
        return self.name

    def to_dict(self) -> dict[str, Any]:
        return {
            "instrument_id": self.instrument_id,
            "name": self.name,
            "symbol": self.symbol,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "Instrument":
        return cls(
            instrument_id=int(data["instrument_id"]),
            name=data.get("name") or f"Instrumento {data['instrument_id']}",
            symbol=data.get("symbol", ""),
        )


@dataclass(frozen=True)
class Asset:
    """Lo que recordamos de cada activo de la cartera.

    Por qué estas tres cosas y no otras:

    * `units` (suma de unidades de todas las posiciones abiertas) es el único
      dato que **solo cambia cuando el usuario opera**. El precio del activo no
      lo toca, ni el del resto de la cartera. Comparando unidades se distingue
      "ha comprado" de "el mercado se ha movido", que es justo lo que hay que
      distinguir para no dar falsos avisos.
    * `direction` ("Buy"/"Sell") dice si el activo está en largo o en corto.
    * `invested_pct` (campo `Invested` de eToro: porcentaje de la cartera que
      supone el coste del activo) es solo **contexto para el mensaje**. No se
      usa para detectar nada, porque se mueve con el mercado.

    A propósito NO guardamos: identificadores de posición, unidades de cada
    posición, fechas de apertura, precios de entrada ni ganancias.
    """

    instrument_id: int
    direction: str
    units: float
    invested_pct: float = 0.0

    @property
    def key(self) -> str:
        """Clave estable en el estado: un activo puede estar en largo y en corto."""
        return f"{self.instrument_id}:{self.direction}"

    @property
    def is_short(self) -> bool:
        return self.direction.strip().lower() == "sell"

    def to_dict(self) -> dict[str, Any]:
        return {
            "instrument_id": self.instrument_id,
            "direction": self.direction,
            "units": round(self.units, UNITS_DECIMALS),
            "invested_pct": round(self.invested_pct, 4),
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "Asset":
        return cls(
            instrument_id=int(data["instrument_id"]),
            direction=str(data.get("direction") or "Buy"),
            units=float(data.get("units") or 0.0),
            invested_pct=float(data.get("invested_pct") or 0.0),
        )


@dataclass
class Snapshot:
    """Foto de la cartera de un usuario en un momento dado."""

    user: EtoroUser
    assets: dict[str, Asset] = field(default_factory=dict)

    @property
    def instrument_ids(self) -> set[int]:
        return {a.instrument_id for a in self.assets.values()}

    def weight_of(self, instrument_id: int) -> Optional[float]:
        for asset in self.assets.values():
            if asset.instrument_id == instrument_id:
                return asset.invested_pct
        return None


@dataclass(frozen=True)
class Change:
    """Una operación detectada en un activo."""

    kind: str  # opened | increased | reduced | closed
    instrument_id: int
    direction: str
    before: Optional[Asset] = None
    after: Optional[Asset] = None
    # El activo no existía en la cartera antes de este cambio.
    instrument_was_new: bool = False
    # El activo se queda sin ninguna posición después de este cambio.
    instrument_now_empty: bool = False

    @property
    def asset(self) -> Asset:
        asset = self.after or self.before
        assert asset is not None
        return asset

    @property
    def is_short(self) -> bool:
        return self.asset.is_short

    @property
    def weight_before(self) -> Optional[float]:
        return self.before.invested_pct if self.before else None

    @property
    def weight_after(self) -> Optional[float]:
        return self.after.invested_pct if self.after else None


KIND_ORDER = {"opened": 0, "increased": 1, "reduced": 2, "closed": 3}
