"""HaravanClient HTTP/auth/retry behavior.

Uses `respx` to mock httpx routes so we don't need live Haravan credentials
in CI. VCR cassettes against a real shop may be added in M6 hardening.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import httpx
import pytest
import respx

from haravan_elt.client.exceptions import (
    HaravanAuthError,
    HaravanServerError,
    HaravanTransientError,
    HaravanValidationError,
)
from haravan_elt.client.haravan import HaravanClient
from haravan_elt.config import Settings


@pytest.fixture
def client(
    tmp_path: Path,
    fake_settings_env: None,  # noqa: ARG001 — autouse-ish dependency
) -> HaravanClient:
    """Builds a client pointing at a throwaway .env so refresh writes don't leak."""
    env = tmp_path / ".env"
    env.write_text(
        "HARAVAN_ACCESS_TOKEN=dummy-access\nHARAVAN_REFRESH_TOKEN=dummy-refresh\n",
    )
    return HaravanClient(Settings(), env_path=env)


@respx.mock
def test_get_200_happy_path(client: HaravanClient) -> None:
    route = respx.get("https://apis.haravan.com/com/orders.json").mock(
        return_value=httpx.Response(
            200,
            json={"orders": []},
            headers={"X-Haravan-Api-Call-Limit": "10/80"},
        )
    )
    resp = client.get("/com/orders.json")
    assert resp.status_code == 200
    assert route.called


@respx.mock
def test_401_then_refresh_then_retry_succeeds(client: HaravanClient, tmp_path: Path) -> None:
    api_route = respx.get("https://apis.haravan.com/com/orders.json").mock(
        side_effect=[
            httpx.Response(401, json={"error": "expired"}),
            httpx.Response(200, json={"orders": []}),
        ]
    )
    refresh_route = respx.post("https://accounts.haravan.com/connect/token").mock(
        return_value=httpx.Response(
            200,
            json={"access_token": "new-access", "refresh_token": "new-refresh"},
        )
    )
    resp = client.get("/com/orders.json")
    assert resp.status_code == 200
    assert refresh_route.called
    assert api_route.call_count == 2
    # In-memory tokens rotated.
    assert client.settings.haravan.access_token.get_secret_value() == "new-access"
    assert client.settings.haravan.refresh_token.get_secret_value() == "new-refresh"
    # .env file updated.
    env_text = (tmp_path / ".env").read_text()
    assert "HARAVAN_ACCESS_TOKEN=new-access" in env_text
    assert "HARAVAN_REFRESH_TOKEN=new-refresh" in env_text


@respx.mock
def test_401_after_refresh_raises_auth_error(client: HaravanClient) -> None:
    respx.get("https://apis.haravan.com/com/orders.json").mock(
        return_value=httpx.Response(401, json={"error": "still bad"})
    )
    respx.post("https://accounts.haravan.com/connect/token").mock(
        return_value=httpx.Response(
            200, json={"access_token": "new-access", "refresh_token": "new-refresh"}
        )
    )
    with pytest.raises(HaravanAuthError):
        client.get("/com/orders.json")


@respx.mock
def test_429_retries_with_retry_after_then_succeeds(client: HaravanClient) -> None:
    route = respx.get("https://apis.haravan.com/com/orders.json").mock(
        side_effect=[
            httpx.Response(429, headers={"Retry-After": "0"}, text="slow down"),
            httpx.Response(200, json={"orders": []}),
        ]
    )
    # Patch sleep to make the test instant.
    with patch("haravan_elt.client.haravan.time.sleep"):
        resp = client.get("/com/orders.json")
    assert resp.status_code == 200
    assert route.call_count == 2


@respx.mock
def test_500_then_200_succeeds_via_tenacity(client: HaravanClient) -> None:
    route = respx.get("https://apis.haravan.com/com/orders.json").mock(
        side_effect=[
            httpx.Response(500, text="oops"),
            httpx.Response(200, json={"orders": []}),
        ]
    )
    with patch("haravan_elt.client.haravan.time.sleep"):
        resp = client.get("/com/orders.json")
    assert resp.status_code == 200
    assert route.call_count == 2


@respx.mock
def test_500_persistent_raises_after_retry_budget(client: HaravanClient) -> None:
    respx.get("https://apis.haravan.com/com/orders.json").mock(
        return_value=httpx.Response(500, text="still down")
    )
    with patch("haravan_elt.client.haravan.time.sleep"), pytest.raises(HaravanServerError):
        client.get("/com/orders.json")


@respx.mock
def test_400_validation_error_no_retry(client: HaravanClient) -> None:
    """400/403/404 etc. are caller bugs — NOT retried, surface immediately."""
    route = respx.get("https://apis.haravan.com/com/orders.json").mock(
        return_value=httpx.Response(400, json={"errors": "bad query"})
    )
    with pytest.raises(HaravanValidationError):
        client.get("/com/orders.json")
    assert route.call_count == 1


@respx.mock
def test_422_then_200_succeeds_via_tenacity(client: HaravanClient) -> None:
    """422 is treated as transient — verified live 2026-04-27 that the same
    products page returned 422 once then 200 on subsequent attempts."""
    route = respx.get("https://apis.haravan.com/com/orders.json").mock(
        side_effect=[
            httpx.Response(422, text="hiccup"),
            httpx.Response(200, json={"orders": []}),
        ]
    )
    with patch("haravan_elt.client.haravan.time.sleep"):
        resp = client.get("/com/orders.json")
    assert resp.status_code == 200
    assert route.call_count == 2


@respx.mock
def test_422_persistent_raises_after_retry_budget(client: HaravanClient) -> None:
    """If 422 truly persists across the retry budget, surface as transient
    error (not validation error) so callers can distinguish."""
    respx.get("https://apis.haravan.com/com/orders.json").mock(
        return_value=httpx.Response(422, text="still 422")
    )
    with patch("haravan_elt.client.haravan.time.sleep"), pytest.raises(HaravanTransientError):
        client.get("/com/orders.json")


@respx.mock
def test_refresh_failure_raises_auth(client: HaravanClient) -> None:
    respx.get("https://apis.haravan.com/com/orders.json").mock(return_value=httpx.Response(401))
    respx.post("https://accounts.haravan.com/connect/token").mock(
        return_value=httpx.Response(401, text="bad refresh token")
    )
    with pytest.raises(HaravanAuthError):
        client.get("/com/orders.json")


@respx.mock
def test_refresh_response_missing_access_token_raises_auth(client: HaravanClient) -> None:
    """If the token endpoint returns 200 but no access_token, surface as auth err."""
    respx.get("https://apis.haravan.com/com/orders.json").mock(return_value=httpx.Response(401))
    respx.post("https://accounts.haravan.com/connect/token").mock(
        return_value=httpx.Response(200, json={"token_type": "Bearer"})  # no access_token
    )
    with pytest.raises(HaravanAuthError, match="malformed refresh response"):
        client.get("/com/orders.json")


@respx.mock
def test_malformed_quota_header_does_not_crash(client: HaravanClient) -> None:
    """`_monitor_quota` must tolerate garbage values in `X-Haravan-Api-Call-Limit`."""
    respx.get("https://apis.haravan.com/com/orders.json").mock(
        return_value=httpx.Response(
            200, json={"orders": []}, headers={"X-Haravan-Api-Call-Limit": "abc/xyz"}
        )
    )
    resp = client.get("/com/orders.json")
    assert resp.status_code == 200  # quota parse failure is logged at DEBUG, not raised


@respx.mock
def test_limiter_saturation_blocks_instead_of_raising(client: HaravanClient) -> None:
    """Regression for review-phase-02 critical: pyrate-limiter saturation must
    sleep, not raise BucketFullException. We test by setting an artificially
    low limit fixture and firing N+1 requests in a tight loop."""
    from haravan_elt.client.rate_limit import make_limiter

    # 2 req/s limit so 3 calls in tight loop would saturate.
    client._limiter = make_limiter(rate_per_sec=2, burst_capacity=10)
    route = respx.get("https://apis.haravan.com/com/orders.json").mock(
        return_value=httpx.Response(200, json={"orders": []})
    )
    # Fire 5 requests; would raise BucketFullException under raise_when_fail=True.
    for _ in range(5):
        resp = client.get("/com/orders.json")
        assert resp.status_code == 200
    assert route.call_count == 5
