# Phase 02 — Haravan Client + Auth (M1)

## Context Links

- PRD §4.2 (FR-E1, FR-E2, FR-E3), §6.1 (stack)
- API endpoints: `/Users/duyngo/Downloads/documents-elt-tools/docs-etl2/haravan-api/Haravan-API-Endpoints.md`
- Research (API quirks §1 rate limit, §3 OAuth, §5 auth header, §6 errors): `plans/reports/researcher-260426-1340-haravan-api-quirks.md`
- Research (idempotent §1 retry, §2 rate limit, §6 logging, §7 VCR, §9 settings): `plans/reports/researcher-260426-1340-idempotent-elt-python.md`
- Phase-01 (config + folder skeleton)

## Overview

- **Priority:** high
- **Status:** pending
- **Effort:** 5 days
- **Description:** Build production-grade `HaravanClient` with httpx + tenacity retry + pyrate-limiter LeakyBucket + OAuth refresh (with `.env` write-back). Stub `TelegramClient` for use by error handlers. Tests via pytest-vcr with `Authorization` header filtered.

## Key Insights

- **Rate limit is leaky bucket, NOT 40/min strict.** Config: `LeakyBucket(max_rate=4, time_period=Duration.SECOND)` with bucket size 80 (research §1). Monitor `X-Haravan-Api-Call-Limit: <current>/<capacity>` header.
- **Refresh token rotates every use** (30d rolling). After refresh, MUST persist new `access_token` AND `refresh_token` back to `.env` atomically (write `.env.tmp` + rename).
- **Refresh endpoint:** `POST https://accounts.haravan.com/connect/token` (NOT `apis.haravan.com`) per research §3 + Haravan OAuth docs. Verify on first live test.
- **Retry-After parsing:** numeric seconds expected; HTTP-date format possible — fallback to exponential backoff (2→4→8→16→30s).
- **Error JSON shape unknown** — defensive parser; expose raw body in `HaravanAPIError`.

## Requirements

**Functional:**
- FR-E1: OAuth 2.0 token-only + auto refresh on 401
- FR-E3: 429 → respect `Retry-After`; 5xx → exp backoff (max 5 attempts)
- Read `X-Haravan-Api-Call-Limit` after each request, log at WARN if >70/80

**Non-functional:**
- NFR-2: Retries idempotent (GET only)
- NFR-4: `SecretStr` for tokens; never log raw value
- NFR-5: Type-hinted client; mypy strict passes

## Architecture

```
HaravanClient
  ├─ httpx.Client (base_url=https://apis.haravan.com, timeout=30s)
  ├─ pyrate_limiter.Limiter(LeakyBucket(4 req/s, capacity=80))
  ├─ tenacity @retry(...)  ← decorator on `_request_raw`
  │     ├─ retry_on (HTTPStatusError 429/500/502/503/504, ConnectError, ReadTimeout)
  │     ├─ wait_exponential(multiplier=2, min=1, max=30)
  │     └─ stop_after_attempt(5)
  ├─ on 429: parse Retry-After → sleep that long → re-raise to trigger tenacity retry
  ├─ on 401: trigger _refresh_access_token() → retry once (max 1, separate from tenacity)
  └─ _refresh_access_token():
        POST https://accounts.haravan.com/connect/token
          grant_type=refresh_token
          refresh_token=<current>
          client_id=<...>
          client_secret=<...>
        → update settings.haravan.access_token / refresh_token (in-memory)
        → atomic write-back to .env via dotenv module

TelegramClient (stub in this phase, full impl phase-08)
  └─ async-free: httpx.post(...) wrapped in try/except, never raises
```

Exception hierarchy:
```
HaravanAPIError(Exception)
  ├─ HaravanAuthError(401)
  ├─ HaravanRateLimitError(429, retry_after)
  ├─ HaravanValidationError(4xx)
  └─ HaravanServerError(5xx)
```

## Related Code Files

**Create:**
- `src/haravan_elt/client/haravan.py`
- `src/haravan_elt/client/telegram.py` (stub)
- `src/haravan_elt/client/exceptions.py`
- `src/haravan_elt/client/rate_limit.py` (LeakyBucket wrapper)
- `tests/test_haravan_client.py`
- `tests/fixtures/vcr/haravan_orders_page1_ok.yaml`
- `tests/fixtures/vcr/haravan_401_then_refresh.yaml`
- `tests/fixtures/vcr/haravan_429_retry_after.yaml`
- `tests/fixtures/vcr/haravan_500_then_200.yaml`

**Modify:**
- `src/haravan_elt/config.py` — add nested `HaravanSettings`, `TelegramSettings`, `DatabaseSettings`
- `tests/conftest.py` — VCR fixture (see code skeleton in step 8)

## Implementation Steps

1. **Expand `config.py`** to nested settings (research §9 pattern):
   ```python
   from pydantic import SecretStr, Field
   from pydantic_settings import BaseSettings, SettingsConfigDict

   class HaravanSettings(BaseSettings):
       model_config = SettingsConfigDict(env_prefix="HARAVAN_", env_file=".env", extra="ignore")
       shop_domain: str
       access_token: SecretStr
       refresh_token: SecretStr
       client_id: str
       client_secret: SecretStr
       rate_limit_per_sec: int = 4
       rate_limit_burst: int = 80

   class DatabaseSettings(BaseSettings):
       model_config = SettingsConfigDict(env_file=".env", extra="ignore")
       database_url: str

   class TelegramSettings(BaseSettings):
       model_config = SettingsConfigDict(env_prefix="TELEGRAM_", env_file=".env", extra="ignore")
       bot_token: SecretStr
       chat_id: str

   class Settings(BaseSettings):
       model_config = SettingsConfigDict(env_file=".env", extra="ignore")
       haravan: HaravanSettings = Field(default_factory=HaravanSettings)
       database: DatabaseSettings = Field(default_factory=DatabaseSettings)
       telegram: TelegramSettings = Field(default_factory=TelegramSettings)
       log_level: str = "INFO"
       log_format: str = "console"
       extract_batch_size: int = 50
       load_batch_size: int = 500
       timezone: str = "Asia/Ho_Chi_Minh"
   ```

2. **`exceptions.py`:**
   ```python
   class HaravanAPIError(Exception):
       def __init__(self, msg: str, status: int | None = None, body: str = ""):
           super().__init__(msg)
           self.status = status
           self.body = body

   class HaravanAuthError(HaravanAPIError): ...
   class HaravanRateLimitError(HaravanAPIError):
       def __init__(self, msg: str, retry_after: float = 1.0, **kw):
           super().__init__(msg, **kw)
           self.retry_after = retry_after
   class HaravanValidationError(HaravanAPIError): ...
   class HaravanServerError(HaravanAPIError): ...
   ```

3. **`rate_limit.py`:**
   ```python
   from pyrate_limiter import Limiter, RequestRate, Duration, BucketFullException

   def make_limiter(rate_per_sec: int, capacity: int) -> Limiter:
       # 4 req/s, burst 80 = 1 RequestRate at second + capacity at minute window
       rate = RequestRate(rate_per_sec, Duration.SECOND)
       burst = RequestRate(capacity, 20 * Duration.SECOND)  # capacity over ~20s drain
       return Limiter(rate, burst)
   ```
   Tune in M6 hardening if real API behaves differently.

4. **`haravan.py` core:**
   ```python
   import os, httpx, structlog, time, threading
   from pathlib import Path
   from tenacity import retry, stop_after_attempt, wait_exponential, retry_if_exception_type
   from .exceptions import HaravanAuthError, HaravanRateLimitError, HaravanServerError, HaravanValidationError, HaravanAPIError
   from .rate_limit import make_limiter
   from ..config import Settings

   logger = structlog.get_logger()

   class HaravanClient:
       BASE_URL = "https://apis.haravan.com"
       TOKEN_URL = "https://accounts.haravan.com/connect/token"

       def __init__(self, settings: Settings):
           self.settings = settings
           self._http = httpx.Client(base_url=self.BASE_URL, timeout=30.0)
           self._limiter = make_limiter(
               settings.haravan.rate_limit_per_sec,
               settings.haravan.rate_limit_burst,
           )
           self._refresh_lock = threading.Lock()

       def _headers(self) -> dict[str, str]:
           tok = self.settings.haravan.access_token.get_secret_value()
           return {"Authorization": f"Bearer {tok}", "Content-Type": "application/json"}

       def get(self, path: str, params: dict | None = None) -> httpx.Response:
           return self._request("GET", path, params=params)

       @retry(
           stop=stop_after_attempt(5),
           wait=wait_exponential(multiplier=2, min=1, max=30),
           retry=retry_if_exception_type((HaravanRateLimitError, HaravanServerError, httpx.TransportError)),
           reraise=True,
       )
       def _request(self, method: str, path: str, **kwargs) -> httpx.Response:
           self._limiter.try_acquire("haravan")
           resp = self._http.request(method, path, headers=self._headers(), **kwargs)
           self._monitor_quota(resp)
           if resp.status_code == 429:
               retry_after = self._parse_retry_after(resp)
               logger.warning("rate_limit_429", retry_after=retry_after, path=path)
               time.sleep(retry_after)
               raise HaravanRateLimitError("429", status=429, retry_after=retry_after, body=resp.text)
           if resp.status_code == 401:
               # auth error → refresh + ONE retry, outside tenacity loop
               self._refresh_access_token()
               resp = self._http.request(method, path, headers=self._headers(), **kwargs)
               self._monitor_quota(resp)
               if resp.status_code == 401:
                   raise HaravanAuthError("auth still failing after refresh", status=401, body=resp.text)
           if 500 <= resp.status_code < 600:
               raise HaravanServerError(f"5xx {resp.status_code}", status=resp.status_code, body=resp.text)
           if 400 <= resp.status_code < 500:
               raise HaravanValidationError(f"4xx {resp.status_code}", status=resp.status_code, body=resp.text)
           return resp

       @staticmethod
       def _parse_retry_after(resp: httpx.Response) -> float:
           hdr = resp.headers.get("Retry-After", "1")
           try:
               return float(hdr)
           except ValueError:
               return 5.0  # HTTP-date fallback; use 5s safe default

       def _monitor_quota(self, resp: httpx.Response) -> None:
           hdr = resp.headers.get("X-Haravan-Api-Call-Limit")
           if not hdr or "/" not in hdr:
               return
           cur, cap = (int(x) for x in hdr.split("/"))
           level = "warning" if cur >= int(cap * 0.85) else "debug"
           getattr(logger, level)("quota", current=cur, capacity=cap)

       def _refresh_access_token(self) -> None:
           with self._refresh_lock:
               logger.info("oauth_refresh_start")
               body = {
                   "grant_type": "refresh_token",
                   "refresh_token": self.settings.haravan.refresh_token.get_secret_value(),
                   "client_id": self.settings.haravan.client_id,
                   "client_secret": self.settings.haravan.client_secret.get_secret_value(),
               }
               resp = httpx.post(self.TOKEN_URL, data=body, timeout=30.0)
               if resp.status_code != 200:
                   raise HaravanAuthError("refresh failed", status=resp.status_code, body=resp.text)
               data = resp.json()
               new_access = data["access_token"]
               new_refresh = data.get("refresh_token", self.settings.haravan.refresh_token.get_secret_value())
               # in-memory update
               self.settings.haravan.access_token = SecretStr(new_access)
               self.settings.haravan.refresh_token = SecretStr(new_refresh)
               # persist to .env
               _write_env_atomic({
                   "HARAVAN_ACCESS_TOKEN": new_access,
                   "HARAVAN_REFRESH_TOKEN": new_refresh,
               })
               logger.info("oauth_refresh_success")
   ```

5. **`_write_env_atomic` helper** (atomic `.env` rewrite):
   ```python
   def _write_env_atomic(updates: dict[str, str], env_path: Path = Path(".env")) -> None:
       lines = env_path.read_text().splitlines() if env_path.exists() else []
       seen: set[str] = set()
       out: list[str] = []
       for ln in lines:
           if "=" in ln and not ln.lstrip().startswith("#"):
               k, _ = ln.split("=", 1)
               k = k.strip()
               if k in updates:
                   out.append(f"{k}={updates[k]}")
                   seen.add(k)
                   continue
           out.append(ln)
       for k, v in updates.items():
           if k not in seen:
               out.append(f"{k}={v}")
       tmp = env_path.with_suffix(".env.tmp")
       tmp.write_text("\n".join(out) + "\n")
       tmp.replace(env_path)
       os.chmod(env_path, 0o600)
   ```

6. **`telegram.py` stub:**
   ```python
   import httpx, structlog
   logger = structlog.get_logger()

   class TelegramClient:
       def __init__(self, bot_token: str, chat_id: str):
           self._token = bot_token; self._chat = chat_id

       def send(self, text: str, parse_mode: str = "Markdown") -> None:
           url = f"https://api.telegram.org/bot{self._token}/sendMessage"
           try:
               httpx.post(url, json={"chat_id": self._chat, "text": text, "parse_mode": parse_mode}, timeout=10.0)
           except Exception as exc:
               logger.warning("telegram_send_failed", error=str(exc))
   ```

7. **structlog setup in `__init__` or pipeline.py** (research §6 — full setup runs once at CLI entry):
   ```python
   def setup_logging(json_output: bool, level: str = "INFO") -> None:
       structlog.configure(
           processors=[
               structlog.contextvars.merge_contextvars,
               structlog.stdlib.add_log_level,
               structlog.processors.TimeStamper(fmt="iso"),
               structlog.processors.format_exc_info,
               structlog.processors.JSONRenderer() if json_output else structlog.dev.ConsoleRenderer(),
           ],
           cache_logger_on_first_use=True,
       )
   ```

8. **VCR fixture (`tests/conftest.py`):**
   ```python
   import pytest
   from pathlib import Path

   @pytest.fixture
   def vcr_config():
       return {
           "filter_headers": [("authorization", "Bearer DUMMY")],
           "match_on": ["method", "scheme", "host", "port", "path", "query"],
           "cassette_library_dir": str(Path(__file__).parent / "fixtures/vcr"),
           "record_mode": "none",  # CI-safe: fail if cassette missing
       }
   ```

9. **Test cases (`tests/test_haravan_client.py`):**
   - 200 OK happy path
   - 401 → refresh → retry → 200
   - 429 → Retry-After=2 → retry → 200
   - 500 → exp backoff → 200 on attempt 3
   - 5x 500 → raises `HaravanServerError`
   - Refresh failure → `HaravanAuthError`
   - `_write_env_atomic` test (use `tmp_path` fixture, no live API)

10. **Manual smoke test (gated by env var):** `HARAVAN_LIVE=1 pytest -m live` to record cassettes for the first time. Default skip.

## Todo List

- [ ] Expand `src/haravan_elt/config.py` to nested settings (Haravan/DB/Telegram + root)
- [ ] Create `src/haravan_elt/client/exceptions.py` (4 subclasses)
- [ ] Create `src/haravan_elt/client/rate_limit.py` (LeakyBucket factory)
- [ ] Create `src/haravan_elt/client/haravan.py` (HaravanClient + tenacity + refresh + write-back)
- [ ] Create `src/haravan_elt/client/telegram.py` stub (fail-soft)
- [ ] Add `setup_logging()` helper (structlog JSON/console toggle)
- [ ] Write `tests/conftest.py` VCR fixture (filter `authorization`, `record_mode=none`)
- [ ] Record VCR cassettes: 200, 401-refresh, 429, 500, 500x5
- [ ] Write `tests/test_haravan_client.py` covering all 6 cases above
- [ ] Verify `make typecheck` passes (mypy strict)
- [ ] Verify `make test` covers `client/` ≥80%

## Success Criteria

- All 6 test cases pass via VCR (no live API calls in CI)
- `mypy --strict src/haravan_elt/client/` clean
- Refresh roundtrip: write-back to throwaway `.env` (tmp_path) verified byte-identical
- Quota header parsed and logged for every request (verified in cassette test assertions)
- `_write_env_atomic` is atomic (interrupt simulation: kill mid-write → original `.env` intact)

## Risk Assessment

| Risk | Likelihood | Impact | Mitigation |
|------|-----------|--------|-----------|
| Refresh endpoint is `apis.haravan.com/connect/token` not `accounts.haravan.com` | Medium | High | Confirm via live test; make `TOKEN_URL` overridable via env |
| `Retry-After` is HTTP-date format, not seconds | Low | Med | Fallback parser uses 5s safe default |
| pyrate-limiter API surface changed across versions | Med | Low | Pin `pyrate-limiter>=3.7,<4`; integration test |
| `.env` write race if 2 cron jobs run (lock not yet implemented) | Low | High | Phase-10 adds `fcntl.flock`; phase-02 documents single-instance assumption |

## Security Considerations

- `SecretStr` wraps all tokens — never accidentally logged via `repr()`
- VCR filter strips `Authorization` header before save
- `.env` chmod 600 after write (POSIX); document Windows limitation
- Refresh token on disk is rotated — old token invalidated on Haravan side after first refresh use
- Refresh secret never appears in logs (`logger.info("oauth_refresh_success")` does NOT include token)

## Next Steps

Unblocks **phase-03** (orders extractor reuses HaravanClient).

## Unresolved Questions

- Q1: Token TTL not published — assume 24h; add proactive refresh (every 12h) only if observed expiry pattern requires it (defer to phase-10).
- Q2: Live token refresh endpoint exact host — `apis.haravan.com` vs `accounts.haravan.com` — verify with first live request, codify in `.env.example` comment.
