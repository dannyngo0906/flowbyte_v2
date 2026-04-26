"""OrdersExtractor — `/com/orders.json` end-to-end (PRD P0).

Refunds + transactions are EMBEDDED in the order JSON (research §7) so we
ingest the whole envelope as JSONB and explode the nested arrays in dbt
staging (phase-05). No separate fetch loops here.
"""

from __future__ import annotations

from collections.abc import Iterator
from datetime import datetime
from typing import Any

from psycopg.types.json import Jsonb

from haravan_elt.extractors.base import BaseExtractor


def _parse_haravan_timestamp(value: str) -> datetime:
    """Haravan returns `2026-04-26T10:00:00Z` style — `fromisoformat` needs `+00:00`."""
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


class OrdersExtractor(BaseExtractor):
    domain = "orders"
    raw_table = "raw.haravan_orders"

    PATH = "/com/orders.json"

    def __init__(
        self,
        *args: Any,
        page_limit: int = 250,
        **kwargs: Any,
    ) -> None:
        super().__init__(*args, **kwargs)
        self._limit = page_limit

    def iter_pages(
        self,
        since: datetime | None = None,
        until: datetime | None = None,
    ) -> Iterator[list[dict[str, Any]]]:
        page = 1
        while True:
            params: dict[str, Any] = {
                "page": page,
                "limit": self._limit,
                "status": "any",
            }
            if since is not None:
                params["updated_at_min"] = since.isoformat()
            if until is not None:
                params["updated_at_max"] = until.isoformat()
            resp = self.client.get(self.PATH, params=params)
            items: list[dict[str, Any]] = resp.json().get("orders", [])
            if not items:
                return
            yield items
            # EOF: short page (research §2 — Haravan has no Link header / total).
            if len(items) < self._limit:
                return
            page += 1

    def to_raw_row(self, item: dict[str, Any]) -> dict[str, Any]:
        return {
            "id": item["id"],
            "payload": Jsonb(item),
            "updated_at": _parse_haravan_timestamp(item["updated_at"]),
            "source_run_id": str(self.run_id),
        }
