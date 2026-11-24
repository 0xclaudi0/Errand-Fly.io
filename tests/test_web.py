import json
import threading
import urllib.error
import urllib.request

import pytest

from errand_server.config import Secrets
from errand_server.web import App, make_server
from tests.test_db import make

SECRETS = Secrets(tick_token="tick-secret", admin_token="admin-secret")


class FakeDispatcher:
    def __init__(self):
        self.submitted = []
        self.ticks = 0

    def tick(self):
        self.ticks += 1
        return ["due"]

    def submit(self, automation, trigger, event=None):
        self.submitted.append((automation.id, trigger, event))


@pytest.fixture
def server(repo):
    dispatcher = FakeDispatcher()
    httpd = make_server(App(repo, dispatcher, SECRETS), host="127.0.0.1", port=0)
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    base = f"http://127.0.0.1:{httpd.server_address[1]}"
    yield base, dispatcher
    httpd.shutdown()


def call(url, method="GET", body=None, headers=None):
    data = body if isinstance(body, bytes) else (json.dumps(body).encode() if body else None)
    req = urllib.request.Request(url, data=data, method=method, headers=headers or {})
    try:
        with urllib.request.urlopen(req, timeout=5) as resp:
            return resp.status, json.loads(resp.read() or b"null")
    except urllib.error.HTTPError as err:
        return err.code, json.loads(err.read() or b"null")


def test_health(server):
    base, _ = server
    assert call(f"{base}/healthz") == (200, {"ok": True, "data": "ok", "error": None})


def test_tick_needs_the_token(server):
    base, dispatcher = server
    status, body = call(f"{base}/tick")
    assert status == 401 and body["ok"] is False
    assert dispatcher.ticks == 0


@pytest.mark.parametrize("how", ["header", "query"])
def test_tick_with_token_starts_due_runs(server, how):
    base, dispatcher = server
    if how == "header":
        status, body = call(f"{base}/tick", headers={"X-Errand-Token": "tick-secret"})
    else:
        status, body = call(f"{base}/tick?token=tick-secret")
    assert status == 202
    assert body["data"] == {"started": ["due"]}


def test_admin_token_does_not_open_tick_and_tick_token_does_not_open_api(server):
    base, _ = server
    assert call(f"{base}/tick", headers={"X-Errand-Token": "admin-secret"})[0] == 401
    auth = {"Authorization": "Bearer tick-secret"}
    assert call(f"{base}/api/automations", headers=auth)[0] == 401


def test_hook_unknown_id_is_404(server):
    base, _ = server
    assert call(f"{base}/hooks/nope", "POST", {"a": 1})[0] == 404


def test_hook_known_id_submits_the_parsed_event(server, repo):
    base, dispatcher = server
    repo.save_automation(make("gh", trigger="webhook"))
    status, _ = call(
        f"{base}/hooks/hook-abc", "POST", {"star": True}, {"Content-Type": "application/json"}
    )
    assert status == 202
    assert dispatcher.submitted == [("gh", "webhook", {"star": True})]


def test_hook_on_disabled_automation_is_404(server, repo):
    base, dispatcher = server
    repo.save_automation(make("gh", trigger="webhook", enabled=False))
    assert call(f"{base}/hooks/hook-abc", "POST", {"a": 1})[0] == 404
    assert dispatcher.submitted == []


def test_hook_body_too_large_is_413(server, repo):
    base, _ = server
    repo.save_automation(make("gh", trigger="webhook"))
    assert call(f"{base}/hooks/hook-abc", "POST", b"x" * 1_100_000)[0] == 413


def test_api_lists_automations_with_admin_token(server, repo):
    base, _ = server
    repo.save_automation(make())
    status, body = call(f"{base}/api/automations", headers={"Authorization": "Bearer admin-secret"})
    assert status == 200
    assert [a["id"] for a in body["data"]] == ["price-watch"]
    assert "hook_id" in body["data"][0]


def test_api_manual_run(server, repo):
    base, dispatcher = server
    repo.save_automation(make())
    auth = {"Authorization": "Bearer admin-secret"}
    assert call(f"{base}/api/automations/price-watch/run", "POST", {}, auth)[0] == 202
    assert call(f"{base}/api/automations/missing/run", "POST", {}, auth)[0] == 404
    assert dispatcher.submitted == [("price-watch", "manual", None)]


def test_unknown_path_is_404(server):
    base, _ = server
    assert call(f"{base}/nothing-here")[0] == 404
