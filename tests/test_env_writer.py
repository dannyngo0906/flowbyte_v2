"""Atomic .env write-back tests.

`tmp_path` ensures we never touch the project's real .env. Atomicity is
covered by checking that an interrupted write leaves the original file intact.
"""

from __future__ import annotations

import os
import stat
from pathlib import Path
from unittest.mock import patch

import pytest

from haravan_elt.client.env_writer import write_env_atomic


def test_replaces_existing_keys(tmp_path: Path) -> None:
    env = tmp_path / ".env"
    env.write_text("# comment\nFOO=old-foo\nBAR=keep-bar\nHARAVAN_ACCESS_TOKEN=old-access\n")
    write_env_atomic(
        {"HARAVAN_ACCESS_TOKEN": "new-access", "FOO": "new-foo"},
        env_path=env,
    )
    text = env.read_text()
    assert "HARAVAN_ACCESS_TOKEN=new-access" in text
    assert "FOO=new-foo" in text
    assert "BAR=keep-bar" in text  # untouched
    assert "# comment" in text  # comments preserved
    assert "old-foo" not in text and "old-access" not in text


def test_appends_missing_keys(tmp_path: Path) -> None:
    env = tmp_path / ".env"
    env.write_text("EXISTING=keep\n")
    write_env_atomic({"NEW_KEY": "new-value"}, env_path=env)
    text = env.read_text()
    assert "EXISTING=keep" in text
    assert "NEW_KEY=new-value" in text


def test_creates_file_when_missing(tmp_path: Path) -> None:
    env = tmp_path / ".env"
    write_env_atomic({"K": "v"}, env_path=env)
    assert env.read_text() == "K=v\n"


def test_chmod_600_after_write(tmp_path: Path) -> None:
    env = tmp_path / ".env"
    env.write_text("X=1\n")
    write_env_atomic({"X": "2"}, env_path=env)
    mode = stat.S_IMODE(os.stat(env).st_mode)
    assert mode == 0o600


def test_atomic_failure_keeps_original(tmp_path: Path) -> None:
    env = tmp_path / ".env"
    original = "ORIG=keep\n"
    env.write_text(original)

    # Force os.replace to blow up after the tmp file is written.
    with (
        patch("haravan_elt.client.env_writer.os.replace", side_effect=OSError("boom")),
        pytest.raises(OSError, match="boom"),
    ):
        write_env_atomic({"ORIG": "modified"}, env_path=env)
    assert env.read_text() == original  # untouched
    # Tmp file cleaned up.
    leftovers = list(tmp_path.glob(".env.*"))
    assert leftovers == []
