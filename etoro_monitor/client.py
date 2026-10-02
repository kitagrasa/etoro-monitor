"""Cliente de solo lectura sobre los endpoints PÚBLICOS de eToro.

IMPORTANTE
----------
Todo lo que hace este módulo es lo mismo que hace tu navegador cuando abres
`https://www.etoro.com/people/<usuario>/portfolio` sin haber iniciado sesión:

    GET /api/logininfo/v1.1/users/<usuario>                       -> usuario -> CID
    GET /sapi/trade-data-real/live/public/portfolios?cid=<cid>    -> cartera agregada
    GET /sapi/trade-data-real/live/public/portfolios/exposure     -> % de cada activo
    GET /sapi/trade-data-real/live/public/positions?cid&instrumentId
                                                                  -> cada posición abierta
    GET /sapi/instrumentsmetadata/V1.1/instruments/<id>           -> nombre del activo

No hay login, ni cookies de sesión, ni API key, ni KYC. Nada de esto toca
ninguna cuenta privada: solo carteras que sus dueños han hecho públicas.
"""

from __future__ import annotations

import logging
import random
import time
from typing import Any, Optional
from urllib.parse import urlencode

import requests

log = logging.getLogger(__name__)

BASE_URL = "https://www.etoro.com"

DEFAULT_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/154.0.0.0 Safari/537.36"
)

# eToro pone por delante DataDome/Cloudflare. Si te pasas de rosca te
# devuelve una página de captcha en vez de JSON. Detectamos ese caso para
# no interpretarlo como "cartera vacía" (¡que sería catastrófico: creería
# que el usuario ha vendido todo!).
_CAPTCHA_MARKERS = (
    "captcha-delivery.com",
    "geo.captcha-delivery.com",
    "Attention Required! | Cloudflare",
    "Just a moment...",
)


class EtoroError(RuntimeError):
    """Error genérico hablando con eToro."""


class UserNotFound(EtoroError):
    """El nombre de usuario no existe."""


class PrivatePortfolio(EtoroError):
    """El usuario existe pero su cartera es privada."""


class Blocked(EtoroError):
    """eToro (DataDome/Cloudflare) nos ha bloqueado temporalmente."""


class EtoroClient:
    """Cliente HTTP con reintentos, pausas y detección de bloqueos."""

    def __init__(
        self,
        delay: float = 0.35,
        timeout: float = 30.0,
        max_retries: int = 3,
        user_agent: str = DEFAULT_USER_AGENT,
        session: Optional[requests.Session] = None,
    ) -> None:
        self.delay = max(0.0, delay)
        self.timeout = timeout
        self.max_retries = max(0, max_retries)
        self.session = session or requests.Session()
        self.session.headers.update(
            {
                "User-Agent": user_agent,
                "Accept": "application/json, text/plain, */*",
                "Accept-Language": "en-US,en;q=0.9",
                "Referer": f"{BASE_URL}/",
            }
        )
        self.request_count = 0

    # ------------------------------------------------------------------ #
    # HTTP
    # ------------------------------------------------------------------ #
    def _sleep(self) -> None:
        """Pausa con jitter para no parecer un script agresivo."""
        if self.delay:
            time.sleep(self.delay * (0.75 + random.random() * 0.5))

    def _request(
        self,
        path: str,
        params: Optional[dict[str, Any]] = None,
        *,
        allow_empty: bool = True,
    ) -> requests.Response:
        url = path if path.startswith("http") else BASE_URL + path
        last_error: Optional[Exception] = None

        for attempt in range(self.max_retries + 1):
            if attempt or self.request_count:
                self._sleep()
            try:
                response = self.session.get(url, params=params, timeout=self.timeout)
            except (requests.ConnectionError, requests.Timeout) as exc:
                last_error = exc
                log.warning("Fallo de red (%s), reintento %s/%s", exc, attempt + 1, self.max_retries)
                time.sleep(min(2**attempt, 8) + random.random())
                continue
            finally:
                self.request_count += 1

            status = response.status_code

            if status in (429, 500, 502, 503, 504):
                wait = _retry_after(response) or min(2**attempt, 15)
                log.warning("HTTP %s en %s, espero %.1fs", status, url, wait)
                last_error = EtoroError(f"HTTP {status} en {url}")
                time.sleep(wait + random.random())
                continue

            return response

        raise EtoroError(f"No se pudo completar la petición a {url}: {last_error}")

    def _get_json(
        self,
        path: str,
        params: Optional[dict[str, Any]] = None,
        *,
        not_found_ok: bool = False,
        private_ok: bool = False,
    ) -> Optional[dict[str, Any]]:
        response = self._request(path, params)
        status = response.status_code
        body = response.text
        content_type = (response.headers.get("content-type") or "").lower()

        if status == 403:
            if _looks_like_captcha(body, content_type):
                raise Blocked(
                    "eToro ha devuelto un captcha (DataDome/Cloudflare). "
                    "Baja la frecuencia o prueba más tarde."
                )
            if "PRIVATE" in body.upper():
                if private_ok:
                    return None
                raise PrivatePortfolio(f"Cartera privada: {body[:200]}")
            raise Blocked(f"HTTP 403 no esperado: {body[:200]}")

        if status == 404:
            if not_found_ok:
                return None
            raise UserNotFound(f"No encontrado: {urlencode(params or {})}")

        if status != 200:
            raise EtoroError(f"HTTP {status} en {path} -> {body[:200]}")

        if _looks_like_captcha(body, content_type) or "json" not in content_type:
            # La web devuelve el shell HTML del SPA cuando la ruta no existe.
            raise Blocked(
                f"Respuesta no-JSON en {path} (content-type={content_type!r}). "
                "Puede ser un bloqueo de eToro o un endpoint que ha cambiado."
            )

        try:
            return response.json()
        except ValueError as exc:  # pragma: no cover - defensivo
            raise EtoroError(f"JSON inválido en {path}: {exc}") from exc

    # ------------------------------------------------------------------ #
    # Endpoints
    # ------------------------------------------------------------------ #
    def resolve_username(self, username: str) -> dict[str, Any]:
        """Nombre de usuario -> datos públicos (incluye el CID de la cartera real)."""
        data = self._get_json(
            f"/api/logininfo/v1.1/users/{username.strip()}",
            not_found_ok=True,
        )
        if not data:
            raise UserNotFound(f"El usuario {username!r} no existe en eToro")
        return data

    def get_portfolio(self, cid: int) -> dict[str, Any]:
        """Cartera agregada por activo + espejos (mirrors)."""
        data = self._get_json(
            "/sapi/trade-data-real/live/public/portfolios",
            {"format": "json", "cid": cid},
            private_ok=True,
        )
        if data is None:
            raise PrivatePortfolio(f"La cartera del CID {cid} es privada")
        return data

    def get_exposure(self, cid: int) -> dict[int, float]:
        """Porcentaje de la cartera que ocupa cada activo (suma ~100)."""
        data = self._get_json(
            "/sapi/trade-data-real/live/public/portfolios/exposure",
            {"cid": cid},
            private_ok=True,
        )
        if data is None:
            raise PrivatePortfolio(f"La exposición del CID {cid} es privada")
        result: dict[int, float] = {}
        for row in data.get("InstrumentIdExposurePercentageList") or []:
            try:
                result[int(row["InstrumentID"])] = float(row["ExposurePercentage"])
            except (KeyError, TypeError, ValueError):
                continue
        return result

    def get_positions(self, cid: int, instrument_id: int) -> dict[str, Any]:
        """Todas las posiciones ABIERTAS de un activo, con su PositionID."""
        data = self._get_json(
            "/sapi/trade-data-real/live/public/positions",
            {"cid": cid, "instrumentId": instrument_id},
            private_ok=True,
        )
        return data or {}

    def get_instrument(self, instrument_id: int) -> Optional[dict[str, Any]]:
        """Metadatos de un activo (nombre legible y ticker)."""
        data = self._get_json(
            f"/sapi/instrumentsmetadata/V1.1/instruments/{instrument_id}",
            not_found_ok=True,
        )
        if not data:
            return None
        return data.get("InstrumentDisplayData") or None

    def close(self) -> None:
        self.session.close()


def _retry_after(response: requests.Response) -> Optional[float]:
    value = response.headers.get("retry-after")
    if not value:
        return None
    try:
        return max(0.0, float(value))
    except ValueError:
        return None


def _looks_like_captcha(body: str, content_type: str) -> bool:
    if "captcha-delivery.com" in body:
        return True
    if content_type.startswith("text/html") and any(m in body for m in _CAPTCHA_MARKERS):
        return True
    return False
