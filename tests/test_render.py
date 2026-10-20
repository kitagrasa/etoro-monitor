"""Tests de los mensajes: compra/venta total o parcial, sin unidades ni precios."""

from __future__ import annotations

import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from etoro_monitor.diff import diff_snapshots  # noqa: E402
from etoro_monitor.models import Asset, EtoroUser, Position, Snapshot  # noqa: E402
from etoro_monitor.render import (  # noqa: E402
    en_madrid,
    format_number,
    format_weight,
    render_operation,
    render_user_changes,
)
from etoro_monitor.telegram import split_message  # noqa: E402

USER = EtoroUser(
    username="UsuarioEjemplo",
    gcid=1111111,
    real_cid=2222222,
    first_name="Usuario",
    last_name="Ejemplo",
    allow_display_full_name=True,
)

LABELS = {1002: "Alphabet (GOOG)", 6: "EUR/USD"}


def asset(instrument_id, units, *, direction="Buy", pct=1.0, position_id=None, extra=None):
    principal = position_id if position_id is not None else instrument_id
    positions = {principal: Position(principal, units)}
    for otro_id, otras_unidades in (extra or {}).items():
        positions[otro_id] = Position(otro_id, otras_unidades)
    return Asset(
        instrument_id=instrument_id,
        direction=direction,
        positions=positions,
        invested_pct=pct,
    )


def snap(*assets):
    return Snapshot(user=USER, assets={a.key: a for a in assets})


def solo(changes):
    """El bloque del activo, tal como lo vería el usuario."""
    return render_operation(
        changes, instrument_label=LABELS[changes[0].instrument_id]
    )


# ---------------------------------------------------------------------- #
# Formato
# ---------------------------------------------------------------------- #
def test_formato_espanol():
    assert format_number(1234.5678) == "1.234,57"
    assert format_weight(17.5672) == "17,57%"
    assert format_weight(None) == "?"


# ---------------------------------------------------------------------- #
# Operaciones en largo
# ---------------------------------------------------------------------- #
def test_compra():
    changes = diff_snapshots(snap(), snap(asset(1002, 0.6, pct=18.29)))
    texto = solo(changes)
    assert "<b>COMPRA</b>" in texto
    assert "Alphabet (GOOG)" in texto
    assert "nueva en cartera" in texto
    assert "18,29%" in texto


def test_compra_parcial_al_anadir_a_una_posicion():
    changes = diff_snapshots(
        snap(asset(1002, 1.0, pct=17.90)), snap(asset(1002, 1.6, pct=18.29))
    )
    texto = solo(changes)
    assert "<b>COMPRA PARCIAL</b>" in texto
    assert "AMPLÍA" not in texto
    assert "17,90% → 18,29%" in texto


def test_compra_parcial_al_abrir_una_segunda_posicion():
    """El caso que no se veía mirando solo las unidades totales.

    Ya tenía Alphabet: la operación nueva se ve como compra (no "nueva en
    cartera") y el peso pasa del 10% al 16%.
    """
    before = snap(asset(1002, 1.0, pct=10.0))
    after = snap(asset(1002, 1.0, pct=16.0, extra={9999: 0.6}))
    texto = solo(diff_snapshots(before, after))
    assert "<b>COMPRA PARCIAL</b>" in texto
    assert "nueva en cartera" not in texto
    assert "10,00% → 16,00%" in texto


def test_venta_parcial():
    changes = diff_snapshots(
        snap(asset(1002, 1.0, pct=20.0)), snap(asset(1002, 0.4, pct=9.0))
    )
    texto = solo(changes)
    assert "<b>VENTA PARCIAL</b>" in texto
    assert "REDUCE" not in texto
    assert "20,00% → 9,00%" in texto


def test_venta_total():
    changes = diff_snapshots(snap(asset(1002, 1.0, pct=1.61)), snap())
    texto = solo(changes)
    assert "<b>VENTA</b>" in texto
    assert "VENTA PARCIAL" not in texto
    assert "cierra todo el activo" not in texto
    assert "1,61%" in texto


# ---------------------------------------------------------------------- #
# Cortos: en negrita y con la palabra CORTO
# ---------------------------------------------------------------------- #
def test_abrir_corto_en_negrita():
    changes = diff_snapshots(snap(), snap(asset(6, 0.35, direction="Sell")))
    texto = solo(changes)
    assert "<b>ABRE CORTO</b>" in texto
    assert "CORTO" in texto


def test_ampliar_corto_en_negrita():
    changes = diff_snapshots(
        snap(asset(6, 0.3, direction="Sell")), snap(asset(6, 0.9, direction="Sell"))
    )
    assert "<b>COMPRA PARCIAL CORTO</b>" in solo(changes)


def test_reducir_corto_en_negrita():
    changes = diff_snapshots(
        snap(asset(6, 0.9, direction="Sell")), snap(asset(6, 0.3, direction="Sell"))
    )
    assert "<b>VENTA PARCIAL CORTO</b>" in solo(changes)


def test_cerrar_corto_en_negrita():
    changes = diff_snapshots(snap(asset(6, 0.5, direction="Sell")), snap())
    assert "<b>CIERRA CORTO</b>" in solo(changes)


# ---------------------------------------------------------------------- #
# Dos operaciones del mismo activo en una sola pasada
# ---------------------------------------------------------------------- #
def test_dos_operaciones_del_mismo_activo_van_en_un_solo_bloque():
    """Vender y comprar el mismo activo a la vez: un bloque, no dos."""
    before = snap(asset(1002, 1.0, position_id=1002))
    after = snap(asset(1002, 0.8, position_id=9999, extra={1002: 0.5}))
    texto = solo(diff_snapshots(before, after))
    assert texto.count("Alphabet (GOOG)") == 1
    assert "<b>COMPRA PARCIAL</b> + <b>VENTA PARCIAL</b>" in texto


def test_varias_ventas_parciales_no_repiten_la_palabra():
    """Tres ventas parciales del mismo activo: la palabra sale una vez.

    Antes salía "VENTA PARCIAL + VENTA PARCIAL + VENTA PARCIAL", que se lee
    fatal (es el caso real que reportó el usuario).
    """
    before = snap(asset(1002, 1.0, extra={2002: 1.0, 3002: 1.0}))
    after = snap(asset(1002, 0.5, extra={2002: 0.5, 3002: 0.5}))
    texto = solo(diff_snapshots(before, after))
    assert texto.count("VENTA PARCIAL") == 1, texto
    assert "×" not in texto


def test_varias_compras_parciales_no_llevan_cuenta():
    """Dos posiciones que suben y una nueva: "COMPRA PARCIAL", sin ×3."""
    before = snap(asset(1002, 1.0, extra={2002: 1.0}))
    after = snap(asset(1002, 1.5, position_id=1002, extra={2002: 1.5, 3002: 0.3}))
    texto = solo(diff_snapshots(before, after))
    assert texto.count("COMPRA PARCIAL") == 1, texto
    assert "×" not in texto


def test_compra_y_varias_ventas_parciales_en_el_mismo_bloque():
    before = snap(asset(1002, 1.0, extra={2002: 1.0, 3002: 1.0}))
    after = snap(asset(1002, 1.0, extra={2002: 0.5, 3002: 0.5, 4002: 0.4}))
    texto = solo(diff_snapshots(before, after))
    # Una compra (posicion nueva) y dos ventas parciales, cada verbo una vez.
    assert "<b>COMPRA PARCIAL</b> + <b>VENTA PARCIAL</b>" in texto
    assert "×" not in texto


# ---------------------------------------------------------------------- #
# Nada de datos de posición
# ---------------------------------------------------------------------- #
def test_el_bloque_de_operacion_no_lleva_unidades_ni_precios_ni_fechas():
    """La cabecera lleva la fecha del aviso (correcto); la operación, ninguna."""
    changes = diff_snapshots(snap(), snap(asset(1002, 0.648492, pct=18.29)))
    bloque = solo(changes)
    assert "uds" not in bloque
    assert "USD" not in bloque
    assert "0,648492" not in bloque
    assert "@" not in bloque
    import re

    assert not re.search(r"\d{2}/\d{2}/\d{4}", bloque), "no debe haber fechas"


def test_el_mensaje_completo_lleva_la_fecha_del_aviso():
    changes = diff_snapshots(snap(), snap(asset(1002, 1.0)))
    texto = render_user_changes(
        USER, changes, instrument_labels=LABELS, timestamp="02/10/2026 14:00"
    )
    assert "02/10/2026 14:00" in texto


def test_cabecera_solo_lleva_el_nombre_y_la_fecha():
    """Pedido el 20/10: fuera "ha movido su cartera" y el recuento."""
    changes = diff_snapshots(snap(), snap(asset(1002, 1.0), asset(5712, 1.0)))
    texto = render_user_changes(
        USER, changes, instrument_labels={1002: "A", 5712: "B"}
    )
    assert "<b>Usuario Ejemplo</b>" in texto
    assert "ha movido su cartera" not in texto
    assert "operaciones en" not in texto
    assert "activos" not in texto
    assert "https://www.etoro.com/people/usuarioejemplo/portfolio" in texto


def test_la_hora_del_mensaje_es_de_madrid():
    """18:06 UTC en octubre son las 20:06 en Madrid; en enero, las 19:06."""
    verano = datetime(2026, 10, 20, 18, 6, tzinfo=timezone.utc)
    assert en_madrid(verano).strftime("%d/%m/%Y %H:%M") == "20/10/2026 20:06"

    invierno = datetime(2026, 1, 15, 18, 6, tzinfo=timezone.utc)
    assert en_madrid(invierno).strftime("%d/%m/%Y %H:%M") == "15/01/2026 19:06"


def test_la_hora_del_mensaje_no_lleva_utc():
    texto = render_user_changes(
        USER,
        diff_snapshots(snap(), snap(asset(1002, 1.0))),
        instrument_labels=LABELS,
    )
    assert "UTC" not in texto


def test_el_mensaje_no_lleva_linea_de_ajuste_ni_peso_con_etiqueta():
    """El usuario no quiere ni la línea del ajuste ni "peso en cartera:"."""
    changes = diff_snapshots(
        snap(asset(1002, 1.0, pct=20.0)), snap(asset(1002, 0.5, pct=9.0))
    )
    texto = render_user_changes(USER, changes, instrument_labels=LABELS)
    assert "20,00% → 9,00%" in texto
    assert "peso en cartera" not in texto
    assert "peso antes" not in texto
    assert "Reducción proporcional" not in texto


def test_el_mensaje_no_lleva_cierra_todo_el_activo():
    changes = diff_snapshots(snap(asset(1002, 1.0)), snap())
    texto = render_user_changes(USER, changes, instrument_labels=LABELS)
    assert "cierra todo el activo" not in texto


def test_troceado_de_mensajes_largos():
    text = "\n".join(f"linea {i}" for i in range(2000))
    chunks = split_message(text, limit=100)
    assert all(len(c) <= 100 for c in chunks)
    assert split_message("hola", limit=100) == ["hola"]
