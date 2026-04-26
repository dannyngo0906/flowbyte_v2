"""Discounts + promotions + events extractor tests.

Discounts/promotions follow the standard PaginatedListExtractor pattern;
events uses id-ascending pagination via since_id and self-manages
last_high_id watermark — covered by dedicated cases below.
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
from haravan_elt.extractors.events import EventsExtractor
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


# ---------------------------------------------------------- discounts / promotions


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


# ---------------------------------------------------------- events


def _build_events(page_limit: int = 2, last_high_id: int | None = None) -> EventsExtractor:
    client = HaravanClient(Settings())
    loader = MagicMock()
    state = MagicMock()
    state.get_high_id = MagicMock(return_value=last_high_id)
    state.update_high_id = MagicMock()
    return EventsExtractor(client, loader, state, uuid4(), page_limit=page_limit)


def _event(id_: int) -> dict[str, Any]:
    return {
        "id": id_,
        "created_at": "2026-04-25T10:00:00Z",
        "subject_type": "Order",
        "verb": "create",
    }


@respx.mock
def test_events_paginates_id_ascending_until_short_page(fake_settings_env: None) -> None:
    del fake_settings_env
    ext = _build_events(page_limit=2)
    endpoint = "https://apis.haravan.com/com/events.json"
    respx.get(endpoint, params={"since_id": 0}).mock(
        return_value=httpx.Response(200, json={"events": [_event(1), _event(2)]})
    )
    respx.get(endpoint, params={"since_id": 2}).mock(
        return_value=httpx.Response(200, json={"events": [_event(3)]})
    )
    pages = list(ext.iter_pages_from_id(0))
    flat = [item for page in pages for item in page]
    assert [e["id"] for e in flat] == [1, 2, 3]


@respx.mock
def test_events_resumes_from_last_high_id(fake_settings_env: None) -> None:
    del fake_settings_env
    ext = _build_events(page_limit=50, last_high_id=42)
    endpoint = "https://apis.haravan.com/com/events.json"
    respx.get(endpoint, params={"since_id": 42}).mock(
        return_value=httpx.Response(200, json={"events": [_event(43), _event(44)]})
    )
    rows, max_ts = ext.idempotent_load("incremental")
    assert rows == 2
    # Returns max_ts=None so pipeline doesn't fire timestamp watermark update.
    assert max_ts is None
    ext.state.update_high_id.assert_called_once()
    args = ext.state.update_high_id.call_args.args
    assert args[0] == "events"
    assert args[1] == 44  # max id seen


@respx.mock
def test_events_idempotent_load_skips_state_write_when_no_rows(
    fake_settings_env: None,
) -> None:
    del fake_settings_env
    ext = _build_events(page_limit=50, last_high_id=99)
    respx.get("https://apis.haravan.com/com/events.json", params={"since_id": 99}).mock(
        return_value=httpx.Response(200, json={"events": []})
    )
    rows, _ = ext.idempotent_load("incremental")
    assert rows == 0
    ext.state.update_high_id.assert_not_called()


def test_events_to_raw_row_uses_created_at(fake_settings_env: None) -> None:
    """Events have no `updated_at` — loader's upsert guard reads created_at."""
    del fake_settings_env
    ext = _build_events()
    row = ext.to_raw_row(_event(7))
    assert row["id"] == 7
    assert row["updated_at"] is not None
