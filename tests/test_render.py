"""Tests del formateo de mensajes y del troceado de Telegram."""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from etoro_monitor.diff import diff_snapshots  # noqa: E402
from etoro_monitor.models import EtoroUser, Position, Snapshot  # noqa: E402
from etoro_monitor.render import (  # noqa: E402
    format_datetime,
    format_number,
    format_weight,
    render_instrument_block,
    render_user_changes,
)
from etoro_monitor.telegram import split_message  # noqa: E402

# Usuario ficticio: los tests no deben contener datos de personas reales.
USER = EtoroUser(
    username="UsuarioEjemplo",
    gcid=1111111,
    real_cid=2222222,
    first_name="Usuario",
    last_name="Ejemplo",
    allow_display_full_name=True,
)


def test_formato_espanol():
    assert format_number(1234.5678) == "1.234,57"
    assert format_number(0.648492, 6) == "0,648492"
    assert format_weight(17.5672) == "17,57%"
    assert format_weight(None) == "?"


def test_fecha():
    assert format_datetime("2025-07-08T14:02:14.517Z") == "08/07/2025 14:02 UTC"
    assert format_datetime("") == ""


def test_mensaje_de_compra():
    before = Snapshot(user=USER, positions={})
    after = Snapshot(
        user=USER,
        positions={
            1: Position(
                position_id=1,
                instrument_id=1002,
                is_buy=True,
                amount=0.648492,
                open_rate=176.2875,
                open_datetime="2025-07-08T14:02:14.517Z",
            )
        },
    )
    changes = diff_snapshots(before, after)
    text = render_user_changes(
        USER, changes, instrument_labels={1002: "Alphabet (GOOG)"}, weights={1002: 17.5}
    )
    assert "COMPRA" in text
    assert "Alphabet (GOOG)" in text
    assert "0,648492" in text
    assert "17,50%" in text
    assert "Usuario Ejemplo" in text
    # El enlace usa la forma canónica (minúsculas), que es la que eToro sirve
    # sin redirección.
    assert "https://www.etoro.com/people/usuarioejemplo/portfolio" in text


def test_bloque_agrupa_varias_posiciones_del_mismo_activo():
    before = Snapshot(user=USER, positions={})
    after = Snapshot(
        user=USER,
        positions={
            i: Position(
                position_id=i,
                instrument_id=1004,
                is_buy=True,
                amount=0.1 * i,
                open_rate=100.0 + i,
                open_datetime="2025-01-0%dT10:00:00Z" % i,
            )
            for i in range(1, 5)
        },
    )
    changes = diff_snapshots(before, after)
    block = render_instrument_block(changes, instrument_label="Amazon.com Inc (AMZN)")
    # Un solo título para los 4 cambios y el total de unidades sumado
    assert block.count("COMPRA") == 1
    assert "4 posiciones nuevas" in block
    assert "1,000000" in block
    assert "nueva en cartera" in block


def test_bloque_de_venta_total():
    before = Snapshot(
        user=USER,
        positions={
            i: Position(
                position_id=i,
                instrument_id=1180,
                is_buy=True,
                amount=0.5,
                open_rate=44.0,
                open_datetime="2024-03-0%dT10:00:00Z" % i,
            )
            for i in range(1, 4)
        },
    )
    after = Snapshot(user=USER, positions={})
    changes = diff_snapshots(before, after)
    block = render_instrument_block(changes, instrument_label="Xiaomi Corp (1810.HK)")
    assert "VENTA" in block
    assert "cierra todo el activo" in block
    assert "3 posiciones cerradas" in block
    assert "1,500000" in block
    assert "entre 01/03/2024 y 03/03/2024" in block


def test_mensaje_de_short():
    before = Snapshot(user=USER, positions={})
    after = Snapshot(
        user=USER,
        positions={
            7: Position(
                position_id=7,
                instrument_id=1002,
                is_buy=False,
                amount=1.0,
                open_rate=100.0,
                open_datetime="2025-01-01T00:00:00Z",
            )
        },
    )
    text = render_user_changes(
        USER, diff_snapshots(before, after), instrument_labels={1002: "Alphabet"}
    )
    assert "ABRE CORTO" in text


def test_cabecera_resume_el_total():
    before = Snapshot(user=USER, positions={})
    after = Snapshot(
        user=USER,
        positions={
            1: Position(1, 1002, True, 1.0, 100.0, "2025-01-01T00:00:00Z"),
            2: Position(2, 1004, True, 1.0, 100.0, "2025-01-01T00:00:00Z"),
        },
    )
    text = render_user_changes(
        USER, diff_snapshots(before, after), instrument_labels={1002: "A", 1004: "B"}
    )
    assert "2 cambios en 2 activos" in text
    assert "2 de compra" in text


def test_troceado_de_mensajes_largos():
    text = "\n".join(f"linea {i}" for i in range(2000))
    chunks = split_message(text, limit=100)
    assert all(len(c) <= 100 for c in chunks)
    assert "".join(chunks).replace("\n", "") == text.replace("\n", "")


def test_mensaje_corto_no_se_trocea():
    assert split_message("hola", limit=100) == ["hola"]
