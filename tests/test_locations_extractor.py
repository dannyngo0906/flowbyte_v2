"""LocationsExtractor — full-refresh, single-fetch, no pagination."""

from __future__ import annotations

from datetime import UTC, datetime
from unittest.mock import MagicMock
from uuid import uuid4

import httpx
import respx

from haravan_elt.client.haravan import HaravanClient
from haravan_elt.config import Settings
from haravan_elt.extractors.locations import LocationsExtractor


def _build() -> tuple[LocationsExtractor, MagicMock]:
    client = HaravanClient(Settings())
    loader = MagicMock()
    state = MagicMock()
    state.get_watermark = MagicMock(return_value=None)
    return LocationsExtractor(client, loader, state, uuid4()), loader


@respx.mock
def test_single_fetch_yields_one_page(fake_settings_env: None) -> None:
    del fake_settings_env
    extractor, _ = _build()
    route = respx.get("https://apis.haravan.com/com/locations.json").mock(
        return_value=httpx.Response(
            200,
            json={
                "locations": [
                    {"id": 1, "name": "HCM", "updated_at": "2026-04-01T00:00:00Z"},
                    {"id": 2, "name": "HN", "updated_at": "2026-04-02T00:00:00Z"},
                ]
            },
        )
    )
    pages = list(extractor.iter_pages())
    assert len(pages) == 1
    assert len(pages[0]) == 2
    # Single request — pagination params NOT sent.
    assert route.call_count == 1


@respx.mock
def test_ignores_since_until(fake_settings_env: None) -> None:
    """Locations does full refresh — since/until kwargs must not change behavior."""
    del fake_settings_env
    extractor, _ = _build()
    route = respx.get("https://apis.haravan.com/com/locations.json").mock(
        return_value=httpx.Response(200, json={"locations": [{"id": 1}]})
    )
    list(
        extractor.iter_pages(
            since=datetime(2026, 4, 1, tzinfo=UTC),
            until=datetime(2026, 4, 30, tzinfo=UTC),
        )
    )
    sent_url = str(route.calls[0].request.url)
    assert "updated_at_min" not in sent_url
    assert "updated_at_max" not in sent_url


@respx.mock
def test_supports_incremental_is_false(fake_settings_env: None) -> None:
    del fake_settings_env
    extractor, _ = _build()
    assert extractor.supports_incremental is False


def test_to_raw_row_falls_back_when_no_timestamp(fake_settings_env: None) -> None:
    del fake_settings_env
    extractor, _ = _build()
    row = extractor.to_raw_row({"id": 99, "name": "Pop-up"})  # no updated_at
    assert row["id"] == 99
    assert row["updated_at"].tzinfo is not None  # tz-aware fallback


def test_to_raw_row_uses_modified_on_when_present(fake_settings_env: None) -> None:
    del fake_settings_env
    extractor, _ = _build()
    row = extractor.to_raw_row({"id": 1, "name": "Branch", "modified_on": "2026-04-25T10:00:00Z"})
    assert row["updated_at"] == datetime(2026, 4, 25, 10, 0, tzinfo=UTC)


@respx.mock
def test_idempotent_load_incremental_still_full_refresh(fake_settings_env: None) -> None:
    """`mode='incremental'` must NOT consult the state watermark for locations."""
    del fake_settings_env
    extractor, _ = _build()
    respx.get("https://apis.haravan.com/com/locations.json").mock(
        return_value=httpx.Response(
            200, json={"locations": [{"id": 1, "updated_at": "2026-04-25T00:00:00Z"}]}
        )
    )
    rows, _ = extractor.idempotent_load("incremental")
    assert rows == 1
    extractor.state.get_watermark.assert_not_called()
