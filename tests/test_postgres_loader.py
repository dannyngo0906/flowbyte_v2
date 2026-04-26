"""PostgresLoader integration tests against the docker-compose Postgres.

Skipped automatically when the container isn't reachable (CI without DB).
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from uuid import uuid4

import psycopg
from psycopg.types.json import Jsonb

from haravan_elt.loaders.postgres import PostgresLoader


def _row(id_: int, updated_at: datetime, payload_overrides: dict | None = None) -> dict:
    payload = {"id": id_, "name": f"#{id_}", "extra": payload_overrides or {}}
    return {
        "id": id_,
        "payload": Jsonb(payload),
        "updated_at": updated_at,
        "source_run_id": str(uuid4()),
    }


def _count(dsn: str) -> int:
    with psycopg.connect(dsn) as conn:
        cur = conn.execute("SELECT count(*) FROM raw.haravan_orders")
        result = cur.fetchone()
        assert result is not None
        n: int = result[0]
        return n


def test_upsert_inserts_new_rows(pg_clean: str) -> None:
    loader = PostgresLoader(pg_clean)
    now = datetime.now(UTC)
    rows = [_row(i, now) for i in range(5)]
    inserted = loader.upsert_batch("raw.haravan_orders", rows)
    assert inserted == 5
    assert _count(pg_clean) == 5


def test_upsert_idempotent_same_rows(pg_clean: str) -> None:
    loader = PostgresLoader(pg_clean)
    now = datetime.now(UTC)
    rows = [_row(i, now) for i in range(10)]
    loader.upsert_batch("raw.haravan_orders", rows)
    loader.upsert_batch("raw.haravan_orders", rows)
    loader.upsert_batch("raw.haravan_orders", rows)
    assert _count(pg_clean) == 10  # no duplicates


def test_upsert_replaces_payload_when_newer(pg_clean: str) -> None:
    loader = PostgresLoader(pg_clean)
    base = datetime.now(UTC)
    loader.upsert_batch(
        "raw.haravan_orders",
        [_row(1, base, payload_overrides={"v": "old"})],
    )
    loader.upsert_batch(
        "raw.haravan_orders",
        [_row(1, base + timedelta(seconds=1), payload_overrides={"v": "new"})],
    )
    with psycopg.connect(pg_clean) as conn:
        cur = conn.execute("SELECT payload FROM raw.haravan_orders WHERE id = 1")
        result = cur.fetchone()
        assert result is not None
        payload = result[0]
    assert payload["extra"]["v"] == "new"


def test_upsert_skips_older_payload(pg_clean: str) -> None:
    """The `WHERE EXCLUDED.updated_at >= existing` guard keeps newer rows safe."""
    loader = PostgresLoader(pg_clean)
    base = datetime.now(UTC)
    loader.upsert_batch(
        "raw.haravan_orders",
        [_row(1, base + timedelta(seconds=10), payload_overrides={"v": "newer"})],
    )
    loader.upsert_batch(
        "raw.haravan_orders",
        [_row(1, base, payload_overrides={"v": "older"})],
    )
    with psycopg.connect(pg_clean) as conn:
        cur = conn.execute("SELECT payload FROM raw.haravan_orders WHERE id = 1")
        result = cur.fetchone()
        assert result is not None
        payload = result[0]
    assert payload["extra"]["v"] == "newer"


def test_upsert_batches_large_set(pg_clean: str) -> None:
    loader = PostgresLoader(pg_clean)
    now = datetime.now(UTC)
    rows = [_row(i, now) for i in range(1500)]
    inserted = loader.upsert_batch("raw.haravan_orders", rows, batch_size=500)
    assert inserted == 1500
    assert _count(pg_clean) == 1500


def test_upsert_empty_returns_zero(pg_clean: str) -> None:
    loader = PostgresLoader(pg_clean)
    assert loader.upsert_batch("raw.haravan_orders", []) == 0


def test_unqualified_table_raises(pg_clean: str) -> None:
    loader = PostgresLoader(pg_clean)
    now = datetime.now(UTC)
    try:
        loader.upsert_batch("haravan_orders", [_row(1, now)])
    except ValueError as exc:
        assert "qualified table" in str(exc)
    else:
        raise AssertionError("expected ValueError")
