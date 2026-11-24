from datetime import datetime
from zoneinfo import ZoneInfo

import pytest

from errand_server.schedule import ScheduleError, next_run, parse_schedule

UTC = ZoneInfo("UTC")
LONDON = ZoneInfo("Europe/London")


def ts(year, month, day, hour=0, minute=0, tz=UTC) -> float:
    return datetime(year, month, day, hour, minute, tzinfo=tz).timestamp()


@pytest.mark.parametrize(
    "spec, seconds",
    [("every 5m", 300), ("every 2h", 7200), ("every 1d", 86400), ("EVERY 30M", 1800)],
)
def test_every_adds_the_interval(spec, seconds):
    schedule = parse_schedule(spec)
    assert next_run(schedule, 1000.0) == 1000.0 + seconds


def test_daily_runs_later_today_when_time_is_ahead():
    schedule = parse_schedule("daily 08:00")
    assert next_run(schedule, ts(2026, 11, 24, 6)) == ts(2026, 11, 24, 8)


def test_daily_runs_tomorrow_when_time_has_passed():
    schedule = parse_schedule("daily 08:00")
    assert next_run(schedule, ts(2026, 11, 24, 8)) == ts(2026, 11, 25, 8)


def test_daily_uses_the_given_timezone():
    schedule = parse_schedule("daily 08:00 Europe/London")
    after = ts(2026, 7, 1, 0)  # London is UTC+1 in summer
    assert next_run(schedule, after) == ts(2026, 7, 1, 8, tz=LONDON)
    assert next_run(schedule, after) == ts(2026, 7, 1, 7)


def test_weekly_finds_the_next_matching_weekday():
    schedule = parse_schedule("weekly mon 09:30 UTC")
    # 2026-11-24 is a Tuesday, so the next Monday is 2026-11-30.
    assert next_run(schedule, ts(2026, 11, 24, 12)) == ts(2026, 11, 30, 9, 30)


def test_weekly_same_day_before_the_time_runs_today():
    schedule = parse_schedule("weekly tue 13:00")
    assert next_run(schedule, ts(2026, 11, 24, 12)) == ts(2026, 11, 24, 13)


@pytest.mark.parametrize(
    "spec",
    [
        "",
        "every",
        "every 0m",
        "every 10s",
        "every five minutes",
        "daily 25:00",
        "daily 8am",
        "daily 08:00 Mars/Olympus",
        "weekly funday 08:00",
        "hourly",
    ],
)
def test_invalid_specs_raise(spec):
    with pytest.raises(ScheduleError):
        parse_schedule(spec)
