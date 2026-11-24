"""HTTP layer. The only service on the Sprite with an HTTP port.

    GET  /healthz                         open
    GET  /                                open, no data
    GET|POST /tick                        X-Errand-Token or ?token=  (tick token)
    POST /hooks/<hook_id>                 the hook id is the secret
    GET  /api/automations                 Authorization: Bearer <admin token>
    GET  /api/automations/<id>/runs       admin
    POST /api/automations/<id>/run        admin
    GET  /api/drafts                      admin: builder drafts, newest first
    POST /api/drafts                      admin: {"message"} starts a draft (202)
    GET  /api/drafts/<id>                 admin: transcript, files, proposal, status
    POST /api/drafts/<id>/messages        admin: {"message"} continues a draft (202)
    POST /api/drafts/<id>/approve         admin: install the proposal
"""

from __future__ import annotations

import hmac
import json
import logging
import re
from dataclasses import dataclass
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any, Callable
from urllib.parse import parse_qs, urlsplit

from .builder import BuilderError
from .config import MAX_BODY_BYTES, Secrets
from .db import Repository
from .drafts import public_view, summary_view

log = logging.getLogger(__name__)

DISCARD_LIMIT_BYTES = 10 * MAX_BODY_BYTES
HOME_PAGE = b"<!doctype html><title>Errand</title><p>errand-server is running.</p>"


class HttpError(Exception):
    def __init__(self, status: int, message: str) -> None:
        super().__init__(message)
        self.status = status
        self.message = message


@dataclass
class App:
    repo: Repository
    dispatcher: Any  # Dispatcher, or a fake in tests
    secrets: Secrets
    builder: Any = None  # Builder, or a fake in tests
    drafts: Any = None  # DraftStore


@dataclass(frozen=True)
class Request:
    method: str
    path: str
    query: dict[str, list[str]]
    headers: Any
    read_body: Callable[[], bytes]


Response = tuple[int, Any]


def route(app: App, req: Request) -> Response:
    if req.path == "/healthz":
        return 200, "ok"
    if req.path == "/tick" and req.method in ("GET", "POST"):
        return tick(app, req)
    hook = re.fullmatch(r"/hooks/([A-Za-z0-9_-]{8,64})", req.path)
    if hook and req.method == "POST":
        return webhook(app, req, hook.group(1))
    if req.path.startswith("/api/"):
        return api(app, req)
    raise HttpError(404, "not found")


def tick(app: App, req: Request) -> Response:
    token = req.headers.get("X-Errand-Token") or (req.query.get("token") or [""])[0]
    _require(token, app.secrets.tick_token)
    return 202, {"started": app.dispatcher.tick()}


def webhook(app: App, req: Request, hook_id: str) -> Response:
    automation = app.repo.find_by_hook(hook_id)
    if automation is None or not automation.enabled:
        raise HttpError(404, "not found")
    event = _parse_event(req.read_body(), req.headers.get("Content-Type", ""))
    app.dispatcher.submit(automation, "webhook", event)
    return 202, {"accepted": automation.id}


def api(app: App, req: Request) -> Response:
    auth = req.headers.get("Authorization", "")
    _require(auth.removeprefix("Bearer ").strip(), app.secrets.admin_token)
    if req.path == "/api/automations" and req.method == "GET":
        return 200, [a.to_dict() for a in app.repo.list_automations()]
    if req.path.startswith("/api/drafts"):
        try:
            return drafts_api(app, req)
        except BuilderError as err:
            raise HttpError(err.status, err.message) from err
    match = re.fullmatch(r"/api/automations/([a-z0-9-]+)/(runs|run)", req.path)
    if not match:
        raise HttpError(404, "not found")
    automation = app.repo.get_automation(match.group(1))
    if automation is None:
        raise HttpError(404, "no such automation")
    if match.group(2) == "runs" and req.method == "GET":
        return 200, [r.to_dict() for r in app.repo.list_runs(automation.id)]
    if match.group(2) == "run" and req.method == "POST":
        app.dispatcher.submit(automation, "manual")
        return 202, {"accepted": automation.id}
    raise HttpError(405, "method not allowed")


def drafts_api(app: App, req: Request) -> Response:
    if app.builder is None:
        raise HttpError(503, "the builder is not available")
    if req.path == "/api/drafts":
        if req.method == "GET":
            return 200, [summary_view(d) for d in app.drafts.list()]
        if req.method == "POST":
            draft = app.builder.start(_json_body(req).get("message"))
            return 202, {"id": draft.id, "status": draft.status}
        raise HttpError(405, "method not allowed")
    match = re.fullmatch(r"/api/drafts/(d-[0-9a-f]{8})(?:/(messages|approve))?", req.path)
    if not match:
        raise HttpError(404, "not found")
    draft_id, action = match.groups()
    if action is None and req.method == "GET":
        draft = app.drafts.load(draft_id)
        if draft is None:
            raise HttpError(404, "no such draft")
        return 200, public_view(draft, app.drafts.files(draft_id))
    if action == "messages" and req.method == "POST":
        draft = app.builder.send(draft_id, _json_body(req).get("message"))
        return 202, {"id": draft.id, "status": draft.status}
    if action == "approve" and req.method == "POST":
        automation = app.builder.approve(draft_id)
        data = automation.to_dict()
        if automation.hook_id:
            data["hook_path"] = f"/hooks/{automation.hook_id}"
        return 200, data
    raise HttpError(405, "method not allowed")


def _json_body(req: Request) -> dict:
    try:
        data = json.loads(req.read_body() or b"{}")
    except (json.JSONDecodeError, UnicodeDecodeError) as err:
        raise HttpError(400, "body must be JSON") from err
    if not isinstance(data, dict):
        raise HttpError(400, "body must be a JSON object")
    return data


def _require(given: str, expected: str) -> None:
    if not given or not hmac.compare_digest(given.encode(), expected.encode()):
        raise HttpError(401, "unauthorized")


def _parse_event(body: bytes, content_type: str) -> Any:
    text = body.decode("utf-8", errors="replace")
    if "json" in content_type or text[:1] in ("{", "["):
        try:
            return json.loads(text)
        except json.JSONDecodeError:
            pass
    return {"body": text, "content_type": content_type}


def make_handler(app: App) -> type[BaseHTTPRequestHandler]:
    class Handler(BaseHTTPRequestHandler):
        server_version = "errand"

        def do_GET(self) -> None:  # noqa: N802 (http.server naming)
            self._handle("GET")

        def do_POST(self) -> None:  # noqa: N802
            self._handle("POST")

        def _handle(self, method: str) -> None:
            url = urlsplit(self.path)
            if url.path == "/" and method == "GET":
                self._send(200, HOME_PAGE, "text/html; charset=utf-8")
                return
            req = Request(method, url.path, parse_qs(url.query), self.headers, self._read_body)
            try:
                status, data = route(app, req)
                body = {"ok": True, "data": data, "error": None}
            except HttpError as err:
                status, body = err.status, {"ok": False, "data": None, "error": err.message}
            except Exception:
                log.exception("unhandled error on %s %s", method, url.path)
                status, body = 500, {"ok": False, "data": None, "error": "internal error"}
            self._send(status, json.dumps(body).encode(), "application/json")

        def _read_body(self) -> bytes:
            length = int(self.headers.get("Content-Length") or 0)
            if length > MAX_BODY_BYTES:
                self._discard(length)
                raise HttpError(413, "body too large")
            return self.rfile.read(length) if length else b""

        def _discard(self, length: int) -> None:
            """Read and drop a large body (up to a cap) so the client gets a clean 413."""
            left = min(length, DISCARD_LIMIT_BYTES)
            while left > 0:
                chunk = self.rfile.read(min(left, 65536))
                if not chunk:
                    break
                left -= len(chunk)

        def _send(self, status: int, body: bytes, content_type: str) -> None:
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(body)))
            if status == 413:
                self.send_header("Connection", "close")
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, fmt: str, *args: Any) -> None:
            # Log the path without the query string, so tokens never reach the log.
            log.info("%s %s -> %s", self.command, urlsplit(self.path).path, args[1:2])

    return Handler


def make_server(app: App, host: str, port: int) -> ThreadingHTTPServer:
    return ThreadingHTTPServer((host, port), make_handler(app))
