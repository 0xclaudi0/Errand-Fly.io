import http.client
import json
import threading

import pytest

from errand_server.auth import Auth
from errand_server.builder import Builder
from errand_server.builder_tools import Toolbox
from errand_server.drafts import DraftStore
from errand_server.install import install
from errand_server.web import App, make_server
from tests.test_builder import HAPPY_PATH, FakeExecute, ScriptedGemini, reply
from tests.test_jobs import FakeAwake
from tests.test_web import SECRETS, FakeDispatcher

BEARER = {"Authorization": "Bearer admin-secret"}


class FakeSprite:
    def __init__(self):
        self.made, self.restored = [], []

    def checkpoints(self):
        return [{"id": "v1", "create_time": "2026-11-23T00:00:00Z"},
                {"id": "v2", "create_time": "2026-11-24T00:00:00Z", "comment": "errand: x"}]

    def create_checkpoint(self, comment):
        self.made.append(comment)
        return f"v{len(self.made) + 10}"

    def restore(self, checkpoint_id):
        self.restored.append(checkpoint_id)


class Site:
    def __init__(self, port, gemini, sprite, auth, dispatcher):
        self.port, self.gemini, self.sprite, self.auth = port, gemini, sprite, auth
        self.dispatcher = dispatcher

    def call(self, method, path, body=None, headers=None):
        conn = http.client.HTTPConnection("127.0.0.1", self.port, timeout=5)
        data = body if isinstance(body, bytes) else json.dumps(body).encode() if body is not None else None
        conn.request(method, path, data, headers or {})
        resp = conn.getresponse()
        raw = resp.read()
        conn.close()
        try:
            parsed = json.loads(raw) if raw else None
        except ValueError:
            parsed = raw
        return resp.status, parsed, resp


@pytest.fixture
def site(repo, paths):
    store = DraftStore(paths)
    gemini = ScriptedGemini()
    sprite = FakeSprite()
    tools = Toolbox(store, paths, repo, execute=FakeExecute(), providers=lambda: ["resend"],
                    fetch=lambda url: "")
    builder = Builder(store, paths, repo, tools, post=gemini, models=("m1",),
                      keep_awake=FakeAwake(), background=False, sleep=lambda s: None,
                      checkpoint=sprite.create_checkpoint)
    auth = Auth(paths.auth)
    dispatcher = FakeDispatcher()
    app = App(repo, dispatcher, SECRETS, builder, store, auth=auth, paths=paths,
              sprite=sprite, base_url=lambda: "https://e.sprites.app")
    httpd = make_server(app, "127.0.0.1", 0)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    yield Site(httpd.server_address[1], gemini, sprite, auth, dispatcher)
    httpd.shutdown()


@pytest.fixture
def installed(repo, paths, tmp_path):
    source = tmp_path / "src"
    source.mkdir()
    (source / "main.py").write_text("print('v1')\n")
    install(repo, paths, source, automation_id="digest", name="Digest", trigger="schedule",
            schedule="every 1h")
    (source / "main.py").write_text("print('v2')\n")
    install(repo, paths, source, automation_id="digest", name="Digest", trigger="schedule",
            schedule="every 1h")
    return "digest"


def login(site):
    status, _, resp = site.call("GET", f"/login?code={site.auth.new_login_code()}")
    assert status == 303 and resp.getheader("Location") == "/"
    cookie = resp.getheader("Set-Cookie")
    assert "HttpOnly" in cookie and "Secure" in cookie
    return {"Cookie": cookie.split(";")[0]}


# ---- pages, login and cookies ---------------------------------------------------

def test_home_page_and_static_files_have_security_headers(site):
    status, body, resp = site.call("GET", "/")
    assert status == 200 and b"/static/app.js" in body
    assert "script-src 'self'" in resp.getheader("Content-Security-Policy")
    assert resp.getheader("X-Content-Type-Options") == "nosniff"
    assert site.call("GET", "/static/app.js")[0] == 200
    assert site.call("GET", "/static/../web.py")[0] == 404
    assert site.call("GET", "/static/secrets.json")[0] == 404


def test_bad_login_code_redirects_with_a_flag(site):
    status, _, resp = site.call("GET", "/login?code=wrong")
    assert status == 303 and resp.getheader("Location") == "/?login=expired"
    assert resp.getheader("Set-Cookie") is None


def test_cookie_session_reads_the_api_and_needs_the_client_header_to_write(site, installed):
    cookie = login(site)
    assert site.call("GET", "/api/automations", headers=cookie)[0] == 200
    assert site.call("POST", f"/api/automations/{installed}/run", {}, cookie)[0] == 403
    web = {**cookie, "X-Errand-Client": "web"}
    assert site.call("POST", f"/api/automations/{installed}/run", {}, web)[0] == 202
    assert site.call("POST", "/logout", None, web)[0] == 204
    assert site.call("GET", "/api/automations", headers=cookie)[0] == 401


def test_no_auth_is_401_and_a_wrong_bearer_is_401(site):
    assert site.call("GET", "/api/session")[0] == 401
    assert site.call("GET", "/api/session", headers={"Authorization": "Bearer nope"})[0] == 401
    status, body, resp = site.call("GET", "/api/session", headers=BEARER)
    assert status == 200 and body["data"]["sprite_url"] == "https://e.sprites.app"
    assert resp.getheader("Cache-Control") == "no-store"


# ---- settings -------------------------------------------------------------------

def test_settings_round_trip_and_checks(site):
    status, body, _ = site.call("POST", "/api/settings",
                                {"notify_email": "me@example.com", "timezone": "Africa/Lagos"}, BEARER)
    assert status == 200 and body["data"]["timezone"] == "Africa/Lagos"
    status, body, _ = site.call("POST", "/api/settings", {"timezone": "Nowhere/Land"}, BEARER)
    assert status == 400 and "timezone" in body["error"]
    data = site.call("GET", "/api/settings", headers=BEARER)[1]["data"]
    assert data["notify_email"] == "me@example.com" and data["auto_repair"] == "on"


# ---- automations ----------------------------------------------------------------

def test_list_hides_the_hook_id_and_shows_the_last_run(site, repo, installed):
    run_id = repo.start_run(installed, "manual", 5.0)
    repo.finish_run(run_id, "ok", 0, "fine", 6.0)
    item = site.call("GET", "/api/automations", headers=BEARER)[1]["data"][0]
    assert "hook_id" not in item and item["last_run"]["status"] == "ok"


def test_detail_has_files_versions_and_runs(site, installed):
    data = site.call("GET", f"/api/automations/{installed}", headers=BEARER)[1]["data"]
    assert data["files"] == {"main.py": "print('v2')\n"}
    assert [v["message"] for v in data["versions"]] == ["update digest", "install digest"]
    assert data["runs"] == [] and data["drafts"] == []
    assert site.call("GET", "/api/automations/nope", headers=BEARER)[0] == 404


def test_pause_and_resume(site, repo, installed):
    status, body, _ = site.call("POST", f"/api/automations/{installed}/enabled",
                                {"enabled": False}, BEARER)
    assert status == 200 and body["data"]["enabled"] is False
    assert site.call("POST", f"/api/automations/{installed}/enabled",
                     {"enabled": "yes"}, BEARER)[0] == 400
    site.call("POST", f"/api/automations/{installed}/enabled", {"enabled": True}, BEARER)
    assert repo.get_automation(installed).enabled is True


def test_roll_back(site, paths, installed):
    versions = site.call("GET", f"/api/automations/{installed}", headers=BEARER)[1]["data"]["versions"]
    status, _, _ = site.call("POST", f"/api/automations/{installed}/rollback",
                             {"version": versions[1]["sha"]}, BEARER)
    assert status == 200
    assert (paths.automation_dir(installed) / "main.py").read_text() == "print('v1')\n"
    assert site.call("POST", f"/api/automations/{installed}/rollback",
                     {"version": "zzz"}, BEARER)[0] == 400


def test_delete_saves_a_checkpoint_and_moves_the_code_to_trash(site, repo, paths, installed):
    status, body, _ = site.call("DELETE", f"/api/automations/{installed}", headers=BEARER)
    assert status == 200 and body["data"]["checkpoint"] == "v11"
    assert site.sprite.made == ["before delete of digest"]
    assert repo.get_automation(installed) is None
    assert not paths.automation_dir(installed).exists()
    assert len(list((paths.home / "trash").iterdir())) == 1


def test_edit_starts_a_change_draft_and_approve_updates_in_place(site, repo, paths, installed):
    site.gemini.replies = [
        *HAPPY_PATH[1:3],
        reply({"functionCall": {"name": "propose", "args": {
            "id": "something-else", "name": "Digest", "summary": "Now says hi.",
            "trigger": "schedule", "schedule": "every 2h"}}, "thoughtSignature": "s"}),
        reply({"text": "Updated it to run every 2 h."}),
    ]
    status, body, _ = site.call("POST", f"/api/automations/{installed}/edit",
                                {"message": "run every 2 hours"}, BEARER)
    assert status == 202
    draft_id = body["data"]["id"]
    first = site.gemini.requests[0][1]["contents"][0]["parts"][0]["text"]
    assert 'changing the installed automation "digest"' in first and "print('v2')" in first
    view = site.call("GET", f"/api/drafts/{draft_id}", headers=BEARER)[1]["data"]
    assert view["kind"] == "change" and view["target"] == installed
    assert view["proposal"]["id"] == installed  # the id stays the installed one
    assert view["transcript"][0]["text"] == "run every 2 hours"

    status, body, _ = site.call("POST", f"/api/drafts/{draft_id}/approve", {}, BEARER)
    assert status == 200 and body["data"]["schedule"] == "every 2h"
    assert site.sprite.made == ["before update of digest"]
    versions = site.call("GET", f"/api/automations/{installed}", headers=BEARER)[1]["data"]["versions"]
    assert versions[0]["message"] == "update digest" and len(versions) == 3


def test_edit_checks_its_input(site, installed):
    path = f"/api/automations/{installed}/edit"
    assert site.call("POST", path, {"message": "x", "kind": "delete"}, BEARER)[0] == 400
    assert site.call("POST", path, {"message": "x", "run_id": "1"}, BEARER)[0] == 400
    assert site.call("POST", path, {"message": "x", "run_id": 999}, BEARER)[0] == 404
    assert site.call("POST", path, {"message": ""}, BEARER)[0] == 400


def test_discard_a_draft(site):
    site.gemini.replies = [reply({"text": "Which city?"})]
    draft_id = site.call("POST", "/api/drafts", {"message": "weather"}, BEARER)[1]["data"]["id"]
    status, body, _ = site.call("POST", f"/api/drafts/{draft_id}/discard", {}, BEARER)
    assert status == 200 and body["data"]["status"] == "discarded"
    assert site.call("POST", f"/api/drafts/{draft_id}/messages", {"message": "x"}, BEARER)[0] == 409


# ---- checkpoints ----------------------------------------------------------------

def test_checkpoints_list_create_and_restore(site):
    data = site.call("GET", "/api/checkpoints", headers=BEARER)[1]["data"]
    assert [c["id"] for c in data] == ["v2", "v1"]
    assert site.call("POST", "/api/checkpoints", {"comment": "by hand"}, BEARER)[0] == 201
    assert site.call("POST", "/api/checkpoints/v2/restore", {}, BEARER)[0] == 202
    assert site.sprite.restored == ["v2"]
    assert site.call("POST", "/api/checkpoints/latest/restore", {}, BEARER)[0] == 404


def test_egress_status_route(site, paths):
    folder = paths.automation_dir("w")
    folder.mkdir(parents=True)
    (folder / "main.py").write_text('URL = "https://wttr.in/x"\n')
    data = site.call("GET", "/api/egress", headers=BEARER)[1]["data"]
    assert data["wanted"] == ["wttr.in"] and data["mode"] in ("open", "locked")
