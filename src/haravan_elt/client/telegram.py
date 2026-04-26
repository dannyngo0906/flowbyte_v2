"""Telegram Bot client — fail-soft.

Phase-02 ships only `send()`. Phase-08 expands to typed event helpers
(start / success / fail / warning) with Markdown templates.
"""

from __future__ import annotations

import httpx
import structlog

logger = structlog.get_logger(__name__)


class TelegramClient:
    """Sends Markdown messages to a single chat. Errors logged but never raised
    so a Telegram outage doesn't take the whole pipeline down (PRD FR-N4)."""

    BASE_URL = "https://api.telegram.org"

    def __init__(self, bot_token: str, chat_id: str) -> None:
        self._token = bot_token
        self._chat_id = chat_id

    @property
    def enabled(self) -> bool:
        return bool(self._token and self._chat_id)

    def send(self, text: str, *, parse_mode: str = "Markdown") -> bool:
        """Returns True on success, False on any failure (logged)."""
        if not self.enabled:
            logger.debug("telegram_disabled")
            return False
        url = f"{self.BASE_URL}/bot{self._token}/sendMessage"
        payload = {"chat_id": self._chat_id, "text": text, "parse_mode": parse_mode}
        try:
            resp = httpx.post(url, json=payload, timeout=10.0)
            if resp.status_code != 200:
                logger.warning(
                    "telegram_send_failed", status=resp.status_code, body=resp.text[:200]
                )
                return False
            return True
        except httpx.HTTPError as exc:
            logger.warning("telegram_send_error", error=str(exc))
            return False
