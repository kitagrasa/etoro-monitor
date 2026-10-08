"""Tests del estado persistente: formato, poda y ficheros dañados.

Todos los datos son ficticios: no se usan nombres ni identificadores de
personas reales.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from etoro_monitor.models import Asset, EtoroUser, Position  # noqa: E402
from etoro_monitor.state import (  # noqa: E402
    STATE_VERSION,
    RuntimeState,
    State,
    StateCorruptError,
    cooldown_path,
)

EJEMPLO = "UsuarioEjemplo"
EJEMPLO_MIN = "usuarioejemplo"


def usuario(nombre: str) -> EtoroUser:
    return EtoroUser(username=nombre, gcid=1111111, real_cid=2222222)


def asset(instrument_id: int, units: float, *, direction: str = "Buy") -> Asset:
    return Asset(
        instrument_id=instrument_id,
        direction=direction,
        positions={instrument_id: Position(instrument_id, units)},
        invested_pct=1.0,
    )


# ---------------------------------------------------------------------- #
# Formato
# ---------------------------------------------------------------------- #
def test_slug_en_minusculas():
    assert usuario("UsuarioEjemplo").slug == EJEMPLO_MIN
    assert usuario("UsuarioEjemplo").portfolio_url == (
        f"https://www.etoro.com/people/{EJEMPLO_MIN}/portfolio"
    )


def test_la_clave_del_estado_es_el_slug(tmp_path):
    state = State(path=tmp_path / "state.json")
    state.upsert_user(usuario(EJEMPLO))
    assert list(state.users) == [EJEMPLO_MIN]


def test_cambiar_mayusculas_no_pierde_la_memoria(tmp_path):
    state = State(path=tmp_path / "state.json")
    primero = state.upsert_user(usuario(EJEMPLO_MIN))
    primero.baseline_sent = True
    primero.assets["1002:Buy"] = asset(1002, 1.0)

    segundo = state.upsert_user(usuario(EJEMPLO))
    assert segundo is primero
    assert segundo.baseline_sent is True
    assert list(state.users) == [EJEMPLO_MIN]


def test_roundtrip(tmp_path):
    ruta = tmp_path / "state.json"
    state = State(path=ruta)
    user_state = state.upsert_user(usuario(EJEMPLO))
    user_state.assets["1002:Buy"] = asset(1002, 11.417114)
    user_state.assets["6:Sell"] = asset(6, 0.354594, direction="Sell")
    user_state.baseline_sent = True
    state.save()

    releido = State.load(ruta)
    recuperado = releido.user(EJEMPLO_MIN)
    assert recuperado is not None
    assert recuperado.assets["1002:Buy"].units == 11.417114
    assert recuperado.assets["1002:Buy"].positions[1002].units == 11.417114
    assert recuperado.assets["6:Sell"].is_short is True
    assert recuperado.baseline_sent is True
    assert recuperado.needs_positions_baseline is False
    assert releido.version == STATE_VERSION


def test_un_estado_antiguo_no_se_compara_hasta_tener_posiciones(tmp_path):
    """El estado anterior al 3 guardaba unidades totales, sin posiciones.

    Comparar contra él daría avisos equivocados (una venta parcial parecería
    una venta total), así que se marca para volver a tomar la foto sin avisar.
    """
    ruta = tmp_path / "state.json"
    ruta.write_text(
        json.dumps(
            {
                "version": 2,
                "instruments": {},
                "users": {
                    EJEMPLO_MIN: {
                        "username": EJEMPLO,
                        "slug": EJEMPLO_MIN,
                        "gcid": 1111111,
                        "real_cid": 2222222,
                        "baseline_sent": True,
                        "assets": {
                            "1002:Buy": {
                                "instrument_id": 1002,
                                "direction": "Buy",
                                "units": 11.417114,
                                "invested_pct": 11.41,
                            }
                        },
                    }
                },
            }
        ),
        encoding="utf-8",
    )

    heredado = State.load(ruta).user(EJEMPLO_MIN)
    assert heredado is not None
    assert heredado.baseline_sent is True
    assert heredado.needs_positions_baseline is True
    # Las unidades del estado viejo no se pierden: quedan con un identificador
    # 0 (que eToro no usa) para que se vean al revisar el fichero.
    assert heredado.assets["1002:Buy"].units == 11.417114
    assert heredado.assets["1002:Buy"].positions[0].units == 11.417114


def test_no_hay_campos_que_crezcan_sin_limite(tmp_path):
    """El estado de un usuario debe tener solo lo imprescindible.

    Hubo un campo (`known_instruments`) que se acumulaba en cada ejecución y
    no se leía en ningún sitio: peso muerto que solo crecía. Este test evita
    que vuelva a colarse algo así.
    """
    ruta = tmp_path / "state.json"
    state = State(path=ruta)
    user_state = state.upsert_user(usuario(EJEMPLO))
    user_state.assets["1002:Buy"] = asset(1002, 1.5)
    state.save()

    datos = json.loads(ruta.read_text(encoding="utf-8"))
    campos = set(datos["users"][EJEMPLO_MIN])
    assert campos == {
        "username",
        "slug",
        "gcid",
        "real_cid",
        "demo_cid",
        "display_name",
        "allow_display_full_name",
        "avatar_url",
        "baseline_sent",
        "needs_positions_baseline",
        "assets",
    }, f"el estado ha ganado campos: {campos}"


def test_el_estado_solo_guarda_unidades_de_cada_posicion(tmp_path):
    """De cada posición solo el identificador y las unidades.

    Es lo mínimo para saber si una compra abre una posición nueva o amplía una
    que ya existía. Nada de precios de entrada, fechas ni ganancias.
    """
    ruta = tmp_path / "state.json"
    state = State(path=ruta)
    user_state = state.upsert_user(usuario(EJEMPLO))
    user_state.assets["1002:Buy"] = asset(1002, 1.5)
    state.save()

    datos = json.loads(ruta.read_text(encoding="utf-8"))
    activo = datos["users"][EJEMPLO_MIN]["assets"]["1002:Buy"]
    assert set(activo) == {
        "instrument_id",
        "direction",
        "units",
        "invested_pct",
        "positions",
    }
    assert activo["positions"] == [{"position_id": 1002, "units": 1.5}]
    texto = ruta.read_text(encoding="utf-8").lower()
    for prohibido in ("openrate", "opendatetime", "netprofit", "currentrate"):
        assert prohibido not in texto


def test_guardar_solo_si_cambia(tmp_path):
    state = State(path=tmp_path / "state.json")
    state.upsert_user(usuario(EJEMPLO))
    assert state.save() is True
    assert state.save() is False


def test_estado_inexistente(tmp_path):
    state = State.load(tmp_path / "no-existe.json")
    assert state.users == {}
    assert state.instruments == {}


def test_fichero_vacio_es_valido(tmp_path):
    ruta = tmp_path / "state.json"
    ruta.write_text("", encoding="utf-8")
    assert State.load(ruta).users == {}


# ---------------------------------------------------------------------- #
# Poda de usuarios que ya no se siguen
# ---------------------------------------------------------------------- #
def test_prune_borra_a_quien_ya_no_sigue(tmp_path):
    state = State(path=tmp_path / "state.json")
    state.upsert_user(usuario("uno_ejemplo"))
    state.upsert_user(usuario("dos_ejemplo"))
    borrados = state.prune_users(["uno_ejemplo"])
    assert borrados == ["dos_ejemplo"]
    assert list(state.users) == ["uno_ejemplo"]


def test_prune_no_distingue_mayusculas(tmp_path):
    state = State(path=tmp_path / "state.json")
    state.upsert_user(usuario("usuarioejemplo"))
    assert state.prune_users(["UsuarioEjemplo"]) == []
    assert list(state.users) == ["usuarioejemplo"]


def test_prune_con_lista_vacia_borra_todo(tmp_path):
    state = State(path=tmp_path / "state.json")
    state.upsert_user(usuario("uno_ejemplo"))
    assert state.prune_users([]) == ["uno_ejemplo"]
    assert state.users == {}


def test_prune_de_lo_que_no_existe_no_falla(tmp_path):
    state = State(path=tmp_path / "state.json")
    assert state.prune_users(["nadie_ejemplo"]) == []


# ---------------------------------------------------------------------- #
# Fichero dañado
# ---------------------------------------------------------------------- #
def test_json_dañado_lanza_error_explicito(tmp_path):
    ruta = tmp_path / "state.json"
    ruta.write_text("{ esto no es JSON valido", encoding="utf-8")
    with pytest.raises(StateCorruptError) as info:
        State.load(ruta)
    assert "dañado" in str(info.value).lower() or "está" in str(info.value)


def test_el_fichero_dañado_no_se_toca_al_fallar(tmp_path):
    ruta = tmp_path / "state.json"
    original = "{ esto no es JSON valido"
    ruta.write_text(original, encoding="utf-8")
    with pytest.raises(StateCorruptError):
        State.load(ruta)
    # Sigue intacto: se puede reparar a mano.
    assert ruta.read_text(encoding="utf-8") == original


def test_un_json_que_no_es_objeto_es_dañado(tmp_path):
    ruta = tmp_path / "state.json"
    ruta.write_text("[1, 2, 3]", encoding="utf-8")
    with pytest.raises(StateCorruptError):
        State.load(ruta)


# ---------------------------------------------------------------------- #
# Ficheros auxiliares
# ---------------------------------------------------------------------- #
def test_cooldown_junto_al_estado():
    assert cooldown_path("state/state.json") == Path("state/cooldown.json")


def test_runtime_state(tmp_path):
    runtime = RuntimeState(tmp_path / "rt.json")
    runtime.set("last_run_at", "2026-01-01T00:00:00+00:00")
    runtime.save()
    assert RuntimeState(tmp_path / "rt.json").get("last_run_at").startswith("2026")
