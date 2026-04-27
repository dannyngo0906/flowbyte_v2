"""EventsExtractor — `/com/events.json`, append-only audit log.

Events are immutable once created; Haravan paginates by `since_id` rather
than `updated_at_min`. Watermark therefore tracks the largest event id seen
(`meta.sync_state.last_high_id`) instead of a timestamp. Re-running with
the same since_id yields zero new rows → idempotent.
"""

from __future__ import annotations

from collections.abc import Iterator
from datetime import datetime
from typing import Any

import structlog
from psycopg.types.json import Jsonb

from haravan_elt.extractors._helpers import parse_haravan_timestamp
from haravan_elt.extractors.base import BaseExtractor

logger = structlog.get_logger(__name__)

# Live API caps `/com/events.json` at 50 rows per request regardless of the
# requested limit (verified 2026-04-27 — same pattern as orders/products/
# inventory_adjustments). Setting limit=250 + the short-page check (`len <
# limit`) terminates after page 1 with only 50 events captured.
DEFAULT_PAGE_LIMIT = 50


class EventsExtractor(BaseExtractor):
    domain = "events"
    raw_table = "raw.haravan_events"
    # Pipeline still calls extract_one() — but we self-manage the watermark
    # via `last_high_id`, so override `idempotent_load` and return max_ts=None
    # to keep the pipeline's timestamp-watermark path inert for this domain.
    supports_incremental = True

    PATH = "/com/events.json"
    RESPONSE_KEY = "events"

    def __init__(self, *args: Any, page_limit: int = DEFAULT_PAGE_LIMIT, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self._limit = page_limit

    # ------------------------------------------------------------------ overrides

    def iter_pages(
        self,
        since: datetime | None = None,
        until: datetime | None = None,
    ) -> Iterator[list[dict[str, Any]]]:
        """Internal implementation uses `since_id` rather than timestamps —
        the `since`/`until` params are ignored. Real high-id resolution happens
        in `idempotent_load` so tests can drive `iter_pages_from_id` directly.
        """
        del since, until
        yield from self.iter_pages_from_id(0)

    def iter_pages_from_id(self, since_id: int) -> Iterator[list[dict[str, Any]]]:
        """Yield successive pages of events strictly newer than `since_id`."""
        cursor = since_id
        while True:
            params = {"since_id": cursor, "limit": self._limit}
            resp = self.client.get(self.PATH, params=params)
            items: list[dict[str, Any]] = resp.json().get(self.RESPONSE_KEY, [])
            if not items:
                return
            yield items
            cursor = max(int(item["id"]) for item in items)
            if len(items) < self._limit:
                return

    def to_raw_row(self, item: dict[str, Any]) -> dict[str, Any]:
        # Events lack `updated_at`; use `created_at` so the loader's
        # WHERE EXCLUDED.updated_at >= existing guard still functions.
        ts = parse_haravan_timestamp(item["created_at"])
        return {
            "id": item["id"],
            "payload": Jsonb(item),
            "updated_at": ts,
            "source_run_id": str(self.run_id),
        }

    def idempotent_load(
        self,
        mode: str,
        *,
        since: datetime | None = None,
        until: datetime | None = None,
    ) -> tuple[int, datetime | None]:
        """Resume from last_high_id; persist new high_id on success.

        Returns `(rows, None)` so pipeline.extract_one skips the timestamp
        watermark update path. State write happens here (at end) so a partial
        failure mid-pipeline doesn't advance the cursor.
        """
        del since, until
        start_id = 0
        if mode == "incremental":
            start_id = self.state.get_high_id(self.domain) or 0

        rows_total = 0
        max_id: int | None = None
        for page in self.iter_pages_from_id(start_id):
            rows = [self.to_raw_row(item) for item in page]
            if not rows:
                continue
            self.loader.upsert_batch(self.raw_table, rows, conflict_col=self.conflict_col)
            rows_total += len(rows)
            page_max = max(int(item["id"]) for item in page)
            max_id = page_max if max_id is None else max(max_id, page_max)

        # Persist watermark advance — only if we actually pulled new rows.
        if max_id is not None:
            self.state.update_high_id(self.domain, max_id, self.run_id)
        return rows_total, None
