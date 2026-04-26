"""Domain → extractor class registry + canonical run order.

Used by `cli.py extract` and (phase-07) the `run-all` orchestrator. Order
matters because dbt marts will join on FKs: locations + customers + products
must land before orders so dim references resolve cleanly.
"""

from __future__ import annotations

from haravan_elt.extractors.base import BaseExtractor
from haravan_elt.extractors.customers import CustomersExtractor
from haravan_elt.extractors.locations import LocationsExtractor
from haravan_elt.extractors.orders import OrdersExtractor
from haravan_elt.extractors.products import ProductsExtractor

EXTRACTORS: dict[str, type[BaseExtractor]] = {
    "locations": LocationsExtractor,
    "customers": CustomersExtractor,
    "products": ProductsExtractor,
    "orders": OrdersExtractor,
}

# Canonical run order: dims first, facts last.
DOMAIN_ORDER: list[str] = ["locations", "customers", "products", "orders"]
