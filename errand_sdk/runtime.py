"""Facts about the current run that the helpers need."""

from __future__ import annotations

import os
import sys


def is_dry_run() -> bool:
    """True while the builder tests a draft. Helpers that send or change things only print."""
    return os.environ.get("ERRAND_DRY_RUN") == "1"


def dry_run_note(action: str, detail: str) -> None:
    print(f"[dry run] {action} was not done.\n{detail}", file=sys.stdout, flush=True)
