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
from .cooldown import CooldownStore
from .models import Asset, Change, EtoroUser, Instrument, Snapshot
from .state import State, StateCorruptError

__version__ = "0.2.0"

__all__ = [
    "Asset",
    "Blocked",
    "Change",
    "CooldownStore",
    "EtoroClient",
    "EtoroError",
    "EtoroUser",
    "Instrument",
    "PrivatePortfolio",
    "Snapshot",
    "State",
    "StateCorruptError",
    "UserNotFound",
]
