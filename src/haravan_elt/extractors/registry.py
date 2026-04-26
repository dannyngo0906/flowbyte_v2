"""Domain → extractor class registry + canonical run order.

Used by `cli.py extract` and the `run-all` orchestrator. Order matters
because dbt marts will join on FKs: locations + customers + products must
land before orders so dim references resolve cleanly. P2 domains
(discounts, promotions, events) come last — events specifically needs
locations/products already populated for any future correlation work.
"""

from __future__ import annotations

from haravan_elt.extractors.base import BaseExtractor
from haravan_elt.extractors.custom_collections import CustomCollectionsExtractor
from haravan_elt.extractors.customers import CustomersExtractor
from haravan_elt.extractors.discounts import DiscountsExtractor
from haravan_elt.extractors.events import EventsExtractor
from haravan_elt.extractors.inventory_adjustments import InventoryAdjustmentsExtractor
from haravan_elt.extractors.inventory_locations import InventoryLocationsExtractor
from haravan_elt.extractors.locations import LocationsExtractor
from haravan_elt.extractors.orders import OrdersExtractor
from haravan_elt.extractors.products import ProductsExtractor
from haravan_elt.extractors.promotions import PromotionsExtractor
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
    "discounts": DiscountsExtractor,
    "promotions": PromotionsExtractor,
    "events": EventsExtractor,
}

# Canonical run order: dims → P0/P1 facts → snapshot-cartesian → P2 domains.
DOMAIN_ORDER: list[str] = [
    "locations",
    "customers",
    "products",
    "custom_collections",
    "smart_collections",
    "orders",
    "inventory_adjustments",
    "inventory_locations",
    "discounts",
    "promotions",
    "events",
]
