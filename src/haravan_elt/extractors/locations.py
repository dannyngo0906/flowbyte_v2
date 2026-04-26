"""LocationsExtractor — `/com/locations.json`.

Small dim (typically <50 rows). Full refresh each run, no pagination, no
incremental filter (research §plan: locations endpoint may not even support
updated_at_min). `updated_at` falls back to current time if missing.
"""

from __future__ import annotations

from collections.abc import Iterator
from datetime import datetime
from typing import Any
from zoneinfo import ZoneInfo

from psycopg.types.json import Jsonb

from haravan_elt.extractors._helpers import parse_haravan_timestamp
from haravan_elt.extractors.base import BaseExtractor


class LocationsExtractor(BaseExtractor):
    domain = "locations"
    raw_table = "raw.haravan_locations"
    supports_incremental = False

    PATH = "/com/locations.json"
    RESPONSE_KEY = "locations"

    def iter_pages(
        self,
        since: datetime | None = None,
        until: datetime | None = None,
    ) -> Iterator[list[dict[str, Any]]]:
        del since, until  # not honored; locations is full refresh
        resp = self.client.get(self.PATH)
        items: list[dict[str, Any]] = resp.json().get(self.RESPONSE_KEY, [])
        if items:
            yield items

    def to_raw_row(self, item: dict[str, Any]) -> dict[str, Any]:
        ts_raw = item.get("updated_at") or item.get("modified_on")
        ts = parse_haravan_timestamp(ts_raw) if ts_raw else datetime.now(tz=ZoneInfo("UTC"))
        return {
            "id": item["id"],
            "payload": Jsonb(item),
            "updated_at": ts,
            "source_run_id": str(self.run_id),
        }
