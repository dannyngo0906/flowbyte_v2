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
