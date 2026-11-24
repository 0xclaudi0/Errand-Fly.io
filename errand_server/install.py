"""Install (or update) an automation from a folder that contains main.py."""

from __future__ import annotations

import secrets
import shutil
import time
from pathlib import Path

from . import history
from .config import MAX_TIMEOUT_S, Paths
from .db import Repository
from .models import Automation, validate_id
from .schedule import next_run, parse_schedule

KEEP_ON_UPDATE = {"state.json", ".git", ".gitignore"}


def install(repo: Repository, paths: Paths, source: Path, *, automation_id: str, name: str,
            trigger: str, schedule: str | None = None, timeout_s: int = 120,
            description: str = "") -> Automation:
    validate_id(automation_id)
    if not (source / "main.py").is_file():
        raise ValueError(f"{source} has no main.py")
    if not 1 <= timeout_s <= MAX_TIMEOUT_S:
        raise ValueError(f"timeout must be 1..{MAX_TIMEOUT_S} seconds")
    now = time.time()
    next_at = _first_run(trigger, schedule, now)
    old = repo.get_automation(automation_id)

    target = paths.automation_dir(automation_id)
    _copy_code(source, target)
    history.commit(target, f"install {automation_id}" if old is None else f"update {automation_id}")

    hook_id = None
    if trigger == "webhook":
        hook_id = old.hook_id if old and old.hook_id else secrets.token_urlsafe(24)
    automation = Automation(
        id=automation_id, name=name, description=description, trigger=trigger,
        schedule=schedule if trigger == "schedule" else None, hook_id=hook_id,
        enabled=True, timeout_s=timeout_s, next_run_at=next_at,
        created_at=old.created_at if old else now,
    )
    repo.save_automation(automation)
    return automation


def _first_run(trigger: str, schedule: str | None, now: float) -> float | None:
    if trigger == "schedule":
        if not schedule:
            raise ValueError("a schedule trigger needs a schedule, for example 'daily 08:00'")
        return next_run(parse_schedule(schedule), now)
    if trigger in ("webhook", "manual"):
        return None
    raise ValueError(f"unknown trigger {trigger!r}")


def _copy_code(source: Path, target: Path) -> None:
    """Replace the code in `target` but keep state.json and the git history."""
    target.mkdir(parents=True, exist_ok=True)
    for item in target.iterdir():
        if item.name in KEEP_ON_UPDATE:
            continue
        shutil.rmtree(item) if item.is_dir() else item.unlink()
    ignore = shutil.ignore_patterns("__pycache__", "state.json", ".git")
    shutil.copytree(source, target, dirs_exist_ok=True, ignore=ignore)
