"""Customers + Products extractor tests (parametrized).

OrdersExtractor has its own test file (status=any extra param). The other
two paginated P0 extractors share enough surface that one parametrized
test suite is sufficient.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any
from unittest.mock import MagicMock
from uuid import uuid4

import httpx
import pytest
import respx

from haravan_elt.client.haravan import HaravanClient
from haravan_elt.config import Settings
from haravan_elt.extractors.base import PaginatedListExtractor
from haravan_elt.extractors.customers import CustomersExtractor
from haravan_elt.extractors.products import PRODUCTS_PAGE_LIMIT, ProductsExtractor


def _build(
    cls: type[PaginatedListExtractor], page_limit: int = 2
) -> tuple[PaginatedListExtractor, MagicMock]:
    client = HaravanClient(Settings())
    loader = MagicMock()
    state = MagicMock()
    state.get_watermark = MagicMock(return_value=None)
    extractor = cls(client, loader, state, uuid4(), page_limit=page_limit)
    return extractor, loader


def test_products_default_page_limit_matches_haravan_cap(fake_settings_env: None) -> None:
    """Regression: Haravan caps `/com/products.json` at 50/page server-side."""
    del fake_settings_env
    ext = ProductsExtractor(HaravanClient(Settings()), MagicMock(), MagicMock(), uuid4())
    assert PRODUCTS_PAGE_LIMIT == 50
    assert ext._limit == 50


def _item(id_: int, updated_at: str = "2026-04-25T10:00:00Z") -> dict[str, Any]:
    return {"id": id_, "updated_at": updated_at, "name": f"item-{id_}"}


@pytest.mark.parametrize(
    ("cls", "endpoint", "wrapper"),
    [
        (CustomersExtractor, "https://apis.haravan.com/com/customers.json", "customers"),
        (ProductsExtractor, "https://apis.haravan.com/com/products.json", "products"),
    ],
    ids=["customers", "products"],
)
@respx.mock
def test_paginates_until_short_page(
    cls: type[PaginatedListExtractor],
    endpoint: str,
    wrapper: str,
    fake_settings_env: None,
) -> None:
    del fake_settings_env
    extractor, _ = _build(cls, page_limit=2)
    respx.get(endpoint, params={"page": 1}).mock(
        return_value=httpx.Response(200, json={wrapper: [_item(1), _item(2)]})
    )
    respx.get(endpoint, params={"page": 2}).mock(
        return_value=httpx.Response(200, json={wrapper: [_item(3)]})
    )
    pages = list(extractor.iter_pages())
    assert sum(len(p) for p in pages) == 3


@pytest.mark.parametrize(
    ("cls", "endpoint", "wrapper", "expected_table"),
    [
        (
            CustomersExtractor,
            "https://apis.haravan.com/com/customers.json",
            "customers",
            "raw.haravan_customers",
        ),
        (
            ProductsExtractor,
            "https://apis.haravan.com/com/products.json",
            "products",
            "raw.haravan_products",
        ),
    ],
    ids=["customers", "products"],
)
@respx.mock
def test_idempotent_load_writes_to_correct_table(
    cls: type[PaginatedListExtractor],
    endpoint: str,
    wrapper: str,
    expected_table: str,
    fake_settings_env: None,
) -> None:
    del fake_settings_env
    extractor, loader = _build(cls, page_limit=50)
    respx.get(endpoint).mock(return_value=httpx.Response(200, json={wrapper: [_item(1)]}))
    rows, max_ts = extractor.idempotent_load("full")
    assert rows == 1
    assert max_ts == datetime(2026, 4, 25, 10, 0, tzinfo=UTC)
    loader.upsert_batch.assert_called_once()
    args, _ = loader.upsert_batch.call_args
    assert args[0] == expected_table


def test_products_preserves_embedded_variants(fake_settings_env: None) -> None:
    """Variants ride along inside the product payload (research §8) — verify
    the JSONB envelope keeps the array intact for dbt to explode later."""
    del fake_settings_env
    extractor, _ = _build(ProductsExtractor, page_limit=50)
    item = {
        "id": 1,
        "updated_at": "2026-04-25T10:00:00Z",
        "title": "Tee",
        "variants": [
            {"id": 11, "sku": "tee-S"},
            {"id": 12, "sku": "tee-M"},
        ],
    }
    row = extractor.to_raw_row(item)
    assert row["payload"].obj == item
    assert row["payload"].obj["variants"][1]["sku"] == "tee-M"
