"""CustomersExtractor — `/com/customers.json`."""

from __future__ import annotations

from haravan_elt.extractors.base import PaginatedListExtractor


class CustomersExtractor(PaginatedListExtractor):
    domain = "customers"
    raw_table = "raw.haravan_customers"
    PATH = "/com/customers.json"
    RESPONSE_KEY = "customers"
