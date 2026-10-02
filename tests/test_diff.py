"""Tests de la detección de operaciones (sin red, con datos a mano).

La idea que hay que proteger: comparar solo unidades, de modo que el mercado
moviéndose nunca genere un aviso.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from etoro_monitor.diff import diff_snapshots  # noqa: E402
from etoro_monitor.models import Asset, EtoroUser, Snapshot  # noqa: E402

# Usuario ficticio: los tests no deben contener datos de personas reales.
USER = EtoroUser(
    username="UsuarioEjemplo", gcid=1111111, real_cid=2222222, first_name="Usuario"
)


def asset(instrument_id: int, units: float, *, direction: str = "Buy", pct: float = 1.0):
    return Asset(
        instrument_id=instrument_id,
        direction=direction,
        units=units,
        invested_pct=pct,
    )


def snap(*assets: Asset) -> Snapshot:
    return Snapshot(user=USER, assets={a.key: a for a in assets})


def kinds(changes):
    return [(c.kind, c.instrument_id) for c in changes]


# ---------------------------------------------------------------------- #
# Lo esencial: nada de falsos positivos
# ---------------------------------------------------------------------- #
def test_sin_operaciones_no_hay_cambios():
    before = snap(asset(1002, 0.5), asset(1004, 1.0))
    after = snap(asset(1002, 0.5), asset(1004, 1.0))
    assert diff_snapshots(before, after) == []


def test_solo_cambia_el_peso_no_es_una_operacion():
    """El peso se mueve con el mercado: no debe generar avisos."""
    before = snap(asset(1002, 11.417114, pct=11.41), asset(1004, 16.058518, pct=16.05))
    after = snap(asset(1002, 11.417114, pct=18.27), asset(1004, 16.058518, pct=30.31))
    assert diff_snapshots(before, after) == []


def test_solo_cambia_el_lado_no_aplica_sin_unidades():
    before = snap(asset(1002, 1.0, direction="Buy"))
    after = snap(asset(1002, 1.0, direction="Buy"))
    assert diff_snapshots(before, after) == []


def test_ruido_de_coma_flotante_no_genera_avisos():
    """Sumar muchas posiciones en distinto orden no debe dar diferencias."""
    before = snap(asset(1002, 11.417114000000001))
    after = snap(asset(1002, 11.417114))
    assert diff_snapshots(before, after) == []


# ---------------------------------------------------------------------- #
# Las cuatro operaciones
# ---------------------------------------------------------------------- #
def test_compra_en_activo_nuevo():
    changes = diff_snapshots(snap(asset(1002, 0.5)), snap(asset(1002, 0.5), asset(5712, 0.25)))
    assert kinds(changes) == [("opened", 5712)]
    assert changes[0].instrument_was_new is True


def test_ampliacion_con_mas_unidades():
    changes = diff_snapshots(snap(asset(1002, 1.0)), snap(asset(1002, 1.6)))
    assert kinds(changes) == [("increased", 1002)]


def test_reduccion_con_menos_unidades():
    changes = diff_snapshots(snap(asset(1002, 1.0)), snap(asset(1002, 0.4)))
    assert kinds(changes) == [("reduced", 1002)]
    assert changes[0].before.units == 1.0
    assert changes[0].after.units == 0.4


def test_venta_total_cierra_el_activo():
    changes = diff_snapshots(snap(asset(1002, 1.0), asset(1004, 2.0)), snap(asset(1004, 2.0)))
    assert kinds(changes) == [("closed", 1002)]
    assert changes[0].instrument_now_empty is True


def test_mezcla_de_operaciones():
    before = snap(asset(1002, 0.5), asset(1004, 1.0), asset(1003, 2.0))
    after = snap(asset(1002, 0.7), asset(1003, 2.0), asset(1484, 0.1))
    changes = diff_snapshots(before, after)
    assert kinds(changes) == [
        ("opened", 1484),   # compra
        ("increased", 1002),  # amplía
        ("closed", 1004),   # vende todo
    ]


# ---------------------------------------------------------------------- #
# Cortos
# ---------------------------------------------------------------------- #
def test_abrir_un_corto_se_detecta_como_activo_nuevo():
    changes = diff_snapshots(snap(), snap(asset(6, 0.354594, direction="Sell")))
    assert kinds(changes) == [("opened", 6)]
    assert changes[0].is_short is True


def test_largo_y_corto_del_mismo_activo_son_independientes():
    """Si un activo está en largo y en corto, se tratan por separado."""
    before = snap(asset(1002, 1.0, direction="Buy"))
    after = snap(
        asset(1002, 1.0, direction="Buy"),
        asset(1002, 0.5, direction="Sell"),
    )
    changes = diff_snapshots(before, after)
    assert kinds(changes) == [("opened", 1002)]
    assert changes[0].is_short is True


def test_cerrar_el_corto_no_afecta_al_largo():
    before = snap(
        asset(1002, 1.0, direction="Buy"),
        asset(1002, 0.5, direction="Sell"),
    )
    after = snap(asset(1002, 1.0, direction="Buy"))
    changes = diff_snapshots(before, after)
    assert kinds(changes) == [("closed", 1002)]
    assert changes[0].is_short is True
    # El activo sigue existiendo (en largo), así que NO cierra todo el activo.
    assert changes[0].instrument_now_empty is False


def test_ampliar_un_corto():
    before = snap(asset(6, 0.3, direction="Sell"))
    after = snap(asset(6, 0.9, direction="Sell"))
    changes = diff_snapshots(before, after)
    assert kinds(changes) == [("increased", 6)]
    assert changes[0].is_short is True
