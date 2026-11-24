"""Schedule specs that the builder agent writes and people can read.

    every 30m | every 2h | every 1d
    daily 08:00 [Europe/London]
    weekly mon 08:00 [Europe/London]

The timezone is optional and defaults to UTC.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

DAYS = ("mon", "tue", "wed", "thu", "fri", "sat", "sun")
UNIT_SECONDS = {"m": 60, "h": 3600, "d": 86400}
INTERVAL = re.compile(r"^(\d{1,4})([mhd])$")
CLOCK = re.compile(r"^(\d{2}):(\d{2})$")


class ScheduleError(ValueError):
    pass


@dataclass(frozen=True)
class Schedule:
    kind: str  # "every" | "daily" | "weekly"
    interval_s: int = 0
    hour: int = 0
    minute: int = 0
    weekday: int = 0
    tz: str = "UTC"


def parse_schedule(spec: str) -> Schedule:
    words = (spec or "").split()
    kind = words[0].lower() if words else ""
    if kind == "every" and len(words) == 2:
        return _parse_every(words[1].lower())
    if kind == "daily" and len(words) in (2, 3):
        hour, minute = _parse_clock(words[1])
        return Schedule("daily", hour=hour, minute=minute, tz=_parse_tz(words[2:]))
    if kind == "weekly" and len(words) in (3, 4):
        day = words[1].lower()[:3]
        if day not in DAYS:
            raise ScheduleError(f"unknown weekday {words[1]!r}")
        hour, minute = _parse_clock(words[2])
        tz = _parse_tz(words[3:])
        return Schedule("weekly", hour=hour, minute=minute, weekday=DAYS.index(day), tz=tz)
    raise ScheduleError(f"cannot read schedule {spec!r}; try 'every 1h' or 'daily 08:00'")


def next_run(schedule: Schedule, after: float) -> float:
    """Return the first run time (epoch seconds) strictly after `after`."""
    if schedule.kind == "every":
        return after + schedule.interval_s
    local = datetime.fromtimestamp(after, ZoneInfo(schedule.tz))
    candidate = local.replace(hour=schedule.hour, minute=schedule.minute, second=0, microsecond=0)
    if schedule.kind == "weekly":
        candidate += timedelta(days=(schedule.weekday - candidate.weekday()) % 7)
        step = timedelta(days=7)
    else:
        step = timedelta(days=1)
    if candidate <= local:
        candidate += step
    return candidate.timestamp()


def _parse_every(token: str) -> Schedule:
    match = INTERVAL.match(token)
    if not match or int(match.group(1)) == 0:
        raise ScheduleError(f"bad interval {token!r}; use a number and m, h or d")
    return Schedule("every", interval_s=int(match.group(1)) * UNIT_SECONDS[match.group(2)])


def _parse_clock(token: str) -> tuple[int, int]:
    match = CLOCK.match(token)
    if not match:
        raise ScheduleError(f"bad time {token!r}; use HH:MM")
    hour, minute = int(match.group(1)), int(match.group(2))
    if hour > 23 or minute > 59:
        raise ScheduleError(f"bad time {token!r}")
    return hour, minute


def _parse_tz(rest: list[str]) -> str:
    if not rest:
        return "UTC"
    try:
        ZoneInfo(rest[0])
    except (ZoneInfoNotFoundError, ValueError) as err:
        raise ScheduleError(f"unknown timezone {rest[0]!r}") from err
    return rest[0]
