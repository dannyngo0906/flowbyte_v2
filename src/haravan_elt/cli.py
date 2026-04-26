"""Typer CLI entrypoint — final command surface (PRD §4.5).

Verbs: init, extract, transform, test, run-all, status, notify, validate.
Global flags: --verbose / --quiet / --json (structlog reconfigure).
Exit codes: 0 success, 1 fail, 2 invalid args / lock conflict.
"""

from __future__ import annotations

from datetime import datetime
from importlib import resources
from zoneinfo import ZoneInfo

import psycopg
import typer
from rich.console import Console

from haravan_elt.cli_helpers import render_status_table
from haravan_elt.client.haravan import HaravanClient
from haravan_elt.client.telegram import TelegramClient
from haravan_elt.config import Settings, load_settings
from haravan_elt.extractors.registry import EXTRACTORS
from haravan_elt.logging import setup_logging
from haravan_elt.meta.state import StateManager
from haravan_elt.pipeline import Pipeline
from haravan_elt.validate import DEFAULT_TOLERANCE, DOMAIN_RAW_TABLE
from haravan_elt.validate import validate as run_validate

app = typer.Typer(no_args_is_help=True, add_completion=False)
console = Console()


@app.callback()
def main(
    verbose: bool = typer.Option(False, "--verbose", "-v"),
    quiet: bool = typer.Option(False, "--quiet", "-q"),
    json_logs: bool = typer.Option(False, "--json", help="Emit logs as JSON lines."),
) -> None:
    """haravan-elt — Haravan Omni API → PostgreSQL → dbt star schema."""
    level = "DEBUG" if verbose else "WARNING" if quiet else "INFO"
    setup_logging(json_output=json_logs, level=level)


# ---------------------------------------------------------------------- init


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


# ---------------------------------------------------------------------- extract


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
    since_dt = _parse_iso_arg("since", since, shop_tz)
    until_dt = _parse_iso_arg("until", until, shop_tz)

    with Pipeline(settings) as pipe:
        try:
            rows = pipe.extract_one(
                domain, mode=mode, since=since_dt, until=until_dt, dry_run=dry_run
            )
            console.print(f"[green]OK[/green] {domain}: {rows} rows, dry_run={dry_run}")
        except Exception as exc:
            typer.echo(f"FAIL {domain}: {exc}", err=True)
            raise typer.Exit(1) from exc


# ---------------------------------------------------------------------- transform / test


@app.command()
def transform(
    select: str | None = typer.Option(None, help="dbt --select expression"),
) -> None:
    """Run `dbt run` (transform layer; tests are separate verb)."""
    settings = load_settings()
    with Pipeline(settings) as pipe:
        result = pipe.transform(select=select)
    if not result["success"]:
        typer.echo(f"dbt run failed: {result['exception']}", err=True)
        raise typer.Exit(1)
    console.print("[green]✓[/green] dbt run OK")


@app.command(name="test")
def dbt_test_cmd(
    select: str | None = typer.Option(None, help="dbt --select expression"),
) -> None:
    """Run `dbt test`."""
    settings = load_settings()
    with Pipeline(settings) as pipe:
        result = pipe.dbt_test(select=select)
    if not result["success"]:
        typer.echo(f"dbt test failed: {result['exception']}", err=True)
        raise typer.Exit(1)
    console.print("[green]✓[/green] dbt test OK")


# ---------------------------------------------------------------------- run-all


@app.command(name="run-all")
def run_all(
    mode: str = typer.Option("incremental", help="full | incremental"),
    since: str | None = None,
    until: str | None = None,
    dry_run: bool = typer.Option(False, "--dry-run"),
    no_notify: bool = typer.Option(False, "--no-notify"),
    triggered_by: str = typer.Option("manual"),
) -> None:
    """Full pipeline: extract every domain → `dbt build` → Telegram summary."""
    if mode not in {"full", "incremental"}:
        typer.echo(f"invalid --mode: {mode!r}", err=True)
        raise typer.Exit(2)

    settings = load_settings()
    shop_tz = ZoneInfo(settings.timezone)
    since_dt = _parse_iso_arg("since", since, shop_tz)
    until_dt = _parse_iso_arg("until", until, shop_tz)

    telegram = (
        None
        if no_notify
        else TelegramClient(
            settings.telegram.bot_token.get_secret_value(),
            settings.telegram.chat_id,
        )
    )
    with Pipeline(settings, telegram=telegram, triggered_by=triggered_by) as pipe:
        code = pipe.run_all(
            mode=mode,
            since=since_dt,
            until=until_dt,
            dry_run=dry_run,
            no_notify=no_notify,
        )
    raise typer.Exit(code)


# ---------------------------------------------------------------------- status / notify / validate


@app.command()
def status() -> None:
    """Print high watermark + last run for each known domain."""
    settings = load_settings()
    state = StateManager(settings.database.database_url.get_secret_value())
    rows = state.list_status()
    render_status_table(rows, console=console)


@app.command()
def notify(message: str = typer.Argument(..., help="Free-form Markdown message.")) -> None:
    """Send a one-off message via Telegram (debug helper)."""
    settings = load_settings()
    client = TelegramClient(
        settings.telegram.bot_token.get_secret_value(),
        settings.telegram.chat_id,
    )
    if not client.enabled:
        typer.echo("telegram disabled (token or chat_id missing)", err=True)
        raise typer.Exit(2)
    ok = client.send(message)
    if not ok:
        raise typer.Exit(1)


@app.command()
def validate(
    domain: str = typer.Argument(..., help="Domain to validate (raw row count vs API)"),
    tolerance: float = typer.Option(
        DEFAULT_TOLERANCE,
        help="Relative drift tolerance (default 0.001 = 0.1%).",
    ),
) -> None:
    """Compare raw row count vs Haravan API `/count.json`. Exit 0 if within
    tolerance, 1 on mismatch, 2 on bad input."""
    if domain not in DOMAIN_RAW_TABLE:
        typer.echo(
            f"unknown domain: {domain}; known: {sorted(DOMAIN_RAW_TABLE)}",
            err=True,
        )
        raise typer.Exit(2)
    settings = load_settings()
    dsn = settings.database.database_url.get_secret_value()
    with HaravanClient(settings) as client:
        result = run_validate(domain, client, dsn, tolerance=tolerance)
    status = "[green]OK[/green]" if result.ok else "[red]MISMATCH[/red]"
    console.print(
        f"{status} {domain}: api={result.api_count} db={result.db_count} "
        f"ratio={result.ratio:.4f} (tolerance={tolerance})"
    )
    if not result.ok:
        raise typer.Exit(1)


# ---------------------------------------------------------------------- helpers


def _parse_iso_arg(name: str, value: str | None, default_tz: ZoneInfo) -> datetime | None:
    """Parse an ISO datetime CLI arg. Naive values coerced to `default_tz`
    (research §4: Haravan filters use shop tz). Bad input → exit 2."""
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


def _read_packaged(relpath: str) -> str:
    return resources.files("haravan_elt").joinpath(relpath).read_text(encoding="utf-8")


# Avoid unused-import lint when Settings isn't referenced directly.
_ = Settings


if __name__ == "__main__":
    app()
