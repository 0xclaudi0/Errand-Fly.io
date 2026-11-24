"""Owner settings (settings.json): who gets email, timezone, self-repair."""

from __future__ import annotations

import json
import re
from pathlib import Path
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

EMAIL = re.compile(r"^[^@\s]{1,64}@[^@\s]{1,190}\.[A-Za-z]{2,24}$")
KEYS = ("notify_email", "email_from", "timezone", "auto_repair")


def load(path: Path) -> dict:
    try:
        data = json.loads(path.read_text())
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def update(path: Path, changes: dict) -> dict:
    """Check every change, then write them all. Raise ValueError on the first bad one."""
    clean = {key: check(key, value) for key, value in changes.items()}
    merged = {**load(path), **clean}
    path.write_text(json.dumps(merged, indent=2))
    return merged


def check(key: str, value: object) -> str:
    if key not in KEYS:
        raise ValueError(f"unknown setting {key!r}")
    if not isinstance(value, str):
        raise ValueError(f"{key} must be a string")
    value = value.strip()
    if key == "notify_email" and not EMAIL.fullmatch(value):
        raise ValueError("notify_email must be one email address")
    if key == "email_from" and not (3 <= len(value) <= 200 and "@" in value):
        raise ValueError('email_from must look like "Name <you@example.com>"')
    if key == "timezone":
        try:
            ZoneInfo(value)
        except (ZoneInfoNotFoundError, ValueError) as err:
            raise ValueError(f"unknown timezone {value!r}") from err
    if key == "auto_repair" and value not in ("on", "off"):
        raise ValueError("auto_repair must be on or off")
    return value
