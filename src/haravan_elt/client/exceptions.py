"""Haravan API exception hierarchy.

Callers catch the base `HaravanAPIError`; tenacity retries on
`HaravanRateLimitError` and `HaravanServerError` (transient).
"""

from __future__ import annotations


class HaravanAPIError(Exception):
    """Base class. `body` keeps raw response so caller can debug unknown shapes."""

    def __init__(self, message: str, *, status: int | None = None, body: str = "") -> None:
        super().__init__(message)
        self.status = status
        self.body = body


class HaravanAuthError(HaravanAPIError):
    """401 after refresh attempt — surface to user."""


class HaravanRateLimitError(HaravanAPIError):
    """429 — retry-after seconds attached for tenacity."""

    def __init__(
        self,
        message: str,
        *,
        retry_after: float = 1.0,
        status: int | None = None,
        body: str = "",
    ) -> None:
        super().__init__(message, status=status, body=body)
        self.retry_after = retry_after


class HaravanValidationError(HaravanAPIError):
    """4xx other than 401/422/429 — caller bug, NOT retryable.

    Note: 422 is treated as transient (`HaravanTransientError`) because
    Haravan returns 422 intermittently on valid pagination requests
    (verified live 2026-04-27: same page 136 returned 422 once then 200
    for 5 consecutive retries). Other 4xx (400, 403, 404, ...) bubble up
    directly so true caller bugs surface immediately.
    """


class HaravanTransientError(HaravanAPIError):
    """422 specifically — Haravan API hiccup that retry usually resolves."""


class HaravanServerError(HaravanAPIError):
    """5xx — retryable transient failure."""
