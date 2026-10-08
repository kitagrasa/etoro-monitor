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

from .models import UNITS_DECIMALS, Asset, Change, Position, Snapshot


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
