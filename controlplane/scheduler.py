"""Cron scheduler — fires suite runs from the schedules table.

A single daemon thread wakes every 30s and fires any enabled schedule whose
next occurrence (croniter, server-local time) has passed since it last fired.
Missed windows while the server was down fire ONCE on startup (last_fired
seeds the croniter base), which is the behaviour you want for a nightly fence.

실행 경로 (D5 후속 결정, 오너 승인 2026-07-11): 발화 = **이 서버(콘솔 엔진)의
LIVE 런** (``tools.console2_server.launch_suite_run``). 종전의 GitHub Actions
workflow_dispatch 경로는 오너가 "실제로 사용 안 함"으로 확정(D5)해 제거.
따라서 (1) 스케줄은 이 서버 프로세스가 켜져 있는 동안만 동작하고, (2)
heavy(과금) suite 는 무인 실행이 거부되며 (Hard Rule 1 — heavy 는 pre-flight
수동 opt-in), (3) profile 은 로컬 실행에서 무시된다 — 서버의 .env 가 곧 환경.
"""
from __future__ import annotations

import threading
import time
from datetime import datetime

from controlplane import db

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
        from tools import console2_server as c2
        ok, msg = c2.launch_suite_run(sched["suite"],
                                      trigger=f"schedule:{sched['id']}")
        if not ok:
            # 발화 실패(heavy 거부·LIVE 중복·suite 없음)도 실행 기록에 보이게 —
            # failed 런 행으로 기록. 성공 시에는 엔진의 DB 미러가 run 행을
            # 만들므로 여기서 만들지 않는다 (이중 기록 금지).
            db.record_local_run(
                f"local-sched{sched['id']}-{now.strftime('%Y%m%d%H%M%S')}",
                status="failed", suite=sched["suite"],
                profile=sched["profile"] or "",
                trigger=f"schedule:{sched['id']}", detail=msg)
        db.mark_fired(sched["id"])
        fired.append(sched["id"])
        print(f"[scheduler] fired schedule {sched['id']} "
              f"({sched['suite']} → 이 서버 LIVE): {msg}")
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
