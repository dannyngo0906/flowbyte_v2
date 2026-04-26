---
title: "Haravan ELT Pipeline"
description: "Self-hosted Python 3.11 ELT: Haravan Omni API → PostgreSQL JSONB → dbt star schema, CLI + Telegram notifications, full M0–M7 scope (~8 weeks)."
name: Haravan ELT Pipeline
status: in-progress
priority: P1
effort: ~8 weeks
progress: 10/12 phases (~83%)
branch: feat/haravan-elt
date: 2026-04-26
created: 2026-04-26
last_synced: 2026-04-26
tags: [elt, dbt, postgres, haravan, python]
blockedBy: []
blocks: []
---

## Summary

`haravan-elt` extracts Haravan Omni API data into Postgres `raw.*` (JSONB) via a sync Python pipeline, then transforms into a Kimball star schema via dbt-core. CLI-only orchestration (`Typer`), Telegram notifications, cron-driven daily incremental, idempotent re-runs (high watermark + `ON CONFLICT`). Scope covers all P0/P1/P2 domains plus post-MVP polish (Discounts/Promotions/Events, refresh token auto-rotation, VN holidays seed, daily inventory snapshot, `validate` command).

## Tech Stack

See [`docs/tech-stack.md`](../../docs/tech-stack.md) — Python 3.11, httpx, tenacity, pyrate-limiter (LeakyBucket), Typer, psycopg[binary] 3.x, pydantic-settings, structlog, dbt-core 1.7+, dbt-postgres, dbt-utils, Postgres 15.

## Phase Status

| # | Title | Milestone | Status | Commits | Effort |
|---|-------|-----------|--------|---------|--------|
| 01 | Setup environment | M0 | ✅ completed | d9e4ee4 | 3d |
| 02 | Haravan client + auth | M1 | ✅ completed | 4fda5e6 + b951ace | 5d |
| 03 | Orders extract + load | M1 | ✅ completed | 6f0c08b + 6886181 | 3d |
| 04 | Multi-domain extractors (P0) | M2 | ✅ completed | f9500b9 + 541521c | 5d |
| 05 | dbt staging | M3 | ✅ completed | ec2194c + 4128184 | 5d |
| 06 | dbt marts (core) | M3 | ✅ completed | 5bb286f + 2209b91 | 5d |
| 07 | CLI + orchestrator | M4 | ✅ completed | ccc650a + 1fb0780 | 4d |
| 08 | Telegram notifications | M4 | ✅ completed | (this branch) | 2d |
| 09 | P1 domains + validate | M5 | ✅ completed | (this branch) | 5d |
| 10 | Hardening | M6 | ✅ completed | (this branch) | 5d |
| 11 | Post-MVP domains | M7 | pending | — | 5d |
| 12 | CI (GitHub Actions) | M6 (parallel) | pending | — | 1d |

**Progress:** 10 / 12 phases done. Verification: 121/121 pytest pass (89.85% coverage; coverage gate 70% enforced), dbt build 135/135 nodes, mypy clean. Lockfile + cron entry + systemd unit + run_log archive job + README rewrite shipped.

**Deferred items (committed but not strictly checked off in phase todo lists):**
- VCR cassette recording (phases 02/03/04) → used `respx` mocks; real cassettes need live Haravan token (phase-10 hardening)
- Manual end-to-end verification with live token (phases 03/04/06/07/08) → blocked on user obtaining Haravan API + Telegram credentials
- README skeleton (phase-01 last todo) → README.md rewrite is phase-10 deliverable

**Total:** ~48 dev-days ≈ 8 weeks (1 dev part-time).

## Dependency Graph

```
phase-01 (M0)
  ├─▶ phase-02 (M1 client) ─▶ phase-03 (M1 orders) ─▶ phase-04 (M2 multi-domain) ─▶ phase-05 (M3 staging) ─▶ phase-06 (M3 marts)
  │                                                                                                            └─▶ phase-07 (M4 CLI) ─▶ phase-08 (M4 Telegram) ─▶ phase-09 (M5 P1 + validate) ─▶ phase-10 (M6 hardening) ─▶ phase-11 (M7 post-MVP)
  └─▶ phase-12 (M6 CI, parallel — needs only pyproject.toml + tests folder skeleton)
```

## Key Risks (1-line)

- Haravan API schema drift → raw JSONB preserved + dbt tests catch early
- Refresh token rotation (30d rolling, every-use) → `.env` write-back required
- Rate limit leaky bucket (4 req/s burst 80) — PRD said 40/min, research-corrected
- Postgres 15+ MERGE required for dbt incremental — locked in tech stack
- Cron overlap → `fcntl.flock` with non-blocking acquire (exit 2 on lock)
- Telegram bot blocked → fail-soft (log only, no pipeline abort)

## Phase Files

- [phase-01-setup-environment.md](./phase-01-setup-environment.md)
- [phase-02-haravan-client-and-auth.md](./phase-02-haravan-client-and-auth.md)
- [phase-03-orders-extract-load.md](./phase-03-orders-extract-load.md)
- [phase-04-multi-domain-extractors.md](./phase-04-multi-domain-extractors.md)
- [phase-05-dbt-staging.md](./phase-05-dbt-staging.md)
- [phase-06-dbt-marts-core.md](./phase-06-dbt-marts-core.md)
- [phase-07-cli-and-orchestrator.md](./phase-07-cli-and-orchestrator.md)
- [phase-08-telegram-notifications.md](./phase-08-telegram-notifications.md)
- [phase-09-p1-domains-and-validate.md](./phase-09-p1-domains-and-validate.md)
- [phase-10-hardening.md](./phase-10-hardening.md)
- [phase-11-post-mvp-domains.md](./phase-11-post-mvp-domains.md)
- [phase-12-ci.md](./phase-12-ci.md)
