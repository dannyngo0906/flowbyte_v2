"""Validate command tests — count comparison.

ValidationResult is a pure dataclass — exercise its math directly without
hitting Postgres or the network. The two `validate(...)` end-to-end tests
patch `_fetch_db_count` and use respx for the API count.
"""

from __future__ import annotations

import httpx
import pytest
import respx

from haravan_elt.client.haravan import HaravanClient
from haravan_elt.config import Settings
from haravan_elt.validate import (
    DEFAULT_TOLERANCE,
    DOMAIN_RAW_TABLE,
    ValidationResult,
    validate,
)


def test_result_ok_when_within_tolerance() -> None:
    r = ValidationResult(domain="orders", api_count=1000, db_count=999, tolerance=DEFAULT_TOLERANCE)
    assert r.ratio == pytest.approx(0.001)
    assert r.ok is True


def test_result_mismatch_when_above_tolerance() -> None:
    r = ValidationResult(domain="orders", api_count=1000, db_count=900, tolerance=DEFAULT_TOLERANCE)
    assert r.ok is False


def test_result_handles_zero_api_count() -> None:
    r = ValidationResult(domain="orders", api_count=0, db_count=0, tolerance=0.001)
    assert r.ok is True
    assert r.ratio == 0.0


def test_unknown_domain_raises() -> None:
    client = HaravanClient(Settings())
    with pytest.raises(ValueError, match="unknown domain"):
        validate("nope", client, "postgresql://x/y")


@respx.mock
def test_validate_orders_ok(monkeypatch: pytest.MonkeyPatch, fake_settings_env: None) -> None:
    del fake_settings_env
    monkeypatch.setattr("haravan_elt.validate._fetch_db_count", lambda _domain, _dsn: 1000)
    respx.get("https://apis.haravan.com/com/orders/count.json").mock(
        return_value=httpx.Response(200, json={"count": 1000})
    )
    client = HaravanClient(Settings())
    result = validate("orders", client, "postgresql://stub")
    assert result.ok is True
    assert result.api_count == 1000
    assert result.db_count == 1000


@respx.mock
def test_validate_mismatch_returns_not_ok(
    monkeypatch: pytest.MonkeyPatch, fake_settings_env: None
) -> None:
    del fake_settings_env
    monkeypatch.setattr("haravan_elt.validate._fetch_db_count", lambda _domain, _dsn: 800)
    respx.get("https://apis.haravan.com/com/orders/count.json").mock(
        return_value=httpx.Response(200, json={"count": 1000})
    )
    client = HaravanClient(Settings())
    result = validate("orders", client, "postgresql://stub", tolerance=0.01)
    assert result.ok is False
    assert result.api_count == 1000
    assert result.db_count == 800


@respx.mock
def test_validate_locations_uses_list_length_fallback(
    monkeypatch: pytest.MonkeyPatch, fake_settings_env: None
) -> None:
    """Locations has no /count endpoint — fall back to len(list)."""
    del fake_settings_env
    monkeypatch.setattr("haravan_elt.validate._fetch_db_count", lambda _d, _dsn: 3)
    respx.get("https://apis.haravan.com/com/locations.json").mock(
        return_value=httpx.Response(
            200,
            json={"locations": [{"id": 1}, {"id": 2}, {"id": 3}]},
        )
    )
    client = HaravanClient(Settings())
    result = validate("locations", client, "postgresql://stub")
    assert result.api_count == 3
    assert result.db_count == 3
    assert result.ok is True


def test_all_known_domains_have_count_strategy() -> None:
    """Every domain with a raw table must have an API count strategy
    (either /count.json or the locations list-length fallback)."""
    from haravan_elt.validate import DOMAIN_COUNT_ENDPOINT

    for domain in DOMAIN_RAW_TABLE:
        has_endpoint = domain in DOMAIN_COUNT_ENDPOINT
        is_locations = domain == "locations"
        assert has_endpoint or is_locations, f"no API count strategy for {domain}"
