"""Telegram Bot client — fail-soft.

Phase-08: typed `send()` with parse_mode, MarkdownV2 escape helper,
single retry on 5xx/transport-error. Errors are logged but never raised
so a Telegram outage never aborts the pipeline (PRD FR-N4).
"""

from __future__ import annotations

import re
import time

import httpx
import structlog

logger = structlog.get_logger(__name__)

# MarkdownV2 reserved chars per Telegram Bot API docs.
_MDV2_ESCAPE_RE = re.compile(r"([_*\[\]()~`>#+\-=|{}.!\\])")
# Plain Markdown (legacy) only escapes a small subset.
_MD_ESCAPE_RE = re.compile(r"([_*`\[])")


class TelegramClient:
    """Sends Markdown messages to a single chat. Fail-soft: every error is
    swallowed so callers can treat `send()` as best-effort."""

    BASE_URL = "https://api.telegram.org"
    SEND_TIMEOUT_SEC = 10.0

    def __init__(
        self,
        bot_token: str,
        chat_id: str,
        *,
        parse_mode: str = "Markdown",
    ) -> None:
        self._token = bot_token
        self._chat_id = chat_id
        self._parse_mode = parse_mode

    @property
    def enabled(self) -> bool:
        return bool(self._token and self._chat_id)

    def send(self, text: str, *, parse_mode: str | None = None) -> bool:
        """Returns True on 200, False on any other outcome (logged)."""
        if not self.enabled:
            logger.debug("telegram_disabled")
            return False
        mode = parse_mode if parse_mode is not None else self._parse_mode
        url = f"{self.BASE_URL}/bot{self._token}/sendMessage"
        payload = {
            "chat_id": self._chat_id,
            "text": text,
            "parse_mode": mode,
            "disable_web_page_preview": True,
        }

        ok, retryable = self._post_once(url, payload)
        if ok:
            return True
        if not retryable:
            return False
        # Retry once on transient (5xx / network) only — small backoff.
        time.sleep(1.0)
        ok, _ = self._post_once(url, payload)
        return ok

    def _post_once(self, url: str, payload: dict[str, object]) -> tuple[bool, bool]:
        """Returns (success, retryable)."""
        try:
            resp = httpx.post(url, json=payload, timeout=self.SEND_TIMEOUT_SEC)
        except httpx.HTTPError as exc:
            logger.warning("telegram_send_error", error=str(exc))
            return False, True
        if resp.status_code == 200:
            return True, False
        retryable = resp.status_code >= 500
        logger.warning(
            "telegram_send_failed",
            status=resp.status_code,
            body=resp.text[:200],
            retryable=retryable,
        )
        return False, retryable

    @staticmethod
    def md_escape(s: str, *, v2: bool = False) -> str:
        """Escape Telegram Markdown special chars in dynamic content.

        v2=True for MarkdownV2 (stricter set). Default targets legacy `Markdown`.
        """
        regex = _MDV2_ESCAPE_RE if v2 else _MD_ESCAPE_RE
        return regex.sub(r"\\\1", s)
