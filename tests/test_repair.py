import json

import pytest

from errand_server.builder import Builder
from errand_server.builder_tools import Toolbox
from errand_server.drafts import DraftStore
from errand_server.jobs import Dispatcher
from errand_server.repair import Repairer
from errand_server.runner import RunOutcome
from tests.test_builder import HAPPY_PATH, FakeExecute, ScriptedGemini, reply
from tests.test_db import make
from tests.test_jobs import FakeAwake


@pytest.fixture
def kit(repo, paths):
    store = DraftStore(paths)
    gemini = ScriptedGemini()
    tools = Toolbox(store, paths, repo, execute=FakeExecute(), providers=lambda: ["resend"],
                    fetch=lambda url: "")
    builder = Builder(store, paths, repo, tools, post=gemini, models=("m1",),
                      keep_awake=FakeAwake(), background=False, sleep=lambda s: None)
    sent = []
    settings = {}
    repairer = Repairer(repo, builder, settings=lambda: settings,
                        notify=lambda subject, text: sent.append((subject, text)),
                        base_url=lambda: "https://e.sprites.app")
    outcomes = []

    def execute(folder, env, timeout_s, **_):
        return outcomes.pop(0)

    dispatcher = Dispatcher(repo, paths, keep_awake=FakeAwake(), execute=execute,
                            clock=lambda: 1000.0, background=False,
                            on_failure=repairer.on_failure)
    folder = paths.automation_dir("gh")
    folder.mkdir(parents=True)
    (folder / "main.py").write_text("print(event()['action'])\n")
    repo.save_automation(make("gh", trigger="webhook", name="Star alert"))
    return dispatcher, gemini, store, sent, settings, outcomes


FIX = (
    *HAPPY_PATH[1:3],
    reply({"functionCall": {"name": "propose", "args": {
        "id": "gh", "name": "Star alert", "summary": "Handles created.", "trigger": "webhook"}},
        "thoughtSignature": "s"}),
    reply({"text": "Fixed: it now accepts the created action."}),
)


def test_a_failed_run_starts_one_repair_and_emails_the_fix(repo, kit):
    dispatcher, gemini, store, sent, _, outcomes = kit
    gemini.replies = list(FIX)
    outcomes.append(RunOutcome("error", 1, "KeyError: 'action'"))
    dispatcher.run_now(repo.get_automation("gh"), "webhook", event={"zen": "hi"})

    (draft,) = store.list()
    assert draft.kind == "repair" and draft.target == "gh" and draft.status == "proposed"
    context = gemini.requests[0][1]["contents"][0]["parts"][0]["text"]
    assert "KeyError: 'action'" in context and '"zen": "hi"' in context
    assert draft.transcript[0]["text"] == "Run #1 failed (error). Please fix it."
    assert "Find the cause in the run output" in context
    (subject, text), = sent
    assert subject == "Errand: Star alert failed"
    assert f"https://e.sprites.app/#/draft/{draft.id}" in text and "wrote and tested a fix" in text

    outcomes.append(RunOutcome("error", 1, "KeyError again"))
    dispatcher.run_now(repo.get_automation("gh"), "webhook", event={"zen": "hi"})
    assert len(store.list()) == 1  # the open repair draft is reused, not repeated
    assert len(sent) == 1  # same failure streak: no second email


def test_with_auto_repair_off_only_the_first_failure_emails(repo, kit):
    dispatcher, _, store, sent, settings, outcomes = kit
    settings["auto_repair"] = "off"
    outcomes.extend([RunOutcome("timeout", None, "slow"), RunOutcome("ok", 0, ""),
                     RunOutcome("error", 1, "boom")])
    for _ in range(3):
        dispatcher.run_now(repo.get_automation("gh"), "manual")
    assert store.list() == []
    assert len(sent) == 2 and "Open Errand" in sent[0][1]


def test_a_repair_without_a_fix_still_emails(repo, kit):
    dispatcher, gemini, store, sent, _, outcomes = kit
    gemini.replies = [reply({"text": "I need the full error."})]
    outcomes.append(RunOutcome("error", 1, "boom"))
    dispatcher.run_now(repo.get_automation("gh"), "manual")
    assert "has no tested fix yet" in sent[0][1]


def test_a_mail_failure_is_only_logged(repo, kit, caplog):
    dispatcher, _, _, _, settings, outcomes = kit
    settings["auto_repair"] = "off"

    def broken(subject, text):
        raise RuntimeError("no notify_email")

    dispatcher._on_failure.__self__._notify = broken
    outcomes.append(RunOutcome("error", 1, "boom"))
    dispatcher.run_now(repo.get_automation("gh"), "manual")
    assert "could not email" in caplog.text


def test_webhook_payload_is_kept_for_repairs(repo, paths, kit):
    dispatcher, _, _, _, settings, outcomes = kit
    outcomes.append(RunOutcome("ok", 0, ""))
    dispatcher.run_now(repo.get_automation("gh"), "webhook", event={"n": 1})
    assert json.loads((paths.events / "gh.json").read_text()) == {"n": 1}


def test_a_crash_in_the_failure_hook_does_not_break_the_run(repo, paths):
    def explode(automation, run_id):
        raise RuntimeError("hook")

    repo.save_automation(make())
    dispatcher = Dispatcher(repo, paths, keep_awake=FakeAwake(),
                            execute=lambda *a, **k: RunOutcome("error", 1, "x"),
                            clock=lambda: 1.0, background=False, on_failure=explode)
    run_id = dispatcher.run_now(repo.get_automation("price-watch"), "manual")
    assert repo.get_run(run_id).status == "error"
