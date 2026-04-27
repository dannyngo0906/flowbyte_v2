"""InventoryLocationsExtractor — `/com/inventory_locations.json` cartesian fetch.

Haravan exposes inventory balance only via per-(location, variant) tuples and
requires both `location_ids` AND `variant_ids` query params. This extractor
reads location_ids from `raw.haravan_locations` and variant_ids from
`raw.haravan_products` JSONB (variants[] array), then walks the cartesian
product in batches of 100 variants per request.

Snapshot model: each run records a row per (location, variant, run_date) with
PK = "location:variant:date". Replays on same day overwrite via the loader's
ON CONFLICT path; following days produce new snapshot rows. The
`fct_inventory_snapshot` mart consumes this directly.

`supports_incremental = False` — there is no API watermark; we always sweep
the full grid.
"""

from __future__ import annotations

from collections.abc import Iterator
from datetime import UTC, datetime
from typing import Any

import psycopg
import structlog
from psycopg.types.json import Jsonb

from haravan_elt.extractors.base import BaseExtractor

logger = structlog.get_logger(__name__)

# Haravan caps `variant_ids` at 50 per request — verified live (422
# "Tối đa chỉ được 50 biến thể" once batch exceeds 50). URL length not the
# binding constraint; the server-side limit is.
DEFAULT_VARIANT_BATCH = 50


class InventoryLocationsExtractor(BaseExtractor):
    domain = "inventory_locations"
    raw_table = "raw.haravan_inventory_locations"
    supports_incremental = False

    PATH = "/com/inventory_locations.json"
    RESPONSE_KEY = "inventory_locations"

    def __init__(
        self,
        *args: Any,
        variant_batch: int = DEFAULT_VARIANT_BATCH,
        **kwargs: Any,
    ) -> None:
        super().__init__(*args, **kwargs)
        self._variant_batch = variant_batch
        # Stamped once per run — keeps snapshot_date stable across all batches
        # of a single extract, even if the run crosses midnight.
        self._snapshot_date = datetime.now(tz=UTC).date()

    def iter_pages(
        self,
        since: datetime | None = None,
        until: datetime | None = None,
    ) -> Iterator[list[dict[str, Any]]]:
        del since, until  # snapshot-only domain; no API watermark
        location_ids = self._fetch_location_ids()
        variant_ids = self._fetch_variant_ids()
        if not location_ids or not variant_ids:
            logger.warning(
                "inventory_locations_skipped_empty_grid",
                locations=len(location_ids),
                variants=len(variant_ids),
            )
            return
        for loc_id in location_ids:
            for start in range(0, len(variant_ids), self._variant_batch):
                chunk = variant_ids[start : start + self._variant_batch]
                params = {
                    "location_ids": loc_id,
                    "variant_ids": ",".join(str(v) for v in chunk),
                }
                resp = self.client.get(self.PATH, params=params)
                items: list[dict[str, Any]] = resp.json().get(self.RESPONSE_KEY, [])
                if items:
                    yield items

    def to_raw_row(self, item: dict[str, Any]) -> dict[str, Any]:
        # Haravan API returns `loc_id`, not `location_id`.
        loc_id = item["loc_id"]
        var_id = item["variant_id"]
        snap = self._snapshot_date
        return {
            "id": f"{loc_id}:{var_id}:{snap.isoformat()}",
            "location_id": loc_id,
            "variant_id": var_id,
            "snapshot_date": snap,
            "payload": Jsonb(item),
            "updated_at": datetime.now(tz=UTC),
            "source_run_id": str(self.run_id),
        }

    # -------------------------------------------------------------- helpers

    def _fetch_location_ids(self) -> list[int]:
        with psycopg.connect(self.loader.dsn) as conn, conn.cursor() as cur:
            cur.execute("SELECT id FROM raw.haravan_locations ORDER BY id")
            return [row[0] for row in cur.fetchall()]

    def _fetch_variant_ids(self) -> list[int]:
        """Pull variant ids from the products JSONB. Deduped + sorted to keep
        cartesian iteration order deterministic across runs."""
        sql = """
            SELECT DISTINCT (variant->>'id')::bigint AS variant_id
            FROM raw.haravan_products,
                 LATERAL jsonb_array_elements(payload->'variants') AS variant
            WHERE variant ? 'id'
            ORDER BY variant_id
        """
        with psycopg.connect(self.loader.dsn) as conn, conn.cursor() as cur:
            cur.execute(sql)
            return [row[0] for row in cur.fetchall()]
