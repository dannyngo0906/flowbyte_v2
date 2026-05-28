"""Cron entrypoint — wraps `haravan-elt run-all` with overlap lock + JSON logs.

Used by `scripts/run-daily.sh` (and the systemd unit) so that:
  * a previous run still running → exit 2 (lock held)
  * any other failure          → exit code from `run-all`
  * logs are JSON regardless of dev console settings (cron output → log file
    or systemd journal; structured rendering eases grep/jq triage).
"""

from __future__ import annotations

import sys

import typer

from haravan_elt.cli import app
from haravan_elt.lockfile import DEFAULT_LOCK_PATH, LockBusyError, acquire_lock
from haravan_elt.logging import setup_logging

LOCK_BUSY_EXIT_CODE = 2


def main(argv: list[str] | None = None) -> int:
    """Run `app run-all --triggered-by cron` under an exclusive lock.

    `argv` is exposed so tests can drive the function without monkey-patching
    `sys.argv`. None means "use the default cron-style invocation".
    """
    setup_logging(json_output=True)
    args = argv if argv is not None else ["run-all", "--triggered-by", "cron"]
    try:
        with acquire_lock(DEFAULT_LOCK_PATH):
            # With standalone_mode=False, Click RETURNS the typer.Exit code as a
            # value instead of raising / calling sys.exit. Capturing that return
            # value is what propagates a dbt/pipeline failure as a non-zero
            # process exit; before, the discarded return let the service report
            # success while dbt tests failed. The `except` stays as a safety net
            # for click versions that re-raise.
            try:
                result = app(args, standalone_mode=False)
            except typer.Exit as exc:
                return int(exc.exit_code or 0)
            return int(result) if isinstance(result, int) else 0
    except LockBusyError:
        # Another instance holds the lock — exit 2 so the caller can detect.
        print("ERROR: another haravan-elt instance is running", file=sys.stderr)
        return LOCK_BUSY_EXIT_CODE


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
