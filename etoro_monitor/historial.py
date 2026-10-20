"""El histórico de cierres como árbitro de las ventas.

El problema que resuelve: el monitor **deduce** las ventas comparando unidades,
y eToro también toca las unidades por su cuenta (recorta todas las posiciones un
0,05% o un 0,30% para cubrir comisiones, retiradas o lo que sea). Esos recortes
no son operaciones, pero desde las posiciones son indistinguibles de una venta
pequeña.

El histórico público de cierres sí los distingue: un recorte proporcional no
deja ningún apunte, y una venta de verdad sí (el 20/10/2026, con 36 posiciones
recortadas, el histórico tenía un solo cierre: el de Gulf Keystone).

Cómo se usa aquí: **no se toca lo que el detector de ajustes ya explica**. Solo
se llama a este módulo con las bajadas que han quedado sueltas, y para cada una
se pregunta si su activo tiene un cierre en el histórico que la respalde.

Regla de oro: **una bajada grande nunca se calla**, aunque el histórico no la
respalde. Si eToro no registrara los cierres parciales, prefiero un aviso de más
que uno de menos. Solo se descartan las bajadas pequeñas que el histórico
desmiente, que es exactamente el ruido que queremos quitar.
"""

from __future__ import annotations

from collections import Counter
from typing import Mapping, Optional

from .models import Change

# Qué fracción de una posición tiene que bajar como mínimo para que la bajada
# cuente como "grande" y no se descarte aunque el histórico no la respalde.
# Los recortes de eToro que hemos visto son del 0,05% y del 0,30%; el 1% deja
# margen de sobra y sigue cogiendo las ventas parciales que alguien hace de
# verdad (esas suelen ser mucho más grandes).
UMBRAL_BAJADA_PEQUENA = 0.01


def bajada_relativa(change: Change) -> Optional[float]:
    """Qué fracción del activo se ha vendido (0,05 = un 5%).

    Se mide sobre las unidades totales del activo (`netear` deja un solo cambio
    por activo, sin posición). Si el cambio viniera de una posición concreta, se
    mide sobre esa posición. `None` si no se puede calcular.
    """
    if change.before is None or change.after is None:
        return None

    if change.position_id is not None:
        antes = change.before.positions.get(change.position_id)
        ahora = change.after.positions.get(change.position_id)
        if antes is not None and ahora is not None and antes.units > 0:
            return (antes.units - ahora.units) / antes.units

    if change.before.units <= 0:
        return None
    return (change.before.units - change.after.units) / change.before.units


def es_pequena(change: Change, *, umbral: float = UMBRAL_BAJADA_PEQUENA) -> bool:
    """¿Es una bajada que el histórico podría desmentir?"""
    if change.kind != "reduced":
        return False
    relativa = bajada_relativa(change)
    return relativa is not None and relativa < umbral


def arbitrar(
    changes: list[Change],
    cierres: Mapping[int, int],
    *,
    umbral: float = UMBRAL_BAJADA_PEQUENA,
) -> list[Change]:
    """Quita las bajadas pequeñas que el histórico desmiente.

    `cierres` es `{instrument_id: número de cierres}` desde la pasada anterior.

    No se descarta nada en estos casos:

    * si el activo tiene **más cierres en el histórico que posiciones han
      desaparecido**: ha habido cierres parciales de verdad, así que las
      bajadas de ese activo son ventas;
    * si la bajada es **grande** (>= `umbral` de la posición): no nos fiamos del
      histórico, se avisa;
    * si no se puede calcular el tamaño de la bajada.

    Y, por supuesto, los cambios que no son bajadas (compras, posiciones que
    aparecen o desaparecen) no se tocan nunca: el histórico solo habla de
    cierres, no de aperturas.
    """
    cerradas = Counter(c.instrument_id for c in changes if c.kind == "closed")

    resultado: list[Change] = []
    for change in changes:
        if change.kind != "reduced":
            resultado.append(change)
            continue

        instrument_id = change.instrument_id
        if cierres.get(instrument_id, 0) > cerradas.get(instrument_id, 0):
            # Cierres que no explican las posiciones desaparecidas: parciales.
            resultado.append(change)
            continue

        if not es_pequena(change, umbral=umbral):
            resultado.append(change)
            continue

        # Bajada pequeña y sin ningún cierre que la respalde: recorte de eToro.

    return resultado
