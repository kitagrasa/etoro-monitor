"""Tests de la lógica de comparación (sin red, con datos a mano)."""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from etoro_monitor.diff import diff_snapshots  # noqa: E402
from etoro_monitor.models import EtoroUser, Position, Snapshot  # noqa: E402

# Usuario ficticio: los tests no deben contener datos de personas reales.
USER = EtoroUser(
    username="UsuarioEjemplo", gcid=1111111, real_cid=2222222, first_name="Usuario"
)


def pos(position_id: int, instrument_id: int, amount: float, rate: float = 100.0, is_buy=True):
    return Position(
        position_id=position_id,
        instrument_id=instrument_id,
        is_buy=is_buy,
        amount=amount,
        open_rate=rate,
        open_datetime="2025-07-08T14:02:14.517Z",
    )


def snap(*positions: Position) -> Snapshot:
    return Snapshot(user=USER, positions={p.position_id: p for p in positions})


def kinds(changes):
    return [(c.kind, c.position_id) for c in changes]


def test_sin_cambios():
    before = snap(pos(1, 1002, 0.5), pos(2, 1004, 1.0))
    after = snap(pos(1, 1002, 0.5), pos(2, 1004, 1.0))
    assert diff_snapshots(before, after) == []


def test_el_precio_no_genera_falsos_positivos():
    """Que la posición gane o pierda dinero no debe ser un 'cambio'."""
    before = snap(pos(1, 1002, 0.5, rate=100.0))
    after = snap(pos(1, 1002, 0.5, rate=100.0))
    assert diff_snapshots(before, after) == []


def test_compra_nueva_en_activo_nuevo():
    before = snap(pos(1, 1002, 0.5))
    after = snap(pos(1, 1002, 0.5), pos(2, 5712, 0.25))
    changes = diff_snapshots(before, after)
    assert kinds(changes) == [("opened", 2)]
    assert changes[0].instrument_was_new is True


def test_ampliacion_de_activo_ya_presente():
    before = snap(pos(1, 1002, 0.5))
    after = snap(pos(1, 1002, 0.5), pos(9, 1002, 0.25))
    changes = diff_snapshots(before, after)
    assert kinds(changes) == [("opened", 9)]
    assert changes[0].instrument_was_new is False


def test_venta_total():
    before = snap(pos(1, 1002, 0.5), pos(2, 1004, 1.0))
    after = snap(pos(2, 1004, 1.0))
    changes = diff_snapshots(before, after)
    assert kinds(changes) == [("closed", 1)]
    assert changes[0].instrument_now_empty is True


def test_venta_parcial_sin_cerrar_el_activo():
    before = snap(pos(1, 1002, 0.5), pos(2, 1002, 0.5))
    after = snap(pos(1, 1002, 0.5))
    changes = diff_snapshots(before, after)
    assert kinds(changes) == [("closed", 2)]
    assert changes[0].instrument_now_empty is False


def test_reduccion_parcial_del_mismo_positionid():
    before = snap(pos(1, 1002, 1.0))
    after = snap(pos(1, 1002, 0.4))
    changes = diff_snapshots(before, after)
    assert kinds(changes) == [("reduced", 1)]
    assert changes[0].before.amount == 1.0
    assert changes[0].after.amount == 0.4


def test_ampliacion_parcial_del_mismo_positionid():
    before = snap(pos(1, 1002, 1.0))
    after = snap(pos(1, 1002, 1.6))
    changes = diff_snapshots(before, after)
    assert kinds(changes) == [("increased", 1)]


def test_mezcla_completa():
    before = snap(pos(1, 1002, 0.5), pos(2, 1004, 1.0), pos(3, 1003, 2.0))
    after = snap(pos(1, 1002, 0.7), pos(3, 1003, 2.0), pos(4, 1484, 0.1))
    changes = diff_snapshots(before, after)
    assert kinds(changes) == [
        ("opened", 4),    # compra nueva
        ("increased", 1),  # amplía
        ("closed", 2),     # vende todo 1004
    ]


def test_tolerancia_a_ruido_de_redondeo():
    before = snap(pos(1, 1002, 0.5))
    after = snap(pos(1, 1002, 0.5 + 1e-12))
    assert diff_snapshots(before, after) == []


def test_orden_por_tipo():
    before = snap(pos(1, 1002, 0.5), pos(2, 1004, 1.0))
    after = snap(pos(1, 1002, 0.9), pos(3, 1003, 0.1))
    changes = diff_snapshots(before, after)
    assert [c.kind for c in changes] == ["opened", "increased", "closed"]
