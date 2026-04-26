# Code Review — phase-06 dbt Marts Core

**Scope:** 3 int/ + 6 dim + 6 fct + 2 yml. dbt build PASS=94/94. pytest 55/55 green.

---

## Pros

- Clean Kimball star: 6 dims + 4 active facts + 2 placeholders, FK relationships exhaustive in `_marts_core.yml`.
- Surrogate keys via `dbt_utils.generate_surrogate_key([...])` are pure md5 of input cols → deterministic across runs (review concern #1 holds: same `customer_id` in → same `customer_key` out, even on different machines).
- `int_orders__line_items_exploded.sql:19-25` improves on plan: adds `nullif(... , '')` guards and `coalesce`s on the multiplication so empty-string JSON values don't cast-fail or NaN-poison `line_total_vnd`.
- `dim_date.sql` 3768 rows = 2020-01-01 → ~2030 covers PRD.
- Incremental high-water-mark uses `coalesce(max(...), '1900-01-01')` → safe on first incremental run.
- Placeholder `where false` pattern lets unique tests pass vacuously and FK target tables exist before phase-09. Schema documented in column aliases.
- `.gitignore:106-111` correctly excludes `dbt/target/`, `logs/`, `dbt_packages/`, `profiles.yml`.

## Issues

### Critical
None.

### High
- **Tautological monetary test** (`_marts_core.yml:74-77`, `fct_orders.sql:24`): `expression: "= total_price_vnd - total_refunded_vnd"` where `total_refunded_vnd` is the **calc** value (`total_refunded_calc_vnd as total_refunded_vnd`) and `net_revenue_vnd = total_price_vnd - total_refunded_calc_vnd` is computed from the same source. Test always passes by construction; provides no signal on Haravan-vs-calc drift. **Fix:** expose `total_refunded_haravan_vnd` (passthrough from `stg_haravan__orders.total_refunded_vnd`) alongside `total_refunded_calc_vnd`, plus a separate (warn-level) `dbt_utils.expression_is_true` asserting the two match within tolerance. Catches partial-refund extraction bugs and Haravan reporting drift.

### Medium
- **Incremental late-arrival on lines/transactions/refunds** (`fct_order_lines.sql:25`, `fct_transactions.sql:37`, `fct_refunds.sql:21`): filter on `created_at` (immutable). If Haravan amends a line item without bumping created_at, change is missed and merge skips it. `fct_orders` uses `order_updated_at` (correct). Plan §risks didn't flag — but Haravan refunds/transactions are typically append-only so risk is **low** in practice. Document explicitly in model header comments; add to risk register for phase-10 validate.
- **`dim_payment_methods` collision risk** (`dim_payment_methods.sql:5`): if `stg.gateway` already contains literal string `'unknown'` (lowercase) AND a NULL row, both collapse to `method_name='unknown'`. `select distinct` dedupes, so safe — but if Haravan introduces e.g. `'Unknown'` and `'unknown'` mixed cases, lower() merges them, fine. Watch for `'  cod'` (whitespace) — add `trim(lower(coalesce(gateway,'unknown')))` for resilience.
- **`dim_date` timezone** (`dim_date.sql:7,12-20`): `current_date` and `extract(dow ...)` use server tz (UTC in Docker). PRD wants Asia/Ho_Chi_Minh display. Currently every date_actual past UTC midnight could land on previous VN day in dashboards. Plan §risks line 398 already flagged. **Recommend:** wrap spine with `date_day at time zone 'UTC' at time zone var('shop_timezone')` cast OR explicitly defer to phase-11 with a code comment. Currently silent.
- **`fct_inventory_*` placeholder schema fragility** (`fct_inventory_adjustments.sql:7-15`, `fct_inventory_snapshot.sql:7-13`): when phase-09 fills, types must match exactly (`null::text` → real md5 will be text ✓; `null::int` → real values must be int, not bigint). Risk: phase-09 changes a column type and `on_schema_change='append_new_columns'` doesn't recreate. **Mitigation:** these are `materialized='table'`, so dbt drops + creates each run → schema drift handled. OK as-is, but add a note in phase-09 doc to re-verify column types match.
- **`scripts/seed-raw-fixtures.sql:26,49`** product_id added to line_items: review focus item. Seed fixture is a test artifact; FK is enforced via dbt relationships test on the marts. Acceptable. Confirm seed updates are documented in phase-06 commit.

### Low
- **Schema name `staging_marts`** (`profiles.yml:11` schema=staging + `dbt_project.yml:27` +schema=marts → concat). Postgres lands tables in `staging_marts`. PRD/research mentions just "marts." Functional, but BI tool config will reference `staging_marts.fct_orders`. **Recommend:** for prod profile, set base `schema: analytics` (or null) so prod gets `marts.fct_orders` not `analytics_marts.fct_orders`. Document for phase-12 CI prod target.
- **`fct_transactions.payment_method_key` derivation** (`fct_transactions.sql:18,25`): re-derives `lower(coalesce(gateway,'unknown'))` inline — duplicates `dim_payment_methods.sql:5`. Extract to a macro `payment_method_name(gateway)` for DRY. Low priority.
- **`int_orders__customer_joined.sql`** is created but appears unused by any fact or dim in this phase. Plan §key-insights calls it "sanity FK join." If phase-07+ won't consume, it's dead code. Leave for now; remove if still unused after phase-09.
- **`net_revenue_vnd` null propagation:** if `total_price_vnd` is null, `net_revenue_vnd` is null and the expression test passes (NULL = NULL−0 → unknown, treated as no violation). Acceptable; already not_null on `order_id`/`order_key` catches structural breaks.
- **`dim_variants.product_key` FK** (`dim_variants.sql:5`): generated from `product_id`; if a variant's `product_id` doesn't exist in `dim_products` (orphan), relationships test fails. Currently passes (seed has matching product). Real-world Haravan data may have soft-deleted products → relationships test will redden. Watch in phase-10.

## Recommendations (prioritized)

1. **High (must address before phase-07):** Replace tautological `net_revenue_vnd` test with a pair of `total_refunded_*` columns and a drift-check expression.
2. **Med:** Add `at time zone` cast OR explicit code-comment deferral in `dim_date.sql` so phase-11 reviewer doesn't assume bug.
3. **Med:** Document immutability assumption in `fct_order_lines.sql` / `fct_transactions.sql` / `fct_refunds.sql` headers.
4. **Low:** Extract `payment_method_name()` macro shared by `dim_payment_methods` + `fct_transactions`.
5. **Low:** Plan a prod `analytics` schema namespace before phase-12 CI.
6. **Confirm:** delete or document `int_orders__customer_joined.sql` if no consumer materializes by phase-09.

## Verification

- 94/94 dbt build green confirmed via task description; tests cover PK unique/not_null + FK relationships + 1 expression_is_true.
- Surrogate-key determinism: holds (md5 is pure function of input).
- `dbt/target/` not committed (gitignored line 107).
- `fct_order_lines.sql:22` post-plan fix (added `created_at` to SELECT) is correct — required for incremental subquery `select max(created_at) from {{ this }}`.

## Unresolved Questions

- Q1: Is the prod schema `staging_marts` acceptable for BI consumption, or should phase-12 introduce an `analytics` profile target?
- Q2: Should refund/transaction/line-item incrementals switch to a `_loaded_at` watermark column (added in raw layer) to avoid the immutable-`created_at` blind spot, or accept append-only semantics for these entities?
- Q3: Is `int_orders__customer_joined` consumed downstream in phase-07+? If never used, remove.

---

**Status:** DONE_WITH_CONCERNS
**Summary:** Phase-06 marts ship clean and all 94 tests green; one high-priority concern: the `net_revenue_vnd` expression_is_true test is tautological (calc value compared to itself) and provides no drift signal. Several medium issues around incremental immutability assumptions and dim_date timezone are documentable rather than blocking.
**Verdict:** approve (with high-priority fix tracked for phase-07 or sooner)
**Critical issues count:** 0
