"""Tiny shared utilities for extractor implementations."""

from __future__ import annotations

from datetime import datetime


def parse_haravan_timestamp(value: str) -> datetime:
    """Haravan returns `2026-04-26T10:00:00Z` style — `fromisoformat` needs `+00:00`.

    Tolerates fractional seconds (Python 3.11+ `fromisoformat` handles them).
    """
    return datetime.fromisoformat(value.replace("Z", "+00:00"))
