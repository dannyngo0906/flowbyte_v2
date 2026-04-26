"""Typer CLI tests via `CliRunner`. Exit-code contract is the focus.

`init`, `status`, and parts of `extract` exercise real Postgres via the
`pg_clean` fixture. Other commands are tested without a DB by stubbing.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import patch

import pytest
from typer.testing import CliRunner

from haravan_elt.cli import app

runner = CliRunner()


@pytest.fixture
def cli_env(
    monkeypatch: pytest.MonkeyPatch,
    fake_settings_env: None,
    pg_clean: str,
) -> str:
    """Real DSN + fake Haravan creds in env for CLI invocations."""
    del fake_settings_env
    monkeypatch.setenv("DATABASE_URL", pg_clean)
    return pg_clean


def test_help_lists_all_commands() -> None:
    result = runner.invoke(app, ["--help"])
    assert result.exit_code == 0
    for verb in (
        "init",
        "extract",
        "transform",
        "test",
        "run-all",
        "status",
        "notify",
        "validate",
    ):
        assert verb in result.stdout


def test_init_idempotent(cli_env: str) -> None:
    del cli_env
    result = runner.invoke(app, ["init"])
    assert result.exit_code == 0, result.stderr
    # second invocation = idempotent (CREATE IF NOT EXISTS)
    result2 = runner.invoke(app, ["init"])
    assert result2.exit_code == 0


def test_unknown_domain_exit_2(cli_env: str) -> None:
    del cli_env
    result = runner.invoke(app, ["extract", "not-a-domain"])
    assert result.exit_code == 2
    assert "unknown domain" in result.stderr


def test_invalid_mode_exit_2(cli_env: str) -> None:
    del cli_env
    result = runner.invoke(app, ["extract", "orders", "--mode", "broken"])
    assert result.exit_code == 2
    assert "invalid --mode" in result.stderr


def test_invalid_iso_since_exit_2(cli_env: str) -> None:
    del cli_env
    result = runner.invoke(app, ["extract", "orders", "--since", "not-a-date"])
    assert result.exit_code == 2


def test_validate_unknown_domain_exit_2(cli_env: str) -> None:
    del cli_env
    result = runner.invoke(app, ["validate", "nope"])
    assert result.exit_code == 2
    assert "unknown domain" in result.stderr


def test_status_runs_against_empty_db(cli_env: str) -> None:
    del cli_env
    runner.invoke(app, ["init"])
    result = runner.invoke(app, ["status"])
    assert result.exit_code == 0
    assert "haravan-elt status" in result.stdout


def test_notify_disabled_returns_exit_2(cli_env: str, monkeypatch: pytest.MonkeyPatch) -> None:
    del cli_env
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "")
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "")
    result = runner.invoke(app, ["notify", "hello"])
    assert result.exit_code == 2


def test_transform_short_circuits_on_dbt_failure(cli_env: str) -> None:
    del cli_env
    fake_result = {"success": False, "exception": "boom", "args": []}
    with patch("haravan_elt.pipeline.run_dbt", return_value=fake_result):
        result = runner.invoke(app, ["transform"])
    assert result.exit_code == 1
    assert "dbt run failed" in result.stderr


def test_transform_success(cli_env: str) -> None:
    del cli_env
    fake_result: dict[str, Any] = {"success": True, "exception": None, "args": []}
    with patch("haravan_elt.pipeline.run_dbt", return_value=fake_result):
        result = runner.invoke(app, ["transform"])
    assert result.exit_code == 0


def test_run_all_propagates_exit_code(cli_env: str) -> None:
    del cli_env
    fake_failed_dbt = {"success": False, "exception": "downstream broke", "args": []}
    with patch("haravan_elt.pipeline.run_dbt", return_value=fake_failed_dbt):
        # No raw data → extract_all yields 0 rows but succeeds; dbt mock fails.
        result = runner.invoke(app, ["run-all", "--no-notify"])
    assert result.exit_code == 1
