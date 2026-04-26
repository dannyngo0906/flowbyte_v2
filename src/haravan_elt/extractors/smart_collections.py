"""SmartCollectionsExtractor — `/com/smart_collections.json`.

Rule-based product collections (auto-populated via tags / price / etc.).
Same shape as custom_collections — separate endpoint per Haravan API design.
"""

from __future__ import annotations

from haravan_elt.extractors.base import PaginatedListExtractor


class SmartCollectionsExtractor(PaginatedListExtractor):
    domain = "smart_collections"
    raw_table = "raw.haravan_smart_collections"
    PATH = "/com/smart_collections.json"
    RESPONSE_KEY = "smart_collections"
