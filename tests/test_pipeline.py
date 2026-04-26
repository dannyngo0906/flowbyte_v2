"""Pipeline orchestration tests — mock dbt + extractors so we never touch
the network or wait on a real dbt build."""

from __future__ import annotations

from typing import Any
from unittest.mock import MagicMock, patch

import pytest

from haravan_elt.config import load_settings
from haravan_elt.extractors.registry import DOMAIN_ORDER
from haravan_elt.notifications import Notifier
from haravan_elt.pipeline import Pipeline


@pytest.fixture
def pipe(fake_settings_env: None, pg_clean: str) -> Pipeline:
    del fake_settings_env, pg_clean  # fixtures populate env + truncate DB
    return Pipeline(load_settings())


def _attach_telegram(pipe: Pipeline) -> MagicMock:
    """Bind a fresh mocked TelegramClient + matching Notifier to the pipeline."""
    tg = MagicMock()
    tg.enabled = True
    tg.send.return_value = True
    pipe.telegram = tg
    pipe.notifier = Notifier(tg)
    return tg


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


def test_run_all_notifies_on_failure_with_stage(pipe: Pipeline) -> None:
    tg = _attach_telegram(pipe)
    with patch.object(pipe, "extract_all", side_effect=RuntimeError("boom")):
        pipe.run_all(no_notify=False)

    tg.send.assert_called_once()
    payload = tg.send.call_args.args[0]
    assert "Failure" in payload
    assert "extract" in payload
    assert "boom" in payload


def test_run_all_notifies_on_success(pipe: Pipeline) -> None:
    tg = _attach_telegram(pipe)
    fake_dbt = {"success": True, "exception": None, "args": [], "models_built": 3}
    with (
        patch.object(pipe, "extract_all", return_value={"orders": 5}),
        patch("haravan_elt.pipeline.run_dbt", return_value=fake_dbt),
    ):
        pipe.run_all(no_notify=False)

    tg.send.assert_called_once()
    payload = tg.send.call_args.args[0]
    assert "Daily Run" in payload
    assert "orders" in payload


def test_run_all_no_notify_does_not_send(pipe: Pipeline) -> None:
    tg = _attach_telegram(pipe)
    fake_dbt = {"success": True, "exception": None, "args": []}
    with (
        patch.object(pipe, "extract_all", return_value={}),
        patch("haravan_elt.pipeline.run_dbt", return_value=fake_dbt),
    ):
        pipe.run_all(no_notify=True)
    tg.send.assert_not_called()


def test_run_all_emits_start_only_on_cron_trigger(fake_settings_env: None, pg_clean: str) -> None:
    del fake_settings_env, pg_clean
    cron_pipe = Pipeline(load_settings(), triggered_by="cron")
    tg = _attach_telegram(cron_pipe)
    fake_dbt = {"success": True, "exception": None, "args": []}
    with (
        patch.object(cron_pipe, "extract_all", return_value={}),
        patch("haravan_elt.pipeline.run_dbt", return_value=fake_dbt),
    ):
        cron_pipe.run_all(no_notify=False)
    # 2 sends: start + success.
    assert tg.send.call_count == 2
    assert "Daily Run start" in tg.send.call_args_list[0].args[0]


def test_run_all_warns_on_rate_limit_streak(pipe: Pipeline) -> None:
    tg = _attach_telegram(pipe)
    pipe.client.max_consecutive_429 = 5  # type: ignore[attr-defined]
    fake_dbt = {"success": True, "exception": None, "args": []}
    with (
        patch.object(pipe, "extract_all", return_value={}),
        patch("haravan_elt.pipeline.run_dbt", return_value=fake_dbt),
    ):
        pipe.run_all(no_notify=False)
    # warning + success.
    assert tg.send.call_count == 2
    warning = tg.send.call_args_list[0].args[0]
    assert "Warning" in warning
    assert "5 consecutive" in warning


def test_run_all_warns_on_dbt_test_failures(pipe: Pipeline) -> None:
    tg = _attach_telegram(pipe)
    fake_dbt = {"success": True, "exception": None, "args": [], "tests_failed": 2}
    with (
        patch.object(pipe, "extract_all", return_value={}),
        patch("haravan_elt.pipeline.run_dbt", return_value=fake_dbt),
    ):
        pipe.run_all(no_notify=False)
    assert tg.send.call_count == 2
    warning = tg.send.call_args_list[0].args[0]
    assert "2 dbt tests failed" in warning


def test_run_all_failure_does_not_abort_when_notifier_raises(pipe: Pipeline) -> None:
    """Even if the notifier itself blows up, the pipeline still returns 1
    (FR-N4 fail-soft)."""
    tg = _attach_telegram(pipe)
    tg.send.side_effect = RuntimeError("telegram down")
    with patch.object(pipe, "extract_all", side_effect=RuntimeError("boom")):
        code = pipe.run_all(no_notify=False)
    assert code == 1
