#!/usr/bin/env bash
# Daily backup of haravan + metabase_app DBs.
# Runs from cron as elt user. Uses libpq (postgresql-client-16 on host)
# connecting to the docker postgres @ 127.0.0.1:5433.
# Retention: keep last $RETENTION_DAYS days; older files auto-deleted.

set -euo pipefail

PROJECT_DIR="${HARAVAN_ELT_HOME:-/opt/haravan-elt}"
BACKUP_DIR="${BACKUP_DIR:-/home/elt/backups}"
RETENTION_DAYS="${RETENTION_DAYS:-7}"
TS=$(date -u +%Y%m%dT%H%M%SZ)

mkdir -p "$BACKUP_DIR"

# Read elt_user password from app .env
set -a; source "$PROJECT_DIR/.env"; set +a
PG_DSN="$DATABASE_URL"

# Dump analytics DB (custom format = pg_restore-compatible, compressed)
pg_dump -Fc --dbname "$PG_DSN" -f "$BACKUP_DIR/haravan-${TS}.dump"

# Dump Metabase metadata DB — connect with same elt_user (superuser in container)
PG_DSN_MB="${PG_DSN%/*}/metabase_app"
pg_dump -Fc --dbname "$PG_DSN_MB" -f "$BACKUP_DIR/metabase_app-${TS}.dump"

# Retention: delete dumps older than $RETENTION_DAYS days
find "$BACKUP_DIR" -maxdepth 1 -name '*.dump' -type f -mtime +"$RETENTION_DAYS" -delete

# Log final state
echo "[$(date -Iseconds)] backup ok: $(ls -1 "$BACKUP_DIR" | wc -l) files, total $(du -sh "$BACKUP_DIR" | cut -f1)"
