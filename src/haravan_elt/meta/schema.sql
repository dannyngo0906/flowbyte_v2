-- haravan-elt schema bootstrap.
-- Idempotent: safe to re-run.

CREATE SCHEMA IF NOT EXISTS raw;
CREATE SCHEMA IF NOT EXISTS staging;
CREATE SCHEMA IF NOT EXISTS marts;
CREATE SCHEMA IF NOT EXISTS meta;

-- High watermark per domain. Drives incremental extracts.
CREATE TABLE IF NOT EXISTS meta.sync_state (
    domain          TEXT PRIMARY KEY,
    last_updated_at TIMESTAMPTZ NOT NULL,
    last_run_id     UUID,
    updated_at      TIMESTAMPTZ DEFAULT now()
);

-- One row per CLI invocation. Joined with notifications for ops dashboards.
CREATE TABLE IF NOT EXISTS meta.run_log (
    run_id         UUID PRIMARY KEY,
    domain         TEXT NOT NULL,
    mode           TEXT NOT NULL,             -- 'full' | 'incremental'
    started_at     TIMESTAMPTZ NOT NULL,
    ended_at       TIMESTAMPTZ,
    rows_ingested  INTEGER,
    status         TEXT NOT NULL,             -- 'running' | 'success' | 'failed'
    error_message  TEXT,
    triggered_by   TEXT                       -- 'cron' | 'manual'
);

CREATE INDEX IF NOT EXISTS idx_run_log_started ON meta.run_log(started_at DESC);
