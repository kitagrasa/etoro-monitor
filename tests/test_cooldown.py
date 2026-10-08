"""Tests del control de repetición de avisos (cooldown.json)."""

from __future__ import annotations

import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from etoro_monitor.cooldown import (  # noqa: E402
    STATE_ERROR_KEY,
    CooldownStore,
    error_key,
)

AHORA = datetime(2026, 10, 2, 12, 0, tzinfo=timezone.utc)


def test_sin_fichero_se_puede_avisar(tmp_path):
    store = CooldownStore.load(tmp_path / "cooldown.json")
    assert store.is_ready("error:x", minutes=180, now=AHORA) is True


def test_tras_avisar_no_vuelve_a_avisar(tmp_path):
    store = CooldownStore.load(tmp_path / "cooldown.json")
    store.mark("error:x", now=AHORA)
    assert store.is_ready("error:x", minutes=180, now=AHORA) is False
    # 179 minutos después, todavía no
    assert (
        store.is_ready("error:x", minutes=180, now=AHORA + timedelta(minutes=179))
        is False
    )
    # 181 minutos después, sí
    assert (
        store.is_ready("error:x", minutes=180, now=AHORA + timedelta(minutes=181))
        is True
    )


def test_el_cooldown_va_por_usuario(tmp_path):
    """Un problema en una persona no debe silenciar el de otra."""
    store = CooldownStore.load(tmp_path / "cooldown.json")
    store.mark(error_key("uno_ejemplo"), now=AHORA)
    assert store.is_ready(error_key("uno_ejemplo"), minutes=180, now=AHORA) is False
    assert store.is_ready(error_key("dos_ejemplo"), minutes=180, now=AHORA) is True


def test_forget_permite_volver_a_avisar(tmp_path):
    store = CooldownStore.load(tmp_path / "cooldown.json")
    store.mark("error:x", now=AHORA)
    store.forget("error:x")
    assert store.is_ready("error:x", minutes=180, now=AHORA) is True


def test_persiste_entre_ejecuciones(tmp_path):
    """Esta es LA propiedad que hacía falta para que funcione en GitHub."""
    ruta = tmp_path / "cooldown.json"
    store = CooldownStore.load(ruta)
    store.mark(STATE_ERROR_KEY, now=AHORA)
    assert store.save() is True

    # Nueva ejecución: se relee el fichero del repositorio
    otra = CooldownStore.load(ruta)
    assert otra.is_ready(STATE_ERROR_KEY, minutes=180, now=AHORA) is False


def test_guardar_solo_si_cambia(tmp_path):
    ruta = tmp_path / "cooldown.json"
    store = CooldownStore.load(ruta)
    store.mark("error:x", now=AHORA)
    assert store.save() is True
    assert store.save() is False


def test_un_fichero_dañado_no_rompe_nada(tmp_path):
    ruta = tmp_path / "cooldown.json"
    ruta.write_text("{ esto no es JSON", encoding="utf-8")
    store = CooldownStore.load(ruta)
    # Peor caso: se repite algún aviso, pero el monitor sigue funcionando.
    assert store.is_ready("error:x", minutes=180, now=AHORA) is True


def test_se_olvidan_los_avisos_antiguos(tmp_path):
    store = CooldownStore.load(tmp_path / "cooldown.json")
    store.mark("error:viejo", now=AHORA - timedelta(days=40))
    store.mark("error:nuevo", now=AHORA)
    assert store.prune(now=AHORA) == 1
    assert "error:viejo" not in store.sent
    assert "error:nuevo" in store.sent


def test_estructura_del_fichero(tmp_path):
    ruta = tmp_path / "cooldown.json"
    store = CooldownStore.load(ruta)
    store.mark(STATE_ERROR_KEY, now=AHORA)
    store.save()
    datos = json.loads(ruta.read_text(encoding="utf-8"))
    assert datos["version"] == 1
    assert list(datos["sent"]) == [STATE_ERROR_KEY]


def test_un_json_que_no_es_objeto_no_rompe(tmp_path):
    ruta = tmp_path / "cooldown.json"
    ruta.write_text("[]", encoding="utf-8")
    store = CooldownStore.load(ruta)
    assert store.sent == {}
