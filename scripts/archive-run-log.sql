-- Move meta.run_log rows older than 90 days into meta.run_log_archive.
-- Idempotent: safe to re-run; rows already moved leave run_log empty for
-- that range. CTE keeps DELETE+INSERT in one transaction so we can't lose
-- rows if the INSERT fails (the DELETE rolls back too).
--
-- Recommended cadence: monthly via cron, e.g.:
--   0 3 1 * * psql "$DATABASE_URL" -f scripts/archive-run-log.sql

CREATE TABLE IF NOT EXISTS meta.run_log_archive (LIKE meta.run_log INCLUDING ALL);

WITH moved AS (
    DELETE FROM meta.run_log
    WHERE started_at < now() - interval '90 days'
    RETURNING *
)
INSERT INTO meta.run_log_archive
SELECT * FROM moved;
