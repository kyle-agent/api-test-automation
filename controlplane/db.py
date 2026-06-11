"""SQLite store for the platform control plane (M1, docs/PLATFORM-PLAN.md).

Holds what git does NOT: run records, schedules, ingested live events and AI
triage results. Everything declarative (suites / environments / scenarios /
knowledge) stays in the repo files — the DB only tracks executions of them.

Connection-per-call keeps things trivially safe across FastAPI workers and the
scheduler thread; volumes are tiny (a few rows per run).
"""
from __future__ import annotations

import os
import sqlite3
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DB_PATH = Path(os.environ.get("PLATFORM_DB", str(ROOT / "controlplane" / "data" / "platform.db")))

SCHEMA = """
CREATE TABLE IF NOT EXISTS runs (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  gh_run_id TEXT UNIQUE,                 -- GitHub Actions run id (joins oplog/snapshot data)
  suite TEXT DEFAULT '',
  profile TEXT DEFAULT '',
  trigger TEXT DEFAULT 'manual',         -- manual | schedule:<id> | external
  status TEXT DEFAULT 'dispatched',      -- dispatched | running | done | failed
  requested_at TEXT, started_at TEXT, finished_at TEXT,
  detail TEXT DEFAULT ''
);
CREATE TABLE IF NOT EXISTS schedules (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  cron TEXT NOT NULL,                    -- 5-field cron, UTC (scheduler ticks in UTC)
  suite TEXT NOT NULL,
  profile TEXT DEFAULT '',
  enabled INTEGER DEFAULT 1,
  note TEXT DEFAULT '',
  last_fired TEXT
);
CREATE TABLE IF NOT EXISTS events (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  gh_run_id TEXT, kind TEXT, ts TEXT,
  job TEXT DEFAULT '', stage TEXT DEFAULT '', status TEXT DEFAULT '',
  detail TEXT DEFAULT ''
);
CREATE INDEX IF NOT EXISTS idx_events_run ON events(gh_run_id);
CREATE TABLE IF NOT EXISTS triage (
  gh_run_id TEXT PRIMARY KEY,
  ts TEXT, model TEXT DEFAULT '',
  summary TEXT DEFAULT '',               -- one-paragraph natural-language summary
  detail TEXT DEFAULT ''                 -- JSON: per-endpoint classifications
);
"""


def now() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def connect() -> sqlite3.Connection:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(DB_PATH, timeout=10)
    con.row_factory = sqlite3.Row
    con.executescript(SCHEMA)
    return con


# --- runs --------------------------------------------------------------------

def create_run(suite: str, profile: str, trigger: str = "manual",
               gh_run_id: str | None = None, detail: str = "") -> int:
    with connect() as con:
        cur = con.execute(
            "INSERT INTO runs (gh_run_id, suite, profile, trigger, requested_at, detail)"
            " VALUES (?, ?, ?, ?, ?, ?)",
            (gh_run_id, suite, profile, trigger, now(), detail))
        return cur.lastrowid


def list_runs(limit: int = 50) -> list[sqlite3.Row]:
    with connect() as con:
        return con.execute(
            "SELECT * FROM runs ORDER BY id DESC LIMIT ?", (limit,)).fetchall()


def get_run(gh_run_id: str) -> sqlite3.Row | None:
    with connect() as con:
        return con.execute(
            "SELECT * FROM runs WHERE gh_run_id = ?", (gh_run_id,)).fetchone()


def attach_run(gh_run_id: str) -> int:
    """Bind an incoming gh_run_id to a run record.

    workflow_dispatch doesn't return the run id it started, so the first
    ingested event claims the OLDEST still-unbound dispatched record (FIFO —
    matches the Actions queue order); with none pending (file-triggered or
    out-of-band runs) a fresh 'external' record is created."""
    with connect() as con:
        row = con.execute(
            "SELECT id FROM runs WHERE gh_run_id = ?", (gh_run_id,)).fetchone()
        if row:
            return row["id"]
        row = con.execute(
            "SELECT id FROM runs WHERE gh_run_id IS NULL AND status = 'dispatched'"
            " ORDER BY id LIMIT 1").fetchone()
        if row:
            con.execute("UPDATE runs SET gh_run_id = ? WHERE id = ?",
                        (gh_run_id, row["id"]))
            return row["id"]
        cur = con.execute(
            "INSERT INTO runs (gh_run_id, trigger, status, requested_at)"
            " VALUES (?, 'external', 'running', ?)", (gh_run_id, now()))
        return cur.lastrowid


def apply_milestone(gh_run_id: str, stage: str, status: str, detail: str = "") -> None:
    """Advance the run's lifecycle from an oplog milestone event."""
    with connect() as con:
        if stage == "run-start":
            con.execute(
                "UPDATE runs SET status = 'running', started_at = COALESCE(started_at, ?)"
                " WHERE gh_run_id = ?", (now(), gh_run_id))
            # file-triggered runs carry their options in the run-start detail
            if detail and "mutations=" in detail:
                con.execute(
                    "UPDATE runs SET detail = ? WHERE gh_run_id = ? AND detail = ''",
                    (detail[:500], gh_run_id))
        elif stage == "dashboard":
            # the dashboard milestone is the orchestrator's final stage
            final = "done" if status in ("done", "success") else "failed"
            con.execute(
                "UPDATE runs SET status = ?, finished_at = COALESCE(finished_at, ?)"
                " WHERE gh_run_id = ?", (final, now(), gh_run_id))


def insert_event(gh_run_id: str, kind: str, ts: str, job: str = "",
                 stage: str = "", status: str = "", detail: str = "") -> None:
    with connect() as con:
        con.execute(
            "INSERT INTO events (gh_run_id, kind, ts, job, stage, status, detail)"
            " VALUES (?, ?, ?, ?, ?, ?, ?)",
            (gh_run_id, kind, ts, job, stage, status, detail[:2000]))


def list_events(gh_run_id: str, kind: str | None = None, limit: int = 500) -> list[sqlite3.Row]:
    with connect() as con:
        if kind:
            return con.execute(
                "SELECT * FROM events WHERE gh_run_id = ? AND kind = ? ORDER BY id LIMIT ?",
                (gh_run_id, kind, limit)).fetchall()
        return con.execute(
            "SELECT * FROM events WHERE gh_run_id = ? ORDER BY id LIMIT ?",
            (gh_run_id, limit)).fetchall()


# --- schedules ---------------------------------------------------------------

def add_schedule(cron: str, suite: str, profile: str = "", note: str = "") -> int:
    with connect() as con:
        cur = con.execute(
            "INSERT INTO schedules (cron, suite, profile, note) VALUES (?, ?, ?, ?)",
            (cron, suite, profile, note))
        return cur.lastrowid


def list_schedules() -> list[sqlite3.Row]:
    with connect() as con:
        return con.execute("SELECT * FROM schedules ORDER BY id").fetchall()


def toggle_schedule(schedule_id: int) -> None:
    with connect() as con:
        con.execute("UPDATE schedules SET enabled = 1 - enabled WHERE id = ?",
                    (schedule_id,))


def delete_schedule(schedule_id: int) -> None:
    with connect() as con:
        con.execute("DELETE FROM schedules WHERE id = ?", (schedule_id,))


def mark_fired(schedule_id: int) -> None:
    with connect() as con:
        con.execute("UPDATE schedules SET last_fired = ? WHERE id = ?",
                    (now(), schedule_id))


# --- triage ------------------------------------------------------------------

def set_triage(gh_run_id: str, model: str, summary: str, detail: str) -> None:
    with connect() as con:
        con.execute(
            "INSERT INTO triage (gh_run_id, ts, model, summary, detail)"
            " VALUES (?, ?, ?, ?, ?)"
            " ON CONFLICT(gh_run_id) DO UPDATE SET ts=excluded.ts,"
            " model=excluded.model, summary=excluded.summary, detail=excluded.detail",
            (gh_run_id, now(), model, summary, detail))


def get_triage(gh_run_id: str) -> sqlite3.Row | None:
    with connect() as con:
        return con.execute(
            "SELECT * FROM triage WHERE gh_run_id = ?", (gh_run_id,)).fetchone()
