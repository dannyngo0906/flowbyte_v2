# Phase 01 — Setup Environment (M0)

## Context Links

- PRD §6.1 (stack), §6.3 (folder), §8 (meta tables), §9 (.env): `/Users/duyngo/Downloads/documents-elt-tools/docs-etl2/haravan-api/PRD.md`
- Tech stack (locked): `/Users/duyngo/Claude/etl-tool-v3-clean/docs/tech-stack.md`
- Research (idempotent ELT §9 pydantic-settings, §10 lockfile): `plans/reports/researcher-260426-1340-idempotent-elt-python.md`

## Overview

- **Priority:** high
- **Status:** pending
- **Effort:** 3 days
- **Description:** Scaffold repo, lock dependencies, dev Postgres via docker-compose, init schemas + meta tables, folder skeleton, lint/format/test commands.

## Key Insights

- Tech stack is locked — no runtime substitution allowed
- `meta.sync_state` + `meta.run_log` schemas defined verbatim in PRD §8 — copy as-is
- Schemas split: `raw` (JSONB landing), `staging` (dbt views), `marts` (dbt tables), `meta` (operational)
- pre-commit hook = `ruff format` + `ruff check` (no `black` — ruff format replaces it)
- Postgres 15+ mandatory (dbt MERGE strategy) → pin docker image to `postgres:15-alpine`

## Requirements

**Functional:**
- FR-CLI1: Exit codes correct (foundation; verified later)
- Init creates 4 schemas + 2 meta tables (PRD §8)

**Non-functional:**
- NFR-4: `.env` file chmod 600, never committed
- NFR-5: Type hints mandatory, ruff format + ruff check + mypy strict
- NFR-6: Ubuntu 22.04 + macOS 12+ supported (Linux/macOS Postgres docker)

## Architecture

```
Local dev machine
  ├─ docker-compose up   ─▶  postgres:15-alpine (port 5432, volume pg_data)
  ├─ pip install -e .[dev]
  ├─ make init           ─▶  src.haravan_elt.meta.schema executes ./src/haravan_elt/meta/schema.sql
  └─ pytest              ─▶  uses VCR cassettes (none yet, but path configured)
```

Folder skeleton (see PRD §6.3):
```
haravan-elt/
├── pyproject.toml
├── docker-compose.yml
├── Makefile
├── .env.example
├── .pre-commit-config.yaml
├── README.md
├── src/haravan_elt/
│   ├── __init__.py
│   ├── cli.py            # stub Typer app
│   ├── config.py         # pydantic-settings stub
│   ├── pipeline.py       # stub
│   ├── client/           # __init__.py
│   ├── extractors/       # __init__.py + base.py stub
│   ├── loaders/          # __init__.py
│   └── meta/
│       ├── __init__.py
│       ├── schema.sql    # CREATE SCHEMA + meta tables
│       └── state.py      # stub
├── dbt/                  # placeholder, populated M3
├── tests/
│   ├── conftest.py
│   └── fixtures/vcr/     # empty, populated M1+
├── deploy/
│   └── crontab.example
└── scripts/
    └── run-daily.sh      # stub
```

## Related Code Files

**Create:**
- `pyproject.toml` (PEP 621)
- `docker-compose.yml`
- `.env.example`
- `Makefile`
- `.pre-commit-config.yaml`
- `.gitignore`
- `README.md` (skeleton; full rewrite in phase-10)
- `src/haravan_elt/__init__.py`
- `src/haravan_elt/cli.py` (stub)
- `src/haravan_elt/config.py`
- `src/haravan_elt/pipeline.py` (stub)
- `src/haravan_elt/client/__init__.py`
- `src/haravan_elt/extractors/__init__.py`
- `src/haravan_elt/extractors/base.py` (abstract stub)
- `src/haravan_elt/loaders/__init__.py`
- `src/haravan_elt/meta/__init__.py`
- `src/haravan_elt/meta/schema.sql`
- `src/haravan_elt/meta/state.py` (stub)
- `tests/conftest.py`
- `tests/test_smoke.py` (asserts package imports)
- `deploy/crontab.example`
- `scripts/run-daily.sh`

**Modify:** none (greenfield)

**Delete:** none

## Implementation Steps

1. **Init repo**
   ```bash
   git init && git checkout -b feat/haravan-elt
   ```

2. **Create `pyproject.toml`** (PEP 621). Skeleton:
   ```toml
   [project]
   name = "haravan-elt"
   version = "0.1.0"
   requires-python = ">=3.11"
   dependencies = [
     "httpx>=0.27",
     "tenacity>=8.2",
     "pyrate-limiter>=3.7",
     "typer>=0.12",
     "rich>=13.7",
     "psycopg[binary]>=3.1.18",
     "pydantic-settings>=2.2",
     "structlog>=24.1",
     "dbt-core>=1.7,<2.0",
     "dbt-postgres>=1.7,<2.0",
     "dbt-utils",  # installed via dbt deps in M3
   ]

   [project.optional-dependencies]
   dev = [
     "pytest>=8.0",
     "pytest-vcr>=1.0",
     "pytest-cov>=5.0",
     "ruff>=0.4",
     "mypy>=1.10",
     "types-requests",
   ]

   [project.scripts]
   haravan-elt = "haravan_elt.cli:app"

   [build-system]
   requires = ["setuptools>=68"]
   build-backend = "setuptools.build_meta"

   [tool.setuptools.packages.find]
   where = ["src"]

   [tool.ruff]
   line-length = 100
   target-version = "py311"

   [tool.ruff.lint]
   select = ["E", "F", "I", "B", "UP", "N", "SIM"]

   [tool.mypy]
   strict = true
   python_version = "3.11"
   exclude = ["dbt/"]

   [tool.pytest.ini_options]
   testpaths = ["tests"]
   addopts = "--cov=haravan_elt --cov-report=term-missing"
   ```

3. **Create `docker-compose.yml`:**
   ```yaml
   services:
     postgres:
       image: postgres:15-alpine
       container_name: haravan_elt_pg
       environment:
         POSTGRES_USER: elt_user
         POSTGRES_PASSWORD: elt_pass
         POSTGRES_DB: haravan
       ports: ["5432:5432"]
       volumes: ["pg_data:/var/lib/postgresql/data"]
       healthcheck:
         test: ["CMD-SHELL", "pg_isready -U elt_user -d haravan"]
         interval: 5s
         timeout: 3s
         retries: 5
   volumes:
     pg_data:
   ```

4. **Create `.env.example`** matching PRD §9 (every var listed).

5. **Create `src/haravan_elt/meta/schema.sql`** (verbatim PRD §8):
   ```sql
   CREATE SCHEMA IF NOT EXISTS raw;
   CREATE SCHEMA IF NOT EXISTS staging;
   CREATE SCHEMA IF NOT EXISTS marts;
   CREATE SCHEMA IF NOT EXISTS meta;

   CREATE TABLE IF NOT EXISTS meta.sync_state (
       domain          TEXT PRIMARY KEY,
       last_updated_at TIMESTAMPTZ NOT NULL,
       last_run_id     UUID,
       updated_at      TIMESTAMPTZ DEFAULT now()
   );

   CREATE TABLE IF NOT EXISTS meta.run_log (
       run_id         UUID PRIMARY KEY,
       domain         TEXT NOT NULL,
       mode           TEXT NOT NULL,
       started_at     TIMESTAMPTZ NOT NULL,
       ended_at       TIMESTAMPTZ,
       rows_ingested  INTEGER,
       status         TEXT NOT NULL,
       error_message  TEXT,
       triggered_by   TEXT
   );

   CREATE INDEX IF NOT EXISTS idx_run_log_started ON meta.run_log(started_at DESC);
   ```

6. **Stub `config.py`:** pydantic-settings `Settings` class loads `.env`, exposes `database_url`, log level, etc. Full nesting (haravan/db/telegram subclasses) added in phase-02.

7. **Stub `extractors/base.py`:**
   ```python
   from abc import ABC, abstractmethod
   from typing import Iterator, Any

   class BaseExtractor(ABC):
       domain: str

       @abstractmethod
       def iter_pages(self, since=None, until=None) -> Iterator[list[dict]]: ...

       @abstractmethod
       def to_raw_row(self, item: dict, run_id: str) -> dict: ...
   ```

8. **Stub `cli.py`:**
   ```python
   import typer
   app = typer.Typer(no_args_is_help=True)

   @app.command()
   def init() -> None:
       """Run schema.sql against DATABASE_URL."""
       # Minimal impl: psycopg.connect(url).cursor().execute(schema_sql)
       ...
   ```

9. **Create `Makefile`:**
   ```make
   .PHONY: dev test lint format init typecheck

   dev:
   	docker-compose up -d
   	pip install -e ".[dev]"
   	pre-commit install

   test:
   	pytest

   lint:
   	ruff check src tests
   	ruff format --check src tests

   format:
   	ruff format src tests
   	ruff check --fix src tests

   typecheck:
   	mypy src

   init:
   	haravan-elt init
   ```

10. **Create `.pre-commit-config.yaml`:**
    ```yaml
    repos:
      - repo: https://github.com/astral-sh/ruff-pre-commit
        rev: v0.4.0
        hooks:
          - id: ruff-format
          - id: ruff
            args: [--fix]
    ```

11. **Create `.gitignore`** including `.env`, `__pycache__/`, `.venv/`, `dbt/target/`, `dbt/logs/`, `*.lock`, `.pytest_cache/`, `.mypy_cache/`, `.ruff_cache/`, `.coverage`, `tests/fixtures/vcr/*.yaml.tmp`.

12. **Smoke test (`tests/test_smoke.py`):** `import haravan_elt; assert haravan_elt`.

13. **Verify:**
    ```bash
    make dev
    haravan-elt init   # connects to docker-compose Postgres, runs schema.sql
    psql $DATABASE_URL -c "\dt meta.*"  # see sync_state, run_log
    make lint && make typecheck && make test
    ```

## Todo List

- [x] Init git repo + branch `feat/haravan-elt`
- [x] Write `pyproject.toml` (PEP 621, all deps + dev deps + ruff/mypy/pytest config)
- [x] Write `docker-compose.yml` (postgres:15-alpine, healthcheck, volume)
- [x] Write `.env.example` matching PRD §9
- [x] Write `Makefile` (dev/test/lint/format/typecheck/init targets)
- [x] Write `.pre-commit-config.yaml` (ruff-format + ruff)
- [x] Write `.gitignore`
- [x] Create `src/haravan_elt/` package skeleton (cli, config, pipeline, client/, extractors/, loaders/, meta/)
- [x] Write `src/haravan_elt/meta/schema.sql` (4 schemas + 2 meta tables verbatim PRD §8)
- [x] Stub `cli.py` with `init` subcommand running schema.sql
- [x] Stub `extractors/base.py` abstract class
- [x] Write `tests/conftest.py` + `tests/test_smoke.py`
- [x] Write `deploy/crontab.example` + `scripts/run-daily.sh` (stub)
- [ ] Write `README.md` skeleton (full version in phase-10)  <!-- DEFERRED: full README rewrite is phase-10 deliverable -->
- [x] Verify `make dev && haravan-elt init && make lint && make typecheck && make test` all green

## Success Criteria

- `docker-compose up -d` boots Postgres 15, healthcheck passes
- `pip install -e .[dev]` succeeds on Python 3.11
- `haravan-elt init` creates 4 schemas + 2 meta tables (verify via `psql -c "\dn"` and `\dt meta.*`)
- `make lint && make typecheck && make test` exit 0
- `pre-commit install` registers hooks; trivial commit triggers ruff format

## Risk Assessment

| Risk | Likelihood | Impact | Mitigation |
|------|-----------|--------|-----------|
| Local Python ≠ 3.11 | Medium | Med | Document `pyenv install 3.11` in README; CI pins py3.11 |
| psycopg[binary] wheel missing on Apple Silicon | Low | Low | Falls back to source build; `psycopg[c]` alternative documented |
| Docker Desktop not installed | Medium | Low | Document install link; `brew install docker` on macOS |

## Security Considerations

- `.env` MUST be in `.gitignore` (verified by hook)
- `.env.example` ships with placeholder values only
- Default `docker-compose.yml` uses `elt_pass` — flag in README "dev only, override in prod"
- No secrets committed; pre-commit hook to detect (post-MVP, optional `gitleaks`)

## Next Steps

Unblocks **all** subsequent phases. Phase-02 (client) and phase-12 (CI) can start immediately after phase-01 sign-off.

## Unresolved Questions

- None.
