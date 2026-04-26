"""Custom + smart collections extractor smoke tests.

Both share the standard `PaginatedListExtractor` shape, so a single
parametrized suite covers pagination + raw_table targeting.
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
from haravan_elt.extractors.custom_collections import CustomCollectionsExtractor
from haravan_elt.extractors.smart_collections import SmartCollectionsExtractor


def _build(cls: type[PaginatedListExtractor], page_limit: int = 50) -> PaginatedListExtractor:
    client = HaravanClient(Settings())
    loader = MagicMock()
    state = MagicMock()
    state.get_watermark = MagicMock(return_value=None)
    return cls(client, loader, state, uuid4(), page_limit=page_limit)


def _item(id_: int) -> dict[str, Any]:
    return {
        "id": id_,
        "updated_at": "2026-04-25T10:00:00Z",
        "title": f"col-{id_}",
        "handle": f"col-{id_}",
    }


@pytest.mark.parametrize(
    ("cls", "endpoint", "wrapper", "expected_table"),
    [
        (
            CustomCollectionsExtractor,
            "https://apis.haravan.com/com/custom_collections.json",
            "custom_collections",
            "raw.haravan_custom_collections",
        ),
        (
            SmartCollectionsExtractor,
            "https://apis.haravan.com/com/smart_collections.json",
            "smart_collections",
            "raw.haravan_smart_collections",
        ),
    ],
    ids=["custom", "smart"],
)
@respx.mock
def test_collections_extract_targets_correct_raw_table(
    cls: type[PaginatedListExtractor],
    endpoint: str,
    wrapper: str,
    expected_table: str,
    fake_settings_env: None,
) -> None:
    del fake_settings_env
    extractor = _build(cls, page_limit=50)
    respx.get(endpoint).mock(return_value=httpx.Response(200, json={wrapper: [_item(1)]}))
    rows, _ = extractor.idempotent_load("full")
    assert rows == 1
    args, _ = extractor.loader.upsert_batch.call_args
    assert args[0] == expected_table
