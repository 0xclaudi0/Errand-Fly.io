"""Start runs: from /tick (due schedules), webhooks and manual requests."""

from __future__ import annotations

import json
import logging
import os
import threading
import time
import traceback
from pathlib import Path
from typing import Any, Callable

from . import keepawake, runner
from .config import HOLD_MARGIN_S, MAX_PARALLEL_RUNS, Paths
from .db import Repository
from .models import Automation
from .runner import RunOutcome
from .schedule import next_run, parse_schedule

SDK_ROOT = Path(__file__).resolve().parent.parent  # folder that holds errand_sdk/
PASS_THROUGH_ENV = ("PATH", "HOME", "LANG", "TZ", "SSL_CERT_FILE")

log = logging.getLogger(__name__)


class Dispatcher:
    def __init__(
        self,
        repo: Repository,
        paths: Paths,
        keep_awake: Callable = keepawake.keep_awake,
        execute: Callable[..., RunOutcome] = runner.execute,
        clock: Callable[[], float] = time.time,
        background: bool = True,
    ) -> None:
        self._repo = repo
        self._paths = paths
        self._keep_awake = keep_awake
        self._execute = execute
        self._clock = clock
        self._background = background
        self._slots = threading.BoundedSemaphore(MAX_PARALLEL_RUNS)

    def tick(self) -> list[str]:
        """Claim every due schedule, start it, and return the ids."""
        now = self._clock()
        claimed = self._repo.claim_due(now, lambda a: next_run(parse_schedule(a.schedule), now))
        for automation in claimed:
            self.submit(automation, "tick")
        return [a.id for a in claimed]

    def submit(self, automation: Automation, trigger: str, event: Any = None) -> None:
        if not self._background:
            self.run_now(automation, trigger, event)
            return
        thread = threading.Thread(
            target=self._run_in_slot, args=(automation, trigger, event),
            name=f"run-{automation.id}", daemon=False,
        )
        thread.start()

    def _run_in_slot(self, automation: Automation, trigger: str, event: Any) -> None:
        with self._slots:
            self.run_now(automation, trigger, event)

    def run_now(self, automation: Automation, trigger: str, event: Any = None) -> int | None:
        """Run synchronously. Return the run id, or None if a run is already active."""
        run_id = self._repo.start_run(automation.id, trigger, self._clock())
        if run_id is None:
            log.info("skip %s (%s): a run is already active", automation.id, trigger)
            return None
        event_file = self._write_event(run_id, event)
        hold_s = automation.timeout_s + HOLD_MARGIN_S
        try:
            with self._keep_awake(f"errand-run-{run_id}", hold_s):
                env = self._env(automation, run_id, trigger, event_file)
                outcome = self._execute(
                    self._paths.automation_dir(automation.id), env, automation.timeout_s
                )
        except Exception:  # a server fault must still close the run row
            log.exception("run %s of %s crashed", run_id, automation.id)
            outcome = RunOutcome("error", None, "errand: internal error\n" + traceback.format_exc())
        finally:
            if event_file:
                event_file.unlink(missing_ok=True)
        self._repo.finish_run(run_id, outcome.status, outcome.exit_code, outcome.output,
                              self._clock())
        log.info("run %s of %s: %s", run_id, automation.id, outcome.status)
        return run_id

    def _write_event(self, run_id: int, event: Any) -> Path | None:
        if event is None:
            return None
        path = self._paths.tmp / f"event-{run_id}.json"
        path.write_text(json.dumps(event))
        return path

    def _env(self, automation: Automation, run_id: int, trigger: str,
             event_file: Path | None) -> dict[str, str]:
        """Build the script env from scratch, so server secrets never leak in."""
        env = {k: os.environ[k] for k in PASS_THROUGH_ENV if k in os.environ}
        folder = self._paths.automation_dir(automation.id)
        env.update(
            PYTHONPATH=str(SDK_ROOT),
            PYTHONUNBUFFERED="1",
            ERRAND_HOME=str(self._paths.home),
            ERRAND_AUTOMATION_ID=automation.id,
            ERRAND_RUN_ID=str(run_id),
            ERRAND_TRIGGER=trigger,
            ERRAND_STATE_FILE=str(folder / "state.json"),
        )
        if event_file:
            env["ERRAND_EVENT_FILE"] = str(event_file)
        return env
