"""Tests de los mensajes: cortos en negrita, sin unidades ni precios."""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from etoro_monitor.diff import diff_snapshots  # noqa: E402
from etoro_monitor.models import Asset, EtoroUser, Snapshot  # noqa: E402
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


def asset(instrument_id, units, *, direction="Buy", pct=1.0):
    return Asset(instrument_id, direction, units, pct)


def snap(*assets):
    return Snapshot(user=USER, assets={a.key: a for a in assets})


def solo(change):
    return render_operation(change, instrument_label=LABELS[change.instrument_id])


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
    texto = solo(changes[0])
    assert "<b>COMPRA</b>" in texto
    assert "Alphabet (GOOG)" in texto
    assert "nueva en cartera" in texto
    assert "peso en cartera: 18,29%" in texto


def test_ampliacion_muestra_el_peso_antes_y_despues():
    changes = diff_snapshots(
        snap(asset(1002, 1.0, pct=17.90)), snap(asset(1002, 1.6, pct=18.29))
    )
    texto = solo(changes[0])
    assert "<b>AMPLÍA</b>" in texto
    assert "17,90% → 18,29%" in texto


def test_reduccion():
    changes = diff_snapshots(
        snap(asset(1002, 1.0, pct=20.0)), snap(asset(1002, 0.4, pct=9.0))
    )
    texto = solo(changes[0])
    assert "<b>REDUCE</b>" in texto
    assert "20,00% → 9,00%" in texto


def test_venta_total():
    changes = diff_snapshots(snap(asset(1002, 1.0, pct=1.61)), snap())
    texto = solo(changes[0])
    assert "<b>VENTA</b>" in texto
    assert "cierra todo el activo" in texto
    assert "peso antes: 1,61%" in texto


# ---------------------------------------------------------------------- #
# Cortos: en negrita y con la palabra CORTO
# ---------------------------------------------------------------------- #
def test_abrir_corto_en_negrita():
    changes = diff_snapshots(snap(), snap(asset(6, 0.35, direction="Sell")))
    texto = solo(changes[0])
    assert "<b>ABRE CORTO</b>" in texto
    assert "CORTO" in texto


def test_ampliar_corto_en_negrita():
    changes = diff_snapshots(
        snap(asset(6, 0.3, direction="Sell")), snap(asset(6, 0.9, direction="Sell"))
    )
    assert "<b>AMPLÍA CORTO</b>" in solo(changes[0])


def test_reducir_corto_en_negrita():
    changes = diff_snapshots(
        snap(asset(6, 0.9, direction="Sell")), snap(asset(6, 0.3, direction="Sell"))
    )
    assert "<b>REDUCE CORTO</b>" in solo(changes[0])


def test_cerrar_corto_en_negrita():
    changes = diff_snapshots(snap(asset(6, 0.5, direction="Sell")), snap())
    assert "<b>CIERRA CORTO</b>" in solo(changes[0])


# ---------------------------------------------------------------------- #
# Nada de datos de posición
# ---------------------------------------------------------------------- #
def test_el_bloque_de_operacion_no_lleva_unidades_ni_precios_ni_fechas():
    """La cabecera lleva la fecha del aviso (correcto); la operación, ninguna."""
    changes = diff_snapshots(snap(), snap(asset(1002, 0.648492, pct=18.29)))
    bloque = solo(changes[0])
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
