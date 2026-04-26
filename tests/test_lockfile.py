"""acquire_lock — POSIX flock contract.

Use a tmp_path lockfile so tests don't need /var/lock perms or interfere
with a real cron run.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from haravan_elt.lockfile import LockBusyError, acquire_lock


def test_acquire_then_release_allows_reacquire(tmp_path: Path) -> None:
    lock_path = str(tmp_path / "elt.lock")
    with acquire_lock(lock_path):
        pass
    # After context exit the lock is freed — second acquire works.
    with acquire_lock(lock_path):
        pass


def test_concurrent_acquire_raises(tmp_path: Path) -> None:
    lock_path = str(tmp_path / "elt.lock")
    with acquire_lock(lock_path), pytest.raises(LockBusyError), acquire_lock(lock_path):
        pass


def test_lockfile_writes_pid(tmp_path: Path) -> None:
    """The lockfile body should hold the holder's PID for ops triage
    (`cat /var/lock/haravan-elt.lock`)."""
    import os

    lock_path = tmp_path / "elt.lock"
    with acquire_lock(str(lock_path)):
        # File flushed inside the context — read while held.
        contents = lock_path.read_text().strip()
    assert contents == str(os.getpid())


def test_acquire_creates_parent_dir(tmp_path: Path) -> None:
    lock_path = tmp_path / "nested" / "subdir" / "elt.lock"
    assert not lock_path.parent.exists()
    with acquire_lock(str(lock_path)):
        assert lock_path.exists()
    assert lock_path.parent.is_dir()
