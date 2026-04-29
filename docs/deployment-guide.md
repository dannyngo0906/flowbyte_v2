# Deployment Guide — haravan-elt (flowbyte)

**Target VPS**: `vpsn8n` (160.25.81.157, Ubuntu 22.04, shared with n8n + nocodb + qdrant + browserless + portainer + nginx-proxy-manager)
**Public URL**: https://metabase.salesai.vn (SSL via NPM Let's Encrypt)
**Daily run**: 02:00 GMT+7 via `haravan-elt.timer` systemd unit
**Last migrated**: 2026-04-29 (fresh Haravan API backfill, no data transfer from old VPS)

## Topology

```
Internet
   │
   ▼ HTTPS (443) — Let's Encrypt SSL
nginx-proxy-manager (Docker, host network: 80/81/443)
   │  proxy_pass http://haravan_metabase:3000
   ▼ haravan-elt-stack_haravan_net (bridge)
┌──────────────────────────────────────────────┐
│ haravan_metabase   metabase/metabase:latest  │
│   listens 3000 in net, 127.0.0.1:3001 host   │
│   reads metabase_app DB on haravan_pg        │
│   queries staging_marts.* via metabase_reader│
├──────────────────────────────────────────────┤
│ haravan_pg         postgres:16-alpine        │
│   listens 5432 in net, 127.0.0.1:5433 host   │
│   DB haravan      (analytics — raw/staging/marts) │
│   DB metabase_app (questions/dashboards/users)    │
└──────────────────────────────────────────────┘
   ▲
   │ libpq @ localhost:5433 (DATABASE_URL)
host /opt/haravan-elt/.venv (Python 3.11, systemd timer)
   ▲
   │ Haravan Omni API (4/s rate limit)
shop apis.haravan.com
```

## Files & Locations

| Path | Owner | Purpose |
|---|---|---|
| `/opt/haravan-elt/` | `elt:elt` | Project source + venv + .env |
| `/opt/haravan-elt/.env` | `elt:elt 600` | Secrets (DATABASE_URL, HARAVAN_*, TELEGRAM_*) |
| `/home/elt/.dbt/profiles.yml` | `elt:elt 600` | dbt connection profile (`haravan_elt`) |
| `/opt/haravan-elt-stack/` | `root:root` | docker-compose stack |
| `/opt/haravan-elt-stack/.env` | `root:root 600` | Container passwords |
| `/etc/systemd/system/haravan-elt.{service,timer}` | root | Daily 02:00 GMT+7 run |
| `/etc/cron.d/haravan-elt` | root | Monthly cleanup + daily backups |
| `/home/elt/backups/` | `elt:elt` | pg_dump backups (retention 7 days) |
| `/var/log/haravan-elt*.log` | `elt:elt` | Run logs |

## Operate

### View daily run status
```bash
systemctl status haravan-elt.service
systemctl list-timers haravan-elt.timer
journalctl -u haravan-elt.service -n 100
```

### Manual trigger
```bash
sudo systemctl start haravan-elt.service
sudo journalctl -u haravan-elt.service -f
```

### Run a single domain extract
```bash
sudo -u elt -i bash -c '
  cd /opt/haravan-elt
  source .venv/bin/activate
  set -a; source .env; set +a
  haravan-elt extract orders --mode incremental
'
```

### Connect to Postgres (read-only as metabase_reader)
```bash
docker exec -it haravan_pg psql -U metabase_reader -d haravan
```

### Restore from backup
```bash
# Stop metabase to release connections
docker compose -f /opt/haravan-elt-stack/docker-compose.yml stop metabase

# Restore haravan
docker exec haravan_pg pg_restore -U elt_user -d haravan --clean --if-exists \
  --no-owner --role=elt_user /home/elt/backups/haravan-<TS>.dump

# Restore metabase_app
docker exec haravan_pg psql -U elt_user -d postgres -c "DROP DATABASE metabase_app;"
docker exec haravan_pg psql -U elt_user -d postgres -c "CREATE DATABASE metabase_app OWNER metabase_app;"
docker exec haravan_pg pg_restore -U elt_user -d metabase_app --no-owner \
  --role=metabase_app /home/elt/backups/metabase_app-<TS>.dump

docker compose -f /opt/haravan-elt-stack/docker-compose.yml start metabase
```

## Cron Schedule

| Time (GMT+7) | UTC | Job |
|---|---|---|
| Daily 02:00 | 19:00 prev | `haravan-elt.timer` → `run-daily.sh` (extract + dbt build) |
| Daily 04:00 | 21:00 prev | `backup-postgres.sh` (pg_dump haravan + metabase_app, retention 7d) |
| Monthly 03:00 (1st) | 20:00 last day prev month | `cleanup-run-log.sh` (archive >90d run_log rows) |

## Permissions Model

| Role | Privilege |
|---|---|
| `elt_user` | Superuser within container — owns all schemas, runs ETL + dbt |
| `metabase_app` | Owns DB `metabase_app` only — Metabase backend |
| `metabase_reader` | USAGE+SELECT on `staging_marts` (marts) and `raw` (for `payload->>'user_id'` joins). Default privileges auto-extend to new tables. |

## Network/Security

- **Postgres bind**: `127.0.0.1:5433` only — never exposed publicly
- **Metabase bind**: `127.0.0.1:3001` only — public access via NPM SSL
- **NPM container** is connected to `haravan-elt-stack_haravan_net` so it can reach `haravan_metabase:3000` by hostname
- **Secrets**: `.env` files chmod 600
- **Postgres passwords**: Auto-generated 32-char random base64 (rotated from old VPS)

## Health Check Quick Reference

```bash
# Container health
docker compose -f /opt/haravan-elt-stack/docker-compose.yml ps

# Public URL
curl -I https://metabase.salesai.vn/api/health

# dbt connectivity
sudo -u elt -i bash -c 'cd /opt/haravan-elt/dbt && source ../.venv/bin/activate && dbt debug'

# Latest pipeline run
docker exec haravan_pg psql -U elt_user -d haravan -c \
  "SELECT domain, status, ended_at - started_at AS duration FROM meta.run_log ORDER BY started_at DESC LIMIT 12;"

# Backup files
ls -lh /home/elt/backups/
```

## Rollback to old VPS

Old VPS at `103.140.249.215` is **not deleted** — timer disabled + Metabase container stopped, but data preserved at `/opt/haravan-elt`, `/var/lib/postgresql`, and Docker volume `metabase_metabase_data`. To rollback:

```bash
ssh vmadmin@103.140.249.215
sudo systemctl enable --now haravan-elt.timer
sudo docker start metabase
```

Then disable target timer to avoid dual run.

## Known Issues

- **4 dbt data quality test failures** (orphan refs from deleted variants/products). Marts still build successfully. Pre-existing data quality issue from Haravan source, not migration bug.
- **dbt build exit code !=0** when tests fail → systemd flags service as failed even though pipeline ran. Acceptable for now; track via Telegram notifications for hard failures.
