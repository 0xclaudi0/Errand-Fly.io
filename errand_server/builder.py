"""The builder agent: plain English in, a tested automation proposal out.

One turn = the owner's message, then model calls and tool calls until the model
answers in text (a question, or the summary after propose). The owner approves
a proposal with approve(); only then is the code installed.
"""

from __future__ import annotations

import copy
import json
import logging
import threading
import time
from typing import Any, Callable

from errand_sdk.llm import LLMError, ModelHealth, Post, generate, reply_text

from . import keepawake
from .config import Paths
from .db import Repository
from .drafts import Draft, DraftStore
from .install import install
from .models import Automation
from .prompts import system_prompt
from .builder_tools import DECLARATIONS, Toolbox

BUILDER_MODELS = ("gemini-flash-lite-latest", "gemini-3.1-flash-lite",
                  "gemini-flash-latest", "gemini-3.5-flash")
MAX_STEPS = 16
MODEL_TIMEOUT_S = 40  # lite models answer in 1-10 s; a hung call should not cost a minute
MAX_MESSAGE_CHARS = 4000
BUILD_HOLD_S = 900
# Gemini accepts this in place of a thought signature that another model made.
FOREIGN_SIGNATURE = "skip_thought_signature_validator"

log = logging.getLogger(__name__)


class BuilderError(Exception):
    def __init__(self, status: int, message: str) -> None:
        super().__init__(message)
        self.status = status
        self.message = message


class Builder:
    def __init__(self, store: DraftStore, paths: Paths, repo: Repository, toolbox: Toolbox, *,
                 post: Post | None = None, models: tuple[str, ...] = BUILDER_MODELS,
                 keep_awake: Callable = keepawake.keep_awake,
                 settings: Callable[[], dict] = dict, background: bool = True,
                 sleep: Callable[[float], None] = time.sleep,
                 health: ModelHealth | None = None) -> None:
        self._store = store
        self._paths = paths
        self._repo = repo
        self._tools = toolbox
        self._post = post
        self._models = models
        self._keep_awake = keep_awake
        self._settings = settings
        self._background = background
        self._sleep = sleep
        self._health = health or ModelHealth()
        self._locks: dict[str, threading.Lock] = {}
        self._guard = threading.Lock()

    # ---- public API -------------------------------------------------------

    def start(self, message: str) -> Draft:
        _check_message(message)
        return self.send(self._store.create().id, message)

    def send(self, draft_id: str, message: str) -> Draft:
        """Add the owner's message and start a turn (in the background by default)."""
        _check_message(message)
        lock = self._acquire(draft_id)
        try:
            draft = self._load(draft_id)
            if draft.status == "installed":
                raise BuilderError(409, "this draft is installed; start a new one")
            entry = {"model": None, "content": {"role": "user", "parts": [{"text": message}]}}
            draft = draft.note("user", message, status="working", error=None,
                               contents=draft.contents + (entry,))
            self._store.save(draft)
        except BaseException:
            lock.release()
            raise
        if self._background:
            threading.Thread(target=self._turn_then_release, args=(draft, lock),
                             name=f"build-{draft_id}", daemon=True).start()
        else:
            self._turn_then_release(draft, lock)
        return self._load(draft_id)

    def approve(self, draft_id: str) -> Automation:
        lock = self._acquire(draft_id)
        try:
            draft = self._load(draft_id)
            proposal = draft.proposal
            if draft.status != "proposed" or not proposal or not draft.tested:
                raise BuilderError(409, "nothing to approve: the draft has no tested proposal")
            if self._repo.get_automation(proposal["id"]) is not None:
                raise BuilderError(409, f"an automation {proposal['id']!r} exists already")
            automation = install(
                self._repo, self._paths, self._store.code_dir(draft_id),
                automation_id=proposal["id"], name=proposal["name"],
                trigger=proposal["trigger"], schedule=proposal["schedule"],
                timeout_s=proposal["timeout_s"], description=proposal["summary"],
            )
            self._store.save(draft.note("step", f"Installed {automation.id}",
                                        status="installed", installed=automation.id))
            return automation
        finally:
            lock.release()

    def recover(self) -> int:
        """After a restart, no turn is running: mark stuck drafts so the owner can retry."""
        count = 0
        for draft in self._store.list():
            if draft.status == "working":
                self._store.save(draft.with_changes(
                    status="error", error="a restart stopped this turn; send a message to retry"))
                count += 1
        return count

    # ---- the turn ---------------------------------------------------------

    def _turn_then_release(self, draft: Draft, lock: threading.Lock) -> None:
        try:
            with self._keep_awake(f"errand-build-{draft.id}", BUILD_HOLD_S):
                draft = self._turn(draft)
        except Exception:
            log.exception("builder turn for %s crashed", draft.id)
            draft = draft.with_changes(status="error", error="internal error in the builder")
        finally:
            self._store.save(draft)
            lock.release()

    def _turn(self, draft: Draft) -> Draft:
        for _ in range(MAX_STEPS):
            try:
                model, content = generate(
                    lambda m: self._body(draft, m), models=self._models, post=self._post,
                    timeout_s=MODEL_TIMEOUT_S, health=self._health, sleep=self._sleep)
            except LLMError as err:
                return draft.with_changes(status="error", error=str(err))
            draft = draft.with_changes(
                contents=draft.contents + ({"model": model, "content": content},))
            calls = [p["functionCall"] for p in content.get("parts", []) if p.get("functionCall")]
            text = reply_text(content)
            if not calls:
                status = "proposed" if draft.proposal else "waiting"
                return draft.note("agent", text or "(no reply)", status=status)
            if text:
                draft = draft.note("agent", text)
            draft = self._run_calls(draft, calls)
            self._store.save(draft)  # the UI polls the draft and shows each step
        return draft.note("agent", f"I stopped after {MAX_STEPS} steps without finishing. "
                                   "Reply 'continue' to let me go on, or change the request.",
                          status="waiting")

    def _run_calls(self, draft: Draft, calls: list[dict]) -> Draft:
        parts = []
        for call in calls:
            name = call.get("name", "")
            draft, result = self._tools.run(draft, name, call.get("args") or {})
            log.info("draft %s tool %s -> %s", draft.id, name, json.dumps(result)[:200])
            response: dict[str, Any] = {"name": name, "response": result}
            if call.get("id"):
                response["id"] = call["id"]
            parts.append({"functionResponse": response})
        entry = {"model": None, "content": {"role": "user", "parts": parts}}
        return draft.with_changes(contents=draft.contents + (entry,))

    def _body(self, draft: Draft, model: str) -> dict:
        timezone = self._settings().get("timezone")
        return {
            "systemInstruction": {"parts": [{"text": system_prompt(timezone)}]},
            "contents": [_for_model(entry, model) for entry in draft.contents],
            "tools": [{"functionDeclarations": DECLARATIONS}],
        }

    # ---- helpers ----------------------------------------------------------

    def _acquire(self, draft_id: str) -> threading.Lock:
        with self._guard:
            lock = self._locks.setdefault(draft_id, threading.Lock())
        if not lock.acquire(blocking=False):
            raise BuilderError(409, "the builder is still working on this draft")
        return lock

    def _load(self, draft_id: str) -> Draft:
        draft = self._store.load(draft_id)
        if draft is None:
            raise BuilderError(404, "no such draft")
        return draft


def _check_message(message: Any) -> None:
    if not isinstance(message, str) or not message.strip():
        raise BuilderError(400, "message must be a non-empty string")
    if len(message) > MAX_MESSAGE_CHARS:
        raise BuilderError(400, f"message is longer than {MAX_MESSAGE_CHARS} characters")


def _for_model(entry: dict, model: str) -> dict:
    """Return the stored content, with signatures made safe if another model wrote it."""
    if entry["model"] in (None, model):
        return entry["content"]
    content = copy.deepcopy(entry["content"])
    for part in content.get("parts", []):
        if part.get("functionCall"):
            part["thoughtSignature"] = FOREIGN_SIGNATURE
        else:
            part.pop("thoughtSignature", None)
    return content
