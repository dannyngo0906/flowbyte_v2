"""StateManager integration tests — watermark + run_log."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from uuid import uuid4

import psycopg

from haravan_elt.meta.state import StateManager


def test_watermark_starts_none(pg_clean: str) -> None:
    state = StateManager(pg_clean)
    assert state.get_watermark("orders") is None


def test_watermark_set_and_read(pg_clean: str) -> None:
    state = StateManager(pg_clean)
    ts = datetime(2026, 4, 25, 10, 0, tzinfo=UTC)
    state.update_watermark("orders", ts, uuid4())
    assert state.get_watermark("orders") == ts


def test_watermark_greatest_prevents_regression(pg_clean: str) -> None:
    """Update with older ts must NOT clobber the newer watermark (research §5)."""
    state = StateManager(pg_clean)
    new = datetime(2026, 4, 25, 10, 0, tzinfo=UTC)
    old = new - timedelta(days=10)
    state.update_watermark("orders", new, uuid4())
    state.update_watermark("orders", old, uuid4())  # stale write
    assert state.get_watermark("orders") == new  # still the newer one


def test_run_log_lifecycle(pg_clean: str) -> None:
    state = StateManager(pg_clean)
    run_id = uuid4()
    state.start_run(run_id, domain="orders", mode="full", triggered_by="manual")

    with psycopg.connect(pg_clean) as conn:
        cur = conn.execute(
            "SELECT status, mode, ended_at FROM meta.run_log WHERE run_id = %s", (run_id,)
        )
        result = cur.fetchone()
        assert result is not None
        status, mode, ended_at = result
    assert (status, mode, ended_at) == ("running", "full", None)

    state.end_run(run_id, rows_ingested=42, status="success")

    with psycopg.connect(pg_clean) as conn:
        cur = conn.execute(
            "SELECT status, rows_ingested, ended_at FROM meta.run_log WHERE run_id = %s",
            (run_id,),
        )
        result = cur.fetchone()
        assert result is not None
        status, rows, ended_at = result
    assert status == "success"
    assert rows == 42
    assert ended_at is not None


def test_run_log_truncates_long_error(pg_clean: str) -> None:
    state = StateManager(pg_clean)
    run_id = uuid4()
    state.start_run(run_id, domain="orders", mode="full")
    long_error = "x" * 5000
    state.end_run(run_id, rows_ingested=0, status="failed", error_message=long_error)
    with psycopg.connect(pg_clean) as conn:
        cur = conn.execute("SELECT error_message FROM meta.run_log WHERE run_id = %s", (run_id,))
        result = cur.fetchone()
        assert result is not None
        stored = result[0]
    assert stored is not None
    assert len(stored) == 1000  # truncated


def test_high_id_starts_none(pg_clean: str) -> None:
    state = StateManager(pg_clean)
    assert state.get_high_id("promotions") is None


def test_high_id_set_and_read(pg_clean: str) -> None:
    state = StateManager(pg_clean)
    state.update_high_id("promotions", 12345, uuid4())
    assert state.get_high_id("promotions") == 12345


def test_high_id_greatest_prevents_regression(pg_clean: str) -> None:
    state = StateManager(pg_clean)
    state.update_high_id("promotions", 200, uuid4())
    state.update_high_id("promotions", 100, uuid4())  # stale
    assert state.get_high_id("promotions") == 200


def test_latest_runs_orders_descending(pg_clean: str) -> None:
    state = StateManager(pg_clean)
    ids = [uuid4() for _ in range(3)]
    for rid in ids:
        state.start_run(rid, domain="orders", mode="full")
        state.end_run(rid, 1, "success")
    runs = state.latest_runs(limit=10)
    assert len(runs) == 3
    starts = [r["started_at"] for r in runs]
    assert starts == sorted(starts, reverse=True)
