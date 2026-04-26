"""CLI presentation helpers: Rich status table.

Telegram message formatting moved to `haravan_elt.notifications.Notifier`
in phase-08.
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
