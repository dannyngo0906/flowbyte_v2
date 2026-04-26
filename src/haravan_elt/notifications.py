"""Notification orchestrator — wraps `TelegramClient` with typed event helpers
and Markdown templates per PRD §4.6 FR-N3. All sends are fail-soft.

Events:
  - start    (cron-only — manual runs would spam)
  - success  (extract counts + dbt summary)
  - failure  (stage + truncated traceback)
  - warning  (rate limit, dbt test failures, refresh failure, etc.)
"""

from __future__ import annotations

from typing import Any

import structlog

from haravan_elt.client.telegram import TelegramClient

logger = structlog.get_logger(__name__)

# Hard cap on tracebacks pushed to Telegram — matches `meta.run_log.error_message`.
ERROR_MESSAGE_MAX_CHARS = 1000


class Notifier:
    """Routes pipeline lifecycle events to Telegram with consistent formatting.

    `telegram=None` (or a disabled client) makes every method a no-op so
    callers can wire a Notifier unconditionally.
    """

    def __init__(self, telegram: TelegramClient | None) -> None:
        self._tg = telegram

    @property
    def enabled(self) -> bool:
        return bool(self._tg and self._tg.enabled)

    def start(self, mode: str, triggered_by: str) -> bool:
        """Cron-only — manual triggers are silent (FR-N2)."""
        if not self.enabled or triggered_by != "cron":
            return False
        text = f"▶️ *Haravan ELT — Daily Run start*\nMode: `{mode}`"
        return self._send(text)

    def success(
        self,
        ext_summary: dict[str, int],
        dbt_summary: dict[str, Any],
        duration_sec: float,
    ) -> bool:
        if not self.enabled:
            return False
        lines = [
            "✅ *Haravan ELT — Daily Run*",
            f"⏱ Duration: {duration_sec:.1f}s",
            "📥 Extracted:",
        ]
        for domain, rows in ext_summary.items():
            lines.append(f"  • `{domain}`: {rows:,} rows")
        if dbt_summary.get("models_built") is not None:
            tests_passed = dbt_summary.get("tests_passed", 0)
            tests_total = dbt_summary.get("tests_total", 0)
            lines.append(
                f"🔧 dbt: {dbt_summary['models_built']} models, "
                f"{tests_passed}/{tests_total} tests passed"
            )
        elif "success" in dbt_summary:
            lines.append(f"🔧 dbt: {'OK' if dbt_summary['success'] else 'FAIL'}")
        return self._send("\n".join(lines))

    def failure(self, stage: str, exc: BaseException) -> bool:
        if not self.enabled:
            return False
        # Strip backticks so they don't break the fenced code block.
        msg = str(exc).replace("```", "'''")[:ERROR_MESSAGE_MAX_CHARS]
        text = f"❌ *Haravan ELT — Failure*\nStage: `{stage}`\n```\n{msg}\n```"
        return self._send(text)

    def warning(self, reason: str, details: str = "") -> bool:
        if not self.enabled:
            return False
        body = f"⚠️ *Haravan ELT — Warning*\n{reason}"
        if details:
            body += f"\n{details}"
        return self._send(body)

    # -------------------------------------------------------------- internals

    def _send(self, text: str) -> bool:
        """Defensive wrapper — even if `TelegramClient.send` ever leaks an
        exception, the pipeline must not abort (FR-N4)."""
        assert self._tg is not None  # guarded by `self.enabled`
        try:
            return self._tg.send(text)
        except Exception as exc:  # noqa: BLE001 — fail-soft contract
            logger.warning("notifier_send_swallowed", error=str(exc))
            return False
