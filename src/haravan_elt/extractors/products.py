"""ProductsExtractor — `/com/products.json`.

Variants are EMBEDDED in `payload->'variants'` (research §8). dbt
intermediate layer explodes via `jsonb_array_elements` (phase-06).
"""

from __future__ import annotations

from typing import Any

from haravan_elt.extractors.base import PaginatedListExtractor

# Haravan caps `/com/products.json` at 50 rows/page server-side regardless
# of the requested `limit` — verified live 2026-04-27. Using a higher
# request limit makes the base-class short-page check terminate after page 1.
PRODUCTS_PAGE_LIMIT = 50


class ProductsExtractor(PaginatedListExtractor):
    domain = "products"
    raw_table = "raw.haravan_products"
    PATH = "/com/products.json"
    RESPONSE_KEY = "products"

    def __init__(self, *args: Any, page_limit: int = PRODUCTS_PAGE_LIMIT, **kwargs: Any) -> None:
        super().__init__(*args, page_limit=page_limit, **kwargs)
