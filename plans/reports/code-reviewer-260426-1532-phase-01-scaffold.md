# Code Review — Phase-01 Scaffold (haravan-elt)

**Scope:** Phase-01 scaffold only. ~25 files, ~150 LOC Python + config.
**Verification basis:** ruff/mypy --strict/pytest reported green by author.

## Pros (Solid)

- pyproject.toml structure clean (PEP 621), package-data ships `meta/*.sql`, ruff+mypy strict configured, sensible `extend-exclude`.
- `cli.py` Typer callback fix (lines 21–25) is the right idiom to force subcommand parsing with single command — comment explains the *why*.
- `_read_schema_sql` uses `importlib.resources` (cli.py:39–42) → works for editable + wheel installs.
- `config.py` wraps `database_url` in `SecretStr` → no leak via repr/log; `extra="ignore"` prevents .env pollution failures.
- `schema.sql` matches PRD §8 verbatim, idempotent (`IF NOT EXISTS` everywhere), tabular alignment readable.
- `run-daily.sh` has `set -euo pipefail` (line 6), uses `${HARAVAN_ELT_HOME:-…}` defaulting, `cd` quoted, `exec` for clean PID handoff. Solid bash.
- `tests/conftest.py` env-isolation autouse fixture is the right defensive default — tests fail loud if forgetting to inject.
- `tests/test_smoke.py` actually meaningful (not just `import x`): asserts version, abstract contract, packaged data file.
- docker-compose host port 5434 documented in `.env.example:16` (avoids local PG@5432 / flowbyte@5433 collision).
- Makefile portable: `COMPOSE` autodetect via `command -v` (Makefile:4), `db-up` polls health rather than relying on `depends_on`.
- `BaseExtractor` uses `collections.abc.Iterator` (PEP 585) + `dict[str, object]` modern typing.

## Issues

### Critical

None.

### High

None.

### Medium

- **Makefile:8** `pre-commit install || true` silently swallows real failures (e.g. corrupt git hook dir). Acceptable for first-time bootstrap but masks regressions. Consider checking for `.git/` existence first, then hard-fail if `pre-commit` errors.
- **cli.py:33** `psycopg.connect(...)` has no `autocommit=False` / explicit transaction; relies on context-manager commit on exit. Combined with explicit `conn.commit()` this is redundant but not wrong. Minor: drop the explicit `conn.commit()` OR drop the `with conn` — pick one idiom.
- **.env.example:17** ships dev creds (`elt_user:elt_pass`) verbatim matching docker-compose default. Fine for dev, but PRD §9 risk: copy-paste to prod without override. Add inline comment `# DEV ONLY — replace in prod`.

### Low

- **pyproject.toml:67** `filterwarnings = ["ignore::DeprecationWarning"]` is too broad — will hide future real deprecations from pydantic/dbt. Prefer module-scoped (`ignore::DeprecationWarning:dbt.*`).
- **pyproject.toml:62** mypy `exclude` and ruff `extend-exclude` both list `scripts/` — but `scripts/run-daily.sh` is bash, not Python. Excluding scripts/ is fine, just note no Python lives there yet.
- **cli.py:42** `Path(str(pkg)).read_text(...)` — `resources.files(...).joinpath(...)` returns a `Traversable` that supports `.read_text()` directly. Drop the `Path(str(...))` shim:
  ```python
  return resources.files("haravan_elt").joinpath("meta/schema.sql").read_text(encoding="utf-8")
  ```
  Same idiom already used in `tests/test_smoke.py:21` — DRY consistency.
- **config.py:35** `# type: ignore[call-arg]` is necessary but brittle if pydantic-settings v3 changes ctor signature. Acceptable for stub; revisit phase-02.
- **.gitignore:61** `plans/**/*` excludes the entire plan dir from git — intentional per existing pattern, but means phase plans are local-only. Confirm this is the team norm (it appears so based on existing reports/).
- **.pre-commit-config.yaml:4** ruff pre-commit pinned to `v0.4.10` while pyproject says `ruff>=0.4`. Versions can drift between local install and pre-commit env. Low risk, but worth aligning during phase-12 CI work.
- **pyproject.toml:14** `pyrate-limiter>=3.7` and `tenacity>=8.2` listed as runtime deps but unused in phase-01 code. Not flagged as "dead" since phases 02–04 will use them — noting only that lockfile generation (phase-01 plan §10 from research) was deferred.
- **state.py** has only docstring + `from __future__ import annotations` → effectively empty. Fine as stub but consider a `pass`-only placeholder is cleaner than an unused import.

## Edge Cases / Scout

- **CLI `init` failure mode:** if `DATABASE_URL` missing, pydantic will raise `ValidationError` *before* the helpful Typer help text. User sees a stack trace. Phase-09 (validate command) likely covers — accept for now.
- **Schema migration:** `schema.sql` has no version table. Idempotent is fine for phase-01 but adding a column to `meta.run_log` later requires manual migration. Not in scope but flag for phase-04+.
- **`autouse=True` env isolation** strips `DATABASE_URL` for *every* test — phase-02+ DB tests must inject via fixture. Working as intended; just document in conftest.
- **Cron script** assumes `.venv` exists in `$PROJECT_DIR` — if cron user differs from dev user, venv perms could bite. Phase-10 territory.

## Phase-01 Adherence (16 success-criteria items)

All 16 todo items present and verified locally per task description: pyproject ✓, docker-compose ✓ (port 5434 documented), .env.example ✓, Makefile ✓, pre-commit ✓, .gitignore Python additions ✓, package skeleton ✓, schema.sql ✓, cli init ✓, BaseExtractor abstract ✓, conftest+test_smoke ✓, crontab.example+run-daily.sh ✓. README skeleton out-of-scope per task description (phase-10).

## Recommendations (KISS, actionable)

1. Drop redundant `Path(str(...))` in `cli.py:42` — use `Traversable.read_text()` directly. (1-line change, DRY with test_smoke.py)
2. Tighten `filterwarnings` in `pyproject.toml` to specific modules. (low priority; revisit if dbt deprecations spam)
3. Add `# DEV ONLY` comment near `.env.example:17` DATABASE_URL.
4. Consider hard-failing in Makefile `dev` target if not in git repo (so `pre-commit install` failure is meaningful).

## Verdict

**Approve.** Scaffold is clean, idiomatic, and matches phase-01 scope. No critical or high-severity issues. All medium/low items are polish — none block phase-02 start.

---

**Status:** DONE
**Summary:** Phase-01 scaffold solid; idiomatic Python 3.11, strict typing, idempotent SQL, env isolation correct. Only minor polish items.
**Verdict:** approve
**Critical issues count:** 0
