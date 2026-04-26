# Phase 04 — Multi-Domain Extractors P0 (M2)

## Context Links

- PRD §4.1 (P0 domain table), §4.2, §4.3
- API endpoints: `/com/customers.json`, `/com/products.json`, `/com/variants/{id}.json`, `/com/locations.json`
- Research (API quirks §7 nested refunds/transactions, §8 variants embedded): `plans/reports/researcher-260426-1340-haravan-api-quirks.md`
- Phase-03 (Orders extractor pattern)

## Overview

- **Priority:** high
- **Status:** pending
- **Effort:** 5 days
- **Description:** Replicate the orders extractor pattern for the remaining P0 domains: customers, products (variants embedded), locations. Refunds + transactions are NOT separate extractors — embedded in orders payload, parsed in dbt staging (phase-05). Locations = full refresh each run (small dim).

## Key Insights

- **Refunds + transactions:** PRD §4.1 lists them as "P0 domains" with separate endpoints, but research §7 confirms they ride along with `order` JSON. **Decision: NO separate refund/transaction extractor.** Save 100s of API calls. Parse from `payload->'refunds'` / `payload->'transactions'` in dbt staging (phase-05).
- **Variants embedded in product:** `GET /com/products.json` returns each product with full `variants[]` array. **Decision: NO separate variant extractor for MVP.** Variant rows materialize in dbt via `jsonb_array_elements`. Single-fetch endpoint `/com/variants/{id}.json` is for ad-hoc lookups; not used here.
- **Locations:** small dim (typically <50 rows per shop). Full refresh each run, no incremental. No `updated_at_min` filter passed.
- **Customers, Products** support `updated_at_min`/`max` per research §4 — same pattern as orders.
- All P0 domains share `BaseExtractor.idempotent_load()`. Only `iter_pages()` + `to_raw_row()` overridden.

## Requirements

**Functional:**
- FR-E2/E4/E5/E6/E7: same as orders, applied to customers/products/locations
- FR-L1: tables `raw.haravan_customers`, `raw.haravan_products`, `raw.haravan_locations`
- FR-L2: same schema as orders
- Locations: `--mode full` ignored if absent (always full)

**Non-functional:**
- NFR-1: incremental for all P0 < 10 min combined

## Architecture

```
Domain dependency order (for run-all later in phase-07):
  locations  (no deps; full refresh)
  customers  (no deps; incremental)
  products   (no deps; incremental — includes variants[])
  orders     (depends on above as logical FK in dbt; embedded refunds/transactions)

Extractor registry:
  EXTRACTORS = {
    "orders": OrdersExtractor,
    "customers": CustomersExtractor,
    "products": ProductsExtractor,
    "locations": LocationsExtractor,
  }
```

## Related Code Files

**Create:**
- `src/haravan_elt/extractors/customers.py`
- `src/haravan_elt/extractors/products.py`
- `src/haravan_elt/extractors/locations.py`
- `src/haravan_elt/extractors/registry.py`
- `tests/test_customers_extractor.py`
- `tests/test_products_extractor.py`
- `tests/test_locations_extractor.py`
- `tests/fixtures/vcr/haravan_customers_full.yaml`
- `tests/fixtures/vcr/haravan_products_full.yaml`
- `tests/fixtures/vcr/haravan_locations_full.yaml`

**Modify:**
- `src/haravan_elt/meta/raw_tables.sql` — add `raw.haravan_customers`, `raw.haravan_products`, `raw.haravan_locations`
- `src/haravan_elt/cli.py` — wire registry; reject unknown domains; allow `--all` deferred to phase-07
- `src/haravan_elt/extractors/base.py` — extension point: `supports_incremental: bool = True` (False for locations)

## Implementation Steps

1. **DDL append (`raw_tables.sql`):**
   ```sql
   CREATE TABLE IF NOT EXISTS raw.haravan_customers (LIKE raw.haravan_orders INCLUDING ALL);
   CREATE TABLE IF NOT EXISTS raw.haravan_products  (LIKE raw.haravan_orders INCLUDING ALL);
   CREATE TABLE IF NOT EXISTS raw.haravan_locations (LIKE raw.haravan_orders INCLUDING ALL);
   ```
   (`LIKE` reuses constraint + index template; verify INCLUDING ALL behavior with PK.)

2. **`extractors/customers.py`** — clone orders pattern, change endpoint + key:
   ```python
   class CustomersExtractor(BaseExtractor):
       domain = "customers"
       raw_table = "raw.haravan_customers"

       def iter_pages(self, since, until):
           page, limit = 1, 50
           while True:
               params = {"page": page, "limit": limit}
               if since: params["updated_at_min"] = since.isoformat()
               if until: params["updated_at_max"] = until.isoformat()
               resp = self.client.get("/com/customers.json", params=params)
               items = resp.json().get("customers", [])
               if not items: break
               yield items
               if len(items) < limit: break
               page += 1

       def to_raw_row(self, item):
           return {
               "id": item["id"],
               "payload": Jsonb(item),
               "updated_at": _parse_iso(item["updated_at"]),
               "source_run_id": str(self.run_id),
           }
   ```

3. **`extractors/products.py`** — same pattern, key=`products`. Variants embedded — no extra logic. Note: `payload` contains full `variants` array; dbt phase explodes.

4. **`extractors/locations.py`** — full refresh always:
   ```python
   class LocationsExtractor(BaseExtractor):
       domain = "locations"
       raw_table = "raw.haravan_locations"
       supports_incremental = False

       def iter_pages(self, since=None, until=None):
           # No pagination needed; locations is small
           resp = self.client.get("/com/locations.json")
           items = resp.json().get("locations", [])
           if items:
               yield items

       def to_raw_row(self, item):
           # locations may not have updated_at — fallback to now()
           updated_at = item.get("updated_at") or item.get("modified_on")
           return {
               "id": item["id"],
               "payload": Jsonb(item),
               "updated_at": _parse_iso(updated_at) if updated_at else datetime.now(timezone.utc),
               "source_run_id": str(self.run_id),
           }
   ```

5. **`extractors/registry.py`:**
   ```python
   from .orders import OrdersExtractor
   from .customers import CustomersExtractor
   from .products import ProductsExtractor
   from .locations import LocationsExtractor

   EXTRACTORS = {
       "orders": OrdersExtractor,
       "customers": CustomersExtractor,
       "products": ProductsExtractor,
       "locations": LocationsExtractor,
   }
   DOMAIN_ORDER = ["locations", "customers", "products", "orders"]
   ```

6. **Helper `_parse_iso`** (shared util in `extractors/base.py` or `utils.py`):
   ```python
   def _parse_iso(s: str) -> datetime:
       return datetime.fromisoformat(s.replace("Z", "+00:00"))
   ```

7. **Update `cli.py`** to accept any registered domain (still single-domain at this phase; `--all` in phase-07).

8. **Tests** — for each extractor:
   - Fixture: 1 page or empty page
   - Assert correct endpoint URL hit (VCR records URL)
   - Assert correct JSON key parsed (`customers`, `products`, `locations`)
   - Assert idempotent re-run (same VCR replayed twice → same N rows in DB)
   - Locations test: assert `iter_pages` yields once even with `since/until` set

## Todo List

- [x] Append DDL for `raw.haravan_customers/products/locations` (LIKE haravan_orders pattern or explicit)
- [x] Implement `customers.py` extractor
- [x] Implement `products.py` extractor (variants embedded passthrough)
- [x] Implement `locations.py` extractor (full refresh, no pagination)
- [x] Add `extractors/registry.py` with `EXTRACTORS` dict + `DOMAIN_ORDER`
- [x] Wire registry in `cli.py extract` subcommand
- [ ] Record 3 VCR cassettes (customers, products, locations)  <!-- DEFERRED: respx mocks used; phase-10 cassette pass -->
- [x] Write 3 test files (parity with `test_orders_extractor.py`)
- [ ] Manual verify: 4 separate `haravan-elt extract <domain>` runs → 4 raw tables populated  <!-- DEFERRED: needs live token -->
- [x] Verify watermark advances correctly per domain (no cross-contamination)

## Success Criteria

- `haravan-elt extract customers/products/locations` each succeeds, idempotent on re-run
- 4 raw tables exist with correct row counts (verified via `psql`)
- `meta.sync_state` has 4 rows after first full run
- `meta.run_log` has 4 success rows
- Each extractor has dedicated VCR cassette + ≥80% test coverage
- Locations: re-running with `--mode incremental` flag still does full refresh (silent ignore + log warn)

## Risk Assessment

| Risk | Likelihood | Impact | Mitigation |
|------|-----------|--------|-----------|
| Locations endpoint returns wrapped key different from `locations` | Medium | Med | Defensive: `data.get("locations") or data.get("data") or []`, log on miss |
| Products variants array exceeds 100 → JSONB row too large | Low | Low | Postgres JSONB max 1GB; not a real risk |
| Customer endpoint uses `email_min`/`phone_min` filter quirks | Low | Med | Documented in test plan; `updated_at_min` confirmed sufficient |
| Refunds nested but missing transactions field if not loaded → dbt fails | Med | High | Phase-05 dbt model uses `coalesce(payload->'transactions', '[]'::jsonb)` defensively |

## Security Considerations

- All extractors reuse `HaravanClient` (auth handled there) — no token leakage
- `payload` stored as JSONB; PII (customer email/phone) in DB → defer GDPR/PII redaction discussion to post-MVP

## Next Steps

Unblocks **phase-05** (dbt staging — needs all 4 raw tables populated for fixture data).

## Unresolved Questions

- Q1: Locations endpoint: does it support `updated_at_min`? If yes, switch from full-refresh to incremental — minor optimization, defer.
- Q2: `/com/products.json` — confirm response wrapper key is `products` (not `data` or omitted at top level).
- Q3: Should we record a separate cassette per page for products (large shops have 1000s) or use only 1 page in tests? Decision: 1 page enough for unit; integration test in phase-10 hardening.
