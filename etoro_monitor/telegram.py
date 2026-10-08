"""Envío de avisos por Telegram (Bot API, sin dependencias extra)."""

from __future__ import annotations

import logging
import time
from typing import Optional

import requests

log = logging.getLogger(__name__)

TELEGRAM_API = "https://api.telegram.org"
MAX_MESSAGE_LENGTH = 4096


class TelegramError(RuntimeError):
    pass


class TelegramNotifier:
    def __init__(
        self,
        token: Optional[str],
        chat_id: Optional[str],
        *,
        timeout: float = 20.0,
        disable_notification: bool = False,
        session: Optional[requests.Session] = None,
    ) -> None:
        self.token = (token or "").strip()
        self.chat_id = (chat_id or "").strip()
        self.timeout = timeout
        self.disable_notification = disable_notification
        self.session = session or requests.Session()

    @property
    def configured(self) -> bool:
        return bool(self.token and self.chat_id)

    def send(self, text: str, *, disable_web_page_preview: bool = True) -> bool:
        if not self.configured:
            raise TelegramError(
                "Faltan TELEGRAM_BOT_TOKEN o TELEGRAM_CHAT_ID. "
                "Configúralos como secretos del repositorio."
            )
        ok = True
        for chunk in split_message(text):
            ok = self._send_chunk(chunk, disable_web_page_preview) and ok
        return ok

    # ------------------------------------------------------------------ #
    def _send_chunk(self, text: str, disable_web_page_preview: bool) -> bool:
        url = f"{TELEGRAM_API}/bot{self.token}/sendMessage"
        payload = {
            "chat_id": self.chat_id,
            "text": text,
            "parse_mode": "HTML",
            "disable_web_page_preview": disable_web_page_preview,
            "disable_notification": self.disable_notification,
        }
        last_error = ""
        for attempt in range(4):
            try:
                response = self.session.post(url, json=payload, timeout=self.timeout)
            except (requests.ConnectionError, requests.Timeout) as exc:
                last_error = str(exc)
                time.sleep(2 + attempt * 2)
                continue

            if response.status_code == 200:
                return True

            if response.status_code == 429:
                retry_after = 3
                try:
                    retry_after = int(
                        response.json().get("parameters", {}).get("retry_after", 3)
                    )
                except (ValueError, AttributeError):
                    pass
                log.warning("Telegram 429: espero %ss", retry_after)
                time.sleep(retry_after + 1)
                continue

            if response.status_code >= 500:
                last_error = response.text[:200]
                time.sleep(3 + attempt * 2)
                continue

            # 4xx -> error de configuración: no insistir
            raise TelegramError(
                f"Telegram devolvió {response.status_code}: {response.text[:300]}"
            )

        raise TelegramError(f"No se pudo enviar el mensaje a Telegram: {last_error}")


def split_message(text: str, limit: int = MAX_MESSAGE_LENGTH) -> list[str]:
    """Trocea el mensaje respetando el límite de Telegram (4096 caracteres)."""
    if len(text) <= limit:
        return [text]

    chunks: list[str] = []
    current = ""
    for line in text.splitlines(keepends=True):
        while len(line) > limit:  # línea absurdamente larga
            chunks.append(current + line[:limit])
            current = ""
            line = line[limit:]
        if len(current) + len(line) > limit:
            chunks.append(current)
            current = line
        else:
            current += line
    if current:
        chunks.append(current)
    return chunks
