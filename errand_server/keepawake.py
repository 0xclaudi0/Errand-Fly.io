"""Keep the Sprite awake while a run works.

HTTP services do not keep a Sprite awake on their own. A task on the Sprite
API socket does, until it expires or we delete it:

    POST   /v1/tasks          {"name": ..., "expire": "120s"}  -> 201
    PUT    /v1/tasks/<name>   {"expire": "120s"}               -> 200 (extend)
    DELETE /v1/tasks/<name>                                    -> 204
"""

from __future__ import annotations

import logging
from contextlib import contextmanager
from typing import Iterator

from .sprite_api import SOCKET_PATH, request

MAX_HOLD_S = 3600

log = logging.getLogger(__name__)


def _request(method: str, path: str, body: dict | None, socket_path: str) -> int:
    return request(method, path, body, socket_path=socket_path)[0]


@contextmanager
def keep_awake(name: str, seconds: int, socket_path: str = SOCKET_PATH) -> Iterator[None]:
    """Hold a Sprite task for the block. If the API fails, log and run anyway."""
    seconds = max(1, min(seconds, MAX_HOLD_S))
    held = False
    try:
        status = _request("POST", "/v1/tasks", {"name": name, "expire": f"{seconds}s"}, socket_path)
        held = status == 201
        if not held:
            log.warning("keep_awake: task %s not created (HTTP %s)", name, status)
    except OSError as err:
        log.warning("keep_awake: Sprite API unavailable (%s); run continues without a hold", err)
    try:
        yield
    finally:
        if held:
            try:
                _request("DELETE", f"/v1/tasks/{name}", None, socket_path)
            except OSError as err:
                log.warning("keep_awake: could not delete task %s (%s); it expires", name, err)
