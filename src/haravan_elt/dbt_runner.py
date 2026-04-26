"""Thin wrapper around `dbt.cli.main.dbtRunner` so the pipeline can capture
events / failures uniformly. Lazy-imports dbt to keep `haravan-elt --help`
fast (dbt's import surface is heavy).
"""

from __future__ import annotations

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
    summary = {
        "success": bool(result.success),
        "exception": str(result.exception) if result.exception else None,
        "args": full_args,
    }
    logger.info("dbt_done", **{k: v for k, v in summary.items() if k != "args"})
    return summary
