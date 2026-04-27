"""OrdersExtractor — `/com/orders.json` end-to-end (PRD P0).

Refunds + transactions are EMBEDDED in the order JSON (research §7) so we
ingest the whole envelope as JSONB and explode the nested arrays in dbt
staging (phase-05). No separate fetch loops here.
"""

from __future__ import annotations

from types import MappingProxyType
from typing import Any

from haravan_elt.extractors.base import PaginatedListExtractor

# Haravan caps `/com/orders.json` at 50 rows/page server-side regardless of
# the requested `limit` — verified live 2026-04-27. Using a higher request
# limit makes the base-class short-page check terminate after page 1.
ORDERS_PAGE_LIMIT = 50


class OrdersExtractor(PaginatedListExtractor):
    domain = "orders"
    raw_table = "raw.haravan_orders"
    PATH = "/com/orders.json"
    RESPONSE_KEY = "orders"
    EXTRA_PARAMS = MappingProxyType({"status": "any"})

    def __init__(self, *args: Any, page_limit: int = ORDERS_PAGE_LIMIT, **kwargs: Any) -> None:
        super().__init__(*args, page_limit=page_limit, **kwargs)
