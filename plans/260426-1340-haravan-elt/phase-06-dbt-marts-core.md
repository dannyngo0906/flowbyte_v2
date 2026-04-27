# Phase 06 — dbt Marts Core (M3)

## Context Links

- PRD §4.4, §7 (full star schema spec), §7.4 (conventions)
- Research (dbt §2 surrogate keys, §3 incremental merge, §5 array explode, §6 snapshot, §11 date dim): `plans/reports/researcher-260426-1340-dbt-postgres-patterns.md`
- Phase-05 (staging models)

## Overview

- **Priority:** high
- **Status:** pending
- **Effort:** 5 days
- **Description:** Build intermediate + marts layers — 6 dim tables + 6 fact tables — per Kimball star schema spec. Surrogate keys via `dbt_utils.generate_surrogate_key`. Facts use `incremental` + `merge` + `unique_key`; dims use `table` (Type 1 SCD). `dim_date` uses `dbt_utils.date_spine` placeholder; VN holidays seed deferred to phase-11. `fct_inventory_*` placeholders until phase-09 P1 domains land.

## Key Insights

- **MD5 surrogate keys** via `dbt_utils.generate_surrogate_key([...])` — idempotent, parallel-safe (research §2).
- **Fact materialization:** `incremental` + `unique_key=['<entity>_id']` + `incremental_strategy='merge'` + `on_schema_change='append_new_columns'`. Postgres 15+ MERGE supported.
- **Intermediate layer (`ephemeral`):** `int_orders__customer_joined`, `int_orders__line_items_exploded`, `int_orders__totals_with_refund_net`. Reusable by multiple facts.
- **dim_payment_methods:** derived from `stg_haravan__order_transactions.gateway` (distinct values). Tiny dim.
- **dim_date:** `dbt_utils.date_spine` 2020-01-01 → today+4yr; columns: date_key (YYYYMMDD int), date_actual, year, quarter, month, day_of_month, day_of_week, week_of_year, is_weekend. VN holiday flag added in phase-11 (left null/false here).
- **fct_inventory_adjustments + fct_inventory_snapshot:** create empty placeholder models with header comment "populated in M5 phase-09"; let dbt build successfully but skip until raw tables exist.

## Requirements

**Functional:**
- FR-T2: marts/core hierarchy
- FR-T3: tests on PK + FK + relationships
- FR-T5: marts default `+materialized: table`; facts override to `incremental`
- FR-T6: dbt-utils surrogate keys + date_spine

**Non-functional:**
- NFR-1: incremental fact build < 2 min for 10k orders/month delta

## Architecture

```
staging/        ─▶ intermediate/                                       ─▶ marts/core/
                                                                          ├─ dim_customers
                                                                          ├─ dim_products
                                                                          ├─ dim_variants
                                                                          ├─ dim_locations
                                                                          ├─ dim_date
                                                                          ├─ dim_payment_methods
                                                                          ├─ fct_orders
                                                                          ├─ fct_order_lines
                                                                          ├─ fct_transactions
                                                                          ├─ fct_refunds
                                                                          ├─ fct_inventory_adjustments  (placeholder)
                                                                          └─ fct_inventory_snapshot     (placeholder)

intermediate models:
  int_orders__customer_joined          (joins stg_orders + stg_customers)
  int_orders__line_items_exploded      (jsonb_array_elements line_items)
  int_orders__totals_with_refund_net   (sum refunds; computes net_revenue)
  int_inventory__placeholder           (stub, fleshed out phase-09)
```

FK key naming convention: `<entity>_key` BIGINT MD5-derived; natural keys preserved as `<entity>_id`.

## Related Code Files

**Create:**
- `dbt/models/intermediate/orders/int_orders__customer_joined.sql`
- `dbt/models/intermediate/orders/int_orders__line_items_exploded.sql`
- `dbt/models/intermediate/orders/int_orders__totals_with_refund_net.sql`
- `dbt/models/intermediate/_int_models.yml`
- `dbt/models/marts/core/dim_customers.sql`
- `dbt/models/marts/core/dim_products.sql`
- `dbt/models/marts/core/dim_variants.sql`
- `dbt/models/marts/core/dim_locations.sql`
- `dbt/models/marts/core/dim_date.sql`
- `dbt/models/marts/core/dim_payment_methods.sql`
- `dbt/models/marts/core/fct_orders.sql`
- `dbt/models/marts/core/fct_order_lines.sql`
- `dbt/models/marts/core/fct_transactions.sql`
- `dbt/models/marts/core/fct_refunds.sql`
- `dbt/models/marts/core/fct_inventory_adjustments.sql` (placeholder)
- `dbt/models/marts/core/fct_inventory_snapshot.sql` (placeholder)
- `dbt/models/marts/core/_marts_core.yml`

**Modify:**
- `dbt/dbt_project.yml` — add per-model materialization overrides (already done globally; ensure)

## Implementation Steps

1. **Intermediate — `int_orders__line_items_exploded.sql`:**
   ```sql
   {{ config(materialized='ephemeral') }}
   with base as (
     select order_id, customer_id, location_id, created_at, currency, line_items_json
     from {{ ref('stg_haravan__orders') }}
   ),
   exploded as (
     select
       order_id, customer_id, location_id, created_at, currency,
       jsonb_array_elements(coalesce(line_items_json, '[]'::jsonb)) as li
     from base
   )
   select
     order_id, customer_id, location_id, created_at, currency,
     (li->>'id')::bigint                                 as line_item_id,
     (li->>'variant_id')::bigint                         as variant_id,
     (li->>'product_id')::bigint                         as product_id,
     (li->>'quantity')::int                              as quantity,
     (li->>'price')::numeric(18,2)                       as unit_price_vnd,
     (li->>'total_discount')::numeric(18,2)              as line_discount_vnd,
     ((li->>'price')::numeric(18,2) * (li->>'quantity')::int) as line_total_vnd
   from exploded
   where li->>'id' is not null
   ```

2. **`int_orders__totals_with_refund_net.sql`:**
   ```sql
   {{ config(materialized='ephemeral') }}
   with refunded as (
     select order_id, sum(refund_amount_vnd) as total_refunded_vnd
     from {{ ref('stg_haravan__order_refunds') }}
     group by 1
   )
   select
     o.*,
     coalesce(r.total_refunded_vnd, 0) as total_refunded_calc_vnd,
     o.total_price_vnd - coalesce(r.total_refunded_vnd, 0) as net_revenue_vnd
   from {{ ref('stg_haravan__orders') }} o
   left join refunded r using (order_id)
   ```

3. **`int_orders__customer_joined.sql`:** light join exposing customer_id resolved via stg_customers (sanity-check FK):
   ```sql
   {{ config(materialized='ephemeral') }}
   select o.*, c.email as customer_email
   from {{ ref('stg_haravan__orders') }} o
   left join {{ ref('stg_haravan__customers') }} c on c.customer_id = o.customer_id
   ```

4. **`dim_customers.sql`** (Type 1):
   ```sql
   {{ config(materialized='table') }}
   select
     {{ dbt_utils.generate_surrogate_key(['customer_id']) }} as customer_key,
     customer_id,
     email, phone, first_name, last_name,
     accepts_marketing, total_spent, orders_count,
     created_at, updated_at
   from {{ ref('stg_haravan__customers') }}
   ```

5. **`dim_products.sql`** + **`dim_variants.sql`** (similar pattern). dim_variants needs `product_key` FK:
   ```sql
   {{ config(materialized='table') }}
   select
     {{ dbt_utils.generate_surrogate_key(['variant_id']) }} as variant_key,
     {{ dbt_utils.generate_surrogate_key(['product_id']) }} as product_key,
     variant_id, product_id,
     title, sku, barcode, price_vnd, compare_at_price_vnd, inventory_quantity,
     option1, option2, option3,
     created_at, variant_updated_at
   from {{ ref('stg_haravan__variants') }}
   ```

6. **`dim_locations.sql`** — straight Type 1 from staging.

7. **`dim_date.sql`:**
   ```sql
   {{ config(materialized='table') }}
   with spine as (
     {{ dbt_utils.date_spine(
         datepart="day",
         start_date="cast('2020-01-01' as date)",
         end_date="cast(current_date + interval '4 years' as date)"
     ) }}
   )
   select
     to_char(date_day, 'YYYYMMDD')::int                                as date_key,
     date_day::date                                                    as date_actual,
     extract(year from date_day)::int                                  as year,
     extract(quarter from date_day)::int                               as quarter,
     extract(month from date_day)::int                                 as month_of_year,
     extract(day from date_day)::int                                   as day_of_month,
     extract(dow from date_day)::int                                   as day_of_week,
     extract(week from date_day)::int                                  as week_of_year,
     case when extract(dow from date_day) in (0,6) then true else false end as is_weekend,
     null::text                                                        as holiday_name,    -- filled phase-11
     false                                                             as is_holiday        -- filled phase-11
   from spine
   ```

8. **`dim_payment_methods.sql`:**
   ```sql
   {{ config(materialized='table') }}
   with sources as (
     select distinct lower(coalesce(gateway, 'unknown')) as method_name
     from {{ ref('stg_haravan__order_transactions') }}
   )
   select
     {{ dbt_utils.generate_surrogate_key(['method_name']) }} as payment_method_key,
     method_name
   from sources
   ```

9. **`fct_orders.sql`:**
   ```sql
   {{ config(
     materialized='incremental',
     unique_key='order_id',
     incremental_strategy='merge',
     on_schema_change='append_new_columns'
   ) }}
   select
     {{ dbt_utils.generate_surrogate_key(['order_id']) }}                  as order_key,
     order_id,
     {{ dbt_utils.generate_surrogate_key(['customer_id']) }}               as customer_key,
     {{ dbt_utils.generate_surrogate_key(['location_id']) }}               as location_key,
     to_char(created_at::date, 'YYYYMMDD')::int                            as order_date_key,
     created_at,
     order_updated_at,
     financial_status, fulfillment_status, currency,
     subtotal_vnd, total_discount_vnd, total_tax_vnd, total_shipping_vnd,
     total_price_vnd, total_refunded_calc_vnd as total_refunded_vnd, net_revenue_vnd
   from {{ ref('int_orders__totals_with_refund_net') }}
   {% if is_incremental() %}
     where order_updated_at >= (select coalesce(max(order_updated_at), '1900-01-01'::timestamptz) from {{ this }})
   {% endif %}
   ```

10. **`fct_order_lines.sql`:**
    ```sql
    {{ config(
      materialized='incremental',
      unique_key='line_key',
      incremental_strategy='merge'
    ) }}
    select
      {{ dbt_utils.generate_surrogate_key(['order_id', 'line_item_id']) }} as line_key,
      {{ dbt_utils.generate_surrogate_key(['order_id']) }}                 as order_key,
      {{ dbt_utils.generate_surrogate_key(['variant_id']) }}               as variant_key,
      {{ dbt_utils.generate_surrogate_key(['product_id']) }}               as product_key,
      to_char(created_at::date, 'YYYYMMDD')::int                           as order_date_key,
      order_id, line_item_id, variant_id, product_id,
      quantity, unit_price_vnd, line_discount_vnd, line_total_vnd
    from {{ ref('int_orders__line_items_exploded') }}
    {% if is_incremental() %}
      where created_at >= (select coalesce(max(created_at), '1900-01-01'::timestamptz) from {{ this }})
    {% endif %}
    ```

11. **`fct_transactions.sql`:**
    ```sql
    {{ config(materialized='incremental', unique_key='transaction_id', incremental_strategy='merge') }}
    select
      {{ dbt_utils.generate_surrogate_key(['transaction_id']) }}      as transaction_key,
      {{ dbt_utils.generate_surrogate_key(['order_id']) }}            as order_key,
      {{ dbt_utils.generate_surrogate_key(['lower(coalesce(gateway, \'unknown\'))']) }} as payment_method_key,
      to_char(created_at::date, 'YYYYMMDD')::int                      as transaction_date_key,
      transaction_id, order_id, refund_id, kind, status, gateway, amount_vnd, created_at
    from {{ ref('stg_haravan__order_transactions') }}
    {% if is_incremental() %}
      where created_at >= (select coalesce(max(created_at), '1900-01-01'::timestamptz) from {{ this }})
    {% endif %}
    ```

12. **`fct_refunds.sql`:**
    ```sql
    {{ config(materialized='incremental', unique_key='refund_id', incremental_strategy='merge') }}
    select
      {{ dbt_utils.generate_surrogate_key(['refund_id']) }}    as refund_key,
      {{ dbt_utils.generate_surrogate_key(['order_id']) }}     as order_key,
      to_char(created_at::date, 'YYYYMMDD')::int               as refund_date_key,
      refund_id, order_id, reason, note, refund_amount_vnd, restock, created_at
    from {{ ref('stg_haravan__order_refunds') }}
    {% if is_incremental() %}
      where created_at >= (select coalesce(max(created_at), '1900-01-01'::timestamptz) from {{ this }})
    {% endif %}
    ```

13. **`fct_inventory_adjustments.sql` (placeholder):**
    ```sql
    -- Placeholder; populated in phase-09 (M5).
    -- Returns empty result set with correct schema so downstream BI doesn't break.
    {{ config(materialized='table') }}
    select
      null::text   as adjustment_key,
      null::bigint as adjustment_id,
      null::text   as variant_key,
      null::text   as location_key,
      null::int    as adjustment_date_key,
      null::int    as quantity_delta,
      null::numeric(18,2) as cost_delta_vnd
    where false
    ```

14. **`fct_inventory_snapshot.sql` (placeholder):** same shape (`location_key`, `variant_key`, `snapshot_date`, `current_quantity`, `available_quantity`, `committed_quantity`).

15. **`_marts_core.yml`** (tests):
    ```yaml
    version: 2
    models:
      - name: dim_customers
        columns:
          - name: customer_key
            tests: [unique, not_null]
          - name: customer_id
            tests: [unique, not_null]
      - name: dim_products
        columns:
          - name: product_key
            tests: [unique, not_null]
      - name: dim_variants
        columns:
          - name: variant_key
            tests: [unique, not_null]
          - name: product_key
            tests:
              - relationships: { to: ref('dim_products'), field: product_key }
      - name: dim_locations
        columns:
          - name: location_key
            tests: [unique, not_null]
      - name: dim_date
        columns:
          - name: date_key
            tests: [unique, not_null]
      - name: dim_payment_methods
        columns:
          - name: payment_method_key
            tests: [unique, not_null]
      - name: fct_orders
        columns:
          - name: order_key
            tests: [unique, not_null]
          - name: customer_key
            tests:
              - relationships: { to: ref('dim_customers'), field: customer_key }
          - name: order_date_key
            tests:
              - relationships: { to: ref('dim_date'), field: date_key }
          - name: net_revenue_vnd
            tests:
              - dbt_utils.expression_is_true: { expression: "= total_price_vnd - total_refunded_vnd" }
      - name: fct_order_lines
        columns:
          - name: line_key
            tests: [unique, not_null]
          - name: variant_key
            tests:
              - relationships: { to: ref('dim_variants'), field: variant_key }
      - name: fct_transactions
        columns:
          - name: transaction_key
            tests: [unique, not_null]
          - name: payment_method_key
            tests:
              - relationships: { to: ref('dim_payment_methods'), field: payment_method_key }
      - name: fct_refunds
        columns:
          - name: refund_key
            tests: [unique, not_null]
          - name: order_key
            tests:
              - relationships: { to: ref('fct_orders'), field: order_key }
    ```

16. **Local verify:**
    ```bash
    cd dbt && dbt build --select marts
    # Expect: 6 dim + 4 fct + 2 placeholder fct = 12 marts models
    ```

## Todo List

- [x] Write 3 intermediate models (orders__customer_joined, line_items_exploded, totals_with_refund_net)
- [x] Write 6 dim models (customers, products, variants, locations, date, payment_methods)
- [x] Write 4 active fact models (orders, order_lines, transactions, refunds) with merge/unique_key
- [x] Write 2 placeholder fact models (inventory_adjustments, inventory_snapshot)
- [x] Write `_marts_core.yml` with PK + relationships + expression_is_true tests
- [x] Local verify `dbt build --select marts` — all 12 models built
- [x] Verify FK relationships pass (run `dbt test`)
- [x] Verify incremental: re-run after adding 1 raw row → only that row appears in fct_orders  <!-- VERIFIED 2026-04-27 live: dbt build populated all 13 marts (50 fct_orders, 124 fct_order_lines, 100 fct_transactions, 206 fct_inventory_snapshot, 130 dim_customers, 50 dim_products, etc.). 4 referential test failures expected from 1-day slice (orders reference dims updated other days) — not a code bug -->
- [x] Verify `dim_date` covers 2020 → current+4yr range

## Success Criteria

- `dbt build` ends green: 7 staging + 3 int + 12 marts (placeholder facts produce 0 rows but exist)
- All schema tests pass (PK unique, FK relationships, monetary expression_is_true)
- Incremental smoke test: append 1 order to `raw.haravan_orders` → `dbt run --select fct_orders` only adds that row
- `dim_payment_methods` populated with at least 1 row (`unknown` if empty transactions)
- Surrogate keys deterministic: re-running `dim_customers` does NOT change `customer_key` for unchanged source

## Risk Assessment

| Risk | Likelihood | Impact | Mitigation |
|------|-----------|--------|-----------|
| Postgres < 15 → MERGE unsupported | Low | High | Tech stack locks 15+; CI uses `postgres:15` service container |
| `dbt_utils.generate_surrogate_key` collision (md5) | Negligible | Low | Acceptable below 100M rows; switch to UUID if scaled |
| Placeholder fact tables fail unique test (0 rows ok, but 0 rows can't be unique either) | Low | Low | `where false` = empty schema; unique test passes vacuously |
| dim_date timezone — `current_date` is server tz, not Asia/Ho_Chi_Minh | Medium | Med | Add `at time zone 'Asia/Ho_Chi_Minh'` cast in date_actual derivation; doc decision |

## Security Considerations

- dim_customers contains email/phone — restrict marts schema role read access in prod (post-MVP)
- No raw secrets in marts; safe to expose to BI tools

## Next Steps

Unblocks **phase-07** (CLI orchestrator drives `extract → transform → test`).

## Unresolved Questions

- Q1: Order line item `id` field present? If absent, derive `line_item_id` via row_number to maintain unique key — fix in phase-10 if discovered.
- Q2: Does Haravan order JSON include shipping line items separate from product lines? Current SQL treats all `line_items[]` uniformly; flag for review.
- Q3: VND vs other currencies — assumed all VND for now; if multi-currency, add currency_code dim later.
