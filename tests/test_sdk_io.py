import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from errand_sdk import gateway, notify, web
from errand_sdk.gateway import GatewayError


class Recorder(BaseHTTPRequestHandler):
    routes: dict = {}
    seen: list = []

    def do_GET(self):  # noqa: N802
        self._answer()

    def do_POST(self):  # noqa: N802
        self._answer()

    def _answer(self):
        length = int(self.headers.get("Content-Length") or 0)
        body = json.loads(self.rfile.read(length)) if length else None
        self.seen.append((self.command, self.path, dict(self.headers), body))
        status, reply = self.routes.get(self.path, (404, {"error": {"message": "nope"}}))
        raw = json.dumps(reply).encode() if not isinstance(reply, bytes) else reply
        self.send_response(status)
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    def log_message(self, *args):
        pass


@pytest.fixture
def fake_gateway(monkeypatch, tmp_path):
    Recorder.routes, Recorder.seen = {}, []
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), Recorder)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    base = f"http://127.0.0.1:{httpd.server_address[1]}"
    monkeypatch.setattr(gateway, "GATEWAY", base)
    monkeypatch.setenv("ERRAND_HOME", str(tmp_path))
    yield base
    httpd.shutdown()


def listing(base, *providers):
    conns = [{"id": f"c-{p}", "provider": p, "gateway_base_url": f"{base}/{p}/c-{p}",
              "secret_field": "x"} for p in providers]
    return (200, {"connections": conns})


def test_base_url_finds_and_caches_the_connection(fake_gateway, tmp_path):
    Recorder.routes["/list"] = listing(fake_gateway, "gemini", "resend")
    assert gateway.base_url("resend") == f"{fake_gateway}/resend/c-resend"
    assert gateway.base_url("gemini") == f"{fake_gateway}/gemini/c-gemini"
    assert [p for _, p, _, _ in Recorder.seen] == ["/list"]
    cached = json.loads((tmp_path / "connections.json").read_text())
    assert "secret_field" not in cached["connections"][0]


def test_base_url_refreshes_once_then_fails(fake_gateway):
    Recorder.routes["/list"] = listing(fake_gateway, "gemini")
    with pytest.raises(GatewayError, match="no 'resend' connector"):
        gateway.base_url("resend")
    assert len(Recorder.seen) == 2


@pytest.mark.parametrize(
    "reply, expected",
    [
        ({"error": {"message": "quota"}}, "quota"),
        ({"error": "plain"}, "plain"),
        ({"message": "resend style"}, "resend style"),
        (b"not json", "not json"),
    ],
)
def test_request_json_turns_http_errors_into_gateway_errors(fake_gateway, reply, expected):
    Recorder.routes["/x"] = (429, reply)
    with pytest.raises(GatewayError) as info:
        gateway.request_json("GET", f"{fake_gateway}/x")
    assert info.value.status == 429 and info.value.message == expected


def test_request_json_network_error_is_status_zero():
    with pytest.raises(GatewayError) as info:
        gateway.request_json("GET", "http://127.0.0.1:9/nothing", timeout=2)
    assert info.value.status == 0


def test_send_email_uses_settings_and_an_idempotency_key(fake_gateway, tmp_path, monkeypatch):
    Recorder.routes["/list"] = listing(fake_gateway, "resend")
    Recorder.routes["/resend/c-resend/emails"] = (200, {"id": "em_1"})
    (tmp_path / "settings.json").write_text(json.dumps({"notify_email": "me@test.dev"}))
    monkeypatch.setenv("ERRAND_AUTOMATION_ID", "digest")
    monkeypatch.setenv("ERRAND_RUN_ID", "7")
    assert notify.send_email("Hi", "Body") == "em_1"
    _, _, headers, body = Recorder.seen[-1]
    assert body == {"from": notify.DEFAULT_FROM, "to": ["me@test.dev"], "subject": "Hi",
                    "text": "Body"}
    assert headers["Idempotency-Key"].startswith("digest-7-")


def test_send_email_without_recipient_fails_clearly(fake_gateway):
    with pytest.raises(notify.NotifyError, match="notify_email"):
        notify.send_email("Hi", "Body")


def test_fetch_json_and_size_limit(fake_gateway, monkeypatch):
    Recorder.routes["/data"] = (200, {"a": 1})
    assert web.fetch_json(f"{fake_gateway}/data") == {"a": 1}
    monkeypatch.setattr(web, "MAX_BYTES", 3)
    with pytest.raises(ValueError, match="more than 3 bytes"):
        web.fetch_text(f"{fake_gateway}/data")
