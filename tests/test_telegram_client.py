"""TelegramClient tests — fail-soft contract (PRD FR-N4).

Mix of respx (precise control over retry/timeout flows) and pytest-vcr
cassettes (recorded snapshots in `tests/fixtures/vcr/telegram_*`).
"""

from __future__ import annotations

import httpx
import pytest
import respx

from haravan_elt.client.telegram import TelegramClient


def test_disabled_when_token_or_chat_missing() -> None:
    assert not TelegramClient("", "123").enabled
    assert not TelegramClient("token", "").enabled
    assert not TelegramClient("", "").enabled
    assert TelegramClient("token", "123").enabled


@respx.mock
def test_send_returns_true_on_200() -> None:
    route = respx.post("https://api.telegram.org/botT/sendMessage").mock(
        return_value=httpx.Response(200, json={"ok": True})
    )
    client = TelegramClient("T", "123")
    assert client.send("hi") is True
    assert route.called


@respx.mock
def test_send_returns_false_on_400_no_raise_no_retry() -> None:
    """4xx is not retryable — exactly one POST."""
    route = respx.post("https://api.telegram.org/botT/sendMessage").mock(
        return_value=httpx.Response(400, json={"ok": False, "description": "bad chat"})
    )
    client = TelegramClient("T", "123")
    assert client.send("hi") is False  # MUST NOT raise
    assert route.call_count == 1


@respx.mock
def test_send_retries_once_on_5xx_then_succeeds(monkeypatch: pytest.MonkeyPatch) -> None:
    """5xx is retryable — first 500 then 200, both calls observed."""
    monkeypatch.setattr("haravan_elt.client.telegram.time.sleep", lambda _s: None)
    route = respx.post("https://api.telegram.org/botT/sendMessage").mock(
        side_effect=[httpx.Response(500), httpx.Response(200, json={"ok": True})]
    )
    client = TelegramClient("T", "123")
    assert client.send("hi") is True
    assert route.call_count == 2


@respx.mock
def test_send_returns_false_after_two_5xx(monkeypatch: pytest.MonkeyPatch) -> None:
    """Retry budget is 1 — two 5xx responses → False."""
    monkeypatch.setattr("haravan_elt.client.telegram.time.sleep", lambda _s: None)
    route = respx.post("https://api.telegram.org/botT/sendMessage").mock(
        return_value=httpx.Response(500)
    )
    client = TelegramClient("T", "123")
    assert client.send("hi") is False
    assert route.call_count == 2


@respx.mock
def test_send_returns_false_on_network_error_no_raise(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("haravan_elt.client.telegram.time.sleep", lambda _s: None)
    respx.post("https://api.telegram.org/botT/sendMessage").mock(
        side_effect=httpx.ConnectError("boom")
    )
    client = TelegramClient("T", "123")
    assert client.send("hi") is False


def test_send_short_circuits_when_disabled() -> None:
    client = TelegramClient("", "")
    # Will not even reach respx — no network call attempted.
    assert client.send("hi") is False


@respx.mock
def test_parse_mode_override() -> None:
    import json as _json

    route = respx.post("https://api.telegram.org/botT/sendMessage").mock(
        return_value=httpx.Response(200, json={"ok": True})
    )
    client = TelegramClient("T", "123", parse_mode="Markdown")
    client.send("x", parse_mode="MarkdownV2")
    body = _json.loads(route.calls.last.request.read())
    assert body["parse_mode"] == "MarkdownV2"


def test_md_escape_legacy_markdown() -> None:
    out = TelegramClient.md_escape("a_b*c[d`e")
    assert out == r"a\_b\*c\[d\`e"


def test_md_escape_v2_covers_extra_chars() -> None:
    out = TelegramClient.md_escape("a.b!c-d", v2=True)
    assert out == r"a\.b\!c\-d"


# ---------------------------------------------------------- VCR cassette tests
# pytest-vcr auto-derives cassette name from test function — we want the
# `telegram_send_ok.yaml` / `telegram_send_500.yaml` filenames per plan, so
# call vcr.use_cassette() directly with `record_mode='none'`.

import vcr  # noqa: E402

_CASSETTE_DIR = "tests/fixtures/vcr"


def test_send_against_recorded_200() -> None:
    with vcr.use_cassette(
        f"{_CASSETTE_DIR}/telegram_send_ok.yaml",
        record_mode="none",
        match_on=["method", "scheme", "host", "port", "path", "query"],
    ):
        client = TelegramClient("TEST_TOKEN", "999")
        assert client.send("hello") is True


def test_send_against_recorded_500_retries_then_fails(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr("haravan_elt.client.telegram.time.sleep", lambda _s: None)
    with vcr.use_cassette(
        f"{_CASSETTE_DIR}/telegram_send_500.yaml",
        record_mode="none",
        match_on=["method", "scheme", "host", "port", "path", "query"],
    ):
        client = TelegramClient("TEST_TOKEN", "999")
        assert client.send("hello") is False
