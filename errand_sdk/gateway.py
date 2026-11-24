"""Find Sprites connector connections and call them over HTTP.

Code in the Sprite calls https://api.sprites.dev/v1/gateway/<provider>/<id>/...
with no key. The gateway adds the credential. No secret ever lives on disk here.
"""

from __future__ import annotations

import json
import os
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

GATEWAY = "https://api.sprites.dev/v1/gateway"
CACHE_TTL_S = 3600
USER_AGENT = "errand/0.1"


class GatewayError(Exception):
    def __init__(self, status: int, message: str) -> None:
        super().__init__(f"HTTP {status}: {message}")
        self.status = status
        self.message = message


def request_json(method: str, url: str, body: Any = None, timeout: float = 60,
                 headers: dict[str, str] | None = None) -> Any:
    """Send JSON, return parsed JSON. Raise GatewayError for HTTP errors (status 0 = network)."""
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(url, data=data, method=method, headers={
        "Content-Type": "application/json", "User-Agent": USER_AGENT, **(headers or {}),
    })
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            raw = resp.read()
    except urllib.error.HTTPError as err:
        raise GatewayError(err.code, _error_text(err.read())) from err
    except (urllib.error.URLError, TimeoutError, ConnectionError) as err:
        raise GatewayError(0, f"network error: {err}") from err
    return json.loads(raw) if raw else None


def base_url(provider: str) -> str:
    """Return the gateway base URL for the first connection of `provider`."""
    for refresh in (False, True):
        for conn in _connections(refresh):
            if conn.get("provider") == provider:
                return conn.get("gateway_base_url") or f"{GATEWAY}/{provider}/{conn['id']}"
    raise GatewayError(404, f"no {provider!r} connector is available to this Sprite")


def _connections(refresh: bool) -> list[dict]:
    cache = Path(os.environ.get("ERRAND_HOME", Path.home() / ".errand")) / "connections.json"
    if not refresh and cache.exists():
        data = json.loads(cache.read_text())
        if time.time() - data.get("fetched_at", 0) < CACHE_TTL_S:
            return data["connections"]
    listing = request_json("GET", f"{GATEWAY}/list", timeout=30) or {}
    connections = [
        {k: c.get(k) for k in ("id", "provider", "gateway_base_url")}
        for c in listing.get("connections", [])
    ]
    cache.parent.mkdir(parents=True, exist_ok=True)
    cache.write_text(json.dumps({"fetched_at": time.time(), "connections": connections}))
    return connections


def _error_text(raw: bytes) -> str:
    text = raw.decode("utf-8", errors="replace")
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        return text[:500]
    if isinstance(data, dict):
        err = data.get("error")
        if isinstance(err, dict):
            return str(err.get("message", err))[:500]
        return str(err or data.get("message") or data)[:500]
    return text[:500]
