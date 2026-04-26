"""Abstract BaseExtractor + the shared idempotent-load orchestration.

Concrete extractors implement `iter_pages` (HTTP pagination) and `to_raw_row`
(API-item → upsert-row mapper). The reusable load loop lives here so all
domains get identical batching, watermark, and run-log semantics.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Iterator
from datetime import datetime
from typing import TYPE_CHECKING, Any
from uuid import UUID

if TYPE_CHECKING:
    from haravan_elt.client.haravan import HaravanClient
    from haravan_elt.loaders.postgres import PostgresLoader
    from haravan_elt.meta.state import StateManager


class BaseExtractor(ABC):
    """Base contract every domain extractor must satisfy.

    Subclasses set:
        domain      - identifier used in `meta.sync_state` and `meta.run_log`
        raw_table   - target qualified table name, e.g. "raw.haravan_orders"
        conflict_col - PK column for ON CONFLICT (defaults to "id")
    """

    domain: str
    raw_table: str
    conflict_col: str = "id"

    def __init__(
        self,
        client: HaravanClient,
        loader: PostgresLoader,
        state: StateManager,
        run_id: UUID,
    ) -> None:
        self.client = client
        self.loader = loader
        self.state = state
        self.run_id = run_id

    @abstractmethod
    def iter_pages(
        self,
        since: datetime | None = None,
        until: datetime | None = None,
    ) -> Iterator[list[dict[str, Any]]]:
        """Yield successive pages from the API. Each page = list of raw items."""

    @abstractmethod
    def to_raw_row(self, item: dict[str, Any]) -> dict[str, Any]:
        """Map a single API item to a row dict for raw.haravan_<domain>."""

    def idempotent_load(
        self,
        mode: str,
        *,
        since: datetime | None = None,
        until: datetime | None = None,
    ) -> tuple[int, datetime | None]:
        """Run extract → upsert. Returns (rows_total, max_updated_at_seen).

        - In `incremental` mode, the watermark from `meta.sync_state` is used
          as a default `since` floor when caller didn't pass one.
        - Watermark itself is NOT updated here — caller (CLI) updates after
          all pages succeed, in a separate transaction (research §5).
        """
        if mode == "incremental" and since is None:
            since = self.state.get_watermark(self.domain)

        rows_total = 0
        max_updated: datetime | None = None
        for page in self.iter_pages(since, until):
            rows = [self.to_raw_row(item) for item in page]
            if not rows:
                continue
            self.loader.upsert_batch(self.raw_table, rows, conflict_col=self.conflict_col)
            rows_total += len(rows)
            for row in rows:
                row_ts = row["updated_at"]
                if max_updated is None or row_ts > max_updated:
                    max_updated = row_ts
        return rows_total, max_updated
