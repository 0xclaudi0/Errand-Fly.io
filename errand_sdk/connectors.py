"""call(): any connected service through the Sprites gateway.

    call("github", "GET", "/repos/fly-apps/flyctl/releases/latest")
    call("slack", "POST", "/chat.postMessage", {"channel": "#ops", "text": "hi"})

The gateway adds the credential. In a dry run, only GET requests go out; other
methods print what they would send and return {"dry_run": True}.
"""

from __future__ import annotations

import json
from typing import Any
from urllib.parse import urlencode

from . import gateway
from .runtime import dry_run_note, is_dry_run

READ_METHODS = {"GET", "HEAD"}


def call(provider: str, method: str, path: str, body: Any = None, *,
         query: dict[str, Any] | None = None, timeout: float = 60) -> Any:
    method = method.upper()
    url = f"{gateway.base_url(provider)}/{path.lstrip('/')}"
    if query:
        url = f"{url}?{urlencode(query)}"
    if method not in READ_METHODS and is_dry_run():
        dry_run_note(f"{method} {provider} {path}", json.dumps(body, indent=2)[:2000])
        return {"dry_run": True}
    return gateway.request_json(method, url, body, timeout=timeout)


def connected_services() -> list[str]:
    return gateway.providers()
