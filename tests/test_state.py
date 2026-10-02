"""Tests del estado persistente (claves normalizadas a minúsculas).

Todos los datos de estos tests son ficticios: no se usan nombres de usuario,
identificadores ni carteras de personas reales.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from etoro_monitor.models import EtoroUser, Position  # noqa: E402
from etoro_monitor.state import RuntimeState, State  # noqa: E402

EJEMPLO = "UsuarioEjemplo"
EJEMPLO_MIN = "usuarioejemplo"


def usuario(nombre: str) -> EtoroUser:
    return EtoroUser(username=nombre, gcid=1111111, real_cid=2222222)


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
    primero.positions[1] = Position(1, 1002, True, 1.0, 100.0)

    # El usuario edita watchlist.md y escribe "UsuarioEjemplo"
    segundo = state.upsert_user(usuario(EJEMPLO))
    assert segundo is primero
    assert segundo.baseline_sent is True
    assert len(segundo.positions) == 1
    assert list(state.users) == [EJEMPLO_MIN]


def test_migra_un_estado_antiguo_con_mayusculas(tmp_path):
    ruta = tmp_path / "state.json"
    ruta.write_text(
        json.dumps(
            {
                "version": 1,
                "instruments": {},
                "users": {
                    EJEMPLO: {
                        "username": EJEMPLO,
                        "gcid": 1111111,
                        "real_cid": 2222222,
                        "baseline_sent": True,
                        "positions": {
                            "1": {
                                "position_id": 1,
                                "instrument_id": 1002,
                                "is_buy": True,
                                "amount": 1.0,
                                "open_rate": 100.0,
                            }
                        },
                    }
                },
            }
        ),
        encoding="utf-8",
    )
    state = State.load(ruta)
    assert state.user(EJEMPLO.upper()) is not None
    assert state.user(EJEMPLO.upper()).baseline_sent is True

    actualizado = state.upsert_user(usuario(EJEMPLO))
    assert actualizado.baseline_sent is True
    assert list(state.users) == [EJEMPLO_MIN]


def test_guardar_solo_si_cambia(tmp_path):
    state = State(path=tmp_path / "state.json")
    state.upsert_user(usuario(EJEMPLO))
    assert state.save() is True
    # Guardar dos veces seguidas no debe tocar el fichero (nada de commits
    # innecesarios en el workflow).
    assert state.save() is False
    state.users[EJEMPLO_MIN].baseline_sent = True
    assert state.save() is True


def test_roundtrip(tmp_path):
    ruta = tmp_path / "state.json"
    state = State(path=ruta)
    user_state = state.upsert_user(usuario(EJEMPLO))
    user_state.positions[42] = Position(42, 1002, True, 0.5, 176.28, "2025-07-08T14:02:14Z")
    user_state.known_instruments = {1002, 1004}
    state.save()

    releido = State.load(ruta)
    recuperado = releido.user(EJEMPLO_MIN)
    assert recuperado is not None
    assert recuperado.positions[42].amount == 0.5
    assert recuperado.positions[42].open_rate == 176.28
    assert recuperado.known_instruments == {1002, 1004}


def test_estado_inexistente(tmp_path):
    state = State.load(tmp_path / "no-existe.json")
    assert state.users == {}
    assert state.instruments == {}


def test_runtime_state(tmp_path):
    runtime = RuntimeState(tmp_path / "rt.json")
    runtime.set("last_run_at", "2026-01-01T00:00:00+00:00")
    runtime.save()
    assert RuntimeState(tmp_path / "rt.json").get("last_run_at").startswith("2026")
