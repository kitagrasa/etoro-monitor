"""Interfaz de línea de comandos y configuración.

Aquí se comprueban dos cosas delicadas:

1. Que las opciones valen tanto antes como después del subcomando (argparse
   pisaba con su valor por defecto lo indicado antes del subcomando).
2. Que al podar usuarios no se borre a nadie por error.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from etoro_monitor.cli import _apply_defaults, build_parser  # noqa: E402
from etoro_monitor.config import load_config  # noqa: E402
from etoro_monitor.monitor import Monitor  # noqa: E402
from etoro_monitor.state import State  # noqa: E402
from etoro_monitor.models import EtoroUser  # noqa: E402
from etoro_monitor.client import EtoroClient  # noqa: E402


def parse(argv):
    return _apply_defaults(build_parser().parse_args(argv))


# ---------------------------------------------------------------------- #
# Argumentos
# ---------------------------------------------------------------------- #
def test_el_subcomando_apunta_a_su_funcion():
    assert parse(["check"]).func.__name__ == "cmd_check"
    assert parse(["users"]).func.__name__ == "cmd_users"
    assert parse(["show", "x"]).func.__name__ == "cmd_show"


def test_usuarios_file_antes_o_despues_del_subcomando():
    """Regresión: argparse pisaba el valor puesto antes del subcomando."""
    assert parse(["--users-file", "x.md", "users"]).users_file == "x.md"
    assert parse(["users", "--users-file", "x.md"]).users_file == "x.md"
    assert parse(["--users-file", "x.md", "check"]).users_file == "x.md"
    assert parse(["check", "--users-file", "x.md"]).users_file == "x.md"


def test_valores_por_defecto():
    args = parse(["check"])
    assert args.config == "watchlist.yml"
    assert args.state == "state/state.json"
    assert args.log_level == "INFO"
    assert args.users_file is None


def test_variables_de_entorno(monkeypatch):
    monkeypatch.setenv("ETORO_CONFIG", "otro.yml")
    monkeypatch.setenv("ETORO_STATE", "otro.json")
    monkeypatch.setenv("ETORO_WATCHLIST_MD", "otro.md")
    monkeypatch.setenv("LOG_LEVEL", "DEBUG")
    args = parse(["check"])
    assert args.config == "otro.yml"
    assert args.state == "otro.json"
    assert args.users_file == "otro.md"
    assert args.log_level == "DEBUG"


def test_los_argumentos_mandan_sobre_las_variables(monkeypatch):
    monkeypatch.setenv("ETORO_WATCHLIST_MD", "env.md")
    assert parse(["check", "--users-file", "argv.md"]).users_file == "argv.md"


# ---------------------------------------------------------------------- #
# Config
# ---------------------------------------------------------------------- #
def escribir(tmp_path, users_md: str | None, config_yml: str | None = None):
    yml = tmp_path / "watchlist.yml"
    yml.write_text(config_yml or "users: []\n", encoding="utf-8")
    md = tmp_path / "watchlist.md"
    if users_md is not None:
        md.write_text(users_md, encoding="utf-8")
    return yml, md


def test_lee_los_usuarios_del_markdown(tmp_path):
    yml, _ = escribir(tmp_path, "uno_ejemplo\ndos_ejemplo\n")
    config = load_config(yml)
    assert config.users == ["uno_ejemplo", "dos_ejemplo"]
    assert config.users_origin == "watchlist.md"
    assert config.describe_users() == "uno_ejemplo y dos_ejemplo"


def test_el_markdown_manda_sobre_el_yaml(tmp_path):
    yml, _ = escribir(tmp_path, "# solo este\nuno_ejemplo\n", "users:\n  - otro_ejemplo\n")
    assert load_config(yml).users == ["uno_ejemplo"]


def test_sin_markdown_usa_el_yaml(tmp_path):
    yml, _ = escribir(tmp_path, None, "users:\n  - uno_ejemplo\n")
    config = load_config(yml)
    assert config.users == ["uno_ejemplo"]
    assert config.users_origin == "watchlist.yml"


def test_sin_usuarios_en_ningun_sitio(tmp_path):
    yml, _ = escribir(tmp_path, "# todo comentado\n")
    try:
        load_config(yml)
    except ValueError as exc:
        assert "watchlist.md" in str(exc)
    else:  # pragma: no cover
        raise AssertionError("debería haber fallado sin usuarios")


def test_ajustes_tecnicos_del_yaml(tmp_path):
    yml, _ = escribir(
        tmp_path,
        "uno_ejemplo\n",
        "http:\n  delay_seconds: 1.5\n  max_retries: 7\nalerts:\n  notify_on_baseline: false\n",
    )
    config = load_config(yml)
    assert config.http.delay_seconds == 1.5
    assert config.http.max_retries == 7
    assert config.alerts.notify_on_baseline is False
    assert config.alerts.error_cooldown_minutes == 180


def test_fichero_de_usuarios_explicito(tmp_path):
    yml, _ = escribir(tmp_path, "uno_ejemplo\n")
    otro = tmp_path / "personas.md"
    otro.write_text("dos_ejemplo\n", encoding="utf-8")
    config = load_config(yml, otro)
    assert config.users == ["dos_ejemplo"]
    assert config.users_source == otro


def test_el_del_repositorio_esta_bien_formado(monkeypatch):
    """El repo debe cargar sin incrustar a quién sigues."""
    monkeypatch.delenv("ETORO_USERS", raising=False)
    raiz = Path(__file__).resolve().parents[1]
    try:
        config = load_config(raiz / "watchlist.yml")
    except ValueError as exc:
        assert "watchlist.md" in str(exc)
        assert "ETORO_USERS" in str(exc)
    else:
        assert config.users


# ---------------------------------------------------------------------- #
# ETORO_USERS: fuera del repositorio
# ---------------------------------------------------------------------- #
def test_etoro_users_tiene_prioridad(tmp_path, monkeypatch):
    yml, _ = escribir(tmp_path, "del_fichero\n")
    monkeypatch.setenv("ETORO_USERS", "del_secreto")
    config = load_config(yml)
    assert config.users == ["del_secreto"]
    assert config.users_source is None
    assert "ETORO_USERS" in config.users_origin


def test_etoro_users_admite_varios_formatos(tmp_path, monkeypatch):
    yml, _ = escribir(tmp_path, "del_fichero\n")
    monkeypatch.setenv("ETORO_USERS", "uno, dos\ntres")
    assert load_config(yml).users == ["uno", "dos", "tres"]


def test_etoro_users_vacia_no_estorba(tmp_path, monkeypatch):
    yml, _ = escribir(tmp_path, "del_fichero\n")
    monkeypatch.setenv("ETORO_USERS", "   ")
    assert load_config(yml).users == ["del_fichero"]


def test_sin_fichero_pero_con_secreto(tmp_path, monkeypatch):
    yml, _ = escribir(tmp_path, "# (vacío a propósito)\n")
    monkeypatch.setenv("ETORO_USERS", "uno_ejemplo")
    assert load_config(yml).users == ["uno_ejemplo"]


# ---------------------------------------------------------------------- #
# Diagnóstico sin lista de usuarios
# ---------------------------------------------------------------------- #
def test_los_comandos_de_diagnostico_funcionan_sin_usuarios(tmp_path, monkeypatch):
    """`ping --username X` fallaba si el watchlist estaba vacío, que es justo
    cuando más falta hace el diagnóstico."""
    monkeypatch.delenv("ETORO_USERS", raising=False)
    yml, _ = escribir(tmp_path, "# vacío a propósito\n")
    try:
        load_config(yml)
    except ValueError:
        pass
    else:  # pragma: no cover
        raise AssertionError("debería exigir usuarios")

    assert load_config(yml, require_users=False).users == []


def test_sin_config_funciona_para_diagnostico(tmp_path, monkeypatch):
    monkeypatch.delenv("ETORO_USERS", raising=False)
    config = load_config(tmp_path / "no-existe.yml", require_users=False)
    assert config.users == []


# ---------------------------------------------------------------------- #
# La poda no debe borrar a nadie por error
# ---------------------------------------------------------------------- #
def usuario(nombre: str) -> EtoroUser:
    return EtoroUser(username=nombre, gcid=1, real_cid=2)


def test_prune_false_no_borra_a_nadie(tmp_path):
    """Con `--user X` solo se revisa a una persona: los demás deben quedarse."""
    state = State(path=tmp_path / "state.json")
    state.upsert_user(usuario("uno_ejemplo"))
    state.upsert_user(usuario("dos_ejemplo"))

    monitor = Monitor(EtoroClient(delay=0), state, notify=lambda _t: None)
    monitor._prune(["uno_ejemplo"])  # lo que haría prune=True
    assert list(state.users) == ["uno_ejemplo"]


def test_la_poda_solo_usa_la_lista_completa(tmp_path):
    """La poda se basa en la lista de seguimiento, no en lo procesado.

    Así un usuario con el nombre mal escrito (que sí está en la lista) NO se
    borra, y se sigue reintentando.
    """
    state = State(path=tmp_path / "state.json")
    state.upsert_user(usuario("con_typo_ejemplo"))
    monitor = Monitor(EtoroClient(delay=0), state, notify=lambda _t: None)
    monitor._prune(["con_typo_ejemplo", "otro_ejemplo"])
    assert list(state.users) == ["con_typo_ejemplo"]
