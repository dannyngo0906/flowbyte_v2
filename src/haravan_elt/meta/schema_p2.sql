-- Phase-11: P2 raw tables (Discounts, Promotions, Events) + sync_state
-- column for append-only event-style domains.
-- Idempotent: safe to re-run alongside schema.sql.

CREATE TABLE IF NOT EXISTS raw.haravan_discounts (
    id            BIGINT PRIMARY KEY,
    payload       JSONB NOT NULL,
    updated_at    TIMESTAMPTZ NOT NULL,
    ingested_at   TIMESTAMPTZ DEFAULT now(),
    source_run_id UUID NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_haravan_discounts_updated
    ON raw.haravan_discounts(updated_at);

CREATE TABLE IF NOT EXISTS raw.haravan_promotions (
    id            BIGINT PRIMARY KEY,
    payload       JSONB NOT NULL,
    updated_at    TIMESTAMPTZ NOT NULL,
    ingested_at   TIMESTAMPTZ DEFAULT now(),
    source_run_id UUID NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_haravan_promotions_updated
    ON raw.haravan_promotions(updated_at);

-- Events are append-only audit-log style. No update semantics; we still
-- keep `updated_at` for the loader's ON CONFLICT WHERE EXCLUDED guard
-- (set to created_at on insert), and track `last_high_id` in sync_state.
CREATE TABLE IF NOT EXISTS raw.haravan_events (
    id            BIGINT PRIMARY KEY,
    payload       JSONB NOT NULL,
    updated_at    TIMESTAMPTZ NOT NULL,
    ingested_at   TIMESTAMPTZ DEFAULT now(),
    source_run_id UUID NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_haravan_events_id_desc
    ON raw.haravan_events(id DESC);

-- For append-only event-style domains, the high watermark is the largest
-- id seen so far rather than a timestamp. Existing time-based domains
-- ignore this column.
ALTER TABLE meta.sync_state ADD COLUMN IF NOT EXISTS last_high_id BIGINT;
