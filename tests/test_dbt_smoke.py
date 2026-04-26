"""dbt project smoke test — guards against syntax / yaml regressions in CI.

Runs `dbt parse` (no DB needed) so this test passes even on machines without
the docker-compose Postgres up. The full `dbt build` integration test is
exercised by `make dbt-build` locally and the M6 hardening phase will gate
PRs on it explicitly.
"""

from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

import pytest

DBT_DIR = Path(__file__).resolve().parent.parent / "dbt"


def _dbt_available() -> bool:
    return shutil.which("dbt") is not None or (DBT_DIR.parent / ".venv/bin/dbt").exists()


@pytest.mark.skipif(not _dbt_available(), reason="dbt CLI not installed")
def test_dbt_parses_clean() -> None:
    """`dbt parse` exits 0 → all model SQL + yaml refs valid."""
    venv_dbt = DBT_DIR.parent / ".venv/bin/dbt"
    cmd = [str(venv_dbt) if venv_dbt.exists() else "dbt", "parse"]
    env = {**os.environ, "DBT_PROFILES_DIR": "."}
    result = subprocess.run(cmd, cwd=DBT_DIR, capture_output=True, text=True, env=env, check=False)
    assert result.returncode == 0, f"dbt parse failed:\n{result.stdout}\n{result.stderr}"
