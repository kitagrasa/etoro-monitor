"""Tests del árbitro del histórico: qué bajadas se descartan y cuáles no.

La regla que hay que proteger: el histórico **solo puede callar bajadas
pequeñas**. Ni una compra, ni una posición que desaparece, ni una venta grande
se pueden silenciar porque el histórico no las respalde.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from etoro_monitor.historial import (  # noqa: E402
    UMBRAL_BAJADA_PEQUENA,
    arbitrar,
    bajada_relativa,
    es_pequena,
)
from etoro_monitor.models import Asset, EtoroUser, Position, Snapshot  # noqa: E402

USER = EtoroUser(
    username="UsuarioEjemplo", gcid=1111111, real_cid=2222222, first_name="Usuario"
)


def asset(instrument_id, *, posiciones: dict[int, float], pct: float = 5.0) -> Asset:
    return Asset(
        instrument_id=instrument_id,
        direction="Buy",
        positions={pid: Position(pid, units) for pid, units in posiciones.items()},
        invested_pct=pct,
    )


def snap(*assets: Asset) -> Snapshot:
    return Snapshot(user=USER, assets={a.key: a for a in assets})


def changes_entre(antes: Snapshot, despues: Snapshot):
    from etoro_monitor.diff import diff_snapshots

    return diff_snapshots(antes, despues)


def solo(instrument_id: int, *, antes: float, despues: float, pid: int = None):
    """El cambio de una posición de un activo que baja (o sube) de unidades."""
    pid = instrument_id * 10 if pid is None else pid
    return changes_entre(
        snap(asset(instrument_id, posiciones={pid: antes})),
        snap(asset(instrument_id, posiciones={pid: despues})),
    )


# ---------------------------------------------------------------------- #
# Tamaño de la bajada
# ---------------------------------------------------------------------- #
def test_la_bajada_relativa_se_calcula_sobre_la_posicion():
    cambio, = solo(1002, antes=1.0, despues=0.4)
    assert abs(bajada_relativa(cambio) - 0.6) < 1e-9
    assert es_pequena(cambio) is False


def test_una_bajada_del_0_05_es_pequena():
    """El recorte real del 20/10: 1.449445 -> 1.448720 (0,05%)."""
    cambio, = solo(1002, antes=1.449445, despues=1.448720)
    assert abs(bajada_relativa(cambio) - 0.0005) < 1e-6
    assert es_pequena(cambio) is True


def test_el_umbral_esta_por_encima_de_los_recortes_conocidos():
    """Los recortes de eToro que hemos visto son del 0,05% y del 0,30%."""
    assert UMBRAL_BAJADA_PEQUENA > 0.0030


def test_una_subida_no_es_una_bajada():
    cambio, = solo(1002, antes=1.0, despues=1.2)
    assert cambio.kind == "increased"
    # Negativa = ha subido; nunca se puede descartar por "pequeña".
    assert bajada_relativa(cambio) < 0
    assert es_pequena(cambio) is False


# ---------------------------------------------------------------------- #
# Qué se descarta y qué no
# ---------------------------------------------------------------------- #
def test_recorte_pequeno_sin_cierres_se_descarta():
    """Cinco bajadas del 0,4% y el histórico sin cierres: son recortes."""
    cambios = []
    for iid in (1002, 1003, 1004, 1005, 1006):
        cambios.extend(solo(iid, antes=1.0, despues=0.996))

    assert len(cambios) == 5
    assert arbitrar(cambios, {}) == []


def test_una_venta_grande_se_avisa_aunque_el_historico_calle():
    """Si eToro no registrara las parciales, preferimos avisar de más."""
    cambios = solo(1002, antes=1.0, despues=0.60)
    assert len(arbitrar(cambios, {})) == 1


def test_una_bajada_pequena_con_cierre_parcial_en_el_historico_se_avisa():
    """El histórico tiene un cierre que no explica ninguna posición caída."""
    cambios = solo(1002, antes=1.0, despues=0.995)
    assert len(arbitrar(cambios, {1002: 1})) == 1


def test_un_cierre_que_ya_se_ve_en_las_posiciones_no_respalda_las_bajadas():
    """Cierra una posición (se ve) y además recorta las demás: solo el cierre.

    El histórico dice "1 cierre" y el diff dice "1 posición cerrada": ese cierre
    ya está contado, así que las bajadas pequeñas de ese activo son recortes.
    """
    antes = snap(
        asset(1002, posiciones={10020: 1.0, 10021: 1.0}),
    )
    despues = snap(
        asset(1002, posiciones={10021: 0.9995}),
    )
    cambios = changes_entre(antes, despues)
    assert sorted(c.kind for c in cambios) == ["closed", "reduced"]

    avisos = arbitrar(cambios, {1002: 1})

    assert [c.kind for c in avisos] == ["closed"]


def test_las_compras_y_las_aperturas_no_se_tocan_nunca():
    antes = snap(asset(1002, posiciones={10020: 1.0}))
    despues = snap(
        asset(1002, posiciones={10020: 1.0, 10021: 0.05}),
        asset(1003, posiciones={10030: 0.001}),
    )
    cambios = changes_entre(antes, despues)
    assert arbitrar(cambios, {}) == cambios


def test_una_posicion_que_desaparece_se_avisa_siempre():
    antes = snap(asset(1002, posiciones={10020: 1.0, 10021: 0.5}))
    despues = snap(asset(1002, posiciones={10021: 0.5}))
    cambios = changes_entre(antes, despues)
    assert [c.kind for c in cambios] == ["closed"]
    assert arbitrar(cambios, {}) == cambios


def test_solo_se_descartan_las_pequenas_de_los_activos_sin_cierre():
    """Mezcla: una venta grande, dos recortes, y un cierre en otro activo."""
    cambios = solo(1002, antes=1.0, despues=0.60)  # grande, se avisa
    cambios += solo(1003, antes=1.0, despues=0.999)  # pequeña, se calla
    cambios += solo(1004, antes=1.0, despues=0.998)  # pequeña, con cierre: se avisa

    avisos = arbitrar(cambios, {1004: 1})

    assert [c.instrument_id for c in avisos] == [1002, 1004]
