#!/usr/bin/env bash
# Cron entrypoint. Phase-10 wraps with fcntl.flock for overlap protection.
# Until then, this is a thin wrapper around `haravan-elt run-all`.

set -euo pipefail

PROJECT_DIR="${HARAVAN_ELT_HOME:-/opt/haravan-elt}"
cd "$PROJECT_DIR"

# shellcheck disable=SC1091
source .venv/bin/activate

exec haravan-elt run-all --mode incremental
