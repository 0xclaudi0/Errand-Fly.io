"""Value types shared by the database, the dispatcher and the web layer."""

from __future__ import annotations

import re
from dataclasses import asdict, dataclass

ID_PATTERN = re.compile(r"^[a-z0-9][a-z0-9-]{0,47}$")
TRIGGERS = ("schedule", "webhook", "manual")
RUN_STATUSES = ("running", "ok", "error", "timeout", "interrupted")


def validate_id(value: str) -> str:
    """Automation ids become folder names, so allow only safe slugs."""
    if not ID_PATTERN.match(value or ""):
        raise ValueError(f"invalid automation id {value!r}: use a-z, 0-9 and '-', max 48 chars")
    return value


@dataclass(frozen=True)
class Automation:
    id: str
    name: str
    description: str
    trigger: str
    schedule: str | None
    hook_id: str | None
    enabled: bool
    timeout_s: int
    next_run_at: float | None
    created_at: float

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass(frozen=True)
class Run:
    id: int
    automation_id: str
    trigger: str
    status: str
    started_at: float
    finished_at: float | None
    exit_code: int | None
    output: str

    def to_dict(self) -> dict:
        return asdict(self)
