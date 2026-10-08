"""Tests de la lectura de la cartera: qué se guarda de cada posición.

Aquí está la pieza que hace posible distinguir una compra de una compra
parcial: de cada posición que publica eToro se guarda su `PositionID` y sus
unidades. El orden de las claves no importa; el contenido sí.

Todo se prueba con un `session` falso: **ningún test toca la red**.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from etoro_monitor.client import EtoroClient  # noqa: E402
from etoro_monitor.monitor import Monitor  # noqa: E402
from etoro_monitor.state import State  # noqa: E402

USUARIO = {
    "username": "UsuarioEjemplo",
    "gcid": 1111111,
    "realCid": 2222222,
    "firstName": "Usuario",
}

CARTERA = {
    "AggregatedPositions": [
        {"InstrumentID": 1002, "Direction": "Buy", "Invested": 11.41},
        {"InstrumentID": 6, "Direction": "Sell", "Invested": 0.71},
    ]
}

# Dos operaciones vivas del mismo activo: el caso que antes se veía como un
# solo montón de unidades.
POSICIONES_1002 = {
    "PublicPositions": [
        {"PositionID": 701039100, "IsBuy": True, "Amount": 1.0},
        {"PositionID": 701039999, "IsBuy": True, "Amount": 0.6},
    ]
}

POSICIONES_6 = {
    "PublicPositions": [
        {"PositionID": 705000000, "IsBuy": False, "Amount": 0.354594},
        # Otra posición del mismo activo pero en largo: no debe contarse aquí.
        {"PositionID": 705000001, "IsBuy": True, "Amount": 9.0},
    ]
}


class FakeResponse:
    def __init__(self, body):
        self.status_code = 200
        self.text = json.dumps(body)
        self.headers = {"content-type": "application/json"}

    def json(self):
        return json.loads(self.text)


class FakeSession:
    """Devuelve la respuesta que toca según la ruta pedida.

    Por defecto usa la cartera y las posiciones de arriba; se pueden cambiar
    pasando `cartera` y/o `posiciones` (un diccionario activo -> lista).
    """

    def __init__(self, posiciones=None, cartera=None):
        self.headers = {}
        self.llamadas: list[str] = []
        self.cartera = CARTERA if cartera is None else cartera
        self.posiciones = (
            {1002: POSICIONES_1002["PublicPositions"], 6: POSICIONES_6["PublicPositions"]}
            if posiciones is None
            else posiciones
        )

    def get(self, url, params=None, timeout=None):
        self.llamadas.append(url)
        params = params or {}
        if "logininfo" in url:
            return FakeResponse(USUARIO)
        if "public/portfolios" in url:
            return FakeResponse(self.cartera)
        if "public/positions" in url:
            instrumento = int(params.get("instrumentId", 0))
            return FakeResponse(
                {"PublicPositions": self.posiciones.get(instrumento, [])}
            )
        if "instrumentsmetadata" in url:
            return FakeResponse(
                {"InstrumentDisplayData": {"InstrumentDisplayName": "Activo"}}
            )
        raise AssertionError(f"petición inesperada: {url}")

    def close(self):
        pass


def test_cada_posicion_se_guarda_con_su_identificador(tmp_path):
    client = EtoroClient(delay=0.0, session=FakeSession())
    monitor = Monitor(client, State(path=tmp_path / "state.json"), notify=lambda _t: None)

    usuario = monitor._resolve("UsuarioEjemplo")
    snapshot = monitor.fetch_snapshot(usuario)

    largo = snapshot.assets["1002:Buy"]
    assert set(largo.positions) == {701039100, 701039999}
    assert largo.positions[701039100].units == 1.0
    assert largo.positions[701039999].units == 0.6
    assert largo.units == 1.6  # el total es la suma, como antes

    # El corto solo cuenta las posiciones de su lado.
    corto = snapshot.assets["6:Sell"]
    assert set(corto.positions) == {705000000}
    assert corto.units == 0.354594
    assert corto.is_short is True


def test_dos_operaciones_iguales_del_mismo_activo_no_se_pisan(tmp_path):
    """Dos compras del mismo importe son dos posiciones, no una."""
    dos_iguales = [
        {"PositionID": 701039100, "IsBuy": True, "Amount": 1.0},
        {"PositionID": 701039999, "IsBuy": True, "Amount": 1.0},
    ]
    client = EtoroClient(delay=0.0, session=FakeSession(posiciones={1002: dos_iguales}))
    monitor = Monitor(client, State(path=tmp_path / "state.json"), notify=lambda _t: None)

    activo = monitor.fetch_snapshot(monitor._resolve("UsuarioEjemplo")).assets["1002:Buy"]
    assert set(activo.positions) == {701039100, 701039999}
    assert activo.units == 2.0


# ---------------------------------------------------------------------- #
# Migración desde el estado antiguo
# ---------------------------------------------------------------------- #
def estado_antiguo(tmp_path, *, unidades_previas: float, unidades_ahora: float):
    """Un estado del formato viejo (solo unidades) y una cartera de ahora.

    El corte de `unidades_previas` a `unidades_ahora` es el caso peligroso:
    con solo mirar el total parece una venta, aunque en realidad se haya
    cerrado una operación y abierto otra.
    """
    ruta = tmp_path / "state.json"
    ruta.write_text(
        json.dumps(
            {
                "version": 2,
                "instruments": {},
                "users": {
                    "usuarioejemplo": {
                        "username": "UsuarioEjemplo",
                        "slug": "usuarioejemplo",
                        "gcid": 1111111,
                        "real_cid": 2222222,
                        "baseline_sent": True,
                        "assets": {
                            "1002:Buy": {
                                "instrument_id": 1002,
                                "direction": "Buy",
                                "units": unidades_previas,
                                "invested_pct": 10.0,
                            }
                        },
                    }
                },
            }
        ),
        encoding="utf-8",
    )

    cartera = [{"InstrumentID": 1002, "Direction": "Buy", "Invested": 5.0}]
    posiciones = {
        1002: [{"PositionID": 701039100, "IsBuy": True, "Amount": unidades_ahora}]
    }
    avisos: list[str] = []
    state = State.load(ruta)
    client = EtoroClient(
        delay=0.0,
        session=FakeSession(
            cartera={"AggregatedPositions": cartera}, posiciones=posiciones
        ),
    )
    monitor = Monitor(client, state, notify=avisos.append)
    monitor.run(["UsuarioEjemplo"])
    client.close()
    return state, avisos


def test_un_estado_antiguo_no_dispara_avisos_equivocados(tmp_path):
    """Con el estado viejo, una venta parcial parecería una venta total.

    Por eso la primera pasada solo vuelve a tomar la foto: no se avisa de
    nada, y a partir de la siguiente ya se compara posición a posición.
    """
    state, avisos = estado_antiguo(tmp_path, unidades_previas=1.0, unidades_ahora=0.4)

    assert avisos == [], "no debe avisar con datos que no puede comparar"
    assert state.user("usuarioejemplo").needs_positions_baseline is False
    assert set(state.user("usuarioejemplo").assets["1002:Buy"].positions) == {701039100}


def test_tras_migrar_el_estado_ya_compara_posiciones(tmp_path):
    """Y la foto nueva sirve: el cambio siguiente sí se avisa."""
    state, _ = estado_antiguo(tmp_path, unidades_previas=1.0, unidades_ahora=0.4)

    escenario = {
        "cartera": [{"InstrumentID": 1002, "Direction": "Buy", "Invested": 7.0}],
        "posiciones": {
            1002: [
                {"PositionID": 701039100, "IsBuy": True, "Amount": 0.4},
                {"PositionID": 701039999, "IsBuy": True, "Amount": 0.3},
            ]
        },
    }
    avisos: list[str] = []
    client = EtoroClient(
        delay=0.0,
        session=FakeSession(
            cartera={"AggregatedPositions": escenario["cartera"]},
            posiciones=escenario["posiciones"],
        ),
    )
    monitor = Monitor(client, state, notify=avisos.append)
    monitor.run(["UsuarioEjemplo"])
    client.close()

    assert len(avisos) == 1
    assert "COMPRA PARCIAL" in avisos[0]

