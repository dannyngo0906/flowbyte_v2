"""Haravan Omni API HTTP client.

Production-grade: pyrate-limiter LeakyBucket + tenacity retry on transient
failures + automatic OAuth refresh on 401 with atomic .env write-back.

See plan: plans/260426-1340-haravan-elt/phase-02-haravan-client-and-auth.md
"""

from __future__ import annotations

import threading
import time
from pathlib import Path
from typing import Any

import httpx
import structlog
from pydantic import SecretStr
from tenacity import (
    retry,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential,
)

from haravan_elt.client.env_writer import write_env_atomic
from haravan_elt.client.exceptions import (
    HaravanAuthError,
    HaravanRateLimitError,
    HaravanServerError,
    HaravanTransientError,
    HaravanValidationError,
)
from haravan_elt.client.rate_limit import make_limiter
from haravan_elt.config import Settings

logger = structlog.get_logger(__name__)


class HaravanClient:
    """Synchronous HTTP client for the Haravan Omni API."""

    BASE_URL = "https://apis.haravan.com"
    TOKEN_URL = "https://accounts.haravan.com/connect/token"

    def __init__(
        self,
        settings: Settings,
        *,
        env_path: Path | str = ".env",
        http_client: httpx.Client | None = None,
    ) -> None:
        self.settings = settings
        self._env_path = Path(env_path)
        self._http = http_client or httpx.Client(base_url=self.BASE_URL, timeout=30.0)
        self._limiter = make_limiter(
            settings.haravan.rate_limit_per_sec,
            settings.haravan.rate_limit_burst,
        )
        self._refresh_lock = threading.Lock()
        # Tracks consecutive 429s observed across requests. Pipeline reads this
        # after a run to decide whether to emit a Telegram rate-limit warning.
        # Resets on any non-429 response.
        self.consecutive_429 = 0
        self.max_consecutive_429 = 0

    def close(self) -> None:
        self._http.close()

    def __enter__(self) -> HaravanClient:
        return self

    def __exit__(self, *_: object) -> None:
        self.close()

    # ----------------------------------------------------------------- public

    def get(self, path: str, params: dict[str, Any] | None = None) -> httpx.Response:
        """GET with full retry + auth-refresh + quota-monitor handling."""
        return self._request("GET", path, params=params)

    # ----------------------------------------------------------------- internals

    def _headers(self) -> dict[str, str]:
        token = self.settings.haravan.access_token.get_secret_value()
        return {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}

    @retry(
        stop=stop_after_attempt(5),
        wait=wait_exponential(multiplier=2, min=1, max=30),
        retry=retry_if_exception_type(
            (
                HaravanRateLimitError,
                HaravanServerError,
                HaravanTransientError,
                httpx.TransportError,
            )
        ),
        reraise=True,
    )
    def _request(self, method: str, path: str, **kwargs: Any) -> httpx.Response:
        # Defensive copy: callers' kwargs (e.g. `params` dict) MUST NOT be mutated
        # across our internal retry-after-refresh attempt.
        kwargs = dict(kwargs)
        resp = self._send(method, path, **kwargs)
        self._monitor_quota(resp)

        if resp.status_code == 429:
            # NOTE: tenacity's `wait_exponential` kicks in AFTER we raise here,
            # in addition to the explicit Retry-After sleep. Both are kept on
            # purpose: Retry-After matches Haravan's signal exactly, tenacity
            # backoff guards against malformed/missing headers.
            self.consecutive_429 += 1
            self.max_consecutive_429 = max(self.max_consecutive_429, self.consecutive_429)
            retry_after = self._parse_retry_after(resp)
            logger.warning(
                "haravan_429",
                retry_after=retry_after,
                path=path,
                consecutive=self.consecutive_429,
            )
            time.sleep(retry_after)
            raise HaravanRateLimitError(
                "rate limit", retry_after=retry_after, status=429, body=resp.text
            )

        # Any non-429 (including non-2xx that error below) clears the streak —
        # a run interleaved with 200s shouldn't trip the warning.
        self.consecutive_429 = 0

        if resp.status_code == 401:
            # Auth error: refresh + retry ONCE, outside tenacity loop.
            self._refresh_access_token()
            resp = self._send(method, path, **kwargs)
            self._monitor_quota(resp)
            if resp.status_code == 401:
                raise HaravanAuthError(
                    "auth still failing after refresh", status=401, body=resp.text
                )

        if 500 <= resp.status_code < 600:
            raise HaravanServerError(
                f"5xx {resp.status_code}", status=resp.status_code, body=resp.text
            )
        if resp.status_code == 422:
            # Haravan returns 422 intermittently on otherwise-valid requests
            # (pagination edge cases). Tenacity retries via HaravanTransientError;
            # if it persists 5 times the exception bubbles up unchanged.
            logger.warning(
                "haravan_422_transient",
                path=path,
                body_preview=resp.text[:200],
            )
            raise HaravanTransientError("422 transient", status=422, body=resp.text)
        if 400 <= resp.status_code < 500:
            raise HaravanValidationError(
                f"4xx {resp.status_code}", status=resp.status_code, body=resp.text
            )
        return resp

    def _send(self, method: str, path: str, **kwargs: Any) -> httpx.Response:
        """Single send through limiter + transport. Used for both first attempt
        and post-refresh retry so limiter accounting stays consistent."""
        self._limiter.try_acquire("haravan")
        return self._http.request(method, path, headers=self._headers(), **kwargs)

    @staticmethod
    def _parse_retry_after(resp: httpx.Response) -> float:
        """Numeric seconds expected. HTTP-date format → 5s safe fallback."""
        hdr = resp.headers.get("Retry-After", "1")
        try:
            return float(hdr)
        except ValueError:
            return 5.0

    def _monitor_quota(self, resp: httpx.Response) -> None:
        """Log Haravan quota header. WARN at >=85% of capacity."""
        hdr = resp.headers.get("X-Haravan-Api-Call-Limit")
        if not hdr or "/" not in hdr:
            return
        try:
            current_str, capacity_str = hdr.split("/", 1)
            current = int(current_str)
            capacity = int(capacity_str)
        except ValueError:
            logger.debug("haravan_quota_unparseable", header=hdr)
            return
        if capacity > 0 and current >= int(capacity * 0.85):
            logger.warning("haravan_quota_high", current=current, capacity=capacity)
        else:
            logger.debug("haravan_quota", current=current, capacity=capacity)

    def _refresh_access_token(self) -> None:
        """OAuth refresh + atomic .env write-back. Locked for thread-safety.

        Reuses `self._http` so any custom proxy / SSL / mTLS config configured
        on the main client also applies to the token endpoint.
        """
        with self._refresh_lock:
            logger.info("haravan_oauth_refresh_start")
            data = {
                "grant_type": "refresh_token",
                "refresh_token": self.settings.haravan.refresh_token.get_secret_value(),
                "client_id": self.settings.haravan.client_id,
                "client_secret": self.settings.haravan.client_secret.get_secret_value(),
            }
            # Use absolute URL so the request bypasses the BASE_URL on `_http`.
            resp = self._http.post(self.TOKEN_URL, data=data, timeout=30.0)
            if resp.status_code != 200:
                raise HaravanAuthError("refresh failed", status=resp.status_code, body=resp.text)
            payload = resp.json()
            try:
                new_access = payload["access_token"]
            except (KeyError, TypeError) as exc:
                raise HaravanAuthError(
                    "malformed refresh response: missing access_token",
                    status=200,
                    body=resp.text,
                ) from exc
            # Refresh tokens rotate every use (research §3) — fall back to old if endpoint omits.
            new_refresh = payload.get(
                "refresh_token",
                self.settings.haravan.refresh_token.get_secret_value(),
            )
            self.settings.haravan.access_token = SecretStr(new_access)
            self.settings.haravan.refresh_token = SecretStr(new_refresh)
            write_env_atomic(
                {
                    "HARAVAN_ACCESS_TOKEN": new_access,
                    "HARAVAN_REFRESH_TOKEN": new_refresh,
                },
                env_path=self._env_path,
            )
            logger.info("haravan_oauth_refresh_success")
