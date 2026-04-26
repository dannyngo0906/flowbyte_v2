# Code Review — Phase-03: Orders Extract + Load

**Scope:** 5 new + 5 modified files. ~430 LOC src + ~200 LOC tests. Plan: `phase-03-orders-extract-load.md`.
**Verification observed:** ruff/format clean, mypy --strict clean, 43/43 tests pass, coverage orders=100% loader=97% state=100%.

---

## Pros

- Idempotency contract correct: `idempotent_load` returns `(rows, max_updated)`, watermark advanced by CLI in separate tx AFTER `state.end_run` would normally fire — matches research §5 + plan line 23. Watermark NOT touched in-loop.
- `WHERE EXCLUDED.updated_at >= {schema}.{tbl}.updated_at` SQL shape is valid Postgres; `sql.Identifier` composition makes injection-safe; verified by `test_upsert_skips_older_payload`.
- `GREATEST(meta.sync_state.last_updated_at, EXCLUDED.last_updated_at)` table-qualified ref inside `ON CONFLICT DO UPDATE` is correct (plain column would be ambiguous in some dialects). Regression test passes.
- Pagination EOF: dual-guard (`not items` OR `len < limit`) — the "exactly limit on last page" case correctly takes ONE extra empty roundtrip, then bails (acceptable; alternative would need cursor/total).
- Mypy strict cleanliness: TYPE_CHECKING-guarded imports in `base.py` avoid circular deps; `Iterator[list[dict[str, Any]]]` annotation propagated.
- SQL injection surface: zero string concat. `_split_table` validates qualified-only.
- Connect-per-call lifecycle for StateManager is fine for MVP (a few calls per run); no leaked connections (psycopg3 `with` block auto-closes).
- `error_message[:1000]` truncation defends against multi-MB tracebacks bloating run_log (state.py:76).
- `EXTRACTOR_REGISTRY` dict in cli.py is fine for ≤5 domains; YAGNI to abstract.
- CLI uses `raise typer.Exit(1) from exc` preserving traceback chain; `from exc` is correct usage.

---

## Issues

### Critical
None.

### High
None.

### Medium

**M1. `Settings()` vs `load_settings()` inconsistency** — `cli.py:70` calls `Settings()` directly inside `extract`, but `init` (cli.py:44) uses `load_settings()`. If `load_settings()` has any normalization/file-resolution logic, `extract` skips it. Confirm both code paths produce equivalent settings or unify.

**M2. `client.close()` not guaranteed if construction fails** — `cli.py:75-79` constructs `client`, `loader`, `state` after `state.start_run` is NOT yet called, but a `HaravanClient(settings)` failure raises before `try:` block. Inverse problem: `state.start_run(run_id, ...)` at line 81 happens BEFORE `try:` — if `start_run` raises (Postgres down), no `run_log` row, OK. But the `finally: client.close()` (line 99) only runs after `try:` is entered — if `start_run` raises, `client` exists but isn't closed. Wrap construction inside `try`/`finally` or use context managers.

**M3. `since`/`until` parsed via `datetime.fromisoformat` without tz coercion** — `cli.py:85-86` accepts naive ISO strings (e.g. `"2026-04-25"`). These become naive `datetime`, then passed to `iter_pages` which calls `.isoformat()` for `updated_at_min` — Haravan API likely expects timezone-aware. Plan §"Key Insights" says "ISO 8601 with explicit offset". No coercion to UTC/+07:00. Risk: silent off-by-7-hours boundary. Add `if dt.tzinfo is None: dt = dt.replace(tzinfo=...)` or document operator must pass `+07:00`.

### Low

**L1. Microsecond timestamp coverage** — `_parse_haravan_timestamp` (orders.py:19-21) handles `Z` suffix, but no test for `"2026-04-25T10:00:00.123456Z"`. Python 3.11+ `fromisoformat` does support fractional seconds, so fix is "no fix needed" — but a regression test would lock the contract. (Plan risk-table flagged this; test missing.)

**L2. Test isolation: pg_clean truncates 3 tables but no others** — conftest.py:83 hard-codes `raw.haravan_orders, meta.sync_state, meta.run_log`. When phase-04 adds `raw.haravan_customers` etc., this list must grow or tests pollute each other. Consider `TRUNCATE ALL TABLES IN SCHEMA raw` pattern via DO block, or accept and update per-phase.

**L3. `cur.executemany` rowcount discarded; `total += len(chunk)` is correct but** — counts attempted rows, not actually-upserted rows. After the `WHERE EXCLUDED.updated_at >= …` guard, "rejected as older" rows are still counted in `inserted=5`. Test `test_upsert_inserts_new_rows` asserts `inserted == 5` matching submitted, so contract is "submitted count". Worth a docstring note: "returns count of submitted rows, not rows actually written." (loader.py:39 docstring is close but could be sharper.)

**L4. `triggered_by` always defaults to `"manual"`** — cli.py:81 omits the kwarg. Cron invocation will record `manual` in run_log. Phase-07 will likely fix; flag for that phase.

**L5. `since`/`until` ValueError on bad ISO leaks raw exception** — cli.py:85 — `datetime.fromisoformat("xx")` raises `ValueError`, caught by the broad `except Exception`, which then writes the error to run_log AND echoes "FAIL orders: ...". But because `state.start_run` already inserted a `running` row, this is fine. However exit code is 1 (generic fail), per plan request exit 2 reserved for invalid args. Pre-validate `since`/`until` BEFORE `state.start_run` and exit 2 to keep cron semantics.

**L6. `loader.upsert_batch.call_args` typed as Any in tests** — orders extractor tests (test_orders_extractor.py:97) — `args, kwargs = loader.upsert_batch.call_args` unpacks `Any`, then `args[0] == ...`. Mypy strict allows this because MagicMock returns Any. Acceptable; not worth tightening.

**L7. `EXTRACTOR_REGISTRY` typing** — cli.py:30 — implicitly `dict[str, type[OrdersExtractor]]`. When phase-04 adds 4 more, mypy will widen to `dict[str, type[BaseExtractor]]`. Will need explicit annotation then. Not a phase-03 issue.

---

## Recommendations

1. **M2 + L5 (combined fix):** Refactor `extract()` cli to validate args + construct deps INSIDE try/finally:
   ```python
   try:
       _since = datetime.fromisoformat(since) if since else None
   except ValueError:
       raise typer.Exit(2)
   try:
       client = HaravanClient(...); ...
       state.start_run(...)
       try: ...
       except Exception as exc: state.end_run(...); raise typer.Exit(1) from exc
   finally:
       if client: client.close()
   ```
2. **M3:** Add tz default. If naive, assume Asia/Ho_Chi_Minh (matches PRD shop tz). One-liner in cli.py.
3. **M1:** Replace `Settings()` with `load_settings()` in `extract` for consistency.
4. **L1:** Add unit test: `_parse_haravan_timestamp("2026-04-25T10:00:00.123456Z")` → assert microseconds preserved.
5. **L3:** Tighten docstring on `upsert_batch` return to clarify "rows submitted, may differ from rows written when guard rejects stale".
6. **L2:** Open follow-up for phase-04 to extend `pg_clean` truncate list (or switch to per-schema cascade).

---

## Verdict

Plan acceptance criteria all met (idempotency, GREATEST regression, ON CONFLICT guard, separate-tx watermark, run_log lifecycle, ≥80% coverage). Code is clean, mypy-strict-safe, and well-tested. The medium issues are correctness-adjacent (tz handling, error path hygiene) but do not block phase-03 acceptance — they should be tracked as follow-ups for phase-04 / phase-07 hardening.

**Status:** DONE_WITH_CONCERNS
**Summary:** Phase-03 implementation matches plan; SQL/idempotency/watermark guards correct and tested. Three medium issues (CLI error-path lifecycle, tz coercion on `since`/`until`, Settings vs load_settings drift) should be tracked but don't block.
**Verdict:** approve
**Critical issues count:** 0

---

## Unresolved Questions

- Q1 (M3): Is naive ISO date input expected to be Asia/Ho_Chi_Minh or UTC? Plan says shop tz; cli silent.
- Q2 (M1): Why does `init` use `load_settings()` but `extract` uses `Settings()`? Was this intentional?
- Q3 (L4): Is `triggered_by="cron"` populated by an env flag or wrapper script? Phase-07 to clarify.
