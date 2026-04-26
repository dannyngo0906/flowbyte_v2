# Phase 11 — Post-MVP Domains + Polish (M7)

## Context Links

- PRD §4.1 P2 row (Discounts/Promotions/Events), §11 M7 row, §12 risk row "Token hết hạn ngoài giờ"
- API: `Haravan-API-Endpoints.md` § Discounts (Discount Codes + Promotions), § Events
- Research: `plans/reports/researcher-260426-1340-haravan-api-quirks.md` (OAuth refresh rotation), `plans/reports/researcher-260426-1340-dbt-postgres-patterns.md` (date_spine + seed)
- Depends on: phase-10 (hardening done before extending domains in production)

## Overview

- **Priority:** medium
- **Status:** pending
- **Effort:** 5 days
- **Description:** Add P2 domains (Discounts, Promotions, Events), polish refresh token auto-rotation (atomic .env write-back), seed VN holidays into `dim_date`. All MVP-grade tests + dbt tests carry over.

## Key Insights

- **Discount Codes vs Promotions:** Haravan exposes 2 separate endpoints — `/com/discounts.json` (code-based) and `/com/promotions.json` (auto-applied). Treat as 2 sibling domains; share dim if business logic warrants (most BI uses them combined).
- **Events = audit log, append-only:** `/com/events.json` returns ordered events with `created_at`. No update needed → incremental on `id` ascending; no high watermark on `updated_at`.
- **Refresh token rotation (research-confirmed):** every refresh returns NEW refresh_token (30d rolling TTL). Atomic write: write to `.env.tmp` → `os.replace(.env.tmp, .env)` to avoid partial reads under cron concurrency.
- **VN holidays:** generate via Python `holidays` library (BSD license) snapshotted to CSV seed; covers Tết Dương lịch, Tết Nguyên đán (lunar), Giỗ tổ, 30/4, 1/5, 2/9. Static seed avoids runtime dep.
- **dim_date enrichment:** join `dbt_utils.date_spine` with `vn_holidays` seed → flag `is_public_holiday`, `holiday_name`, `is_weekend`, `is_business_day`.
- **fct_promotions_used (optional):** join discount/promotion to orders via `discount_code` field if present in order payload. Skip if Haravan doesn't expose; capture via dbt staging exploration.

## Requirements

**Functional:**
- FR-E1..E7 carry over for new domains (auth, pagination, rate limit, idempotent, since/until, mode toggle)
- FR-L1..L5 carry over (raw schema, upsert, batch, run_log)
- FR-T1..T6: dbt staging + (optional) marts for new domains
- Token auto-rotation: refresh writes back to `.env` atomically; Telegram alert if write fails

**Non-functional:**
- NFR-2 (idempotent): re-running discounts/promotions extract must not duplicate
- NFR-4 (security): atomic file replace prevents `.env` corruption; fchmod 600 preserved after write

## Architecture

```
src/haravan_elt/extractors/
  ├─ discounts.py       # /com/discounts.json
  ├─ promotions.py      # /com/promotions.json
  └─ events.py          # /com/events.json (incremental on id ascending)

src/haravan_elt/client/haravan.py
  └─ refresh_token() → atomic .env write
       1. response = POST /connect/token grant=refresh_token
       2. with FileLock(".env.lock"): write .env.tmp
       3. os.replace(".env.tmp", ".env")
       4. os.chmod(".env", 0o600)
       5. on failure → telegram.send_warning() + raise

dbt/seeds/
  └─ vn_holidays.csv    # date,holiday_name,is_public_holiday

dbt/models/marts/core/
  └─ dim_date.sql       # date_spine LEFT JOIN vn_holidays seed
                          adds is_public_holiday, holiday_name,
                          is_weekend, is_business_day, fiscal_year, fiscal_quarter

dbt/models/staging/haravan/
  ├─ stg_haravan__discounts.sql
  ├─ stg_haravan__promotions.sql
  └─ stg_haravan__events.sql
```

## Related Code Files

**Create:**
- `src/haravan_elt/extractors/discounts.py`
- `src/haravan_elt/extractors/promotions.py`
- `src/haravan_elt/extractors/events.py`
- `src/haravan_elt/meta/schema_p2.sql` — `raw.haravan_discounts`, `raw.haravan_promotions`, `raw.haravan_events`
- `dbt/seeds/vn_holidays.csv`
- `dbt/models/staging/haravan/stg_haravan__discounts.sql`
- `dbt/models/staging/haravan/stg_haravan__promotions.sql`
- `dbt/models/staging/haravan/stg_haravan__events.sql`
- `tests/extractors/test_discounts.py`, `test_promotions.py`, `test_events.py` (with VCR cassettes)
- `tests/fixtures/vcr/discounts_*.yaml`, `promotions_*.yaml`, `events_*.yaml`
- `scripts/generate_vn_holidays_seed.py` — one-shot script (uses `holidays` library) to (re)generate CSV. Not run at runtime.

**Modify:**
- `src/haravan_elt/client/haravan.py` — replace stub refresh with atomic `.env` write-back
- `src/haravan_elt/cli.py` — register new extractors in `extract` command domain choices; include in `--all` order (after orders/customers/products)
- `src/haravan_elt/pipeline.py` — append discounts/promotions/events to `run-all` sequence
- `dbt/models/marts/core/dim_date.sql` — replace placeholder with date_spine + holidays join
- `dbt/dbt_project.yml` — register `seeds:` path, `quote_columns: true`
- `dbt/models/staging/haravan/sources.yml` — add 3 new tables
- `README.md` — document `holidays` regen script + P2 domain coverage

**Delete:** none

## Implementation Steps

1. **Generate VN holidays seed** (one-shot, run locally):
   ```bash
   pip install holidays==0.59  # dev-only, not project dep
   python scripts/generate_vn_holidays_seed.py --start 2020 --end 2035 \
     --out dbt/seeds/vn_holidays.csv
   ```
   `generate_vn_holidays_seed.py` skeleton:
   ```python
   import csv, holidays
   from datetime import date
   def main(start: int, end: int, out: str):
       vn = holidays.Vietnam(years=range(start, end+1))
       with open(out, "w", newline="") as f:
           w = csv.writer(f)
           w.writerow(["date","holiday_name","is_public_holiday"])
           for d, name in sorted(vn.items()):
               w.writerow([d.isoformat(), name, "true"])
   ```

2. **dbt seeds config** in `dbt_project.yml`:
   ```yaml
   seeds:
     haravan_elt:
       vn_holidays:
         +column_types:
           date: date
           holiday_name: varchar(100)
           is_public_holiday: boolean
   ```
   Run `dbt seed --select vn_holidays` to load.

3. **Replace `dim_date.sql`** placeholder:
   ```sql
   {{ config(materialized='table') }}
   with spine as (
     {{ dbt_utils.date_spine(
         datepart="day",
         start_date="cast('2020-01-01' as date)",
         end_date="cast('2035-12-31' as date)"
     ) }}
   ),
   enriched as (
     select
       date_day::date as date,
       extract(year from date_day)  as year,
       extract(quarter from date_day) as quarter,
       extract(month from date_day) as month,
       extract(day from date_day)   as day,
       extract(dow from date_day)   as day_of_week,
       case when extract(dow from date_day) in (0,6) then true else false end as is_weekend,
       to_char(date_day, 'YYYY-MM-DD') as date_key
     from spine
   )
   select
     e.*,
     coalesce(h.is_public_holiday, false) as is_public_holiday,
     h.holiday_name,
     case when e.is_weekend or coalesce(h.is_public_holiday, false) then false else true end as is_business_day
   from enriched e
   left join {{ ref('vn_holidays') }} h on h.date = e.date
   ```

4. **Schema migrations** for P2 raw tables — append to `src/haravan_elt/meta/schema_p2.sql`:
   ```sql
   CREATE TABLE IF NOT EXISTS raw.haravan_discounts (
     id BIGINT PRIMARY KEY, payload JSONB NOT NULL,
     updated_at TIMESTAMPTZ NOT NULL,
     ingested_at TIMESTAMPTZ DEFAULT now(),
     source_run_id UUID NOT NULL
   );
   -- repeat for haravan_promotions, haravan_events
   -- events: PK on id, no updated_at semantics → use created_at
   ```
   `haravan-elt init` should idempotently apply both `schema.sql` + `schema_p2.sql`.

5. **Discounts extractor** — copy `extractors/customers.py` template; endpoint `/com/discounts.json`; supports `since/until` if API allows (test live), else full refresh only with WARN.

6. **Promotions extractor** — same shape; `/com/promotions.json`.

7. **Events extractor** — different watermark logic: order by `id ASC`, paginate until empty; track `last_event_id` in `meta.sync_state` (use `last_updated_at` column with `to_timestamp(id::text)` cast workaround OR add column `last_high_id BIGINT`). Recommendation: add `last_high_id` column to `meta.sync_state` for events-style domains. Document in phase Implementation Step 7a.

   7a. Schema migration for `meta.sync_state`:
   ```sql
   ALTER TABLE meta.sync_state ADD COLUMN IF NOT EXISTS last_high_id BIGINT;
   ```
   Use `last_updated_at` for time-based domains, `last_high_id` for append-only event-style domains.

8. **dbt staging models** — JSONB → typed cols per discount/promotion/event payload. `stg_haravan__discounts.sql` example columns: discount_id, code, value_type, value, applies_to, starts_at, ends_at, usage_count, status.

9. **Atomic refresh token write-back** — replace stub in `client/haravan.py`:
   ```python
   import os, tempfile
   from filelock import FileLock  # add to deps
   def _persist_tokens(env_path: Path, access: str, refresh: str) -> None:
       lock = FileLock(str(env_path) + ".lock")
       with lock.acquire(timeout=5):
           lines = env_path.read_text().splitlines()
           updated = []
           seen = {"HARAVAN_ACCESS_TOKEN": False, "HARAVAN_REFRESH_TOKEN": False}
           for ln in lines:
               if ln.startswith("HARAVAN_ACCESS_TOKEN="):
                   updated.append(f"HARAVAN_ACCESS_TOKEN={access}"); seen["HARAVAN_ACCESS_TOKEN"]=True
               elif ln.startswith("HARAVAN_REFRESH_TOKEN="):
                   updated.append(f"HARAVAN_REFRESH_TOKEN={refresh}"); seen["HARAVAN_REFRESH_TOKEN"]=True
               else:
                   updated.append(ln)
           for k, v in [("HARAVAN_ACCESS_TOKEN", access), ("HARAVAN_REFRESH_TOKEN", refresh)]:
               if not seen[k]: updated.append(f"{k}={v}")
           tmp = tempfile.NamedTemporaryFile("w", delete=False, dir=env_path.parent)
           try:
               tmp.write("\n".join(updated) + "\n"); tmp.close()
               os.replace(tmp.name, env_path)
               os.chmod(env_path, 0o600)
           except Exception:
               os.unlink(tmp.name); raise
   ```
   Add `filelock` to `pyproject.toml` deps (small, well-maintained).

10. **Pipeline integration** — update `pipeline.py` `run-all` sequence:
    ```
    locations → customers → products → variants → orders
    → inventory_adjustments → inventory_balance → collections (P1)
    → discounts → promotions → events (P2)
    → dbt run + dbt test
    ```

11. **Tests** — VCR cassettes for each new extractor; integration test for atomic refresh write (uses tmp `.env` fixture).

12. **README update** — section "P2 domains" + "Refreshing VN holidays seed" + "Re-generating tokens".

## Todo List

- [ ] Add `filelock`, `holidays` (dev-only) to deps
- [ ] Write `scripts/generate_vn_holidays_seed.py`
- [ ] Generate `dbt/seeds/vn_holidays.csv` for 2020–2035
- [ ] Configure `seeds:` in `dbt_project.yml`
- [ ] Run `dbt seed`
- [ ] Replace `dim_date.sql` placeholder with date_spine + holidays join
- [ ] Add `meta/schema_p2.sql` for 3 new raw tables + `last_high_id` column on `sync_state`
- [ ] Implement `extractors/discounts.py`
- [ ] Implement `extractors/promotions.py`
- [ ] Implement `extractors/events.py` (id-ascending pagination)
- [ ] Add 3 staging dbt models with sources.yml entries + tests
- [ ] Replace stub refresh with atomic `.env` write-back (filelock + os.replace)
- [ ] Telegram warning on refresh failure (re-uses notifier from phase-08)
- [ ] Append P2 domains to `cli.py` choices + `pipeline.run_all` order
- [ ] VCR cassettes for 3 new extractors (record locally, commit with `record_mode='none'`)
- [ ] Integration test: atomic refresh write (mock token endpoint, fixture .env)
- [ ] Update README: P2 domains coverage + holidays regen + tokens recovery

## Success Criteria

- [ ] `haravan-elt extract discounts --mode full` populates `raw.haravan_discounts`, idempotent
- [ ] `haravan-elt extract events` paginates by id ascending; resumable
- [ ] `dbt build --select dim_date+ stg_haravan__discounts+ stg_haravan__promotions+ stg_haravan__events+` 100% pass
- [ ] `dim_date` covers 2020–2035 with VN public holidays flagged correctly (spot-check 30/4, 1/5, Tết 2026)
- [ ] Forced 401 in test triggers refresh; new tokens written atomically; `.env` chmod stays 600; lock file released
- [ ] Pipeline `run-all` runs all P0+P1+P2 + dbt within rate-limit budget; <15min on test shop
- [ ] Test coverage stays ≥70% (new code covered)

## Risk Assessment

| Risk | Impact | Mitigation |
|------|--------|------------|
| `holidays` library schema shifts (unlikely) | Stale dim_date | Static CSV seed = no runtime dep; regen ad-hoc |
| Discounts API doesn't support `updated_at_min` filter | Full refresh every run = slow | Confirm in M1 live test; if missing, document as "full refresh only" + run weekly not daily |
| Events table grows huge (audit log) | Disk + slow pagination | Apply same `meta.run_log` archive pattern (>90d) to `raw.haravan_events` if needed; document as M7 follow-up |
| Atomic write fails on NFS / non-POSIX FS | `.env` corruption | `os.replace` is atomic on POSIX (project target Ubuntu/macOS). Document NFS as unsupported. |
| Refresh token write-back race vs cron | Lost token | `filelock` on `.env.lock`; cron lock at process level (phase-10) prevents concurrent runs anyway |

## Security Considerations

- `.env` file mode preserved at 0o600 after every write (chmod after replace)
- `filelock` lockfile in same dir as `.env`; mode 0o600
- Token values never logged (already enforced via `SecretStr` in pydantic-settings since phase-02)
- Discounts/Promotions payloads may contain PII (customer-specific codes) → same JSONB redaction policy as orders (no policy in MVP, document as future improvement)
- Events log may capture internal staff actions → restrict access to `raw.haravan_events` via Postgres role grants (note in deploy docs)

## Next Steps

After phase-11 completion → project at full M0–M7 scope. Outstanding follow-ups:
- SCD Type 2 evaluation for `dim_customers` (PRD Q1) after 1 month of operation
- Multi-shop strategy (PRD Q4) when needed
- Optional `fct_promotions_used` mart if `order.payload` exposes `discount_code` field (validate live)
- Optional auto-archive `raw.haravan_events` rows >180 days

## Unresolved Questions

1. Does `/com/discounts.json` accept `updated_at_min`? (Confirm in M1 live; affects daily vs weekly cadence)
2. Does `/com/events.json` return deleted entities? Likely yes (events log = source of truth for deletes — could enable soft-delete tracking)
3. Should we attempt `fct_promotions_used` join in this phase, or defer? Default: defer until field presence confirmed.
