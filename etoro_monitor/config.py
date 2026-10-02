"""Carga de la configuración.

Hay dos ficheros, cada uno con una responsabilidad clara:

* `watchlist.md`  -> **la lista de personas** que quieres seguir, una por
  línea. Es el único que tocas habitualmente y se edita a mano.
* `watchlist.yml` -> los ajustes técnicos (pausa entre peticiones, avisos...).
  No hace falta tocarlo.

También se acepta `watchlist.yml` con una lista `users:` (por compatibilidad),
pero si existe `watchlist.md` manda el Markdown.
"""

from __future__ import annotations

import logging
import os
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional

from .schedule import (
    DEFAULT_DAYS,
    DEFAULT_END,
    DEFAULT_START,
    DEFAULT_TIMEZONE,
    ScheduleWindow,
    parse_days,
    parse_time,
)
from .watchlist import describe, load_usernames

try:  # PyYAML es opcional: si falta, tiramos de un JSON equivalente
    import yaml
except ImportError:  # pragma: no cover
    yaml = None  # type: ignore[assignment]

DEFAULT_CONFIG = "watchlist.yml"
DEFAULT_USERS_FILE = "watchlist.md"

log = logging.getLogger(__name__)


@dataclass
class HttpConfig:
    delay_seconds: float = 0.35
    timeout_seconds: float = 30.0
    max_retries: int = 3
    user_agent: str = ""


@dataclass
class AlertsConfig:
    # Mensaje de bienvenida la primera vez que se ve a un usuario.
    notify_on_baseline: bool = True
    # Avisar por Telegram si eToro bloquea o falla (con periodo de enfriamiento).
    notify_on_error: bool = True
    error_cooldown_minutes: int = 180


@dataclass
class Config:
    users: list[str] = field(default_factory=list)
    http: HttpConfig = field(default_factory=HttpConfig)
    alerts: AlertsConfig = field(default_factory=AlertsConfig)
    schedule: ScheduleWindow = field(default_factory=ScheduleWindow)
    source: Optional[Path] = None
    users_source: Optional[Path] = None
    # De dónde salen los usuarios, en texto legible para el usuario final.
    users_origin: str = ""

    def describe_users(self) -> str:
        return describe(self.users)


def load_config(
    path: str | os.PathLike[str] = DEFAULT_CONFIG,
    users_file: str | os.PathLike[str] | None = None,
    *,
    require_users: bool = True,
) -> Config:
    """Carga la configuración.

    `require_users=False` sirve para los comandos de diagnóstico
    (`ping`, `show`, `resolve`), que funcionan aunque todavía no hayas
    apuntado a nadie: es justo el momento en el que hacen falta.
    """
    config_path = Path(path)
    raw: dict[str, Any] = {}

    if config_path.exists():
        raw_text = config_path.read_text(encoding="utf-8")
        if yaml is not None:
            raw = yaml.safe_load(raw_text) or {}
        else:
            import json

            raw = json.loads(raw_text)
    elif users_file is not None and Path(users_file).exists():
        log.warning("No encuentro %s: uso solo %s", config_path, users_file)
    elif not require_users:
        # Comandos de diagnóstico: los valores por defecto bastan.
        log.debug("No encuentro %s; sigo con los valores por defecto", config_path)
    else:
        raise FileNotFoundError(
            f"No encuentro {config_path}. Copia el repositorio completo o crea "
            f"los ficheros {config_path} y {DEFAULT_USERS_FILE}."
        )

    # ------------------------------------------------------------------ #
    # Usuarios, por orden de prioridad:
    #   1. ETORO_USERS (variable de entorno / secreto de GitHub)
    #   2. watchlist.md (un usuario por línea)  <- lo normal
    #   3. la lista 'users:' del YAML (formato antiguo)
    # ------------------------------------------------------------------ #
    md_path = Path(users_file) if users_file else _default_users_path(config_path)
    users: list[str] = []
    users_source: Optional[Path] = None

    users_origin = ""
    env_users = _users_from_env()
    if env_users:
        users = env_users
        users_source = None
        users_origin = "la variable ETORO_USERS (secreto de GitHub)"
        log.info(
            "Uso la lista de la variable ETORO_USERS (%s); ignoro %s",
            describe(users),
            md_path.name,
        )
    elif md_path.exists():
        users = load_usernames(md_path)
        users_source = md_path
        users_origin = md_path.name
        yml_users = _users_from_yml(raw)
        if yml_users and yml_users != users:
            log.info(
                "%s tiene la lista buena (%s); ignoro los 'users' de %s",
                md_path.name,
                describe(users),
                config_path.name,
            )
    else:
        users = _users_from_yml(raw)
        if users:
            users_source = config_path
            users_origin = config_path.name

    if not users and require_users:
        raise ValueError(
            f"No hay ningún usuario que seguir. Escribe uno por línea en "
            f"{md_path.name} (por ejemplo: usuario_ejemplo), o define la "
            f"variable de entorno ETORO_USERS."
        )

    http_raw = raw.get("http") or {}
    alerts_raw = raw.get("alerts") or {}
    schedule_raw = raw.get("schedule") or {}
    schedule = ScheduleWindow(
        enabled=bool(schedule_raw.get("enabled", True)),
        timezone=str(schedule_raw.get("timezone") or DEFAULT_TIMEZONE),
        start=parse_time(schedule_raw.get("start", DEFAULT_START)),
        end=parse_time(schedule_raw.get("end", DEFAULT_END)),
        days=parse_days(schedule_raw.get("days", list(DEFAULT_DAYS))),
    )

    return Config(
        users=users,
        http=HttpConfig(
            delay_seconds=float(http_raw.get("delay_seconds", 0.35)),
            timeout_seconds=float(http_raw.get("timeout_seconds", 30.0)),
            max_retries=int(http_raw.get("max_retries", 3)),
            user_agent=str(http_raw.get("user_agent", "") or ""),
        ),
        alerts=AlertsConfig(
            notify_on_baseline=bool(alerts_raw.get("notify_on_baseline", True)),
            notify_on_error=bool(alerts_raw.get("notify_on_error", True)),
            error_cooldown_minutes=int(alerts_raw.get("error_cooldown_minutes", 180)),
        ),
        schedule=schedule,
        source=config_path,
        users_source=users_source,
        users_origin=users_origin or (
            users_source.name if users_source else "desconocido"
        ),
    )


def _default_users_path(config_path: Path) -> Path:
    """watchlist.yml -> watchlist.md (o ETORO_WATCHLIST_MD si está definido)."""
    override = os.environ.get("ETORO_WATCHLIST_MD")
    if override:
        return Path(override)
    if config_path.stem == "watchlist" and config_path.suffix in (".yml", ".yaml"):
        return config_path.with_suffix(".md")
    return config_path.parent / DEFAULT_USERS_FILE


def _users_from_env() -> list[str]:
    """Lista de usuarios en la variable ETORO_USERS (separados por comas,
    espacios o saltos de línea).

    Sirve para quien prefiera NO tener su lista de seguimiento dentro del
    repositorio: se guarda como secreto de GitHub y el repo queda limpio.

        ETORO_USERS="usuario_uno, usuario_dos"
    """
    raw = os.environ.get("ETORO_USERS", "").strip()
    if not raw:
        return []
    partes = re.split(r"[,\s]+", raw)
    vistos: set[str] = set()
    usuarios: list[str] = []
    for parte in partes:
        usuario = parte.strip()
        if usuario and usuario.lower() not in vistos:
            vistos.add(usuario.lower())
            usuarios.append(usuario)
    return usuarios


def _users_from_yml(raw: dict[str, Any]) -> list[str]:
    """Compatibilidad: lista `users:` dentro del YAML."""
    users: list[str] = []
    for entry in raw.get("users") or []:
        if isinstance(entry, str):
            users.append(entry.strip())
        elif isinstance(entry, dict):
            value = entry.get("username") or entry.get("user") or entry.get("name")
            if value:
                users.append(str(value).strip())
    return [u for u in users if u]
