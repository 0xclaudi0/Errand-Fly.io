"""Command line: python -m errand_server <command>.

    serve                          start the HTTP service
    install DIR --id ID --name N   install DIR/main.py (--schedule SPEC | --webhook | --manual)
    run ID                         run one automation now and print the result
    list                           list automations and their last run
    set KEY VALUE                  change a setting (notify_email, email_from, timezone)
    build MESSAGE [--draft ID]     ask the builder agent for an automation (or continue a draft)
    approve DRAFT                  install the draft's tested proposal
    drafts                         list builder drafts
    show DRAFT                     print a draft's transcript, proposal and code
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import sys
import time
from pathlib import Path

from errand_sdk import gateway, web as sdk_web

from . import runner
from .builder import Builder, BuilderError
from .builder_tools import Toolbox
from .config import DEFAULT_PORT, DEFAULT_TIMEOUT_S, Paths, load_or_create_secrets, paths_from_env
from .db import Repository
from .drafts import DraftStore
from .install import install
from .jobs import Dispatcher
from .web import App, make_server

SETTING_KEYS = ("notify_email", "email_from", "timezone")

log = logging.getLogger("errand")


def main(argv: list[str] | None = None) -> int:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    args = _parser().parse_args(argv)
    paths = paths_from_env()
    paths.ensure()
    repo = Repository(paths.db)
    repo.init_schema()
    return args.func(args, paths, repo)


def make_builder(paths: Paths, repo: Repository, background: bool) -> tuple[Builder, DraftStore]:
    store = DraftStore(paths)
    toolbox = Toolbox(store, paths, repo, execute=runner.execute,
                      providers=gateway.providers, fetch=sdk_web.fetch_text)
    builder = Builder(store, paths, repo, toolbox, settings=lambda: _settings(paths),
                      background=background)
    return builder, store


def _settings(paths: Paths) -> dict:
    return json.loads(paths.settings.read_text()) if paths.settings.exists() else {}


def cmd_serve(args, paths, repo) -> int:
    closed = repo.mark_interrupted(time.time())
    if closed:
        log.warning("closed %d run(s) that a restart interrupted", closed)
    builder, store = make_builder(paths, repo, background=True)
    stuck = builder.recover()
    if stuck:
        log.warning("marked %d builder draft(s) that a restart interrupted", stuck)
    app = App(repo, Dispatcher(repo, paths), load_or_create_secrets(paths), builder, store)
    port = int(os.environ.get("PORT", DEFAULT_PORT))
    log.info("errand-server listening on :%d, home %s", port, paths.home)
    make_server(app, "0.0.0.0", port).serve_forever()
    return 0


def cmd_install(args, paths, repo) -> int:
    trigger = "webhook" if args.webhook else "manual" if args.manual else "schedule"
    automation = install(
        repo, paths, Path(args.dir), automation_id=args.id, name=args.name, trigger=trigger,
        schedule=args.schedule, timeout_s=args.timeout, description=args.description,
    )
    print(json.dumps(automation.to_dict(), indent=2))
    if automation.hook_id:
        print(f"webhook path: /hooks/{automation.hook_id}")
    return 0


def cmd_run(args, paths, repo) -> int:
    automation = repo.get_automation(args.id)
    if automation is None:
        print(f"no automation {args.id!r}", file=sys.stderr)
        return 2
    run_id = Dispatcher(repo, paths, background=False).run_now(automation, "manual")
    if run_id is None:
        print("a run is already active", file=sys.stderr)
        return 1
    run = repo.list_runs(args.id, limit=1)[0]
    print(run.output, end="" if run.output.endswith("\n") else "\n")
    print(f"--- run {run.id}: {run.status} in {run.finished_at - run.started_at:.1f}s")
    return 0 if run.status == "ok" else 1


def cmd_list(args, paths, repo) -> int:
    for a in repo.list_automations():
        last = repo.list_runs(a.id, limit=1)
        status = last[0].status if last else "never run"
        when = a.schedule or a.trigger
        print(f"{a.id:24} {when:28} {'on' if a.enabled else 'off':4} last: {status}")
    return 0


def cmd_set(args, paths, repo) -> int:
    paths.settings.write_text(json.dumps({**_settings(paths), args.key: args.value}, indent=2))
    print(f"{args.key} set")
    return 0


def cmd_build(args, paths, repo) -> int:
    builder, store = make_builder(paths, repo, background=False)
    before = len(store.load(args.draft).transcript) if args.draft and store.load(args.draft) else 0
    try:
        draft = builder.send(args.draft, args.message) if args.draft else builder.start(args.message)
    except BuilderError as err:
        print(f"error: {err.message}", file=sys.stderr)
        return 1
    for entry in draft.transcript[before:]:
        print(_line(entry))
    _print_outcome(draft)
    return 0 if draft.status in ("waiting", "proposed") else 1


def cmd_approve(args, paths, repo) -> int:
    builder, _ = make_builder(paths, repo, background=False)
    try:
        automation = builder.approve(args.draft)
    except BuilderError as err:
        print(f"error: {err.message}", file=sys.stderr)
        return 1
    print(f"installed {automation.id} ({automation.schedule or automation.trigger})")
    if automation.hook_id:
        print(f"webhook path: /hooks/{automation.hook_id}")
    return 0


def cmd_drafts(args, paths, repo) -> int:
    for draft in DraftStore(paths).list():
        first = next((t["text"] for t in draft.transcript if t["role"] == "user"), "")
        print(f"{draft.id}  {draft.status:9} {first[:70]}")
    return 0


def cmd_show(args, paths, repo) -> int:
    store = DraftStore(paths)
    draft = store.load(args.draft)
    if draft is None:
        print("no such draft", file=sys.stderr)
        return 2
    for entry in draft.transcript:
        print(_line(entry))
    _print_outcome(draft)
    for name, code in store.files(draft.id).items():
        print(f"\n===== {name}\n{code}")
    return 0


def _line(entry: dict) -> str:
    label = {"user": "you", "agent": "errand", "step": "  ·"}[entry["role"]]
    return f"{label}: {entry['text']}" if entry["role"] != "step" else f"{label} {entry['text']}"


def _print_outcome(draft) -> None:
    if draft.dry_runs:
        last = draft.dry_runs[-1]
        print(f"\n--- last dry run ({last['status']}, {last['seconds']} s):\n{last['output'].strip()}")
    if draft.proposal:
        print("\n--- proposal:\n" + json.dumps(draft.proposal, indent=2))
    if draft.error:
        print(f"\n--- error: {draft.error}")
    print(f"\n[{draft.id}] status: {draft.status}")


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="errand_server")
    sub = parser.add_subparsers(required=True)
    sub.add_parser("serve").set_defaults(func=cmd_serve)

    p = sub.add_parser("install")
    p.add_argument("dir")
    p.add_argument("--id", required=True)
    p.add_argument("--name", required=True)
    p.add_argument("--description", default="")
    p.add_argument("--timeout", type=int, default=DEFAULT_TIMEOUT_S)
    kind = p.add_mutually_exclusive_group(required=True)
    kind.add_argument("--schedule")
    kind.add_argument("--webhook", action="store_true")
    kind.add_argument("--manual", action="store_true")
    p.set_defaults(func=cmd_install)

    p = sub.add_parser("run")
    p.add_argument("id")
    p.set_defaults(func=cmd_run)
    sub.add_parser("list").set_defaults(func=cmd_list)

    p = sub.add_parser("set")
    p.add_argument("key", choices=SETTING_KEYS)
    p.add_argument("value")
    p.set_defaults(func=cmd_set)

    p = sub.add_parser("build")
    p.add_argument("message")
    p.add_argument("--draft")
    p.set_defaults(func=cmd_build)
    p = sub.add_parser("approve")
    p.add_argument("draft")
    p.set_defaults(func=cmd_approve)
    sub.add_parser("drafts").set_defaults(func=cmd_drafts)
    p = sub.add_parser("show")
    p.add_argument("draft")
    p.set_defaults(func=cmd_show)
    return parser


if __name__ == "__main__":
    sys.exit(main())
