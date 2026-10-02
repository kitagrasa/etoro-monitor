"""Redacción de los avisos (texto HTML para Telegram)."""

from __future__ import annotations

from datetime import datetime, timezone
from html import escape
from typing import Iterable, Mapping, Sequence

from .models import Change, EtoroUser, Position, Snapshot

# emoji + verbo por tipo de cambio, separando largos y cortos
_ACTIONS = {
    "opened": (("🟢", "COMPRA"), ("🟢", "ABRE CORTO")),
    "increased": (("🟢", "AMPLÍA"), ("🟢", "AMPLÍA CORTO")),
    "reduced": (("🔻", "REDUCE"), ("🔻", "REDUCE CORTO")),
    "closed": (("🔴", "VENTA"), ("🔴", "CIERRA CORTO")),
}
_ORDER = ("opened", "increased", "reduced", "closed")


def action_of(kind: str, is_buy: bool) -> tuple[str, str]:
    long_form, short_form = _ACTIONS.get(kind, (("❓", kind.upper()),) * 2)
    return long_form if is_buy else short_form


# ---------------------------------------------------------------------- #
# Formato de números y fechas
# ---------------------------------------------------------------------- #
def format_number(value: float, decimals: int = 2) -> str:
    """1234.5678 -> '1.234,57' (formato español)."""
    text = f"{value:,.{decimals}f}"
    return text.replace(",", "\x00").replace(".", ",").replace("\x00", ".")


def format_weight(weight: float | None) -> str:
    if weight is None:
        return "?"
    return f"{format_number(weight, 2)}%"


def format_datetime(iso: str, with_time: bool = True) -> str:
    """'2025-07-08T14:02:14.517Z' -> '08/07/2025 14:02 UTC'."""
    if not iso:
        return ""
    try:
        parsed = datetime.fromisoformat(iso.replace("Z", "+00:00"))
    except ValueError:
        return iso
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    parsed = parsed.astimezone(timezone.utc)
    return parsed.strftime("%d/%m/%Y %H:%M UTC" if with_time else "%d/%m/%Y")


def format_date(iso: str) -> str:
    return format_datetime(iso, with_time=False)


def now_utc() -> str:
    return datetime.now(timezone.utc).strftime("%d/%m/%Y %H:%M UTC")


# ---------------------------------------------------------------------- #
# Bloques
# ---------------------------------------------------------------------- #
def _plural(count: int, singular: str, plural: str) -> str:
    return f"{count} {singular if count == 1 else plural}"


def _prices(positions: Sequence[Position], label: str = "entrada ", max_inline: int = 3) -> str:
    """Precios de apertura de las posiciones (deduplicados)."""
    rates = sorted({p.open_rate for p in positions})
    if not rates:
        return ""
    if len(rates) == 1:
        return f" ({label}@ {format_number(rates[0], 4)} USD)"
    if len(rates) <= max_inline:
        joined = " y ".join(format_number(r, 4) for r in rates)
        return f" ({label}@ {joined} USD)"
    return (
        f" ({label}@ {format_number(rates[0], 2)}–{format_number(rates[-1], 2)} USD)"
    )


def _dates(positions: Sequence[Position]) -> str:
    dates = sorted(p.open_datetime for p in positions if p.open_datetime)
    if not dates:
        return ""
    first, last = format_date(dates[0]), format_date(dates[-1])
    return f" · {first}" if first == last else f" · entre {first} y {last}"


def _group_detail(kind: str, group: Sequence[Change]) -> str:
    positions = [c.after or c.before for c in group]
    positions = [p for p in positions if p is not None]
    if not positions:
        return ""

    if kind in ("opened", "increased"):
        units = sum(p.amount for p in positions)
        text = f"+{_plural(len(positions), 'posición nueva', 'posiciones nuevas')} · "
        text += f"{format_number(units, 6)} uds{_prices(positions)}{_dates(positions)}"
        return text

    if kind == "reduced":
        deltas = [
            (c.before.amount - c.after.amount)
            for c in group
            if c.before is not None and c.after is not None
        ]
        text = f"-{_plural(len(positions), 'posición', 'posiciones')} · "
        text += f"−{format_number(sum(deltas), 6)} uds"
        return text

    # closed: el precio que conocemos es el de ENTRADA, no el de la venta
    units = sum(p.amount for p in positions)
    text = f"-{_plural(len(positions), 'posición cerrada', 'posiciones cerradas')} · "
    text += f"{format_number(units, 6)} uds{_prices(positions)}{_dates(positions)}"
    return text


def render_instrument_block(
    changes: Sequence[Change],
    *,
    instrument_label: str,
    weight: float | None = None,
) -> str:
    if not changes:
        return ""

    head = changes[0]
    head_position = head.after or head.before
    emoji, verb = action_of(head.kind, head_position.is_buy if head_position else True)

    notes: list[str] = []
    if head.instrument_was_new and head.kind in ("opened", "increased"):
        notes.append("nueva en cartera")
    if any(c.instrument_now_empty for c in changes):
        notes.append("cierra todo el activo")

    title = f"{emoji} <b>{verb}</b> · {escape(instrument_label)}"
    if notes:
        title += f"  <i>({' / '.join(notes)})</i>"

    lines = [title]
    for kind in _ORDER:
        group = [c for c in changes if c.kind == kind]
        if not group:
            continue
        detail = _group_detail(kind, group)
        if not detail:
            continue
        if kind == head.kind:
            lines.append(f"   {detail}")
        else:
            group_position = group[0].after or group[0].before
            group_emoji, group_verb = action_of(
                kind, group_position.is_buy if group_position else True
            )
            lines.append(f"   {group_emoji} <b>{group_verb}</b> {detail}")

    if weight is not None and any(
        c.kind in ("opened", "increased", "reduced") for c in changes
    ):
        lines.append(f"   peso en cartera ahora: {format_weight(weight)}")
    return "\n".join(lines)


# ---------------------------------------------------------------------- #
# Mensajes completos
# ---------------------------------------------------------------------- #
def render_user_changes(
    user: EtoroUser,
    changes: Sequence[Change],
    *,
    instrument_labels: Mapping[int, str],
    weights: Mapping[int, float] | None = None,
    timestamp: str | None = None,
) -> str:
    weights = weights or {}

    grouped: dict[int, list[Change]] = {}
    for change in changes:
        grouped.setdefault(change.instrument_id, []).append(change)

    # El activo con el cambio más "importante" primero.
    def sort_key(item: tuple[int, list[Change]]):
        return (_ORDER.index(item[1][0].kind), -len(item[1]), item[0])

    blocks = [
        render_instrument_block(
            instrument_changes,
            instrument_label=instrument_labels.get(
                instrument_id, f"Instrumento {instrument_id}"
            ),
            weight=weights.get(instrument_id),
        )
        for instrument_id, instrument_changes in sorted(grouped.items(), key=sort_key)
    ]

    bought = sum(1 for c in changes if c.kind in ("opened", "increased"))
    sold = sum(1 for c in changes if c.kind in ("reduced", "closed"))
    resumen = ", ".join(
        part
        for part in (
            f"{bought} de compra" if bought else "",
            f"{sold} de venta" if sold else "",
        )
        if part
    )

    header = [
        f"🔔 <b>{escape(user.display_name)}</b> ha movido su cartera",
        f"<i>{timestamp or now_utc()} · "
        f"{_plural(len(changes), 'cambio', 'cambios')} en "
        f"{_plural(len(grouped), 'activo', 'activos')} ({resumen})</i>",
        "",
    ]
    footer = ["", f'🔗 <a href="{escape(user.portfolio_url)}">Ver cartera pública</a>']
    return "\n".join(header + blocks + footer)


def render_baseline(
    user: EtoroUser,
    snapshot: Snapshot,
    *,
    instrument_labels: Mapping[int, str],
    timestamp: str | None = None,
) -> str:
    """Primer mensaje: sirve para confirmar que el bot está funcionando."""
    top = sorted(snapshot.weights.items(), key=lambda kv: kv[1], reverse=True)[:8]
    lines = [
        f"✅ <b>Monitor activo para {escape(user.display_name)}</b>",
        f"<i>{timestamp or now_utc()}</i>",
        "",
        f"Activos en cartera: <b>{len(snapshot.instrument_ids)}</b>",
        f"Posiciones abiertas: <b>{len(snapshot.positions)}</b>",
        "",
        "<b>Mayores pesos</b>",
    ]
    for instrument_id, weight in top:
        lines.append(
            f"  · {escape(instrument_labels.get(instrument_id, str(instrument_id)))}: "
            f"{format_weight(weight)}"
        )
    lines += [
        "",
        "<i>A partir de ahora recibirás un aviso cada vez que compre,</i>",
        "<i>venda, amplíe o reduzca alguna posición.</i>",
        f'🔗 <a href="{escape(user.portfolio_url)}">Ver cartera pública</a>',
    ]
    return "\n".join(lines)


def render_problem(user: EtoroUser | None, message: str) -> str:
    who = escape(user.display_name) if user else "el monitor"
    return (
        f"⚠️ <b>Aviso del monitor ({who})</b>\n"
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
    lines = [
        f"\n{user.display_name} ({user.username}) · CID {user.real_cid}",
        f"{len(snapshot.instrument_ids)} activos · "
        f"{len(snapshot.positions)} posiciones abiertas\n",
        f"{'activo':<34}{'peso':>9}{'pos.':>6}{'unidades':>16}{'1ª posición':>14}",
        "-" * 82,
    ]
    for instrument_id, weight in sorted(
        snapshot.weights.items(), key=lambda kv: kv[1], reverse=True
    ):
        positions = snapshot.positions_of(instrument_id)
        units = sum(p.amount for p in positions)
        first = min((p.open_datetime for p in positions if p.open_datetime), default="")
        label = instrument_labels.get(instrument_id, str(instrument_id))[:33]
        lines.append(
            f"{label:<34}{format_weight(weight):>9}{len(positions):>6}"
            f"{format_number(units, 6):>16}{format_date(first):>14}"
        )
    return "\n".join(lines) + "\n"


def summarize_changes(changes: Iterable[Change]) -> str:
    """Línea corta para los logs de GitHub Actions."""
    return ", ".join(f"{c.kind}:{c.instrument_id}#{c.position_id}" for c in changes)
