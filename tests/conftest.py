"""Pytest fixtures shared across the suite.

Phase-01: only env isolation. Phase-02+ adds VCR config + Postgres test fixtures.
"""

from __future__ import annotations

import os
from collections.abc import Iterator

import pytest


@pytest.fixture(autouse=True)
def _isolate_env(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    """Strip Haravan/DB env vars by default so tests fail loudly if a fixture forgot to inject them."""
    for key in list(os.environ):
        if key.startswith(("HARAVAN_", "TELEGRAM_")) or key in {"DATABASE_URL", "LOG_FORMAT"}:
            monkeypatch.delenv(key, raising=False)
    yield
