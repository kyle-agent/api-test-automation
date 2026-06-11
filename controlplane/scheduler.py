"""Cron scheduler — fires suite × profile runs from the schedules table.

A single daemon thread wakes every 30s and fires any enabled schedule whose
next occurrence (croniter, server-local time) has passed since it last fired.
Missed windows while the server was down fire ONCE on startup (last_fired
seeds the croniter base), which is the behaviour you want for a nightly fence.
"""
from __future__ import annotations

import threading
import time
from datetime import datetime

from controlplane import db, dispatch

_POLL_SECONDS = 30


def _due(cron: str, last_fired: str | None, now: datetime) -> bool:
    from croniter import croniter
    if last_fired:
        base = datetime.strptime(last_fired, "%Y-%m-%dT%H:%M:%SZ")
    else:
        base = now  # new schedules fire from their NEXT occurrence, not retroactively
    try:
        nxt = croniter(cron, base).get_next(datetime)
    except (ValueError, KeyError):
        return False  # invalid expression — validated at add time, belt-and-braces
    return nxt <= now


def tick(now: datetime | None = None) -> list[int]:
    """One scheduler pass; returns the schedule ids fired (used by tests)."""
    now = now or datetime.utcnow()
    fired = []
    for sched in db.list_schedules():
        if not sched["enabled"]:
            continue
        if not _due(sched["cron"], sched["last_fired"], now):
            continue
        ok, msg = dispatch.dispatch_run(sched["suite"], sched["profile"])
        db.create_run(sched["suite"], sched["profile"],
                      trigger=f"schedule:{sched['id']}",
                      detail=msg if not ok else "")
        db.mark_fired(sched["id"])
        fired.append(sched["id"])
        print(f"[scheduler] fired schedule {sched['id']} "
              f"({sched['suite']} × {sched['profile'] or '-'}): {msg}")
    return fired


def _loop() -> None:
    while True:
        try:
            tick()
        except Exception as exc:  # the scheduler must never die
            print(f"[scheduler] tick failed: {exc}")
        time.sleep(_POLL_SECONDS)


def start() -> threading.Thread:
    t = threading.Thread(target=_loop, name="cron-scheduler", daemon=True)
    t.start()
    return t
