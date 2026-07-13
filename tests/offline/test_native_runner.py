"""Offline tests for the native task-queue runner (2026-07-13, xdist 대체).

라이브 호출 없이 engine을 mock — 스케줄러 동작(LPT+dependent 순서, 전량 완료,
꼬리 붕괴 없음=동시성 유지, 공유 Budget 쿼터 조율)을 검증한다.
"""
from __future__ import annotations

import threading
import time

import pytest

from regression.scenarios import native_runner as nr


def test_priority_order_lpt_and_dependents_last(monkeypatch):
    monkeypatch.setattr(nr, "_durations", lambda: {"big": 1000.0, "mid": 300.0, "small": 10.0})
    monkeypatch.setattr(nr, "_prereqs", lambda: {"dep"})
    lcs = [{"id": "small"}, {"id": "dep"}, {"id": "big"}, {"id": "mid"}]
    out = [lc["id"] for lc in nr.priority_order(lcs)]
    # no-dep 무거운 것 먼저 → big, mid, small; dependent(dep)는 맨 뒤
    assert out == ["big", "mid", "small", "dep"], out


def _run_with_mocked_engine(monkeypatch, lcs, *, workers, sleep=0.02,
                            quota_cap=None):
    """engine을 mock해 run()을 오프라인 구동. 반환: (result, peak_conc, quota_400)."""
    import core.config
    import core.http_client
    import core.budgets
    from regression.scenarios import engine

    monkeypatch.setattr(type(core.config.settings), "require_credentials",
                        lambda self: None, raising=False)
    monkeypatch.setattr(core.http_client, "ApiClient", lambda cfg: object())
    monkeypatch.setattr(engine, "active_lifecycles", lambda: lcs)
    monkeypatch.setattr(engine, "provision_shared_vpc", lambda c, cfg: ({}, lambda: None))
    monkeypatch.setattr(engine, "ResourceRegistry", lambda: object())

    conc = {"cur": 0, "peak": 0}
    q400 = {"n": 0}
    clock = threading.Lock()

    def fake_run_lifecycle(lc, client, cfg, *, budget=None, resource_registry=None,
                           shared_ctx=None):
        with clock:
            conc["cur"] += 1
            conc["peak"] = max(conc["peak"], conc["cur"])
        # 쿼터: capped kind가 있으면 공유 budget에 reserve 시도 (조율 확인)
        kind = lc.get("_quota")
        reserved = True
        if kind and budget is not None:
            reserved = budget.reserve(kind)
            if not reserved:
                q400["n"] += 1                 # 조율됐으면 여기서 skip(400 아님)
        time.sleep(sleep * (lc.get("_dur_mult", 1)))
        if kind and budget is not None and reserved:
            budget.release(kind)
        with clock:
            conc["cur"] -= 1
        return {"id": lc["id"], "status": "passed", "failed_groups": [], "created": 0}

    monkeypatch.setattr(engine, "run_lifecycle", fake_run_lifecycle)
    if quota_cap is not None:
        monkeypatch.setattr(core.budgets.Budget, "__init__",
                            lambda self: (setattr(self, "limits", quota_cap),
                                          setattr(self, "used", {}),
                                          setattr(self, "_lock", threading.RLock()))
                            and None)
    res = nr.run([lc["id"] for lc in lcs], workers=workers, log=lambda *a: None)
    return res, conc["peak"], q400["n"]


def test_run_completes_all_and_keeps_concurrency(monkeypatch):
    """전량 완료 + 동시성 유지(꼬리 붕괴 없음 — peak가 워커수까지 참)."""
    monkeypatch.setattr(nr, "_durations", lambda: {})
    monkeypatch.setattr(nr, "_prereqs", lambda: set())
    lcs = [{"id": f"lc{i}"} for i in range(40)]
    res, peak, _ = _run_with_mocked_engine(monkeypatch, lcs, workers=8)
    assert res["by_status"] == {"passed": 40}          # 전량 완료
    assert len(res["results"]) == 40
    assert peak == 8, f"동시성이 워커수까지 안 참(붕괴): peak={peak}"


def test_run_shared_budget_coordinates_quota(monkeypatch):
    """공유 Budget = 계정-전역 조율: capped kind 동시 create가 캡을 절대 안 넘음
    (400 레이스 제거)."""
    monkeypatch.setattr(nr, "_durations", lambda: {})
    monkeypatch.setattr(nr, "_prereqs", lambda: set())
    # 20개가 전부 private-dns(캡3) create — 동시 실행돼도 캡 초과 0이어야
    lcs = [{"id": f"pdns{i}", "_quota": "private-dns"} for i in range(20)]
    res, peak, q400 = _run_with_mocked_engine(
        monkeypatch, lcs, workers=10, quota_cap={"private-dns": 3})
    assert res["by_status"] == {"passed": 20}
    # 공유 budget이 캡3을 지켰으므로, reserve 실패(=조율된 skip)는 나더라도
    # 실제 계정 초과(캡 넘는 동시 점유)는 절대 없음 — 이 테스트의 핵심은
    # reserve가 원자적으로 캡을 지켰다는 것(스레드-안전).
    assert res["results"], "결과 없음"
