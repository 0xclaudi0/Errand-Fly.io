"""Self-repair: when a run fails, ask the builder for a fix and tell the owner.

- At most one open repair draft per automation, so a schedule that keeps failing
  does not start a new repair (and use model quota) on every run.
- One email per failure streak: only when the run before this one did not fail.
- The fix is a proposal. The owner still approves it; nothing installs itself.
"""

from __future__ import annotations

import logging
import time
from typing import Callable

from .db import Repository
from .drafts import Draft
from .models import Automation

log = logging.getLogger(__name__)

Notify = Callable[[str, str], object]
OUTPUT_LINES = 15


class Repairer:
    def __init__(self, repo: Repository, builder, *, settings: Callable[[], dict],
                 notify: Notify, base_url: Callable[[], str]) -> None:
        self._repo = repo
        self._builder = builder
        self._settings = settings
        self._notify = notify
        self._base_url = base_url

    def on_failure(self, automation: Automation, run_id: int) -> None:
        run = self._repo.get_run(run_id)
        if run is None:
            return
        previous = self._repo.list_runs(automation.id, limit=2)[1:]
        first_failure = not previous or previous[0].status not in ("error", "timeout")
        auto_repair = self._settings().get("auto_repair", "on") != "off"
        if auto_repair and self._builder.open_draft(automation.id, "repair") is None:
            message = f"Run #{run.id} failed ({run.status}). Please fix it."
            then = (lambda draft: self._email(automation, run, draft)) if first_failure else None
            self._builder.start_edit(automation.id, message, kind="repair", run_id=run.id,
                                     then=then)
            log.info("started a repair draft for %s after run %s", automation.id, run.id)
            return
        if first_failure:
            self._email(automation, run, None)

    def _email(self, automation: Automation, run, draft: Draft | None) -> None:
        when = time.strftime("%Y-%m-%d %H:%M UTC", time.gmtime(run.started_at))
        tail = "\n".join(run.output.strip().splitlines()[-OUTPUT_LINES:])
        lines = [f'"{automation.name}" failed ({run.status}) at {when}.', "",
                 "Last lines of the run:", tail, ""]
        base = self._base_url().rstrip("/")
        if draft is not None and draft.status == "proposed":
            lines.append("Errand wrote and tested a fix. Read it and approve it here:")
            lines.append(f"{base}/#/draft/{draft.id}")
        elif draft is not None:
            lines.append("Errand tried to fix it but has no tested fix yet:")
            lines.append(f"{base}/#/draft/{draft.id}")
        else:
            lines.append(f"Open Errand to look at it: {base}/#/automation/{automation.id}")
        try:
            self._notify(f"Errand: {automation.name} failed", "\n".join(lines))
        except Exception as err:  # no notify_email set, or Resend is down: log only
            log.warning("could not email the failure of %s: %s", automation.id, err)
