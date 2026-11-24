import json
from contextlib import contextmanager

from errand_server.jobs import Dispatcher
from errand_server.runner import RunOutcome
from tests.test_db import make


class FakeAwake:
    def __init__(self):
        self.holds = []

    @contextmanager
    def __call__(self, name, seconds):
        self.holds.append((name, seconds))
        yield


def fake_execute(record):
    def run(folder, env, timeout_s, **_):
        event = None
        if env.get("ERRAND_EVENT_FILE"):
            with open(env["ERRAND_EVENT_FILE"]) as fh:
                event = json.load(fh)
        record.append({"folder": folder, "env": env, "timeout": timeout_s, "event": event})
        return RunOutcome("ok", 0, "fine")

    return run


def build(repo, paths, record, clock=lambda: 1000.0):
    awake = FakeAwake()
    dispatcher = Dispatcher(
        repo, paths, keep_awake=awake, execute=fake_execute(record), clock=clock, background=False
    )
    return dispatcher, awake


def test_run_now_records_a_run_and_holds_the_sprite_awake(repo, paths):
    record = []
    repo.save_automation(make(timeout_s=60))
    dispatcher, awake = build(repo, paths, record)
    run_id = dispatcher.run_now(repo.get_automation("price-watch"), "manual")
    (run,) = repo.list_runs("price-watch")
    assert run.id == run_id and run.status == "ok" and run.output == "fine"
    assert awake.holds == [(f"errand-run-{run_id}", 120)]
    env = record[0]["env"]
    assert env["ERRAND_AUTOMATION_ID"] == "price-watch"
    assert env["ERRAND_TRIGGER"] == "manual"
    assert record[0]["folder"] == paths.automation_dir("price-watch")


def test_event_reaches_the_script_and_the_file_is_removed(repo, paths):
    record = []
    repo.save_automation(make("gh", trigger="webhook"))
    dispatcher, _ = build(repo, paths, record)
    dispatcher.run_now(repo.get_automation("gh"), "webhook", event={"star": 1})
    assert record[0]["event"] == {"star": 1}
    assert list(paths.tmp.iterdir()) == []


def test_tick_runs_due_automations_and_moves_next_run(repo, paths):
    record = []
    repo.save_automation(make("due", next_run_at=900.0, schedule="every 5m"))
    repo.save_automation(make("later", next_run_at=5000.0))
    dispatcher, _ = build(repo, paths, record)
    assert dispatcher.tick() == ["due"]
    assert repo.get_automation("due").next_run_at == 1300.0
    assert [r["env"]["ERRAND_AUTOMATION_ID"] for r in record] == ["due"]


def test_a_crash_in_execute_is_recorded_as_error(repo, paths):
    repo.save_automation(make())

    def explode(*_a, **_k):
        raise RuntimeError("kaboom")

    dispatcher = Dispatcher(
        repo, paths, keep_awake=FakeAwake(), execute=explode, clock=lambda: 1.0, background=False
    )
    dispatcher.run_now(repo.get_automation("price-watch"), "manual")
    (run,) = repo.list_runs("price-watch")
    assert run.status == "error"
    assert "kaboom" in run.output


def test_overlapping_run_is_skipped(repo, paths):
    repo.save_automation(make())
    repo.start_run("price-watch", "manual", now=1.0)
    dispatcher, _ = build(repo, paths, [])
    assert dispatcher.run_now(repo.get_automation("price-watch"), "tick") is None
