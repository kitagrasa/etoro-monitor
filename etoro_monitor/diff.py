"""Comparación de dos fotos de cartera -> lista de operaciones.

Solo se compara **unidades por activo**. Como las unidades únicamente cambian
cuando el usuario opera (el precio no las toca), no hay falsos positivos por
movimientos del mercado:

  * activo que aparece              -> COMPRA (abre, o entra en corto)
  * activo que desaparece           -> VENTA (cierra todo)
  * más unidades                    -> AMPLÍA
  * menos unidades                  -> REDUCE

La clave incluye la dirección, así que un mismo activo puede detectarse por
separado en largo y en corto.
"""

from __future__ import annotations

from .models import UNITS_DECIMALS, Change, Snapshot


def diff_snapshots(before: Snapshot, after: Snapshot) -> list[Change]:
    """Devuelve las operaciones detectadas entre dos instantáneas."""
    old = before.assets
    new = after.assets

    old_instruments = {a.instrument_id for a in old.values()}
    new_instruments = {a.instrument_id for a in new.values()}

    changes: list[Change] = []

    for key in sorted(set(new) - set(old)):
        asset = new[key]
        changes.append(
            Change(
                kind="opened",
                instrument_id=asset.instrument_id,
                direction=asset.direction,
                after=asset,
                instrument_was_new=asset.instrument_id not in old_instruments,
            )
        )

    for key in sorted(set(old) & set(new)):
        previous, current = old[key], new[key]
        if _units_changed(previous.units, current.units):
            changes.append(
                Change(
                    kind="increased" if current.units > previous.units else "reduced",
                    instrument_id=current.instrument_id,
                    direction=current.direction,
                    before=previous,
                    after=current,
                )
            )

    for key in sorted(set(old) - set(new)):
        asset = old[key]
        changes.append(
            Change(
                kind="closed",
                instrument_id=asset.instrument_id,
                direction=asset.direction,
                before=asset,
                instrument_now_empty=asset.instrument_id not in new_instruments,
            )
        )

    order = {"opened": 0, "increased": 1, "reduced": 2, "closed": 3}
    changes.sort(key=lambda c: (order.get(c.kind, 9), c.instrument_id, c.direction))
    return changes


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
