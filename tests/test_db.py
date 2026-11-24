import sqlite3

import pytest

from errand_server.models import Automation, validate_id


def make(id_="price-watch", trigger="schedule", **kw) -> Automation:
    base = dict(
        id=id_,
        name="Price watch",
        description="",
        trigger=trigger,
        schedule="every 1h" if trigger == "schedule" else None,
        hook_id="hook-abc" if trigger == "webhook" else None,
        enabled=True,
        timeout_s=60,
        next_run_at=100.0 if trigger == "schedule" else None,
        created_at=1.0,
    )
    base.update(kw)
    return Automation(**base)


def test_save_and_get_round_trip(repo):
    repo.save_automation(make())
    assert repo.get_automation("price-watch") == make()


def test_save_replaces_an_existing_automation(repo):
    repo.save_automation(make())
    repo.save_automation(make(name="Renamed"))
    assert repo.get_automation("price-watch").name == "Renamed"
    assert len(repo.list_automations()) == 1


def test_find_by_hook(repo):
    repo.save_automation(make("gh-stars", trigger="webhook"))
    assert repo.find_by_hook("hook-abc").id == "gh-stars"
    assert repo.find_by_hook("nope") is None


def test_claim_due_only_returns_due_enabled_schedules(repo):
    repo.save_automation(make("due", next_run_at=50.0))
    repo.save_automation(make("later", next_run_at=500.0))
    repo.save_automation(make("off", next_run_at=50.0, enabled=False))
    repo.save_automation(make("hook", trigger="webhook"))
    claimed = repo.claim_due(now=100.0, next_at=lambda a: 200.0)
    assert [a.id for a in claimed] == ["due"]
    assert repo.get_automation("due").next_run_at == 200.0


def test_claim_due_twice_claims_once(repo):
    repo.save_automation(make(next_run_at=50.0))
    first = repo.claim_due(now=100.0, next_at=lambda a: 200.0)
    second = repo.claim_due(now=100.0, next_at=lambda a: 200.0)
    assert len(first) == 1
    assert second == []


def test_start_run_refuses_a_second_concurrent_run(repo):
    repo.save_automation(make())
    run_id = repo.start_run("price-watch", "manual", now=10.0)
    assert run_id is not None
    assert repo.start_run("price-watch", "tick", now=11.0) is None
    repo.finish_run(run_id, "ok", 0, "done", now=12.0)
    assert repo.start_run("price-watch", "tick", now=13.0) is not None


def test_finish_run_records_the_outcome(repo):
    repo.save_automation(make())
    run_id = repo.start_run("price-watch", "manual", now=10.0)
    repo.finish_run(run_id, "error", 1, "boom", now=12.5)
    (run,) = repo.list_runs("price-watch")
    assert (run.status, run.exit_code, run.output, run.finished_at) == ("error", 1, "boom", 12.5)


def test_mark_interrupted_closes_running_runs(repo):
    repo.save_automation(make())
    repo.start_run("price-watch", "manual", now=10.0)
    assert repo.mark_interrupted(now=20.0) == 1
    (run,) = repo.list_runs("price-watch")
    assert run.status == "interrupted"


def test_list_runs_is_newest_first_and_limited(repo):
    repo.save_automation(make())
    for i in range(5):
        run_id = repo.start_run("price-watch", "tick", now=float(i))
        repo.finish_run(run_id, "ok", 0, str(i), now=float(i))
    runs = repo.list_runs("price-watch", limit=3)
    assert [r.output for r in runs] == ["4", "3", "2"]


def test_bad_status_is_rejected_by_the_schema(repo):
    repo.save_automation(make())
    run_id = repo.start_run("price-watch", "manual", now=1.0)
    with pytest.raises(sqlite3.IntegrityError):
        repo.finish_run(run_id, "maybe", 0, "", now=2.0)


@pytest.mark.parametrize("value", ["hn-digest", "a", "x1-2-3"])
def test_valid_ids(value):
    assert validate_id(value) == value


@pytest.mark.parametrize("value", ["", "../etc", "Has Caps", "-lead", "a/b", "x" * 49])
def test_invalid_ids(value):
    with pytest.raises(ValueError):
        validate_id(value)
