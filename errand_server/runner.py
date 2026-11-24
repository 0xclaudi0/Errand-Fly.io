"""Run one automation script in its own process group, with a hard timeout."""

from __future__ import annotations

import os
import signal
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

from .config import MAX_OUTPUT_CHARS

KILL_GRACE_S = 5


@dataclass(frozen=True)
class RunOutcome:
    status: str  # "ok" | "error" | "timeout"
    exit_code: int | None
    output: str


def execute(folder: Path, env: dict[str, str], timeout_s: int,
            max_output: int = MAX_OUTPUT_CHARS) -> RunOutcome:
    """Run `main.py` in `folder` with exactly `env`. Never raises for script faults."""
    script = folder / "main.py"
    if not script.is_file():
        return RunOutcome("error", None, f"errand: {script} does not exist")
    proc = subprocess.Popen(
        [sys.executable, "main.py"],
        cwd=folder,
        env=env,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        start_new_session=True,  # own process group, so a timeout kills children too
    )
    try:
        raw, _ = proc.communicate(timeout=timeout_s)
    except subprocess.TimeoutExpired:
        _kill_group(proc.pid)
        raw, _ = proc.communicate(timeout=KILL_GRACE_S)
        text = _tail(raw, max_output) + f"\nerrand: stopped after {timeout_s}s timeout"
        return RunOutcome("timeout", proc.returncode, text)
    status = "ok" if proc.returncode == 0 else "error"
    return RunOutcome(status, proc.returncode, _tail(raw, max_output))


def _kill_group(pid: int) -> None:
    try:
        os.killpg(pid, signal.SIGKILL)
    except ProcessLookupError:
        pass


def _tail(raw: bytes | None, limit: int) -> str:
    text = (raw or b"").decode("utf-8", errors="replace")
    if len(text) <= limit:
        return text
    return f"errand: output truncated, last {limit} chars follow\n" + text[-limit:]
