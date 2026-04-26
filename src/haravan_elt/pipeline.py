"""Pipeline orchestrator.

Single class encapsulating the extract → load → transform → test sequence.
Reused by both CLI verbs (`extract`, `run-all`) and tests so behavior stays
consistent.
"""

from __future__ import annotations

import time
import uuid
from datetime import datetime
from typing import Any

import structlog

from haravan_elt.client.exceptions import HaravanAuthError
from haravan_elt.client.haravan import HaravanClient
from haravan_elt.client.telegram import TelegramClient
from haravan_elt.config import Settings
from haravan_elt.dbt_runner import run_dbt
from haravan_elt.extractors.registry import DOMAIN_ORDER, EXTRACTORS
from haravan_elt.loaders.postgres import PostgresLoader
from haravan_elt.meta.state import StateManager
from haravan_elt.notifications import Notifier

logger = structlog.get_logger(__name__)

# Threshold for emitting a rate-limit Telegram warning. Below this, retries
# are considered normal noise.
RATE_LIMIT_WARN_THRESHOLD = 3


class Pipeline:
    """Composes Haravan client + Postgres loader + state + dbt invocation."""

    def __init__(
        self,
        settings: Settings,
        *,
        telegram: TelegramClient | None = None,
        triggered_by: str = "manual",
        client: HaravanClient | None = None,
    ) -> None:
        self.settings = settings
        self.triggered_by = triggered_by
        dsn = settings.database.database_url.get_secret_value()
        self.client = client or HaravanClient(settings)
        self.loader = PostgresLoader(dsn)
        self.state = StateManager(dsn)
        self.telegram = telegram
        self.notifier = Notifier(telegram)

    def close(self) -> None:
        self.client.close()

    def __enter__(self) -> Pipeline:
        return self

    def __exit__(self, *_: object) -> None:
        self.close()

    # ------------------------------------------------------------------ extract

    def extract_one(
        self,
        domain: str,
        mode: str = "incremental",
        *,
        since: datetime | None = None,
        until: datetime | None = None,
        dry_run: bool = False,
    ) -> int:
        """Run `extract → load` for a single domain, with run_log + watermark
        bookkeeping. Returns rows ingested. Raises on failure (caller handles
        exit code)."""
        if domain not in EXTRACTORS:
            raise ValueError(f"unknown domain: {domain}")
        run_id = uuid.uuid4()
        # `bound_contextvars` (vs `bind_contextvars`) scopes log context to this
        # domain only — prevents the next iteration of `extract_all` from logging
        # under a stale run_id/domain.
        with structlog.contextvars.bound_contextvars(run_id=str(run_id), domain=domain, mode=mode):
            extractor = EXTRACTORS[domain](self.client, self.loader, self.state, run_id)
            self.state.start_run(run_id, domain, mode, triggered_by=self.triggered_by)
            try:
                rows, max_ts = extractor.idempotent_load(mode, since=since, until=until)
                if max_ts is not None and not dry_run and extractor.supports_incremental:
                    self.state.update_watermark(domain, max_ts, run_id)
                self.state.end_run(run_id, rows, "success")
                return rows
            except Exception as exc:
                self.state.end_run(run_id, 0, "failed", str(exc))
                raise

    def extract_all(
        self,
        mode: str = "incremental",
        *,
        since: datetime | None = None,
        until: datetime | None = None,
        dry_run: bool = False,
    ) -> dict[str, int]:
        """Run extractors in DOMAIN_ORDER. Stops at first error (idempotent
        re-run is the recovery path). Returns `{domain: rows}` for those
        completed successfully."""
        results: dict[str, int] = {}
        for domain in DOMAIN_ORDER:
            rows = self.extract_one(domain, mode=mode, since=since, until=until, dry_run=dry_run)
            results[domain] = rows
        return results

    # ------------------------------------------------------------------ transform

    def transform(self, *, select: str | None = None) -> dict[str, Any]:
        """`dbt run` over the project. Returns the run_dbt summary dict."""
        args = ["run"]
        if select is not None:
            args += ["--select", select]
        return run_dbt(args)

    def dbt_test(self, *, select: str | None = None) -> dict[str, Any]:
        args = ["test"]
        if select is not None:
            args += ["--select", select]
        return run_dbt(args)

    def dbt_build(self, *, select: str | None = None) -> dict[str, Any]:
        args = ["build"]
        if select is not None:
            args += ["--select", select]
        return run_dbt(args)

    # ------------------------------------------------------------------ run-all

    def run_all(
        self,
        mode: str = "incremental",
        *,
        since: datetime | None = None,
        until: datetime | None = None,
        dry_run: bool = False,
        no_notify: bool = False,
    ) -> int:
        """Full pipeline: extract-all → dbt build → notify. Returns exit code.

        Notifier wiring (PRD §4.6 FR-N2):
          - start    → cron only (manual would spam)
          - warning  → rate-limit streak or dbt test failures, BEFORE success
          - success  → final status with extract counts + dbt summary
          - failure  → any exception aborts and notifies with stage + traceback
        """
        notifier = self._effective_notifier(no_notify)
        start = time.time()
        notifier.start(mode, self.triggered_by)
        ext_summary: dict[str, int] = {}
        stage = "extract"
        try:
            ext_summary = self.extract_all(mode=mode, since=since, until=until, dry_run=dry_run)
            stage = "dbt_build"
            dbt_summary = self.dbt_build()
            duration = time.time() - start
            self._notify_warnings(notifier, dbt_summary)
            if not dbt_summary.get("success"):
                raise RuntimeError(f"dbt build failed: {dbt_summary.get('exception') or 'unknown'}")
            notifier.success(ext_summary, dbt_summary, duration)
            return 0
        except Exception as exc:
            logger.error("run_all_failed", stage=stage, error=str(exc))
            # Auth refresh failure is the operator's most urgent signal — emit
            # a warning before the failure event so it shows even if the
            # generic failure render obscures the auth context.
            if isinstance(exc, HaravanAuthError):
                notifier.warning(
                    "Haravan token refresh failed",
                    "Re-issue refresh token in the Haravan admin and update .env.",
                )
            notifier.failure(stage=stage, exc=exc)
            return 1

    # ------------------------------------------------------------------ helpers

    def _effective_notifier(self, no_notify: bool) -> Notifier:
        """Return a no-op notifier when `--no-notify` is set, else the real
        one bound at construction time."""
        return Notifier(None) if no_notify else self.notifier

    def _notify_warnings(self, notifier: Notifier, dbt_summary: dict[str, Any]) -> None:
        """Emit non-fatal warnings BEFORE the final success/failure event so
        they're visible even if the run later fails (Q2 in plan)."""
        peak = getattr(self.client, "max_consecutive_429", 0)
        if peak > RATE_LIMIT_WARN_THRESHOLD:
            notifier.warning(
                f"Rate limit hit {peak} consecutive times",
                "Consider lowering HARAVAN_RATE_LIMIT_PER_SEC",
            )
        tests_failed = dbt_summary.get("tests_failed", 0)
        if tests_failed:
            notifier.warning(f"{tests_failed} dbt tests failed")
