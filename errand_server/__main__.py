"""Command line: python -m errand_server <command>.

    serve                          start the HTTP service
    install DIR --id ID --name N   install DIR/main.py (--schedule SPEC | --webhook | --manual)
    run ID                         run one automation now and print the result
    list                           list automations and their last run
    set KEY VALUE                  change a setting (notify_email, email_from)
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import sys
import time
from pathlib import Path

from .config import DEFAULT_PORT, DEFAULT_TIMEOUT_S, load_or_create_secrets, paths_from_env
from .db import Repository
from .install import install
from .jobs import Dispatcher
from .web import App, make_server

SETTING_KEYS = ("notify_email", "email_from")

log = logging.getLogger("errand")


def main(argv: list[str] | None = None) -> int:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    args = _parser().parse_args(argv)
    paths = paths_from_env()
    paths.ensure()
    repo = Repository(paths.db)
    repo.init_schema()
    return args.func(args, paths, repo)


def cmd_serve(args, paths, repo) -> int:
    closed = repo.mark_interrupted(time.time())
    if closed:
        log.warning("closed %d run(s) that a restart interrupted", closed)
    app = App(repo, Dispatcher(repo, paths), load_or_create_secrets(paths))
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
    settings = json.loads(paths.settings.read_text()) if paths.settings.exists() else {}
    paths.settings.write_text(json.dumps({**settings, args.key: args.value}, indent=2))
    print(f"{args.key} set")
    return 0


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
    return parser


if __name__ == "__main__":
    sys.exit(main())
