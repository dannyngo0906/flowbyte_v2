"""cron_entry.main — bootstrap exit-code contract.

Patches `acquire_lock` (no /var/lock permission needed) and `app` (no real
pipeline run). Verifies the cron mode passes correct args + maps lock
contention to exit 2.
"""

from __future__ import annotations

from contextlib import contextmanager
from typing import Any

import pytest
import typer

from haravan_elt import cron_entry
from haravan_elt.lockfile import LockBusyError


@contextmanager
def _ok_lock(path: str) -> Any:
    del path
    yield


@contextmanager
def _busy_lock(path: str) -> Any:
    del path
    raise LockBusyError("simulated contention")
    yield  # unreachable; satisfies the generator contract


def test_main_invokes_app_with_cron_args(monkeypatch: pytest.MonkeyPatch) -> None:
    """Default invocation forwards `run-all --triggered-by cron` to the CLI."""
    captured: list[list[str]] = []

    def _fake_app(args: list[str], **_: Any) -> None:
        captured.append(args)

    monkeypatch.setattr(cron_entry, "acquire_lock", _ok_lock)
    monkeypatch.setattr(cron_entry, "app", _fake_app)
    code = cron_entry.main()
    assert code == 0
    assert captured == [["run-all", "--triggered-by", "cron"]]


def test_main_translates_typer_exit_code(monkeypatch: pytest.MonkeyPatch) -> None:
    """Pipeline failure (typer.Exit(1)) → exit 1, not silent zero."""

    def _fake_app(args: list[str], **_: Any) -> None:
        del args
        raise typer.Exit(1)

    monkeypatch.setattr(cron_entry, "acquire_lock", _ok_lock)
    monkeypatch.setattr(cron_entry, "app", _fake_app)
    assert cron_entry.main() == 1


def test_main_returns_2_when_lock_busy(monkeypatch: pytest.MonkeyPatch) -> None:
    """A previous run still in progress → exit 2 without touching `app`."""
    called = False

    def _should_not_run(*_args: Any, **_kw: Any) -> None:
        nonlocal called
        called = True

    monkeypatch.setattr(cron_entry, "acquire_lock", _busy_lock)
    monkeypatch.setattr(cron_entry, "app", _should_not_run)
    assert cron_entry.main() == cron_entry.LOCK_BUSY_EXIT_CODE
    assert called is False


def test_main_accepts_explicit_argv(monkeypatch: pytest.MonkeyPatch) -> None:
    """Tests / scripts can drive non-cron commands through main()."""
    captured: list[list[str]] = []

    def _fake_app(args: list[str], **_: Any) -> None:
        captured.append(args)

    monkeypatch.setattr(cron_entry, "acquire_lock", _ok_lock)
    monkeypatch.setattr(cron_entry, "app", _fake_app)
    cron_entry.main(["status"])
    assert captured == [["status"]]
