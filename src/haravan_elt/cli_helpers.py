"""CLI presentation helpers: status table + Telegram message templates.

Phase-07 ships plain-text Markdown templates; phase-08 will swap in the
typed event helpers on TelegramClient.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from rich.console import Console
from rich.table import Table


def render_status_table(rows: list[dict[str, Any]], console: Console | None = None) -> None:
    """Pretty-print `StateManager.list_status()` rows as a Rich table."""
    console = console or Console()
    table = Table(title="haravan-elt status")
    for col in ("domain", "watermark", "mode", "status", "started_at", "ended_at", "rows"):
        table.add_column(col)
    for r in rows:
        table.add_row(
            r["domain"],
            _fmt(r.get("last_updated_at")),
            r.get("mode") or "-",
            r.get("status") or "-",
            _fmt(r.get("started_at")),
            _fmt(r.get("ended_at")),
            str(r.get("rows_ingested") or 0),
        )
    console.print(table)


def _fmt(value: Any) -> str:
    if value is None:
        return "-"
    if isinstance(value, datetime):
        return value.isoformat(timespec="seconds")
    return str(value)


def format_run_success(
    ext_summary: dict[str, int],
    dbt_summary: dict[str, Any],
    duration_sec: float,
) -> str:
    """Telegram-friendly Markdown for a successful `run-all`."""
    lines = [
        "*Haravan ELT — Run OK*",
        f"Duration: {duration_sec:.1f}s",
        "Extracted:",
    ]
    lines += [f"  • `{domain}`: {rows} rows" for domain, rows in ext_summary.items()]
    dbt_status = "OK" if dbt_summary.get("success") else "FAIL"
    lines.append(f"dbt: {dbt_status}")
    return "\n".join(lines)


def format_run_failure(error: BaseException) -> str:
    """Telegram-friendly Markdown for a failed `run-all`. Truncated traceback."""
    msg = str(error)[:1000]
    return f"*Haravan ELT — Run FAILED*\n```\n{msg}\n```"
