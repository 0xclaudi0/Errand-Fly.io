"""Git history for each automation folder. state.json is not tracked."""

from __future__ import annotations

import logging
import subprocess
from pathlib import Path

GITIGNORE = "state.json\n__pycache__/\n"
GIT_IDENTITY = ("-c", "user.name=errand", "-c", "user.email=errand@localhost")

log = logging.getLogger(__name__)


def commit(folder: Path, message: str) -> bool:
    """Commit every change in `folder`. Return False if there was nothing to commit."""
    ignore = folder / ".gitignore"
    if not ignore.exists():
        ignore.write_text(GITIGNORE)
    if not (folder / ".git").exists():
        _git(folder, "init", "-q", "-b", "main")
    _git(folder, "add", "-A")
    if not _git(folder, "status", "--porcelain").strip():
        return False
    _git(folder, *GIT_IDENTITY, "commit", "-q", "-m", message)
    return True


def _git(folder: Path, *args: str) -> str:
    result = subprocess.run(
        ["git", *args], cwd=folder, capture_output=True, text=True, timeout=30, check=False
    )
    if result.returncode != 0:
        raise RuntimeError(f"git {args[0]} failed: {result.stderr.strip()}")
    return result.stdout
