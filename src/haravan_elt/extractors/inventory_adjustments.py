"""InventoryAdjustmentsExtractor — `/com/inventories/adjustments.json`.

Each adjustment is a stocktake event with N nested `line_items` (variant +
qty + cost). The dbt staging layer explodes line_items so the mart
maintains per-(adjustment, variant) grain.

Verified live 2026-04-27 against boshop-8.myharavan.com:
- API wraps response under `adjustments` (NOT `inventory_adjustments`).
- Variant data is nested in `line_items[].product_variant_id`.
- Per-page server cap = 50 rows regardless of requested limit (same
  behavior as orders/products endpoints) — using the base 250 default
  terminates after page 1 because 50 < 250.
"""

from __future__ import annotations

from typing import Any

from haravan_elt.extractors.base import PaginatedListExtractor

ADJUSTMENTS_PAGE_LIMIT = 50


class InventoryAdjustmentsExtractor(PaginatedListExtractor):
    domain = "inventory_adjustments"
    raw_table = "raw.haravan_inventory_adjustments"
    PATH = "/com/inventories/adjustments.json"
    # Live API returns `{"adjustments": [...]}` — using the original
    # `inventory_adjustments` key silently produced 0 rows on every run.
    RESPONSE_KEY = "adjustments"

    def __init__(
        self, *args: Any, page_limit: int = ADJUSTMENTS_PAGE_LIMIT, **kwargs: Any
    ) -> None:
        super().__init__(*args, page_limit=page_limit, **kwargs)
