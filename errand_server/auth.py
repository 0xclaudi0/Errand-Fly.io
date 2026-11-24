"""Browser login for the owner.

There are no passwords. The owner runs `python -m errand_server login-link` on the
Sprite and opens the one-time link it prints. The link sets a session cookie.

Only SHA-256 hashes of codes and session tokens are stored (auth.json, mode 0600).
"""

from __future__ import annotations

import hashlib
import hmac
import json
import secrets
import threading
import time
from pathlib import Path
from typing import Callable

from .config import write_private_json

CODE_TTL_S = 10 * 60
EMAIL_CODE_TTL_S = 15 * 60  # email can be slow
SESSION_TTL_S = 30 * 24 * 3600
EMAIL_GAP_S = 60  # at most one login email a minute ...
EMAIL_PER_HOUR = 5  # ... and five an hour, so the button cannot flood the inbox
COOKIE = "errand_session"


def _digest(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


class Auth:
    def __init__(self, path: Path, clock: Callable[[], float] = time.time) -> None:
        self._path = path
        self._clock = clock
        self._lock = threading.Lock()

    def new_login_code(self, ttl_s: int = CODE_TTL_S) -> str:
        code = secrets.token_urlsafe(24)
        with self._lock:
            data = self._load()
            data["codes"][_digest(code)] = self._clock() + ttl_s
            self._save(data)
        return code

    def take_email_slot(self) -> int:
        """Record a login email. Return 0 if one may go now, else seconds to wait."""
        with self._lock:
            data = self._load()
            now = self._clock()
            sent = [t for t in data["sent"] if now - t < 3600]
            if sent and now - sent[-1] < EMAIL_GAP_S:
                return int(EMAIL_GAP_S - (now - sent[-1])) + 1
            if len(sent) >= EMAIL_PER_HOUR:
                return int(3600 - (now - sent[0])) + 1
            data["sent"] = sent + [now]
            self._save(data)
        return 0

    def redeem(self, code: str) -> str | None:
        """Use a login code once. Return a new session token, or None."""
        if not code:
            return None
        with self._lock:
            data = self._load()
            expires = data["codes"].pop(_digest(code), None)
            if expires is None or expires < self._clock():
                self._save(data)
                return None
            token = secrets.token_urlsafe(32)
            data["sessions"][_digest(token)] = self._clock() + SESSION_TTL_S
            self._save(data)
        return token

    def is_session(self, token: str | None) -> bool:
        if not token:
            return False
        with self._lock:
            sessions = self._load()["sessions"]
        wanted = _digest(token)
        for known, expires in sessions.items():
            if hmac.compare_digest(known, wanted) and expires >= self._clock():
                return True
        return False

    def end_session(self, token: str | None) -> None:
        if not token:
            return
        with self._lock:
            data = self._load()
            data["sessions"].pop(_digest(token), None)
            self._save(data)

    def _load(self) -> dict:
        data = json.loads(self._path.read_text()) if self._path.exists() else {}
        now = self._clock()
        fresh = {key: {k: v for k, v in (data.get(key) or {}).items() if v >= now}
                 for key in ("codes", "sessions")}
        fresh["sent"] = [t for t in data.get("sent") or [] if now - t < 3600]
        return fresh

    def _save(self, data: dict) -> None:
        write_private_json(self._path, data)


def session_cookie(token: str, max_age: int = SESSION_TTL_S) -> str:
    return (f"{COOKIE}={token}; Path=/; Max-Age={max_age}; HttpOnly; Secure; "
            "SameSite=Strict")


def cookie_value(header: str | None) -> str | None:
    for part in (header or "").split(";"):
        name, _, value = part.strip().partition("=")
        if name == COOKIE and value:
            return value
    return None
