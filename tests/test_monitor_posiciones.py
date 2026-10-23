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
        {"InstrumentID": 1002, "Direction": "Buy", "Invested": 1.6},
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
    def __init__(self, body, *, status: int = 200, content_type: str = "application/json"):
        self.status_code = status
        self.text = body if isinstance(body, str) else json.dumps(body)
        self.headers = {"content-type": content_type}

    def json(self):
        return json.loads(self.text)


class FakeSession:
    """Devuelve la respuesta que toca según la ruta pedida.

    Por defecto usa la cartera y las posiciones de arriba; se pueden cambiar
    pasando `cartera` y/o `posiciones` (un diccionario activo -> lista).

    `cierres` es lo que contestará el histórico de cierres (lista de grupos tal
    cual la devuelve eToro) y `historico_falla` permite simular que no contesta.

    `secuencias` sirve para simular una lectura vieja de un activo:
    `{instrumento: [[posiciones de la primera lectura], [posiciones de la
    segunda], ...]}`. Cada llamada gasta una; la última se repite.
    """

    def __init__(
        self,
        posiciones=None,
        cartera=None,
        cierres=None,
        historico_falla=None,
        secuencias=None,
    ):
        self.headers = {}
        self.llamadas: list[str] = []
        self.historico_llamadas = 0
        self.historico_params: list[dict] = []
        # (instrumento, ¿se pidió sin caché?) por cada lectura de posiciones.
        self.llamadas_posiciones: list[tuple[int, bool]] = []
        self.cartera = CARTERA if cartera is None else cartera
        self.cierres = cierres if cierres is not None else []
        self.historico_falla = historico_falla
        self.secuencias = {k: [list(v) for v in lista] for k, lista in (secuencias or {}).items()}
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
        if "history/public" in url:
            self.historico_llamadas += 1
            self.historico_params.append(dict(params))
            if self.historico_falla == "caido":
                return FakeResponse({"error": "no"}, status=404)
            if self.historico_falla == "bloqueado":
                return FakeResponse("Just a moment...", content_type="text/html")
            return FakeResponse({"AggregatedInstrumentHistories": self.cierres})
        if "public/portfolios" in url:
            return FakeResponse(self.cartera)
        if "public/positions" in url:
            instrumento = int(params.get("instrumentId", 0))
            self.llamadas_posiciones.append((instrumento, "_" in params))
            if instrumento in self.secuencias:
                lecturas = self.secuencias[instrumento]
                return FakeResponse(
                    {"PublicPositions": lecturas.pop(0) if len(lecturas) > 1 else lecturas[0]}
                )
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

    cartera = [{"InstrumentID": 1002, "Direction": "Buy", "Invested": unidades_ahora}]
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
        "cartera": [{"InstrumentID": 1002, "Direction": "Buy", "Invested": 0.7}],
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


def test_un_activo_que_desaparece_se_avisa_con_su_nombre(tmp_path):
    """21/10/2026: se vendió Berkshire entera y el aviso decía "Instrumento 1118".

    Un activo que se vende entero ya no está en la foto, así que su nombre solo
    puede venir de la caché. Si falta (o es el relleno de un estado viejo), se
    pide al catálogo antes de redactar el aviso.
    """
    state = State(path=tmp_path / "state.json")
    avisos: list[str] = []
    _pasada_arbitro(state, avisos, UNA)  # línea base: cachea los cinco nombres
    avisos.clear()

    vendido = ACTIVOS_ARBITRO[-1]
    state.instruments.pop(vendido, None)  # simula el estado al que le faltaba

    cartera, posiciones = _cartera_de({iid: 1.0 for iid in ACTIVOS_ARBITRO[:-1]})
    session = FakeSession(cartera=cartera, posiciones=posiciones)
    client = EtoroClient(delay=0.0, session=session)
    reporte = Monitor(client, state, notify=avisos.append).run(["UsuarioEjemplo"])[0]
    client.close()

    assert [c.kind for c in reporte.changes] == ["closed"]
    assert "Activo" in avisos[0]
    assert "Instrumento" not in avisos[0], avisos[0]
    assert any(url.endswith(f"/{vendido}") for url in session.llamadas)
    assert state.instrument_label(vendido) == "Activo"


def test_si_el_nombre_del_que_desaparece_ya_esta_no_se_pide(tmp_path):
    """Y si ya estaba cacheado, no se gasta ninguna petición de más."""
    state = State(path=tmp_path / "state.json")
    avisos: list[str] = []
    _pasada_arbitro(state, avisos, UNA)
    avisos.clear()

    vendido = ACTIVOS_ARBITRO[-1]
    cartera, posiciones = _cartera_de({iid: 1.0 for iid in ACTIVOS_ARBITRO[:-1]})
    session = FakeSession(cartera=cartera, posiciones=posiciones)
    client = EtoroClient(delay=0.0, session=session)
    Monitor(client, state, notify=avisos.append).run(["UsuarioEjemplo"])
    client.close()

    assert not any(url.endswith(f"/{vendido}") for url in session.llamadas)
    assert "Instrumento" not in avisos[0]


# ---------------------------------------------------------------------- #
# Ajustes proporcionales de punta a punta
# ---------------------------------------------------------------------- #
def _cartera_con(factor: float = 1.0, *, compra_nueva: bool = False):
    """Seis instrumentos con una posición cada uno, todas al mismo factor."""
    cartera = []
    posiciones = {}
    for iid in (1002, 1003, 1004, 1005, 1006, 1007):
        cartera.append({"InstrumentID": iid, "Direction": "Buy", "Invested": factor})
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


def test_un_ajuste_del_0_05_solo_tampoco_se_avisa(tmp_path):
    """El recorte del 20/10/2026: 0,05% en todas las posiciones.

    Salían 10 "ventas parciales" de 0,01 puntos de peso. No hay nada que
    contar: es un ajuste, no una operación.
    """
    state = State(path=tmp_path / "state.json")
    avisos: list[str] = []
    _pasada(state, avisos, 1.0)  # línea base
    avisos.clear()

    reporte = _pasada(state, avisos, 0.9995)

    assert avisos == []
    assert reporte.adjustment is not None
    assert reporte.adjustment.positions == 6
    assert abs(reporte.adjustment.percentage - 0.05) < 1e-9


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


# ---------------------------------------------------------------------- #
# El histórico de cierres como árbitro
# ---------------------------------------------------------------------- #
# Cinco activos con una posición cada uno. Cada pasada les pone las unidades
# que diga `unidades`, así se puede simular un recorte DESIGUAL (el que el
# detector de ajustes no puede cazar) sin tocar el caso del ajuste uniforme.
ACTIVOS_ARBITRO = (1002, 1003, 1004, 1005, 1006)
UNA = {iid: 1.0 for iid in ACTIVOS_ARBITRO}
# Del 0,1% al 0,9%: cada activo una cosa distinta, así que no hay ajuste.
RECORTE_DESIGUAL = {1002: 0.999, 1003: 0.997, 1004: 0.995, 1005: 0.993, 1006: 0.991}


def _cartera_de(unidades):
    """Cartera con una posición por cada activo de `unidades`."""
    cartera = []
    posiciones = {}
    for iid, cantidad in unidades.items():
        cartera.append({"InstrumentID": iid, "Direction": "Buy", "Invested": cantidad})
        posiciones[iid] = [{"PositionID": iid * 10, "IsBuy": True, "Amount": cantidad}]
    return {"AggregatedPositions": cartera}, posiciones


def _cartera_arbitro(unidades):
    return _cartera_de({iid: unidades[iid] for iid in ACTIVOS_ARBITRO})


def _pasada_arbitro(state, avisos, unidades, *, cierres=None, falla=None):
    cartera, posiciones = _cartera_arbitro(unidades)
    session = FakeSession(
        cartera=cartera,
        posiciones=posiciones,
        cierres=cierres,
        historico_falla=falla,
    )
    client = EtoroClient(delay=0.0, session=session)
    reporte = Monitor(client, state, notify=avisos.append).run(["UsuarioEjemplo"])[0]
    client.close()
    return reporte, session


def test_un_recorte_desigual_lo_resuelve_el_historico(tmp_path):
    """Lo que el detector de ajustes no puede cazar lo caza el histórico.

    Cinco recortes de tamaños distintos (0,1% ... 0,9%): la heurística no puede
    llamarlo ajuste, pero el histórico dice que no ha cerrado nada, así que no
    son ventas.
    """
    state = State(path=tmp_path / "state.json")
    avisos: list[str] = []
    _pasada_arbitro(state, avisos, UNA)  # línea base
    avisos.clear()

    reporte, session = _pasada_arbitro(state, avisos, RECORTE_DESIGUAL)

    assert session.historico_llamadas == 1, "el histórico se pide una sola vez"
    assert reporte.closes == {}
    assert reporte.changes == []
    assert avisos == []
    assert reporte.adjustment is None


def test_si_el_historico_no_contesta_se_decide_como_antes(tmp_path):
    """El histórico no puede tumbar una pasada: se avisa como hasta ahora."""
    state = State(path=tmp_path / "state.json")
    avisos: list[str] = []
    _pasada_arbitro(state, avisos, UNA)
    avisos.clear()

    reporte, session = _pasada_arbitro(state, avisos, RECORTE_DESIGUAL, falla="caido")

    assert session.historico_llamadas == 1
    assert reporte.closes is None
    assert len(reporte.changes) == 5
    assert len(avisos) == 1
    assert "VENTA PARCIAL" in avisos[0]


def test_si_el_historico_esta_bloqueado_tampoco_rompe_la_pasada(tmp_path):
    state = State(path=tmp_path / "state.json")
    avisos: list[str] = []
    _pasada_arbitro(state, avisos, UNA)
    avisos.clear()

    reporte, session = _pasada_arbitro(
        state, avisos, RECORTE_DESIGUAL, falla="bloqueado"
    )

    assert session.historico_llamadas == 1
    assert reporte.closes is None
    assert len(avisos) == 1


def test_un_cierre_en_el_historico_respalda_la_bajada(tmp_path):
    """Si el histórico dice que ese activo ha cerrado algo, la bajada es venta."""
    state = State(path=tmp_path / "state.json")
    avisos: list[str] = []
    _pasada_arbitro(state, avisos, UNA)
    avisos.clear()

    reporte, session = _pasada_arbitro(
        state,
        avisos,
        RECORTE_DESIGUAL,
        cierres=[{"InstrumentID": 1004, "TotalNumberOfPositions": 1}],
    )

    assert reporte.closes == {1004: 1}
    assert [c.instrument_id for c in reporte.changes] == [1004]
    assert len(avisos) == 1


def test_una_pasada_de_compras_no_descarta_nada(tmp_path):
    """El histórico se pide siempre, pero en una compra no toca nada."""
    state = State(path=tmp_path / "state.json")
    avisos: list[str] = []
    _pasada_arbitro(state, avisos, UNA)
    avisos.clear()

    reporte, session = _pasada_arbitro(
        state, avisos, {**UNA, 1002: 1.5}, cierres=[]
    )

    assert session.historico_llamadas == 1
    assert reporte.closes == {}
    assert len(reporte.changes) == 1
    assert "COMPRA PARCIAL" in avisos[0]


def test_la_marca_de_agua_se_guarda_y_es_la_que_se_usa(tmp_path):
    """El histórico se pide "desde la foto anterior", no desde el principio."""
    state = State(path=tmp_path / "state.json")
    avisos: list[str] = []
    _pasada_arbitro(state, avisos, UNA)  # línea base

    primero = state.user("usuarioejemplo").history_since
    assert primero is not None

    avisos.clear()
    _, session = _pasada_arbitro(state, avisos, RECORTE_DESIGUAL)

    assert session.historico_params[0]["startTime"] == primero
    segundo = state.user("usuarioejemplo").history_since
    assert segundo is not None and segundo >= primero


def test_un_estado_sin_marca_de_agua_no_se_calla_nada(tmp_path):
    """Estado de antes de este cambio: hay posiciones pero no marca de agua."""
    state = State(path=tmp_path / "state.json")
    avisos: list[str] = []
    _pasada_arbitro(state, avisos, UNA)
    state.user("usuarioejemplo").history_since = None  # como un estado viejo
    avisos.clear()

    reporte, session = _pasada_arbitro(state, avisos, RECORTE_DESIGUAL)

    assert session.historico_llamadas == 0
    assert len(reporte.changes) == 5
    assert len(avisos) == 1
    # Y se aprovecha para dejar puesta la marca de agua.
    assert state.user("usuarioejemplo").history_since is not None


# ---------------------------------------------------------------------- #
# Coherencia cartera <-> posiciones: la segunda lectura
# ---------------------------------------------------------------------- #
def _pasada_con(state, avisos, cartera, posiciones, *, secuencias=None):
    session = FakeSession(cartera=cartera, posiciones=posiciones, secuencias=secuencias)
    client = EtoroClient(delay=0.0, session=session)
    reporte = Monitor(client, state, notify=avisos.append).run(["UsuarioEjemplo"])[0]
    client.close()
    return reporte, session


def test_si_las_posiciones_no_cuadran_con_la_cartera_se_relee(tmp_path):
    """El fallo del 22/10: la lectura de un activo se quedó atrás.

    La cartera ya dice que ese activo tiene 2 unidades, pero la primera lectura
    de posiciones devuelve 1 (vieja). Se relee con anti-caché, cuadra, y la
    compra se avisa en la misma pasada.
    """
    state = State(path=tmp_path / "state.json")
    avisos: list[str] = []
    _pasada_arbitro(state, avisos, UNA)  # línea base: 1 unidad por activo
    avisos.clear()

    cartera, posiciones = _cartera_de({**UNA, 1002: 2.0})
    vieja = [{"PositionID": 10020, "IsBuy": True, "Amount": 1.0}]
    nueva = [{"PositionID": 10020, "IsBuy": True, "Amount": 2.0}]

    reporte, session = _pasada_con(
        state, avisos, cartera, posiciones, secuencias={1002: [vieja, nueva]}
    )

    assert [c.instrument_id for c in reporte.changes] == [1002]
    assert reporte.changes[0].after.units == 2.0
    assert "COMPRA PARCIAL" in avisos[0]

    lecturas = [x for x in session.llamadas_posiciones if x[0] == 1002]
    assert lecturas == [(1002, False), (1002, True)], "la segunda va sin caché"


def test_si_cuadra_no_se_gasta_ninguna_lectura_de_mas(tmp_path):
    state = State(path=tmp_path / "state.json")
    avisos: list[str] = []
    _pasada_arbitro(state, avisos, UNA)
    avisos.clear()

    cartera, posiciones = _cartera_de({**UNA, 1002: 2.0})
    reporte, session = _pasada_con(state, avisos, cartera, posiciones)

    assert [c.instrument_id for c in reporte.changes] == [1002]
    assert [x for x in session.llamadas_posiciones if x[0] == 1002] == [(1002, False)]


def test_si_tras_releer_sigue_sin_cuadrar_se_usa_la_lectura_nueva(tmp_path):
    """No se bloquea el activo: se usa la más reciente y se sigue."""
    state = State(path=tmp_path / "state.json")
    avisos: list[str] = []
    _pasada_arbitro(state, avisos, UNA)
    avisos.clear()

    cartera, posiciones = _cartera_de({**UNA, 1002: 2.0})
    primera = [{"PositionID": 10020, "IsBuy": True, "Amount": 1.0}]
    segunda = [{"PositionID": 10020, "IsBuy": True, "Amount": 1.5}]

    reporte, session = _pasada_con(
        state, avisos, cartera, posiciones, secuencias={1002: [primera, segunda]}
    )

    assert [c.instrument_id for c in reporte.changes] == [1002]
    assert reporte.changes[0].after.units == 1.5
    assert len([x for x in session.llamadas_posiciones if x[0] == 1002]) == 2


def test_en_los_cortos_no_se_comprueba_la_coherencia(tmp_path):
    """En un corto no está comprobado que Invested sea la suma de unidades."""
    state = State(path=tmp_path / "state.json")
    avisos: list[str] = []
    cartera = {
        "AggregatedPositions": [
            {"InstrumentID": 6, "Direction": "Sell", "Invested": 0.71}
        ]
    }
    posiciones = {6: [{"PositionID": 60, "IsBuy": False, "Amount": 0.354594}]}

    _, session = _pasada_con(state, avisos, cartera, posiciones)

    assert session.llamadas_posiciones == [(6, False)], "no debe releer en cortos"

