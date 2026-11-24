"""Plain HTTP fetches for public pages and JSON APIs."""

from __future__ import annotations

import json
import urllib.request
from typing import Any

from .gateway import USER_AGENT

MAX_BYTES = 5_000_000
TIMEOUT_S = 30


def fetch_text(url: str, timeout: float = TIMEOUT_S) -> str:
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        raw = resp.read(MAX_BYTES + 1)
    if len(raw) > MAX_BYTES:
        raise ValueError(f"{url} returned more than {MAX_BYTES} bytes")
    return raw.decode("utf-8", errors="replace")


def fetch_json(url: str, timeout: float = TIMEOUT_S) -> Any:
    return json.loads(fetch_text(url, timeout))
