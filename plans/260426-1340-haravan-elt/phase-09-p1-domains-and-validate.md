# Phase 09 — P1 Domains + Validate Command (M5)

## Context Links

- PRD §4.1 (P1 domains: Inventory Adjustments, Inventory Location Balance, Custom/Smart Collections), §13 Q5 (validate)
- API endpoints: `/com/inventories/adjustments.json`, `/com/inventory_locations.json`, `/com/custom_collections.json`, `/com/smart_collections.json`, `<entity>/count.json`
- Phase-04 (extractor pattern), phase-06 (placeholder fct_inventory_*)

## Overview

- **Priority:** medium
- **Status:** pending
- **Effort:** 5 days
- **Description:** Add P1 extractors (inventory adjustments + location balance + collections); flesh out `fct_inventory_adjustments` mart; add `fct_inventory_snapshot` daily snapshot fact; implement `haravan-elt validate <domain>` command (raw row count vs Haravan `/count.json`). User-locked extras IN scope.

## Key Insights

- **Inventory Adjustments:** standard list+pagination; supports `updated_at_min`. Same pattern as orders/customers.
- **Inventory Location Balance:** requires `location_ids` and `variant_ids` query params — cartesian. **Strategy:** iterate location_ids (~10s) × paginated variant_ids batches (~100/req). Memory: store per-(location,variant) rows.
- **Snapshot fact `fct_inventory_snapshot`:** populated daily via `incremental_strategy='merge'` keyed on `(location_id, variant_id, snapshot_date)`. Source = current state from inventory_locations API; snapshot_date = `current_date` at run time.
- **Collections:** custom + smart — small dims; full refresh; one staging view each, joined into `dim_products` later (post-MVP).
- **Validate command:** call `GET /com/<entity>/count.json` → compare to `SELECT count(*) FROM raw.haravan_<entity>`. Tolerance ±0.1% for incremental skew. Exit 0 if within tolerance, 1 otherwise.
- **Snapshot trigger:** part of `run-all` cron pipeline; fact computed in dbt after load.

## Requirements

**Functional:**
- P1 extractors: `inventory_adjustments`, `inventory_locations` (balance), `custom_collections`, `smart_collections`
- New fact: `fct_inventory_snapshot` daily incremental
- New command: `haravan-elt validate <domain>` — exit 0/1
- Run-all updated to include P1 domains in DOMAIN_ORDER (after orders)

**Non-functional:**
- Inventory location balance fetch < 5min for shop with 10 locations × 5k variants
- Snapshot fact insert idempotent on `(location_id, variant_id, snapshot_date)`

## Architecture

```
P1 extractor additions:
  inventory_adjustments     ← /com/inventories/adjustments.json (paginated, updated_at_min)
  inventory_locations       ← /com/inventory_locations.json?location_ids=...&variant_ids=...
                              (cartesian iteration, batches)
  custom_collections        ← /com/custom_collections.json
  smart_collections         ← /com/smart_collections.json

Updated DOMAIN_ORDER:
  locations
  customers
  products
  custom_collections
  smart_collections
  orders
  inventory_adjustments
  inventory_locations  ← run last (depends on locations + variants existing)

dbt staging additions:
  stg_haravan__inventory_adjustments
  stg_haravan__inventory_locations    (with snapshot_date column)
  stg_haravan__custom_collections
  stg_haravan__smart_collections

dbt marts additions:
  fct_inventory_adjustments    (replace placeholder)
  fct_inventory_snapshot       (replace placeholder; incremental merge on (loc,variant,date))

Validate flow:
  haravan-elt validate orders
    ├─ api_count = GET /com/orders/count.json → {"count": N}
    ├─ db_count  = SELECT count(*) FROM raw.haravan_orders
    ├─ ratio = abs(api_count - db_count) / max(api_count, 1)
    ├─ if ratio <= 0.001: print OK, exit 0
    └─ else: print mismatch, exit 1
```

## Related Code Files

**Create:**
- `src/haravan_elt/extractors/inventory_adjustments.py`
- `src/haravan_elt/extractors/inventory_locations.py`
- `src/haravan_elt/extractors/custom_collections.py`
- `src/haravan_elt/extractors/smart_collections.py`
- `src/haravan_elt/validate.py` — count comparison logic
- `dbt/models/staging/haravan/stg_haravan__inventory_adjustments.sql`
- `dbt/models/staging/haravan/stg_haravan__inventory_locations.sql`
- `dbt/models/staging/haravan/stg_haravan__custom_collections.sql`
- `dbt/models/staging/haravan/stg_haravan__smart_collections.sql`
- `dbt/models/intermediate/inventory/int_inventory__snapshot_prepared.sql`
- `tests/test_inventory_extractors.py`
- `tests/test_collections_extractors.py`
- `tests/test_validate.py`
- VCR fixtures for new endpoints

**Modify:**
- `src/haravan_elt/meta/raw_tables.sql` — add 4 new raw tables
- `src/haravan_elt/extractors/registry.py` — add 4 entries + update DOMAIN_ORDER
- `dbt/models/marts/core/fct_inventory_adjustments.sql` — replace placeholder
- `dbt/models/marts/core/fct_inventory_snapshot.sql` — replace placeholder
- `dbt/models/marts/core/_marts_core.yml` — add tests
- `src/haravan_elt/cli.py` — `validate` real impl
- `dbt/models/staging/haravan/sources.yml` — register 4 new sources

## Implementation Steps

1. **Raw tables DDL append:**
   ```sql
   CREATE TABLE IF NOT EXISTS raw.haravan_inventory_adjustments (LIKE raw.haravan_orders INCLUDING ALL);
   CREATE TABLE IF NOT EXISTS raw.haravan_inventory_locations (
       id            TEXT PRIMARY KEY,            -- composite "location_id:variant_id:snapshot_date"
       location_id   BIGINT NOT NULL,
       variant_id    BIGINT NOT NULL,
       snapshot_date DATE NOT NULL,
       payload       JSONB NOT NULL,
       updated_at    TIMESTAMPTZ NOT NULL,
       ingested_at   TIMESTAMPTZ DEFAULT now(),
       source_run_id UUID NOT NULL
   );
   CREATE TABLE IF NOT EXISTS raw.haravan_custom_collections (LIKE raw.haravan_orders INCLUDING ALL);
   CREATE TABLE IF NOT EXISTS raw.haravan_smart_collections (LIKE raw.haravan_orders INCLUDING ALL);
   ```

2. **`inventory_adjustments.py`** — copy of customers/orders pattern, endpoint `/com/inventories/adjustments.json`, json key `inventory_adjustments`.

3. **`inventory_locations.py`** — special: cartesian batched fetch:
   ```python
   class InventoryLocationsExtractor(BaseExtractor):
       domain = "inventory_locations"
       raw_table = "raw.haravan_inventory_locations"
       supports_incremental = False  # snapshot-style, full each run

       def iter_pages(self, since=None, until=None):
           # Get all location_ids from raw.haravan_locations
           # Get all variant_ids from raw.haravan_products (variants[])
           location_ids = self._fetch_location_ids()
           variant_ids = self._fetch_variant_ids()
           BATCH = 100  # variants per request
           for loc_id in location_ids:
               for i in range(0, len(variant_ids), BATCH):
                   chunk = variant_ids[i:i+BATCH]
                   params = {
                       "location_ids": loc_id,
                       "variant_ids": ",".join(str(v) for v in chunk),
                   }
                   resp = self.client.get("/com/inventory_locations.json", params=params)
                   items = resp.json().get("inventory_locations", [])
                   if items:
                       yield items

       def to_raw_row(self, item):
           snap = datetime.now(timezone.utc).date()
           pk = f"{item['location_id']}:{item['variant_id']}:{snap}"
           return {
               "id": pk,
               "location_id": item["location_id"],
               "variant_id":  item["variant_id"],
               "snapshot_date": snap,
               "payload": Jsonb(item),
               "updated_at": datetime.now(timezone.utc),
               "source_run_id": str(self.run_id),
           }
   ```

4. **`custom_collections.py`** + **`smart_collections.py`** — standard pattern.

5. **Update `registry.py`:**
   ```python
   EXTRACTORS = {
     "orders": OrdersExtractor,
     "customers": CustomersExtractor,
     "products": ProductsExtractor,
     "locations": LocationsExtractor,
     "inventory_adjustments": InventoryAdjustmentsExtractor,
     "inventory_locations": InventoryLocationsExtractor,
     "custom_collections": CustomCollectionsExtractor,
     "smart_collections": SmartCollectionsExtractor,
   }
   DOMAIN_ORDER = ["locations","customers","products","custom_collections","smart_collections",
                   "orders","inventory_adjustments","inventory_locations"]
   ```

6. **dbt staging models** (similar inline cast pattern):
   ```sql
   -- stg_haravan__inventory_adjustments.sql
   {{ config(materialized='view') }}
   select
     (payload->>'id')::bigint                          as adjustment_id,
     (payload->>'variant_id')::bigint                  as variant_id,
     (payload->>'location_id')::bigint                 as location_id,
     (payload->>'quantity_delta')::int                 as quantity_delta,
     (payload->>'cost_delta')::numeric(18,2)           as cost_delta_vnd,
     (payload->>'created_at')::timestamptz             as created_at,
     (payload->>'updated_at')::timestamptz             as adjustment_updated_at,
     payload->>'reason'                                as reason,
     ingested_at, source_run_id
   from {{ source('raw_haravan', 'inventory_adjustments') }}
   ```

   ```sql
   -- stg_haravan__inventory_locations.sql
   {{ config(materialized='view') }}
   select
     location_id, variant_id, snapshot_date,
     (payload->>'available')::int                      as available_quantity,
     (payload->>'on_hand')::int                        as current_quantity,
     (payload->>'committed')::int                      as committed_quantity,
     ingested_at, source_run_id
   from {{ source('raw_haravan', 'inventory_locations') }}
   ```

7. **Replace placeholder `fct_inventory_adjustments.sql`:**
   ```sql
   {{ config(materialized='incremental', unique_key='adjustment_id', incremental_strategy='merge') }}
   select
     {{ dbt_utils.generate_surrogate_key(['adjustment_id']) }}    as adjustment_key,
     {{ dbt_utils.generate_surrogate_key(['variant_id']) }}       as variant_key,
     {{ dbt_utils.generate_surrogate_key(['location_id']) }}      as location_key,
     to_char(created_at::date, 'YYYYMMDD')::int                   as adjustment_date_key,
     adjustment_id, variant_id, location_id,
     quantity_delta, cost_delta_vnd, reason, created_at
   from {{ ref('stg_haravan__inventory_adjustments') }}
   {% if is_incremental() %}
     where created_at >= (select coalesce(max(created_at), '1900-01-01'::timestamptz) from {{ this }})
   {% endif %}
   ```

8. **Replace placeholder `fct_inventory_snapshot.sql`:**
   ```sql
   {{ config(
     materialized='incremental',
     unique_key=['location_id','variant_id','snapshot_date'],
     incremental_strategy='merge'
   ) }}
   select
     {{ dbt_utils.generate_surrogate_key(['location_id','variant_id','snapshot_date']) }} as snapshot_key,
     {{ dbt_utils.generate_surrogate_key(['variant_id']) }}   as variant_key,
     {{ dbt_utils.generate_surrogate_key(['location_id']) }}  as location_key,
     to_char(snapshot_date, 'YYYYMMDD')::int                  as snapshot_date_key,
     location_id, variant_id, snapshot_date,
     current_quantity, available_quantity, committed_quantity
   from {{ ref('stg_haravan__inventory_locations') }}
   {% if is_incremental() %}
     where snapshot_date >= (select coalesce(max(snapshot_date), current_date - interval '90 days') from {{ this }})
   {% endif %}
   ```

9. **Sources YAML** add:
   ```yaml
   - name: inventory_adjustments
     identifier: haravan_inventory_adjustments
   - name: inventory_locations
     identifier: haravan_inventory_locations
   - name: custom_collections
     identifier: haravan_custom_collections
   - name: smart_collections
     identifier: haravan_smart_collections
   ```

10. **`validate.py`:**
    ```python
    DOMAIN_COUNT_ENDPOINT = {
      "orders": "/com/orders/count.json",
      "customers": "/com/customers/count.json",
      "products": "/com/products/count.json",
      "inventory_adjustments": "/com/inventories/adjustments/count.json",
      "custom_collections": "/com/custom_collections/count.json",
      "smart_collections": "/com/smart_collections/count.json",
      # locations has no /count endpoint; handle via len(/locations.json)
    }
    DOMAIN_RAW_TABLE = {
      "orders": "raw.haravan_orders",
      ...
    }

    def validate(domain: str, settings, tolerance: float = 0.001) -> tuple[bool, int, int]:
        client = HaravanClient(settings)
        endpoint = DOMAIN_COUNT_ENDPOINT.get(domain)
        if endpoint:
            api_count = client.get(endpoint).json().get("count", 0)
        else:
            # locations: count via list length
            api_count = len(client.get("/com/locations.json").json().get("locations", []))
        with psycopg.connect(settings.database.database_url) as conn:
            db_count = conn.execute(f"SELECT count(*) FROM {DOMAIN_RAW_TABLE[domain]}").fetchone()[0]
        ratio = abs(api_count - db_count) / max(api_count, 1)
        return ratio <= tolerance, api_count, db_count
    ```

11. **CLI `validate` final:**
    ```python
    @app.command()
    def validate(domain: str, tolerance: float = 0.001):
        settings = Settings()
        ok, api_n, db_n = run_validate(domain, settings, tolerance)
        typer.echo(f"{domain}: api={api_n} db={db_n} ratio={(abs(api_n-db_n)/max(api_n,1)):.4f}")
        if not ok:
            raise typer.Exit(1)
    ```

12. **Tests:** VCR fixtures for each new endpoint; one fixture per cassette covering happy + edge cases. `test_validate.py` mocks Postgres count + Haravan count.

## Todo List

- [ ] Append DDL for 4 new raw tables (incl. composite-PK `inventory_locations`)
- [ ] Implement 4 new extractors (inventory_adjustments, inventory_locations cartesian, custom_collections, smart_collections)
- [ ] Update `extractors/registry.py` (8 domains total + new DOMAIN_ORDER)
- [ ] Add 4 staging models + 1 intermediate (snapshot_prepared if needed)
- [ ] Replace placeholder `fct_inventory_adjustments` and `fct_inventory_snapshot` with full impl
- [ ] Add new sources to `sources.yml`
- [ ] Implement `validate.py` (count comparison) + CLI command
- [ ] Record VCR cassettes for new endpoints + count.json
- [ ] Write tests (4 extractors + validate); verify cartesian batching correctness
- [ ] Manual: `haravan-elt run-all` includes P1 domains; `haravan-elt validate orders` returns OK
- [ ] Verify `fct_inventory_snapshot` incremental: re-run same day → row count unchanged; next day → new row per (loc,variant)

## Success Criteria

- All 4 P1 extractors integrate into `run-all`; no domain order regressions
- `fct_inventory_snapshot` populated correctly: 1 row per (location, variant, snapshot_date)
- `haravan-elt validate orders` exit 0 on parity, exit 1 on mismatch (verified via fixture)
- Cartesian iteration in `inventory_locations` covers all (loc, variant) pairs (assert in test)
- Test coverage of new code ≥75%

## Risk Assessment

| Risk | Likelihood | Impact | Mitigation |
|------|-----------|--------|-----------|
| Cartesian inventory fetch hits rate limit | High | Med | Already rate-limited via HaravanClient; chunk variants 100/req; expect ~1000 calls for 5k variants |
| Snapshot rerun on same day → unique key collision | Low | Med | `unique_key=['loc,var,date']` with merge → idempotent overwrite |
| `inventory_locations` endpoint requires non-empty `variant_ids` (errors on empty) | Med | Low | Skip request if chunk empty |
| `count.json` endpoint missing for some domains (e.g., locations) | Med | Low | Fallback to list length; documented in `DOMAIN_COUNT_ENDPOINT` |
| Validate tolerance too tight after legitimate incremental drift | Med | Low | Default 0.1% configurable via `--tolerance` flag |

## Security Considerations

- No new secrets; same HaravanClient + DB credentials
- Validate command performs one DB count query — read-only, safe

## Next Steps

Unblocks **phase-10** (hardening builds on full P1 coverage).

## Unresolved Questions

- Q1: Does `inventory_locations` endpoint return historical state, or only current? Assumption: only current → fine for daily snapshot.
- Q2: `inventory_adjustments` PK — does Haravan return `id` for each adjustment? Assume yes; test confirms.
- Q3: Should `validate` support `--all`? Defer; manual per-domain enough for MVP.
