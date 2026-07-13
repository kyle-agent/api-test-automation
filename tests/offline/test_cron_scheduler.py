"""controlplane.scheduler — 재시작 캐치업 억제 + 킬스위치 (오너 2026-07-13
"다시 서버 띄워도 돌지 않게"). 종전에는 서버가 꺼진 사이 놓친 창이 startup에
1회 캐치업 발화해, pull 직후 재시작만 해도 스케줄 풀런이 자동으로 나갔다."""
from __future__ import annotations

from datetime import datetime, timedelta

import pytest

import controlplane.scheduler as sched


def test_start_disabled_by_env(monkeypatch):
    monkeypatch.setenv("SCP_SCHEDULER_DISABLE", "true")
    assert sched.start() is None


def test_due_skips_missed_windows_on_restart(monkeypatch):
    pytest.importorskip("croniter")
    now = datetime.utcnow()
    # 프로세스가 방금 시작 — last_fired가 이틀 전이라 놓친 nightly 창이 있어도
    # 발화하지 않는다 (기준선 = max(last_fired, 프로세스 시작)).
    monkeypatch.setattr(sched, "_PROCESS_START", now - timedelta(minutes=1))
    last = (now - timedelta(days=2)).strftime("%Y-%m-%dT%H:%M:%SZ")
    assert sched._due("0 2 * * *", last, now) is False


def test_due_fires_next_window_after_start(monkeypatch):
    pytest.importorskip("croniter")
    now = datetime.utcnow()
    # 프로세스 시작 후 하루 넘게 지났으면 그 사이의 실제 cron 창은 발화한다
    # (억제 대상은 '시작 이전'에 놓친 창뿐).
    monkeypatch.setattr(sched, "_PROCESS_START", now - timedelta(days=2))
    last = (now - timedelta(days=2)).strftime("%Y-%m-%dT%H:%M:%SZ")
    assert sched._due("0 2 * * *", last, now) is True
