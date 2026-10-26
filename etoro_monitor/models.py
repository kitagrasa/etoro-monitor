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

# Cuántos decimales se enseñan del peso en el mensaje. Es también la precisión
# a la que se decide si un cambio "se ve": una compra que no mueve este decimal
# no se puede contar (el mensaje diría el mismo porcentaje dos veces) y no hay
# nada que copiar. Ver `diff.quitar_compras_invisibles`.
PESO_DECIMALES = 2


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
class Position:
    """Una posición abierta de un activo, tal como la publica eToro.

    De todo lo que devuelve el endpoint público de posiciones solo se guardan
    dos cosas:

    * `position_id`: el identificador que eToro da **a cada operación**. Es lo
      que permite distinguir "ha abierto una posición que no tenía" de "ha
      metido más dinero en una que ya tenía". Sin él, dos compras distintas se
      ven como un solo montón de unidades y no hay forma de saber si lo que ha
      pasado es una compra nueva o una ampliación.
    * `units` (`Amount`): cuántas unidades tiene esa posición. Sumando las
      posiciones sale el total del activo, que es lo que **solo cambia cuando
      el usuario opera**: el precio del activo no lo toca, ni el del resto de
      la cartera.

    A propósito NO se guarda nada más: ni precios de entrada, ni fechas de
    apertura, ni ganancias, ni apalancamiento.
    """

    position_id: int
    units: float

    def to_dict(self) -> dict[str, Any]:
        return {
            "position_id": self.position_id,
            "units": round(self.units, UNITS_DECIMALS),
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "Position":
        return cls(
            position_id=int(data["position_id"]),
            units=float(data.get("units") or 0.0),
        )


@dataclass(frozen=True)
class Asset:
    """Lo que recordamos de cada activo (y lado) de la cartera.

    Sus unidades totales, que es lo único que decide si hubo operación. Se
    pueden saber de dos maneras:

    * `total` (modo cartera, el normal): el campo `Invested` del agregado de la
      cartera es **exactamente la suma de unidades de las posiciones** — en los
      71 activos de las carteras que se siguen cuadra al céntimo — y no se mueve
      con el mercado. Con eso basta y no hace falta pedir las posiciones: una
      petición por persona en vez de una por activo.
    * `positions` (una entrada por operación abierta): la vía de siempre, que
      se sigue usando en los **cortos**, donde no está comprobado que
      `Invested` sea la suma de unidades, y para los estados antiguos.

    `direction` ("Buy"/"Sell") dice si el activo está en largo o en corto, e
    `invested_pct` es el peso que se enseña en el mensaje: en el modo cartera es
    el mismo número que `total`.
    """

    instrument_id: int
    direction: str
    positions: dict[int, Position] = field(default_factory=dict)
    invested_pct: float = 0.0
    # Unidades totales cuando no se leen las posiciones. `None` = se suman.
    total: Optional[float] = None

    @property
    def key(self) -> str:
        """Clave estable en el estado: un activo puede estar en largo y en corto."""
        return f"{self.instrument_id}:{self.direction}"

    @property
    def is_short(self) -> bool:
        return self.direction.strip().lower() == "sell"

    @property
    def units(self) -> float:
        """Total de unidades del activo: el total o la suma de sus posiciones."""
        if self.total is not None:
            return self.total
        return sum(p.units for p in self.positions.values())

    @property
    def por_posiciones(self) -> bool:
        """¿Las unidades salen de las posiciones (cortos / estados antiguos)?"""
        return bool(self.positions) or self.total is None

    def to_dict(self) -> dict[str, Any]:
        return {
            "instrument_id": self.instrument_id,
            "direction": self.direction,
            "units": round(self.units, UNITS_DECIMALS),
            "invested_pct": round(self.invested_pct, 4),
            "positions": [
                position.to_dict()
                for position in sorted(
                    self.positions.values(), key=lambda p: p.position_id
                )
            ],
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "Asset":
        raw_positions = data.get("positions") or []
        positions = {
            position.position_id: position
            for position in (Position.from_dict(item) for item in raw_positions)
        }
        # Sin posiciones guardadas, el total está en `units` (el estado nuevo
        # guarda solo el total; el antiguo, también, y así se migra sin perder
        # el dato).
        total: Optional[float] = None
        if not positions and data.get("units") is not None:
            total = float(data["units"])
        return cls(
            instrument_id=int(data["instrument_id"]),
            direction=str(data.get("direction") or "Buy"),
            positions=positions,
            invested_pct=float(data.get("invested_pct") or 0.0),
            total=total,
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
    # El activo se queda sin ninguna posición (de este lado) después del cambio.
    instrument_now_empty: bool = False
    # Identificador de la posición de eToro a la que se refiere la operación.
    position_id: Optional[int] = None

    @property
    def asset(self) -> Asset:
        asset = self.after or self.before
        assert asset is not None
        return asset

    @property
    def is_short(self) -> bool:
        return self.asset.is_short

    @property
    def is_partial(self) -> bool:
        """¿La compra o la venta ha sido parcial?

        * abrir una posición de un activo del que **ya tenía algo** (porque
          antes abrió otra operación) es una **compra parcial**: está metiendo
          más dinero en algo que ya estaba en su cartera;
        * añadir unidades a una posición abierta es una **compra parcial**;
        * quitar unidades de una posición abierta es una **venta parcial**;
        * cerrar una posición es una venta, parcial si le quedan otras
          posiciones abiertas del mismo activo y lado.
        """
        if self.kind == "opened":
            return self.before is not None
        if self.kind in ("increased", "reduced"):
            return True
        if self.kind == "closed":
            # Si no queda nada del activo, la venta ha sido total.
            if self.after is not None and self.after.positions:
                return True
            return self.instrument_now_empty is False
        return False

    @property
    def weight_before(self) -> Optional[float]:
        return self.before.invested_pct if self.before else None

    @property
    def weight_after(self) -> Optional[float]:
        return self.after.invested_pct if self.after else None


KIND_ORDER = {"opened": 0, "increased": 1, "reduced": 2, "closed": 3}


@dataclass(frozen=True)
class Adjustment:
    """Un ajuste proporcional de la cartera, no una operación.

    eToro (o una retirada de efectivo, o un rebalanceo) a veces reduce TODAS
    las posiciones en el mismo factor: por ejemplo, un 0,30% en las 143
    posiciones a la vez. Eso no son 143 ventas: es un solo ajuste. Se detecta
    porque muchas posiciones cambian con el mismo factor (ver
    `diff.split_adjustment`), y se resume en una línea o no se avisa.

    `factor` es el multiplicador (0,997 = bajar un 0,30%).
    `positions` es cuántos elementos entraron en el ajuste (activos, desde que se
    compara el total de cada uno).
    """

    factor: float
    positions: int

    @property
    def percentage(self) -> float:
        """La bajada en %, para el mensaje (0,30)."""
        return (1.0 - self.factor) * 100.0

