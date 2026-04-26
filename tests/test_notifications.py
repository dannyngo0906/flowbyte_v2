"""Notifier tests — verify event routing, formatting, and fail-soft contract.

Notifier wraps TelegramClient; here we mock the client and assert on `send`
calls so tests are network-free and inspect the rendered text.
"""

from __future__ import annotations

from unittest.mock import MagicMock

from haravan_elt.notifications import ERROR_MESSAGE_MAX_CHARS, Notifier


def _mk_client(enabled: bool = True) -> MagicMock:
    tg = MagicMock()
    tg.enabled = enabled
    tg.send.return_value = True
    return tg


# ---------------------------------------------------------------- start


def test_start_silent_when_manual_trigger() -> None:
    tg = _mk_client()
    n = Notifier(tg)
    assert n.start(mode="incremental", triggered_by="manual") is False
    tg.send.assert_not_called()


def test_start_sends_when_cron_trigger() -> None:
    tg = _mk_client()
    n = Notifier(tg)
    n.start(mode="incremental", triggered_by="cron")
    tg.send.assert_called_once()
    text = tg.send.call_args.args[0]
    assert "Daily Run start" in text
    assert "incremental" in text


def test_start_no_op_when_telegram_none() -> None:
    n = Notifier(None)
    assert n.start("incremental", "cron") is False


def test_start_no_op_when_disabled_client() -> None:
    tg = _mk_client(enabled=False)
    n = Notifier(tg)
    n.start("incremental", "cron")
    tg.send.assert_not_called()


# ---------------------------------------------------------------- success


def test_success_includes_extract_counts_and_dbt_summary() -> None:
    tg = _mk_client()
    n = Notifier(tg)
    n.success(
        ext_summary={"orders": 12345, "customers": 0},
        dbt_summary={"models_built": 7, "tests_passed": 42, "tests_total": 45},
        duration_sec=12.345,
    )
    text = tg.send.call_args.args[0]
    assert "Daily Run" in text
    assert "12.3s" in text
    assert "12,345" in text  # thousands separator
    assert "orders" in text
    assert "customers" in text
    assert "7 models" in text
    assert "42/45" in text


def test_success_falls_back_to_dbt_status_when_no_run_results() -> None:
    tg = _mk_client()
    n = Notifier(tg)
    n.success(ext_summary={}, dbt_summary={"success": True}, duration_sec=1.0)
    text = tg.send.call_args.args[0]
    assert "dbt: OK" in text


# ---------------------------------------------------------------- failure


def test_failure_truncates_traceback_to_1000_chars() -> None:
    tg = _mk_client()
    n = Notifier(tg)
    huge = "z" * 5000
    n.failure(stage="dbt", exc=RuntimeError(huge))
    text = tg.send.call_args.args[0]
    assert "Failure" in text
    assert "dbt" in text
    # 'z' appears only inside the payload — surrounding markup uses none.
    assert text.count("z") == ERROR_MESSAGE_MAX_CHARS


def test_failure_strips_backticks_so_code_block_is_safe() -> None:
    tg = _mk_client()
    n = Notifier(tg)
    n.failure(stage="dbt", exc=RuntimeError("err with ``` inside"))
    text = tg.send.call_args.args[0]
    # Only the outer fence's two ``` should remain.
    assert text.count("```") == 2


# ---------------------------------------------------------------- warning


def test_warning_includes_reason_and_optional_details() -> None:
    tg = _mk_client()
    n = Notifier(tg)
    n.warning("rate limit hit 5 times", details="lower the throttle")
    text = tg.send.call_args.args[0]
    assert "Warning" in text
    assert "rate limit hit 5 times" in text
    assert "lower the throttle" in text


def test_warning_omits_details_block_when_empty() -> None:
    tg = _mk_client()
    n = Notifier(tg)
    n.warning("just a heads-up")
    text = tg.send.call_args.args[0]
    assert "just a heads-up" in text
    # Two lines: header + reason. No third line.
    assert text.count("\n") == 1


# ---------------------------------------------------------------- fail-soft


def test_send_swallows_unexpected_exception() -> None:
    """If TelegramClient.send leaks an exception (shouldn't, but guard
    anyway), Notifier swallows it and returns False (FR-N4)."""
    tg = _mk_client()
    tg.send.side_effect = RuntimeError("boom")
    n = Notifier(tg)
    assert n.warning("anything") is False
