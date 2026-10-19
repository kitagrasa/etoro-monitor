"""Comparación de dos fotos de cartera -> lista de operaciones.

Se compara **posición a posición**, no solo el total de unidades. eToro da un
identificador (`PositionID`) a cada operación, así que una foto de la cartera es
una lista de posiciones abiertas con sus unidades. De ahí salen las cuatro
cosas que le interesan a quien sigue a alguien:

  * aparece una posición que no estaba        -> COMPRA (o ABRE CORTO)
  * una posición abierta gana unidades        -> COMPRA PARCIAL
  * una posición abierta pierde unidades      -> VENTA PARCIAL
  * desaparece una posición                   -> VENTA (o CIERRA CORTO)

Comparar posiciones y no el total es lo que permite distinguir una compra de
una compra parcial: si alguien que ya tenía Alphabet compra más, el total de
unidades sube (eso ya se veía), pero además **la posición nueva aparece con su
propio identificador**, que es la prueba de que ha habido una compra.

Las unidades (y no el precio) siguen siendo la base: solo cambian cuando el
usuario opera, así que el mercado no puede generar un aviso falso. El peso del
activo se mueve solo, y por eso no se usa para detectar nada.

La clave de cada activo incluye la dirección, así que un mismo activo puede
detectarse por separado en largo y en corto.
"""

from __future__ import annotations

import statistics
from typing import Optional

from .models import UNITS_DECIMALS, Adjustment, Asset, Change, Position, Snapshot


def diff_snapshots(before: Snapshot, after: Snapshot) -> list[Change]:
    """Devuelve las operaciones detectadas entre dos instantáneas."""
    old = before.assets
    new = after.assets

    old_instruments = {a.instrument_id for a in old.values()}
    new_instruments = {a.instrument_id for a in new.values()}

    changes: list[Change] = []

    for key in sorted(set(old) | set(new)):
        previous = old.get(key)
        current = new.get(key)

        if previous is None:
            changes.append(
                Change(
                    kind="opened",
                    instrument_id=current.instrument_id,
                    direction=current.direction,
                    after=current,
                    instrument_was_new=current.instrument_id not in old_instruments,
                )
            )
            continue

        if current is None:
            changes.append(
                Change(
                    kind="closed",
                    instrument_id=previous.instrument_id,
                    direction=previous.direction,
                    before=previous,
                    instrument_now_empty=previous.instrument_id not in new_instruments,
                )
            )
            continue

        changes.extend(_changes_between(previous, current))

    order = {"opened": 0, "increased": 1, "reduced": 2, "closed": 3}
    changes.sort(
        key=lambda c: (
            order.get(c.kind, 9),
            c.instrument_id,
            c.direction,
            c.position_id or 0,
        )
    )
    return changes


def _changes_between(previous: Asset, current: Asset) -> list[Change]:
    """Compara las posiciones abiertas del mismo activo y lado."""
    comunes = [
        Change(
            kind="increased" if ahora.units > antes.units else "reduced",
            instrument_id=current.instrument_id,
            direction=current.direction,
            before=previous,
            after=current,
            position_id=position_id,
        )
        for position_id, antes, ahora in _positions_changed(previous, current)
    ]

    abiertas = [
        Change(
            kind="opened",
            instrument_id=current.instrument_id,
            direction=current.direction,
            before=previous,
            after=current,
            position_id=position_id,
        )
        for position_id in sorted(set(current.positions) - set(previous.positions))
    ]

    cerradas = [
        Change(
            kind="closed",
            instrument_id=previous.instrument_id,
            direction=previous.direction,
            before=previous,
            after=current,
            position_id=position_id,
        )
        for position_id in sorted(set(previous.positions) - set(current.positions))
    ]

    return comunes + abiertas + cerradas


def _positions_changed(
    previous: Asset, current: Asset
) -> list[tuple[int, Position, Position]]:
    """Posiciones que siguen abiertas y han cambiado de unidades."""
    cambiadas = []
    for position_id, antes in previous.positions.items():
        ahora = current.positions.get(position_id)
        if ahora is None:
            continue
        if _units_changed(antes.units, ahora.units):
            cambiadas.append((position_id, antes, ahora))
    return cambiadas


def _units_changed(old_units: float, new_units: float) -> bool:
    """Compara unidades sin dejarse engañar por el ruido de coma flotante.

    Las unidades se redondean al mismo número de decimales con que se guardan
    en el estado, de modo que el resultado no depende de en qué orden se hayan
    sumado las posiciones (que puede variar entre ejecuciones).
    """
    return round(old_units, UNITS_DECIMALS) != round(new_units, UNITS_DECIMALS)


def summarize_by_instrument(changes: list[Change]) -> dict[str, list[Change]]:
    """Agrupa las operaciones por clave de activo, para redactar el mensaje."""
    grouped: dict[str, list[Change]] = {}
    for change in changes:
        grouped.setdefault(change.asset.key, []).append(change)
    return grouped


# ---------------------------------------------------------------------- #
# Ajustes proporcionales: separarlos de las operaciones reales
# ---------------------------------------------------------------------- #
# Cuántas posiciones tienen que cambiar para poder hablar de "ajuste".
ADJUSTMENT_MIN_POSITIONS = 4
# Qué fracción de las cambiadas tiene que compartir el mismo factor (0,8 = 80%).
ADJUSTMENT_SHARE = 0.8
# Tolerancia al comparar factores (0,001 = una décima de punto porcentual).
ADJUSTMENT_TOLERANCE = 0.001
# Cuánto tiene que bajar como mínimo el factor para considerarlo un ajuste.
ADJUSTMENT_MIN_DROP = 0.0005
# Qué fracción de TODA la cartera tiene que entrar en el ajuste. Es lo que
# distingue un ajuste de cartera (retirada, rebalanceo) de vender un 10% de un
# solo activo con muchas posiciones: lo segundo puede ser la mitad de las
# posiciones cambiadas, pero no de la cartera entera.
ADJUSTMENT_PORTFOLIO_SHARE = 0.5


def split_adjustment(
    changes: list[Change],
    *,
    total_positions: int,
    total_assets: int,
) -> tuple[list[Change], Optional[Adjustment]]:
    """Separa un ajuste proporcional de las operaciones reales.

    La idea (heurística, pero basada en un modelo): si muchas posiciones bajan
    en el MISMO factor, no son muchas ventas, es un solo ajuste de la cartera
    (una retirada de efectivo, un rebalanceo...). En cambio, una venta real de
    verdad tiene su propio factor y no explica al resto.

    Para no confundir una venta grande de UN solo activo con un ajuste, se
    exigen dos cosas más:

    * que el ajuste cubra al menos la mitad de TODAS las posiciones abiertas
      (no solo de las que cambiaron);
    * que toque al menos dos activos distintos (si la cartera solo tiene uno,
      no hay nada que repartir y se omite esta condición).

    Devuelve `(operaciones, ajuste)`:

    * si no hay ajuste, `(changes, None)`;
    * si lo hay, las operaciones que NO forman parte del ajuste y un
      `Adjustment` con el factor y cuántas posiciones entraron.
    """
    candidatos: list[tuple[Change, float]] = []
    for change in changes:
        if change.kind not in ("increased", "reduced"):
            continue
        if change.before is None or change.after is None or change.position_id is None:
            continue
        antes = change.before.positions.get(change.position_id)
        ahora = change.after.positions.get(change.position_id)
        if antes is None or ahora is None or antes.units <= 0:
            continue
        candidatos.append((change, ahora.units / antes.units))

    if len(candidatos) < ADJUSTMENT_MIN_POSITIONS:
        return changes, None

    factor = statistics.median(ratio for _, ratio in candidatos)
    # Solo tiene sentido para bajadas: una subida proporcional es una compra
    # (o algo raro), y no queremos silenciarla.
    if factor >= 1.0 - ADJUSTMENT_MIN_DROP:
        return changes, None

    dentro = [
        (change, ratio)
        for change, ratio in candidatos
        if abs(ratio - factor) <= ADJUSTMENT_TOLERANCE
    ]
    if len(dentro) / len(candidatos) < ADJUSTMENT_SHARE:
        return changes, None

    # Tiene que ser una parte grande de TODA la cartera, no solo de lo que
    # cambió: si no, vender el 10% de un activo con 20 posiciones se colaría.
    if total_positions > 0 and len(dentro) / total_positions < ADJUSTMENT_PORTFOLIO_SHARE:
        return changes, None

    # Y tiene que repartirse entre varios activos. Un solo activo, por muy
    # grande que sea, es una venta de ese activo, no un ajuste de cartera.
    activos = {change.instrument_id for change, _ in dentro}
    if total_assets > 1 and len(activos) < 2:
        return changes, None

    ajustadas = {id(change) for change, _ in dentro}
    operaciones = [change for change in changes if id(change) not in ajustadas]
    return operaciones, Adjustment(factor=factor, positions=len(dentro))


