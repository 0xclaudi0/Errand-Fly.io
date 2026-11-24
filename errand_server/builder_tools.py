"""The tools that the builder agent can call, and their Gemini declarations."""

from __future__ import annotations

import json
import time
from typing import Any, Callable

from urllib.parse import urlsplit

from . import codecheck, egress
from .config import MAX_TIMEOUT_S, Paths
from .db import Repository
from .drafts import Draft, DraftStore
from .jobs import script_env
from .models import validate_id
from .runner import RunOutcome
from .schedule import ScheduleError, parse_schedule

DRY_RUN_TIMEOUT_S = 120
TOOL_OUTPUT_CHARS = 4000
PEEK_CHARS = 4000
SDK_NOTES = {
    "gemini": "use ask_llm() from errand_sdk",
    "resend": "use send_email() from errand_sdk",
}

_STR = {"type": "string"}
DECLARATIONS = [
    {"name": "list_connectors",
     "description": "List the services this Sprite can call through its connectors.",
     "parameters": {"type": "object", "properties": {}}},
    {"name": "peek_url",
     "description": "GET a public URL and return the first 4000 characters, to learn its format.",
     "parameters": {"type": "object", "properties": {"url": _STR}, "required": ["url"]}},
    {"name": "write_file",
     "description": "Create or replace a Python file of the automation. main.py is the entry point.",
     "parameters": {"type": "object", "properties": {
         "name": {"type": "string", "description": "File name, for example main.py"},
         "content": {"type": "string", "description": "The full file content"}},
         "required": ["name", "content"]}},
    {"name": "dry_run",
     "description": "Run main.py once in dry-run mode and return its output. Emails and "
                    "non-GET connector calls only print. State starts empty unless "
                    "state_json is given.",
     "parameters": {"type": "object", "properties": {
         "event_json": {"type": "string",
                        "description": "Optional sample webhook payload as a JSON string"},
         "state_json": {"type": "string",
                        "description": "Optional saved state (JSON) to start from, to test "
                                       "the path of a later run"}}}},
    {"name": "propose",
     "description": "Propose the tested automation to the owner for approval.",
     "parameters": {"type": "object", "properties": {
         "id": {"type": "string", "description": "Lowercase slug, for example hn-digest"},
         "name": {"type": "string", "description": "Short human name"},
         "summary": {"type": "string", "description": "One or two plain sentences"},
         "trigger": {"type": "string", "enum": ["schedule", "webhook", "manual"]},
         "schedule": {"type": "string", "description": "Needed when trigger is schedule"},
         "timeout_s": {"type": "integer", "description": "Run time limit in seconds"},
         "services": {"type": "array", "items": _STR,
                      "description": "Connectors and sites the script uses"}},
         "required": ["id", "name", "summary", "trigger"]}},
]

Result = tuple[Draft, dict[str, Any]]


class Toolbox:
    def __init__(self, store: DraftStore, paths: Paths, repo: Repository, *,
                 execute: Callable[..., RunOutcome], providers: Callable[[], list[str]],
                 fetch: Callable[[str], str]) -> None:
        self._store = store
        self._paths = paths
        self._repo = repo
        self._execute = execute
        self._providers = providers
        self._fetch = fetch

    def run(self, draft: Draft, name: str, args: dict) -> Result:
        handler = getattr(self, f"_tool_{name}", None)
        if handler is None:
            return draft, {"error": f"unknown tool {name!r}"}
        try:
            return handler(draft, args or {})
        except Exception as err:  # a tool fault goes back to the model, not up the stack
            return draft, {"error": f"{type(err).__name__}: {err}"}

    def _tool_list_connectors(self, draft: Draft, args: dict) -> Result:
        services = self._providers()
        notes = {s: SDK_NOTES.get(s, f'use call("{s}", METHOD, PATH)') for s in services}
        return draft, {"services": services, "how_to_use": notes,
                       "also": "fetch_text/fetch_json read any public URL"}

    def _tool_peek_url(self, draft: Draft, args: dict) -> Result:
        url = str(args.get("url", ""))
        if not url.startswith(("https://", "http://")):
            return draft, {"error": "url must start with https:// or http://"}
        try:
            text = self._fetch(url)
        except Exception as err:
            result = {"error": f"{type(err).__name__}: {err}"}
            hint = egress.dns_hint(str(err), urlsplit(url).hostname)
            return draft, {**result, "hint": hint} if hint else result
        draft = draft.note("step", f"Looked at {url[:120]}")
        return draft, {"length": len(text), "start": text[:PEEK_CHARS]}

    def _tool_write_file(self, draft: Draft, args: dict) -> Result:
        name, content = str(args.get("name", "")), str(args.get("content", ""))
        problem = codecheck.check_file_name(name)
        if problem:
            return draft, {"error": problem}
        code = self._store.code_dir(draft.id)
        if not (code / name).exists() and len(list(code.glob("*.py"))) >= codecheck.MAX_FILES:
            return draft, {"error": f"at most {codecheck.MAX_FILES} files"}
        problems = codecheck.check_code(name, content)
        if problems:
            return draft, {"error": "the code check rejected the file", "problems": problems}
        (code / name).write_text(content)
        lines = content.count("\n") + 1
        draft = draft.note("step", f"Wrote {name} ({lines} lines)", tested=False, proposal=None)
        return draft, {"ok": True, "lines": lines}

    def _tool_dry_run(self, draft: Draft, args: dict) -> Result:
        folder = self._store.folder(draft.id)
        try:
            event = _json_arg(args, "event_json")
            state = _json_arg(args, "state_json")
        except ValueError as err:
            return draft, {"error": str(err)}
        event_file = None
        if event is not None:
            event_file = folder / "event.json"
            event_file.write_text(json.dumps(event))
        state_file = folder / "state.json"
        state_file.unlink(missing_ok=True)
        if state is not None:
            state_file.write_text(json.dumps(state))
        run_no = len(draft.dry_runs) + 1
        env = script_env(self._paths, f"draft-{draft.id}", f"dry-{run_no}", "dry-run",
                         state_file, event_file, extra={"ERRAND_DRY_RUN": "1"})
        started = time.monotonic()
        outcome = self._execute(self._store.code_dir(draft.id), env, DRY_RUN_TIMEOUT_S)
        seconds = round(time.monotonic() - started, 1)
        output = outcome.output[-TOOL_OUTPUT_CHARS:]
        record = {"status": outcome.status, "exit_code": outcome.exit_code,
                  "seconds": seconds, "output": output, "event": bool(event_file)}
        word = {"ok": "passed", "timeout": "timed out"}.get(outcome.status, "failed")
        draft = draft.note("step", f"Dry run {run_no} {word} in {seconds} s",
                           dry_runs=draft.dry_runs + (record,), tested=outcome.status == "ok")
        result = {k: record[k] for k in ("status", "exit_code", "seconds", "output")}
        hint = egress.dns_hint(outcome.output) if outcome.status != "ok" else None
        return draft, {**result, "hint": hint} if hint else result

    def _tool_propose(self, draft: Draft, args: dict) -> Result:
        if not draft.tested:
            return draft, {"error": "run dry_run after the last change, and make it pass first"}
        if draft.target:  # a change or repair keeps the installed id
            args = {**args, "id": draft.target}
        proposal = _proposal(args)
        if not draft.target and self._repo.get_automation(proposal["id"]) is not None:
            return draft, {"error": f"an automation {proposal['id']!r} exists; choose another id"}
        draft = draft.note("step", f"Proposed {proposal['id']}", proposal=proposal)
        return draft, {"ok": True, "next": "Tell the owner what it does and ask them to approve."}


def _json_arg(args: dict, key: str) -> Any:
    raw = args.get(key)
    if not raw:
        return None
    try:
        return json.loads(raw)
    except json.JSONDecodeError as err:
        raise ValueError(f"{key} is not valid JSON: {err}") from err


def _proposal(args: dict) -> dict:
    automation_id = str(args.get("id", "")).strip()
    validate_id(automation_id)
    trigger = args.get("trigger")
    if trigger not in ("schedule", "webhook", "manual"):
        raise ValueError("trigger must be schedule, webhook or manual")
    schedule = (args.get("schedule") or "").strip() or None
    if trigger == "schedule":
        if not schedule:
            raise ValueError("a schedule trigger needs a schedule")
        try:
            parse_schedule(schedule)
        except ScheduleError as err:
            raise ValueError(f"bad schedule: {err}") from err
    timeout_s = int(args.get("timeout_s") or DRY_RUN_TIMEOUT_S)
    if not 10 <= timeout_s <= MAX_TIMEOUT_S:
        raise ValueError(f"timeout_s must be 10..{MAX_TIMEOUT_S}")
    return {
        "id": automation_id,
        "name": str(args.get("name", automation_id))[:80],
        "summary": str(args.get("summary", ""))[:600],
        "trigger": trigger,
        "schedule": schedule if trigger == "schedule" else None,
        "timeout_s": timeout_s,
        "services": [str(s)[:60] for s in (args.get("services") or [])][:10],
    }
