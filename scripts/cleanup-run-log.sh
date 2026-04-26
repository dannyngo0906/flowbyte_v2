#!/usr/bin/env bash
# Run the run_log archive job. Loads $DATABASE_URL from .env (cron has no
# shell environment by default).

set -euo pipefail

PROJECT_DIR="${HARAVAN_ELT_HOME:-/opt/haravan-elt}"
cd "$PROJECT_DIR"

# shellcheck disable=SC1091
set -a; source .env; set +a

exec psql "$DATABASE_URL" -v ON_ERROR_STOP=1 -f scripts/archive-run-log.sql
