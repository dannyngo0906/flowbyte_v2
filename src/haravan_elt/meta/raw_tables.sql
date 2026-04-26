-- raw landing tables (one per Haravan domain). Idempotent.
-- Phase-03 ships only `raw.haravan_orders`; phase-04 adds the rest.

CREATE TABLE IF NOT EXISTS raw.haravan_orders (
    id            BIGINT PRIMARY KEY,
    payload       JSONB NOT NULL,
    updated_at    TIMESTAMPTZ NOT NULL,
    ingested_at   TIMESTAMPTZ DEFAULT now(),
    source_run_id UUID NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_haravan_orders_updated
    ON raw.haravan_orders(updated_at);

-- Phase-04: P0 dims share the same shape (id PK + JSONB payload + watermark cols).
CREATE TABLE IF NOT EXISTS raw.haravan_customers (
    id            BIGINT PRIMARY KEY,
    payload       JSONB NOT NULL,
    updated_at    TIMESTAMPTZ NOT NULL,
    ingested_at   TIMESTAMPTZ DEFAULT now(),
    source_run_id UUID NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_haravan_customers_updated
    ON raw.haravan_customers(updated_at);

CREATE TABLE IF NOT EXISTS raw.haravan_products (
    id            BIGINT PRIMARY KEY,
    payload       JSONB NOT NULL,
    updated_at    TIMESTAMPTZ NOT NULL,
    ingested_at   TIMESTAMPTZ DEFAULT now(),
    source_run_id UUID NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_haravan_products_updated
    ON raw.haravan_products(updated_at);

CREATE TABLE IF NOT EXISTS raw.haravan_locations (
    id            BIGINT PRIMARY KEY,
    payload       JSONB NOT NULL,
    updated_at    TIMESTAMPTZ NOT NULL,
    ingested_at   TIMESTAMPTZ DEFAULT now(),
    source_run_id UUID NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_haravan_locations_updated
    ON raw.haravan_locations(updated_at);

-- Phase-09: P1 domains.
-- Inventory adjustments share the orders shape (BIGINT id PK + JSONB).
CREATE TABLE IF NOT EXISTS raw.haravan_inventory_adjustments (
    id            BIGINT PRIMARY KEY,
    payload       JSONB NOT NULL,
    updated_at    TIMESTAMPTZ NOT NULL,
    ingested_at   TIMESTAMPTZ DEFAULT now(),
    source_run_id UUID NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_haravan_inventory_adjustments_updated
    ON raw.haravan_inventory_adjustments(updated_at);

-- Inventory location balance: composite key materialized as TEXT
-- ("location_id:variant_id:snapshot_date") so the standard upsert path works.
CREATE TABLE IF NOT EXISTS raw.haravan_inventory_locations (
    id            TEXT PRIMARY KEY,
    location_id   BIGINT NOT NULL,
    variant_id    BIGINT NOT NULL,
    snapshot_date DATE NOT NULL,
    payload       JSONB NOT NULL,
    updated_at    TIMESTAMPTZ NOT NULL,
    ingested_at   TIMESTAMPTZ DEFAULT now(),
    source_run_id UUID NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_haravan_inventory_locations_snapshot
    ON raw.haravan_inventory_locations(snapshot_date);

CREATE TABLE IF NOT EXISTS raw.haravan_custom_collections (
    id            BIGINT PRIMARY KEY,
    payload       JSONB NOT NULL,
    updated_at    TIMESTAMPTZ NOT NULL,
    ingested_at   TIMESTAMPTZ DEFAULT now(),
    source_run_id UUID NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_haravan_custom_collections_updated
    ON raw.haravan_custom_collections(updated_at);

CREATE TABLE IF NOT EXISTS raw.haravan_smart_collections (
    id            BIGINT PRIMARY KEY,
    payload       JSONB NOT NULL,
    updated_at    TIMESTAMPTZ NOT NULL,
    ingested_at   TIMESTAMPTZ DEFAULT now(),
    source_run_id UUID NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_haravan_smart_collections_updated
    ON raw.haravan_smart_collections(updated_at);
