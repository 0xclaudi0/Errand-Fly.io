import json
import os
import socketserver
import tempfile
import threading
from http.server import BaseHTTPRequestHandler

import pytest

from errand_server.sprite_api import SpriteApi, SpriteApiError

CHECKPOINTS = [{"id": "Current", "create_time": "2026-11-24T19:00:00Z"},
               {"id": "v3", "create_time": "2026-11-24T18:00:00Z", "comment": "errand: x"},
               {"id": "v1", "create_time": "2026-11-23T18:00:00Z"}]


class FakeSprite(BaseHTTPRequestHandler):
    calls: list = []
    create_status = 200

    def do_GET(self):  # noqa: N802
        if self.path == "/info":
            self._reply(200, json.dumps({"sprite_url": "https://e.sprites.app"}).encode())
        elif self.path == "/v1/checkpoints":
            self._reply(200, json.dumps(CHECKPOINTS).encode())
        else:
            self._reply(404, b"nope")

    def do_POST(self):  # noqa: N802
        length = int(self.headers.get("Content-Length") or 0)
        body = json.loads(self.rfile.read(length)) if length else None
        self.calls.append((self.path, body))
        if self.path == "/v1/checkpoint":
            lines = [{"type": "info", "data": "Creating checkpoint..."},
                     {"type": "complete", "data": "Checkpoint v4 created successfully"}]
            raw = "\n".join(json.dumps(x) for x in lines).encode()
            self._reply(self.create_status, raw)
        elif self.path.endswith("/restore"):
            self._reply(200, b"{}")

    def _reply(self, status, raw):
        self.send_response(status)
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    def address_string(self):
        return "unix"

    def log_message(self, *args):
        pass


class UnixServer(socketserver.ThreadingMixIn, socketserver.UnixStreamServer):
    daemon_threads = True


@pytest.fixture
def sprite():
    FakeSprite.calls, FakeSprite.create_status = [], 200
    path = os.path.join(tempfile.mkdtemp(dir="/tmp"), "api.sock")
    server = UnixServer(path, FakeSprite)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    yield SpriteApi(path)
    server.shutdown()
    server.server_close()


def test_info_and_checkpoints(sprite):
    assert sprite.info()["sprite_url"] == "https://e.sprites.app"
    assert [c["id"] for c in sprite.checkpoints()] == ["v3", "v1"]


def test_create_checkpoint_returns_the_id_and_marks_the_comment(sprite):
    assert sprite.create_checkpoint("before install of x") == "v4"
    assert FakeSprite.calls == [("/v1/checkpoint", {"comment": "errand: before install of x"})]


def test_create_checkpoint_failure_raises(sprite):
    FakeSprite.create_status = 500
    with pytest.raises(SpriteApiError):
        sprite.create_checkpoint("x")


def test_restore_checks_the_id(sprite):
    sprite.restore("v3")
    assert FakeSprite.calls == [("/v1/checkpoints/v3/restore", None)]
    with pytest.raises(SpriteApiError):
        sprite.restore("../v1")
