"""Tests del cliente HTTP: reintentos, límites de eToro y bloqueos.

Esta lógica es la que decide si el monitor espera o se rinde, así que conviene
tenerla cubierta: un cambio inocente aquí puede dejar el workflow colgado 40
minutos o, al contrario, rendirse ante un simple fallo temporal.

Todo se prueba con un `session` falso: **ningún test toca la red**.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from etoro_monitor.client import (  # noqa: E402
    Blocked,
    EtoroClient,
    EtoroError,
    PrivatePortfolio,
    UserNotFound,
)
from etoro_monitor.cooldown import CooldownStore  # noqa: E402
from etoro_monitor.monitor import Monitor  # noqa: E402
from etoro_monitor.state import State  # noqa: E402


# ---------------------------------------------------------------------- #
# Dobles de prueba
# ---------------------------------------------------------------------- #
class FakeResponse:
    def __init__(self, status, *, body="{}", content_type="application/json", headers=None):
        self.status_code = status
        self.text = body
        self.headers = {"content-type": content_type}
        self.headers.update(headers or {})

    def json(self):
        return json.loads(self.text)


class FakeSession:
    """Devuelve respuestas en orden y recuerda cuántas peticiones se hicieron."""

    def __init__(self, respuestas):
        self.headers = {}
        self._respuestas = list(respuestas)
        self.llamadas = []

    def get(self, url, params=None, timeout=None):
        self.llamadas.append(url)
        if self._respuestas:
            return self._respuestas.pop(0)
        raise AssertionError("se pidieron más respuestas de las previstas")

    def close(self):
        pass


@pytest.fixture(autouse=True)
def sin_esperas(monkeypatch):
    """Anota las esperas en vez de dormirlas de verdad."""
    esperas: list[float] = []
    monkeypatch.setattr("etoro_monitor.client.time.sleep", lambda s: esperas.append(s))
    return esperas


def cliente(respuestas, *, max_retries=3):
    return EtoroClient(
        delay=0.0, max_retries=max_retries, session=FakeSession(respuestas)
    )


def OK(body='{"InstrumentDisplayDatas": []}'):
    return FakeResponse(200, body=body)


# ---------------------------------------------------------------------- #
# 429: eToro pide esperar
# ---------------------------------------------------------------------- #
def test_429_con_espera_larga_se_rinde_ya():
    """El caso que vimos de verdad: retry-after de 2500 s (~42 min).

    Esperar sería peor que rendirse: el workflow tiene 15 minutos de tope, así
    que GitHub mataría el job a mitad de espera. Debe fallar en el acto.
    """
    sesion = FakeSession([FakeResponse(429, headers={"retry-after": "2504"})])
    client = EtoroClient(delay=0.0, max_retries=3, session=sesion)

    with pytest.raises(Blocked) as info:
        client.get_portfolio(123)

    # Un solo intento: no se queda reintentando
    assert len(sesion.llamadas) == 1
    # Y el mensaje explica qué pasa y qué hacer
    texto = str(info.value)
    assert "2504" in texto or "42" in texto
    assert "cron" in texto.lower()


def test_429_con_espera_corta_si_reintenta():
    client = cliente(
        [
            FakeResponse(429, headers={"retry-after": "3"}),
            OK(),
        ]
    )
    assert client.get_portfolio(123) == {"InstrumentDisplayDatas": []}
    assert client.request_count == 2


def test_429_sin_cabecera_usa_espera_creciente():
    client = cliente([FakeResponse(429), FakeResponse(429), OK()])
    client.get_portfolio(123)
    assert client.request_count == 3


def test_429_repetido_termina_en_bloqueo():
    client = cliente([FakeResponse(429, headers={"retry-after": "1"})] * 3, max_retries=2)
    with pytest.raises(Blocked):
        client.get_portfolio(123)


def test_la_espera_larga_no_llega_a_dormirse(sin_esperas):
    """Lo importante: no se duerme nada cuando la espera pedida es enorme."""
    client = cliente([FakeResponse(429, headers={"retry-after": "2504"})])
    with pytest.raises(Blocked):
        client.get_portfolio(123)
    assert sin_esperas == []


# ---------------------------------------------------------------------- #
# Otros códigos
# ---------------------------------------------------------------------- #
def test_5xx_reintenta():
    client = cliente([FakeResponse(503), FakeResponse(502), OK()])
    client.get_portfolio(123)
    assert client.request_count == 3


def test_5xx_agotado_lanza_error():
    client = cliente([FakeResponse(500)] * 2, max_retries=1)
    with pytest.raises(EtoroError):
        client.get_portfolio(123)


def test_403_captcha_es_bloqueo():
    client = cliente(
        [
            FakeResponse(
                403,
                body='{"url": "https://geo.captcha-delivery.com/captcha/?x=1"}',
            )
        ]
    )
    with pytest.raises(Blocked):
        client.get_portfolio(123)


def test_403_cartera_privada():
    client = cliente([FakeResponse(403, body='{"Message": "user is PRIVATE"}')])
    with pytest.raises(PrivatePortfolio):
        client.get_portfolio(123)


def test_403_privada_tolerada_en_las_posiciones():
    """En el endpoint de posiciones una cartera privada no corta la ejecución."""
    client = cliente([FakeResponse(403, body='{"Message": "user is PRIVATE"}')])
    assert client.get_positions(123, 1002) == {}


def test_404_usuario_inexistente():
    client = cliente([FakeResponse(404, body='{"ErrorCode": "NotFound"}')])
    with pytest.raises(UserNotFound):
        client.resolve_username("zzz_no_existe")


def test_404_tolerado_en_metadatos_devuelve_none():
    """Si un activo no tiene metadatos, el monitor sigue sin cortarse."""
    client = cliente([FakeResponse(404, body="{}")])
    assert client.get_instrument(999999) is None


def test_resolve_username_siempre_lanza_si_no_existe():
    """Aquí no se tolera: sin CID no hay nada que vigilar."""
    client = cliente([FakeResponse(404, body="{}")])
    with pytest.raises(UserNotFound):
        client.resolve_username("zzz_no_existe")


def test_respuesta_html_no_es_json():
    """La web devuelve el shell HTML cuando la ruta no existe."""
    client = cliente([FakeResponse(200, body="<!DOCTYPE html><html>", content_type="text/html")])
    with pytest.raises(Blocked):
        client.get_portfolio(123)


# ---------------------------------------------------------------------- #
# Un bloqueo es del sitio, no de una persona
# ---------------------------------------------------------------------- #
def test_un_bloqueo_no_se_convierte_en_un_reporte_por_usuario(tmp_path, sin_esperas):
    """Con 5 personas seguidas, un bloqueo no debe generar 5 avisos iguales."""
    sesion = FakeSession([FakeResponse(429, headers={"retry-after": "2504"})])
    client = EtoroClient(delay=0.0, max_retries=3, session=sesion)
    state = State(path=tmp_path / "state.json")
    avisos: list[str] = []
    monitor = Monitor(client, state, notify=avisos.append)

    with pytest.raises(Blocked):
        monitor.run(["uno_ejemplo", "dos_ejemplo", "tres_ejemplo"])

    # Sube entero: no devuelve un informe por usuario
    assert avisos == []
    # Y solo se intentó una vez, sin seguir pidiendo carteras
    assert len(sesion.llamadas) == 1


def test_un_error_de_usuario_si_se_avisa_por_usuario(tmp_path, sin_esperas):
    """Un 404 sí es de esa persona: se informa y se sigue con las demás."""
    sesion = FakeSession([FakeResponse(404, body="{}"), FakeResponse(404, body="{}")])
    client = EtoroClient(delay=0.0, max_retries=0, session=sesion)
    state = State(path=tmp_path / "state.json")
    avisos: list[str] = []
    monitor = Monitor(
        client,
        state,
        notify=avisos.append,
        cooldown=CooldownStore.load(tmp_path / "cooldown.json"),
    )

    reports = monitor.run(["uno_ejemplo", "dos_ejemplo"])

    assert len(reports) == 2
    assert all(r.error for r in reports)
    # Dos avisos: son dos personas distintas, con problemas distintos
    assert len(avisos) == 2
