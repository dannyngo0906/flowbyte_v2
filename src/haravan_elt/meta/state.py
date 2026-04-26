"""StateManager: watermark + run_log persistence on `meta.*`.

Watermark update uses `GREATEST(existing, incoming)` so concurrent or
out-of-order runs never regress the high mark (research §5). Each method
opens its own short-lived connection — the run_log start/end happens in a
separate transaction from data load, satisfying FR-L5 + idempotency.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any
from uuid import UUID

import psycopg
import structlog
from psycopg.rows import dict_row

logger = structlog.get_logger(__name__)


class StateManager:
    def __init__(self, dsn: str) -> None:
        self._dsn = dsn

    # ------------------------------------------------------------------ watermark

    def get_watermark(self, domain: str) -> datetime | None:
        sql = "SELECT last_updated_at FROM meta.sync_state WHERE domain = %s"
        with psycopg.connect(self._dsn, row_factory=dict_row) as conn:
            row = conn.execute(sql, (domain,)).fetchone()
        if row is None:
            return None
        ts: datetime = row["last_updated_at"]
        return ts

    def update_watermark(self, domain: str, ts: datetime, run_id: UUID) -> None:
        sql = """
            INSERT INTO meta.sync_state (domain, last_updated_at, last_run_id, updated_at)
            VALUES (%s, %s, %s, now())
            ON CONFLICT (domain) DO UPDATE SET
                last_updated_at = GREATEST(meta.sync_state.last_updated_at, EXCLUDED.last_updated_at),
                last_run_id = EXCLUDED.last_run_id,
                updated_at = now()
        """
        with psycopg.connect(self._dsn) as conn:
            conn.execute(sql, (domain, ts, run_id))
        logger.debug("watermark_updated", domain=domain, ts=ts.isoformat())

    # ------------------------------------------------------------- high_id watermark
    # Used by append-only event-style domains (phase-11 events) where the API
    # paginates by `since_id` instead of `updated_at_min`.

    def get_high_id(self, domain: str) -> int | None:
        sql = "SELECT last_high_id FROM meta.sync_state WHERE domain = %s"
        with psycopg.connect(self._dsn, row_factory=dict_row) as conn:
            row = conn.execute(sql, (domain,)).fetchone()
        if row is None or row["last_high_id"] is None:
            return None
        return int(row["last_high_id"])

    def update_high_id(self, domain: str, high_id: int, run_id: UUID) -> None:
        # last_updated_at is NOT NULL on the table — events have no native ts,
        # so we stamp now() for that column. The semantic source of truth is
        # last_high_id; last_updated_at acts as "row most recently touched".
        sql = """
            INSERT INTO meta.sync_state (domain, last_updated_at, last_high_id, last_run_id, updated_at)
            VALUES (%s, now(), %s, %s, now())
            ON CONFLICT (domain) DO UPDATE SET
                last_high_id = GREATEST(coalesce(meta.sync_state.last_high_id, 0), EXCLUDED.last_high_id),
                last_run_id = EXCLUDED.last_run_id,
                updated_at = now()
        """
        with psycopg.connect(self._dsn) as conn:
            conn.execute(sql, (domain, high_id, run_id))
        logger.debug("high_id_updated", domain=domain, high_id=high_id)

    # --------------------------------------------------------------------- run_log

    def start_run(
        self,
        run_id: UUID,
        domain: str,
        mode: str,
        triggered_by: str = "manual",
    ) -> None:
        sql = """
            INSERT INTO meta.run_log
                (run_id, domain, mode, started_at, status, triggered_by)
            VALUES (%s, %s, %s, now(), 'running', %s)
        """
        with psycopg.connect(self._dsn) as conn:
            conn.execute(sql, (run_id, domain, mode, triggered_by))
        logger.info("run_started", run_id=str(run_id), domain=domain, mode=mode)

    def end_run(
        self,
        run_id: UUID,
        rows_ingested: int,
        status: str,
        error_message: str | None = None,
    ) -> None:
        # Truncate huge tracebacks to 1000 chars to keep run_log lean.
        truncated = error_message[:1000] if error_message else None
        sql = """
            UPDATE meta.run_log
               SET ended_at = now(),
                   rows_ingested = %s,
                   status = %s,
                   error_message = %s
             WHERE run_id = %s
        """
        with psycopg.connect(self._dsn) as conn:
            conn.execute(sql, (rows_ingested, status, truncated, run_id))
        logger.info("run_ended", run_id=str(run_id), status=status, rows=rows_ingested)

    # ------------------------------------------------------------------ inspection

    def latest_runs(self, limit: int = 10) -> list[dict[str, Any]]:
        """Used by the future `status` CLI subcommand (phase-07)."""
        sql = """
            SELECT run_id, domain, mode, started_at, ended_at,
                   rows_ingested, status, error_message
              FROM meta.run_log
             ORDER BY started_at DESC
             LIMIT %s
        """
        with psycopg.connect(self._dsn, row_factory=dict_row) as conn:
            return conn.execute(sql, (limit,)).fetchall()

    def list_status(self) -> list[dict[str, Any]]:
        """Per-domain snapshot: high watermark + most recent run row.

        LATERAL join picks the latest `run_log` row per domain so the result is
        always 1 row per known domain, regardless of how many runs exist.
        """
        sql = """
            SELECT s.domain,
                   s.last_updated_at,
                   r.status,
                   r.mode,
                   r.started_at,
                   r.ended_at,
                   r.rows_ingested,
                   r.error_message
              FROM meta.sync_state s
              LEFT JOIN LATERAL (
                  SELECT status, mode, started_at, ended_at, rows_ingested, error_message
                    FROM meta.run_log
                   WHERE domain = s.domain
                   ORDER BY started_at DESC
                   LIMIT 1
              ) r ON true
             ORDER BY s.domain
        """
        with psycopg.connect(self._dsn, row_factory=dict_row) as conn:
            return conn.execute(sql).fetchall()
