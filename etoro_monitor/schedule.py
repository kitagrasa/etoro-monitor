"""Ventana horaria en la que el monitor puede ejecutarse.

El cron de GitHub Actions funciona **siempre en UTC** y no entiende de
cambios de hora. Madrid, en cambio, pasa de UTC+1 (invierno) a UTC+2
(verano). Por eso un cron no puede clavarse a una hora local todo el año.

Solución: el cron dispara en un rango amplio (que cubre los dos horarios) y
aquí se recorta con la zona horaria real de Madrid. Así el monitor solo actúa
cuando de verdad toca, en invierno y en verano.

Ejemplo de por qué hace falta: para las 08:30 de Madrid, en invierno el cron
dispara a las 07:30 UTC y en verano a las 06:30 UTC. Ambos deben pasar el
filtro, y ninguno de los dos se corresponde con la otra estación.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime, time, timezone
from typing import Optional
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

log = logging.getLogger(__name__)

DEFAULT_TIMEZONE = "Europe/Madrid"
DEFAULT_START = "08:30"
DEFAULT_END = "23:00"
# Días ISO: 1 = lunes ... 7 = domingo
DEFAULT_DAYS = (1, 2, 3, 4, 5)


def parse_time(value: str | time) -> time:
    """'08:30' -> datetime.time(8, 30)."""
    if isinstance(value, time):
        return value
    text = str(value).strip()
    for fmt in ("%H:%M", "%H:%M:%S"):
        try:
            return datetime.strptime(text, fmt).time()
        except ValueError:
            continue
    raise ValueError(f"Hora no válida: {value!r} (usa el formato 'HH:MM')")


def parse_days(value) -> frozenset[int]:
    """[1,2,3,4,5] o 'lun-vie' -> conjunto de días ISO (1=lunes, 7=domingo)."""
    if value is None:
        return frozenset(DEFAULT_DAYS)
    if isinstance(value, str):
        nombres = {
            "lun": 1,
            "mar": 2,
            "mie": 3,
            "jue": 4,
            "vie": 5,
            "sab": 6,
            "dom": 7,
        }
        texto = value.strip().lower().replace("é", "e").replace("á", "a")
        if "-" in texto:
            inicio, _, fin = texto.partition("-")
            a = nombres.get(inicio[:3].strip())
            b = nombres.get(fin[:3].strip())
            if a and b:
                return frozenset(range(a, b + 1)) if a <= b else frozenset(
                    list(range(a, 8)) + list(range(1, b + 1))
                )
        raise ValueError(f"Días no válidos: {value!r}")
    dias = []
    for item in value:
        try:
            numero = int(item)
        except (TypeError, ValueError):
            raise ValueError(f"Día no válido: {item!r} (usa 1=lunes ... 7=domingo)")
        if not 1 <= numero <= 7:
            raise ValueError(f"Día fuera de rango: {item!r} (usa 1=lunes ... 7=domingo)")
        dias.append(numero)
    return frozenset(dias)


def _load_zone(nombre: str) -> ZoneInfo:
    try:
        return ZoneInfo(nombre)
    except (ZoneInfoNotFoundError, ValueError, KeyError):
        # En Windows, sin el paquete 'tzdata', no hay base de datos horaria.
        log.warning(
            "No puedo cargar la zona horaria %r; uso UTC. "
            "En Windows instala el paquete 'tzdata' (pip install tzdata).",
            nombre,
        )
        return ZoneInfo("UTC")


@dataclass(frozen=True)
class ScheduleWindow:
    """Franja en la que se permite ejecutar (hora local de `timezone`)."""

    enabled: bool = True
    timezone: str = DEFAULT_TIMEZONE
    start: time = field(default_factory=lambda: parse_time(DEFAULT_START))
    end: time = field(default_factory=lambda: parse_time(DEFAULT_END))
    days: frozenset[int] = field(default_factory=lambda: frozenset(DEFAULT_DAYS))

    def local_now(self) -> datetime:
        return datetime.now(_load_zone(self.timezone))

    def is_open(self, moment: Optional[datetime] = None) -> bool:
        """¿Toca ejecutar en este instante? (por defecto, ahora mismo)."""
        if not self.enabled:
            return True
        ahora = moment or datetime.now(timezone.utc)
        if ahora.tzinfo is None:
            ahora = ahora.replace(tzinfo=timezone.utc)
        local = ahora.astimezone(_load_zone(self.timezone))

        if local.isoweekday() not in self.days:
            return False

        hora = local.time()
        if self.start <= self.end:
            return self.start <= hora <= self.end
        # Franja que cruza la medianoche (p. ej. 22:00 -> 02:00)
        return hora >= self.start or hora <= self.end

    def describe(self) -> str:
        if not self.enabled:
            return "sin restricción horaria (se ejecuta siempre)"
        nombres = ["lun", "mar", "mié", "jue", "vie", "sáb", "dom"]
        dias = ", ".join(nombres[d - 1] for d in sorted(self.days))
        return (
            f"{self.start.strftime('%H:%M')}–{self.end.strftime('%H:%M')} "
            f"({self.timezone}), {dias}"
        )
