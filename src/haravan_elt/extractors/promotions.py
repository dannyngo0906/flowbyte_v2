"""PromotionsExtractor — `/com/promotions.json` (auto-applied campaigns)."""

from __future__ import annotations

from haravan_elt.extractors.base import PaginatedListExtractor


class PromotionsExtractor(PaginatedListExtractor):
    domain = "promotions"
    raw_table = "raw.haravan_promotions"
    PATH = "/com/promotions.json"
    RESPONSE_KEY = "promotions"
