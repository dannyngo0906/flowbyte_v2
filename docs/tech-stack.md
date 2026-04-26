# Tech Stack — haravan-elt

**Locked:** 2026-04-26 | **Source:** PRD §6.1 + research findings (260426-1340)

## Runtime

| Layer | Choice | Version | Notes |
|-------|--------|---------|-------|
| Language | Python | 3.11+ | type hints mandatory |
| OS | Ubuntu 22.04+ / macOS 12+ | — | systemd deploy on VPS |
| DB | PostgreSQL | 15+ | required for dbt MERGE strategy |

## Application

| Concern | Library | Why |
|---------|---------|-----|
| HTTP client | `httpx` | sync; matches Typer simplicity |
| Retry/backoff | `tenacity` | parses `Retry-After`; PRD didn't specify, research-added |
| Rate limit | `pyrate-limiter` (LeakyBucket) | matches Haravan leaky bucket model 1:1 |
| CLI framework | `Typer` + `Rich` | type-hinted commands, pretty output |
| DB driver | `psycopg[binary]` 3.x | native JSONB via `Jsonb()` wrapper, pipeline mode |
| Config | `pydantic-settings` | nested config, `SecretStr` for tokens |
| Logging | `structlog` + `contextvars` | JSON prod, console dev, run_id correlation |
| Locking | `fcntl.flock` (stdlib) | cron-safe, auto-release on process death |

## Transform

| Concern | Library | Notes |
|---------|---------|-------|
| Engine | `dbt-core` 1.7+ | invoked via `dbtRunner` (Python API) |
| Adapter | `dbt-postgres` | matches DB choice |
| Utils | `dbt-utils` | surrogate keys, date_spine |

**dbt structure:**
- `staging/` — view, 1:1 with raw, JSONB → typed cols inline `payload->>'key'::type`
- `intermediate/` — ephemeral, joins + `jsonb_array_elements()` for line items
- `marts/core/` — incremental for facts, table for dims

## Observability

| Concern | Choice |
|---------|--------|
| Notifications | Telegram Bot API (direct HTTP, no SDK) |
| Run log | `meta.run_log` (Postgres) |
| Watermark | `meta.sync_state` (Postgres) |

## Quality

| Concern | Tool |
|---------|------|
| Format | `ruff format` (replaces `black`) |
| Lint | `ruff check` |
| Type check | `mypy --strict` |
| Test | `pytest` + `pytest-vcr` (cassettes in `tests/fixtures/vcr/`) |
| Coverage | `pytest-cov`, target ≥70% (PRD M6) |

## Deploy

| Concern | Choice |
|---------|--------|
| Dev DB | `docker-compose.yml` (Postgres 15 + volume) |
| Prod | systemd service + cron daily incremental |
| Packaging | `pyproject.toml` (PEP 621), `pip install -e .` |

## CI

| Concern | Choice |
|---------|--------|
| Platform | GitHub Actions |
| Triggers | PR to `main`, push to `main` |
| Steps | `ruff format --check` → `ruff check` → `mypy` → `pytest` (with Postgres service container) |

## Out of scope (explicit non-deps)

- ❌ asyncio / async httpx — sync simpler, Haravan rate limit makes async overkill
- ❌ ORM (SQLAlchemy) — raw SQL via psycopg is simpler for ELT
- ❌ Airflow / Prefect / Dagster — cron is enough per PRD
- ❌ Web framework — CLI only
- ❌ Docker for prod — systemd preferred, Docker only for dev

## Research backing

- `plans/reports/researcher-260426-1340-haravan-api-quirks.md`
- `plans/reports/researcher-260426-1340-dbt-postgres-patterns.md`
- `plans/reports/researcher-260426-1340-idempotent-elt-python.md`
