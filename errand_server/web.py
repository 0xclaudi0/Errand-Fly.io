"""HTTP layer. The only service on the Sprite with an HTTP port.

    GET  /healthz                 open
    GET  /, /static/<file>        open: the web UI (it asks the API who you are)
    GET  /login?code=...          one-time login link -> session cookie
    POST /logout                  end the session
    GET|POST /tick                X-Errand-Token or ?token=  (tick token)
    POST /hooks/<hook_id>         the hook id is the secret
    /api/...                      admin: Bearer <admin token>, or the session cookie
                                  (a cookie request that changes data also needs the
                                  header X-Errand-Client, which a cross-site form cannot set)
"""

from __future__ import annotations

import hmac
import json
import logging
import re
from dataclasses import dataclass, field
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Callable
from urllib.parse import parse_qs, urlsplit

from .auth import COOKIE, cookie_value, session_cookie
from .config import MAX_BODY_BYTES, Secrets
from .db import Repository

log = logging.getLogger(__name__)

DISCARD_LIMIT_BYTES = 10 * MAX_BODY_BYTES
STATIC_DIR = Path(__file__).resolve().parent / "static"
STATIC_FILES = {
    "index.html": "text/html; charset=utf-8",
    "app.js": "text/javascript; charset=utf-8",
    "app.css": "text/css; charset=utf-8",
    "icon.svg": "image/svg+xml",
}
CSP = ("default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; "
       "connect-src 'self'; frame-ancestors 'none'; base-uri 'none'; form-action 'self'")
CLIENT_HEADER = "X-Errand-Client"


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
    auth: Any = None  # Auth
    paths: Any = None  # Paths
    sprite: Any = None  # SpriteApi
    base_url: Callable[[], str] = field(default=lambda: "")


@dataclass(frozen=True)
class Request:
    method: str
    path: str
    query: dict[str, list[str]]
    headers: Any
    read_body: Callable[[], bytes]


@dataclass(frozen=True)
class Raw:
    """A response that is not the JSON envelope: a page, a file, or a redirect."""
    status: int
    body: bytes
    content_type: str
    headers: tuple[tuple[str, str], ...] = ()


Response = tuple[int, Any]


def route(app: App, req: Request) -> Response | Raw:
    if req.path == "/healthz":
        return 200, "ok"
    if req.method == "GET" and (req.path == "/" or req.path.startswith("/static/")):
        return static_file("index.html" if req.path == "/" else req.path[len("/static/"):])
    if req.path == "/login" and req.method == "GET":
        return login(app, req)
    if req.path == "/logout" and req.method == "POST":
        return logout(app, req)
    if req.path == "/tick" and req.method in ("GET", "POST"):
        return tick(app, req)
    hook = re.fullmatch(r"/hooks/([A-Za-z0-9_-]{8,64})", req.path)
    if hook and req.method == "POST":
        return webhook(app, req, hook.group(1))
    if req.path.startswith("/api/"):
        authorize_api(app, req)
        from .api_routes import handle  # late import: api_routes imports this module

        return handle(app, req)
    raise HttpError(404, "not found")


def static_file(name: str) -> Raw:
    content_type = STATIC_FILES.get(name)
    if content_type is None:
        raise HttpError(404, "not found")
    return Raw(200, (STATIC_DIR / name).read_bytes(), content_type)


def login(app: App, req: Request) -> Raw:
    code = (req.query.get("code") or [""])[0]
    token = app.auth.redeem(code) if app.auth else None
    if token is None:
        return Raw(303, b"", "text/plain", (("Location", "/?login=expired"),))
    return Raw(303, b"", "text/plain",
               (("Location", "/"), ("Set-Cookie", session_cookie(token))))


def logout(app: App, req: Request) -> Raw:
    _require_client_header(req)
    if app.auth:
        app.auth.end_session(cookie_value(req.headers.get("Cookie")))
    return Raw(204, b"", "text/plain", (("Set-Cookie", session_cookie("", max_age=0)),))


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


def authorize_api(app: App, req: Request) -> None:
    bearer = req.headers.get("Authorization", "").removeprefix("Bearer ").strip()
    if bearer:
        _require(bearer, app.secrets.admin_token)
        return
    if app.auth and app.auth.is_session(cookie_value(req.headers.get("Cookie"))):
        if req.method != "GET":
            _require_client_header(req)
        return
    raise HttpError(401, "unauthorized")


def _require_client_header(req: Request) -> None:
    if req.headers.get(CLIENT_HEADER) != "web":
        raise HttpError(403, f"missing {CLIENT_HEADER} header")


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

        def do_DELETE(self) -> None:  # noqa: N802
            self._handle("DELETE")

        def _handle(self, method: str) -> None:
            url = urlsplit(self.path)
            req = Request(method, url.path, parse_qs(url.query), self.headers, self._read_body)
            try:
                result = route(app, req)
            except HttpError as err:
                result = (err.status, None, err.message)
            except Exception:
                log.exception("unhandled error on %s %s", method, url.path)
                result = (500, None, "internal error")
            if isinstance(result, Raw):
                self._send(result.status, result.body, result.content_type, result.headers)
                return
            status, data, *error = result
            body = {"ok": not error, "data": data, "error": error[0] if error else None}
            self._send(status, json.dumps(body).encode(), "application/json",
                       (("Cache-Control", "no-store"),))

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

        def _send(self, status: int, body: bytes, content_type: str,
                  headers: tuple[tuple[str, str], ...] = ()) -> None:
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("Referrer-Policy", "no-referrer")
            if content_type.startswith("text/html"):
                self.send_header("Content-Security-Policy", CSP)
            if not content_type.startswith("application/json"):
                self.send_header("Cache-Control", "no-cache")  # a deploy must show at once
            for name, value in headers:
                self.send_header(name, value)
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


__all__ = ["App", "COOKIE", "HttpError", "Raw", "Request", "make_server", "route"]
