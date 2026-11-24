"""Git history for each automation folder. state.json is not tracked."""

from __future__ import annotations

import logging
import re
import shutil
import subprocess
from pathlib import Path

SHA = re.compile(r"^[0-9a-f]{7,40}$")
KEEP = {"state.json", ".git", ".gitignore"}
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


def versions(folder: Path, limit: int = 20) -> list[dict]:
    """Newest first: [{"sha", "message", "at"}]. Empty if the folder has no history."""
    if not (folder / ".git").exists():
        return []
    out = _git(folder, "log", f"-{limit}", "--format=%H%x1f%s%x1f%ct")
    rows = [line.split("\x1f") for line in out.splitlines() if line]
    return [{"sha": sha, "message": msg, "at": float(at)} for sha, msg, at in rows]


def roll_back(folder: Path, sha: str) -> str:
    """Make the files match version `sha` again, as a new commit. Return the new sha."""
    if not SHA.fullmatch(sha):
        raise ValueError("bad version id")
    full = _git(folder, "rev-parse", "--verify", f"{sha}^{{commit}}").strip()
    for item in folder.iterdir():
        if item.name not in KEEP:
            shutil.rmtree(item) if item.is_dir() else item.unlink()
    _git(folder, "checkout", full, "--", ".")
    if not commit(folder, f"roll back to {full[:7]}"):
        raise ValueError("that version is the current one")
    return _git(folder, "rev-parse", "HEAD").strip()


def _git(folder: Path, *args: str) -> str:
    result = subprocess.run(
        ["git", *args], cwd=folder, capture_output=True, text=True, timeout=30, check=False
    )
    if result.returncode != 0:
        raise RuntimeError(f"git {args[0]} failed: {result.stderr.strip()}")
    return result.stdout
