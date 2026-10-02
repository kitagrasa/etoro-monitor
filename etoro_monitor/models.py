"""Modelos de datos del monitor."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Optional


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
    """Activo (instrumento) de eToro."""

    instrument_id: int
    name: str
    symbol: str = ""
    instrument_type_id: int = 0
    industry_id: int = 0
    exchange_id: int = 0

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
            "instrument_type_id": self.instrument_type_id,
            "industry_id": self.industry_id,
            "exchange_id": self.exchange_id,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "Instrument":
        return cls(
            instrument_id=int(data["instrument_id"]),
            name=data.get("name") or f"Instrumento {data['instrument_id']}",
            symbol=data.get("symbol", ""),
            instrument_type_id=int(data.get("instrument_type_id") or 0),
            industry_id=int(data.get("industry_id") or 0),
            exchange_id=int(data.get("exchange_id") or 0),
        )


@dataclass(frozen=True)
class Position:
    """Una posición individual abierta (PositionID) de un usuario.

    En eToro una "posición" es cada compra concreta: si alguien compra el
    mismo activo tres veces tiene 3 posiciones con 3 PositionID distintos.
    Esa es la clave que nos permite detectar compras y ventas sin ambigüedad.
    """

    position_id: int
    instrument_id: int
    is_buy: bool
    amount: float
    open_rate: float
    open_datetime: str = ""
    leverage: int = 1
    mirror_id: int = 0

    @property
    def direction(self) -> str:
        return "long" if self.is_buy else "short"

    def to_dict(self) -> dict[str, Any]:
        return {
            "position_id": self.position_id,
            "instrument_id": self.instrument_id,
            "is_buy": self.is_buy,
            "amount": round(self.amount, 8),
            "open_rate": round(self.open_rate, 8),
            "open_datetime": self.open_datetime,
            "leverage": self.leverage,
            "mirror_id": self.mirror_id,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "Position":
        return cls(
            position_id=int(data["position_id"]),
            instrument_id=int(data["instrument_id"]),
            is_buy=bool(data["is_buy"]),
            amount=float(data["amount"]),
            open_rate=float(data["open_rate"]),
            open_datetime=data.get("open_datetime", ""),
            leverage=int(data.get("leverage") or 1),
            mirror_id=int(data.get("mirror_id") or 0),
        )


@dataclass
class Snapshot:
    """Foto de la cartera de un usuario en un momento dado."""

    user: EtoroUser
    positions: dict[int, Position] = field(default_factory=dict)
    # instrument_id -> % de la cartera (suma 100 en toda la cartera)
    weights: dict[int, float] = field(default_factory=dict)
    # instrument_id -> "Buy" / "Sell" según la vista agregada de eToro
    directions: dict[int, str] = field(default_factory=dict)

    def positions_of(self, instrument_id: int) -> list[Position]:
        return [p for p in self.positions.values() if p.instrument_id == instrument_id]

    @property
    def instrument_ids(self) -> set[int]:
        return {p.instrument_id for p in self.positions.values()}


@dataclass(frozen=True)
class Change:
    """Un cambio detectado entre dos fotos de la cartera."""

    kind: str  # opened | increased | reduced | closed
    instrument_id: int
    position_id: int
    before: Optional[Position] = None
    after: Optional[Position] = None
    # El activo no existía en la cartera antes de este cambio (apertura).
    instrument_was_new: bool = False
    # El activo se queda sin ninguna posición después de este cambio (cierre total).
    instrument_now_empty: bool = False


KIND_ORDER = {"opened": 0, "increased": 1, "reduced": 2, "closed": 3}
