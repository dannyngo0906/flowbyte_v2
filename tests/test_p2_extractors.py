"""Discounts + promotions extractor tests.

Both follow the standard PaginatedListExtractor pattern.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import MagicMock
from uuid import uuid4

import httpx
import pytest
import respx

from haravan_elt.client.haravan import HaravanClient
from haravan_elt.config import Settings
from haravan_elt.extractors.base import PaginatedListExtractor
from haravan_elt.extractors.discounts import DiscountsExtractor
from haravan_elt.extractors.promotions import PromotionsExtractor


def _build_paginated(
    cls: type[PaginatedListExtractor], page_limit: int = 50
) -> PaginatedListExtractor:
    client = HaravanClient(Settings())
    loader = MagicMock()
    state = MagicMock()
    state.get_watermark = MagicMock(return_value=None)
    return cls(client, loader, state, uuid4(), page_limit=page_limit)


def _row(id_: int, **extra: Any) -> dict[str, Any]:
    base: dict[str, Any] = {"id": id_, "updated_at": "2026-04-25T10:00:00Z"}
    base.update(extra)
    return base


@pytest.mark.parametrize(
    ("cls", "endpoint", "wrapper", "expected_table"),
    [
        (
            DiscountsExtractor,
            "https://apis.haravan.com/com/discounts.json",
            "discounts",
            "raw.haravan_discounts",
        ),
        (
            PromotionsExtractor,
            "https://apis.haravan.com/com/promotions.json",
            "promotions",
            "raw.haravan_promotions",
        ),
    ],
    ids=["discounts", "promotions"],
)
@respx.mock
def test_p2_paginated_extract_targets_correct_table(
    cls: type[PaginatedListExtractor],
    endpoint: str,
    wrapper: str,
    expected_table: str,
    fake_settings_env: None,
) -> None:
    del fake_settings_env
    extractor = _build_paginated(cls, page_limit=50)
    respx.get(endpoint).mock(return_value=httpx.Response(200, json={wrapper: [_row(1)]}))
    rows, _ = extractor.idempotent_load("full")
    assert rows == 1
    args, _ = extractor.loader.upsert_batch.call_args
    assert args[0] == expected_table
