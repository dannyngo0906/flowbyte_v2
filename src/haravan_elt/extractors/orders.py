"""OrdersExtractor — `/com/orders.json` end-to-end (PRD P0).

Refunds + transactions are EMBEDDED in the order JSON (research §7) so we
ingest the whole envelope as JSONB and explode the nested arrays in dbt
staging (phase-05). No separate fetch loops here.
"""

from __future__ import annotations

from types import MappingProxyType

from haravan_elt.extractors.base import PaginatedListExtractor


class OrdersExtractor(PaginatedListExtractor):
    domain = "orders"
    raw_table = "raw.haravan_orders"
    PATH = "/com/orders.json"
    RESPONSE_KEY = "orders"
    EXTRA_PARAMS = MappingProxyType({"status": "any"})
