"""Redacción de los avisos (texto HTML para Telegram).

Los mensajes hablan solo de **operaciones** y del **peso** del activo. No
incluyen unidades, precios de entrada ni fechas: eso no interesa y además no
se guarda en ningún sitio.

Los cortos se anuncian siempre en negrita y con la palabra CORTO, para que no
se confundan con una compra normal.
"""

from __future__ import annotations

from datetime import datetime, timezone
from html import escape
from typing import Iterable, Mapping, Optional, Sequence

from .models import KIND_ORDER, Change, EtoroUser, Snapshot

# (emoji, verbo en largo, verbo en corto, verbo en largo parcial, verbo en
# corto parcial). Los verbos cortos van en negrita igual que los largos, y
# llevan "CORTO" explícito.
#
# Los verbos son los mismos que verías en el histórico de eToro: se compra o se
# vende, y la operación es total (abre o cierra la posición) o parcial. Por eso
# NO hay verbos del tipo "amplía" o "reduce": el aviso dice directamente si ha
# comprado o vendido.
ACTIONS: dict[str, tuple[str, str, str, str, str]] = {
    "opened": ("🟢", "COMPRA", "ABRE CORTO", "COMPRA PARCIAL", "COMPRA PARCIAL CORTO"),
    "increased": ("🟢", "COMPRA", "ABRE CORTO", "COMPRA PARCIAL", "COMPRA PARCIAL CORTO"),
    "reduced": ("🔻", "VENTA", "CIERRA CORTO", "VENTA PARCIAL", "VENTA PARCIAL CORTO"),
    "closed": ("🔴", "VENTA", "CIERRA CORTO", "VENTA PARCIAL", "VENTA PARCIAL CORTO"),
}


def action_of(
    kind: str, is_short: bool, *, partial: bool = False
) -> tuple[str, str]:
    """Devuelve (emoji, verbo) para un tipo de operación."""
    emoji, largo, corto, largo_parcial, corto_parcial = ACTIONS.get(
        kind, ("❓", kind.upper(), kind.upper(), kind.upper(), kind.upper())
    )
    if is_short:
        return emoji, (corto_parcial if partial else corto)
    return emoji, (largo_parcial if partial else largo)


# ---------------------------------------------------------------------- #
# Formato de números y fechas
# ---------------------------------------------------------------------- #
def format_number(value: float, decimals: int = 2) -> str:
    """1234.5678 -> '1.234,57' (formato español)."""
    text = f"{value:,.{decimals}f}"
    return text.replace(",", "\x00").replace(".", ",").replace("\x00", ".")


def format_weight(weight: Optional[float]) -> str:
    if weight is None:
        return "?"
    return f"{format_number(weight, 2)}%"


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


def now_utc() -> str:
    return datetime.now(timezone.utc).strftime("%d/%m/%Y %H:%M UTC")


def _plural(count: int, singular: str, plural: str) -> str:
    return f"{count} {singular if count == 1 else plural}"


# ---------------------------------------------------------------------- #
# Línea de peso
# ---------------------------------------------------------------------- #
def _weight_line(change: Change) -> str:
    """El peso del activo, como contexto de la operación."""
    antes, ahora = change.weight_before, change.weight_after

    if change.kind == "opened":
        return f"peso en cartera: {format_weight(ahora)}"
    if change.kind == "closed":
        return f"peso antes: {format_weight(antes)}"
    if antes is None or ahora is None:
        return ""
    # Si el peso no se ha movido (redondeado a céntimos) es más claro decirlo
    # una sola vez que poner "16,06% → 16,06%".
    if round(antes, 2) == round(ahora, 2):
        return f"peso en cartera: {format_weight(ahora)}"
    return f"peso en cartera: {format_weight(antes)} → {format_weight(ahora)}"


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
    emoji, verbo = action_of(
        principal.kind, principal.is_short, partial=principal.is_partial
    )
    extra = [_short_action(c) for c in ordenadas[1:]]

    titulo = f"{emoji} <b>{verbo}</b>"
    if extra:
        titulo += " + " + " + ".join(f"<b>{escape(e)}</b>" for e in extra)
    titulo += f" · {escape(instrument_label)}"

    notas: list[str] = []
    if any(c.instrument_was_new for c in ordenadas):
        notas.append("nueva en cartera")
    if any(c.instrument_now_empty for c in ordenadas):
        notas.append("cierra todo el activo")
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


def _short_action(change: Change) -> str:
    """Solo el verbo, sin emoji, para acompañar a la operación principal."""
    return action_of(change.kind, change.is_short, partial=change.is_partial)[1]



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

    activos = len({c.instrument_id for c in changes})
    cabecera = [
        f"🔔 <b>{escape(user.display_name)}</b> ha movido su cartera",
        f"<i>{timestamp or now_utc()} · "
        f"{_plural(len(ordenadas), 'operación', 'operaciones')} en "
        f"{_plural(activos, 'activo', 'activos')}</i>",
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
        f"<i>{timestamp or now_utc()}</i>",
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
        f"<i>{now_utc()}</i>\n\n"
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
