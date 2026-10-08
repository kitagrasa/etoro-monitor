"""Tests de la ventana horaria (hora de Madrid).

El punto clave que se comprueba aquí: el cron de GitHub es UTC y Madrid
cambia de UTC+1 a UTC+2, así que la misma hora UTC NO vale para las dos
estaciones. Estos tests fijan el comportamiento en invierno y en verano.
"""

from __future__ import annotations

import sys
from datetime import datetime, time, timezone
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from etoro_monitor.schedule import (  # noqa: E402
    ScheduleWindow,
    parse_days,
    parse_time,
)

# La franja pedida: 08:30–23:00 de Madrid, de lunes a viernes.
VENTANA = ScheduleWindow()


def utc(texto: str) -> datetime:
    return datetime.fromisoformat(texto).replace(tzinfo=timezone.utc)


# ---------------------------------------------------------------------- #
# Parseo
# ---------------------------------------------------------------------- #
def test_parse_time():
    assert parse_time("08:30") == time(8, 30)
    assert parse_time("23:00") == time(23, 0)
    assert parse_time("00:00") == time(0, 0)
    with pytest.raises(ValueError):
        parse_time("8:30 de la tarde")


def test_parse_days():
    assert parse_days([1, 2, 3, 4, 5]) == frozenset({1, 2, 3, 4, 5})
    assert parse_days(None) == frozenset({1, 2, 3, 4, 5})
    assert parse_days([7]) == frozenset({7})
    with pytest.raises(ValueError):
        parse_days([0])
    with pytest.raises(ValueError):
        parse_days([8])


# ---------------------------------------------------------------------- #
# Dentro y fuera de la franja
# ---------------------------------------------------------------------- #
@pytest.mark.parametrize(
    "momento, esperado",
    [
        # Miércoles 7 de octubre de 2026 (Madrid = UTC+2, verano)
        ("2026-10-07T06:29:00", False),  # 08:29 Madrid -> aún no
        ("2026-10-07T06:30:00", True),   # 08:30 Madrid -> abre
        ("2026-10-07T12:00:00", True),   # 14:00 Madrid
        ("2026-10-07T21:00:00", True),   # 23:00 Madrid -> último turno
        ("2026-10-07T21:30:00", False),  # 23:30 Madrid -> cerrado
        ("2026-10-07T03:00:00", False),  # 05:00 Madrid -> madrugada
    ],
)
def test_verano_madrid_utc_mas_2(momento, esperado):
    assert VENTANA.is_open(utc(momento)) is esperado


@pytest.mark.parametrize(
    "momento, esperado",
    [
        # Miércoles 13 de enero de 2027 (Madrid = UTC+1, invierno)
        ("2027-01-13T07:29:00", False),  # 08:29 Madrid
        ("2027-01-13T07:30:00", True),   # 08:30 Madrid
        ("2027-01-13T22:00:00", True),   # 23:00 Madrid
        ("2027-01-13T22:30:00", False),  # 23:30 Madrid
    ],
)
def test_invierno_madrid_utc_mas_1(momento, esperado):
    assert VENTANA.is_open(utc(momento)) is esperado


def test_la_misma_hora_utc_cambia_segun_la_estacion():
    """Por esto hace falta el recorte: 07:00 UTC es dentro o fuera
    según sea invierno o verano."""
    siete_utc_invierno = utc("2027-01-13T07:00:00")  # 08:00 Madrid -> fuera
    siete_utc_verano = utc("2026-10-07T07:00:00")    # 09:00 Madrid -> dentro
    assert VENTANA.is_open(siete_utc_invierno) is False
    assert VENTANA.is_open(siete_utc_verano) is True


# ---------------------------------------------------------------------- #
# Días de la semana
# ---------------------------------------------------------------------- #
@pytest.mark.parametrize(
    "dia, esperado",
    [
        ("2026-10-05T10:00:00", True),   # lunes
        ("2026-10-09T10:00:00", True),   # viernes
        ("2026-10-10T10:00:00", False),  # sábado
        ("2026-10-11T10:00:00", False),  # domingo
    ],
)
def test_solo_lunes_a_viernes(dia, esperado):
    assert VENTANA.is_open(utc(dia)) is esperado


def test_sabado_a_la_misma_hora_de_un_dia_laborable_esta_cerrado():
    lunes = utc("2026-10-05T10:00:00")
    sabado = utc("2026-10-10T10:00:00")
    assert VENTANA.is_open(lunes) is True
    assert VENTANA.is_open(sabado) is False


# ---------------------------------------------------------------------- #
# Configuración
# ---------------------------------------------------------------------- #
def test_se_puede_desactivar():
    ventana = ScheduleWindow(enabled=False)
    assert ventana.is_open(utc("2026-10-10T03:00:00")) is True  # sábado de madrugada
    assert "sin restricción" in ventana.describe()


def test_franja_que_cruza_la_medianoche():
    ventana = ScheduleWindow(start=time(22, 0), end=time(2, 0))
    assert ventana.is_open(utc("2026-10-07T21:00:00")) is True   # 23:00 Madrid
    assert ventana.is_open(utc("2026-10-07T23:00:00")) is True   # 01:00 Madrid
    assert ventana.is_open(utc("2026-10-07T12:00:00")) is False  # 14:00 Madrid


def test_dias_personalizados():
    ventana = ScheduleWindow(days=frozenset({6, 7}))  # fin de semana
    assert ventana.is_open(utc("2026-10-10T10:00:00")) is True   # sábado
    assert ventana.is_open(utc("2026-10-09T10:00:00")) is False  # viernes


def test_describe():
    texto = VENTANA.describe()
    assert "08:30" in texto and "23:00" in texto
    assert "Europe/Madrid" in texto
    assert "lun" in texto and "vie" in texto


def test_hora_sin_zona_se_interpreta_como_utc():
    ingenua = datetime(2026, 10, 7, 6, 30)  # 08:30 Madrid en verano
    assert VENTANA.is_open(ingenua) is True
