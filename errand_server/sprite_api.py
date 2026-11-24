"""Small client for the Sprite API socket (/.sprite/api.sock).

    GET  /info                           -> {"sprite_name", "sprite_url", ...}
    POST /v1/tasks, DELETE /v1/tasks/<n> -> keep-awake holds (see keepawake.py)
    GET  /v1/checkpoints                 -> [{"id", "create_time", "comment"?}, ...]
    POST /v1/checkpoint {"comment"}      -> NDJSON progress, then {"type": "complete"}
    POST /v1/checkpoints/<id>/restore    -> async; the whole Sprite restarts
"""

from __future__ import annotations

import http.client
import json
import re
import socket
from typing import Any

SOCKET_PATH = "/.sprite/api.sock"
CHECKPOINT_ID = re.compile(r"^v\d{1,6}$")
ERRAND_PREFIX = "errand: "


class SpriteApiError(Exception):
    pass


class _UnixConnection(http.client.HTTPConnection):
    def __init__(self, path: str, timeout: float) -> None:
        super().__init__("sprite", timeout=timeout)
        self._path = path

    def connect(self) -> None:
        sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        sock.settimeout(self.timeout)
        sock.connect(self._path)
        self.sock = sock


def request(method: str, path: str, body: Any = None, *, socket_path: str = SOCKET_PATH,
            timeout: float = 5) -> tuple[int, bytes]:
    """Send one request. Raise OSError if the socket is not there."""
    conn = _UnixConnection(socket_path, timeout)
    try:
        payload = json.dumps(body) if body is not None else None
        conn.request(method, path, payload, {"Content-Type": "application/json"})
        resp = conn.getresponse()
        return resp.status, resp.read()
    finally:
        conn.close()


class SpriteApi:
    def __init__(self, socket_path: str = SOCKET_PATH) -> None:
        self._socket = socket_path

    def info(self) -> dict:
        status, raw = request("GET", "/info", socket_path=self._socket)
        if status != 200:
            raise SpriteApiError(f"info failed (HTTP {status})")
        return json.loads(raw)

    def checkpoints(self) -> list[dict]:
        status, raw = request("GET", "/v1/checkpoints", socket_path=self._socket)
        if status != 200:
            raise SpriteApiError(f"checkpoint list failed (HTTP {status})")
        return [c for c in json.loads(raw) if CHECKPOINT_ID.fullmatch(c.get("id", ""))]

    def create_checkpoint(self, comment: str) -> str:
        """Create a checkpoint and return its id (for example "v22")."""
        status, raw = request("POST", "/v1/checkpoint", {"comment": ERRAND_PREFIX + comment},
                              socket_path=self._socket, timeout=60)
        events = [json.loads(line) for line in raw.decode().splitlines() if line.strip()]
        done = next((e for e in events if e.get("type") == "complete"), None)
        if status != 200 or done is None:
            raise SpriteApiError(f"checkpoint failed (HTTP {status}): {raw[-300:]!r}")
        match = re.search(r"\bv\d+\b", done.get("data", ""))
        if not match:
            raise SpriteApiError(f"checkpoint id missing in {done!r}")
        return match.group(0)

    def restore(self, checkpoint_id: str) -> None:
        """Start a restore. The Sprite restarts, and this server with it."""
        if not CHECKPOINT_ID.fullmatch(checkpoint_id):
            raise SpriteApiError("bad checkpoint id")
        status, raw = request("POST", f"/v1/checkpoints/{checkpoint_id}/restore",
                              socket_path=self._socket, timeout=30)
        if status not in (200, 202):
            raise SpriteApiError(f"restore failed (HTTP {status}): {raw[-300:]!r}")
