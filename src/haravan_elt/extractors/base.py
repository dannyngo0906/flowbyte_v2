"""Abstract BaseExtractor + reusable templates.

`BaseExtractor` defines the load-loop contract. `PaginatedListExtractor`
implements the standard offset-paginated `/com/<domain>.json` pattern so most
P0/P1/P2 domains only need to declare a path + response wrapper key.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Iterator, Mapping
from datetime import datetime, timedelta
from types import MappingProxyType
from typing import TYPE_CHECKING, Any
from uuid import UUID

from psycopg.types.json import Jsonb

from haravan_elt.extractors._helpers import parse_haravan_timestamp

if TYPE_CHECKING:
    from haravan_elt.client.haravan import HaravanClient
    from haravan_elt.loaders.postgres import PostgresLoader
    from haravan_elt.meta.state import StateManager

# Re-scan window subtracted from the incremental watermark. Haravan list feeds
# can surface a record AFTER the watermark already advanced past its
# updated_at (the embedded copy in an order arrives instantly, the standalone
# list entry lags). Without this buffer such records are skipped forever by the
# updated_at_min floor. Upserts are idempotent (ON CONFLICT) so re-fetch is safe.
LATE_ARRIVAL_BUFFER_DAYS = 7


class BaseExtractor(ABC):
    """Base contract every domain extractor must satisfy.

    Subclasses set:
        domain      - identifier used in `meta.sync_state` and `meta.run_log`
        raw_table   - target qualified table name, e.g. "raw.haravan_orders"
        conflict_col - PK column for ON CONFLICT (defaults to "id")
        supports_incremental - False for full-refresh-only domains (locations)
    """

    domain: str
    raw_table: str
    conflict_col: str = "id"
    supports_incremental: bool = True

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
        - Domains with `supports_incremental=False` ignore `mode` semantics
          but still report max_updated for watermark logging.
        """
        if mode == "incremental" and self.supports_incremental and since is None:
            watermark = self.state.get_watermark(self.domain)
            if watermark is not None:
                since = watermark - timedelta(days=LATE_ARRIVAL_BUFFER_DAYS)

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


class PaginatedListExtractor(BaseExtractor):
    """Standard offset-paginated `/com/<domain>.json` template.

    Subclass needs only:
        PATH         - endpoint, e.g. "/com/customers.json"
        RESPONSE_KEY - wrapper key in JSON, e.g. "customers"
        EXTRA_PARAMS - static query params merged into every request

    `to_raw_row` defaults to a JSONB-payload + updated_at row; override for
    domains with non-standard timestamp fields.
    """

    PATH: str
    RESPONSE_KEY: str
    # Read-only mapping prevents accidental in-place mutation that would leak
    # to sibling subclasses sharing the parent's empty default.
    EXTRA_PARAMS: Mapping[str, Any] = MappingProxyType({})

    def __init__(self, *args: Any, page_limit: int = 50, **kwargs: Any) -> None:
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
                **self.EXTRA_PARAMS,
            }
            if since is not None:
                params["updated_at_min"] = since.isoformat()
            if until is not None:
                params["updated_at_max"] = until.isoformat()
            resp = self.client.get(self.PATH, params=params)
            items: list[dict[str, Any]] = resp.json().get(self.RESPONSE_KEY, [])
            if not items:
                return
            yield items
            # EOF heuristic: a short page means no more rows (Haravan has no
            # Link header / total count). This is correct ONLY when page_limit
            # equals the server's real per-page cap. Haravan caps list endpoints
            # at 50/page, so the default page_limit is 50; a larger value makes
            # the first 50-row page look "short" and stops after page 1, silently
            # dropping everything past row 50 (this bug truncated customers).
            if len(items) < self._limit:
                return
            page += 1

    def to_raw_row(self, item: dict[str, Any]) -> dict[str, Any]:
        return {
            "id": item["id"],
            "payload": Jsonb(item),
            "updated_at": parse_haravan_timestamp(item["updated_at"]),
            "source_run_id": str(self.run_id),
        }
