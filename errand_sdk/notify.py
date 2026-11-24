"""send_email(): Resend through the Sprites connector."""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path

from . import gateway
from .runtime import dry_run_note, is_dry_run

DEFAULT_FROM = "Errand <onboarding@resend.dev>"


class NotifyError(Exception):
    pass


def send_email(subject: str, text: str, *, to: str | list[str] | None = None,
               html: str | None = None) -> str:
    """Send an email and return the Resend message id.

    With no `to`, the email goes to the `notify_email` setting.
    """
    settings = _settings()
    recipients = to or settings.get("notify_email")
    if not recipients:
        raise NotifyError("no recipient: run `python -m errand_server set notify_email you@...`")
    body = {
        "from": settings.get("email_from", DEFAULT_FROM),
        "to": [recipients] if isinstance(recipients, str) else list(recipients),
        "subject": subject,
        "text": text,
    }
    if html:
        body["html"] = html
    if is_dry_run():
        preview = f"To: {', '.join(body['to'])}\nSubject: {subject}\n\n{text[:3000]}"
        dry_run_note("Email", preview)
        return "dry-run"
    url = f"{gateway.base_url('resend')}/emails"
    reply = gateway.request_json("POST", url, body, timeout=30,
                                 headers={"Idempotency-Key": _idempotency_key(subject, text)})
    return (reply or {}).get("id", "")


def _settings() -> dict:
    path = Path(os.environ.get("ERRAND_HOME", Path.home() / ".errand")) / "settings.json"
    return json.loads(path.read_text()) if path.exists() else {}


def _idempotency_key(subject: str, text: str) -> str:
    """Same run + same message = one email, even if the script retries."""
    run = f"{os.environ.get('ERRAND_AUTOMATION_ID', 'cli')}-{os.environ.get('ERRAND_RUN_ID', '0')}"
    digest = hashlib.sha256(f"{subject}\n{text}".encode()).hexdigest()[:16]
    return f"{run}-{digest}"
