# Phase-07 Review — CLI + Pipeline + dbt_runner

**Scope:** `src/haravan_elt/cli.py`, `pipeline.py`, `dbt_runner.py`, `cli_helpers.py`, `meta/state.py::list_status`, `tests/test_cli.py`, `tests/test_pipeline.py`. ~600 LOC. Verification (74/74 pass, mypy strict, ruff clean) re-confirmed by spot-check.

## Pros

- Clean separation: CLI = arg parsing + exit codes; `Pipeline` = orchestration; `dbt_runner` = vendor wrapper. Single `Pipeline` class reused by `extract` and `run-all` (plan Q1 honored).
- Context-manager `Pipeline` cleanly closes Haravan client. `extract_one` bookkeeping (start_run / update_watermark / end_run with truncated error) is sound and idempotent.
- `list_status` LATERAL join is correct: 1 row per known domain, NULL-safe via `LEFT JOIN`, deterministic `ORDER BY s.domain`.
- `transform` vs `dbt_test` vs `dbt_build` mapping resolves plan Q3 cleanly: `transform`→`dbt run`, `test`→`dbt test`, `run-all`→`dbt build`. Avoids running tests twice.
- Exit-code contract uniformly enforced (0/1/2). All 11 CLI tests + 8 pipeline tests assert behavior, not implementation.
- `_maybe_notify` short-circuit on `enabled` is the right pattern — tests can swap `MagicMock` cleanly.
- `format_run_failure` truncates at 1000 chars (matches `state.end_run` truncation) — no risk of Telegram 4096-char limit blowup.

## Issues

### Critical
None.

### High
- **`pipeline.py:147` — `except Exception` swallows `dbt_build` success-but-`success=False` path correctly via re-raised `RuntimeError` at :143, BUT the `_maybe_notify` failure call on **dbt failure** also runs, which is correct. However if `format_run_failure` itself raises (e.g. Markdown encoding issue on weird exc text), that propagates out of `run_all` and bypasses exit-code contract. Wrap `_maybe_notify` in defensive try/except (Telegram should never tank exit code). Belt-and-suspenders for FR-N4.
- **`dbt_runner.py:38` — `os.environ.setdefault("DBT_PROFILES_DIR", pfdir)`** is a one-shot global side effect. First `run_dbt` call wins forever for the process. If a user sets `DBT_PROFILES_DIR=/tmp/wrong` env then calls `run-all`, the explicit `--profiles-dir` flag at :36 will be used by dbt CLI parser, BUT any **jinja `env_var('DBT_PROFILES_DIR')`** macro will pick up the stale env. Either (a) drop the `setdefault` since `--profiles-dir` flag is authoritative for dbt, or (b) use unconditional set + restore via `try/finally` to avoid mutating the parent process env. Comment claims jinja motivation but no jinja in current `dbt/` references it (verify via grep).

### Medium
- **`pipeline.py:74` — `bind_contextvars` never cleared.** `extract_all` calls `extract_one` 4× sequentially; each call binds `run_id`/`domain`/`mode` but never `clear_contextvars`. Final domain's context leaks into post-loop `dbt_build` + `_maybe_notify` log lines (they'll show `domain=orders mode=incremental` though context unrelated). Use `with structlog.contextvars.bound_contextvars(...)` (context-manager form) inside `extract_one` so binding is scoped. Plan §unresolved Q4 explicitly notes ContextVar inheritance is phase-10 hardening, but **the leak within a single CLI invocation** is in scope and trivial to fix here.
- **`cli.py:181` — `notify` constructs `TelegramClient` **before** checking `enabled`.** Cheap (no network), but inconsistent with `run-all` which also unconditionally constructs. Acceptable per plan-deviation #3 (cleaner than juggling None). Note: `settings.telegram.bot_token.get_secret_value()` may raise if `bot_token` is None on the Pydantic model — confirm `TELEGRAM_BOT_TOKEN=""` env yields empty SecretStr, not None. Test `test_notify_disabled_returns_exit_2` covers empty-string case; nothing covers truly-missing env. Add a test or document that empty string is the contract.
- **`cli.py:130-131` — `since` and `until` lack `typer.Option(None, ...)`** wrapper, so they're positional defaults. Works (Typer treats annotated `str | None = None` as `--since` flag), but inconsistent with `extract` which uses explicit `typer.Option`. Cosmetic; consistency-only.
- **`pipeline.py:142` — Treating `dbt_build success=False` as exception** is correct for run-all summary, but loses the structured `dbt_summary` (args + exception) when reformatted into `RuntimeError`. Telegram failure message no longer shows which dbt invocation failed. Pass dbt_summary through to `format_run_failure` or include `dbt_summary` in `RuntimeError` args.

### Low
- **`cli.py:225` — `_ = Settings`** trailing line to silence unused-import lint is a smell. `Settings` is genuinely unused in cli.py (only `load_settings` is used). Remove the import + the alias rather than papering over.
- **`cli_helpers.py:54` — Markdown escaping.** Domain names are static (`locations / customers / products / orders`) so no injection risk today, but `format_run_success` interpolates them into a Markdown string with no escape. If domain set ever expands to user-controlled values, `*` / `_` / `` ` `` chars will break Markdown rendering. Document the assumption or add a TODO for phase-08.
- **`dbt_runner.py:17` — `DEFAULT_DBT_PROJECT_DIR` resolved at import time** via `__file__`. Brittle if package is installed via pip/wheel (file ends up in site-packages, dbt/ won't be a sibling). Works for editable installs (current dev mode). Acceptable for MVP; flag for phase-12 packaging.
- **`tests/test_pipeline.py:17` — `pipe` fixture has no teardown.** `Pipeline.__init__` constructs `HaravanClient` which opens an `httpx.Client`. Tests don't call `pipe.close()`. Not a leak in pytest process (GC at exit) but pytest might warn `ResourceWarning: unclosed httpx client`. Add `yield pipe; pipe.close()`.
- Plan-deviation #2 (status table columns): added `started_at` + `mode` is **strictly better** than plan example. Equivalent or better. No issue.

## Recommendations

1. **(High)** Wrap `_maybe_notify` calls in `pipeline.py:144,149` with try/except to fully honor FR-N4 (Telegram never breaks pipeline).
2. **(High)** Decide on `DBT_PROFILES_DIR` semantics: drop `setdefault` if `--profiles-dir` flag suffices; else use save/restore. Add a dbt_runner test verifying env is not leaked.
3. **(Medium)** Use `structlog.contextvars.bound_contextvars` (context-manager form) in `extract_one` so per-domain context doesn't bleed into subsequent log lines.
4. **(Medium)** Pipe `dbt_summary` into the failure-formatter so Telegram failure card shows which dbt args ran.
5. **(Low)** Drop the `_ = Settings` shim — actually remove the unused import.
6. **(Low)** Add `yield`/`close` to the `pipe` fixture in test_pipeline.py.

Phase-07 plan-deviations all judged **acceptable**: dbt build for run-all (Q3), enriched status columns, eager TelegramClient with `enabled` short-circuit, `_parse_iso_arg` co-located with CLI (only consumer; no need to extract).

Per review-focus item 6 (run-all watermark advancement): confirmed acceptable per plan §risks. Idempotent re-run is the documented recovery path.

Per item 7 (Telegram resource): `httpx.post` (function-level) creates+closes its own client per call — no leak.

## Verdict

Approve with minor fixes. None of the issues block landing — all are correctness-polishing. Suggest addressing High items (#1, #2) before phase-08 since phase-08 will exercise both code paths heavily.

## Unresolved Questions

- Does the dbt project (`dbt/profiles.yml`) actually use `env_var('DBT_PROFILES_DIR')` anywhere? If no, the `os.environ.setdefault` in `dbt_runner.py:38` is dead code and should be removed.
- Should `run-all` track a single `pipeline_run_id` correlating all 4 per-domain `run_log` rows? Plan §Key Insights mentions deferring via `triggered_by="cron:<run_id>"` — implementation didn't apply this workaround. Worth a follow-up phase-10 ticket.

**Status:** DONE
**Summary:** Phase-07 implementation matches plan intent with sound separation of concerns and full test coverage. Two High-severity polish items (Telegram exception isolation, DBT_PROFILES_DIR env mutation) and a context-var leak between domains warrant fixes; nothing blocks landing.
**Verdict:** approve
**Critical issues count:** 0
