"""Pipeline orchestrator.

Phase-01 stub. Filled in phase-07 (`run_all` sequencing extractors → dbt).
"""

from __future__ import annotations


def run_all(mode: str = "incremental") -> int:
    """Run extract → load → transform → test. Returns process exit code."""
    raise NotImplementedError("Implemented in phase-07")
