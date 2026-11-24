import json
import os
import socketserver
import tempfile
import threading
from http.server import BaseHTTPRequestHandler

import pytest

from errand_server.config import Paths, load_or_create_secrets
from errand_server.keepawake import keep_awake


class TasksApi(BaseHTTPRequestHandler):
    calls: list = []
    create_status = 201

    def do_POST(self):  # noqa: N802
        length = int(self.headers.get("Content-Length") or 0)
        self.calls.append(("POST", self.path, json.loads(self.rfile.read(length))))
        self._reply(self.create_status)

    def do_DELETE(self):  # noqa: N802
        self.calls.append(("DELETE", self.path, None))
        self._reply(204)

    def _reply(self, status):
        self.send_response(status)
        self.send_header("Content-Length", "0")
        self.end_headers()

    def address_string(self):
        return "unix"

    def log_message(self, *args):
        pass


class UnixServer(socketserver.ThreadingMixIn, socketserver.UnixStreamServer):
    daemon_threads = True


@pytest.fixture
def sock_path():
    TasksApi.calls, TasksApi.create_status = [], 201
    folder = tempfile.mkdtemp(dir="/tmp")  # AF_UNIX paths must stay short
    path = os.path.join(folder, "api.sock")
    server = UnixServer(path, TasksApi)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    yield path
    server.shutdown()
    server.server_close()


def test_hold_is_created_and_deleted(sock_path):
    with keep_awake("errand-run-1", 180, socket_path=sock_path):
        assert TasksApi.calls == [("POST", "/v1/tasks", {"name": "errand-run-1", "expire": "180s"})]
    assert TasksApi.calls[-1] == ("DELETE", "/v1/tasks/errand-run-1", None)


def test_hold_is_capped_at_one_hour(sock_path):
    with keep_awake("long", 99999, socket_path=sock_path):
        pass
    assert TasksApi.calls[0][2]["expire"] == "3600s"


def test_hold_is_deleted_even_when_the_block_raises(sock_path):
    with pytest.raises(RuntimeError):
        with keep_awake("boom", 60, socket_path=sock_path):
            raise RuntimeError("x")
    assert TasksApi.calls[-1][0] == "DELETE"


def test_refused_hold_is_not_deleted(sock_path):
    TasksApi.create_status = 409
    with keep_awake("dup", 60, socket_path=sock_path):
        pass
    assert [c[0] for c in TasksApi.calls] == ["POST"]


def test_missing_socket_still_runs_the_block(tmp_path):
    ran = []
    with keep_awake("x", 60, socket_path=str(tmp_path / "missing.sock")):
        ran.append(True)
    assert ran == [True]


def test_secrets_are_created_once_with_private_mode(tmp_path):
    paths = Paths(tmp_path)
    first = load_or_create_secrets(paths)
    second = load_or_create_secrets(paths)
    assert first == second and first.tick_token != first.admin_token
    assert oct(paths.secrets.stat().st_mode & 0o777) == "0o600"
