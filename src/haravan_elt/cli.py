"""Typer CLI entrypoint.

Phase-01: only `init` (run schema.sql). Phase-07 fills extract/transform/run-all/etc.
"""

from __future__ import annotations

from importlib import resources

import psycopg
import typer
from rich.console import Console

from haravan_elt.config import load_settings

app = typer.Typer(no_args_is_help=True, add_completion=False)
console = Console()


@app.callback()
def main() -> None:
    """haravan-elt — Haravan Omni API → PostgreSQL → dbt star schema."""
    # Forces Typer into subcommand mode (so `haravan-elt init` is parsed correctly
    # even before phase-07 adds the rest of the verbs).


@app.command()
def init() -> None:
    """Apply schema.sql against DATABASE_URL. Idempotent."""
    settings = load_settings()
    sql = _read_schema_sql()
    dsn = settings.database.database_url.get_secret_value()
    # `with conn` already commits on clean exit; no explicit commit needed.
    with psycopg.connect(dsn) as conn, conn.cursor() as cur:
        cur.execute(sql)
    console.print("[green]✓[/green] schemas + meta tables ready")


def _read_schema_sql() -> str:
    """Load packaged schema.sql via importlib.resources for editable + wheel installs."""
    return resources.files("haravan_elt").joinpath("meta/schema.sql").read_text(encoding="utf-8")


if __name__ == "__main__":
    app()
