"""Per-automation memory (state.json) and the trigger event."""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any


def _state_path() -> Path:
    return Path(os.environ.get("ERRAND_STATE_FILE", "state.json"))


def load_state(default: Any = None) -> Any:
    path = _state_path()
    if not path.exists():
        return {} if default is None else default
    return json.loads(path.read_text())


def save_state(state: Any) -> None:
    """Write atomically, so a timeout in the middle cannot leave half a file."""
    path = _state_path()
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(state, indent=2))
    os.replace(tmp, path)


def event() -> Any:
    """The webhook payload for this run, or None for schedule and manual runs."""
    path = os.environ.get("ERRAND_EVENT_FILE")
    if not path:
        return None
    return json.loads(Path(path).read_text())
