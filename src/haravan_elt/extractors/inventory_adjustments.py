"""InventoryAdjustmentsExtractor — `/com/inventories/adjustments.json`.

Standard offset-paginated `updated_at_min` pattern. Each adjustment carries
a delta (+ buy / - sell / manual fix) referenced by variant_id + location_id.
Mart materializes one row per adjustment in `fct_inventory_adjustments`.
"""

from __future__ import annotations

from haravan_elt.extractors.base import PaginatedListExtractor


class InventoryAdjustmentsExtractor(PaginatedListExtractor):
    domain = "inventory_adjustments"
    raw_table = "raw.haravan_inventory_adjustments"
    PATH = "/com/inventories/adjustments.json"
    RESPONSE_KEY = "inventory_adjustments"
