"""TelegramClient tests — fail-soft contract (PRD FR-N4)."""

from __future__ import annotations

import httpx
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
def test_send_returns_false_on_400_no_raise() -> None:
    respx.post("https://api.telegram.org/botT/sendMessage").mock(
        return_value=httpx.Response(400, json={"ok": False, "description": "bad chat"})
    )
    client = TelegramClient("T", "123")
    assert client.send("hi") is False  # MUST NOT raise


@respx.mock
def test_send_returns_false_on_network_error_no_raise() -> None:
    respx.post("https://api.telegram.org/botT/sendMessage").mock(
        side_effect=httpx.ConnectError("boom")
    )
    client = TelegramClient("T", "123")
    assert client.send("hi") is False


def test_send_short_circuits_when_disabled() -> None:
    client = TelegramClient("", "")
    # Will not even reach respx — no network call attempted.
    assert client.send("hi") is False
