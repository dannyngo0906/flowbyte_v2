# Phase-04 Review — Multi-Domain Extractors + Template Hierarchy

**Plan:** `plans/260426-1340-haravan-elt/phase-04-multi-domain-extractors.md`
**Scope:** P0 customers/products/locations + `BaseExtractor` → `PaginatedListExtractor` refactor
**Verification:** ruff/mypy/54 tests green, coverage ≥98% on new modules

## Pros

- **Template-vs-copypaste call: correct.** Plan's "clone orders" would have produced 3× ~30 LOC near-identical pagination loops. Implementation collapses them to declarations (customers 12 LOC, products 16 LOC, orders 18 LOC) plus a 50 LOC template. Net diff: -50 LOC vs plan, single point of change for pagination edge cases. Future P1/P2/M7 (inventory/collections/discounts/promotions/events) are also offset-paginated `/com/<x>.json` endpoints — each will be a 5-line declaration. This is **not** premature abstraction: 4 concrete paginated subclasses already exist; rule-of-three satisfied.
- Clear separation: `LocationsExtractor` extends `BaseExtractor` directly (not `PaginatedListExtractor`) — opt-out is explicit, not a flag-driven escape hatch.
- `EXTRACTORS` is `dict[str, type[BaseExtractor]]` — type-checked, registry surface trivial.
- DDL via `CREATE TABLE IF NOT EXISTS` + `CREATE INDEX IF NOT EXISTS` — fully idempotent (`init` re-run safe).
- Tests use `respx` for HTTP mocking + `MagicMock` for loader/state; no live DB needed for unit suite. Parametrization across customers+products is DRY without losing readability (separate file from orders to keep `EXTRA_PARAMS` test isolated).
- `_helpers.parse_haravan_timestamp` extracted properly — single timestamp parsing rule, not duplicated across 4 files.

## Issues

### Critical
None.

### High
- **`base.py:113` — `EXTRA_PARAMS` class-level mutable dict.** `EXTRA_PARAMS: dict[str, Any] = {}` on `PaginatedListExtractor`, overridden as `{"status": "any"}` on `OrdersExtractor`. The `iter_pages` body uses `**self.EXTRA_PARAMS` (shallow-copy via splat into a new dict), so the dict itself is **not** mutated per-call — safe today. **But:** any future subclass that does `self.EXTRA_PARAMS["foo"] = bar` (e.g., dynamic per-call override) would mutate the class attribute and leak across siblings/instances. No test covers this footgun. **Recommendation:** either (a) freeze via `MappingProxyType` or `@property` returning a fresh dict, or (b) add a one-line class-attr docstring "Treat as immutable; do not mutate in-place — use `params.update()` on the local copy in `iter_pages` instead."

- **`cli.py:101` — locations watermark contract.** `if max_ts is not None and not dry_run: state.update_watermark(...)` runs unconditionally of `supports_incremental`. `LocationsExtractor.to_raw_row` falls back to `datetime.now(tz=UTC)` when timestamps absent. Result: `meta.sync_state` for `locations` advances every run with a fresh `now()`, even though it's never read (`idempotent_load:81` gates `state.get_watermark` on `supports_incremental`). Functionally harmless (write-only), but clutters `meta.sync_state`, confuses operators reading the table, and uses a non-deterministic timestamp where a stable max-API-`updated_at` would be more meaningful. **Recommendation:** in `cli.py extract`, gate watermark write: `if max_ts is not None and not dry_run and extractor.supports_incremental: state.update_watermark(...)`. One-line fix; honors the design contract that `supports_incremental=False` ⇒ no watermark interaction.

### Medium
- **`locations.py:34` — silent ignore of `since/until`.** `del since, until` swallows kwargs; no log. If a caller accidentally passes a watermark expecting incremental behavior, the bug is invisible. **Recommendation:** `if since is not None or until is not None: logger.debug("locations_filter_ignored", since=since, until=until)`. Matches plan §Risk Q1 + the test `test_ignores_since_until` which only asserts URL behavior.
- **`registry.py` eager imports.** Module-level imports fail-fast at CLI startup if any extractor has an import error. Acceptable for current scale (4 extractors, all hand-written). When phase-09/11 lands ~7 more, a single bad import takes the whole CLI down — even for unrelated commands like `init`. **Recommendation:** acceptable now; revisit in phase-07 when `--all` is wired and registry grows. Note for future: `importlib.import_module` lazy loader keyed on domain name.
- **`conftest.py:82-86` — hardcoded TRUNCATE list.** Adding 7+ tables in phase-09/11 means every PR touches conftest. **Recommendation:** switch to `TRUNCATE ALL TABLES IN SCHEMA raw, meta` via DO block, or query `pg_tables WHERE schemaname IN ('raw','meta')` and build TRUNCATE dynamically. Defer to phase-09 when it becomes painful — current list is fine for 4 tables.
- **`base.py:115` — `page_limit: int = 250` default vs Haravan's 50-cap.** Plan §step-2 used `limit=50`. Research §2 says Haravan caps page size; if `250` exceeds the cap, the API silently caps and the `len(items) < self._limit` short-page heuristic becomes "always true" → loop terminates after page 1, missing data. **Verify:** is 250 actually accepted? If cap is lower, default of 250 will under-fetch. Test `test_paginates_until_short_page` uses `page_limit=2` so doesn't exercise the real default. **Recommendation:** confirm against live shop in phase-10 hardening; meanwhile lower default to 50 (matches plan + research) or document the assumption.

### Low
- **`base.py:115` — `page_limit` is `__init__` kwarg, not class attribute.** Plan said "subclasses declare page_limit." Mixed style: `PATH`/`RESPONSE_KEY`/`EXTRA_PARAMS` are class attrs; `page_limit` is constructor-only. Tests pass it via `cls(..., page_limit=2)`. Minor inconsistency. Optional: promote to `PAGE_LIMIT: int = 50` class attr matching the others.
- **`DOMAIN_ORDER` semantically right** (locations dim → customers/products dims → orders fact). But it's not enforced anywhere yet — phase-07's `--all` should iterate `DOMAIN_ORDER`, not `EXTRACTORS.keys()`. Note for phase-07 reviewer.
- **`test_p0_extractors.py` parametrization:** orders correctly excluded (has `EXTRA_PARAMS={"status":"any"}` to assert separately). Good call. Could add one shared `iter_pages` URL-shape test that *includes* orders and asserts `status=any` is present — would catch a regression where someone refactors `EXTRA_PARAMS` handling. Optional.

## Recommendations (priority order)

1. **(High, 1-line fix)** Gate `state.update_watermark` in `cli.py:101` on `extractor.supports_incremental`. Prevents `meta.sync_state.locations` polluting with `now()` every run.
2. **(High, doc/guard)** Document or freeze `EXTRA_PARAMS` immutability in `base.py:113`.
3. **(Medium)** Add `logger.debug("locations_filter_ignored", ...)` in `locations.py:34` for observability.
4. **(Medium)** Verify `page_limit=250` default against Haravan's actual cap; lower to 50 or document.
5. **(Low/defer)** Switch `conftest.pg_clean` to dynamic TRUNCATE when table count > 6.

## Verdict

Template hierarchy is the **right** call — already paying for itself with 5–7 LOC per new domain. Phase-04 ships a clean foundation for P1/P2/M7. Two High-priority fixes are small (one is a literal one-liner) and should land before phase-05 starts using these extractors against integration data; otherwise `meta.sync_state` will accumulate noise that's annoying to clean up later.

## Unresolved Questions

- Q1: Is Haravan's actual page-size cap 50 or 250? `page_limit=250` default depends on this; impacts pagination correctness.
- Q2: Should `meta.sync_state` track non-incremental domains at all (for "last successful run timestamp") or only true watermarks? Plan implies the latter; current code does the former by accident.

**Status:** DONE_WITH_CONCERNS
**Summary:** Template refactor is sound and pays its way; one High-priority watermark contract leak (`cli.py:101` writes `now()` to `meta.sync_state.locations` every run) plus a documented-or-frozen `EXTRA_PARAMS` mutability guard should land before phase-05 consumes these tables.
**Verdict:** approve (with 2 High fixes recommended pre-phase-05; both <5 LOC)
**Critical issues count:** 0
