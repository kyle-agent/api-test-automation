"""Cron scheduler — fires suite × profile runs from the schedules table.

A single daemon thread wakes every 30s and fires any enabled schedule whose
next occurrence (croniter, server-local time) has passed since it last fired.

재시작 캐치업 없음 (오너 2026-07-13 "다시 서버 띄워도 돌지 않게"): 서버가
꺼진 사이 놓친 창은 재시작 시 발화하지 않는다 — 발화 기준선이 max(last_fired,
프로세스 시작 시각)이라, 다음 실제 cron 시각부터만 돈다. (종전에는 놓친 창이
startup에 1회 캐치업 발화해, pull 직후 재시작만 해도 풀런이 자동으로 나갔다.)
완전 비활성은 SCP_SCHEDULER_DISABLE=true.
"""
from __future__ import annotations

import os
import threading
import time
from datetime import datetime

from controlplane import db, dispatch

_POLL_SECONDS = 30
# 프로세스 시작 시각 — import 시점 고정. 캐치업 억제의 기준선.
_PROCESS_START = datetime.utcnow()


def _due(cron: str, last_fired: str | None, now: datetime) -> bool:
    from croniter import croniter
    base = _PROCESS_START  # 재시작 캐치업 금지: 최소 기준선 = 프로세스 시작
    if last_fired:
        lf = datetime.strptime(last_fired, "%Y-%m-%dT%H:%M:%SZ")
        base = max(base, lf)
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


def start() -> threading.Thread | None:
    if os.environ.get("SCP_SCHEDULER_DISABLE", "").strip().lower() == "true":
        print("[scheduler] SCP_SCHEDULER_DISABLE=true — scheduler NOT started")
        return None
    t = threading.Thread(target=_loop, name="cron-scheduler", daemon=True)
    t.start()
    return t
