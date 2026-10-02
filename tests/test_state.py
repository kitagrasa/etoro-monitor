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

from etoro_monitor.models import Asset, EtoroUser  # noqa: E402
from etoro_monitor.state import (  # noqa: E402
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
    return Asset(instrument_id, direction, units, 1.0)


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
    user_state.known_instruments = {1002, 6}
    state.save()

    releido = State.load(ruta)
    recuperado = releido.user(EJEMPLO_MIN)
    assert recuperado is not None
    assert recuperado.assets["1002:Buy"].units == 11.417114
    assert recuperado.assets["6:Sell"].is_short is True
    assert recuperado.known_instruments == {1002, 6}


def test_el_estado_no_guarda_datos_de_posiciones(tmp_path):
    """Solo unidades, lado y peso: nada de IDs, precios ni fechas."""
    ruta = tmp_path / "state.json"
    state = State(path=ruta)
    user_state = state.upsert_user(usuario(EJEMPLO))
    user_state.assets["1002:Buy"] = asset(1002, 1.5)
    state.save()

    datos = json.loads(ruta.read_text(encoding="utf-8"))
    activo = datos["users"][EJEMPLO_MIN]["assets"]["1002:Buy"]
    assert set(activo) == {"instrument_id", "direction", "units", "invested_pct"}
    texto = ruta.read_text(encoding="utf-8").lower()
    for prohibido in ("positionid", "openrate", "opendatetime", "netprofit", "amount"):
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
