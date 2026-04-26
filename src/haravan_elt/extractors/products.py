"""ProductsExtractor — `/com/products.json`.

Variants are EMBEDDED in `payload->'variants'` (research §8). dbt
intermediate layer explodes via `jsonb_array_elements` (phase-06).
"""

from __future__ import annotations

from haravan_elt.extractors.base import PaginatedListExtractor


class ProductsExtractor(PaginatedListExtractor):
    domain = "products"
    raw_table = "raw.haravan_products"
    PATH = "/com/products.json"
    RESPONSE_KEY = "products"
