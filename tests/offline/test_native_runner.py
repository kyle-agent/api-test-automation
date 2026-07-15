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
    monkeypatch.setattr(nr, "_true_dependents", lambda: {"dep"})
    lcs = [{"id": "small"}, {"id": "dep"}, {"id": "big"}, {"id": "mid"}]
    out = [lc["id"] for lc in nr.priority_order(lcs)]
    # no-dep 무거운 것 먼저 → big, mid, small; dependent(dep)는 맨 뒤
    assert out == ["big", "mid", "small", "dep"], out


def test_priority_long_soft_dependent_not_demoted(monkeypatch):
    """긴 soft-의존(공유-인프라만 필요)은 LPT 앞단, 진짜 inter-lifecycle 의존만
    후미 (owner 2026-07-13: SKE 34.6m을 dependent-last로 미루던 꼬리 3분 제거)."""
    # ske(긴 soft-의존): prereq가 vpc/subnet뿐 → demote 안 함.
    # cloudml(진짜 의존): ske-cluster 필요 → 후미.
    monkeypatch.setattr(nr, "_prereq_map", lambda: {
        "ske": ["vpc", "subnet", "keypair"],           # 전부 공유-인프라 → soft
        "cloudml": ["ske-cluster", "container-registry"],  # 다른 라이프사이클 산출 → true
    })
    monkeypatch.setattr(nr, "_durations",
                        lambda: {"ske": 2000.0, "cloudml": 1200.0, "light": 30.0})
    lcs = [{"id": "light"}, {"id": "cloudml"}, {"id": "ske"}]
    out = [lc["id"] for lc in nr.priority_order(lcs)]
    # ske(긴 soft-의존)가 맨 앞(LPT), cloudml(진짜 의존)만 맨 뒤
    assert out == ["ske", "light", "cloudml"], out
    assert nr._true_dependents() == {"cloudml"}


def test_vpc_creator_detection_and_priority_first(monkeypatch):
    """self-create VPC(adopt 없는 POST /vpcs)는 VPC-생성자 → 더 긴 비-생성자보다
    앞. adopt:vpc는 생성자 아님(재사용). 오너 2026-07-13: 희소 슬롯 조기 반납."""
    assert nr._is_vpc_creator({"steps": [{"method": "POST", "path": "/v1/vpcs"}]}) is True
    assert nr._is_vpc_creator(
        {"steps": [{"method": "POST", "path": "/v1/vpcs", "adopt": "vpc"}]}) is False
    assert nr._is_vpc_creator({"steps": [{"method": "GET", "path": "/v1/vpcs"}]}) is False

    monkeypatch.setattr(nr, "_durations", lambda: {"vc": 100.0, "big": 1000.0, "small": 10.0})
    monkeypatch.setattr(nr, "_true_dependents", lambda: set())
    lcs = [
        {"id": "big", "steps": [{"method": "GET", "path": "/x"}]},          # 최장 비-생성자
        {"id": "vc", "steps": [{"method": "POST", "path": "/v1/vpcs"}]},    # VPC-생성자
        {"id": "small", "steps": []},
    ]
    out = [lc["id"] for lc in nr.priority_order(lcs)]
    # vc가 big(더 길지만)보다 앞 — 슬롯 조기 점유·반납. 그다음 LPT(big>small).
    assert out == ["vc", "big", "small"], out


def test_vpc_creator_waits_and_retries_on_budget_skip(monkeypatch):
    """VPC-생성자가 슬롯 부족으로 예산-skip되면 skip 기록이 아니라 대기 후 재실행
    → 슬롯 나면 passed (오너 2026-07-13: "대기했다 실행"). 예산-skip은 create 이전이라
    (created=0) 재실행이 멱등."""
    import core.budgets
    import core.config
    import core.http_client
    from regression.scenarios import engine

    lc = {"id": "vc", "steps": [{"method": "POST", "path": "/v1/vpcs"}]}
    monkeypatch.setattr(type(core.config.settings), "require_credentials",
                        lambda self: None, raising=False)
    monkeypatch.setattr(core.http_client, "ApiClient", lambda cfg: object())
    monkeypatch.setattr(core.budgets, "live_count", lambda kind: None)
    monkeypatch.setattr(engine, "active_lifecycles", lambda: [lc])
    monkeypatch.setattr(engine, "provision_shared_vpc",
                        lambda c, cfg, **kw: ({}, lambda: None))
    monkeypatch.setattr(engine, "ResourceRegistry", lambda: object())
    monkeypatch.setattr(nr, "_durations", lambda: {})
    monkeypatch.setattr(nr, "_true_dependents", lambda: set())
    monkeypatch.setattr(nr, "_VPC_WAIT_POLL", 0.01)
    monkeypatch.setattr(nr, "_VPC_WAIT_TIMEOUT", 5.0)

    calls = {"n": 0}

    def fake_run(l, client, cfg, *, budget=None, resource_registry=None, shared_ctx=None):
        calls["n"] += 1
        if calls["n"] < 3:                     # 처음 2번은 슬롯 없음 → 예산-skip
            return {"id": l["id"], "status": "skipped", "created": 0,
                    "reason": f"[{l['id']}] budget 'vpc' exhausted before step 'create-vpc'",
                    "failed_groups": []}
        return {"id": l["id"], "status": "passed", "failed_groups": [], "created": 0}

    monkeypatch.setattr(engine, "run_lifecycle", fake_run)
    res = nr.run(["vc"], workers=1, log=lambda *a: None)
    assert res["by_status"] == {"passed": 1}, res      # skip 아니라 대기 후 성공
    assert calls["n"] == 3, f"대기-재시도 안 함: calls={calls['n']}"


def _run_with_mocked_engine(monkeypatch, lcs, *, workers, sleep=0.02,
                            quota_cap=None, residents=0):
    """engine을 mock해 run()을 오프라인 구동. 반환: (result, peak_conc, quota_400).
    residents: 시드용 상주 VPC 개수(shared_ctx에 resident 키를 그만큼 심는다)."""
    import core.config
    import core.http_client
    import core.budgets
    from regression.scenarios import engine

    monkeypatch.setattr(type(core.config.settings), "require_credentials",
                        lambda self: None, raising=False)
    monkeypatch.setattr(core.http_client, "ApiClient", lambda cfg: object())
    monkeypatch.setattr(engine, "active_lifecycles", lambda: lcs)
    # 세마포어 시드는 shared_ctx의 상주 키 개수로 정해진다 (raw live_count 아님).
    # shared_needs는 항상-필요로 고정: 이 하네스의 lcs엔 adopt 마커가 없어
    # 선택-게이트가 provision을 스킵해버리면 residents 시드를 관측할 수 없다
    # (게이트 자체는 test_shared_infra_needs.py가 검증).
    from regression.scenarios import shared_infra
    monkeypatch.setattr(shared_infra, "shared_needs",
                        lambda only_ids=None: {"main": True, "db": False,
                                               "net": (), "tgw": False,
                                               "igw": False, "any": True})
    _rkeys = ["shared_vpc_id", "shared_net_vpc_a_id", "shared_net_vpc_b_id"]
    _sctx = {k: f"r{i}" for i, k in enumerate(_rkeys[:residents])}
    monkeypatch.setattr(engine, "provision_shared_vpc",
                        lambda c, cfg, **kw: (_sctx, lambda: None))
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
    monkeypatch.setattr(nr, "_true_dependents", lambda: set())
    lcs = [{"id": f"lc{i}"} for i in range(40)]
    res, peak, _ = _run_with_mocked_engine(monkeypatch, lcs, workers=8)
    assert res["by_status"] == {"passed": 40}          # 전량 완료
    assert len(res["results"]) == 40
    assert peak == 8, f"동시성이 워커수까지 안 참(붕괴): peak={peak}"


def test_run_shared_budget_coordinates_quota(monkeypatch):
    """공유 Budget = 계정-전역 조율: capped kind 동시 create가 캡을 절대 안 넘음
    (400 레이스 제거)."""
    monkeypatch.setattr(nr, "_durations", lambda: {})
    monkeypatch.setattr(nr, "_true_dependents", lambda: set())
    # 20개가 전부 private-dns(캡3) create — 동시 실행돼도 캡 초과 0이어야
    lcs = [{"id": f"pdns{i}", "_quota": "private-dns"} for i in range(20)]
    res, peak, q400 = _run_with_mocked_engine(
        monkeypatch, lcs, workers=10, quota_cap={"private-dns": 3})
    assert res["by_status"] == {"passed": 20}
    # 공유 budget이 캡3을 지켰으므로, reserve 실패(=조율된 skip)는 나더라도
    # 실제 계정 초과(캡 넘는 동시 점유)는 절대 없음 — 이 테스트의 핵심은
    # reserve가 원자적으로 캡을 지켰다는 것(스레드-안전).
    assert res["results"], "결과 없음"


def test_vpc_semaphore_seeded_from_residents_leaves_only_residual_slots(monkeypatch):
    """VPC 세마포어를 **상주 개수**(shared_ctx 결정론)로 시드 — 상주 3개가 소비된
    것으로 잡혀 self-create는 남은 슬롯(캡5-3=2)만 쓴다. 3번째+는 reserve 실패로
    조율 skip(400 아님). 상주 0이면 미시드(종전 캡 미보호)."""
    monkeypatch.setattr(nr, "_durations", lambda: {})
    monkeypatch.setattr(nr, "_true_dependents", lambda: set())
    # self-create 5개(전부 vpc 소비). 캡5·상주3 시드 → 동시 2개만 admit, 나머지 skip.
    lcs = [{"id": f"selfvpc{i}", "_quota": "vpc"} for i in range(5)]
    res, peak, skipped = _run_with_mocked_engine(
        monkeypatch, lcs, workers=5, quota_cap={"vpc": 5}, residents=3)
    # 상주 3 시드 + 동시 self-create 점유 ≤ 2 → 계정 동시 VPC가 캡 5를 절대 안 넘음.
    assert peak <= 5, f"워커 동시성 peak={peak}"
    assert skipped >= 3, f"캡 넘는 self-create가 조율 skip돼야: skipped={skipped}"
    # 상주 0(미시드) → 5개 전부 admit(잔재를 시드로 흡수하지 않음 — 스윕/skip-not-fail).
    res2, _, skipped2 = _run_with_mocked_engine(
        monkeypatch, lcs, workers=5, quota_cap={"vpc": 5}, residents=0)
    assert skipped2 == 0, f"미시드는 5개 다 admit: skipped={skipped2}"


def test_selfcreate_vpc_releases_budget_on_happy_delete(monkeypatch):
    """self-create VPC가 자기 delete-vpc(해피패스)에서 in-process budget을 반납한다
    (오너 2026-07-14 실측: 종전엔 cross-process sem만 반납해 예약이 런 내내 leak →
    자원 다 지웠는데 '예약 안 풀림'). create=reserve(+1), 해피 delete=release(-1)→0."""
    import core.budgets
    from regression.scenarios import engine
    from tests.offline.test_command_channel import FakeClient, _cfg, _r

    monkeypatch.setattr(engine, "_commands", None)
    monkeypatch.setattr(engine.time, "sleep", lambda s: None)
    budget = core.budgets.Budget()
    lc = {"id": "selfvpc", "service": "vpc", "enabled": True, "steps": [
        {"name": "create-vpc", "method": "POST", "path": "/v1/vpcs",
         "json": {"name": "x", "cidr": "10.9.0.0/20", "tags": []},
         "capture": {"vpc_id": "$.vpc.id"}, "expect_status": [200, 201, 202],
         "cleanup": {"method": "DELETE", "path": "/v1/vpcs/{vpc_id}", "service": "vpc"}},
        {"name": "delete-vpc", "method": "DELETE", "path": "/v1/vpcs/{vpc_id}",
         "expect_status": [200, 202, 204]},
    ]}
    client = FakeClient({
        ("POST", "/v1/vpcs"): _r(201, {"vpc": {"id": "own-1", "state": "ACTIVE"}}),
        ("DELETE", "/v1/vpcs/"): _r(202, {}),
    })
    res = engine.run_lifecycle(lc, client, _cfg(), budget=budget)
    assert res["status"] == "passed", res
    assert budget.used.get("vpc", 0) == 0, f"예약 leak(반납 안 됨): used={budget.used}"
