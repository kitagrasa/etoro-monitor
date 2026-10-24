"""Tests de la detección de operaciones (sin red, con datos a mano).

La idea que hay que proteger: comparar **posiciones** (con sus unidades), de
modo que el mercado moviéndose nunca genere un aviso, y que se distinga una
compra de una compra parcial.

En los datos de prueba, el identificador de posición coincide con el del activo
(`position_id=instrument_id`) para que se lea fácil. Cuando una compra es
parcial, la posición nueva lleva un identificador distinto: eso es justo lo que
pasa en eToro, donde cada operación tiene su propio `PositionID`.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from etoro_monitor.diff import diff_snapshots, netear, split_adjustment  # noqa: E402
from etoro_monitor.models import Asset, EtoroUser, Position, Snapshot  # noqa: E402

# Usuario ficticio: los tests no deben contener datos de personas reales.
USER = EtoroUser(
    username="UsuarioEjemplo", gcid=1111111, real_cid=2222222, first_name="Usuario"
)


def position(position_id: int, units: float) -> Position:
    return Position(position_id=position_id, units=units)


def asset(
    instrument_id: int,
    units: float,
    *,
    direction: str = "Buy",
    pct: float = 1.0,
    position_id: int | None = None,
    extra: dict[int, float] | None = None,
):
    """Un activo con una posición (`position_id` por defecto el del activo).

    `extra` añade más posiciones abiertas, para los casos en que alguien tiene
    dos operaciones vivas sobre el mismo activo.
    """
    principal = position_id if position_id is not None else instrument_id
    positions = {principal: position(principal, units)}
    for otro_id, otras_unidades in (extra or {}).items():
        positions[otro_id] = position(otro_id, otras_unidades)
    return Asset(
        instrument_id=instrument_id,
        direction=direction,
        positions=positions,
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


def test_ruido_de_coma_flotante_no_genera_avisos():
    """Sumar muchas posiciones en distinto orden no debe dar diferencias."""
    before = snap(asset(1002, 11.417114000000001))
    after = snap(asset(1002, 11.417114))
    assert diff_snapshots(before, after) == []


# ---------------------------------------------------------------------- #
# Las cuatro operaciones
# ---------------------------------------------------------------------- #
def test_compra_en_activo_nuevo():
    changes = diff_snapshots(
        snap(asset(1002, 0.5)), snap(asset(1002, 0.5), asset(5712, 0.25))
    )
    assert kinds(changes) == [("opened", 5712)]
    assert changes[0].instrument_was_new is True
    assert changes[0].is_partial is False


def test_compra_parcial_al_anadir_unidades():
    """Más dinero en la misma posición: compra parcial, no una ampliación."""
    changes = diff_snapshots(snap(asset(1002, 1.0)), snap(asset(1002, 1.6)))
    assert kinds(changes) == [("increased", 1002)]
    assert changes[0].is_partial is True
    assert changes[0].position_id == 1002


def test_compra_parcial_al_abrir_una_segunda_posicion():
    """Ya tenía Alphabet y compra más: aparece una posición nueva con su ID."""
    before = snap(asset(1002, 1.0))
    after = snap(asset(1002, 1.0, extra={9999: 0.6}))
    changes = diff_snapshots(before, after)
    assert kinds(changes) == [("opened", 1002)]
    # No es "nueva en cartera": del activo ya tenía algo.
    assert changes[0].instrument_was_new is False
    assert changes[0].position_id == 9999
    assert changes[0].after.units == 1.6


def test_vender_y_comprar_a_la_vez_son_dos_operaciones():
    """Baja una posición y abre otra: dos avisos, no un neto de unidades."""
    before = snap(asset(1002, 1.0, position_id=1002))
    after = snap(asset(1002, 0.8, position_id=9999, extra={1002: 0.5}))
    changes = diff_snapshots(before, after)
    assert kinds(changes) == [("opened", 1002), ("reduced", 1002)]
    assert changes[0].is_partial is True  # del activo ya tenía: compra parcial
    assert changes[1].is_partial is True  # venta parcial


def test_venta_parcial_al_quitar_unidades():
    changes = diff_snapshots(snap(asset(1002, 1.0)), snap(asset(1002, 0.4)))
    assert kinds(changes) == [("reduced", 1002)]
    assert changes[0].is_partial is True
    assert changes[0].before.units == 1.0
    assert changes[0].after.units == 0.4


def test_venta_parcial_al_cerrar_una_de_dos_posiciones():
    """Cierra una posición pero le queda otra: la venta es parcial."""
    before = snap(asset(1002, 0.6, position_id=9999, extra={1002: 0.5}))
    after = snap(asset(1002, 0.6, position_id=9999))
    changes = diff_snapshots(before, after)
    assert kinds(changes) == [("closed", 1002)]
    assert changes[0].is_partial is True
    assert changes[0].instrument_now_empty is False


def test_venta_total_cierra_el_activo():
    changes = diff_snapshots(
        snap(asset(1002, 1.0), asset(1004, 2.0)), snap(asset(1004, 2.0))
    )
    assert kinds(changes) == [("closed", 1002)]
    assert changes[0].instrument_now_empty is True
    assert changes[0].is_partial is False


def test_mezcla_de_operaciones():
    before = snap(asset(1002, 0.5), asset(1004, 1.0), asset(1003, 2.0))
    after = snap(asset(1002, 0.7), asset(1003, 2.0), asset(1484, 0.1))
    changes = diff_snapshots(before, after)
    assert kinds(changes) == [
        ("opened", 1484),     # compra
        ("increased", 1002),  # compra parcial
        ("closed", 1004),     # vende todo
    ]


# ---------------------------------------------------------------------- #
# Cortos
# ---------------------------------------------------------------------- #
def test_abrir_un_corto_se_detecta_como_posicion_nueva():
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


# ---------------------------------------------------------------------- #
# Cambiar de lado en la misma pasada
# ---------------------------------------------------------------------- #
def test_pasar_de_largo_a_corto_se_ve_como_venta_y_compra():
    """Un cambio de lado son dos operaciones, no una resta de unidades."""
    before = snap(asset(1002, 1.0, direction="Buy"))
    after = snap(asset(1002, 0.4, direction="Sell"))
    changes = diff_snapshots(before, after)
    assert kinds(changes) == [("opened", 1002), ("closed", 1002)]
    assert changes[0].is_short is True
    assert changes[1].is_short is False


# ---------------------------------------------------------------------- #
# Ajustes proporcionales: no son compras ni ventas
# ---------------------------------------------------------------------- #
def _muchas(factor: float, cuantas: int = 12, *, pct: float = 10.0):
    """Un activo con `cuantas` posiciones, todas multiplicadas por `factor`."""
    return asset(
        1002,
        1.0 * factor,
        pct=pct,
        extra={2000 + i: 1.0 * factor for i in range(cuantas - 1)},
    )


def test_un_ajuste_proporcional_no_son_ventas():
    """El caso real: 12 posiciones bajan lo mismo (0,3%) -> un ajuste."""
    changes = diff_snapshots(snap(_muchas(1.0)), snap(_muchas(0.997)))
    assert len(changes) == 12
    operaciones, ajuste = split_adjustment(
        changes, total_positions=12, total_assets=1
    )
    assert operaciones == []
    assert ajuste is not None
    assert ajuste.positions == 12
    assert abs(ajuste.factor - 0.997) < 1e-9
    assert abs(ajuste.percentage - 0.3) < 1e-9


def test_una_venta_suelta_no_es_un_ajuste():
    changes = diff_snapshots(snap(asset(1002, 1.0)), snap(asset(1002, 0.5)))
    operaciones, ajuste = split_adjustment(
        changes, total_positions=1, total_assets=1
    )
    assert ajuste is None
    assert len(operaciones) == 1


def test_un_ajuste_no_se_come_una_venta_real():
    """12 posiciones al 0,997 (ajuste) y una que se vende a la mitad (real)."""
    before = snap(_muchas(1.0))
    after = snap(
        asset(
            1002,
            0.5,
            extra={2000 + i: 0.997 for i in range(11)},
        )
    )
    operaciones, ajuste = split_adjustment(
        diff_snapshots(before, after), total_positions=12, total_assets=1
    )
    assert ajuste is not None
    assert ajuste.positions == 11
    assert len(operaciones) == 1
    assert operaciones[0].position_id == 1002


def test_cambios_de_tamanos_distintos_no_son_un_ajuste():
    """Si cada posición baja una cosa distinta, son operaciones."""
    before = snap(
        asset(1002, 1.0, extra={2000: 1.0, 2001: 1.0, 2002: 1.0, 2003: 1.0})
    )
    after = snap(
        asset(1002, 0.8, extra={2000: 0.9, 2001: 0.7, 2002: 0.85, 2003: 0.6})
    )
    operaciones, ajuste = split_adjustment(
        diff_snapshots(before, after), total_positions=5, total_assets=1
    )
    assert ajuste is None
    assert len(operaciones) == 5


def test_una_subida_proporcional_no_se_filtra_son_compras():
    changes = diff_snapshots(snap(_muchas(1.0)), snap(_muchas(1.003)))
    operaciones, ajuste = split_adjustment(
        changes, total_positions=12, total_assets=1
    )
    assert ajuste is None
    assert len(operaciones) == 12


def test_las_altas_y_las_bajas_de_posicion_se_mantienen():
    """Un ajuste proporcional no debe ocultar un cierre de posición."""
    before = snap(
        asset(1002, 1.0, extra={2000 + i: 1.0 for i in range(5)}),
        asset(1003, 1.0, extra={3000 + i: 1.0 for i in range(5)}),
        asset(5712, 1.0),
    )
    after = snap(
        asset(1002, 0.997, extra={2000 + i: 0.997 for i in range(5)}),
        asset(1003, 0.997, extra={3000 + i: 0.997 for i in range(5)}),
    )
    operaciones, ajuste = split_adjustment(
        diff_snapshots(before, after), total_positions=13, total_assets=3
    )
    assert ajuste is not None and ajuste.positions == 12
    assert kinds(operaciones) == [("closed", 5712)]


def test_vender_parte_de_un_solo_activo_no_es_un_ajuste():
    """Un activo con muchas posiciones, al 10%: es una venta de ese activo."""
    before = snap(
        asset(1002, 1.0, extra={2000 + i: 1.0 for i in range(19)}),
        asset(1003, 1.0, extra={3000 + i: 1.0 for i in range(19)}),
    )
    after = snap(
        asset(1002, 0.9, extra={2000 + i: 0.9 for i in range(19)}),
        asset(1003, 1.0, extra={3000 + i: 1.0 for i in range(19)}),
    )
    operaciones, ajuste = split_adjustment(
        diff_snapshots(before, after), total_positions=40, total_assets=2
    )
    assert ajuste is None, "un solo activo no es un ajuste de cartera"
    assert len(operaciones) == 20


def test_un_ajuste_que_no_cubre_la_cartera_no_es_un_ajuste():
    """Bajar el 0,3% de 6 posiciones de una cartera de 106 no es un ajuste."""
    before = snap(
        asset(1002, 1.0, extra={2000 + i: 1.0 for i in range(5)}),
        asset(1003, 1.0, extra={3000 + i: 1.0 for i in range(99)}),
    )
    after = snap(
        asset(1002, 0.997, extra={2000 + i: 0.997 for i in range(5)}),
        asset(1003, 1.0, extra={3000 + i: 1.0 for i in range(99)}),
    )
    operaciones, ajuste = split_adjustment(
        diff_snapshots(before, after), total_positions=106, total_assets=2
    )
    assert ajuste is None
    assert len(operaciones) == 6


def test_un_ajuste_en_una_cartera_de_un_solo_activo_si_se_resume():
    """Si toda la cartera es un activo, no hay "varios activos" que exigir."""
    changes = diff_snapshots(snap(_muchas(1.0)), snap(_muchas(0.997)))
    operaciones, ajuste = split_adjustment(
        changes, total_positions=12, total_assets=1
    )
    assert ajuste is not None
    assert operaciones == []


def test_un_recorte_minusculo_de_toda_la_cartera_es_un_ajuste():
    """20/10/2026: un recorte del 0,05% en toda la cartera no son ventas.

    Había un mínimo de bajada del 0,05% y este recorte caía justo ahí: se
    colaba y salían 36 "ventas parciales" de 0,01 puntos de peso cada una.
    El tamaño de la bajada no dice nada; lo que dice algo es que sea
    proporcional y que coja a la cartera entera.
    """
    changes = diff_snapshots(snap(_muchas(1.0)), snap(_muchas(0.9995)))
    assert len(changes) == 12

    operaciones, ajuste = split_adjustment(
        changes, total_positions=12, total_assets=1
    )

    assert operaciones == []
    assert ajuste is not None
    assert ajuste.positions == 12
    assert abs(ajuste.percentage - 0.05) < 1e-9


def test_un_recorte_minusculo_no_tapa_la_venta_de_verdad():
    """El caso completo del 20/10/2026: solo se avisa de la venta real.

    La cartera tenía 11 activos y 36 posiciones. eToro recortó el 0,05% de
    todas y, en la misma pasada, el dueño vendió el 43,5% de un activo (una de
    sus posiciones desapareció del todo). De ahí solo hay que avisar de esa
    venta: las otras 28 posiciones son el recorte.
    """
    recorte = 0.9995
    venta = 0.565
    # 10 activos, 28 posiciones en total (las del recorte).
    cuentas = [4, 3, 3, 3, 3, 3, 3, 3, 2, 1]

    def antes():
        activos = [
            asset(
                iid,
                1.0,
                extra={iid * 100 + i: 1.0 for i in range(cuantas - 1)},
            )
            for iid, cuantas in zip(range(1002, 1012), cuentas)
        ]
        # El activo de la venta: 8 posiciones, una de ellas desaparece.
        activos.append(asset(11230, 1.0, extra={1123000: 0.02, 1123001: 1.0,
                                                1123002: 1.0, 1123003: 1.0,
                                                1123004: 1.0, 1123005: 1.0,
                                                1123006: 1.0}))
        return snap(*activos)

    def despues():
        activos = [
            asset(
                iid,
                recorte,
                extra={iid * 100 + i: recorte for i in range(cuantas - 1)},
            )
            for iid, cuantas in zip(range(1002, 1012), cuentas)
        ]
        activos.append(asset(11230, venta, extra={1123001: venta,
                                                  1123002: venta,
                                                  1123003: venta,
                                                  1123004: venta,
                                                  1123005: venta,
                                                  1123006: venta}))
        return snap(*activos)

    changes = diff_snapshots(antes(), despues())
    assert len(changes) == 36

    operaciones, ajuste = split_adjustment(
        changes, total_positions=36, total_assets=11
    )

    assert ajuste is not None
    assert ajuste.positions == 28
    # Lo único que queda es la venta de verdad: sus 7 posiciones reducidas y
    # la que se cerró.
    assert len(operaciones) == 8
    assert {c.instrument_id for c in operaciones} == {11230}


# ---------------------------------------------------------------------- #
# El neto del activo: lo único que hay que copiar
# ---------------------------------------------------------------------- #
def netos(before: Snapshot, after: Snapshot) -> list:
    return netear(diff_snapshots(before, after))


def test_netear_comprar_y_vender_lo_mismo_no_deja_nada():
    """Comprar 5 y vender esos mismos 5 entre dos pasadas: no hay nada que hacer.

    Por posición sí han pasado dos cosas (una posición se cierra y otra se
    abre), pero el activo termina con las mismas unidades: no se avisa.
    """
    before = snap(asset(1002, 5.0, extra={9999: 5.0}))  # 10 unidades
    after = snap(asset(1002, 5.0, extra={8888: 5.0}))  # otras 10, con otro ID

    assert len(diff_snapshots(before, after)) == 2, "por posición sí hubo dos"
    assert netos(before, after) == []


def test_netear_comprar_5_y_vender_4_deja_una_compra_parcial_de_1():
    """Lo que hay que copiar es el neto: 1 unidad más."""
    before = snap(asset(1002, 5.0, extra={9999: 5.0}))  # 10 unidades
    after = snap(asset(1002, 5.0, extra={8888: 6.0}))  # 11 unidades

    cambios = netos(before, after)

    assert [c.kind for c in cambios] == ["increased"]
    assert cambios[0].after.units - cambios[0].before.units == 1.0
    assert cambios[0].is_partial is True


def test_netear_una_venta_por_partes_es_una_sola_venta_parcial():
    before = snap(asset(1002, 5.0, extra={9999: 5.0}))  # 10 unidades
    after = snap(asset(1002, 5.0, extra={8888: 4.0}))  # 9 unidades

    cambios = netos(before, after)

    assert [c.kind for c in cambios] == ["reduced"]
    assert cambios[0].after.units - cambios[0].before.units == -1.0
    assert cambios[0].is_partial is True


def test_netear_un_activo_nuevo_es_una_compra():
    cambios = netos(snap(), snap(asset(1002, 0.6)))

    assert [c.kind for c in cambios] == ["opened"]
    assert cambios[0].instrument_was_new is True
    assert cambios[0].is_partial is False


def test_netear_un_activo_que_desaparece_es_una_venta():
    cambios = netos(snap(asset(1002, 1.0)), snap())

    assert [c.kind for c in cambios] == ["closed"]
    assert cambios[0].instrument_now_empty is True
    assert cambios[0].is_partial is False


def test_netear_abrir_una_posicion_mas_es_compra_parcial():
    """El activo ya estaba: meter más dinero es una compra parcial."""
    cambios = netos(snap(asset(1002, 1.0)), snap(asset(1002, 1.0, extra={9999: 0.5})))

    assert [c.kind for c in cambios] == ["increased"]
    assert cambios[0].instrument_was_new is False


def test_netear_deja_un_cambio_por_activo():
    """Tres posiciones del mismo activo que bajan: un solo cambio."""
    before = snap(asset(1002, 1.0, extra={2001: 1.0, 2002: 1.0}))
    after = snap(asset(1002, 0.5, extra={2001: 0.5, 2002: 0.5}))

    assert len(diff_snapshots(before, after)) == 3
    assert [c.kind for c in netos(before, after)] == ["reduced"]


# ---------------------------------------------------------------------- #
# El mínimo de cambios no puede superar el tamaño de la cartera
# ---------------------------------------------------------------------- #
def test_un_ajuste_en_una_cartera_de_tres_posiciones_se_detecta():
    """24/10/2026: en una cartera de 3 posiciones no se pueden pedir 4 cambios.

    Un ingreso o una retirada diluye todas las posiciones en el mismo factor; en
    una cartera pequeña hay menos cambios que el mínimo y saldrían "ventas
    parciales" falsas.
    """
    changes = diff_snapshots(
        snap(_muchas(1.0, cuantas=3)), snap(_muchas(0.997, cuantas=3))
    )
    assert len(changes) == 3

    operaciones, ajuste = split_adjustment(changes, total_positions=3, total_assets=1)

    assert operaciones == []
    assert ajuste is not None
    assert ajuste.positions == 3


def test_un_ajuste_en_una_cartera_de_dos_posiciones_tambien():
    changes = diff_snapshots(
        snap(_muchas(1.0, cuantas=2)), snap(_muchas(0.997, cuantas=2))
    )
    operaciones, ajuste = split_adjustment(changes, total_positions=2, total_assets=1)
    assert operaciones == []
    assert ajuste is not None


def test_con_una_sola_posicion_nunca_hay_ajuste():
    """Un único cambio es indistinguible de una venta: no se puede silenciar."""
    changes = diff_snapshots(snap(asset(1002, 1.0)), snap(asset(1002, 0.5)))

    operaciones, ajuste = split_adjustment(changes, total_positions=1, total_assets=1)

    assert ajuste is None
    assert len(operaciones) == 1


def test_en_una_cartera_de_tres_posiciones_dos_ventas_no_son_ajuste():
    """Que el mínimo baje no significa que cualquier cosa sea un ajuste."""
    before = snap(asset(1002, 1.0, extra={2001: 1.0, 2002: 1.0}))
    after = snap(asset(1002, 0.5, extra={2001: 0.5, 2002: 1.0}))

    operaciones, ajuste = split_adjustment(
        diff_snapshots(before, after), total_positions=3, total_assets=1
    )

    assert ajuste is None
    assert len(operaciones) == 2


def test_en_una_cartera_grande_el_minimo_sigue_siendo_cuatro():
    """La cartera de 25 posiciones sigue exigiendo 4 cambios, como antes."""
    before = snap(asset(1002, 1.0, extra={2000 + i: 1.0 for i in range(24)}))
    after = snap(
        asset(
            1002,
            0.997,
            extra={2000 + i: (1.0 if i < 22 else 0.997) for i in range(24)},
        )
    )
    changes = diff_snapshots(before, after)
    assert len(changes) == 3, "solo cambian 3 de las 25"

    operaciones, ajuste = split_adjustment(changes, total_positions=25, total_assets=1)
    assert ajuste is None
    assert len(operaciones) == 3

# ---------------------------------------------------------------------- #
# Modo cartera: se compara el total del activo, sin posiciones
# ---------------------------------------------------------------------- #
def total(iid, unidades, *, pct=1.0, direction="Buy"):
    """Activo en modo cartera: solo el total (sin posiciones)."""
    return Asset(
        instrument_id=iid,
        direction=direction,
        positions={},
        invested_pct=pct,
        total=unidades,
    )


def test_modo_cartera_sin_cambio_no_da_nada():
    assert diff_snapshots(snap(total(1002, 1.0)), snap(total(1002, 1.0))) == []


def test_modo_cartera_compra_parcial():
    cambios = diff_snapshots(snap(total(1002, 1.0)), snap(total(1002, 1.5)))
    assert [c.kind for c in cambios] == ["increased"]
    assert cambios[0].is_partial is True
    assert cambios[0].position_id is None


def test_modo_cartera_venta_parcial():
    cambios = diff_snapshots(snap(total(1002, 1.0)), snap(total(1002, 0.4)))
    assert [c.kind for c in cambios] == ["reduced"]
    assert cambios[0].is_partial is True


def test_modo_cartera_activo_nuevo_y_activo_que_desaparece():
    nuevo = diff_snapshots(snap(total(1002, 1.0)), snap(total(1002, 1.0), total(1003, 2.0)))
    assert [(c.kind, c.instrument_id) for c in nuevo] == [("opened", 1003)]
    assert nuevo[0].instrument_was_new is True
    assert nuevo[0].is_partial is False

    fuera = diff_snapshots(snap(total(1002, 1.0), total(1003, 2.0)), snap(total(1003, 2.0)))
    assert [(c.kind, c.instrument_id) for c in fuera] == [("closed", 1002)]
    assert fuera[0].instrument_now_empty is True


def test_modo_cartera_comprar_y_vender_lo_mismo_no_deja_nada():
    """Comprar 5 y vender 5 entre dos pasadas: el total no cambia."""
    assert diff_snapshots(snap(total(1002, 10.0)), snap(total(1002, 10.0))) == []


def test_modo_cartera_cambiar_de_lado_son_dos_operaciones():
    cambios = diff_snapshots(
        snap(total(1002, 1.0, direction="Buy")),
        snap(total(1002, 1.0, direction="Sell")),
    )
    assert sorted(c.kind for c in cambios) == ["closed", "opened"]


def test_modo_cartera_tambien_vale_para_migrar():
    """Un estado con posiciones contra una foto con solo total: se comparan
    totales (si no, todas las posiciones parecerían cerradas)."""
    cambios = diff_snapshots(
        snap(asset(1002, 1.0, extra={9999: 0.5})), snap(total(1002, 1.5))
    )
    assert cambios == []

    cambios = diff_snapshots(
        snap(asset(1002, 1.0, extra={9999: 0.5})), snap(total(1002, 2.0))
    )
    assert [c.kind for c in cambios] == ["increased"]

