"""Paths, limits and secrets. All server state lives under ERRAND_HOME."""

from __future__ import annotations

import json
import os
import secrets
from dataclasses import dataclass
from pathlib import Path

DEFAULT_PORT = 8080
DEFAULT_TIMEOUT_S = 120
MAX_TIMEOUT_S = 3000  # A Sprite task hold lasts at most 1 h; keep a margin.
HOLD_MARGIN_S = 60
MAX_BODY_BYTES = 1_000_000
MAX_OUTPUT_CHARS = 32_000
MAX_PARALLEL_RUNS = 4


@dataclass(frozen=True)
class Paths:
    home: Path

    @property
    def db(self) -> Path:
        return self.home / "errand.db"

    @property
    def automations(self) -> Path:
        return self.home / "automations"

    @property
    def tmp(self) -> Path:
        return self.home / "tmp"

    @property
    def drafts(self) -> Path:
        return self.home / "drafts"

    @property
    def events(self) -> Path:
        """The last webhook payload of each automation, for repairs."""
        return self.home / "events"

    @property
    def auth(self) -> Path:
        return self.home / "auth.json"

    @property
    def secrets(self) -> Path:
        return self.home / "secrets.json"

    @property
    def settings(self) -> Path:
        return self.home / "settings.json"

    def automation_dir(self, automation_id: str) -> Path:
        return self.automations / automation_id

    def ensure(self) -> None:
        for folder in (self.home, self.automations, self.tmp, self.drafts, self.events):
            folder.mkdir(parents=True, exist_ok=True)


def paths_from_env() -> Paths:
    return Paths(Path(os.environ.get("ERRAND_HOME", Path.home() / ".errand")))


@dataclass(frozen=True)
class Secrets:
    tick_token: str
    admin_token: str


def load_or_create_secrets(paths: Paths) -> Secrets:
    """Read the tokens, or create them on first start (file mode 0600)."""
    if paths.secrets.exists():
        data = json.loads(paths.secrets.read_text())
        return Secrets(tick_token=data["tick_token"], admin_token=data["admin_token"])
    created = Secrets(tick_token=secrets.token_urlsafe(24), admin_token=secrets.token_urlsafe(32))
    write_private_json(paths.secrets, created.__dict__)
    return created


def write_private_json(path: Path, data: dict) -> None:
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as fh:
        json.dump(data, fh, indent=2)
