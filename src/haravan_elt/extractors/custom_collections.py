"""CustomCollectionsExtractor — `/com/custom_collections.json`.

Manually-curated product collections. Small dim, paginated for parity but
typically <100 rows total. Joined into `dim_products` post-MVP.
"""

from __future__ import annotations

from haravan_elt.extractors.base import PaginatedListExtractor


class CustomCollectionsExtractor(PaginatedListExtractor):
    domain = "custom_collections"
    raw_table = "raw.haravan_custom_collections"
    PATH = "/com/custom_collections.json"
    RESPONSE_KEY = "custom_collections"
