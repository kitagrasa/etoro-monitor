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
from etoro_monitor.models import Instrument  # noqa: E402
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


# ---------------------------------------------------------------------- #
# Nombre del activo: no quedarse con "Instrumento 8804"
# ---------------------------------------------------------------------- #
class ClienteDeMetadatos:
    """Solo responde a get_instrument, que es lo único que usa este test."""

    def __init__(self, respuesta: dict | None):
        self.respuesta = respuesta
        self.llamadas = 0

    def get_instrument(self, instrument_id):
        self.llamadas += 1
        return self.respuesta


def test_sin_nombre_no_se_cachea_el_relleno(tmp_path):
    """Si el catálogo no da nombre, no se guarda "Instrumento 8804" para siempre."""
    state = State(path=tmp_path / "state.json")
    monitor = Monitor(ClienteDeMetadatos({}), state, notify=lambda _t: None)

    monitor._ensure_instrument(8804)
    assert 8804 not in state.instruments, "se cacheó un nombre de relleno"

    # La pasada siguiente ya trae el nombre: se guarda bien.
    monitor = Monitor(
        ClienteDeMetadatos(
            {"InstrumentDisplayName": "Sandisk Corp/DE", "SymbolFull": "SNDK"}
        ),
        state,
        notify=lambda _t: None,
    )
    monitor._ensure_instrument(8804)
    assert state.instruments[8804].label == "Sandisk Corp/DE (SNDK)"


def test_un_nombre_de_relleno_ya_cacheado_se_reintenta(tmp_path):
    """Regresión: un estado viejo pudo guardar "Instrumento 8804" como nombre."""
    state = State(path=tmp_path / "state.json")
    state.instruments[8804] = Instrument(8804, "Instrumento 8804", "")

    monitor = Monitor(
        ClienteDeMetadatos(
            {"InstrumentDisplayName": "Sandisk Corp/DE", "SymbolFull": "SNDK"}
        ),
        state,
        notify=lambda _t: None,
    )
    monitor._ensure_instrument(8804)
    assert state.instruments[8804].label == "Sandisk Corp/DE (SNDK)"


# ---------------------------------------------------------------------- #
# Ajustes proporcionales de punta a punta
# ---------------------------------------------------------------------- #
def _cartera_con(factor: float = 1.0, *, compra_nueva: bool = False):
    """Seis instrumentos con una posición cada uno, todas al mismo factor."""
    cartera = []
    posiciones = {}
    for iid in (1002, 1003, 1004, 1005, 1006, 1007):
        cartera.append({"InstrumentID": iid, "Direction": "Buy", "Invested": 5.0})
        posiciones[iid] = [{"PositionID": iid * 10, "IsBuy": True, "Amount": factor}]
    if compra_nueva:
        posiciones[1002].append({"PositionID": 9999, "IsBuy": True, "Amount": 0.5})
    return {"AggregatedPositions": cartera}, posiciones


def _pasada(
    state: State, avisos: list[str], factor: float, *, compra_nueva: bool = False
):
    cartera, posiciones = _cartera_con(factor, compra_nueva=compra_nueva)
    client = EtoroClient(
        delay=0.0,
        session=FakeSession(cartera=cartera, posiciones=posiciones),
    )
    reportes = Monitor(client, state, notify=avisos.append).run(["UsuarioEjemplo"])
    client.close()
    return reportes[0]


def test_un_ajuste_proporcional_solo_no_se_avisa(tmp_path):
    """Solo un ajuste del 0,30%: no hay nada que contar, no se manda mensaje."""
    state = State(path=tmp_path / "state.json")
    avisos: list[str] = []
    _pasada(state, avisos, 1.0)  # línea base
    avisos.clear()

    reporte = _pasada(state, avisos, 0.997)

    assert avisos == []
    assert reporte.adjustment is not None
    assert reporte.adjustment.positions == 6
    assert abs(reporte.adjustment.percentage - 0.3) < 1e-9


def test_un_ajuste_con_una_compra_real_avisa_solo_la_compra(tmp_path):
    """El ajuste no se menciona; la compra real se avisa."""
    state = State(path=tmp_path / "state.json")
    avisos: list[str] = []
    _pasada(state, avisos, 1.0)  # línea base
    avisos.clear()

    reporte = _pasada(state, avisos, 0.997, compra_nueva=True)

    assert len(avisos) == 1
    assert "COMPRA PARCIAL" in avisos[0]
    assert "Reducción proporcional" not in avisos[0]
    assert len(reporte.changes) == 1


