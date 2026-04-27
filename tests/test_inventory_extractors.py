"""Inventory adjustments + cartesian inventory_locations extractor tests.

Adjustments share the standard `PaginatedListExtractor` shape — a single
parametrized smoke test covers it. inventory_locations is custom: it queries
Postgres for location/variant ids then walks a cartesian product, so its
tests use respx + DB stubs to verify batching and snapshot row shape.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any
from unittest.mock import MagicMock
from uuid import uuid4

import httpx
import respx

from haravan_elt.client.haravan import HaravanClient
from haravan_elt.config import Settings
from haravan_elt.extractors.inventory_adjustments import (
    ADJUSTMENTS_PAGE_LIMIT,
    InventoryAdjustmentsExtractor,
)
from haravan_elt.extractors.inventory_locations import (
    DEFAULT_VARIANT_BATCH,
    InventoryLocationsExtractor,
)

# ----------------------------------------------------------- adjustments


def _make_adjustments_extractor(page_limit: int = 2) -> InventoryAdjustmentsExtractor:
    client = HaravanClient(Settings())
    loader = MagicMock()
    state = MagicMock()
    state.get_watermark = MagicMock(return_value=None)
    return InventoryAdjustmentsExtractor(client, loader, state, uuid4(), page_limit=page_limit)


@respx.mock
def test_inventory_adjustments_paginates_until_short_page(fake_settings_env: None) -> None:
    del fake_settings_env
    ext = _make_adjustments_extractor(page_limit=2)
    endpoint = "https://apis.haravan.com/com/inventories/adjustments.json"
    respx.get(endpoint, params={"page": 1}).mock(
        return_value=httpx.Response(
            200,
            json={
                "adjustments": [
                    {"id": 1, "updated_at": "2026-04-25T10:00:00Z", "variant_id": 11},
                    {"id": 2, "updated_at": "2026-04-25T10:05:00Z", "variant_id": 12},
                ]
            },
        )
    )
    respx.get(endpoint, params={"page": 2}).mock(
        return_value=httpx.Response(
            200,
            json={
                "adjustments": [
                    {"id": 3, "updated_at": "2026-04-25T10:10:00Z", "variant_id": 13}
                ]
            },
        )
    )
    pages = list(ext.iter_pages())
    flat = [item for p in pages for item in p]
    assert [it["id"] for it in flat] == [1, 2, 3]


@respx.mock
def test_inventory_adjustments_idempotent_load(fake_settings_env: None) -> None:
    del fake_settings_env
    ext = _make_adjustments_extractor(page_limit=50)
    respx.get("https://apis.haravan.com/com/inventories/adjustments.json").mock(
        return_value=httpx.Response(
            200,
            json={
                "adjustments": [
                    {"id": 7, "updated_at": "2026-04-25T10:00:00Z", "variant_id": 11}
                ]
            },
        )
    )
    rows, max_ts = ext.idempotent_load("full")
    assert rows == 1
    assert max_ts == datetime(2026, 4, 25, 10, 0, tzinfo=UTC)
    args, _ = ext.loader.upsert_batch.call_args
    assert args[0] == "raw.haravan_inventory_adjustments"


# ----------------------------------------------------------- inventory_locations


class _StubLoader:
    """Shim that satisfies the `dsn` accessor without touching real Postgres."""

    def __init__(self, dsn: str = "postgresql://stub/none") -> None:
        self.dsn = dsn
        self.upsert_batch = MagicMock(return_value=0)


def _build_inventory_locations(
    locations: list[int],
    variants: list[int],
    *,
    variant_batch: int = DEFAULT_VARIANT_BATCH,
) -> InventoryLocationsExtractor:
    client = HaravanClient(Settings())
    loader = _StubLoader()
    state = MagicMock()
    state.get_watermark = MagicMock(return_value=None)
    ext = InventoryLocationsExtractor(client, loader, state, uuid4(), variant_batch=variant_batch)
    # Bypass DB lookups — parametrized for batching tests.
    ext._fetch_location_ids = lambda: locations  # type: ignore[method-assign]
    ext._fetch_variant_ids = lambda: variants  # type: ignore[method-assign]
    return ext


def _inventory_payload(loc_id: int, variant_ids: list[int]) -> dict[str, Any]:
    """Mimic the API: one inventory_locations entry per (loc, variant).

    Real API uses `loc_id` (verified against live shop), not `location_id`.
    """
    return {
        "inventory_locations": [
            {
                "loc_id": loc_id,
                "variant_id": v,
                "available": 5,
                "on_hand": 7,
                "committed": 2,
            }
            for v in variant_ids
        ]
    }


@respx.mock
def test_inventory_locations_walks_full_cartesian(fake_settings_env: None) -> None:
    """All (loc, variant) pairs covered when batch == grid size."""
    del fake_settings_env
    locations = [101, 102]
    variants = [1, 2, 3]
    ext = _build_inventory_locations(locations, variants, variant_batch=10)

    endpoint = "https://apis.haravan.com/com/inventory_locations.json"
    for loc in locations:
        respx.get(endpoint, params={"location_ids": str(loc), "variant_ids": "1,2,3"}).mock(
            return_value=httpx.Response(200, json=_inventory_payload(loc, variants))
        )

    pages = list(ext.iter_pages())
    flat = [item for p in pages for item in p]
    pairs = {(it["loc_id"], it["variant_id"]) for it in flat}
    assert pairs == {(101, 1), (101, 2), (101, 3), (102, 1), (102, 2), (102, 3)}


@respx.mock
def test_inventory_locations_batches_variants_by_size(fake_settings_env: None) -> None:
    """Variants split into multiple chunks when count exceeds variant_batch."""
    del fake_settings_env
    locations = [101]
    variants = [1, 2, 3, 4, 5]
    ext = _build_inventory_locations(locations, variants, variant_batch=2)

    endpoint = "https://apis.haravan.com/com/inventory_locations.json"
    seen_chunks: list[str] = []

    def _capture(request: httpx.Request) -> httpx.Response:
        seen_chunks.append(dict(request.url.params)["variant_ids"])
        return httpx.Response(200, json={"inventory_locations": []})

    respx.get(endpoint).mock(side_effect=_capture)

    list(ext.iter_pages())
    assert seen_chunks == ["1,2", "3,4", "5"]


def test_inventory_locations_skipped_when_grid_empty(fake_settings_env: None) -> None:
    """No HTTP calls when either dimension of the grid is empty."""
    del fake_settings_env
    ext = _build_inventory_locations([], [1, 2])
    with respx.mock(assert_all_called=False) as mock_router:
        mock_router.route().mock(return_value=httpx.Response(200, json={}))
        assert list(ext.iter_pages()) == []
        assert mock_router.calls.call_count == 0


def test_inventory_locations_to_raw_row_uses_composite_pk(fake_settings_env: None) -> None:
    """Regression: Haravan returns `loc_id` (not `location_id`) on
    inventory_locations rows. Verified live 2026-04-27."""
    del fake_settings_env
    ext = _build_inventory_locations([101], [11])
    snap = ext._snapshot_date.isoformat()
    item = {"loc_id": 101, "variant_id": 11, "available": 4, "on_hand": 6}
    row = ext.to_raw_row(item)
    assert row["id"] == f"101:11:{snap}"
    assert row["location_id"] == 101
    assert row["variant_id"] == 11
    assert row["snapshot_date"] == ext._snapshot_date
    assert row["payload"].obj == item


def test_inventory_locations_default_variant_batch_matches_haravan_cap() -> None:
    """Regression: Haravan caps variant_ids at 50 per request — verified live
    via 422 response 'Tối đa chỉ được 50 biến thể' once batch >50."""
    assert DEFAULT_VARIANT_BATCH == 50


def test_inventory_adjustments_default_page_limit_matches_haravan_cap(
    fake_settings_env: None,
) -> None:
    """Regression: Haravan caps `/com/inventories/adjustments.json` at 50 rows
    per page server-side, same as orders/products. Verified live 2026-04-27."""
    del fake_settings_env
    # Build directly so we exercise the production default, not the
    # test-helper override (`_make_adjustments_extractor` injects page_limit=2).
    ext = InventoryAdjustmentsExtractor(
        HaravanClient(Settings()), MagicMock(), MagicMock(), uuid4()
    )
    assert ADJUSTMENTS_PAGE_LIMIT == 50
    assert ext._limit == 50


@respx.mock
def test_inventory_locations_idempotent_load_targets_correct_table(
    fake_settings_env: None,
) -> None:
    del fake_settings_env
    ext = _build_inventory_locations([101], [11], variant_batch=10)
    respx.get("https://apis.haravan.com/com/inventory_locations.json").mock(
        return_value=httpx.Response(200, json=_inventory_payload(101, [11]))
    )
    rows, _ = ext.idempotent_load("full")
    assert rows == 1
    args, _ = ext.loader.upsert_batch.call_args
    assert args[0] == "raw.haravan_inventory_locations"
