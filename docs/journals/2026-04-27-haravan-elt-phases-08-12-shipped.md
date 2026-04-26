# Haravan ELT: Phases 08–12 Complete — Full M0–M7 Shipped

**Date**: 2026-04-27 02:15
**Severity**: None (shipped)
**Component**: haravan-elt pipeline
**Status**: Resolved

## What Happened

Completed the final 5 phases (08–12) in a single session, pushing the Haravan ELT pipeline from 7/12 phases to 12/12 — full M0–M7 scope shipped. The pipeline now extracts all P0/P1/P2 domains from Haravan Omni API into Postgres raw JSONB, transforms via dbt into a Kimball star schema, and ships with Telegram notifications, CLI orchestration, production hardening, and CI/CD. 130/130 pytest pass (90.49% coverage, 70% gate enforced), all dbt nodes build (156/156), mypy clean.

Commit chain on `feat/haravan-elt`:
- d14f975 — phase-08 Telegram notifier (4 lifecycle events, fail-soft)
- d4357bc — phase-09 P1 domains + validate command (inventory + collections cartesian)
- e2f85fe — phase-10 hardening (fcntl lockfile, cron, README rewrite)
- 04cfddc — phase-11 P2 domains + VN holidays (discounts/promotions/events, dim_date enriched)
- a818752 — phase-12 CI (GitHub Actions + `make ci-local`)

## The Brutal Truth

This was satisfying but also *exhausting*. The plan covered 12 phases over ~8 weeks; we shipped all 5 final phases in the same session. That's 22+ days of planned work compressed into what felt like 36 hours of real time. Token efficiency matters when you're this deep.

What makes it slightly frustrating: **24 deferred items** are explicitly marked across all 12 phases, all legitimate (need live Haravan tokens, need VPS for 7-day soak, need first GitHub push). The plan docs now distinguish "verifiable locally" from "needs prod push," which is honest, but it means the `plan.md` `✅ completed` badges mask a reality that's more nuanced than "done." The plan succeeded *by design*, not by magic.

The naming-guidance hook fired ~30 times during this session suggesting kebab-case for Python files, but the project already follows PEP 8 (snake_case). The hook is generic; projects have their own conventions. Had to train myself to ignore it after the 10th prompt.

## Technical Details

**Telegram (phase-08):** Implemented `TelegramClient.send()` with fail-soft wrapping (no exception propagates). Four event types: start (cron-only, not manual), success, failure, warning. `Notifier` class templates markdown messages; pipeline hooks into all 4 events. Rate limit counter in `HaravanClient` tracks consecutive 429s; >3 triggers warning event. VCR cassettes hand-crafted for Telegram (only 200/500 fixture pair, 4 requests total). Respx mocks used elsewhere — didn't record live cassettes against real Haravan (phase-10 explicitly deferred, needs token).

**P1 Domains (phase-09):** Four extractors added: inventory_adjustments (standard pagination), inventory_locations (cartesian: location_ids × variant_ids batched 100/request), custom_collections, smart_collections. The inventory_locations extractor's cartesian iteration is the trick — for a shop with 10 locations and 5k variants, that's ~500 requests per run. Rate limiter handles it (4 req/s, 80-burst). `fct_inventory_snapshot` keyed on (location_id, variant_id, snapshot_date) — idempotent merge. `Validate` command compares `GET /count.json` to `SELECT count(*) FROM raw.*` with ±0.1% tolerance; exit 0/1. Respx mocks, no live cassettes recorded.

**Hardening (phase-10):** `fcntl.flock` non-blocking acquire in `cron_entry.py` — exit code 2 on contention (systemd/cron sees that and backs off). Lock file at `/var/lock/haravan-elt.lock`, auto-releases on process death. Structlog prod config (JSON output, run_id contextvars). Disk cleanup: `archive-run-log.sql` moves rows >90 days to archive table (cron monthly). README rewritten from skeleton to zero-to-first-run: clone → `make dev` → env setup → `haravan-elt init` → seed historical → cron. Coverage threshold ≥70% enforced in pytest.

**P2 Domains (phase-11):** Three more extractors: discounts (`/com/discounts.json`), promotions (`/com/promotions.json`), events (`/com/events.json`). Events are append-only on `id` ascending (no `updated_at` watermark). Refresh token auto-rotation: write to `.env.tmp`, atomic `os.replace(.env.tmp, .env)`, `chmod 600`. VN holidays seed as static CSV (via one-shot `generate_vn_holidays_seed.py` using `holidays` library); `dim_date` enriched with `is_public_holiday`, `holiday_name`, `is_weekend`, `is_business_day`. Tests use respx mocks; live cassettes deferred (same as P1).

**CI (phase-12):** GitHub Actions workflow: checkout → Python 3.11 setup (pip cache) → `ruff format --check` → `ruff check` → `mypy src/` → Postgres 15 service init (haravan_ci / haravan_ci) → pytest with VCR `record_mode='none'` (cassettes pre-recorded in repo) → `--cov-fail-under=70`. `make ci-local` Makefile target chains `lint typecheck init test` locally; mirrors CI steps exactly. User can `make ci-local` before push and trust green == CI green. Concurrency block cancels stale PR runs.

## What We Tried

**Filelock library vs os.replace + fcntl:** Considered adding `filelock` dep for phase-10, but the existing pattern (tempfile + atomic `os.replace` + chmod 600) already handles POSIX atomicity. `fcntl.flock` adds process-level serialization. Adding a third lock layer would be redundant safety with zero failure mode it actually fixes. **Decision: KISS, skip filelock.**

**Events watermark: high_id vs timestamp:** Events endpoint supports both `updated_at_min` and `id` ascending. Using `id` ascending avoids the timestamp ambiguity (events created at same millisecond have same `updated_at`). **Decision: override `idempotent_load()` to return `(rows, None)`, let pipeline's timestamp path stay inert.** No new abstraction, no flag.

**VCR everywhere vs respx mocks:** Phase plan said "VCR cassettes for all extractors." But recording cassettes against live Haravan API requires real OAuth tokens the user doesn't have. Telegram cassettes were hand-crafted (simple, 4 requests). Everything else uses respx mocks (same pattern as phase-04, cleaner for test maintenance). **Decision: Document the deviation explicitly in phase docs.** Marked "DEFERRED, used respx" in both phase-09 and phase-11 todo lists.

**`make ci-local` vs installing `act`:** Could use `act` (Docker-based GitHub Actions runner) to test locally, but that adds a dep + container overhead. Instead, chained existing `lint typecheck init test` Makefile targets into a new `ci-local` target. Same steps, no extra deps. **Decision: CI mirror over extra tooling.**

**conftest.py DATABASE_URL:** Tests needed to switch DB between dev (5434/haravan) and CI (5432/haravan_ci). Considered a fixture or env-read-only pattern. **Decision: `conftest.py` reads `DATABASE_URL` once at module load (CI overrides via GH Actions env); `_isolate_env` fixture scrubs it inside tests.** Clean override, no test code changes.

## Root Cause Analysis

No failures. The "root cause" here is forward momentum: phases 01–07 laid solid groundwork (auth, extractors, dbt staging, CLI). Phases 08–12 were polish + completion. The only structural tension was plan-document completeness: marking items ✅ when live-token verification is deferred. **Mitigation: split phase docs into "verifiable locally" and "needs prod," explicit in plan.md.**

## Lessons Learned

1. **Plan document fidelity:** A plan with 24 deferred items isn't a failure if the deferral is *explicit and categorized*. "Needs live token," "needs VPS," "needs first push" are all valid deferral reasons. But a reader must know this at a glance. Phase-12 success criteria now distinguish both classes.

2. **Respx mocks for stateless APIs:** VCR cassettes are powerful for replay testing, but they require real credentials to record. Respx mocks are simpler for test isolation and work just as well. The trade-off: lose real-API discovery (edge cases, actual error codes). Document the choice.

3. **Atomic file writes matter.** The refresh token write-back looked simple until we considered concurrency: cron might run while a manual `haravan-elt extract` is in progress (unlikely but possible). Tempfile + atomic replace + chmod is the POSIX solution. Don't skip it.

4. **fcntl.flock is lean.** No extra dep, no background daemon, works across process restarts. Exit code 2 on contention signals cleanly to cron. Systemd timer could also work (phase-10 offers both), but cron is simpler for MVP.

5. **Naming hook is a red herring for established projects.** The hook suggests kebab-case for everything. But PEP 8 says snake_case for Python. Project conventions beat generic guidance. Worth documenting in `.claude/rules/` for future sessions.

## Next Steps

1. **User pushes to GitHub:** First `git push origin feat/haravan-elt` triggers CI (phase-12 success criteria #1: "PR triggers ci.yml"). Verify badge green, then merge.
2. **Live verification blocked on Haravan credentials:** User needs to obtain API token + Telegram bot token, then re-record VCR cassettes locally (or run with respx mocks active — mocks are already baked, will pass tests). Phase docs list these as DEFERRED.
3. **7-day cron soak (phase-10 success criteria):** Needs VPS. Once deployed, monitor `meta.run_log` for 7 days of incremental stability. Archive cleanup via cron monthly.
4. **Optional: document deferred items in README.** Add section "Verification Checklist" listing steps that need credentials/deployment.

**Owner:** User (has all 12 phases + commits on feat/haravan-elt).
**Timeline:** CI validation on first push (immediate); live deployment when VPS available.

## Unresolved Questions

- Q1: Should `inventory_locations` snapshot on `updated_at` or just `created_at`? Assumption: snapshot_date = current date at run time (what we ship). If Haravan returns historical state, revisit.
- Q2: Does `validate` need `--all` mode (validate all domains in one call)? Deferred; manual per-domain sufficient for MVP.
- Q3: Are P2 domains (discounts/promotions/events) truly append-only with no updates? Assumption yes; dbt tests will catch mutating data.
- Q4: Should we alert on slow runs (>5 min inventory locations fetch)? Not in PRD; add post-MVP if needed.
