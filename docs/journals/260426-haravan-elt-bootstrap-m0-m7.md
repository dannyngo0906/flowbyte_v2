# Haravan ELT Bootstrap: 7 Phases, 3 Code-Review Bugs Caught, One Deferred Decision On Cassettes

**Date**: 2026-04-26 16:30
**Severity**: High
**Component**: haravan-elt (Python 3.11 ELT pipeline)
**Status**: In Progress (phases 1–7 of 12 complete)

## What Happened

Bootstrapped a complete Python ETL pipeline in one 6-hour session: `/ck:bootstrap --full` PRD ingestion, 6-question scoping, 3 parallel researcher agents, detailed 12-phase plan, cook flow phases 1–7 with mandatory code review at each phase, 74/74 tests passing, 91% coverage, mypy --strict clean. 18 commits. **Result: working extract → transform → load → orchestrate pipeline, Haravan API → Postgres 15 → dbt → CLI.**

## The Brutal Truth

This workflow actually works. PRD → plan → implementation → review → test → commit cycle is faster than I expected because **code-reviewer caught 5 real bugs that would've shipped.** The bootstrap process is legit not a toy.

What hurt: **pyrate-limiter v3.9 doesn't do what I thought it does.** Non-blocking `try_acquire()` raises BucketFullException immediately on rate-limit saturation. If not caught, pipeline dies under load. This wasn't a "nice to catch during code review" — this was a critical architecture flaw. Without reviewer, it would've hit production and crashed every sync when Haravan traffic spiked.

The exhausting part: **plan → code gap was real on edge cases.** Plan said "clone orders pattern," which would've meant 4 copy-paste extractors (~120 LOC each, same bugs 4×). Refactored to PaginatedListExtractor template instead. Code-reviewer endorsed. But plan didn't anticipate the DRY value; it assumed copy-paste was acceptable boilerplate. Reviewer's job was to catch that, and it did.

The frustrating bit: **refresh token rotation breaks .env persistence.** Haravan rotates tokens every use (30d TTL). If we don't write back to .env, next run reads stale token. Used tempfile + atomic rename to avoid partial writes on crash. Added to learnings, will need live API to verify edge cases (e.g., what if token write fails mid-sync?). Deferred to phase-10.

## Technical Details

**Haravan API Findings (Researchers):**
- Rate limit is **leaky bucket 4 req/s burst 80**, NOT "40/min strict" (PRD was wrong). Matters for concurrent extractor scheduling.
- Pagination is offset-based, no cursor. Refunds + transactions are **embedded in order JSON** (no N+1 fetches needed).
- Refresh tokens rotate every sync — persistence is required, not optional.

**Phase-02 Critical Bug (code-reviewer):**
```python
# WRONG (pyrate-limiter v3.9)
try:
    limiter.try_acquire(cost=1)  # non-blocking, raises BucketFullException
    # call API
except BucketFullException:
    # token would fail under load because we're not retrying
    pass

# RIGHT
limiter = Limiter(
    LeakyBucket(capacity=4, leak_interval=1),
    max_delay=30,  # blocking retry up to 30s
    raise_when_fail=False
)
# or exponential backoff outside of limiter
```

Without fix, pipeline dies when burst=80 exhausts and next request arrives before leak. Code-reviewer caught this; would've shipped otherwise.

**Phase-04 Refactoring (High):**
Plan assumed copy-paste 4× extractors (Orders, Products, Locations, Customers). Refactored to PaginatedListExtractor base:
```python
class PaginatedListExtractor(BaseExtractor):
    def extract(self, state: SyncState) -> Iterator[dict]:
        # offset-based pagination, watermarking, retry
        # 25 LOC, subclasses ~5 LOC each
        
class OrdersExtractor(PaginatedListExtractor):
    endpoint = "/orders"
    supports_incremental = True
```
Saved 95 LOC of duplicate retry/pagination logic. **Reviewer endorsed; plan didn't anticipate this.** Lesson: plan for boilerplate elimination during code review, not before coding.

**Phase-06 Drift Testing (High):**
Initial fct_order_lines tautological test:
```python
# WRONG: tautological assertion
assert (total_refunded_vnd) == (total_price - net_revenue_vnd)
# this is always true by construction

# RIGHT: expose both
total_refunded_haravan_vnd = (from API)
total_refunded_calc_vnd = sum(refund_tx.amount for ...)
# warning-level test: assert they match (drift signal)
```
Allows us to detect if Haravan refund logic diverges from our transactions table. Reviewer flagged; would've shipped with false confidence.

**Phase-03 Postgres Port Conflict:**
Local Postgres 16 on 5432 → switched docker-compose to 5434. Not a bug, but a discovery. Matters for CI/CD (GitHub Actions will use docker network, not localhost).

**Phase-07 Telegram Exception Isolation (High):**
Code-reviewer caught: if notify fails, it should NOT crash the entire pipeline.
```python
# WRONG
self._maybe_notify(f"Sync complete: {summary}")  # can raise

# RIGHT
try:
    self._maybe_notify(...)
except Exception as e:
    logger.warning(f"Notification failed: {e}")
    # continue
```
Fail-soft for non-critical side effects. Reviewer is opinionated about this; applies to all telemetry.

## What We Tried

**Mock vs VCR Cassettes:**
- **Plan**: Use pytest-vcr cassettes for realistic replay
- **Reality**: No live Haravan token yet → hand-crafted cassettes would be guesswork-shaped JSON
- **Decision**: Use respx (httpx mock) for unit tests + integration vs real Postgres docker
  - Lets us verify HaravanClient state machine (retry, refresh, rate-limit logic) without live API
  - Defering VCR to phase-10 when live token exists
  - This is correct tradeoff; cassettes from guess-work would hide bugs

**dbt Schema Naming Surprise:**
- dbt creates `<profile.schema>_<+schema>` → `staging` + `+schema: marts` = `staging_marts` schema
- Not what user expects in BI dashboards
- Flagged as phase-12 prod profile cleanup (low priority, non-blocking)

**Seed Data Iteration:**
- First seed: order with no `product_id` → FK test failed
- Added `product_id`, then realized no refunds → can't verify transactions UNION logic
- Added 2nd order with refund
- **Reviewer said: "Always seed for the failure path you want to test."** Good reminder.

## Root Cause Analysis

**Why pyrate-limiter bug wasn't caught in plan:**
- Plan written by human planner agent reading docs, not testing implementation
- Pyrate-limiter v3.9 behavior (non-blocking `try_acquire`) wasn't clear in quick docs skim
- Should've been caught during Phase-02 impl, but wasn't until code review
- **Fix: mandatory integration test against local rate limiter under load**

**Why copy-paste pattern wasn't eliminated:**
- Plan default-assumes "standard boilerplate is fine, refactor if big"
- Phase-04 DRY refactoring happened organically during impl, not from plan direction
- **Lesson: code-reviewer role includes "did we abstract too early?" AND "did we copy-paste unnecessarily?"**

**Why tautological test was written:**
- Dev (me) wrote test from spec: "verify total_refunded matches calculation"
- Spec didn't say which total_refunded (Haravan-reported vs calculated)
- Allowed construction to define itself; test became vacuous
- **Lesson: tests must compare independent sources or they're just assertions of logic, not validation of data**

**Why token rotation persistence matters:**
- PRD said "OAuth tokens," didn't specify rotation behavior
- Researchers found rotation happens every use, but .env write-back was optional in plan
- Without this, second run fails silently (token expired)
- **Phase-10 will need live API to test: what if .env write fails? What if process dies mid-write?**

## Lessons Learned

1. **Code review finds architecture bugs, not just linting.** Pyrate-limiter issue, Telegram exception, watermark pollution. Real bugs that would've crashed production.

2. **Plan is right-sized but incomplete.** Got 7/12 phases working. Skipped plan-directed copy-paste; refactored instead. Plan's risk table didn't anticipate DRY value or tautological test trap.

3. **Mock tests are fine when real API is unavailable.** respx lets us verify client state machine (retry, refresh, rate-limit) crisply. VCR cassettes deferred correctly to phase-10.

4. **Token persistence is not optional if tokens rotate.** Standard OAuth spec says refresh tokens may rotate. Haravan does. Must persist atomically.

5. **Seed data needs failure-path coverage.** First seed worked for happy path. Second iteration added refunds to test transactions UNION. Reviewer's feedback was valuable.

6. **Schema naming matters for BI.** `staging_marts` is not user-intuitive. Flagged for phase-12, but matters for delivery.

7. **Tautological tests are silent failures.** Test that passes because it asserts logic, not because it validates data. Need independent source (Haravan-reported vs calculated).

## Next Steps

1. **Phase-08**: Data quality tests (dbt generic tests + custom SQL assertions for cross-system drift)
2. **Phase-09**: Performance & scaling (parallel domain extraction, batch sizes, connection pooling)
3. **Phase-10**: Live API integration (real Haravan shop, VCR cassettes, token rotation under load, .env persistence edge cases)
4. **Phase-11**: Deployment & VPS hardening (docker-compose prod, systemd timer, log aggregation, secrets management)
5. **Phase-12**: Schema/naming cleanup, BI-ready mart configuration, user documentation

**Files**: 25 src files (mypy --strict clean), 74 tests (respx mocks), 95 dbt tests (PASS), 18 commits, 66/75 plan todos checked, 9 deferred (awaiting live token or phase-10).

## Unresolved Questions

- Will token rotation edge case (write fails mid-sync) cause issues? Deferred to phase-10 live API.
- Should phase-10 include cassette generation for CI/CD, or mock respx for all tests? Decision pending.
- Is `staging_marts` schema naming acceptable for BI, or must it be cleaned in phase-12? User to confirm.
- dbt ephemeral models slow query compilation? Not measured yet.
- Postgres 5434 vs docker network DNS for CI/CD? Needs testing in GitHub Actions.
