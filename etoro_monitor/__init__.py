"""Monitor de carteras públicas de eToro con avisos por Telegram.

Solo usa endpoints PÚBLICOS (los mismos que carga la web
https://www.etoro.com/people/<usuario>/portfolio). No hay login,
ni cookies, ni API key, ni KYC.
"""

from .client import (
    Blocked,
    EtoroClient,
    EtoroError,
    PrivatePortfolio,
    UserNotFound,
)
from .models import Change, EtoroUser, Instrument, Position, Snapshot

__version__ = "0.1.0"

__all__ = [
    "Blocked",
    "Change",
    "EtoroClient",
    "EtoroError",
    "EtoroUser",
    "Instrument",
    "Position",
    "PrivatePortfolio",
    "Snapshot",
    "UserNotFound",
]
