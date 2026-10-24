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

Aun así, el aviso que se manda es el **neto del activo** (ver `netear`): este
monitor es para copiar, así que comprar 5 y vender 5 entre dos pasadas no es
nada, y comprar 5 y vender 4 es una compra parcial de 1.

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
    """Los cambios del mismo activo y lado entre las dos fotos.

    Si las dos fotos traen las posiciones (cortos y estados antiguos) se compara
    posición a posición. Si no (modo cartera: el activo solo guarda su total), se
    compara el total, que es lo único que se avisa.
    """
    if not previous.por_posiciones or not current.por_posiciones:
        return _cambio_por_total(previous, current)

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


def _cambio_por_total(previous: Asset, current: Asset) -> list[Change]:
    """Compara el total del activo: un solo cambio, sin posición.

    Comprar 5 y vender 5 entre dos pasadas deja el mismo total y no produce
    cambio; comprar 5 y vender 4 deja uno más y produce una compra parcial.
    """
    delta = round(current.units, UNITS_DECIMALS) - round(previous.units, UNITS_DECIMALS)
    if delta == 0:
        return []
    return [
        Change(
            kind="increased" if delta > 0 else "reduced",
            instrument_id=current.instrument_id,
            direction=current.direction,
            before=previous,
            after=current,
        )
    ]


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


def netear(changes: list[Change]) -> list[Change]:
    """Reduce los cambios de cada activo a UNA operación: el neto.

    Este monitor es para **copiar**: lo que importa es cuántas unidades ha
    ganado o perdido el activo entre las dos fotos, no por qué posiciones han
    pasado. Comprar 5 y vender 5 entre dos pasadas es "nada" (la foto no cambia
    y no hay aviso); comprar 5 y vender 4 es "una compra parcial de 1".

    Por eso, después de comparar posición a posición, se mira el total del
    activo: si sube, COMPRA PARCIAL; si baja, VENTA PARCIAL; si aparece, COMPRA;
    si desaparece, VENTA. Un activo que termina igual que empezó no se avisa,
    aunque por el camino haya habido compras y ventas.

    Así el mensaje no depende de cómo reparta eToro las operaciones entre
    posiciones (una compra nueva y una venta de otra posición antigua del mismo
    activo salen como un solo movimiento neto, que es lo que hay que copiar).
    """
    grupos = summarize_by_instrument(changes)

    netos: list[Change] = []
    for grupo in grupos.values():
        antes = next((c.before for c in grupo if c.before is not None), None)
        despues = next((c.after for c in grupo if c.after is not None), None)
        primero = grupo[0]
        comun = {
            "instrument_id": primero.instrument_id,
            "direction": primero.direction,
            "instrument_was_new": any(c.instrument_was_new for c in grupo),
            "instrument_now_empty": any(c.instrument_now_empty for c in grupo),
        }

        if antes is None:
            # Activo nuevo en la cartera.
            netos.append(Change(kind="opened", before=None, after=despues, **comun))
            continue
        if despues is None:
            # Activo que ya no está.
            netos.append(Change(kind="closed", before=antes, after=None, **comun))
            continue

        delta = round(despues.units, UNITS_DECIMALS) - round(antes.units, UNITS_DECIMALS)
        if delta > 0:
            netos.append(Change(kind="increased", before=antes, after=despues, **comun))
        elif delta < 0:
            netos.append(Change(kind="reduced", before=antes, after=despues, **comun))
        # delta == 0: compró y vendió lo mismo. No hay nada que copiar.

    return netos


# ---------------------------------------------------------------------- #
# Ajustes proporcionales: separarlos de las operaciones reales
# ---------------------------------------------------------------------- #
# Cuántas posiciones tienen que cambiar para poder hablar de "ajuste". Es un
# máximo: nunca se pueden exigir más cambios de los que tiene la cartera (ver
# `split_adjustment`).
ADJUSTMENT_MIN_POSITIONS = 4
# Qué fracción de las cambiadas tiene que compartir el mismo factor (0,8 = 80%).
ADJUSTMENT_SHARE = 0.8
# Tolerancia al comparar factores (0,001 = una décima de punto porcentual).
ADJUSTMENT_TOLERANCE = 0.001
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

    No hay un tamaño mínimo: un recorte del 0,05% en toda la cartera es tan
    ajuste como uno del 0,30%. Lo que decide es que sea proporcional y que
    coja a la cartera entera, no cuánto baja.

    Y el número de cambios exigido es un **máximo**: en una cartera de 3
    posiciones solo se pueden cambiar 3, así que se piden 3 (no 4).

    Devuelve `(operaciones, ajuste)`:

    * si no hay ajuste, `(changes, None)`;
    * si lo hay, las operaciones que NO forman parte del ajuste y un
      `Adjustment` con el factor y cuántas posiciones entraron.
    """
    candidatos: list[tuple[Change, float]] = []
    for change in changes:
        if change.kind not in ("increased", "reduced"):
            continue
        if change.before is None or change.after is None:
            continue
        if change.position_id is not None:
            # Cambio de una posición concreta (cortos y estados antiguos).
            antes = change.before.positions.get(change.position_id)
            ahora = change.after.positions.get(change.position_id)
            if antes is None or ahora is None or antes.units <= 0:
                continue
            ratio = ahora.units / antes.units
        else:
            # Cambio del total del activo (modo cartera).
            if change.before.units <= 0:
                continue
            ratio = change.after.units / change.before.units
        candidatos.append((change, ratio))

    # El mínimo es un máximo: no se pueden exigir más cambios de los que hay.
    # En una cartera de 3 posiciones, un ingreso que diluya todo produce 3
    # cambios y con el mínimo fijo de 4 no se detectaría: saldrían 3 "ventas
    # parciales" falsas (24/10/2026). En una cartera de 25, sigue habiendo que
    # cambiar 4, igual que antes.
    #
    # Pero nunca se baja de 2: con un solo cambio no se puede hablar de ajuste,
    # porque es indistinguible de una venta normal.
    minimo = ADJUSTMENT_MIN_POSITIONS
    if total_positions > 0:
        minimo = min(minimo, total_positions)
    minimo = max(2, minimo)
    if len(candidatos) < minimo:
        return changes, None

    factor = statistics.median(ratio for _, ratio in candidatos)
    # Solo tiene sentido para bajadas: una subida proporcional es una compra
    # (o algo raro), y no queremos silenciarla.
    #
    # No hay un mínimo de bajada: da igual que el factor sea 0,997 (lo que se
    # vio el 19/10) o 0,9995 (el 20/10, un recorte del 0,05% en las 36
    # posiciones de una cartera). Antes había un mínimo del 0,05% y estos
    # recortes pequeños se colaban como una venta parcial por posición.
    if factor >= 1.0:
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


