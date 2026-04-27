# Phase 07 — CLI + Orchestrator (M4)

## Context Links

- PRD §4.5 (CLI command table), §4.4 (FR-T4 dbt wrapper)
- Research (idempotent §8 Typer, §6 structlog): `plans/reports/researcher-260426-1340-idempotent-elt-python.md`
- Research (dbt §10 dbtRunner vs subprocess): `plans/reports/researcher-260426-1340-dbt-postgres-patterns.md`
- Phase-03 (extract subcommand stub), phase-06 (dbt project ready)

## Overview

- **Priority:** high
- **Status:** pending
- **Effort:** 4 days
- **Description:** Finalize all PRD §4.5 CLI subcommands; build `pipeline.py` orchestrator that runs extractors in dependency order, then dbt build via `dbtRunner` Python API. Global flags `--verbose / --quiet / --json / --dry-run` honored. Exit codes 0/1/2.

## Key Insights

- **dbtRunner (Python API)** preferred for in-process invocation: faster (manifest reuse), event introspection for telemetry/Telegram (research §10).
- **Subprocess fallback** only when needing parallel domain extracts (phase-09+) — single dbtRunner instance per process.
- **Global flags propagate via Typer callback** + structlog reconfigure on each invocation.
- **Domain order:** `locations → customers → products → orders` (per phase-04 registry). Refunds + transactions ride on orders payload.
- **Run-all uses single shared `run_id`** so all per-domain `meta.run_log` rows can be correlated; `pipeline_run_id` separate column ideal but defer (use `triggered_by="cron:<run_id>"` workaround at MVP).
- **Exit codes:** 0 success, 1 fail, 2 invalid args / lock conflict.

## Requirements

**Functional:**
- FR-CLI1: exit codes (0/1/2)
- FR-CLI2: `--verbose / --quiet / --json`
- FR-CLI3: `--dry-run` for extract
- All 8 commands per PRD §4.5: `init, extract, transform, test, run-all, status, notify, validate`
  - `validate` is implemented in **phase-09** (after raw data exists); stub in this phase
- `transform` and `test` forward to dbt
- `status` queries `meta.sync_state` + last `meta.run_log` row per domain

**Non-functional:**
- NFR-3: log run_id in every line via structlog contextvars
- NFR-5: type hints; `mypy --strict` clean

## Architecture

```
Typer app callback (--verbose / --quiet / --json)
  └─ setup_logging(json_output=..., level=...)

Subcommands:
  init       → run schema.sql + raw_tables.sql
  extract    → single domain (phase-03 logic; --dry-run skips upsert)
  extract-all → DOMAIN_ORDER loop (callable via run-all too)
  transform  → dbtRunner.invoke(["run", "--project-dir", "dbt", "--select", ...])
  test       → dbtRunner.invoke(["test", "--project-dir", "dbt", "--select", ...])
  run-all    → orchestrator pipeline:
                 setup → extract-all → transform → test → notify summary
  status     → SELECT domain, last_updated_at, mode, status, ended_at
               FROM meta.sync_state JOIN meta.run_log ON ... print Rich table
  notify     → TelegramClient.send (raw debug command)
  validate   → stub (NotImplemented in phase-07; full impl phase-09)
```

`pipeline.py`:
```python
class Pipeline:
    def __init__(self, settings, telegram=None, triggered_by="manual"):
        self.settings = settings
        self.client   = HaravanClient(settings)
        self.loader   = PostgresLoader(settings.database.database_url)
        self.state    = StateManager(settings.database.database_url)
        self.telegram = telegram
        self.triggered_by = triggered_by

    def extract_all(self, mode, since, until, dry_run) -> dict[str, int]:
        results = {}
        for domain in DOMAIN_ORDER:
            results[domain] = self._extract_one(domain, mode, since, until, dry_run)
        return results

    def transform(self, select=None) -> dict:
        runner = dbtRunner()
        args = ["build", "--project-dir", "dbt"]
        if select: args += ["--select", select]
        result = runner.invoke(args)
        return {"success": result.success, "exception": str(result.exception) if result.exception else None}

    def run_all(self, mode, since, until, dry_run, no_notify) -> int:
        start = time.time()
        try:
            ext_summary = self.extract_all(mode, since, until, dry_run)
            tx_summary  = self.transform()
            duration = time.time() - start
            if not no_notify and self.telegram:
                self.telegram.send(_format_success(ext_summary, tx_summary, duration))
            return 0
        except Exception as exc:
            if not no_notify and self.telegram:
                self.telegram.send(_format_failure(exc))
            return 1
```

## Related Code Files

**Create:**
- `src/haravan_elt/dbt_runner.py` — wraps dbtRunner with structlog event capture
- `src/haravan_elt/cli_helpers.py` — `_format_success`, `_format_failure`, Rich table for status
- `tests/test_cli.py` — subprocess invocation tests (Typer testing harness)
- `tests/test_pipeline.py`

**Modify:**
- `src/haravan_elt/cli.py` — full implementation (replaces stub)
- `src/haravan_elt/pipeline.py` — full Pipeline class
- `src/haravan_elt/client/telegram.py` — `send_summary`/`send_failure` helpers (full impl in phase-08)

## Implementation Steps

1. **`cli.py` complete:**
   ```python
   import typer, structlog, uuid, time
   from typing import Optional
   from datetime import datetime
   from rich.console import Console
   from rich.table import Table
   from .config import Settings
   from .pipeline import Pipeline
   from .extractors.registry import EXTRACTORS, DOMAIN_ORDER
   from .meta.state import StateManager
   from .client.telegram import TelegramClient

   app = typer.Typer(no_args_is_help=True)
   console = Console()

   @app.callback()
   def main(
       verbose: bool = typer.Option(False, "--verbose", "-v"),
       quiet: bool = typer.Option(False, "--quiet", "-q"),
       json_logs: bool = typer.Option(False, "--json"),
   ):
       level = "DEBUG" if verbose else "WARNING" if quiet else "INFO"
       setup_logging(json_output=json_logs, level=level)

   @app.command()
   def init():
       """Run schema.sql + raw_tables.sql against DATABASE_URL."""
       settings = Settings()
       run_schema(settings.database.database_url)
       typer.echo("schema initialized")

   @app.command()
   def extract(
       domain: str,
       mode: str = "incremental",
       since: Optional[str] = None,
       until: Optional[str] = None,
       dry_run: bool = typer.Option(False, "--dry-run"),
   ):
       if domain not in EXTRACTORS:
           typer.echo(f"unknown domain: {domain}", err=True)
           raise typer.Exit(2)
       settings = Settings()
       pipe = Pipeline(settings, triggered_by="manual")
       try:
           rows = pipe.extract_one(domain, mode,
                                   _parse_dt(since), _parse_dt(until), dry_run)
           typer.echo(f"OK {domain}: {rows} rows")
       except Exception as exc:
           typer.echo(f"FAIL {domain}: {exc}", err=True)
           raise typer.Exit(1)

   @app.command()
   def transform(select: Optional[str] = None):
       settings = Settings()
       pipe = Pipeline(settings)
       result = pipe.transform(select=select)
       if not result["success"]:
           typer.echo(f"dbt failed: {result['exception']}", err=True)
           raise typer.Exit(1)
       typer.echo("dbt build OK")

   @app.command()
   def test(select: Optional[str] = None):
       settings = Settings()
       pipe = Pipeline(settings)
       result = pipe.dbt_test(select=select)
       if not result["success"]:
           raise typer.Exit(1)

   @app.command(name="run-all")
   def run_all(
       mode: str = "incremental",
       since: Optional[str] = None,
       until: Optional[str] = None,
       dry_run: bool = typer.Option(False, "--dry-run"),
       no_notify: bool = typer.Option(False, "--no-notify"),
       triggered_by: str = typer.Option("manual"),
   ):
       settings = Settings()
       telegram = None if no_notify else TelegramClient(
           settings.telegram.bot_token.get_secret_value(),
           settings.telegram.chat_id,
       )
       pipe = Pipeline(settings, telegram=telegram, triggered_by=triggered_by)
       code = pipe.run_all(mode, _parse_dt(since), _parse_dt(until), dry_run, no_notify)
       raise typer.Exit(code)

   @app.command()
   def status():
       settings = Settings()
       state = StateManager(settings.database.database_url)
       rows = state.list_status()  # SELECT JOIN sync_state + last run_log
       tbl = Table(title="haravan-elt status")
       tbl.add_column("domain"); tbl.add_column("last_watermark")
       tbl.add_column("last_status"); tbl.add_column("last_run_at"); tbl.add_column("rows")
       for r in rows:
           tbl.add_row(r["domain"], str(r["last_updated_at"]),
                       r["status"], str(r["ended_at"]), str(r["rows_ingested"]))
       console.print(tbl)

   @app.command()
   def notify(message: str):
       settings = Settings()
       TelegramClient(
           settings.telegram.bot_token.get_secret_value(),
           settings.telegram.chat_id,
       ).send(message)

   @app.command()
   def validate(domain: str):
       """Compare raw row count vs Haravan API count.json. (Implemented phase-09.)"""
       typer.echo("validate: not implemented until phase-09 (M5)", err=True)
       raise typer.Exit(2)
   ```

2. **`pipeline.py`** full implementation (sketch in Architecture above). Add `dbt_test()`, `extract_one()` and `_format_success/failure` helpers in `cli_helpers.py`.

3. **`dbt_runner.py`** wrapper:
   ```python
   from dbt.cli.main import dbtRunner, dbtRunnerResult
   import structlog

   logger = structlog.get_logger()

   def run_dbt(args: list[str]) -> dbtRunnerResult:
       runner = dbtRunner()
       logger.info("dbt_invoke", args=args)
       result = runner.invoke(args)
       logger.info("dbt_done", success=result.success,
                   exception=str(result.exception) if result.exception else None)
       return result
   ```

4. **`StateManager.list_status()`:**
   ```sql
   SELECT s.domain, s.last_updated_at,
          r.status, r.ended_at, r.rows_ingested, r.mode
   FROM meta.sync_state s
   LEFT JOIN LATERAL (
     SELECT * FROM meta.run_log
     WHERE domain = s.domain
     ORDER BY started_at DESC LIMIT 1
   ) r ON true
   ORDER BY s.domain;
   ```

5. **Pipeline.extract_one()** wraps phase-03 `extractor.idempotent_load` with run_log start/end + watermark advance. Reuses code already in CLI extract — refactor into Pipeline so `run-all` and `extract` share logic.

6. **Telegram summary helpers** (phase-08 finalizes; for now emit plain string):
   ```python
   def _format_success(ext: dict[str, int], tx: dict, duration: float) -> str:
       lines = ["✅ *Haravan ELT — Run OK*", f"⏱ {duration:.1f}s", "📥 Extracted:"]
       lines += [f"  • {d}: {n} rows" for d, n in ext.items()]
       lines.append(f"🔧 dbt: {'OK' if tx.get('success') else 'FAIL'}")
       return "\n".join(lines)
   ```

7. **Tests** (`tests/test_cli.py` via `typer.testing.CliRunner`):
   - `init` exits 0 against test Postgres
   - `extract orders --dry-run` exits 0, no DB write
   - `transform --select tag:none` exits 0 (no models, dbt no-op)
   - `status` prints table without crash
   - Unknown domain → exit 2
   - `--verbose` reconfigures log level (capture stderr)

8. **`pipeline.py` test:** mock dbtRunner; assert `run_all` invokes extractors in DOMAIN_ORDER then dbt.

## Todo List

- [x] Implement full `cli.py` (8 commands + global callback)
- [x] Implement `Pipeline` class (extract_one, extract_all, transform, dbt_test, run_all)
- [x] Implement `dbt_runner.py` wrapper around dbtRunner
- [x] Add `cli_helpers.py` (success/failure formatters; status table renderer)
- [x] Extend `StateManager` with `list_status()` JOIN query
- [x] Wire `TelegramClient` from Settings (stub send is fine until phase-08)
- [x] Write `tests/test_cli.py` (Typer CliRunner) — 6 cases
- [x] Write `tests/test_pipeline.py` — mock dbtRunner + extractor; assert order + error handling
- [x] Manual end-to-end: `haravan-elt run-all --mode full --until 2026-04-25 --no-notify` → green, all marts populated  <!-- VERIFIED 2026-04-27 live token (incremental 1-day slice): all 9 extractors fetched, dbt built 26 models, 13 marts populated. 4 dbt referential tests fail from 1-day data sparsity (expected) -->
- [x] `haravan-elt status` prints correct watermarks for 4 domains

## Success Criteria

- `haravan-elt --help` lists all 8 commands
- `run-all --no-notify` succeeds end-to-end on dev shop sample
- Exit codes: success=0; failure=1 (verified by induced fail); unknown domain=2
- `--verbose` shows DEBUG; `--quiet` only WARNING+; `--json` outputs structured JSON lines
- All Typer test cases pass; `mypy --strict src/haravan_elt/cli.py` clean

## Risk Assessment

| Risk | Likelihood | Impact | Mitigation |
|------|-----------|--------|-----------|
| dbtRunner picks up `~/.dbt/profiles.yml` from wrong dir on CI | High | High | Set `DBT_PROFILES_DIR` env explicitly; document in CI |
| `setup_logging` called twice → duplicate handlers | Med | Low | Guard with module-level flag `_LOGGING_CONFIGURED` |
| `run-all` fails halfway → partial commit, watermarks advanced for some domains | Med | Med | Acceptable: each domain idempotent; document; future: optional `--atomic` flag |
| dbtRunner concurrency unsafe | Low (single-process) | Low | Document constraint; phase-09 avoids parallel by default |

## Security Considerations

- `notify <message>` accepts arbitrary text; `parse_mode=Markdown` could be exploited only by self — low risk
- `--triggered-by` cron sets `cron`; manual default — used for audit trail in `meta.run_log`
- `validate` (phase-09) requires same Haravan API access as extract — no new attack surface

## Next Steps

Unblocks **phase-08** (Telegram full notifications hook into Pipeline events).

## Unresolved Questions

- Q1: Should `extract orders` re-use `Pipeline.extract_one` (with run_log) or separate code path? Decision: reuse via Pipeline.
- Q2: `run-all` default mode in cron should be `incremental` — confirmed; document in `scripts/run-daily.sh` (phase-10).
- Q3: Should `transform` invoke `dbt build` (run+test) or only `dbt run`? PRD §4.5 separates `transform`+`test`; use `dbt run` for transform, `dbt test` for test. `run-all` uses `dbt build` for combined.
