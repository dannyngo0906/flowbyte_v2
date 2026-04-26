"""Typer CLI entrypoint.

Phase-01: `init` (run schema bootstrap).
Phase-03: `extract` (currently orders only).
Phase-07 fills the remaining verbs (transform, run-all, status, validate, notify).
"""

from __future__ import annotations

import uuid
from datetime import datetime
from importlib import resources
from zoneinfo import ZoneInfo

import psycopg
import structlog
import typer
from rich.console import Console

from haravan_elt.client.haravan import HaravanClient
from haravan_elt.config import load_settings
from haravan_elt.extractors.registry import EXTRACTORS
from haravan_elt.loaders.postgres import PostgresLoader
from haravan_elt.logging import setup_logging
from haravan_elt.meta.state import StateManager

app = typer.Typer(no_args_is_help=True, add_completion=False)
console = Console()


def _parse_iso_arg(name: str, value: str | None, default_tz: ZoneInfo) -> datetime | None:
    """Parse an ISO datetime CLI arg. Naive values are coerced to `default_tz`
    (research §4: Haravan filters use shop tz). Bad input → exit 2 (invalid args)."""
    if value is None:
        return None
    try:
        dt = datetime.fromisoformat(value)
    except ValueError as exc:
        typer.echo(f"invalid --{name}: {exc}", err=True)
        raise typer.Exit(2) from exc
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=default_tz)
    return dt


@app.callback()
def main(verbose: bool = typer.Option(False, "--verbose", "-v")) -> None:
    """haravan-elt — Haravan Omni API → PostgreSQL → dbt star schema."""
    setup_logging(json_output=False, level="DEBUG" if verbose else "INFO")


@app.command()
def init() -> None:
    """Apply schema.sql + raw_tables.sql against DATABASE_URL. Idempotent."""
    settings = load_settings()
    dsn = settings.database.database_url.get_secret_value()
    schema_sql = _read_packaged("meta/schema.sql")
    raw_sql = _read_packaged("meta/raw_tables.sql")
    with psycopg.connect(dsn) as conn, conn.cursor() as cur:
        cur.execute(schema_sql)
        cur.execute(raw_sql)
    console.print("[green]✓[/green] schemas + meta + raw tables ready")


@app.command()
def extract(
    domain: str = typer.Argument(..., help="Domain name, e.g. 'orders'"),
    mode: str = typer.Option("incremental", help="full | incremental"),
    since: str | None = typer.Option(None, help="ISO datetime lower bound (overrides watermark)"),
    until: str | None = typer.Option(None, help="ISO datetime upper bound"),
    dry_run: bool = typer.Option(False, "--dry-run"),
) -> None:
    """Extract+load a single domain into raw.haravan_<domain>."""
    if mode not in {"full", "incremental"}:
        typer.echo(f"invalid --mode: {mode!r} (use 'full' or 'incremental')", err=True)
        raise typer.Exit(2)
    if domain not in EXTRACTORS:
        typer.echo(f"unknown domain: {domain}; known: {sorted(EXTRACTORS)}", err=True)
        raise typer.Exit(2)

    settings = load_settings()
    shop_tz = ZoneInfo(settings.timezone)
    # Validate args BEFORE anything mutates run_log so bad input → clean exit 2.
    since_dt = _parse_iso_arg("since", since, shop_tz)
    until_dt = _parse_iso_arg("until", until, shop_tz)

    run_id = uuid.uuid4()
    structlog.contextvars.bind_contextvars(run_id=str(run_id), domain=domain, mode=mode)

    dsn = settings.database.database_url.get_secret_value()
    client: HaravanClient | None = None
    try:
        client = HaravanClient(settings)
        loader = PostgresLoader(dsn)
        state = StateManager(dsn)
        extractor = EXTRACTORS[domain](client, loader, state, run_id)

        state.start_run(run_id, domain, mode)
        try:
            rows, max_ts = extractor.idempotent_load(mode, since=since_dt, until=until_dt)
            # Skip watermark write for full-refresh-only domains (e.g. locations) —
            # their max_ts is synthetic (datetime.now()) and would pollute sync_state.
            if max_ts is not None and not dry_run and extractor.supports_incremental:
                state.update_watermark(domain, max_ts, run_id)
            state.end_run(run_id, rows, "success")
            console.print(
                f"[green]OK[/green] {domain}: {rows} rows, watermark={max_ts}, dry_run={dry_run}"
            )
        except Exception as exc:  # noqa: BLE001 — re-raised below
            state.end_run(run_id, 0, "failed", str(exc))
            typer.echo(f"FAIL {domain}: {exc}", err=True)
            raise typer.Exit(1) from exc
    finally:
        if client is not None:
            client.close()


def _read_packaged(relpath: str) -> str:
    return resources.files("haravan_elt").joinpath(relpath).read_text(encoding="utf-8")


if __name__ == "__main__":
    app()
