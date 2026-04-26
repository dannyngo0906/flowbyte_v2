# haravan-elt

Self-hosted ELT pipeline for Haravan: Omni API → PostgreSQL JSONB → dbt star schema.
Single Python 3.11 process, idempotent re-runs, cron-driven daily incremental,
Telegram notifications. ~8 weeks of work delivered as one CLI binary.

## Quickstart

```bash
git clone <repo> && cd haravan-elt
make dev                                    # spin up Postgres 15 + install venv
cp .env.example .env                        # then fill in OAuth + Telegram (see below)
haravan-elt init                            # apply schemas + raw tables
haravan-elt extract orders --mode full      # first backfill — go grab coffee
haravan-elt run-all                         # full pipeline (extract every domain + dbt build)
```

## Prerequisites

- **Python 3.11** (3.12+ also fine)
- **Docker + Docker Compose** (only needed for the bundled Postgres dev container)
- **Postgres 15+** (uses `MERGE` for dbt incremental — 14 won't work)
- A Haravan shop with Omni API access (`client_id`, `client_secret`, `refresh_token`)

## Configure `.env`

```bash
HARAVAN_SHOP_DOMAIN=yourshop.myharavan.com
HARAVAN_CLIENT_ID=...
HARAVAN_CLIENT_SECRET=...
HARAVAN_ACCESS_TOKEN=...
HARAVAN_REFRESH_TOKEN=...
HARAVAN_RATE_LIMIT_PER_SEC=4         # PRD §4.2 — Haravan allows 4/s burst 80
HARAVAN_RATE_LIMIT_BURST=80

DATABASE_URL=postgresql://elt_user:elt_pass@localhost:5434/haravan

# Optional — leave blank to silence notifications
TELEGRAM_BOT_TOKEN=
TELEGRAM_CHAT_ID=

# 'console' for dev (colored), 'json' for prod
LOG_FORMAT=console
```

`chmod 600 .env` so OAuth secrets aren't world-readable.

## CLI verbs

| Verb | Purpose |
|------|---------|
| `init` | Apply `schema.sql` + `raw_tables.sql`. Idempotent. |
| `extract <domain>` | Extract+load one domain into `raw.haravan_<domain>`. |
| `transform [--select X]` | `dbt run` (transform only). |
| `test [--select X]` | `dbt test`. |
| `run-all` | Full pipeline: extract every domain → `dbt build` → notify. |
| `status` | Watermark + last run for every domain. |
| `validate <domain>` | Compare raw row count vs Haravan `/count.json`. Exit 1 on drift. |
| `notify <message>` | One-off Telegram message (debug). |

Common flags: `--verbose / --quiet`, `--json` (JSON logs), `--dry-run`, `--no-notify`,
`--mode full|incremental` (default incremental), `--since/--until` ISO datetime overrides.

## Domain coverage

P0 (MVP): orders · customers · products · locations
P1: inventory_adjustments · inventory_locations (snapshot) · custom_collections · smart_collections
P2 (post-MVP, phase-11): discounts · promotions · events

`run-all` honors `DOMAIN_ORDER` so dim tables land before the facts that join on them.

## Daily ops via cron

```bash
# Install:
sudo cp deploy/crontab.example /etc/cron.d/haravan-elt   # adjust paths first
sudo cp deploy/systemd/haravan-elt.{service,timer} /etc/systemd/system/   # alternative
sudo systemctl enable --now haravan-elt.timer

# Verify:
systemctl list-timers haravan-elt.timer
journalctl -u haravan-elt.service -n 50
```

The cron entrypoint (`scripts/run-daily.sh` → `python -m haravan_elt.cron_entry`) acquires
an `fcntl.flock` on `/var/lock/haravan-elt.lock`. A second invocation while the first is
still running exits **2** — no overlapping pipelines, no race on `meta.sync_state`.

## Telegram notifications

Pipeline emits 4 event types (PRD §4.6 FR-N2):

- ▶️ **start** — cron-only (manual runs are silent)
- ✅ **success** — extract counts + dbt summary
- ❌ **failure** — stage + truncated traceback
- ⚠️ **warning** — rate-limit streak >3 / dbt test failures

All sends are fail-soft: a Telegram outage never aborts the pipeline.

## Architecture

```
Haravan Omni API
        │  (httpx + tenacity + pyrate-limiter LeakyBucket)
        ▼
Python extractor (paginated GET, JSONB-as-blob)
        │  (psycopg3 ON CONFLICT DO UPDATE WHERE EXCLUDED.updated_at >= existing)
        ▼
raw.haravan_*  ←  meta.sync_state, meta.run_log
        │  (dbt-core + dbt-utils — staging views → marts incremental merge)
        ▼
marts.fct_orders / fct_inventory_snapshot / dim_*
```

See `docs/system-architecture.md` for component-level detail.

## Development

```bash
make test            # pytest with coverage gate (--cov-fail-under=70)
make lint            # ruff check + format check
make typecheck       # mypy strict
make dbt-build       # dbt build --select staging
make dbt-test
make db-up / db-down # Postgres dev container
```

Tests use `respx` for HTTP mocking and `pytest-vcr` cassettes (record_mode=none in CI →
no live API calls).

## Troubleshooting

| Symptom | Likely cause | Fix |
|---------|--------------|-----|
| `HaravanAuthError: refresh failed` | `.env` rotated tokens stale | Re-issue refresh token in Haravan admin; chmod 600 the new `.env` |
| `lock_blocked` (exit 2) | Previous cron run still going | Inspect `journalctl -u haravan-elt`; `kill <pid>` if hung; lockfile auto-releases |
| dbt test `relationships` failures | Out-of-order cron (orders before customers) | `haravan-elt run-all --mode full` — `DOMAIN_ORDER` re-establishes parity |
| `psql: connection refused` | Postgres container down | `make db-up` |
| Validate `MISMATCH` | Incremental drift / API caching | Re-run `extract <domain> --mode full --until <today>` |

## Disk hygiene

`meta.run_log` grows ~1 row per cron run. Monthly cleanup archives rows >90 days old:

```bash
0 3 1 * *  /opt/haravan-elt/scripts/cleanup-run-log.sh
```

(Already in `deploy/crontab.example`.)

## License

MIT. See `LICENSE`.
