#!/usr/bin/env bash
# Cron entrypoint for the daily Haravan ELT pipeline.
#
# Wraps `python -m haravan_elt.cron_entry`, which acquires an exclusive
# fcntl lock and exits 2 if a prior run is still going. Activate the
# project venv explicitly so cron's PATH doesn't matter.

set -euo pipefail

PROJECT_DIR="${HARAVAN_ELT_HOME:-/opt/haravan-elt}"
cd "$PROJECT_DIR"

# shellcheck disable=SC1091
source .venv/bin/activate

exec python -m haravan_elt.cron_entry
