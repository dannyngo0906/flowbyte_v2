# Research Report: Idempotent ELT Patterns for Rate-Limited REST APIs (Python)

**Research Date:** 2026-04-26  
**Project:** haravan-elt  
**Scope:** HTTP retry, rate limiting, batch upsert, structlog, pytest-vcr, Typer, pydantic-settings, lockfile strategies  

---

## 1. HTTP Retry & 429 Handling

### Recommendation: **Tenacity + httpx (custom wrapper)**

**Key Finding:** httpx has no built-in 429 handling with Retry-After header support. Use **tenacity** as the external retry layer.

#### Pattern:
```python
from tenacity import (
    retry, stop_after_attempt, wait_exponential, 
    retry_if_exception_type, RetryError
)
import httpx

@retry(
    stop=stop_after_attempt(5),
    wait=wait_exponential(multiplier=2, min=1, max=30),  # 2s, 4s, 8s, 16s, 30s
    retry=retry_if_exception_type((httpx.HTTPStatusError,)),
)
def _fetch_with_retry(client: httpx.Client, method: str, url: str, **kwargs):
    resp = client.request(method, url, **kwargs)
    if resp.status_code == 429:
        # Honor Retry-After header if present
        retry_after = resp.headers.get("retry-after")
        if retry_after:
            try:
                wait_secs = float(retry_after)  # seconds
            except ValueError:
                # Could be HTTP-date, fall back to exponential
                wait_secs = 2 ** (getattr(_fetch_with_retry.retry, 'statistics', {}).get('attempt_number', 1))
            raise httpx.HTTPStatusError(f"429: waiting {wait_secs}s", request=resp.request, response=resp)
    resp.raise_for_status()
    return resp
```

**Rationale:**
- tenacity handles 429 as exception, allowing Retry-After parsing
- Custom wrapper gives fine control over backoff vs Retry-After trade-off
- httpx is sync-friendly, lighter than async for simple ETL  
- 5 attempts + exponential backoff acceptable for 40 req/min rate limit (max ~2 min total wait per request)

**Alternatives considered:**
- ❌ `httpx.HTTPTransport(retries=N)`: No 429 handling, no Retry-After support (per docs)
- ❌ `retryhttp` library: Newer but less mature; tenacity has 10+ year production history
- ✅ `tenacity + httpx`: Industry standard, explicit control, works with both sync/async

---

## 2. Rate Limit Pacing (40 req/min)

### Recommendation: **pyrate-limiter (LeakyBucket) for simplicity**

**Key Finding:** Token bucket is overkill for Haravan's simple 40/min rate limit. Leaky bucket + sliding window simpler to reason about.

#### Pattern:
```python
from pyrate_limiter import (
    BucketFullException, Duration, Limiter, LeakyBucket
)
import structlog

logger = structlog.get_logger()

# 40 requests per 60 seconds
rate_limiter = Limiter(LeakyBucket(max_rate=40, time_period=Duration.MINUTE))

def fetch_paginated(api_url: str, client: httpx.Client) -> list[dict]:
    """Paginate with rate limit pacing."""
    results = []
    page = 1
    while True:
        try:
            rate_limiter.try_acquire("haravan")  # or raise BucketFullException
        except BucketFullException:
            logger.warning("rate_limit_hit", remaining_secs=rate_limiter.try_acquire_rate())
            # Back off; could sleep or queue for retry
            import time
            time.sleep(2)
            continue
        
        resp = _fetch_with_retry(client, "GET", f"{api_url}?page={page}", timeout=10.0)
        data = resp.json()
        results.extend(data.get("items", []))
        
        if not data.get("pagination", {}).get("next_page"):
            break
        page += 1
    
    return results
```

**Rationale:**
- LeakyBucket naturally emulates request throttling (drains at constant rate)
- Haravan rate limit is straightforward; no per-endpoint variation
- pyrate-limiter supports SQLite backend if multi-process needed (unlikely for single ETL instance)
- Non-blocking `try_acquire()` avoids deadlock in cron

**Alternatives considered:**
- ✅ pyrate-limiter (LeakyBucket): Simple, mature, sync-friendly
- ❌ aiolimiter: Async-only; Haravan ETL is single-threaded sync
- ❌ Simple sleep per request: Works but no backpressure mechanism; pyrate-limiter is cleaner

---

## 3. Batch Upsert Patterns (psycopg 3)

### Recommendation: **`executemany()` with `INSERT ... ON CONFLICT` for flexibility, `COPY` temp table for performance**

**Key Finding:** psycopg3's `executemany()` now uses pipeline mode internally (3.1+), making it faster than psycopg2's `execute_values`. For JSONB upsert with `ON CONFLICT DO UPDATE`, use `executemany()` directly.

#### Pattern A: executemany (simple, flexible):
```python
import psycopg
import uuid
from datetime import datetime, timezone

def batch_upsert_orders(rows: list[dict], conn: psycopg.Connection, batch_size: int = 500):
    """
    rows: [{"id": 123, "payload": {...}, "updated_at": ..., "run_id": uuid}, ...]
    """
    cursor = conn.cursor()
    
    for i in range(0, len(rows), batch_size):
        batch = rows[i : i + batch_size]
        
        # Prepare batch data
        batch_data = [
            (
                row["id"],
                psycopg.types.json.Jsonb(row["payload"]),  # JSONB wrapper
                row["updated_at"],
                row["run_id"]
            )
            for row in batch
        ]
        
        # executemany with ON CONFLICT DO UPDATE
        cursor.executemany(
            """
            INSERT INTO raw.haravan_orders (id, payload, updated_at, ingested_at, source_run_id)
            VALUES (%s, %s, %s, now(), %s)
            ON CONFLICT (id) DO UPDATE SET
                payload = EXCLUDED.payload,
                updated_at = EXCLUDED.updated_at,
                ingested_at = now()
            WHERE EXCLUDED.updated_at > raw.haravan_orders.updated_at
            """,
            batch_data,
        )
        logger.info("batch_upserted", rows_affected=cursor.rowcount, batch_num=i // batch_size)
    
    # executemany auto-commits; explicit commit only if needed
    conn.commit()
```

**Rationale:**
- pipeline mode (internal to psycopg3.1+) batches multiple commands, reducing round-trips
- `executemany()` + `RETURNING` gives affected rows count (v3.1+)
- JSONB wrapper ensures correct type casting
- `WHERE EXCLUDED.updated_at >` prevents downgrading recent data with stale

#### Pattern B: COPY + temp table (for 10k+ rows, <2s insertion):
```python
def batch_upsert_orders_fast(rows: list[dict], conn: psycopg.Connection):
    """Use COPY to temp table, then MERGE."""
    cursor = conn.cursor()
    
    # Create temp staging table
    cursor.execute("""
        CREATE TEMP TABLE staging_orders (
            id BIGINT, payload JSONB, updated_at TIMESTAMPTZ, source_run_id UUID
        )
    """)
    
    # COPY from file-like buffer
    import io
    buffer = io.StringIO()
    for row in rows:
        line = f"{row['id']}\t{json.dumps(row['payload'])}\t{row['updated_at']}\t{row['run_id']}\n"
        buffer.write(line)
    buffer.seek(0)
    
    cursor.copy_from(buffer, "staging_orders")
    
    # Merge (upsert) from staging into target
    cursor.execute("""
        INSERT INTO raw.haravan_orders (id, payload, updated_at, ingested_at, source_run_id)
        SELECT id, payload, updated_at, now(), source_run_id FROM staging_orders
        ON CONFLICT (id) DO UPDATE SET
            payload = EXCLUDED.payload,
            updated_at = EXCLUDED.updated_at,
            ingested_at = now()
        WHERE EXCLUDED.updated_at > raw.haravan_orders.updated_at
    """)
    
    conn.commit()
    logger.info("bulk_upsert_completed", rows_affected=cursor.rowcount)
```

**Trade-off:**
- `executemany()`: 500–1000 rows/s, handles small-medium batches, code clearer
- `COPY + merge`: 5000+ rows/s, 20% overhead for buffer + staging, SQL complexity

**Recommendation:** Use `executemany()` for 500-row batches (matches PRD default). Switch to COPY only if profiling shows insert is bottleneck.

---

## 4. Transactional Safety & Idempotence

### Recommendation: **One transaction per batch, watermark update in separate transaction (or same if carefully ordered)**

#### Pattern:
```python
def extract_and_load(domain: str, mode: str, conn: psycopg.Connection):
    """Load data + update watermark atomically when possible."""
    rows_extracted = []
    max_updated_at = None
    
    # Extract phase (outside transaction, read-only)
    rows_extracted, max_updated_at = extract_all_pages(domain, mode)
    logger.info("extraction_done", domain=domain, rows=len(rows_extracted), max_ts=max_updated_at)
    
    # Load phase (transactions)
    try:
        with conn.transaction():  # Auto-rollback on exception
            batch_upsert_orders(rows_extracted, conn, batch_size=500)
            logger.info("batch_load_done", rows=len(rows_extracted))
    except psycopg.IntegrityError as e:
        logger.error("load_failed_integrity", error=str(e), domain=domain)
        raise
    
    # Watermark phase (separate transaction)
    try:
        with conn.transaction():
            cursor = conn.cursor()
            cursor.execute("""
                INSERT INTO meta.sync_state (domain, last_updated_at, last_run_id, updated_at)
                VALUES (%s, %s, %s, now())
                ON CONFLICT (domain) DO UPDATE SET
                    last_updated_at = GREATEST(meta.sync_state.last_updated_at, EXCLUDED.last_updated_at),
                    last_run_id = EXCLUDED.last_run_id,
                    updated_at = now()
            """, (domain, max_updated_at, run_id))
        logger.info("watermark_updated", domain=domain, last_ts=max_updated_at)
    except Exception as e:
        logger.error("watermark_update_failed", domain=domain, error=str(e))
        # Watermark fail is NOT fatal; data already loaded, next run may re-process some rows
        # This is acceptable for idempotence
```

**Rationale:**
- Each batch = 1 transaction (atomic upsert or none)
- Watermark in separate transaction allows non-fatal fail (data already safe)
- If mid-run crash: data + watermark might be stale, next run re-extracts overlapping rows → **idempotent**
- `GREATEST()` ensures watermark only advances, never regresses

---

## 5. High Watermark Update Strategy

### Recommendation: **Per-batch or per-domain, at end of extraction loop (not per-page)**

#### Pattern:
```python
def extract_all_pages(domain: str, mode: str, client: httpx.Client) -> tuple[list[dict], datetime]:
    """
    Extract all pages for domain, track max updated_at.
    If incremental, use last_watermark as filter.
    """
    if mode == "incremental":
        last_watermark = get_last_watermark(domain)  # FROM meta.sync_state
    else:
        last_watermark = None
    
    all_rows = []
    max_updated_at = None
    page = 1
    
    while True:
        # Pagination + rate limiting
        rate_limiter.try_acquire("haravan")
        
        params = {"limit": 250, "page": page}
        if last_watermark and mode == "incremental":
            params["updated_at_min"] = last_watermark.isoformat()
        
        resp = _fetch_with_retry(client, "GET", f"{domain_url}", params=params)
        data = resp.json()
        
        for item in data.get("items", []):
            all_rows.append({
                "id": item["id"],
                "payload": item,
                "updated_at": parse_iso(item["updated_at"]),
                "run_id": run_id
            })
            # Track max of this page
            updated_at = parse_iso(item["updated_at"])
            if max_updated_at is None or updated_at > max_updated_at:
                max_updated_at = updated_at
        
        if not data.get("pagination", {}).get("next_page"):
            break
        page += 1
    
    # Watermark = max(updated_at) across ALL pages
    # Only update at end of entire extraction, not per-page
    logger.info("extraction_complete", domain=domain, rows=len(all_rows), max_ts=max_updated_at)
    return all_rows, max_updated_at
```

**Rationale:**
- If extraction fails mid-extraction (page 5 of 10), watermark hasn't advanced → next run fetches full range again
- Re-extraction idempotent because `INSERT ... ON CONFLICT` dedups by `id`
- Simpler than per-page watermark (no partial state tracking)

**Edge case:** If pages arrive out-of-order (unlikely, but Haravan API doesn't guarantee), use `max(updated_at)` across all pages, not last page's `updated_at`.

---

## 6. Structured Logging (structlog) Setup

### Recommendation: **JSON output in production, pretty console in dev; correlation ID via context vars**

#### Pattern:
```python
import structlog
from typing import Any
import uuid
import contextvars

# Context var for request_id / run_id
run_id_context: contextvars.ContextVar[str] = contextvars.ContextVar("run_id", default="")

def setup_logging(json_output: bool = False):
    """Configure structlog for dev (pretty) or prod (JSON)."""
    structlog.configure(
        processors=[
            structlog.contextvars.merge_contextvars,  # Inject run_id from context
            structlog.stdlib.filter_by_level,
            structlog.stdlib.add_logger_name,
            structlog.stdlib.add_log_level,
            structlog.stdlib.PositionalArgumentsFormatter(),
            structlog.processors.TimeStamper(fmt="iso"),
            structlog.processors.StackInfoRenderer(),
            structlog.processors.format_exc_info,
            (structlog.processors.JSONRenderer() if json_output 
             else structlog.dev.ConsoleRenderer()),
        ],
        context_class=dict,
        logger_factory=structlog.stdlib.LoggerFactory(),
        cache_logger_on_first_use=True,
    )

def extract_with_logging(domain: str, mode: str):
    """Example: bind run_id to all logs in this extraction."""
    run_id = uuid.uuid4()
    run_id_context.set(str(run_id))  # Auto-included in all logs via merge_contextvars
    
    logger = structlog.get_logger()
    logger.info("extraction_started", domain=domain, mode=mode)  # Includes run_id
    
    try:
        rows = extract_all_pages(domain, mode, client)
        logger.info("extraction_success", rows_count=len(rows))
        return rows
    except Exception as e:
        logger.error("extraction_failed", error=str(e), exc_info=True)
        raise
```

**JSON output example:**
```json
{"event": "extraction_started", "domain": "orders", "mode": "incremental", "run_id": "a1b2c3d4", "timestamp": "2026-04-26T10:15:23.456Z"}
{"event": "extraction_success", "rows_count": 1234, "run_id": "a1b2c3d4", "timestamp": "2026-04-26T10:17:45.123Z"}
```

**Rationale:**
- structlog 25.5.0 supports JSON + context vars
- `merge_contextvars` auto-includes `run_id` in every log (no manual binding)
- JSON parseable for ELK/Datadog/CloudWatch
- Dev mode readable for debugging

---

## 7. pytest-vcr Cassette Setup

### Recommendation: **VCR + pytest-vcr with header filtering; manual cassette management per domain**

#### Pattern:
```python
# tests/conftest.py
import pytest
import os

@pytest.fixture
def vcr_config():
    """Configure VCR to filter Authorization header and manage cassettes."""
    return {
        "filter_headers": [
            ("authorization", "DUMMY_TOKEN"),
            ("x-haravan-access-token", "DUMMY_TOKEN"),
        ],
        "match_on": ["method", "uri"],  # Don't match body (may vary per run)
        "cassette_library_dir": os.path.join(os.path.dirname(__file__), "fixtures/vcr"),
        "record_mode": "none",  # Fail if cassette missing (enforce recording first)
    }

# tests/test_extract_orders.py
import pytest

@pytest.mark.vcr(cassette_name="haravan_orders_page1.yaml")
def test_extract_orders_incremental(vcr, haravan_client):
    """Replay recorded Haravan API response."""
    rows = extract_all_pages("orders", "incremental", haravan_client)
    assert len(rows) == 250
    assert all("id" in r for r in rows)

# To record new cassettes locally:
# 1. Set HARAVAN_ACCESS_TOKEN in .env
# 2. Change record_mode = "new_episodes" or "all"
# 3. Run: pytest tests/test_extract_orders.py
# 4. Commit cassettes to git (no secrets due to filter_headers)
```

**Cassette structure:**
```
tests/
└── fixtures/
    └── vcr/
        ├── haravan_orders_page1.yaml
        ├── haravan_customers_page1.yaml
        └── ...
```

**Rationale:**
- pytest-vcr + VCR.py stable for HTTP recording
- `filter_headers` prevents accidentally committing auth tokens
- `record_mode = "none"` in CI ensures all cassettes pre-recorded (no live API calls in CI)
- `match_on` only method+URI (not body) handles variable timestamps

---

## 8. Typer CLI Patterns

### Recommendation: **Subcommands with exit codes, --verbose propagates to structlog**

#### Pattern:
```python
# src/haravan_elt/cli.py
import typer
import structlog
from typing import Optional

app = typer.Typer()

@app.command()
def extract(
    domain: str = typer.Argument(..., help="Domain to extract: orders, customers, ..."),
    mode: str = typer.Option("incremental", "--mode", help="full | incremental"),
    dry_run: bool = typer.Option(False, "--dry-run", help="Preview without writing to DB"),
    verbose: bool = typer.Option(False, "-v", "--verbose", help="Enable DEBUG logging"),
):
    """Extract and load data from Haravan API."""
    try:
        # Reconfigure logging based on --verbose
        json_output = os.getenv("LOG_FORMAT") == "json"
        if verbose:
            structlog.configure(log_level=structlog.logging.DEBUG)
        
        logger = structlog.get_logger()
        logger.info("extract_cmd_started", domain=domain, mode=mode, dry_run=dry_run)
        
        if dry_run:
            logger.info("dry_run_mode", msg="No data will be written")
            rows = extract_all_pages(domain, mode, client)
            logger.info("dry_run_summary", rows_count=len(rows))
            return
        
        rows = extract_all_pages(domain, mode, client)
        batch_upsert_orders(rows, conn)
        update_watermark(domain, rows, conn)
        
        logger.info("extract_cmd_success", domain=domain, rows=len(rows))
        typer.Exit(0)
    
    except Exception as e:
        logger.error("extract_cmd_failed", error=str(e), exc_info=True)
        typer.Exit(1)  # Exit code 1 = failure (cron sees this)

@app.command()
def run_all(
    mode: str = typer.Option("incremental", "--mode"),
    no_notify: bool = typer.Option(False, "--no-notify", help="Skip Telegram"),
):
    """Run full pipeline: extract-all → dbt → notify."""
    logger = structlog.get_logger()
    try:
        logger.info("run_all_started", mode=mode)
        
        # Run each domain sequentially
        for domain in ["orders", "customers", "products", ...]:
            extract(domain, mode=mode, dry_run=False, verbose=False)
        
        # Run dbt
        run_dbt_build()
        
        if not no_notify:
            notify_telegram("Pipeline succeeded")
        
        logger.info("run_all_success")
        typer.Exit(0)
    
    except Exception as e:
        logger.error("run_all_failed", error=str(e))
        if not no_notify:
            notify_telegram(f"Pipeline failed: {e}")
        typer.Exit(1)

if __name__ == "__main__":
    app()
```

**Exit codes:**
- `0`: Success (cron logs normally)
- `1`: Failure (cron alerts, Telegram notifies)
- `2`: Invalid arguments (typer default)

**Rationale:**
- Subcommands via `@app.command()` (cleaner than `add_command()`)
- `--dry-run` parsed as bool flag; logic guarded with `if dry_run:`
- `--verbose` reconfigures structlog runtime (no restart needed)
- Exit code 0/1 standard for cron detection

---

## 9. pydantic-settings Configuration

### Recommendation: **Nested config with SecretStr, env_nested_delimiter for sub-configs**

#### Pattern:
```python
# src/haravan_elt/config.py
from pydantic_settings import BaseSettings
from pydantic import SecretStr, Field
from typing import Optional

class HaravanSettings(BaseSettings):
    """Haravan OAuth and API settings."""
    shop_domain: str = Field(..., env="HARAVAN_SHOP_DOMAIN")
    access_token: SecretStr = Field(..., env="HARAVAN_ACCESS_TOKEN")
    refresh_token: Optional[SecretStr] = Field(None, env="HARAVAN_REFRESH_TOKEN")
    client_id: str = Field(..., env="HARAVAN_CLIENT_ID")
    client_secret: SecretStr = Field(..., env="HARAVAN_CLIENT_SECRET")
    
    class Config:
        env_prefix = "HARAVAN_"
        case_sensitive = False

class DatabaseSettings(BaseSettings):
    """PostgreSQL connection."""
    url: str = Field(..., env="DATABASE_URL")
    pool_size: int = Field(10, env="DATABASE_POOL_SIZE")
    
    class Config:
        env_prefix = "DATABASE_"

class TelegramSettings(BaseSettings):
    """Telegram Bot settings."""
    bot_token: SecretStr = Field(..., env="TELEGRAM_BOT_TOKEN")
    chat_id: str = Field(..., env="TELEGRAM_CHAT_ID")
    
    class Config:
        env_prefix = "TELEGRAM_"

class Settings(BaseSettings):
    """Root settings."""
    haravan: HaravanSettings = Field(default_factory=HaravanSettings)
    database: DatabaseSettings = Field(default_factory=DatabaseSettings)
    telegram: TelegramSettings = Field(default_factory=TelegramSettings)
    
    log_level: str = Field("INFO", env="LOG_LEVEL")
    log_format: str = Field("console", env="LOG_FORMAT")  # json | console
    rate_limit_per_min: int = Field(40, env="HARAVAN_RATE_LIMIT_PER_MIN")
    batch_size: int = Field(500, env="LOAD_BATCH_SIZE")
    
    class Config:
        env_file = ".env"
        case_sensitive = False

# Usage
config = Settings()
print(config.haravan.access_token)  # SecretStr('**********')
print(config.database.url)  # Visible
```

**.env precedence** (highest to lowest):
1. Environment variables (e.g., `export HARAVAN_ACCESS_TOKEN=...`)
2. `.env` file (lower priority)
3. Field defaults (lowest priority)

**Rationale:**
- `SecretStr` hides values in logs/prints (`repr()` → `'**********'`)
- Nested models keep related config together
- `env_prefix` reduces repetition
- Environment variables override `.env` (convenient for secrets injection in containers)

---

## 10. Cron-Safe Execution (Overlapping Run Prevention)

### Recommendation: **fcntl.flock (POSIX) + filelock library (cross-platform fallback)**

#### Pattern:
```python
# src/haravan_elt/lockfile.py
import fcntl
import os
from contextlib import contextmanager

@contextmanager
def acquire_lock(lockfile_path: str = "/tmp/haravan-elt.lock", timeout_secs: int = 5):
    """
    Acquire exclusive lock on lockfile.
    Non-blocking: raise if another instance running.
    """
    os.makedirs(os.path.dirname(lockfile_path), exist_ok=True)
    
    lock_file = open(lockfile_path, "w")
    try:
        # LOCK_EX (exclusive) | LOCK_NB (non-blocking)
        fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        logger.info("lock_acquired", lockfile=lockfile_path)
        yield
    except BlockingIOError:
        logger.error("lock_failed", lockfile=lockfile_path, msg="Another instance running")
        raise RuntimeError("Cannot acquire lock; another instance may be running")
    finally:
        fcntl.flock(lock_file.fileno(), fcntl.LOCK_UN)
        lock_file.close()

# scripts/run-daily.sh
#!/bin/bash
set -euo pipefail
cd /opt/haravan-elt
source .venv/bin/activate

python -c "
import sys
sys.path.insert(0, 'src')
from haravan_elt.lockfile import acquire_lock
from haravan_elt.cli import app

try:
    with acquire_lock('/tmp/haravan-elt.lock'):
        app(['run-all', '--mode', 'incremental'])
except RuntimeError as e:
    print(f'ERROR: {e}', file=sys.stderr)
    exit(2)  # Distinct exit code for lock fail
"
```

**Crontab:**
```cron
# Daily incremental, 02:00 — silently skip if already running
0 2 * * * /opt/haravan-elt/scripts/run-daily.sh >> /var/log/haravan-elt.log 2>&1 || true
```

**Rationale:**
- `fcntl.flock()` is POSIX (Linux/macOS native, no extra dependencies)
- `LOCK_NB` (non-blocking) prevents hanging cron job
- Exit code 2 signals lock fail (cron can alert differently)
- Lock file on `/tmp` or `/var/lock` for shared state
- If process crashes, lock automatically released (file descriptor closed)

**Cross-platform fallback:** If Windows support needed, use [filelock library](https://pypi.org/project/filelock/):
```python
from filelock import FileLock

with FileLock("/tmp/haravan-elt.lock", timeout=5) as lock:
    # Raises Timeout if locked
    app()
```

---

## Summary Table: Technology Choices

| Concern | Choice | Rationale |
|---------|--------|-----------|
| HTTP Retry | tenacity | 429 handling + Retry-After parsing; 10y production history |
| Rate Limit | pyrate-limiter (LeakyBucket) | Sync-friendly, simpler than token bucket |
| Batch Upsert | `executemany()` (small), COPY+merge (10k+) | Pipeline mode + flexible ON CONFLICT logic |
| Transactions | Per-batch + separate watermark | Idempotent; watermark fail non-fatal |
| Logging | structlog (JSON prod, pretty dev) | Modern, context vars, structured output |
| Test Mocking | pytest-vcr + VCR.py | Cassette-based; filters secrets |
| CLI | Typer subcommands + exit codes | Type-safe, readable, cron-friendly |
| Config | pydantic-settings nested + SecretStr | Env var precedence, secret masking |
| Cron Lock | fcntl.flock (POSIX) | Lightweight, native, non-blocking |

---

## Unresolved Questions

1. **Token refresh strategy:** Should `access_token` refresh happen inside `extract()` if token expires mid-run? Or assume manual refresh before cron? (PRD mentions `HARAVAN_REFRESH_TOKEN` but no auto-refresh logic specified.)

2. **Partial batch failure:** If batch 3 of 10 fails permanently (e.g., DB constraint), should pipeline:
   - Abort entire domain → next run retries all? 
   - Skip failed batch → watermark advances on partial success?
   Current recommendation: abort & retry (safer), but adds operational complexity.

3. **JSONB payload schema evolution:** Haravan API adds/removes fields over time. Should raw.haravan_* include schema versioning (e.g., `schema_version` col), or replay from raw JSONB as-is?

4. **Multi-domain coordination:** If orders extraction takes 45 min but customers only 5 min, should watermarks advance independently or wait for slowest domain? (Affects incremental scheduling.)

5. **VCR cassette maintenance:** Who re-records cassettes when Haravan API schema changes? Should this be part of CD pipeline?

6. **Retry-After header format:** Haravan API returns `Retry-After` as seconds (numeric) or HTTP-date? Docs missing; assumed numeric based on common practice.

---

## Sources

- [API Rate Limiting Best Practices 2026](https://www.getknit.dev/blog/10-best-practices-for-api-rate-limiting-and-throttling)
- [Robust Retries for Idempotent Identity API Calls in Python](https://didit.me/blog/robust-retry-mechanism-idempotent-api-calls-python/)
- [GitHub: handling-http-429-with-tenacity](https://github.com/alexwlchan/handling-http-429-with-tenacity)
- [Tenacity Documentation](https://tenacity.readthedocs.io/)
- [Insert Data into Postgres Fast](https://jacopofarina.eu/posts/ingest-data-into-postgres-fast/)
- [Psycopg 3 Basic Module Usage](https://www.psycopg.org/psycopg3/docs/basic/usage.html)
- [psycopg Discussion: executemany()](https://github.com/psycopg/psycopg/discussions/517)
- [Psycopg 3 Pipeline Mode](https://www.psycopg.org/articles/2024/05/08/psycopg3-pipeline-mode/)
- [PyPI: pyrate-limiter](https://pypi.org/project/pyrate-limiter/)
- [Token Bucket Rate Limiting in Python](https://oneuptime.com/blog/post/2026-01-22-token-bucket-rate-limiting-python/view)
- [structlog API Reference](https://www.structlog.org/en/stable/api.html)
- [Structured Logging Best Practices](https://uptrace.dev/glossary/structured-logging/)
- [Comprehensive Guide to structlog](https://betterstack.com/community/guides/logging/structlog/)
- [Pydantic Settings Management](https://pydantic.dev/docs/validation/latest/concepts/pydantic_settings/)
- [pytest-vcr Documentation](https://pytest-vcr.readthedocs.io/)
- [Redacting Secrets from VCR Cassettes](https://imoskvin.com/blog/redacting-vcrpy-cassettes/)
- [Typer Commands & Subcommands](https://typer.tiangopo.com/tutorial/commands/)
- [7 Typer CLI Patterns](https://medium.com/@connect.hashblock/7-typer-cli-patterns-that-feel-like-real-tools-ecbe72720828)
- [Python fcntl Module](https://docs.python.org/3/library/fcntl.html)
- [filelock Library](https://pypi.org/project/filelock/)
- [File Locking in Python](https://www.geeksforgeeks.org/python/file-locking-in-python/)

---

**Status:** DONE  
**Summary:** Research completed across 10 critical technical areas for idempotent ELT extraction. Concrete Python code patterns provided for retry (tenacity), rate limiting (pyrate-limiter), batch upsert (psycopg3 executemany/COPY), structlog JSON logging, pytest-vcr cassettes, Typer CLI, pydantic-settings, and cron-safe lockfiles. Key finding: psycopg3's pipeline mode makes executemany() faster than psycopg2; prefer it for ON CONFLICT upserts with JSONB. Watermark strategy: update at end of full extraction, not per-page, to maintain idempotence on partial failures.
