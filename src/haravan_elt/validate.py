"""Validate raw row counts against Haravan API `/count.json` endpoints.

Used by the `haravan-elt validate <domain>` CLI verb to sanity-check that
incremental loads aren't silently dropping rows. Default tolerance ±0.1%
absorbs short-window drift between the two queries.

Locations has no `/count.json` endpoint — fall back to the list length
since the dim is small (<100 rows).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

import psycopg
import structlog

if TYPE_CHECKING:
    from haravan_elt.client.haravan import HaravanClient

logger = structlog.get_logger(__name__)

DEFAULT_TOLERANCE = 0.001  # 0.1%

# Domains that expose a dedicated /count endpoint. The `locations` domain
# is intentionally absent and handled via list length below.
DOMAIN_COUNT_ENDPOINT: dict[str, str] = {
    "orders": "/com/orders/count.json",
    "customers": "/com/customers/count.json",
    "products": "/com/products/count.json",
    "inventory_adjustments": "/com/inventories/adjustments/count.json",
    "custom_collections": "/com/custom_collections/count.json",
    "smart_collections": "/com/smart_collections/count.json",
}

DOMAIN_RAW_TABLE: dict[str, str] = {
    "orders": "raw.haravan_orders",
    "customers": "raw.haravan_customers",
    "products": "raw.haravan_products",
    "locations": "raw.haravan_locations",
    "inventory_adjustments": "raw.haravan_inventory_adjustments",
    "custom_collections": "raw.haravan_custom_collections",
    "smart_collections": "raw.haravan_smart_collections",
}


@dataclass(frozen=True)
class ValidationResult:
    domain: str
    api_count: int
    db_count: int
    tolerance: float

    @property
    def ratio(self) -> float:
        return abs(self.api_count - self.db_count) / max(self.api_count, 1)

    @property
    def ok(self) -> bool:
        return self.ratio <= self.tolerance


def validate(
    domain: str,
    client: HaravanClient,
    dsn: str,
    *,
    tolerance: float = DEFAULT_TOLERANCE,
) -> ValidationResult:
    """Compare API count vs raw.* row count for `domain`.

    Raises `ValueError` for unknown domains. Network / DB errors propagate so
    the CLI surfaces them instead of silently passing.
    """
    if domain not in DOMAIN_RAW_TABLE:
        raise ValueError(f"unknown domain for validate: {domain!r}")

    api_count = _fetch_api_count(domain, client)
    db_count = _fetch_db_count(domain, dsn)
    result = ValidationResult(
        domain=domain, api_count=api_count, db_count=db_count, tolerance=tolerance
    )
    logger.info(
        "validate_compared",
        domain=domain,
        api_count=api_count,
        db_count=db_count,
        ratio=result.ratio,
        ok=result.ok,
    )
    return result


def _fetch_api_count(domain: str, client: HaravanClient) -> int:
    endpoint = DOMAIN_COUNT_ENDPOINT.get(domain)
    if endpoint:
        return int(client.get(endpoint).json().get("count", 0))
    if domain == "locations":
        # No dedicated /count endpoint — fall back to list length.
        items = client.get("/com/locations.json").json().get("locations", [])
        return len(items)
    raise ValueError(f"no API count strategy for domain: {domain!r}")


def _fetch_db_count(domain: str, dsn: str) -> int:
    table = DOMAIN_RAW_TABLE[domain]
    # Static map → safe to interpolate; psycopg.sql is overkill for a literal.
    sql = f"SELECT count(*) FROM {table}"  # noqa: S608 — table from static map
    with psycopg.connect(dsn) as conn, conn.cursor() as cur:
        cur.execute(sql)
        row = cur.fetchone()
    return int(row[0]) if row else 0
