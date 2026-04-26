"""POSIX advisory lock for cron-triggered runs.

`fcntl.flock(LOCK_EX | LOCK_NB)` returns immediately if another process
already holds the lock — perfect for cron overlap protection where we want
to abort, not queue. Lock auto-releases when the process exits (kernel
contract), so a crashed run doesn't strand the next slot.

PRD §10.3 / §12 risk: cron jobs occasionally overlap when the previous
run's slow page exceeds the schedule interval. Lockfile gives a clean
exit-code-2 signal instead of two pipelines stomping on `meta.sync_state`.
"""

from __future__ import annotations

import fcntl
import os
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

import structlog

logger = structlog.get_logger(__name__)

DEFAULT_LOCK_PATH = "/var/lock/haravan-elt.lock"


class LockBusyError(RuntimeError):
    """Another instance already holds the lock."""


@contextmanager
def acquire_lock(path: str = DEFAULT_LOCK_PATH) -> Iterator[None]:
    """Non-blocking exclusive flock on `path`.

    Raises `LockBusyError` if the lock is already held — the caller should
    translate this to exit code 2. Always releases on context exit (even on
    exceptions); the kernel also releases if the process dies abruptly.
    """
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    fh = p.open("w")
    try:
        try:
            fcntl.flock(fh.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            logger.error("lock_blocked", path=path)
            fh.close()
            raise LockBusyError(f"lock held by another process: {path}") from exc
        fh.write(str(os.getpid()))
        fh.flush()
        logger.info("lock_acquired", path=path, pid=os.getpid())
        try:
            yield
        finally:
            try:
                fcntl.flock(fh.fileno(), fcntl.LOCK_UN)
                logger.info("lock_released", path=path)
            except OSError as exc:
                # Closing the FD also releases — log and move on.
                logger.warning("lock_release_failed", path=path, error=str(exc))
    finally:
        if not fh.closed:
            fh.close()
