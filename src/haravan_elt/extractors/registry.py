"""Domain → extractor class registry + canonical run order.

Used by `cli.py extract` and the `run-all` orchestrator. Order matters
because dbt marts will join on FKs: locations + customers + products must
land before orders so dim references resolve cleanly. Phase-09 adds 4 P1
domains; `inventory_locations` runs last because it reads
`raw.haravan_locations` + `raw.haravan_products` to drive its cartesian
fetch — those raw tables must already be populated.
"""

from __future__ import annotations

from haravan_elt.extractors.base import BaseExtractor
from haravan_elt.extractors.custom_collections import CustomCollectionsExtractor
from haravan_elt.extractors.customers import CustomersExtractor
from haravan_elt.extractors.inventory_adjustments import InventoryAdjustmentsExtractor
from haravan_elt.extractors.inventory_locations import InventoryLocationsExtractor
from haravan_elt.extractors.locations import LocationsExtractor
from haravan_elt.extractors.orders import OrdersExtractor
from haravan_elt.extractors.products import ProductsExtractor
from haravan_elt.extractors.smart_collections import SmartCollectionsExtractor

EXTRACTORS: dict[str, type[BaseExtractor]] = {
    "locations": LocationsExtractor,
    "customers": CustomersExtractor,
    "products": ProductsExtractor,
    "custom_collections": CustomCollectionsExtractor,
    "smart_collections": SmartCollectionsExtractor,
    "orders": OrdersExtractor,
    "inventory_adjustments": InventoryAdjustmentsExtractor,
    "inventory_locations": InventoryLocationsExtractor,
}

# Canonical run order: dims first, facts middle, snapshot-cartesian last.
DOMAIN_ORDER: list[str] = [
    "locations",
    "customers",
    "products",
    "custom_collections",
    "smart_collections",
    "orders",
    "inventory_adjustments",
    "inventory_locations",
]
