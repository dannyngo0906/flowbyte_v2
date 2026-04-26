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
    """4xx other than 401/429 — caller bug, NOT retryable."""


class HaravanServerError(HaravanAPIError):
    """5xx — retryable transient failure."""
