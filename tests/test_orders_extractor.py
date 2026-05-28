"""OrdersExtractor unit tests — `respx` mocks the API; loader/state are stubs."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any
from unittest.mock import MagicMock
from uuid import uuid4

import httpx
import respx

from haravan_elt.client.haravan import HaravanClient
from haravan_elt.config import Settings
from haravan_elt.extractors.base import LATE_ARRIVAL_BUFFER_DAYS
from haravan_elt.extractors.orders import ORDERS_PAGE_LIMIT, OrdersExtractor


def _make_extractor(page_limit: int = 50) -> tuple[OrdersExtractor, MagicMock, MagicMock]:
    settings = Settings()
    client = HaravanClient(settings)
    loader = MagicMock()
    loader.upsert_batch = MagicMock(return_value=0)
    state = MagicMock()
    state.get_watermark = MagicMock(return_value=None)
    extractor = OrdersExtractor(client, loader, state, uuid4(), page_limit=page_limit)
    return extractor, loader, state


def _order(id_: int, updated_at: str = "2026-04-25T10:00:00Z") -> dict[str, Any]:
    return {"id": id_, "updated_at": updated_at, "name": f"#{id_}"}


def test_orders_default_page_limit_matches_haravan_cap(fake_settings_env: None) -> None:
    """Regression: Haravan caps `/com/orders.json` at 50/page server-side. If
    we ever bump default back to 250, the base-class short-page check will
    terminate after page 1 (`50 < 250`)."""
    del fake_settings_env
    settings = Settings()
    client = HaravanClient(settings)
    ext = OrdersExtractor(client, MagicMock(), MagicMock(), uuid4())
    assert ORDERS_PAGE_LIMIT == 50
    assert ext._limit == 50


@respx.mock
def test_iterates_until_short_page(fake_settings_env: None) -> None:  # noqa: ARG001
    del fake_settings_env
    extractor, _, _ = _make_extractor(page_limit=2)
    respx.get("https://apis.haravan.com/com/orders.json", params={"page": 1}).mock(
        return_value=httpx.Response(200, json={"orders": [_order(1), _order(2)]})
    )
    respx.get("https://apis.haravan.com/com/orders.json", params={"page": 2}).mock(
        return_value=httpx.Response(200, json={"orders": [_order(3)]})  # short → stop
    )
    pages = list(extractor.iter_pages())
    assert len(pages) == 2
    assert [item["id"] for page in pages for item in page] == [1, 2, 3]


@respx.mock
def test_iter_stops_on_empty_first_page(fake_settings_env: None) -> None:  # noqa: ARG001
    del fake_settings_env
    extractor, _, _ = _make_extractor(page_limit=50)
    respx.get("https://apis.haravan.com/com/orders.json").mock(
        return_value=httpx.Response(200, json={"orders": []})
    )
    pages = list(extractor.iter_pages())
    assert pages == []


@respx.mock
def test_passes_since_until_filters(fake_settings_env: None) -> None:  # noqa: ARG001
    del fake_settings_env
    extractor, _, _ = _make_extractor(page_limit=50)
    route = respx.get("https://apis.haravan.com/com/orders.json").mock(
        return_value=httpx.Response(200, json={"orders": []})
    )
    since = datetime(2026, 4, 1, tzinfo=UTC)
    until = datetime(2026, 4, 30, tzinfo=UTC)
    list(extractor.iter_pages(since=since, until=until))
    sent_url = str(route.calls[0].request.url)
    assert "updated_at_min=2026-04-01" in sent_url
    assert "updated_at_max=2026-04-30" in sent_url


def test_to_raw_row_shape(fake_settings_env: None) -> None:  # noqa: ARG001
    del fake_settings_env
    extractor, _, _ = _make_extractor()
    item = _order(42, updated_at="2026-04-25T10:00:00Z")
    row = extractor.to_raw_row(item)
    assert row["id"] == 42
    assert row["updated_at"] == datetime(2026, 4, 25, 10, 0, tzinfo=UTC)
    assert str(row["source_run_id"]) == str(extractor.run_id)
    # Jsonb wrapper is opaque — verify by re-extracting `.obj`.
    assert row["payload"].obj == item


@respx.mock
def test_idempotent_load_full_writes_via_loader(fake_settings_env: None) -> None:  # noqa: ARG001
    del fake_settings_env
    extractor, loader, _ = _make_extractor(page_limit=50)
    respx.get("https://apis.haravan.com/com/orders.json").mock(
        return_value=httpx.Response(200, json={"orders": [_order(1), _order(2)]})
    )
    rows, max_ts = extractor.idempotent_load("full")
    assert rows == 2
    assert max_ts == datetime(2026, 4, 25, 10, 0, tzinfo=UTC)
    loader.upsert_batch.assert_called_once()
    args, kwargs = loader.upsert_batch.call_args
    assert args[0] == "raw.haravan_orders"
    assert kwargs.get("conflict_col") == "id"


@respx.mock
def test_incremental_uses_state_watermark(fake_settings_env: None) -> None:  # noqa: ARG001
    del fake_settings_env
    extractor, _, state = _make_extractor(page_limit=50)
    watermark = datetime(2026, 4, 1, tzinfo=UTC)
    state.get_watermark.return_value = watermark
    route = respx.get("https://apis.haravan.com/com/orders.json").mock(
        return_value=httpx.Response(200, json={"orders": []})
    )
    extractor.idempotent_load("incremental")
    state.get_watermark.assert_called_once_with("orders")
    # since floor = watermark - late-arrival buffer (2026-04-01 → 2026-03-25)
    expected = (watermark - timedelta(days=LATE_ARRIVAL_BUFFER_DAYS)).date().isoformat()
    assert f"updated_at_min={expected}" in str(route.calls[0].request.url)
