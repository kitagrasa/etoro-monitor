"""Comparación de dos fotos de cartera -> lista de cambios.

La clave de todo el sistema: cada compra en eToro crea un `PositionID`
nuevo e inmutable. Comparando los conjuntos de PositionID entre dos
ejecuciones detectamos, sin ruido de precios:

  * PositionID nuevo         -> se ha COMPRADO (abrir o ampliar)
  * PositionID desaparecido  -> se ha VENDIDO (cerrar)
  * mismo ID, menos unidades -> ha REDUCIDO (venta parcial)
  * mismo ID, más unidades   -> ha AMPLIADO (compra parcial)

El ruido típico (que el precio suba o baje) no altera ni los PositionID ni
las unidades, así que no genera falsos positivos.
"""

from __future__ import annotations

from typing import Iterable

from .models import Change, Snapshot

# Tolerancia para comparar unidades (evita falsos positivos por redondeo).
_AMOUNT_RTOL = 1e-6
_AMOUNT_ATOL = 1e-9


def diff_snapshots(before: Snapshot, after: Snapshot) -> list[Change]:
    """Devuelve los cambios de cartera entre dos instantáneas."""
    old = before.positions
    new = after.positions

    old_instruments = {p.instrument_id for p in old.values()}
    new_instruments = {p.instrument_id for p in new.values()}

    changes: list[Change] = []

    for position_id in sorted(set(new) - set(old)):
        position = new[position_id]
        changes.append(
            Change(
                kind="opened",
                instrument_id=position.instrument_id,
                position_id=position_id,
                after=position,
                instrument_was_new=position.instrument_id not in old_instruments,
            )
        )

    for position_id in sorted(set(old) & set(new)):
        previous = old[position_id]
        current = new[position_id]
        if _amount_changed(previous.amount, current.amount):
            changes.append(
                Change(
                    kind="increased" if current.amount > previous.amount else "reduced",
                    instrument_id=current.instrument_id,
                    position_id=position_id,
                    before=previous,
                    after=current,
                )
            )

    for position_id in sorted(set(old) - set(new)):
        position = old[position_id]
        changes.append(
            Change(
                kind="closed",
                instrument_id=position.instrument_id,
                position_id=position_id,
                before=position,
                instrument_now_empty=position.instrument_id not in new_instruments,
            )
        )

    # Los activos que notificamos con más detalle primero: aperturas, luego
    # ampliaciones, reducciones y cierres.
    order = {"opened": 0, "increased": 1, "reduced": 2, "closed": 3}
    changes.sort(key=lambda c: (order.get(c.kind, 9), c.instrument_id, c.position_id))
    return changes


def _amount_changed(old_amount: float, new_amount: float) -> bool:
    if old_amount == new_amount:
        return False
    scale = max(abs(old_amount), abs(new_amount), 1e-9)
    return abs(new_amount - old_amount) > max(_AMOUNT_ATOL, _AMOUNT_RTOL * scale)


def summarize_by_instrument(changes: Iterable[Change]) -> dict[int, list[Change]]:
    """Agrupa los cambios por activo para redactar el mensaje."""
    grouped: dict[int, list[Change]] = {}
    for change in changes:
        grouped.setdefault(change.instrument_id, []).append(change)
    return grouped
