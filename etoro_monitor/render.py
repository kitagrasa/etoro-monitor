"""Redacción de los avisos (texto HTML para Telegram).

Los mensajes hablan solo de **operaciones** y del **peso** del activo. No
incluyen unidades, precios de entrada ni fechas: eso no interesa y además no
se guarda en ningún sitio.

Los cortos se anuncian siempre en negrita y con la palabra CORTO, para que no
se confundan con una compra normal.
"""

from __future__ import annotations

import logging
from datetime import datetime, tzinfo, timezone
from html import escape
from typing import Iterable, Mapping, Optional, Sequence
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from .models import KIND_ORDER, PESO_DECIMALES, Change, EtoroUser, Snapshot

log = logging.getLogger(__name__)

# Iconos: la FORMA dice si la operación es total o parcial y el COLOR si es
# compra o venta.
#
#   🟢 COMPRA            (total, compra)
#   🔴 VENTA             (total, venta)
#   💚 COMPRA PARCIAL    (parcial, compra)
#   ❤️ VENTA PARCIAL     (parcial, venta)
#
# Totales: círculo (🟢/🔴). Parciales: corazón (💚/❤️), pedido el 26/10/2026
# (antes eran ➕/➖). El corazón no dice "más" ni "menos": eso lo dice el verbo
# (COMPRA/VENTA). Lo que aporta es la forma, que es lo que separa una operación
# parcial de una total. El color sigue diciendo el lado: verde compra, rojo venta.
EMOJI_COMPRA = ("🟢", "💚")  # (total, parcial)
EMOJI_VENTA = ("🔴", "❤️")  # (total, parcial)

# Verbos por tipo de operación: (largo, corto, largo parcial, corto parcial).
# Los cortos llevan "CORTO" explícito y van en negrita igual que los largos.
#
# Los verbos son los mismos que verías en el histórico de eToro: se compra o se
# vende, y la operación es total (abre o cierra la posición) o parcial. Por eso
# NO hay verbos del tipo "amplía" o "reduce": el aviso dice directamente si ha
# comprado o vendido.
ACTIONS: dict[str, tuple[str, str, str, str]] = {
    "opened": ("COMPRA", "ABRE CORTO", "COMPRA PARCIAL", "COMPRA PARCIAL CORTO"),
    "increased": ("COMPRA", "ABRE CORTO", "COMPRA PARCIAL", "COMPRA PARCIAL CORTO"),
    "reduced": ("VENTA", "CIERRA CORTO", "VENTA PARCIAL", "VENTA PARCIAL CORTO"),
    "closed": ("VENTA", "CIERRA CORTO", "VENTA PARCIAL", "VENTA PARCIAL CORTO"),
}

# Qué tipos de operación son "compra" a efectos del color.
COMPRAS = ("opened", "increased")


def emoji_of(kind: str, *, partial: bool) -> str:
    """El icono: círculo si es total, corazón si es parcial; verde o rojo."""
    total, parcial = EMOJI_COMPRA if kind in COMPRAS else EMOJI_VENTA
    return parcial if partial else total


def action_of(
    kind: str, is_short: bool, *, partial: bool = False
) -> tuple[str, str]:
    """Devuelve (emoji, verbo) para un tipo de operación."""
    verbos = ACTIONS.get(kind)
    if verbos is None:
        return "❓", kind.upper()

    largo, corto, largo_parcial, corto_parcial = verbos
    if is_short:
        return emoji_of(kind, partial=partial), (
            corto_parcial if partial else corto
        )
    return emoji_of(kind, partial=partial), (largo_parcial if partial else largo)


# ---------------------------------------------------------------------- #
# Formato de números y fechas
# ---------------------------------------------------------------------- #
def format_number(value: float, decimals: int = 2) -> str:
    """1234.5678 -> '1.234,57' (formato español)."""
    text = f"{value:,.{decimals}f}"
    return text.replace(",", "\x00").replace(".", ",").replace("\x00", ".")


def format_weight(weight: Optional[float], decimals: int = PESO_DECIMALES) -> str:
    if weight is None:
        return "?"
    return f"{format_number(weight, decimals)}%"


def format_date(iso: str) -> str:
    """'2025-07-08T14:02:14Z' -> '08/07/2025'."""
    if not iso:
        return ""
    try:
        parsed = datetime.fromisoformat(iso.replace("Z", "+00:00"))
    except ValueError:
        return ""
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc).strftime("%d/%m/%Y")


# Los mensajes van en hora de Madrid, que es la de quien los lee (y la del
# horario del monitor). Se usan zonas de verdad, no un desfase fijo, para que el
# cambio de hora salga solo.
ZONA_MADRID = "Europe/Madrid"


def _zona(nombre: str = ZONA_MADRID) -> tzinfo:
    """La zona pedida; si el sistema no tiene datos de zonas, UTC.

    En Windows hace falta el paquete `tzdata` (está en requirements.txt). Si
    falta, el aviso sale en UTC en vez de romperse.
    """
    try:
        return ZoneInfo(nombre)
    except (ZoneInfoNotFoundError, ValueError, KeyError):
        log.warning("Sin datos de zona para %s: se usa UTC", nombre)
        return timezone.utc


def en_madrid(momento: Optional[datetime] = None) -> datetime:
    """El instante (por defecto, ahora) pasado a hora de Madrid."""
    base = momento or datetime.now(timezone.utc)
    if base.tzinfo is None:
        base = base.replace(tzinfo=timezone.utc)
    return base.astimezone(_zona())


def now_madrid() -> str:
    """'20/10/2026 20:06' (hora de Madrid)."""
    return en_madrid().strftime("%d/%m/%Y %H:%M")


# ---------------------------------------------------------------------- #
# Línea de peso
# ---------------------------------------------------------------------- #
def _weight_line(change: Change) -> str:
    """El peso del activo, como contexto: solo el porcentaje.

    Sin etiqueta: un activo **nuevo** enseña el peso que tiene; uno que
    **desaparece**, el que tenía; y en los parciales, siempre "antes → después",
    también cuando el cambio es tan pequeño que a dos decimales se lee igual
    (26/10/2026: el usuario no quiere un solo número que no explica nada).
    """
    antes, ahora = change.weight_before, change.weight_after

    if change.kind == "opened" and antes is None:
        return format_weight(ahora)
    if change.kind == "closed" and ahora is None:
        return format_weight(antes)
    if antes is None or ahora is None:
        return ""
    return _weight_pair(antes, ahora)


def _weight_pair(antes: float, ahora: float) -> str:
    """«16,67% → 17,80%», con los decimales que hagan falta para distinguirlos.

    Los cambios de milésimas (redondeo de eToro) no llegan aquí: el monitor los
    descarta antes con `diff.quitar_compras_invisibles`. Si aun así los dos
    pesos se leen igual a dos decimales —una bajada pequeña que el histórico sí
    respalda—, se enseñan tres o cuatro antes que repetir el mismo número dos
    veces: repetido parece un fallo y no dice cuánto ha cambiado.
    """
    for decimales in range(PESO_DECIMALES, PESO_DECIMALES + 3):
        izquierda = format_number(antes, decimales)
        derecha = format_number(ahora, decimales)
        if izquierda != derecha:
            return f"{izquierda}% → {derecha}%"
    return format_weight(ahora)


# ---------------------------------------------------------------------- #
# Bloque por activo
# ---------------------------------------------------------------------- #
def render_operation(
    changes: Sequence[Change],
    *,
    instrument_label: str,
) -> str:
    """Un bloque por activo, con todas las operaciones que ha tenido.

    Se agrupan a propósito: si de un activo se cierra una posición y se abre
    otra, dos bloques seguidos con el mismo nombre se leen fatal. Uno solo, con
    las dos operaciones, se entiende de un vistazo.
    """
    ordenadas = _sorted_changes(changes)
    principal = ordenadas[0]
    emoji, _ = action_of(
        principal.kind, principal.is_short, partial=principal.is_partial
    )

    # Cada verbo una sola vez: tres ventas parciales se leen "VENTA PARCIAL",
    # sin "VENTA PARCIAL + VENTA PARCIAL + VENTA PARCIAL" ni "×3".
    partes = [f"<b>{escape(verbo)}</b>" for verbo in _unique_actions(ordenadas)]
    titulo = f"{emoji} " + " + ".join(partes)
    titulo += f" · {escape(instrument_label)}"

    notas: list[str] = []
    if any(c.instrument_was_new for c in ordenadas):
        notas.append("nueva en cartera")
    if notas:
        titulo += f"  <i>({' / '.join(notas)})</i>"

    lineas = [titulo]
    peso = _weight_line(principal)
    if peso:
        lineas.append(f"   {peso}")
    return "\n".join(lineas)


def _sorted_changes(changes: Sequence[Change]) -> list[Change]:
    return sorted(
        changes,
        key=lambda c: (
            KIND_ORDER.get(c.kind, 9),
            c.instrument_id,
            c.direction,
            c.position_id or 0,
        ),
    )


def _unique_actions(changes: Sequence[Change]) -> list[str]:
    """Un verbo por cada tipo de operación, en orden y sin repetir.

    Tres ventas parciales del mismo activo dan ["VENTA PARCIAL"]: la palabra
    sale una vez, sin "+ VENTA PARCIAL" ni "×3".
    """
    vistos: list[str] = []
    for change in changes:
        verbo = action_of(change.kind, change.is_short, partial=change.is_partial)[1]
        if verbo not in vistos:
            vistos.append(verbo)
    return vistos


# ---------------------------------------------------------------------- #
# Mensajes completos
# ---------------------------------------------------------------------- #
def render_user_changes(
    user: EtoroUser,
    changes: Sequence[Change],
    *,
    instrument_labels: Mapping[int, str],
    timestamp: Optional[str] = None,
) -> str:
    """Un mensaje con las operaciones de la pasada.

    Cada activo llega ya reducido a su neto (`diff.netear`), así que aquí hay
    como mucho un cambio por activo y el mensaje no puede decir cosas del estilo
    "compra parcial y venta parcial" del mismo activo.
    """
    ordenadas = _sorted_changes(changes)

    bloques = [
        render_operation(
            grupo,
            instrument_label=instrument_labels.get(
                grupo[0].instrument_id, f"Instrumento {grupo[0].instrument_id}"
            ),
        )
        for grupo in _grouped_by_asset(ordenadas)
    ]

    # Cabecera corta: quién y cuándo. Sin "ha movido su cartera" ni el recuento
    # de operaciones: el nombre y la fecha ya lo dicen todo (20/10/2026).
    cabecera = [
        f"🔔 <b>{escape(user.display_name)}</b>",
        f"<i>{timestamp or now_madrid()}</i>",
        "",
    ]

    pie = ["", f'🔗 <a href="{escape(user.portfolio_url)}">Ver cartera pública</a>']
    return "\n".join(cabecera + bloques + pie)


def _grouped_by_asset(changes: Sequence[Change]) -> list[list[Change]]:
    """Agrupa las operaciones del mismo activo, conservando el orden."""
    grupos: dict[str, list[Change]] = {}
    for change in changes:
        grupos.setdefault(change.asset.key, []).append(change)
    return list(grupos.values())



def render_baseline(
    user: EtoroUser,
    snapshot: Snapshot,
    *,
    instrument_labels: Mapping[int, str],
    timestamp: Optional[str] = None,
) -> str:
    """Primer mensaje: sirve para confirmar que el bot está funcionando."""
    pesos = sorted(
        ((a.instrument_id, a.invested_pct) for a in snapshot.assets.values()),
        key=lambda kv: kv[1],
        reverse=True,
    )[:8]

    lineas = [
        f"✅ <b>Monitor activo para {escape(user.display_name)}</b>",
        f"<i>{timestamp or now_madrid()}</i>",
        "",
        f"Activos en cartera: <b>{len(snapshot.instrument_ids)}</b>",
        "",
        "<b>Mayores pesos</b>",
    ]
    for instrument_id, peso in pesos:
        etiqueta = escape(instrument_labels.get(instrument_id, str(instrument_id)))
        lineas.append(f"  · {etiqueta}: {format_weight(peso)}")
    lineas += [
        "",
        "<i>A partir de ahora recibirás un aviso cada vez que compre</i>",
        "<i>o venda algo de este activo, total o parcialmente.</i>",
        f'🔗 <a href="{escape(user.portfolio_url)}">Ver cartera pública</a>',
    ]
    return "\n".join(lineas)


def render_problem(message: str) -> str:
    return (
        "⚠️ <b>Aviso del monitor</b>\n"
        f"<i>{now_madrid()}</i>\n\n"
        f"{escape(message)}"
    )


def render_portfolio_table(
    user: EtoroUser,
    snapshot: Snapshot,
    *,
    instrument_labels: Mapping[int, str],
) -> str:
    """Volcado legible de la cartera (comando `show`)."""
    lineas = [
        f"\n{user.display_name} ({user.username}) · CID {user.real_cid}",
        f"{len(snapshot.instrument_ids)} activos\n",
        f"{'activo':<36}{'lado':>7}{'peso':>10}",
        "-" * 53,
    ]
    for asset in sorted(
        snapshot.assets.values(), key=lambda a: a.invested_pct, reverse=True
    ):
        etiqueta = instrument_labels.get(
            asset.instrument_id, f"Instrumento {asset.instrument_id}"
        )[:35]
        lado = "CORTO" if asset.is_short else "largo"
        lineas.append(
            f"{etiqueta:<36}{lado:>7}{format_weight(asset.invested_pct):>10}"
        )
    return "\n".join(lineas) + "\n"


def summarize_changes(changes: Iterable[Change]) -> str:
    """Línea corta para los logs."""
    return ", ".join(
        f"{c.kind}:{c.instrument_id}{'/corto' if c.is_short else ''}" for c in changes
    )
