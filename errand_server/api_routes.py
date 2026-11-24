"""Admin API routes (/api/...). web.authorize_api has already checked the caller.

    GET    /api/session                          who am I, Sprite URL, settings
    GET    /api/settings | POST {key: value}     owner settings
    GET    /api/automations                      list, with the last run of each
    GET    /api/automations/<id>                 detail: files, versions, runs, open drafts
    DELETE /api/automations/<id>                 checkpoint, then delete
    GET    /api/automations/<id>/runs
    POST   /api/automations/<id>/run             run now (202)
    POST   /api/automations/<id>/enabled         {"enabled": bool}
    POST   /api/automations/<id>/rollback        {"version": sha}
    POST   /api/automations/<id>/edit            {"message", "run_id"?, "kind"?} -> draft (202)
    GET    /api/egress                           network policy: open or locked, hosts it blocks
    GET    /api/checkpoints | POST {"comment"}   Sprite checkpoints
    POST   /api/checkpoints/<id>/restore         restore the whole Sprite (it restarts)
    GET    /api/drafts | POST {"message"}        builder drafts
    GET    /api/drafts/<id>
    POST   /api/drafts/<id>/messages | /approve | /discard
"""

from __future__ import annotations

import json
import re
import shutil
import time
from typing import Any

from . import egress, history, settings
from .builder import BuilderError
from .drafts import public_view, summary_view
from .schedule import next_run, parse_schedule
from .web import App, HttpError, Request

Response = tuple[int, Any]
AUTOMATION = re.compile(r"/api/automations/([a-z0-9][a-z0-9-]{0,47})(?:/([a-z]+))?")
DRAFT = re.compile(r"/api/drafts/(d-[0-9a-f]{8})(?:/([a-z]+))?")
CHECKPOINT = re.compile(r"/api/checkpoints/(v\d{1,6})/restore")


def handle(app: App, req: Request) -> Response:
    try:
        return _dispatch(app, req)
    except BuilderError as err:
        raise HttpError(err.status, err.message) from err
    except ValueError as err:  # input checks in settings, history, install
        raise HttpError(400, str(err)) from err


def _dispatch(app: App, req: Request) -> Response:
    path, method = req.path, req.method
    if path == "/api/session" and method == "GET":
        return 200, {"sprite_url": app.base_url(), "settings": _settings(app)}
    if path == "/api/settings":
        if method == "GET":
            return 200, _settings(app)
        if method == "POST":
            return 200, settings.update(app.paths.settings, _json_body(req))
    if path == "/api/automations" and method == "GET":
        return 200, [_summary(app, a) for a in app.repo.list_automations()]
    if path == "/api/egress" and method == "GET":
        return 200, egress.status(app.paths, app.egress_policy())
    match = AUTOMATION.fullmatch(path)
    if match:
        return automation_api(app, req, *match.groups())
    if path == "/api/checkpoints" or CHECKPOINT.fullmatch(path):
        return checkpoints_api(app, req)
    if path == "/api/drafts" or DRAFT.fullmatch(path):
        return drafts_api(app, req)
    raise HttpError(404 if method in ("GET", "POST", "DELETE") else 405, "not found")


# ---- automations ------------------------------------------------------------

def automation_api(app: App, req: Request, automation_id: str, action: str | None) -> Response:
    automation = app.repo.get_automation(automation_id)
    if automation is None:
        raise HttpError(404, "no such automation")
    method = req.method
    if action is None and method == "GET":
        return 200, _detail(app, automation)
    if action is None and method == "DELETE":
        return 200, _delete(app, automation)
    if action == "runs" and method == "GET":
        return 200, [r.to_dict() for r in app.repo.list_runs(automation.id)]
    if action == "run" and method == "POST":
        app.dispatcher.submit(automation, "manual")
        return 202, {"accepted": automation.id}
    if action == "enabled" and method == "POST":
        enabled = _json_body(req).get("enabled")
        if not isinstance(enabled, bool):
            raise HttpError(400, "enabled must be true or false")
        next_at = automation.next_run_at
        if enabled and automation.schedule:
            next_at = next_run(parse_schedule(automation.schedule), time.time())
        app.repo.set_enabled(automation.id, enabled, next_at)
        return 200, _summary(app, app.repo.get_automation(automation.id))
    if action == "rollback" and method == "POST":
        version = str(_json_body(req).get("version", ""))
        sha = history.roll_back(app.paths.automation_dir(automation.id), version)
        return 200, {"version": sha}
    if action == "edit" and method == "POST":
        body = _json_body(req)
        kind = body.get("kind", "change")
        if kind not in ("change", "repair"):
            raise HttpError(400, "kind must be change or repair")
        run_id = body.get("run_id")
        if run_id is not None and not isinstance(run_id, int):
            raise HttpError(400, "run_id must be a number")
        draft = app.builder.start_edit(automation.id, body.get("message"), kind=kind,
                                       run_id=run_id)
        return 202, {"id": draft.id, "status": draft.status}
    raise HttpError(405, "method not allowed")


def _summary(app: App, automation) -> dict:
    data = automation.to_dict()
    data.pop("hook_id", None)
    if automation.hook_id:
        data["hook_path"] = f"/hooks/{automation.hook_id}"
    last = app.repo.list_runs(automation.id, limit=1)
    data["last_run"] = _run_brief(last[0]) if last else None
    return data


def _detail(app: App, automation) -> dict:
    folder = app.paths.automation_dir(automation.id)
    open_drafts = [summary_view(d) for d in app.drafts.list()
                   if d.target == automation.id and d.status not in ("installed", "discarded")]
    return {
        **_summary(app, automation),
        "files": {p.name: p.read_text() for p in sorted(folder.glob("*.py"))},
        "versions": history.versions(folder),
        "runs": [r.to_dict() for r in app.repo.list_runs(automation.id, limit=20)],
        "drafts": open_drafts,
    }


def _delete(app: App, automation) -> dict:
    checkpoint = _try_checkpoint(app, f"before delete of {automation.id}")
    app.repo.delete_automation(automation.id)
    trash = app.paths.home / "trash"
    trash.mkdir(exist_ok=True)
    folder = app.paths.automation_dir(automation.id)
    if folder.exists():
        shutil.move(str(folder), str(trash / f"{automation.id}-{int(time.time())}"))
    (app.paths.events / f"{automation.id}.json").unlink(missing_ok=True)
    return {"deleted": automation.id, "checkpoint": checkpoint}


def _run_brief(run) -> dict:
    return {"id": run.id, "status": run.status, "trigger": run.trigger,
            "started_at": run.started_at, "finished_at": run.finished_at}


# ---- checkpoints --------------------------------------------------------------

def checkpoints_api(app: App, req: Request) -> Response:
    if app.sprite is None:
        raise HttpError(503, "checkpoints need the Sprite API")
    if req.path == "/api/checkpoints":
        if req.method == "GET":
            return 200, sorted(app.sprite.checkpoints(), key=lambda c: c.get("create_time", ""),
                               reverse=True)
        if req.method == "POST":
            comment = str(_json_body(req).get("comment") or "manual checkpoint")[:120]
            return 201, {"id": app.sprite.create_checkpoint(comment)}
        raise HttpError(405, "method not allowed")
    if req.method != "POST":
        raise HttpError(405, "method not allowed")
    checkpoint_id = CHECKPOINT.fullmatch(req.path).group(1)
    app.sprite.restore(checkpoint_id)
    return 202, {"restoring": checkpoint_id}


def _try_checkpoint(app: App, comment: str) -> str | None:
    if app.sprite is None:
        return None
    try:
        return app.sprite.create_checkpoint(comment)
    except Exception:  # the delete still goes ahead; the trash folder keeps the code
        return None


# ---- drafts --------------------------------------------------------------------

def drafts_api(app: App, req: Request) -> Response:
    if app.builder is None:
        raise HttpError(503, "the builder is not available")
    if req.path == "/api/drafts":
        if req.method == "GET":
            return 200, [summary_view(d) for d in app.drafts.list()]
        if req.method == "POST":
            draft = app.builder.start(_json_body(req).get("message"))
            return 202, {"id": draft.id, "status": draft.status}
        raise HttpError(405, "method not allowed")
    draft_id, action = DRAFT.fullmatch(req.path).groups()
    if action is None and req.method == "GET":
        draft = app.drafts.load(draft_id)
        if draft is None:
            raise HttpError(404, "no such draft")
        return 200, public_view(draft, app.drafts.files(draft_id))
    if req.method != "POST":
        raise HttpError(405, "method not allowed")
    if action == "messages":
        draft = app.builder.send(draft_id, _json_body(req).get("message"))
        return 202, {"id": draft.id, "status": draft.status}
    if action == "approve":
        return 200, _summary(app, app.builder.approve(draft_id))
    if action == "discard":
        return 200, {"id": draft_id, "status": app.builder.discard(draft_id).status}
    raise HttpError(404, "not found")


# ---- helpers -------------------------------------------------------------------

def _settings(app: App) -> dict:
    data = settings.load(app.paths.settings) if app.paths else {}
    return {key: data.get(key) for key in settings.KEYS} | {
        "auto_repair": data.get("auto_repair", "on")}


def _json_body(req: Request) -> dict:
    try:
        data = json.loads(req.read_body() or b"{}")
    except (json.JSONDecodeError, UnicodeDecodeError) as err:
        raise HttpError(400, "body must be JSON") from err
    if not isinstance(data, dict):
        raise HttpError(400, "body must be a JSON object")
    return data
