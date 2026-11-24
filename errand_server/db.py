"""SQLite storage. One short-lived connection per call, so threads stay safe."""

from __future__ import annotations

import sqlite3
from contextlib import closing, contextmanager
from pathlib import Path
from typing import Callable, Iterator

from .models import Automation, Run

SCHEMA = """
CREATE TABLE IF NOT EXISTS automations (
    id          TEXT PRIMARY KEY,
    name        TEXT NOT NULL,
    description TEXT NOT NULL DEFAULT '',
    trigger     TEXT NOT NULL CHECK (trigger IN ('schedule', 'webhook', 'manual')),
    schedule    TEXT,
    hook_id     TEXT UNIQUE,
    enabled     INTEGER NOT NULL DEFAULT 1,
    timeout_s   INTEGER NOT NULL,
    next_run_at REAL,
    created_at  REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS runs (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    automation_id TEXT NOT NULL REFERENCES automations(id) ON DELETE CASCADE,
    trigger       TEXT NOT NULL,
    status        TEXT NOT NULL
                  CHECK (status IN ('running', 'ok', 'error', 'timeout', 'interrupted')),
    started_at    REAL NOT NULL,
    finished_at   REAL,
    exit_code     INTEGER,
    output        TEXT NOT NULL DEFAULT ''
);
CREATE INDEX IF NOT EXISTS runs_by_automation ON runs (automation_id, id DESC);
"""

AUTOMATION_COLUMNS = (
    "id, name, description, trigger, schedule, hook_id, enabled, timeout_s, next_run_at, created_at"
)
RUN_COLUMNS = "id, automation_id, trigger, status, started_at, finished_at, exit_code, output"


class Repository:
    def __init__(self, db_path: Path) -> None:
        self._db_path = db_path

    @contextmanager
    def _tx(self) -> Iterator[sqlite3.Connection]:
        """Open a connection and hold the write lock for the whole block."""
        conn = sqlite3.connect(self._db_path, timeout=10, isolation_level=None)
        with closing(conn):
            conn.execute("PRAGMA foreign_keys = ON")
            conn.execute("BEGIN IMMEDIATE")
            try:
                yield conn
            except BaseException:
                conn.execute("ROLLBACK")
                raise
            conn.execute("COMMIT")

    def init_schema(self) -> None:
        conn = sqlite3.connect(self._db_path)
        with closing(conn):
            conn.execute("PRAGMA journal_mode = WAL")
            conn.executescript(SCHEMA)

    # Automations

    def save_automation(self, a: Automation) -> None:
        # An upsert, not INSERT OR REPLACE: REPLACE deletes the old row first, and
        # ON DELETE CASCADE would then delete the run history of the automation.
        with self._tx() as conn:
            conn.execute(
                f"INSERT INTO automations ({AUTOMATION_COLUMNS})"
                " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)"
                " ON CONFLICT(id) DO UPDATE SET name = excluded.name,"
                " description = excluded.description, trigger = excluded.trigger,"
                " schedule = excluded.schedule, hook_id = excluded.hook_id,"
                " enabled = excluded.enabled, timeout_s = excluded.timeout_s,"
                " next_run_at = excluded.next_run_at, created_at = excluded.created_at",
                (a.id, a.name, a.description, a.trigger, a.schedule, a.hook_id,
                 int(a.enabled), a.timeout_s, a.next_run_at, a.created_at),
            )

    def set_enabled(self, automation_id: str, enabled: bool, next_run_at: float | None) -> None:
        with self._tx() as conn:
            conn.execute(
                "UPDATE automations SET enabled = ?, next_run_at = ? WHERE id = ?",
                (int(enabled), next_run_at, automation_id),
            )

    def delete_automation(self, automation_id: str) -> bool:
        """Delete the automation and (by cascade) its runs."""
        with self._tx() as conn:
            return conn.execute("DELETE FROM automations WHERE id = ?",
                                (automation_id,)).rowcount > 0

    def get_automation(self, automation_id: str) -> Automation | None:
        return self._one_automation("id = ?", (automation_id,))

    def find_by_hook(self, hook_id: str) -> Automation | None:
        return self._one_automation("hook_id = ?", (hook_id,))

    def list_automations(self) -> list[Automation]:
        with self._tx() as conn:
            rows = conn.execute(f"SELECT {AUTOMATION_COLUMNS} FROM automations ORDER BY id")
            return [_automation(r) for r in rows]

    def claim_due(self, now: float, next_at: Callable[[Automation], float]) -> list[Automation]:
        """Move next_run_at forward for each due schedule and return the ones claimed.

        The write lock makes the claim atomic: two ticks at the same time
        cannot claim the same automation.
        """
        with self._tx() as conn:
            rows = conn.execute(
                f"SELECT {AUTOMATION_COLUMNS} FROM automations WHERE enabled = 1"
                " AND trigger = 'schedule' AND next_run_at <= ? ORDER BY next_run_at",
                (now,),
            ).fetchall()
            claimed = [_automation(r) for r in rows]
            for a in claimed:
                conn.execute(
                    "UPDATE automations SET next_run_at = ? WHERE id = ?", (next_at(a), a.id)
                )
            return claimed

    def _one_automation(self, where: str, args: tuple) -> Automation | None:
        with self._tx() as conn:
            row = conn.execute(
                f"SELECT {AUTOMATION_COLUMNS} FROM automations WHERE {where}", args
            ).fetchone()
            return _automation(row) if row else None

    # Runs

    def start_run(self, automation_id: str, trigger: str, now: float) -> int | None:
        """Insert a 'running' row, unless one already runs for this automation."""
        with self._tx() as conn:
            busy = conn.execute(
                "SELECT 1 FROM runs WHERE automation_id = ? AND status = 'running'",
                (automation_id,),
            ).fetchone()
            if busy:
                return None
            cur = conn.execute(
                "INSERT INTO runs (automation_id, trigger, status, started_at)"
                " VALUES (?, ?, 'running', ?)",
                (automation_id, trigger, now),
            )
            return cur.lastrowid

    def finish_run(self, run_id: int, status: str, exit_code: int | None,
                   output: str, now: float) -> None:
        with self._tx() as conn:
            conn.execute(
                "UPDATE runs SET status = ?, exit_code = ?, output = ?, finished_at = ?"
                " WHERE id = ?",
                (status, exit_code, output, now, run_id),
            )

    def mark_interrupted(self, now: float) -> int:
        """After a restart, no run can still work. Close the ones left open."""
        with self._tx() as conn:
            cur = conn.execute(
                "UPDATE runs SET status = 'interrupted', finished_at = ?"
                " WHERE status = 'running'",
                (now,),
            )
            return cur.rowcount

    def get_run(self, run_id: int) -> Run | None:
        with self._tx() as conn:
            row = conn.execute(f"SELECT {RUN_COLUMNS} FROM runs WHERE id = ?",
                               (run_id,)).fetchone()
            return Run(*row) if row else None

    def list_runs(self, automation_id: str, limit: int = 20) -> list[Run]:
        with self._tx() as conn:
            rows = conn.execute(
                f"SELECT {RUN_COLUMNS} FROM runs WHERE automation_id = ?"
                " ORDER BY id DESC LIMIT ?",
                (automation_id, limit),
            )
            return [Run(*r) for r in rows]


def _automation(row: tuple) -> Automation:
    values = list(row)
    values[6] = bool(values[6])
    return Automation(*values)
