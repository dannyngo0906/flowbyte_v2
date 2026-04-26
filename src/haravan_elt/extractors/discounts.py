"""DiscountsExtractor — `/com/discounts.json` (code-based discounts).

Pairs with `promotions.py` (auto-applied campaigns) for full coverage of
Haravan's two discount surfaces. Standard paginated `updated_at_min` pattern
if the endpoint honors it; otherwise full refresh runs are still idempotent.
"""

from __future__ import annotations

from haravan_elt.extractors.base import PaginatedListExtractor


class DiscountsExtractor(PaginatedListExtractor):
    domain = "discounts"
    raw_table = "raw.haravan_discounts"
    PATH = "/com/discounts.json"
    RESPONSE_KEY = "discounts"
