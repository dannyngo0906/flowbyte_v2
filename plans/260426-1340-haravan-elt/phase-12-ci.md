# Phase 12 — CI (GitHub Actions, parallel to M6)

## Context Links

- PRD §10 (deploy/test infrastructure) — implicit; CI not in PRD §11 milestones but user-locked decision
- Tech stack: `docs/tech-stack.md` § CI (GitHub Actions, lint+test on PR)
- Research: `plans/reports/researcher-260426-1340-idempotent-elt-python.md` § VCR (`record_mode='none'` in CI)
- Depends on: phase-01 (pyproject.toml, ruff/mypy/pytest config, schema init SQL); independent of phases 02–11 — runs in parallel
- Updated by: phase-09 (validate command — add smoke test step), phase-10 (coverage threshold), phase-11 (P2 cassettes)

## Overview

- **Priority:** medium (gates merges; parallel-track delivery)
- **Status:** completed (locally verified; trial PR pending push)
- **Effort:** 1 day
- **Description:** GitHub Actions workflow for `lint → type → test` on every PR + push to `main`. Postgres 15 service container for integration tests. VCR cassettes mode=none. pip + venv cache. Status badge in README.

## Key Insights

- **Trigger surface:** `pull_request` (any branch → main) + `push` (main only). PR triggers needed for review gating; main triggers catch hot-fix bypasses.
- **Postgres service container:** `postgres:15-alpine` healthcheck via `pg_isready`; matches dev `docker-compose.yml` major version. CI DB user: `haravan_ci` / pass from secret or hardcoded ephemeral.
- **VCR cassettes baked in repo** → `record_mode='none'` in CI; tests fail loud if cassette missing or out-of-date (forces dev to re-record locally first).
- **Cache pip wheels** via `actions/setup-python@v5` `cache: pip` — saves ~30s on warm runs. Cache key based on `pyproject.toml` hash.
- **dbt artifact cache** (optional): `~/.dbt` cache cuts ~10s but minor; skip in MVP.
- **Coverage threshold** enforced by `pytest --cov-fail-under=70` (set in phase-10); CI just runs pytest, threshold fails the job natively.
- **Concurrency control:** cancel in-progress runs on the same PR via `concurrency:` block — saves CI minutes.

## Requirements

**Functional:**
- Workflow runs on PR + push main
- Steps: checkout → Python setup → pip install → ruff format check → ruff check → mypy → schema init (SQL) → pytest with coverage
- Postgres 15 service container reachable as `localhost:5432`
- Job fails on any step error (default behavior, no `continue-on-error`)
- Badge URL in `README.md` reflects status

**Non-functional:**
- Total wall time < 5 min for warm cache (typical project size)
- No secrets needed in MVP (VCR cassettes pre-recorded; no live API calls)
- Concurrency: cancel stale PR runs

## Architecture

```
.github/workflows/
  └─ ci.yml
       on: [pull_request, push to main]
       concurrency: cancel-in-progress per ref
       jobs:
         test:
           runs-on: ubuntu-22.04
           services:
             postgres:
               image: postgres:15-alpine
               env: { POSTGRES_USER, POSTGRES_PASSWORD, POSTGRES_DB }
               options: --health-cmd pg_isready --health-interval 10s
               ports: [5432:5432]
           steps:
             1. checkout (actions/checkout@v4)
             2. setup python 3.11 (cache: pip)
             3. pip install -e ".[dev]"
             4. ruff format --check .
             5. ruff check .
             6. mypy src/
             7. psql -c \i src/haravan_elt/meta/schema.sql        # init schemas
             8. psql -c \i src/haravan_elt/meta/schema_p2.sql     # if exists (after phase-11)
             9. pytest --cov=src/haravan_elt --cov-report=term --cov-fail-under=70
            10. upload coverage artifact (optional)
```

## Related Code Files

**Create:**
- `.github/workflows/ci.yml` — main workflow file
- `.github/workflows/README.md` (optional, ≤30 lines) — explain triggers + how to skip CI for docs-only PRs

**Modify:**
- `README.md` — add CI status badge near top: `![CI](https://github.com/<owner>/<repo>/actions/workflows/ci.yml/badge.svg)`
- `pyproject.toml` — ensure `[project.optional-dependencies] dev = [...]` includes pytest, pytest-vcr, pytest-cov, ruff, mypy, types-* stubs (set in phase-01; verify here)
- `pytest.ini` or `pyproject.toml [tool.pytest.ini_options]` — `--cov-fail-under=70` after phase-10 lands; pin in phase-12 if not yet present

**Delete:** none

## Implementation Steps

1. **Pre-flight check** — verify these exist (from phase-01):
   - `pyproject.toml` with `[project.optional-dependencies] dev` including pytest+vcr+cov, ruff, mypy
   - `src/haravan_elt/meta/schema.sql`
   - `tests/` directory + at least one passing test
   - `.env.example` for reference (CI uses inline env, not `.env`)

2. **Write `.github/workflows/ci.yml`:**

   ```yaml
   name: CI

   on:
     pull_request:
       branches: [main]
     push:
       branches: [main]

   concurrency:
     group: ci-${{ github.ref }}
     cancel-in-progress: true

   jobs:
     test:
       name: Lint + Type + Test
       runs-on: ubuntu-22.04
       timeout-minutes: 15

       services:
         postgres:
           image: postgres:15-alpine
           env:
             POSTGRES_USER: haravan_ci
             POSTGRES_PASSWORD: haravan_ci
             POSTGRES_DB: haravan_ci
           ports:
             - 5432:5432
           options: >-
             --health-cmd "pg_isready -U haravan_ci"
             --health-interval 10s
             --health-timeout 5s
             --health-retries 10

       env:
         DATABASE_URL: postgresql://haravan_ci:haravan_ci@localhost:5432/haravan_ci
         # Dummy tokens — VCR cassettes mode=none, no live API calls
         HARAVAN_SHOP_DOMAIN: ci.myharavan.com
         HARAVAN_ACCESS_TOKEN: dummy-access
         HARAVAN_REFRESH_TOKEN: dummy-refresh
         HARAVAN_CLIENT_ID: dummy-client
         HARAVAN_CLIENT_SECRET: dummy-secret
         TELEGRAM_BOT_TOKEN: dummy-bot
         TELEGRAM_CHAT_ID: "0"
         LOG_LEVEL: DEBUG
         LOG_FORMAT: console
         TIMEZONE: Asia/Ho_Chi_Minh

       steps:
         - uses: actions/checkout@v4

         - name: Setup Python 3.11
           uses: actions/setup-python@v5
           with:
             python-version: "3.11"
             cache: pip
             cache-dependency-path: pyproject.toml

         - name: Install deps
           run: |
             python -m pip install --upgrade pip
             pip install -e ".[dev]"

         - name: Ruff format check
           run: ruff format --check .

         - name: Ruff lint
           run: ruff check .

         - name: Mypy type check
           run: mypy src/

         - name: Init Postgres schemas
           run: |
             psql "$DATABASE_URL" -f src/haravan_elt/meta/schema.sql
             # phase-11 adds schema_p2.sql; guard with -f only if exists
             if [ -f src/haravan_elt/meta/schema_p2.sql ]; then
               psql "$DATABASE_URL" -f src/haravan_elt/meta/schema_p2.sql
             fi

         - name: Run tests with coverage
           run: pytest --cov=src/haravan_elt --cov-report=term-missing --cov-report=xml

         - name: Upload coverage artifact
           if: always()
           uses: actions/upload-artifact@v4
           with:
             name: coverage-xml
             path: coverage.xml
             retention-days: 7
   ```

3. **Add `psql` to runner** — `ubuntu-22.04` has `postgresql-client` preinstalled; verify with `which psql` step on first run if surprises.

4. **README badge** — insert near top:
   ```markdown
   ![CI](https://github.com/<owner>/<repo>/actions/workflows/ci.yml/badge.svg)
   ```
   Replace `<owner>/<repo>` after first push to GitHub.

5. **Test locally first** — run each CI step locally to make sure it passes before opening PR:
   ```bash
   ruff format --check .
   ruff check .
   mypy src/
   docker compose up -d postgres
   psql "$DATABASE_URL" -f src/haravan_elt/meta/schema.sql
   pytest --cov=src/haravan_elt --cov-fail-under=70
   ```

6. **Open trial PR** — push branch + open draft PR; verify workflow triggers, all steps pass, badge updates.

7. **(Optional) Skip CI for docs-only changes** — add `paths-ignore: ['**.md', 'docs/**', 'plans/**']` to triggers if needed; defer to first false-positive run.

## Todo List

- [x] Verify phase-01 deliverables — pyproject [dev] + 3 schema files + tests/ all present
- [x] Write `.github/workflows/ci.yml` (paths-ignore for docs/plans/markdown applied upfront)
- [x] `--cov-fail-under=70` already in `pyproject.toml` (phase-10)
- [x] Add CI badge to `README.md` (placeholder OWNER/REPO until first push)
- [x] Run all CI steps locally — added `make ci-local` target mirroring workflow exactly; all green
- [ ] Push trial branch + open draft PR — DEFERRED, user-driven
- [x] Document `make ci-local` in README Development section
- [x] paths-ignore applied upfront (docs/plans/markdown — saves CI minutes for doc-only PRs)
- [x] schema_p2.sql `if [ -f ]` guard verified locally — passes whether file exists or not
- [x] conftest.py honors `DATABASE_URL` env var so CI's `localhost:5432/haravan_ci` overrides dev's `5434/haravan` cleanly

## Success Criteria

- [ ] PR to `main` triggers `ci.yml`; status check appears on PR page — DEFERRED, needs first push
- [x] Workflow steps pass on clean main — locally mirrored via `make ci-local`, all green
- [ ] Total wall time < 5 min on warm cache — DEFERRED, only measurable on real GitHub runner
- [x] Postgres service container healthy via `--health-cmd pg_isready --health-retries 10` (~100s budget)
- [ ] Failing test → red status check → merge blocked — DEFERRED, needs branch protection rule on GitHub
- [ ] CI badge green in README — DEFERRED, OWNER/REPO placeholder until first push
- [x] No secrets required — workflow uses dummy env vars, VCR cassettes mode=none

## Risk Assessment

| Risk | Impact | Mitigation |
|------|--------|------------|
| Postgres service container slow to become healthy | Flaky tests on cold runner | `--health-retries 10` + `--health-interval 10s` (~100s budget); add explicit wait loop if still flaky |
| pip cache miss on every run | Slow CI | `cache-dependency-path: pyproject.toml` — busts only on dep changes |
| VCR cassettes drift from API reality | False CI green, prod failure | Document re-record cadence in README; phase-09 `validate` command + manual recording quarterly |
| `mypy --strict` flags new code on each PR (toxic) | Slow review | Start with `mypy src/` (no `--strict`); tighten incrementally; document in code-standards.md |
| Branch protection not enforced | CI green but PR still merges with red | Configure repo settings post-first-push; document in deploy guide |
| GitHub Actions rate limit on free tier | Concurrency cap | `concurrency: cancel-in-progress` reduces minute usage; monitor when scaling |

## Security Considerations

- **No real secrets in CI:** VCR cassettes use dummy tokens; CI env vars hardcoded dummies. Live API calls would require secrets — explicitly out of scope.
- **Coverage XML upload:** retention 7 days; not public unless artifact link shared.
- **Postgres service container:** ephemeral, destroyed at job end; weak credentials acceptable.
- **Branch protection (manual GitHub setting, document in deploy):** require CI green + 1 approval before merge to `main`.
- **Dependabot (post-MVP):** enable for `pip` ecosystem to flag CVE in deps. Document as follow-up.

## Next Steps

After phase-12 → repo has gating CI; safe to enable branch protection on `main`.

Follow-ups (not in scope):
- Dependabot weekly scans (security + version)
- Release workflow (semantic-release, GitHub release on tag)
- Coverage badge via Codecov / Coveralls
- Nightly job that re-records VCR cassettes against staging API (catches drift)
- Deploy workflow (post-MVP if user later wants auto-deploy to VPS via SSH)

## Unresolved Questions

1. Branch protection rules — want manual setup or scripted via `gh api`? Default: document manual steps in deploy guide.
2. Should mypy run in `--strict` mode from the start, or relaxed? Default: relaxed (matches PRD NFR-5 "type hints đầy đủ" without forcing strict everywhere); revisit after first 3 phases land.
3. Codecov integration desired? Default: no — local `--cov-fail-under` is enough; revisit if multi-contributor.
