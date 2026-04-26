"""Abstract BaseExtractor.

Concrete extractors implement `iter_pages()` (HTTP loop with pagination + watermark)
and `to_raw_row()` (JSON payload → row dict for upsert into raw.haravan_<domain>).
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Iterator
from datetime import datetime


class BaseExtractor(ABC):
    """Base contract every domain extractor must satisfy."""

    domain: str  # e.g. "orders", "customers"

    @abstractmethod
    def iter_pages(
        self,
        since: datetime | None = None,
        until: datetime | None = None,
    ) -> Iterator[list[dict[str, object]]]:
        """Yield successive pages from the API. Each page = list of raw items."""

    @abstractmethod
    def to_raw_row(
        self,
        item: dict[str, object],
        run_id: str,
    ) -> dict[str, object]:
        """Map a single API item to a row dict for raw.haravan_<domain>."""
