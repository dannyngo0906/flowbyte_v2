"""Thin wrapper around `dbt.cli.main.dbtRunner` so the pipeline can capture
events / failures uniformly. Lazy-imports dbt to keep `haravan-elt --help`
fast (dbt's import surface is heavy).
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import structlog

logger = structlog.get_logger(__name__)

# Default project layout: dbt/ at repo root.
DEFAULT_DBT_PROJECT_DIR = Path(__file__).resolve().parent.parent.parent / "dbt"


def run_dbt(
    args: list[str],
    *,
    project_dir: Path | str = DEFAULT_DBT_PROJECT_DIR,
    profiles_dir: Path | str | None = None,
) -> dict[str, Any]:
    """Invoke dbt and return a JSON-serializable summary.

    profiles_dir defaults to project_dir (matches our `dbt/profiles.yml` layout).
    Returns: {"success": bool, "exception": str | None, "args": list[str]}.
    """
    from dbt.cli.main import dbtRunner

    pdir = str(Path(project_dir).resolve())
    pfdir = str(Path(profiles_dir or project_dir).resolve())

    full_args = list(args) + ["--project-dir", pdir, "--profiles-dir", pfdir]

    logger.info("dbt_invoke", args=full_args)
    runner = dbtRunner()
    result = runner.invoke(full_args)
    summary: dict[str, Any] = {
        "success": bool(result.success),
        "exception": str(result.exception) if result.exception else None,
        "args": full_args,
    }
    summary.update(parse_run_results(Path(pdir) / "target"))
    logger.info("dbt_done", **{k: v for k, v in summary.items() if k != "args"})
    return summary


def parse_run_results(target_dir: Path | str) -> dict[str, int]:
    """Read dbt's `target/run_results.json` and return aggregate counts.

    Empty dict if the file is missing or malformed — pipeline must not abort
    on summary parsing failures, since the dbt run itself may have succeeded.
    Used by `Notifier.success` to enrich the Telegram message.
    """
    p = Path(target_dir) / "run_results.json"
    if not p.exists():
        return {}
    try:
        data = json.loads(p.read_text())
    except (OSError, ValueError) as exc:
        logger.warning("dbt_run_results_unreadable", error=str(exc), path=str(p))
        return {}
    results = data.get("results") or []
    models = [r for r in results if str(r.get("unique_id", "")).startswith("model.")]
    tests = [r for r in results if str(r.get("unique_id", "")).startswith("test.")]
    return {
        "models_built": sum(1 for r in models if r.get("status") == "success"),
        "tests_total": len(tests),
        "tests_passed": sum(1 for r in tests if r.get("status") == "pass"),
        "tests_failed": sum(1 for r in tests if r.get("status") == "fail"),
    }
