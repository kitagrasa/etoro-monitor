"""Tests de los mensajes: compra/venta total o parcial, sin unidades ni precios."""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from etoro_monitor.diff import diff_snapshots  # noqa: E402
from etoro_monitor.models import Asset, EtoroUser, Position, Snapshot  # noqa: E402
from etoro_monitor.render import (  # noqa: E402
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
    assert "peso en cartera: 18,29%" in texto


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
    assert "peso en cartera: 16,00%" in texto


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
    assert "cierra todo el activo" in texto
    assert "peso antes: 1,61%" in texto


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
        USER, changes, instrument_labels=LABELS, timestamp="02/10/2026 12:00 UTC"
    )
    assert "02/10/2026 12:00 UTC" in texto


def test_cabecera_resume_el_total():
    changes = diff_snapshots(snap(), snap(asset(1002, 1.0), asset(5712, 1.0)))
    texto = render_user_changes(
        USER, changes, instrument_labels={1002: "A", 5712: "B"}
    )
    assert "2 operaciones en 2 activos" in texto
    assert "Usuario Ejemplo" in texto
    assert "https://www.etoro.com/people/usuarioejemplo/portfolio" in texto


def test_troceado_de_mensajes_largos():
    text = "\n".join(f"linea {i}" for i in range(2000))
    chunks = split_message(text, limit=100)
    assert all(len(c) <= 100 for c in chunks)
    assert split_message("hola", limit=100) == ["hola"]
