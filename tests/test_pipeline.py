"""Pipeline orchestration tests — mock dbt + extractors so we never touch
the network or wait on a real dbt build."""

from __future__ import annotations

from typing import Any
from unittest.mock import MagicMock, patch

import pytest

from haravan_elt.config import load_settings
from haravan_elt.extractors.registry import DOMAIN_ORDER
from haravan_elt.pipeline import Pipeline


@pytest.fixture
def pipe(fake_settings_env: None, pg_clean: str) -> Pipeline:
    del fake_settings_env, pg_clean  # fixtures populate env + truncate DB
    return Pipeline(load_settings())


def test_extract_all_runs_in_domain_order(pipe: Pipeline) -> None:
    """All extractors invoked in DOMAIN_ORDER, never duplicating a domain."""
    calls: list[str] = []

    def _stub(domain: str, **_: Any) -> int:
        calls.append(domain)
        return 0

    with patch.object(pipe, "extract_one", side_effect=_stub):
        result = pipe.extract_all()

    assert calls == DOMAIN_ORDER
    assert set(result) == set(DOMAIN_ORDER)


def test_extract_all_aborts_on_first_error(pipe: Pipeline) -> None:
    def _stub(domain: str, **_: Any) -> int:
        if domain == "customers":
            raise RuntimeError("boom")
        return 0

    with (
        patch.object(pipe, "extract_one", side_effect=_stub),
        pytest.raises(RuntimeError, match="boom"),
    ):
        pipe.extract_all()


def test_run_all_invokes_dbt_build_after_extracts(pipe: Pipeline) -> None:
    fake_dbt = {"success": True, "exception": None, "args": []}
    with (
        patch.object(pipe, "extract_all", return_value={"orders": 2}) as ext,
        patch("haravan_elt.pipeline.run_dbt", return_value=fake_dbt) as dbt,
    ):
        code = pipe.run_all(no_notify=True)
    assert code == 0
    ext.assert_called_once()
    dbt.assert_called_once()
    assert dbt.call_args.args[0] == ["build"]


def test_run_all_returns_1_when_dbt_fails(pipe: Pipeline) -> None:
    fake_dbt = {"success": False, "exception": "syntax error", "args": []}
    with (
        patch.object(pipe, "extract_all", return_value={}),
        patch("haravan_elt.pipeline.run_dbt", return_value=fake_dbt),
    ):
        code = pipe.run_all(no_notify=True)
    assert code == 1


def test_run_all_returns_1_when_extract_raises(pipe: Pipeline) -> None:
    with patch.object(pipe, "extract_all", side_effect=RuntimeError("api down")):
        code = pipe.run_all(no_notify=True)
    assert code == 1


def test_run_all_notifies_on_failure(pipe: Pipeline) -> None:
    pipe.telegram = MagicMock()
    pipe.telegram.enabled = True

    with patch.object(pipe, "extract_all", side_effect=RuntimeError("boom")):
        pipe.run_all(no_notify=False)

    pipe.telegram.send.assert_called_once()
    payload = pipe.telegram.send.call_args.args[0]
    assert "FAILED" in payload
    assert "boom" in payload


def test_run_all_notifies_on_success(pipe: Pipeline) -> None:
    pipe.telegram = MagicMock()
    pipe.telegram.enabled = True
    fake_dbt = {"success": True, "exception": None, "args": []}
    with (
        patch.object(pipe, "extract_all", return_value={"orders": 5}),
        patch("haravan_elt.pipeline.run_dbt", return_value=fake_dbt),
    ):
        pipe.run_all(no_notify=False)

    pipe.telegram.send.assert_called_once()
    payload = pipe.telegram.send.call_args.args[0]
    assert "Run OK" in payload
    assert "orders" in payload


def test_run_all_no_notify_does_not_send(pipe: Pipeline) -> None:
    pipe.telegram = MagicMock()
    fake_dbt = {"success": True, "exception": None, "args": []}
    with (
        patch.object(pipe, "extract_all", return_value={}),
        patch("haravan_elt.pipeline.run_dbt", return_value=fake_dbt),
    ):
        pipe.run_all(no_notify=True)
    pipe.telegram.send.assert_not_called()
