"""InventoryAdjustmentsExtractor — `/com/inventories/adjustments.json`.

Each adjustment is a stocktake event with N nested `line_items` (variant +
qty + cost). The dbt staging layer explodes line_items so the mart
maintains per-(adjustment, variant) grain.

Verified live 2026-04-27: API wraps response under `adjustments` (NOT
`inventory_adjustments` as PRD initially assumed) and nests variant data
inside `line_items[].product_variant_id`.
"""

from __future__ import annotations

from haravan_elt.extractors.base import PaginatedListExtractor


class InventoryAdjustmentsExtractor(PaginatedListExtractor):
    domain = "inventory_adjustments"
    raw_table = "raw.haravan_inventory_adjustments"
    PATH = "/com/inventories/adjustments.json"
    # Live API returns `{"adjustments": [...]}` — using the original
    # `inventory_adjustments` key silently produced 0 rows on every run.
    RESPONSE_KEY = "adjustments"
