"""Drafts: one builder conversation and the code it writes.

    drafts/<id>/draft.json   conversation, transcript, proposal, dry-run results
    drafts/<id>/code/        the files the agent writes (installed on approval)
    drafts/<id>/state.json   state from dry runs (reset before each dry run)
"""

from __future__ import annotations

import dataclasses
import json
import os
import re
import secrets
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .config import Paths

DRAFT_ID = re.compile(r"^d-[0-9a-f]{8}$")
STATUSES = ("working", "waiting", "proposed", "installed", "error")


@dataclass(frozen=True)
class Draft:
    id: str
    status: str
    created_at: float
    updated_at: float
    contents: tuple = ()     # ({"model": str | None, "content": <Gemini Content>}, ...)
    transcript: tuple = ()   # ({"role": "user" | "agent" | "step", "text": str, "at": float}, ...)
    proposal: dict | None = None
    dry_runs: tuple = ()     # ({"status", "exit_code", "seconds", "output", "event"}, ...)
    tested: bool = False     # a dry run passed after the last file change
    installed: str | None = None
    error: str | None = None

    def with_changes(self, **changes: Any) -> "Draft":
        return dataclasses.replace(self, updated_at=time.time(), **changes)

    def note(self, role: str, text: str, **changes: Any) -> "Draft":
        entry = {"role": role, "text": text, "at": time.time()}
        return self.with_changes(transcript=self.transcript + (entry,), **changes)

    def to_dict(self) -> dict:
        return dataclasses.asdict(self)


class DraftStore:
    def __init__(self, paths: Paths) -> None:
        self._root = paths.drafts

    def create(self) -> Draft:
        now = time.time()
        draft = Draft(id=f"d-{secrets.token_hex(4)}", status="waiting",
                      created_at=now, updated_at=now)
        self.code_dir(draft.id).mkdir(parents=True)
        self.save(draft)
        return draft

    def load(self, draft_id: str) -> Draft | None:
        if not DRAFT_ID.fullmatch(draft_id or ""):
            return None
        path = self._file(draft_id)
        if not path.exists():
            return None
        data = json.loads(path.read_text())
        for key in ("contents", "transcript", "dry_runs"):
            data[key] = tuple(data.get(key) or ())
        known = {f.name for f in dataclasses.fields(Draft)}
        return Draft(**{k: v for k, v in data.items() if k in known})

    def save(self, draft: Draft) -> None:
        path = self._file(draft.id)
        tmp = path.with_name(path.name + ".tmp")
        tmp.write_text(json.dumps(draft.to_dict(), indent=1))
        os.replace(tmp, path)

    def list(self) -> list[Draft]:
        drafts = [self.load(p.name) for p in self._root.iterdir() if p.is_dir()]
        return sorted((d for d in drafts if d), key=lambda d: d.updated_at, reverse=True)

    def folder(self, draft_id: str) -> Path:
        return self._root / draft_id

    def code_dir(self, draft_id: str) -> Path:
        return self.folder(draft_id) / "code"

    def files(self, draft_id: str) -> dict[str, str]:
        code = self.code_dir(draft_id)
        return {p.name: p.read_text() for p in sorted(code.glob("*.py"))}

    def _file(self, draft_id: str) -> Path:
        return self.folder(draft_id) / "draft.json"


def public_view(draft: Draft, files: dict[str, str]) -> dict:
    """What the API and UI show. The raw model conversation stays on disk."""
    return {
        "id": draft.id,
        "status": draft.status,
        "created_at": draft.created_at,
        "updated_at": draft.updated_at,
        "transcript": list(draft.transcript),
        "proposal": draft.proposal,
        "dry_runs": list(draft.dry_runs[-3:]),
        "tested": draft.tested,
        "installed": draft.installed,
        "error": draft.error,
        "files": files,
    }


def summary_view(draft: Draft) -> dict:
    first = next((t["text"] for t in draft.transcript if t["role"] == "user"), "")
    return {"id": draft.id, "status": draft.status, "updated_at": draft.updated_at,
            "request": first[:200], "installed": draft.installed}

