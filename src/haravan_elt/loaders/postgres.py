"""PostgresLoader: batch upsert into `raw.*` tables.

Uses `psycopg.sql.Identifier` / `Placeholder` composition (no string concat
→ SQL injection safe). `executemany` + `ON CONFLICT (id) DO UPDATE` keeps
re-runs idempotent. The `WHERE EXCLUDED.updated_at >= existing.updated_at`
clause guards against out-of-order pages overwriting newer rows with older.
"""

from __future__ import annotations

from typing import Any

import psycopg
import structlog
from psycopg import sql

logger = structlog.get_logger(__name__)


class PostgresLoader:
    """Connect-per-batch loader. Each `upsert_batch()` opens a fresh connection
    and commits via `with` block (psycopg3 auto-commits on clean exit)."""

    def __init__(self, dsn: str) -> None:
        self._dsn = dsn

    def upsert_batch(
        self,
        table: str,
        rows: list[dict[str, Any]],
        *,
        conflict_col: str = "id",
        batch_size: int = 500,
    ) -> int:
        """Upsert rows into `table` (qualified, e.g. 'raw.haravan_orders').

        Returns the count of rows SUBMITTED (input length), NOT the count
        actually written: the `WHERE EXCLUDED.updated_at >= existing` guard
        may silently skip stale rows. psycopg3 `cur.rowcount` after
        `executemany` is unreliable per research §3, so we trust the
        Python-side count of submitted rows.
        """
        if not rows:
            return 0

        cols = list(rows[0].keys())
        if conflict_col not in cols:
            raise ValueError(f"conflict_col {conflict_col!r} not present in row keys: {cols}")

        schema_part, table_part = self._split_table(table)
        query = self._build_upsert(schema_part, table_part, cols, conflict_col)

        total = 0
        with psycopg.connect(self._dsn) as conn, conn.cursor() as cur:
            for offset in range(0, len(rows), batch_size):
                chunk = rows[offset : offset + batch_size]
                values = [tuple(row[col] for col in cols) for row in chunk]
                cur.executemany(query, values)
                total += len(chunk)
                logger.debug(
                    "upsert_batch", table=table, batch_index=offset // batch_size, rows=len(chunk)
                )
        return total

    @staticmethod
    def _split_table(table: str) -> tuple[str, str]:
        if "." not in table:
            raise ValueError(f"qualified table required, got {table!r}")
        schema, tbl = table.split(".", 1)
        return schema, tbl

    @staticmethod
    def _build_upsert(
        schema: str,
        table: str,
        cols: list[str],
        conflict_col: str,
    ) -> sql.Composed:
        col_idents = sql.SQL(", ").join(sql.Identifier(c) for c in cols)
        placeholders = sql.SQL(", ").join(sql.Placeholder() for _ in cols)
        update_clauses = sql.SQL(", ").join(
            sql.SQL("{c}=EXCLUDED.{c}").format(c=sql.Identifier(c))
            for c in cols
            if c != conflict_col
        )
        return sql.SQL(
            "INSERT INTO {schema}.{tbl} ({cols}, ingested_at) "
            "VALUES ({ph}, now()) "
            "ON CONFLICT ({k}) DO UPDATE SET "
            "{upd}, ingested_at=now() "
            "WHERE EXCLUDED.updated_at >= {schema}.{tbl}.updated_at"
        ).format(
            schema=sql.Identifier(schema),
            tbl=sql.Identifier(table),
            cols=col_idents,
            ph=placeholders,
            k=sql.Identifier(conflict_col),
            upd=update_clauses,
        )
