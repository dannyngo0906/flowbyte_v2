# Phase-05 Code Review — dbt Staging Layer

**Scope:** dbt project init + 7 staging views + sources/tests + seed fixtures + smoke test.
**Verification observed:** `dbt parse` 0, `dbt compile` 0, `dbt build --select staging` PASS=38, ruff/mypy/pytest 55/55.

---

## Pros

- Clean separation: orders (1:1) vs refunds/transactions/variants (array explode) — matches research §5.
- Defensive `coalesce(payload->'arr', '[]'::jsonb)` on every explode (refunds, variants, both transaction CTEs) — null-safe.
- `where ...->>'id' is not null` filter on every exploded model — guards against malformed array entries.
- `nullif(col, '')::type` pattern correctly localized to optional/timestamp/numeric fields; required PK casts (`(payload->>'id')::bigint`) intentionally NOT wrapped — fail-fast on data quality.
- Transactions UNION uses `refund_envelopes` CTE to flatten refunds before second `jsonb_array_elements` — avoids set-returning-function nesting (Postgres rejects nested SRFs in same SELECT).
- `total_price_vnd ≥ 0` test on order grand total (matches PRD acceptance); `expression_is_true` properly applied to monetary cols.
- Source freshness tiered per domain (orders 6h/24h tightest; locations disabled because full-refresh).
- `accepted_values` on `kind` set to `severity: warn` — non-blocking on unexpected gateway-specific values.
- `profiles.yml` gitignored, `.example` committed; matches dbt convention.
- `dbt_project.yml` `quoting: identifier=false` — avoids case-folding surprises when joining to raw tables.
- Smoke test gracefully skips when `dbt` CLI absent (`pytest.mark.skipif`) — CI-friendly.
- `dbt-deps/build/test/freshness/seed` Makefile targets all use `../$(VENV)/bin/dbt` — works without dbt on global PATH.

---

## Issues

### Critical
None.

### High
None.

### Medium
1. **Missing `intermediate` + `marts` schema bootstrap.** `meta/schema.sql:4-7` creates `raw`, `staging`, `marts`, `meta` — but NOT `intermediate`. dbt-postgres adapter auto-creates target schemas at run time IF the connection role has `CREATE ON DATABASE` (which `elt_user` likely does, since it created its own schemas). Still, deterministic infra > implicit. Add `CREATE SCHEMA IF NOT EXISTS intermediate;` to `schema.sql` before phase-06. (Note: `+materialized: ephemeral` for intermediate means no DB objects — schema may never actually be needed unless you flip to view/table later. Low practical impact; flag for awareness.)

2. **`stg_haravan__customers.sql:10-11` — required casts on `accepts_marketing` + `orders_count` not `nullif`-guarded.** Empty-string from JSON serializer would fail (`(payload->>'accepts_marketing')::boolean` on `''` raises `invalid input syntax for type boolean`). For Haravan customers these fields are typically always present, but if a partial payload lands these will explode. Consider `nullif(...,'')` wrap or accept fail-fast contract — same call you made for monetary; just be intentional. Same applies to `(payload->>'created_at')::timestamptz` / `updated_at` (lines 14-15) and the equivalent on orders/products/locations top-level `created_at/updated_at`.

3. **`stg_haravan__orders.sql:18-19` — `created_at` + `order_updated_at` not `nullif`-guarded** while `closed_at`/`cancelled_at` are. Inconsistent: if you trust the contract on required `created_at`, document it; otherwise apply `nullif` for symmetry. Same pattern in customers/products/locations.

### Low
4. **`stg_haravan__order_transactions.sql:42` comment lists `sale | refund | authorization | void`** but `_stg_haravan__models.yml:39` accepts 6 values (adds `capture`, `change`). Sync comment with yaml or drop comment to avoid drift.

5. **`stg_haravan__variants.sql` no test on `(product_id, variant_id)` composite uniqueness.** Plan unresolved Q3 noted variant_id assumed globally unique — current `unique` test on `variant_id` validates that assumption. Acceptable; just note that if assumption fails in prod data, the unique test fires loud — that's the intended catch.

6. **`scripts/seed-raw-fixtures.sql` covers only happy-path order (0 refunds).** Transaction UNION refund-side branch (`refund_envelopes`/`refund_tx`) is exercised structurally by `dbt compile` but never row-tested. Consider adding a 2nd order row with 1 refund + 1 refund-transaction before phase-06 (marts) — phase-06 fact_payments will read this and silent zero-row bugs are easy to miss. Note as follow-up; not blocking.

7. **`Makefile:56 dbt-seed` uses `$$DATABASE_URL`** — not defined elsewhere in Makefile or `.env.example` (per scope). User must set externally. Minor DX gap; document in phase-10 README or hardcode `psql -h localhost -p 5434 -U elt_user -d haravan -f ...` for parity with `db-up` defaults.

8. **`sources.yml` source `freshness: null` on locations** — valid YAML for "skip" per dbt docs; verified by `dbt source freshness` not erroring. OK.

9. **`stg_haravan__orders.sql:27 `payload as payload_raw`** — duplicates entire JSON in every staging view query result. Views are lazy so no storage cost, but downstream marts referencing `payload_raw` defeat the staging typing layer. Consider whether to expose at all or drop. YAGNI: leave for now if marts don't read it.

10. **No `not_null` test on `order_transactions.amount_vnd`** — refund kind transactions can have negative-zero or null amounts in some gateways. Not a blocker; marts can validate.

---

## Recommendations

1. **Before phase-06:** add `CREATE SCHEMA IF NOT EXISTS intermediate;` to `meta/schema.sql` (1-line change).
2. **Add 2nd seed row with refunds** to `scripts/seed-raw-fixtures.sql` so `stg_haravan__order_refunds` + the refund-side of `stg_haravan__order_transactions` get row-level coverage. Use existing `seed_run` UUID and `DELETE WHERE source_run_id = seed_run` is already idempotent.
3. **Decide `nullif` policy** for top-level required timestamps (`created_at`/`updated_at`) and customer flags. Either (a) wrap all to be defensive, or (b) document the contract "Haravan API guarantees these on every record; cast failure = upstream contract break, fail loud". Pick one; current state is mid.
4. **Sync transaction kind comment** in `stg_haravan__order_transactions.sql:42` with the 6-value `accepted_values` list.
5. **Phase-10 hardening:** add expression index on `raw.haravan_orders((payload->>'updated_at'))` if `dbt source freshness` query plans show seq-scan on million-row tables. Already noted in plan §risk-assessment.

---

## Verdict

**approve** — staging layer is production-grade. JSONB pattern is correct and consistent. Tests cover PKs + monetary invariants. Defensive coalesce/null-filter on every explode. Issues found are stylistic consistency (nullif policy) + one trivial schema-bootstrap follow-up before phase-06; none block phase-06 start.

---

## Unresolved Questions

- Q1: nullif policy for required top-level fields — pick (a) defensive vs (b) fail-loud contract; document in code-standards.md.
- Q2: keep `payload_raw` passthrough in `stg_haravan__orders` or drop? Marts in phase-06 will reveal if anyone reads it.
- Q3: should `intermediate` schema be created in `schema.sql` even though current materialization is ephemeral? Defer until phase-06 picks materialization strategy.

**Status:** DONE
**Summary:** Phase-05 dbt staging layer is well-structured and production-ready. JSONB patterns correct, defensive coalesce on all explodes, tests cover PKs + monetary invariants. 0 critical, 0 high, 3 medium (schema bootstrap, nullif consistency on required cols), 7 low (cosmetic/follow-ups).
**Verdict:** approve
**Critical issues count:** 0
