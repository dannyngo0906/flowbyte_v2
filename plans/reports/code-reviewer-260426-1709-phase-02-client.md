# Code Review — Phase-02 Haravan Client + OAuth + Telegram Stub

**Scope:** `src/haravan_elt/client/{haravan,env_writer,rate_limit,exceptions,telegram}.py`, `config.py`, `logging.py`, `cli.py`, `tests/{conftest,test_haravan_client,test_env_writer,test_telegram_client}.py`.
**Verification:** ruff/mypy-strict clean, 21/21 tests pass, client/ coverage ≥90%.

## Pros

- Atomic `.env` write-back is solid: `tempfile.mkstemp` (avoids `.env.tmp` race / predictable name), `os.replace` (POSIX-atomic same-FS), explicit cleanup on failure, `chmod 600` enforced unconditionally. `test_atomic_failure_keeps_original` proves the rollback contract.
- Exception hierarchy is minimal & correct: only `RateLimit + Server` are retryable types in tenacity decorator → 422 / 401-after-refresh / refresh-itself-failing surface immediately. `body` field on base preserves raw response for debugging.
- OAuth refresh path is single-locked, in-memory `SecretStr` rotation + disk persistence in same critical section. Refresh-token-rotation contract honored (research §3): `payload.get("refresh_token", old)` covers both rotating and non-rotating endpoint behavior.
- Token URL is correct (`accounts.haravan.com/connect/token`). Endpoint configurable per-instance via `TOKEN_URL` class attr if needed for testing.
- `SecretStr` discipline: tokens never appear in `repr()` (verified), structlog event names contain only context keys, never token values. Refresh log uses `oauth_refresh_success` with no payload.
- Tests use respx (httpx-native), 401-after-refresh test asserts both in-memory rotation **and** on-disk `.env` persistence — strong contract coverage.
- `httpx.Client` injectable via constructor → enables proper test isolation; `__enter__/__exit__` for resource cleanup.
- Telegram fail-soft contract verified by 3 tests (200, 4xx, network error) — `httpx.HTTPError` catch covers timeouts/connect/read errors.

## Issues

### Critical

- **`haravan.py:92` — pyrate-limiter `try_acquire` is non-blocking and raises `BucketFullException` on saturation, NOT caught/retried.** Comment on line 91 ("Block until limiter allows the call") is wrong: v3.9 `try_acquire` returns bool / raises `BucketFullException` (default `raise_when_fail=True`). Verified empirically: 5th call within 1s raises. Under sustained extract load (Haravan caps at 4 req/s; we issue >4/s during pagination), this will crash the pipeline. `BucketFullException` is not in the tenacity retry list, so tenacity won't recover. **Fix:** either (a) construct `Limiter(..., max_delay=Duration.SECOND*30, raise_when_fail=False)` so `try_acquire` blocks-then-returns, or (b) wrap in a sleep-loop, or (c) add `BucketFullException` to retry types AND sleep before re-raise. Option (a) matches the comment's intent.

### High

- **`haravan.py:104-112` — 401 re-issue path bypasses tenacity but also bypasses limiter and quota monitor on the SECOND attempt's request.** Wait, `_monitor_quota(resp)` is called (line 108) but `self._limiter.try_acquire("haravan")` is NOT called for the post-refresh retry. If refresh happens at quota peak, the next call may exceed budget. Add a `try_acquire` before the retry request, or extract the request into a helper that always limits + monitors.
- **`haravan.py:107` — post-401 retry uses same `**kwargs` reference that includes mutable state.** Minor but if caller passes `params` dict and respx mutates it, the second call sees mutation. Defensive copy or document immutability requirement.
- **`haravan.py:160` — `httpx.post(self.TOKEN_URL, ...)` uses a fresh httpx client (no proxy / SSL config inheritance from `self._http`).** If user configured custom CA, mTLS, or proxy on `self._http`, refresh bypasses it. Acceptable for MVP but document, or reuse a shared transport.
- **OAuth body content-type / auth method.** Standard OAuth2 token endpoints accept `client_secret` either in form body (current impl, treated as confidential client form-post) OR via HTTP Basic — Haravan's exact preference is unverified (Q2 in plan still unresolved). `httpx.post(..., data=body)` sends `application/x-www-form-urlencoded` which is the OAuth2-spec default and likely correct, but worth confirming against live shop.

### Medium

- **`rate_limit.py:23-24` — second `Rate(burst, drain_seconds)` window is overly permissive.** With `rate=4/sec, burst=80`, second rate becomes `Rate(80, 20s)` = 80/20s = 4/s avg → identical to first rate, providing no extra burst headroom. Either drop the second rate (single Rate is sufficient) OR use a longer drain (e.g., 60s) to actually allow burst. Current config is YAGNI noise.
- **`haravan.py:96-102` — `time.sleep(retry_after)` BEFORE raising means tenacity's `wait_exponential(min=1, max=30)` ALSO sleeps on top.** Effective sleep: `retry_after + tenacity_backoff`. For a Retry-After of 30s plus tenacity max 30s, total wait is up to 60s on a single 429. Intentional double-cushion? Or should the raise skip the time.sleep and let tenacity's wait handle it? At minimum document the design choice in a comment so future maintainers don't "fix" by removing one of them.
- **`haravan.py:145` — `int(capacity * 0.85)` floors the threshold.** For capacity=80: warns at ≥68 (85%). For capacity=10: warns at ≥8 (80%). Acceptable, but plan §requirements says ">70/80" (≥71 = 88.75%). Pick one and align doc/code; current code triggers earlier than plan text claims.
- **`telegram.py:37` — `httpx.post` with no TLS / proxy config and no retries.** Telegram outage that times out at 10s blocks the calling thread for up to 10s per notification. Acceptable as fail-soft but consider non-blocking or shorter timeout for batch error reporting.
- **`config.py:60-65` — `# type: ignore[call-arg]` factories.** Pragmatic for mypy strict but masks future required-field additions. Tag with TODO referencing pydantic-settings PR for proper typing when upstream lands.
- **No test for `BucketFullException` path.** The critical issue above would have been caught by a "exhaust limiter then assert-call-still-succeeds" test. Add one.
- **No test for malformed quota header.** `_monitor_quota` has a `try/except ValueError` (line 142) but coverage shows lines 142-144 missing. Add a test feeding `X-Haravan-Api-Call-Limit: garbage/values` and assert no exception.

### Low

- **`haravan.py:90` — `**kwargs: Any` widens type contract.** Only `params` and `json` are realistic. Consider `params: dict | None = None, json: Any = None` for strict signature.
- **`haravan.py:165` — `payload["access_token"]` will raise `KeyError` (not `HaravanAuthError`) if Haravan returns malformed 200.** Wrap in try/except KeyError → re-raise as HaravanAuthError("malformed refresh response").
- **`env_writer.py:39` — `dir=str(path.parent or ".")`** — `Path.parent` of `Path(".env")` is `Path(".")` which is truthy, so the `or "."` branch never fires. Harmless but dead code; just use `dir=str(path.parent)`.
- **`logging.py:23` — `logging.basicConfig(stream=sys.stdout, ...)`** — if `setup_logging` is called twice (e.g., test re-init), `basicConfig` is a no-op on second call but `structlog.configure` overwrites. Subtle drift risk; document "call once at process entry only".
- **`conftest.py:39` — `fake_settings_env` returns `None` but is used as a fixture dependency in `test_haravan_client`.** Works because Settings() reads from env, but pattern is non-obvious. Comment in client.py fixture (`# noqa: ARG001`) papers over it; consider explicit `_ = fake_settings_env`.
- **Tests don't assert quota WARN log fired.** Use `caplog` to verify the WARN path on the 200-response that includes a near-cap header.

## Recommendations (priority order)

1. **Fix limiter saturation (critical).** Switch to `Limiter(rates, max_delay=Duration.SECOND*30, raise_when_fail=False)` and update line-91 comment to describe actual blocking behavior. Add a regression test that fires N+1 requests in a tight loop and asserts all complete.
2. Add `try_acquire` + missing test for the post-refresh retry request to keep limiter accounting accurate.
3. Drop or extend the second Rate in `make_limiter` — current config is a no-op layer.
4. Reconcile quota threshold: pick "≥85%" (matches code) or ">70/80" (current doc) — update the other.
5. Add tests: malformed quota header; BucketFull recovery; quota WARN log assertion; refresh response missing `access_token`.
6. Decide & document: 429 double-sleep (`time.sleep(retry_after)` + tenacity backoff). Either remove `time.sleep` and rely on tenacity, or comment why both.
7. Confirm OAuth refresh body format (form-post vs Basic) against first live shop; codify in `.env.example`.

## Verdict

**needs-changes** — single critical (limiter raises under load → pipeline crash) blocks production-readiness, but everything else is high-quality. Fix #1 + #2 above, add the regression test, and this phase is ship-ready. Phase-03 can proceed in parallel since the public surface (`HaravanClient.get`) won't change.

## Unresolved Questions

- Q1: Does Haravan's `accounts.haravan.com/connect/token` accept form-post `client_secret` or require HTTP Basic? Verify on first live shop.
- Q2: Is the dual-sleep on 429 (manual `time.sleep` + tenacity wait_exponential) intentional belt-and-suspenders, or oversight?
- Q3: Should `BucketFullException` be a retryable transient (similar to 429), or always treat as caller error?

---

**Status:** DONE
**Summary:** Found one critical bug: `pyrate-limiter` v3 `try_acquire` raises `BucketFullException` under saturation rather than blocking — uncaught, will crash the pipeline. Atomic .env writer, OAuth refresh, exception hierarchy, and test coverage are otherwise solid.
**Verdict:** needs-changes
**Critical issues count:** 1
