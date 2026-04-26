"""Atomic .env writer used by OAuth refresh flow.

POSIX `os.replace` is atomic on the same filesystem → safe under cron concurrency
when paired with the cron-level flock added in phase-10. Preserves existing
comments and ordering; appends new keys at end.
"""

from __future__ import annotations

import os
import tempfile
from pathlib import Path


def write_env_atomic(updates: dict[str, str], env_path: Path | str = ".env") -> None:
    """Apply key=value updates to .env atomically (write tmp + rename + chmod 600).

    Existing keys are replaced in-place; new keys appended at end.
    Comments and blank lines are preserved.
    """
    path = Path(env_path)
    lines = path.read_text(encoding="utf-8").splitlines() if path.exists() else []
    seen: set[str] = set()
    out: list[str] = []
    for line in lines:
        stripped = line.lstrip()
        if "=" in line and not stripped.startswith("#"):
            key, _ = line.split("=", 1)
            key = key.strip()
            if key in updates:
                out.append(f"{key}={updates[key]}")
                seen.add(key)
                continue
        out.append(line)
    for key, value in updates.items():
        if key not in seen:
            out.append(f"{key}={value}")

    fd, tmp_name = tempfile.mkstemp(prefix=".env.", dir=str(path.parent or "."), text=True)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write("\n".join(out) + "\n")
        os.replace(tmp_name, path)
        os.chmod(path, 0o600)
    except Exception:
        # Clean up tmp on failure; original .env left untouched (atomicity guarantee).
        if Path(tmp_name).exists():
            os.unlink(tmp_name)
        raise
