"""Shared pytest fixtures.

- `_isolate_env`: scrub Haravan/Telegram/DB env so leaked values from a
  developer shell never sneak into a test run.
- `vcr_config`: cassette dir + filter Authorization. `record_mode='none'`
  keeps CI 100% offline; record locally first when adding new cassettes.
"""

from __future__ import annotations

import os
from collections.abc import Iterator
from pathlib import Path

import pytest


@pytest.fixture(autouse=True)
def _isolate_env(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    """Strip env vars that the app reads so tests must inject explicitly."""
    for key in list(os.environ):
        if key.startswith(("HARAVAN_", "TELEGRAM_")) or key in {"DATABASE_URL", "LOG_FORMAT"}:
            monkeypatch.delenv(key, raising=False)
    yield


@pytest.fixture
def vcr_config() -> dict[str, object]:
    """pytest-vcr config consumed by `@pytest.mark.vcr` tests in later phases."""
    return {
        "filter_headers": [("authorization", "Bearer DUMMY")],
        "match_on": ["method", "scheme", "host", "port", "path", "query"],
        "cassette_library_dir": str(Path(__file__).parent / "fixtures/vcr"),
        "record_mode": "none",
    }


@pytest.fixture
def fake_settings_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """Populate dummy Haravan + DB + Telegram env so `Settings()` instantiates."""
    monkeypatch.setenv("HARAVAN_SHOP_DOMAIN", "test.myharavan.com")
    monkeypatch.setenv("HARAVAN_ACCESS_TOKEN", "dummy-access")
    monkeypatch.setenv("HARAVAN_REFRESH_TOKEN", "dummy-refresh")
    monkeypatch.setenv("HARAVAN_CLIENT_ID", "dummy-client")
    monkeypatch.setenv("HARAVAN_CLIENT_SECRET", "dummy-secret")
    monkeypatch.setenv("HARAVAN_RATE_LIMIT_PER_SEC", "100")
    monkeypatch.setenv("HARAVAN_RATE_LIMIT_BURST", "1000")
    monkeypatch.setenv("DATABASE_URL", "postgresql://x:x@localhost:5434/x")
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "")
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "")
    monkeypatch.setenv("LOG_FORMAT", "console")
