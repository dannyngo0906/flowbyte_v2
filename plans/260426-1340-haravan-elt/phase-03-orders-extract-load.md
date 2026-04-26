# Phase 03 — Orders Extract + Load (M1)

## Context Links

- PRD §4.2 (FR-E2/E4/E5/E6/E7), §4.3 (FR-L1–L5), §7.2 (fct_orders), §8 (meta tables)
- API endpoint: `GET /com/orders.json` — see `Haravan-API-Endpoints.md`
- Research (idempotent §3 batch upsert, §4 transactions, §5 watermark): `plans/reports/researcher-260426-1340-idempotent-elt-python.md`
- Research (API quirks §2 pagination, §4 filters, §7 nested refunds): `plans/reports/researcher-260426-1340-haravan-api-quirks.md`
- Phase-01 (config + meta), phase-02 (HaravanClient)

## Overview

- **Priority:** high
- **Status:** pending
- **Effort:** 3 days
- **Description:** End-to-end Orders extractor: paginate `/com/orders.json` → upsert to `raw.haravan_orders` (JSONB) via psycopg3 `executemany` + `ON CONFLICT DO UPDATE` → update `meta.sync_state` watermark in separate tx → write `meta.run_log` row. Idempotent on partial failure.

## Key Insights

- **Pagination is offset-based** (`page` + `limit`), max likely 250 — start with `limit=50` per research §2 caution; bump to 250 once confirmed safe per endpoint.
- **EOF detection:** response array length `< limit` ends loop — Haravan returns no `Link` header, no total count in list response.
- **Refunds + transactions embedded in order JSON** — NO separate fetch loop. Parse later in dbt staging (phase-05).
- **Watermark = max(updated_at) across all pages**, applied AFTER load tx commits, in separate tx with `GREATEST()` to prevent regression on parallel run.
- **`updated_at_min` filter** uses shop timezone (Asia/Ho_Chi_Minh = +07:00) — store watermark as `TIMESTAMPTZ` in Postgres; pass to API as ISO 8601 with explicit offset.
- `executemany` with `Jsonb()` wrapper is correct path (psycopg3 §3 research).

## Requirements

**Functional:**
- FR-E2: Pagination handled internally
- FR-E4: `--mode full|incremental`
- FR-E5: Watermark in `meta.sync_state`
- FR-E6: Idempotent (re-run never duplicates)
- FR-E7: `--since` / `--until` ISO date params
- FR-L1: Table `raw.haravan_orders`
- FR-L2: Schema = `(id, payload, updated_at, ingested_at, source_run_id)`
- FR-L3: Upsert via `INSERT ... ON CONFLICT (id) DO UPDATE`
- FR-L4: Batch=500 default
- FR-L5: `meta.run_log` row per run

**Non-functional:**
- NFR-1: 10k orders < 5min on dev
- NFR-2: Idempotent (verified by re-run smoke test)

## Architecture

```
OrdersExtractor (extends BaseExtractor)
  ├─ iter_pages(since, until)  ─▶  yields list[dict] per API page
  │     ├─ page=1, limit=250 (or 50 conservative)
  │     ├─ params: updated_at_min=<watermark>, updated_at_max=<--until>, status=any
  │     └─ EOF: len(items) < limit
  │
  ▼
PostgresLoader.upsert_batch(table, rows, conflict_col="id")
  ├─ executemany(INSERT ... ON CONFLICT (id) DO UPDATE ...)
  ├─ Jsonb(payload), batch=500
  └─ inside `with conn.transaction()` — atomic per batch

State manager
  ├─ run_log: insert at start (status=running) → update at end (success/failed)
  ├─ sync_state: GREATEST(last_updated_at, EXCLUDED.last_updated_at) — separate tx
  └─ run_id: uuid4(), bound to structlog contextvar
```

DDL for `raw.haravan_orders` (run via `cli.py init` extension or migration script):
```sql
CREATE TABLE IF NOT EXISTS raw.haravan_orders (
    id            BIGINT PRIMARY KEY,
    payload       JSONB NOT NULL,
    updated_at    TIMESTAMPTZ NOT NULL,
    ingested_at   TIMESTAMPTZ DEFAULT now(),
    source_run_id UUID NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_haravan_orders_updated ON raw.haravan_orders(updated_at);
```

## Related Code Files

**Create:**
- `src/haravan_elt/extractors/orders.py`
- `src/haravan_elt/loaders/postgres.py`
- `src/haravan_elt/meta/state.py` (full impl)
- `src/haravan_elt/meta/raw_tables.sql` (DDL for `raw.haravan_orders`)
- `tests/test_orders_extractor.py`
- `tests/test_postgres_loader.py`
- `tests/test_state.py`
- `tests/fixtures/vcr/haravan_orders_full_3_pages.yaml`
- `tests/fixtures/vcr/haravan_orders_incremental_empty.yaml`

**Modify:**
- `src/haravan_elt/extractors/base.py` — finalize abstract methods, add `idempotent_load()` helper
- `src/haravan_elt/cli.py` — add `extract` subcommand (orders only at this phase)
- `src/haravan_elt/meta/schema.sql` — append `raw.haravan_orders` DDL (or import `raw_tables.sql`)

## Implementation Steps

1. **Finalize `extractors/base.py`:**
   ```python
   from abc import ABC, abstractmethod
   from collections.abc import Iterator
   from datetime import datetime
   from uuid import UUID

   class BaseExtractor(ABC):
       domain: str  # e.g., "orders"
       raw_table: str  # e.g., "raw.haravan_orders"
       conflict_col: str = "id"

       def __init__(self, client, loader, state, run_id: UUID):
           self.client = client; self.loader = loader; self.state = state; self.run_id = run_id

       @abstractmethod
       def iter_pages(self, since: datetime | None, until: datetime | None) -> Iterator[list[dict]]: ...

       @abstractmethod
       def to_raw_row(self, item: dict) -> dict: ...

       def idempotent_load(self, mode: str, since=None, until=None) -> tuple[int, datetime | None]:
           rows_total = 0
           max_updated = None
           if mode == "incremental" and since is None:
               since = self.state.get_watermark(self.domain)
           for page in self.iter_pages(since, until):
               rows = [self.to_raw_row(item) for item in page]
               if not rows: continue
               self.loader.upsert_batch(self.raw_table, rows, self.conflict_col)
               rows_total += len(rows)
               for r in rows:
                   if max_updated is None or r["updated_at"] > max_updated:
                       max_updated = r["updated_at"]
           return rows_total, max_updated
   ```

2. **`extractors/orders.py`:**
   ```python
   from datetime import datetime
   from psycopg.types.json import Jsonb
   from .base import BaseExtractor

   class OrdersExtractor(BaseExtractor):
       domain = "orders"
       raw_table = "raw.haravan_orders"

       def iter_pages(self, since, until):
           page = 1; limit = 250
           while True:
               params = {"page": page, "limit": limit, "status": "any"}
               if since: params["updated_at_min"] = since.isoformat()
               if until: params["updated_at_max"] = until.isoformat()
               resp = self.client.get("/com/orders.json", params=params)
               items = resp.json().get("orders", [])
               if not items:
                   break
               yield items
               if len(items) < limit:
                   break
               page += 1

       def to_raw_row(self, item):
           return {
               "id": item["id"],
               "payload": Jsonb(item),
               "updated_at": datetime.fromisoformat(item["updated_at"].replace("Z", "+00:00")),
               "source_run_id": str(self.run_id),
           }
   ```

3. **`loaders/postgres.py`:**
   ```python
   import psycopg, structlog
   from psycopg import sql

   logger = structlog.get_logger()

   class PostgresLoader:
       def __init__(self, dsn: str):
           self._dsn = dsn

       def upsert_batch(self, table: str, rows: list[dict], conflict_col: str = "id", batch_size: int = 500):
           if not rows:
               return 0
           cols = list(rows[0].keys())  # id, payload, updated_at, source_run_id
           col_idents = sql.SQL(", ").join(sql.Identifier(c) for c in cols)
           placeholders = sql.SQL(", ").join(sql.Placeholder() * len(cols))
           updates = sql.SQL(", ").join(
               sql.SQL("{c}=EXCLUDED.{c}").format(c=sql.Identifier(c))
               for c in cols if c != conflict_col
           )
           schema, tbl = table.split(".", 1)
           query = sql.SQL(
               "INSERT INTO {schema}.{tbl} ({cols}, ingested_at) "
               "VALUES ({ph}, now()) "
               "ON CONFLICT ({k}) DO UPDATE SET {upd}, ingested_at=now() "
               "WHERE EXCLUDED.updated_at >= {schema}.{tbl}.updated_at"
           ).format(
               schema=sql.Identifier(schema), tbl=sql.Identifier(tbl),
               cols=col_idents, ph=placeholders,
               k=sql.Identifier(conflict_col), upd=updates,
           )
           total = 0
           with psycopg.connect(self._dsn) as conn:
               with conn.cursor() as cur:
                   for i in range(0, len(rows), batch_size):
                       chunk = rows[i:i+batch_size]
                       data = [tuple(r[c] for c in cols) for r in chunk]
                       cur.executemany(query, data)
                       total += cur.rowcount
                       logger.info("upsert_batch", table=table, batch=i//batch_size, rows=len(chunk))
                   conn.commit()
           return total
   ```

4. **`meta/state.py`:**
   ```python
   import psycopg, uuid, structlog
   from datetime import datetime
   from psycopg.rows import dict_row

   logger = structlog.get_logger()

   class StateManager:
       def __init__(self, dsn: str):
           self._dsn = dsn

       def get_watermark(self, domain: str) -> datetime | None:
           with psycopg.connect(self._dsn, row_factory=dict_row) as conn:
               cur = conn.execute("SELECT last_updated_at FROM meta.sync_state WHERE domain=%s", (domain,))
               row = cur.fetchone()
               return row["last_updated_at"] if row else None

       def update_watermark(self, domain: str, ts: datetime, run_id: uuid.UUID) -> None:
           # SEPARATE tx, GREATEST to prevent regression
           with psycopg.connect(self._dsn) as conn:
               conn.execute("""
                   INSERT INTO meta.sync_state (domain, last_updated_at, last_run_id, updated_at)
                   VALUES (%s, %s, %s, now())
                   ON CONFLICT (domain) DO UPDATE SET
                       last_updated_at = GREATEST(meta.sync_state.last_updated_at, EXCLUDED.last_updated_at),
                       last_run_id = EXCLUDED.last_run_id,
                       updated_at = now()
               """, (domain, ts, run_id))
               conn.commit()

       def start_run(self, run_id, domain, mode, triggered_by="manual"):
           with psycopg.connect(self._dsn) as conn:
               conn.execute("""
                   INSERT INTO meta.run_log (run_id, domain, mode, started_at, status, triggered_by)
                   VALUES (%s, %s, %s, now(), 'running', %s)
               """, (run_id, domain, mode, triggered_by))
               conn.commit()

       def end_run(self, run_id, rows, status, error=None):
           with psycopg.connect(self._dsn) as conn:
               conn.execute("""
                   UPDATE meta.run_log
                   SET ended_at=now(), rows_ingested=%s, status=%s, error_message=%s
                   WHERE run_id=%s
               """, (rows, status, error, run_id))
               conn.commit()
   ```

5. **`meta/raw_tables.sql`:** declare `raw.haravan_orders` (DDL above) — append to schema init or create migration runner.

6. **Wire `cli.py extract`:**
   ```python
   @app.command()
   def extract(
       domain: str,
       mode: str = "incremental",
       since: str | None = None,
       until: str | None = None,
       dry_run: bool = False,
   ) -> None:
       run_id = uuid.uuid4()
       structlog.contextvars.bind_contextvars(run_id=str(run_id), domain=domain)
       settings = Settings()
       client = HaravanClient(settings)
       loader = PostgresLoader(settings.database.database_url)
       state = StateManager(settings.database.database_url)
       extractor = {"orders": OrdersExtractor}[domain](client, loader, state, run_id)
       state.start_run(run_id, domain, mode)
       try:
           rows, max_ts = extractor.idempotent_load(mode,
                                                    since=datetime.fromisoformat(since) if since else None,
                                                    until=datetime.fromisoformat(until) if until else None)
           if max_ts and not dry_run:
               state.update_watermark(domain, max_ts, run_id)
           state.end_run(run_id, rows, "success")
           typer.echo(f"OK {domain}: {rows} rows, watermark={max_ts}")
       except Exception as exc:
           state.end_run(run_id, 0, "failed", str(exc)[:1000])
           typer.echo(f"FAIL {domain}: {exc}", err=True)
           raise typer.Exit(1)
   ```

7. **Tests:**
   - `test_orders_extractor.py`: VCR fixture `haravan_orders_full_3_pages.yaml` (50+50+12 rows) → assert generator yields 3 pages, total 112 rows
   - `test_orders_extractor.py`: incremental empty → 0 rows
   - `test_postgres_loader.py`: spin up Postgres test container (or fixture), insert 600 rows → assert 600 rows in DB; insert same 600 again with newer `updated_at` → assert PK count still 600, updated
   - `test_state.py`: watermark regression — write `2026-04-01`, then write `2026-03-01` → assert stored remains `2026-04-01` (GREATEST works)
   - `test_postgres_loader.py`: idempotency — re-run same load 3x → row count never doubles

## Todo List

- [x] Finalize `extractors/base.py` (BaseExtractor abstract + idempotent_load helper)
- [x] Implement `extractors/orders.py` (iter_pages with `updated_at_min`, EOF on `len < limit`, embedded refunds passthrough)
- [x] Implement `loaders/postgres.py` (executemany, Jsonb, ON CONFLICT, WHERE EXCLUDED.updated_at >= ...)
- [x] Implement `meta/state.py` (get/update watermark with GREATEST, run_log start/end)
- [x] Add `meta/raw_tables.sql` for `raw.haravan_orders` + index on updated_at
- [x] Extend `cli.py` `extract` subcommand wiring (run_id, contextvars, exit codes)
- [ ] Record VCR cassettes (3-page full + empty incremental)  <!-- DEFERRED: respx mocks used; phase-10 cassette pass -->
- [x] Write `test_orders_extractor.py`, `test_postgres_loader.py`, `test_state.py`
- [ ] Verify idempotency manually: live-token run gated to phase-10  <!-- DEFERRED: integration tests cover contract; live run pending token -->
- [x] Verify watermark advance: incremental run after full → 0 rows ingested if no API changes

## Success Criteria

- `haravan-elt extract orders --mode full --until 2026-04-25` ingests N rows, exit 0
- `haravan-elt extract orders --mode incremental` re-run twice in a row: 2nd run ingests 0 rows
- `meta.run_log` shows 2 success rows
- `meta.sync_state.orders.last_updated_at` matches max payload `updated_at`
- Tests ≥80% coverage of `extractors/orders.py`, `loaders/postgres.py`, `meta/state.py`
- Tests verify GREATEST regression guard

## Risk Assessment

| Risk | Likelihood | Impact | Mitigation |
|------|-----------|--------|-----------|
| Haravan returns max 50/page instead of 250 | Medium | Med | Param `EXTRACT_BATCH_SIZE` env-controlled; start at 50 |
| `updated_at` ISO format includes microseconds → fromisoformat fails | Low | Low | Use `datetime.fromisoformat(s.replace("Z","+00:00"))`; covered by test |
| Watermark off-by-microsecond → next run misses boundary record | Low | High | API filter is `>=`-ish (verify); add 1ms buffer or rely on PK upsert idempotency |
| psycopg3 `executemany` returns -1 rowcount | Low | Low | Don't trust rowcount for total; count rows in Python |

## Security Considerations

- `dsn` passed via env (`DATABASE_URL`); never echoed in logs
- SQL composed via `psycopg.sql.Identifier` / `Placeholder` — no string concat (SQL injection safe)
- `run_log.error_message` truncated to 1000 chars to prevent DB bloat from huge tracebacks

## Next Steps

Unblocks **phase-04** (multi-domain pattern reuses BaseExtractor + Loader + State).

## Unresolved Questions

- Q1: Does `/com/orders.json` accept `status=any` or require explicit list? Confirm on first live test.
- Q2: What is the actual max `limit` for orders endpoint? Default to 50, escalate to 250 after live confirmation.
