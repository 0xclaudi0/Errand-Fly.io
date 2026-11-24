import threading

import pytest

from errand_server.builder import Builder
from errand_server.builder_tools import Toolbox
from errand_server.drafts import DraftStore
from errand_server.web import App, make_server
from tests.test_builder import HAPPY_PATH, FakeExecute, ScriptedGemini, call, reply
from tests.test_jobs import FakeAwake
from tests.test_web import SECRETS, FakeDispatcher
from tests.test_web import call as http

AUTH = {"Authorization": "Bearer admin-secret"}


@pytest.fixture
def site(repo, paths):
    store = DraftStore(paths)
    gemini = ScriptedGemini()
    tools = Toolbox(store, paths, repo, execute=FakeExecute(), providers=lambda: ["resend"],
                    fetch=lambda url: "")
    builder = Builder(store, paths, repo, tools, post=gemini, models=("m1",),
                      keep_awake=FakeAwake(), background=False, sleep=lambda s: None)
    httpd = make_server(App(repo, FakeDispatcher(), SECRETS, builder, store), "127.0.0.1", 0)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{httpd.server_address[1]}", gemini
    httpd.shutdown()


def test_drafts_need_the_admin_token(site):
    base, _ = site
    assert http(f"{base}/api/drafts", "POST", {"message": "hi"})[0] == 401


def test_build_then_approve_over_http(site, repo):
    base, gemini = site
    gemini.replies = list(HAPPY_PATH)
    status, body = http(f"{base}/api/drafts", "POST", {"message": "mail me hello"}, AUTH)
    assert status == 202
    draft_id = body["data"]["id"]

    status, body = http(f"{base}/api/drafts/{draft_id}", headers=AUTH)
    view = body["data"]
    assert view["status"] == "proposed" and "main.py" in view["files"]
    assert "contents" not in view and view["dry_runs"][0]["status"] == "ok"

    status, body = http(f"{base}/api/drafts", headers=AUTH)
    assert body["data"][0]["request"] == "mail me hello"

    status, body = http(f"{base}/api/drafts/{draft_id}/approve", "POST", {}, AUTH)
    assert status == 200 and body["data"]["id"] == "hello-mail"
    assert repo.get_automation("hello-mail") is not None
    assert http(f"{base}/api/drafts/{draft_id}/approve", "POST", {}, AUTH)[0] == 409


def test_webhook_approval_returns_the_hook_path(site):
    base, gemini = site
    gemini.replies = [
        *HAPPY_PATH[1:3],
        reply(call("propose", id="on-push", name="On push", summary="s", trigger="webhook")),
        reply({"text": "ok"}),
    ]
    draft_id = http(f"{base}/api/drafts", "POST", {"message": "x"}, AUTH)[1]["data"]["id"]
    body = http(f"{base}/api/drafts/{draft_id}/approve", "POST", {}, AUTH)[1]
    assert body["data"]["hook_path"].startswith("/hooks/")


def test_continue_a_draft(site):
    base, gemini = site
    gemini.replies = [reply({"text": "Which city?"}), reply({"text": "Thanks"})]
    draft_id = http(f"{base}/api/drafts", "POST", {"message": "weather"}, AUTH)[1]["data"]["id"]
    status, _ = http(f"{base}/api/drafts/{draft_id}/messages", "POST", {"message": "Lagos"}, AUTH)
    assert status == 202
    view = http(f"{base}/api/drafts/{draft_id}", headers=AUTH)[1]["data"]
    assert [t["text"] for t in view["transcript"]] == ["weather", "Which city?", "Lagos", "Thanks"]


@pytest.mark.parametrize("path, method, body, code", [
    ("/api/drafts", "POST", b"not json", 400),
    ("/api/drafts", "POST", b"[1]", 400),
    ("/api/drafts", "POST", {"message": ""}, 400),
    ("/api/drafts/d-00000000", "GET", None, 404),
    ("/api/drafts/d-00000000/messages", "POST", {"message": "x"}, 404),
    ("/api/drafts/nope", "GET", None, 404),
])
def test_bad_draft_requests(site, path, method, body, code):
    base, _ = site
    assert http(f"{base}{path}", method, body, AUTH)[0] == code


def test_no_builder_is_503(repo):
    httpd = make_server(App(repo, FakeDispatcher(), SECRETS), "127.0.0.1", 0)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    try:
        base = f"http://127.0.0.1:{httpd.server_address[1]}"
        assert http(f"{base}/api/drafts", headers=AUTH)[0] == 503
    finally:
        httpd.shutdown()
