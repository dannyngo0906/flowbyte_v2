# Phase 05 — dbt Staging (M3)

## Context Links

- PRD §4.4 (FR-T1–T6), §6.3 (folder)
- Research (dbt §1 JSONB parsing, §5 array expansion, §7 tests, §8 sources, §9 structure): `plans/reports/researcher-260426-1340-dbt-postgres-patterns.md`
- Phase-04 (raw tables populated)

## Overview

- **Priority:** high
- **Status:** pending
- **Effort:** 5 days
- **Description:** Stand up dbt project; one staging view per raw table; JSONB → typed columns inline `payload->>'key'::type`; refunds & transactions explode from `orders.payload`. Schema docs + minimal tests.

## Key Insights

- **Inline `payload->>'key'::type`** outperforms `jsonb_path_query()` for shallow keys (research §1).
- **Materialization:** `staging` = `view` (cheap; raw freshness checked at source).
- **Refunds & transactions:** distinct staging files `stg_haravan__order_refunds.sql` + `stg_haravan__order_transactions.sql` use `jsonb_array_elements(payload->'refunds')` from `raw.haravan_orders`. NO separate raw table.
- **Variants** also derived from `raw.haravan_products` via array explode in this phase (`stg_haravan__variants.sql`) — keeps M3 scope coherent before marts in phase-06.
- **Source freshness:** orders 6h warn / 24h error; customers 12h/48h; products/locations 24h/72h (research §8).
- Tests at staging: PK unique+not_null; monetary cols ≥0 via `dbt_utils.expression_is_true`.

## Requirements

**Functional:**
- FR-T1: dbt-core + dbt-postgres
- FR-T2: structure `staging/haravan/`
- FR-T3: tests on PK
- FR-T4: `dbt run`, `dbt test`, `dbt build` work via direct `dbt` CLI (CLI wrapper in phase-07)
- FR-T5: staging materialized=view
- FR-T6: dbt-utils used (date_spine deferred to phase-06; surrogate keys in phase-06)

**Non-functional:**
- NFR-3: `dbt source freshness` runnable

## Architecture

```
raw.haravan_orders (JSONB)
  ├──▶ stg_haravan__orders             (1:1, typed cols, PK=order_id)
  ├──▶ stg_haravan__order_refunds      (jsonb_array_elements payload->'refunds')
  └──▶ stg_haravan__order_transactions (jsonb_array_elements payload->'transactions'
                                        AND payload->'refunds'->'transactions')

raw.haravan_customers ──▶ stg_haravan__customers
raw.haravan_products  ──▶ stg_haravan__products
                      ──▶ stg_haravan__variants (explode payload->'variants')
raw.haravan_locations ──▶ stg_haravan__locations
```

## Related Code Files

**Create:**
- `dbt/dbt_project.yml`
- `dbt/profiles.yml.example` (env-var driven)
- `dbt/packages.yml` (dbt-utils)
- `dbt/models/staging/haravan/sources.yml`
- `dbt/models/staging/haravan/_stg_haravan__models.yml` (column docs + tests)
- `dbt/models/staging/haravan/stg_haravan__orders.sql`
- `dbt/models/staging/haravan/stg_haravan__order_refunds.sql`
- `dbt/models/staging/haravan/stg_haravan__order_transactions.sql`
- `dbt/models/staging/haravan/stg_haravan__customers.sql`
- `dbt/models/staging/haravan/stg_haravan__products.sql`
- `dbt/models/staging/haravan/stg_haravan__variants.sql`
- `dbt/models/staging/haravan/stg_haravan__locations.sql`
- `tests/test_dbt_smoke.py` (CI-friendly: `dbt parse` + `dbt compile`)

**Modify:**
- `pyproject.toml` — confirm `dbt-core`, `dbt-postgres` already there; add `dbt-utils` via `dbt deps` not pip
- `Makefile` — add `dbt-deps`, `dbt-build`, `dbt-test` targets

## Implementation Steps

1. **`dbt/dbt_project.yml`:**
   ```yaml
   name: haravan_elt
   version: 0.1.0
   config-version: 2
   profile: haravan_elt
   model-paths: ["models"]
   seed-paths: ["seeds"]
   test-paths: ["tests"]
   macro-paths: ["macros"]
   target-path: "target"
   clean-targets: ["target", "dbt_packages"]

   models:
     haravan_elt:
       staging:
         +materialized: view
         +schema: staging
       intermediate:
         +materialized: ephemeral
         +schema: intermediate
       marts:
         +materialized: table
         +schema: marts
         core:
           +materialized: table
   ```

2. **`dbt/profiles.yml.example`:**
   ```yaml
   haravan_elt:
     target: dev
     outputs:
       dev:
         type: postgres
         host: "{{ env_var('PG_HOST', 'localhost') }}"
         user: "{{ env_var('PG_USER', 'elt_user') }}"
         password: "{{ env_var('PG_PASSWORD', 'elt_pass') }}"
         port: "{{ env_var('PG_PORT', 5432) | int }}"
         dbname: "{{ env_var('PG_DATABASE', 'haravan') }}"
         schema: "{{ env_var('PG_SCHEMA', 'staging') }}"
         threads: 4
   ```
   Document in README: copy to `~/.dbt/profiles.yml`.

3. **`dbt/packages.yml`:**
   ```yaml
   packages:
     - package: dbt-labs/dbt_utils
       version: [">=1.1.0", "<2.0.0"]
   ```
   Run: `cd dbt && dbt deps`.

4. **`sources.yml`** (research §8):
   ```yaml
   version: 2
   sources:
     - name: raw_haravan
       database: haravan
       schema: raw
       loaded_at_field: ingested_at
       freshness:
         warn_after: { count: 24, period: hour }
         error_after: { count: 48, period: hour }
       tables:
         - name: orders
           identifier: haravan_orders
           freshness: { warn_after: { count: 6, period: hour }, error_after: { count: 24, period: hour } }
         - name: customers
           identifier: haravan_customers
           freshness: { warn_after: { count: 12, period: hour }, error_after: { count: 48, period: hour } }
         - name: products
           identifier: haravan_products
         - name: locations
           identifier: haravan_locations
   ```

5. **`stg_haravan__orders.sql`** (key columns; expand list as discovered):
   ```sql
   {{ config(materialized='view') }}
   select
     (payload->>'id')::bigint                                  as order_id,
     payload->>'name'                                          as order_name,
     payload->>'order_number'                                  as order_number,
     (payload->>'customer_id')::bigint                         as customer_id,
     (payload->>'location_id')::bigint                         as location_id,
     payload->>'currency'                                      as currency,
     payload->>'financial_status'                              as financial_status,
     payload->>'fulfillment_status'                            as fulfillment_status,
     (payload->>'subtotal_price')::numeric(18,2)               as subtotal_vnd,
     (payload->>'total_discounts')::numeric(18,2)              as total_discount_vnd,
     (payload->>'total_tax')::numeric(18,2)                    as total_tax_vnd,
     (payload->>'total_shipping')::numeric(18,2)               as total_shipping_vnd,
     (payload->>'total_price')::numeric(18,2)                  as total_price_vnd,
     (payload->>'total_refunded')::numeric(18,2)               as total_refunded_vnd,
     (payload->>'created_at')::timestamptz                     as created_at,
     (payload->>'updated_at')::timestamptz                     as order_updated_at,
     (payload->>'closed_at')::timestamptz                      as closed_at,
     (payload->>'cancelled_at')::timestamptz                   as cancelled_at,
     payload->'line_items'                                     as line_items_json,
     payload->'refunds'                                        as refunds_json,
     payload->'transactions'                                   as transactions_json,
     payload->'shipping_address'                               as shipping_address_json,
     payload->'billing_address'                                as billing_address_json,
     payload                                                   as payload_raw,
     ingested_at,
     source_run_id
   from {{ source('raw_haravan', 'orders') }}
   ```

6. **`stg_haravan__order_refunds.sql`:**
   ```sql
   {{ config(materialized='view') }}
   with exploded as (
     select
       (payload->>'id')::bigint as order_id,
       jsonb_array_elements(coalesce(payload->'refunds', '[]'::jsonb)) as refund_json,
       ingested_at, source_run_id
     from {{ source('raw_haravan', 'orders') }}
   )
   select
     (refund_json->>'id')::bigint                  as refund_id,
     order_id,
     (refund_json->>'created_at')::timestamptz     as created_at,
     refund_json->>'note'                          as note,
     refund_json->>'reason'                        as reason,
     (refund_json->>'amount')::numeric(18,2)       as refund_amount_vnd,
     (refund_json->>'restock')::boolean            as restock,
     refund_json->'refund_line_items'              as refund_line_items_json,
     refund_json->'transactions'                   as refund_transactions_json,
     ingested_at, source_run_id
   from exploded
   where refund_json->>'id' is not null
   ```

7. **`stg_haravan__order_transactions.sql`** — UNION transactions from order top-level + nested in refunds:
   ```sql
   {{ config(materialized='view') }}
   with order_tx as (
     select
       (payload->>'id')::bigint as order_id,
       null::bigint as refund_id,
       jsonb_array_elements(coalesce(payload->'transactions', '[]'::jsonb)) as tx_json,
       ingested_at, source_run_id
     from {{ source('raw_haravan', 'orders') }}
   ),
   refund_tx as (
     select
       (payload->>'id')::bigint as order_id,
       (refund_json->>'id')::bigint as refund_id,
       jsonb_array_elements(coalesce(refund_json->'transactions', '[]'::jsonb)) as tx_json,
       ingested_at, source_run_id
     from (
       select payload, jsonb_array_elements(coalesce(payload->'refunds', '[]'::jsonb)) as refund_json,
              ingested_at, source_run_id
       from {{ source('raw_haravan', 'orders') }}
     ) r
   ),
   unioned as (
     select * from order_tx
     union all select * from refund_tx
   )
   select
     (tx_json->>'id')::bigint                      as transaction_id,
     order_id,
     refund_id,
     tx_json->>'kind'                              as kind,         -- sale|refund|authorization
     tx_json->>'status'                            as status,
     tx_json->>'gateway'                           as gateway,
     (tx_json->>'amount')::numeric(18,2)           as amount_vnd,
     (tx_json->>'created_at')::timestamptz         as created_at,
     ingested_at, source_run_id
   from unioned
   where tx_json->>'id' is not null
   ```

8. **`stg_haravan__customers.sql`** — typed cols from `payload->>...` (id, email, phone, accepts_marketing, created_at, updated_at, total_spent, orders_count, addresses_json, etc.).

9. **`stg_haravan__products.sql`** — id, title, vendor, product_type, status, tags, created_at, updated_at, variants_json (preserved for explode).

10. **`stg_haravan__variants.sql`** — explode `payload->'variants'`:
    ```sql
    {{ config(materialized='view') }}
    with exploded as (
      select
        (payload->>'id')::bigint as product_id,
        jsonb_array_elements(coalesce(payload->'variants', '[]'::jsonb)) as v,
        ingested_at, source_run_id
      from {{ source('raw_haravan', 'products') }}
    )
    select
      (v->>'id')::bigint                            as variant_id,
      product_id,
      v->>'title'                                   as title,
      v->>'sku'                                     as sku,
      v->>'barcode'                                 as barcode,
      (v->>'price')::numeric(18,2)                  as price_vnd,
      (v->>'compare_at_price')::numeric(18,2)       as compare_at_price_vnd,
      (v->>'inventory_quantity')::int               as inventory_quantity,
      v->>'option1' as option1, v->>'option2' as option2, v->>'option3' as option3,
      (v->>'created_at')::timestamptz               as created_at,
      (v->>'updated_at')::timestamptz               as variant_updated_at,
      ingested_at, source_run_id
    from exploded
    where v->>'id' is not null
    ```

11. **`stg_haravan__locations.sql`** — id, name, address1, city, country, phone, active, created_at, updated_at.

12. **`_stg_haravan__models.yml`** (tests + docs):
    ```yaml
    version: 2
    models:
      - name: stg_haravan__orders
        columns:
          - name: order_id
            tests: [unique, not_null]
          - name: total_price_vnd
            tests:
              - not_null
              - dbt_utils.expression_is_true:
                  expression: ">= 0"
      - name: stg_haravan__order_refunds
        columns:
          - name: refund_id
            tests: [unique, not_null]
          - name: order_id
            tests: [not_null]
      - name: stg_haravan__order_transactions
        columns:
          - name: transaction_id
            tests: [unique, not_null]
      - name: stg_haravan__customers
        columns:
          - name: customer_id
            tests: [unique, not_null]
      - name: stg_haravan__products
        columns:
          - name: product_id
            tests: [unique, not_null]
      - name: stg_haravan__variants
        columns:
          - name: variant_id
            tests: [unique, not_null]
          - name: product_id
            tests: [not_null]
      - name: stg_haravan__locations
        columns:
          - name: location_id
            tests: [unique, not_null]
    ```

13. **`Makefile` targets:**
    ```make
    dbt-deps:
    	cd dbt && dbt deps

    dbt-build:
    	cd dbt && dbt build

    dbt-test:
    	cd dbt && dbt test

    dbt-freshness:
    	cd dbt && dbt source freshness
    ```

14. **Smoke test** (`tests/test_dbt_smoke.py`):
    ```python
    import subprocess
    def test_dbt_parse():
        result = subprocess.run(["dbt", "parse"], cwd="dbt", capture_output=True, text=True)
        assert result.returncode == 0, result.stderr
    ```

15. **Verify with seeded raw data** (optional fixture script `scripts/seed-raw-fixtures.sql` for CI without API).

## Todo List

- [ ] Init `dbt/` project (`dbt_project.yml`, `profiles.yml.example`, `packages.yml`)
- [ ] Run `dbt deps` to install dbt-utils
- [ ] Write `sources.yml` (4 source tables + freshness)
- [ ] Write 7 staging models: orders, order_refunds, order_transactions, customers, products, variants, locations
- [ ] Write `_stg_haravan__models.yml` (PK tests + monetary expression_is_true)
- [ ] Update `Makefile` with `dbt-deps/build/test/freshness`
- [ ] Write `tests/test_dbt_smoke.py` (parse + compile)
- [ ] Local verify: `make dbt-deps && make dbt-build` → all 7 models created, all PK tests pass
- [ ] Verify refunds + transactions explode produces expected rows from sample order JSON
- [ ] Document profile setup in README skeleton

## Success Criteria

- `cd dbt && dbt build --select staging` → all 7 staging views built, 100% schema tests pass
- `psql ... -c "SELECT count(*) FROM staging.stg_haravan__order_refunds"` returns rows when sample order has refunds
- `dbt source freshness` reports no errors when raw recently loaded
- `dbt parse` exit 0 in CI (test_dbt_smoke green)
- Test coverage of staging schema docs ≥80% of cols documented

## Risk Assessment

| Risk | Likelihood | Impact | Mitigation |
|------|-----------|--------|-----------|
| `payload->>'key'` returns NULL for missing field → cast fails | Medium | Med | Cast accepts NULL; verify with sample. Add `nullif()` if needed |
| Refunds JSON shape differs from assumption (no `id` field) | Medium | High | `where refund_json->>'id' is not null` filter; fix once schema confirmed |
| dbt-utils version conflict with dbt-core | Low | Low | Pin range `>=1.1, <2.0` |
| Postgres GIN/BTree absent on `payload->>'updated_at'` → slow staging queries | Med | Low | Phase-10 hardening adds expression indexes if profiling shows bottleneck |

## Security Considerations

- dbt connects via env vars (no hardcoded creds)
- `profiles.yml` template uses `env_var()` — actual file in `~/.dbt/` chmod 600
- Staging views expose customer email/phone — flag in README; restrict role access in prod (post-MVP)

## Next Steps

Unblocks **phase-06** (marts read from staging models).

## Unresolved Questions

- Q1: Exact JSON keys in Haravan order payload (e.g., `total_shipping` vs `total_shipping_price`) — discovered during live test; update SQL accordingly.
- Q2: Are `total_*` fields strings or numbers in JSON? Casting `::numeric` works for both; safe.
- Q3: Variant grain — `(product_id, variant_id)` or just `variant_id` unique? Assume `variant_id` globally unique per Haravan (test will catch).
