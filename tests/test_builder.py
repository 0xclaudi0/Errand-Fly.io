import json
from pathlib import Path

import pytest

from errand_sdk.gateway import GatewayError
from errand_sdk.llm import ModelHealth
from errand_server.builder import FOREIGN_SIGNATURE, MAX_STEPS, Builder, BuilderError
from errand_server.builder_tools import Toolbox
from errand_server.drafts import DraftStore
from errand_server.runner import RunOutcome
from tests.test_jobs import FakeAwake

HELLO = "from errand_sdk import send_email\n\nsend_email('hi', 'hello')\nprint('sent')\n"


def call(tool, sig="sig-a", id_=None, **args):
    part = {"functionCall": {"name": tool, "args": args}, "thoughtSignature": sig}
    if id_:
        part["functionCall"]["id"] = id_
    return part


def reply(*parts):
    return {"candidates": [{"content": {"role": "model", "parts": list(parts)}}]}


class ScriptedGemini:
    """Plays back replies in order; a reply can be a GatewayError to raise."""

    def __init__(self, *replies):
        self.replies = list(replies)
        self.requests = []

    def __call__(self, url, body, timeout):
        self.requests.append((url.rsplit("/", 1)[1].split(":")[0], json.loads(json.dumps(body))))
        item = self.replies.pop(0)
        if isinstance(item, Exception):
            raise item
        return item


class FakeExecute:
    def __init__(self, outcome=RunOutcome("ok", 0, "[dry run] Email was not done.\nsent\n")):
        self.outcome = outcome
        self.calls = []

    def __call__(self, folder, env, timeout_s, **_):
        event = None
        if env.get("ERRAND_EVENT_FILE"):
            with open(env["ERRAND_EVENT_FILE"]) as fh:
                event = json.load(fh)
        state_file = Path(env["ERRAND_STATE_FILE"])
        state = json.loads(state_file.read_text()) if state_file.exists() else None
        self.calls.append({"folder": folder, "env": env, "event": event, "state": state,
                           "files": sorted(p.name for p in folder.iterdir())})
        return self.outcome


@pytest.fixture
def kit(paths, repo):
    store = DraftStore(paths)
    execute = FakeExecute()

    def make(*replies, models=("m1", "m2")):
        gemini = ScriptedGemini(*replies)
        tools = Toolbox(store, paths, repo, execute=execute,
                        providers=lambda: ["gemini", "github", "resend"],
                        fetch=lambda url: "<html>" + "x" * 9000)
        builder = Builder(store, paths, repo, tools, post=gemini, models=models, health=ModelHealth(),
                          keep_awake=FakeAwake(), settings=lambda: {"timezone": "Europe/London"},
                          background=False, sleep=lambda s: None)
        return builder, gemini

    return make, store, execute


HAPPY_PATH = (
    reply(call("list_connectors")),
    reply({"text": "Writing it now."}, call("write_file", name="main.py", content=HELLO)),
    reply(call("dry_run")),
    reply(call("propose", id="hello-mail", name="Hello mail", summary="Mails hello daily.",
               trigger="schedule", schedule="daily 08:00 Europe/London",
               services=["resend"])),
    reply({"text": "It sends you hello at 08:00 each day. Approve?"}),
)


def test_happy_path_writes_tests_and_proposes(kit):
    make, store, execute = kit
    builder, gemini = make(*HAPPY_PATH)
    draft = builder.start("email me hello every morning at 8")
    assert draft.status == "proposed"
    assert draft.proposal["id"] == "hello-mail"
    assert draft.proposal["schedule"] == "daily 08:00 Europe/London"
    assert store.files(draft.id) == {"main.py": HELLO}
    assert [t["role"] for t in draft.transcript] == [
        "user", "agent", "step", "step", "step", "agent"]
    assert draft.transcript[-1]["text"].startswith("It sends you hello")
    env = execute.calls[0]["env"]
    assert env["ERRAND_DRY_RUN"] == "1" and env["ERRAND_TRIGGER"] == "dry-run"
    assert env["ERRAND_STATE_FILE"].endswith(f"{draft.id}/state.json")
    first_body = gemini.requests[0][1]
    assert "Europe/London" in first_body["systemInstruction"]["parts"][0]["text"]
    assert first_body["tools"][0]["functionDeclarations"]


def test_function_responses_keep_ids_and_signatures(kit):
    make, _, _ = kit
    builder, gemini = make(reply(call("list_connectors", sig="s1", id_="c1")),
                           reply({"text": "Which site?"}))
    builder.start("watch a page")
    contents = gemini.requests[1][1]["contents"]
    assert contents[1]["parts"][0]["thoughtSignature"] == "s1"
    response = contents[2]["parts"][0]["functionResponse"]
    assert response["id"] == "c1" and response["name"] == "list_connectors"
    assert "github" in response["response"]["services"]


def test_a_question_waits_for_the_owner_and_the_reply_continues(kit):
    make, store, _ = kit
    builder, gemini = make(reply({"text": "Which city?"}), *HAPPY_PATH)
    draft = builder.start("tell me the weather")
    assert draft.status == "waiting" and draft.transcript[-1]["text"] == "Which city?"
    draft = builder.send(draft.id, "Lagos")
    assert draft.status == "proposed"
    texts = [c["parts"][0].get("text") for c in gemini.requests[1][1]["contents"]]
    assert texts[:3] == ["tell me the weather", "Which city?", "Lagos"]


def test_signatures_from_another_model_are_replaced(kit):
    make, _, _ = kit
    builder, gemini = make(
        reply(call("list_connectors", sig="from-m1")),
        GatewayError(503, "busy"),
        reply({"text": "done"}),
    )
    builder.start("x")
    model, body = gemini.requests[-1]
    assert model == "m2"
    assert body["contents"][1]["parts"][0]["thoughtSignature"] == FOREIGN_SIGNATURE


def test_propose_needs_a_passing_dry_run(kit):
    make, _, execute = kit
    execute.outcome = RunOutcome("error", 1, "Traceback: boom")
    builder, gemini = make(
        reply(call("write_file", name="main.py", content=HELLO)),
        reply(call("dry_run")),
        reply(call("propose", id="x", name="X", summary="s", trigger="manual")),
        reply({"text": "It fails."}),
    )
    draft = builder.start("x")
    assert draft.status == "waiting" and draft.proposal is None
    result = gemini.requests[3][1]["contents"][-1]["parts"][0]["functionResponse"]["response"]
    assert "dry_run" in result["error"]


def test_a_new_write_clears_the_test_and_the_proposal(kit):
    make, _, _ = kit
    builder, _ = make(*HAPPY_PATH[:4], reply(call("write_file", name="main.py", content="print(1)\n")),
                      reply({"text": "changed"}))
    draft = builder.start("x")
    assert draft.proposal is None and draft.tested is False and draft.status == "waiting"


def test_code_check_rejects_blocked_imports(kit):
    make, store, _ = kit
    builder, gemini = make(
        reply(call("write_file", name="main.py", content="import subprocess\n")),
        reply(call("write_file", name="../evil.py", content="x = 1\n")),
        reply({"text": "ok"}),
    )
    draft = builder.start("x")
    first = gemini.requests[1][1]["contents"][-1]["parts"][0]["functionResponse"]["response"]
    second = gemini.requests[2][1]["contents"][-1]["parts"][0]["functionResponse"]["response"]
    assert "subprocess" in first["problems"][0]
    assert "file names" in second["error"]
    assert store.files(draft.id) == {}


def test_dry_run_passes_the_sample_event_and_resets_state(kit):
    make, store, execute = kit
    builder, _ = make(
        reply(call("write_file", name="main.py", content=HELLO)),
        reply(call("dry_run", event_json='{"action": "opened"}')),
        reply({"text": "ok"}),
    )
    draft = store.create()
    (store.folder(draft.id) / "state.json").write_text('{"old": true}')
    builder.send(draft.id, "on webhook")
    assert execute.calls[0]["event"] == {"action": "opened"}
    assert not (store.folder(draft.id) / "state.json").exists()


def test_bad_event_json_goes_back_to_the_model(kit):
    make, _, execute = kit
    builder, gemini = make(reply(call("dry_run", event_json="{nope")), reply({"text": "ok"}))
    builder.start("x")
    result = gemini.requests[1][1]["contents"][-1]["parts"][0]["functionResponse"]["response"]
    assert "not valid JSON" in result["error"] and execute.calls == []


def test_peek_url_returns_the_start_of_the_page(kit):
    make, _, _ = kit
    builder, gemini = make(reply(call("peek_url", url="https://example.com")),
                           reply(call("peek_url", url="file:///etc/passwd")),
                           reply({"text": "ok"}))
    builder.start("x")
    ok = gemini.requests[1][1]["contents"][-1]["parts"][0]["functionResponse"]["response"]
    bad = gemini.requests[2][1]["contents"][-1]["parts"][0]["functionResponse"]["response"]
    assert ok["length"] == 9006 and len(ok["start"]) == 4000
    assert "https://" in bad["error"]


def test_unknown_tool_and_bad_proposal_are_reported_to_the_model(kit):
    make, _, _ = kit
    builder, gemini = make(
        *HAPPY_PATH[1:3],
        reply(call("delete_everything")),
        reply(call("propose", id="Bad Id!", name="x", summary="s", trigger="manual")),
        reply(call("propose", id="ok-id", name="x", summary="s", trigger="schedule",
                   schedule="sometimes")),
        reply({"text": "sorry"}),
    )
    builder.start("x")
    responses = [b["contents"][-1]["parts"][0]["functionResponse"]["response"]
                 for _, b in gemini.requests[3:6]]
    assert "unknown tool" in responses[0]["error"]
    assert "ValueError" in responses[1]["error"]
    assert "bad schedule" in responses[2]["error"]


def test_every_model_failing_marks_the_draft_as_error(kit):
    make, _, _ = kit
    builder, _ = make(*[GatewayError(503, "busy")] * 4)
    draft = builder.start("x")
    assert draft.status == "error" and "every model failed" in draft.error


def test_step_limit_stops_a_looping_model(kit):
    make, _, _ = kit
    builder, _ = make(*[reply(call("list_connectors"))] * MAX_STEPS, models=("m1",))
    draft = builder.start("x")
    assert draft.status == "waiting" and "stopped after" in draft.transcript[-1]["text"]


def test_approve_installs_the_proposal(kit, repo, paths):
    make, store, _ = kit
    builder, _ = make(*HAPPY_PATH)
    draft = builder.start("x")
    automation = builder.approve(draft.id)
    assert automation.id == "hello-mail" and automation.schedule == "daily 08:00 Europe/London"
    assert (paths.automation_dir("hello-mail") / "main.py").read_text() == HELLO
    assert store.load(draft.id).status == "installed"
    with pytest.raises(BuilderError) as err:
        builder.approve(draft.id)
    assert err.value.status == 409
    with pytest.raises(BuilderError):
        builder.send(draft.id, "more")


def test_approve_without_a_proposal_is_refused(kit):
    make, store, _ = kit
    builder, _ = make()
    draft = store.create()
    with pytest.raises(BuilderError) as err:
        builder.approve(draft.id)
    assert err.value.status == 409


def test_propose_refuses_an_id_that_exists(kit, repo):
    from tests.test_db import make as make_automation

    make, _, _ = kit
    repo.save_automation(make_automation("hello-mail"))
    builder, gemini = make(*HAPPY_PATH[:4], reply({"text": "taken"}))
    draft = builder.start("x")
    result = gemini.requests[4][1]["contents"][-1]["parts"][0]["functionResponse"]["response"]
    assert "exists" in result["error"] and draft.proposal is None


@pytest.mark.parametrize("message", ["", "   ", None, "x" * 4001])
def test_bad_messages_are_refused(kit, message):
    make, _, _ = kit
    builder, _ = make()
    with pytest.raises(BuilderError) as err:
        builder.start(message)
    assert err.value.status == 400


def test_unknown_draft_is_404(kit):
    make, _, _ = kit
    builder, _ = make()
    with pytest.raises(BuilderError) as err:
        builder.send("d-00000000", "hi")
    assert err.value.status == 404


def test_recover_marks_stuck_drafts(kit):
    make, store, _ = kit
    builder, _ = make()
    draft = store.create()
    store.save(draft.with_changes(status="working"))
    assert builder.recover() == 1
    assert store.load(draft.id).status == "error"


def test_a_crash_in_a_tool_loop_is_recorded(kit, monkeypatch):
    make, store, _ = kit
    builder, _ = make(reply(call("list_connectors")))
    monkeypatch.setattr(builder, "_run_calls", lambda *a: (_ for _ in ()).throw(RuntimeError("x")))
    draft = builder.start("x")
    assert store.load(draft.id).status == "error"


def test_dry_run_can_start_from_a_given_state(kit):
    make, _, execute = kit
    builder, gemini = make(
        reply(call("write_file", name="main.py", content=HELLO)),
        reply(call("dry_run", state_json='{"last": ["3.14"]}')),
        reply(call("dry_run", state_json="{bad")),
        reply({"text": "ok"}),
    )
    builder.start("x")
    assert [c["state"] for c in execute.calls] == [{"last": ["3.14"]}]
    bad = gemini.requests[3][1]["contents"][-1]["parts"][0]["functionResponse"]["response"]
    assert "state_json is not valid JSON" in bad["error"]
