"""공유 인프라 선택-기반 게이트 오프라인 검증 (오너 2026-07-15).

"subnet이 필요한(의존관계 있는) 시나리오면 공용 subnet을 만들어서 활용하면
되는데, 모든 시나리오에 무조건 공용 subnet을 만드는 게 맞지는 않다" —
networking-vpc-subnet 단독 런(adopt 마커 0개, self-create 전용)이 공유
VPC + 메인/DB 서브넷까지 세우던 낭비를 막는다:

  * ``shared_infra.shared_needs()`` — 선택의 adopt 마커를 ONE PASS 스캔해
    main/db/net/tgw/igw 필요 여부를 세분화 (db-subnet 게이트 2026-07-08의
    일반화). 기존 ``_needs_*`` 헬퍼들은 이 스캔의 필드 위임으로 수렴.
  * native_runner — 선택에 adopter가 없으면 provision을 아예 건너뛰고,
    있으면 필요한 것만 플래그로 넘긴다 (CLI provision과 동일 계약).

기존 오프라인 하네스(test_native_runner._run_with_mocked_engine 등)의
패턴을 재사용한다.
"""
from __future__ import annotations

import regression.scenarios.native_runner as nr
from regression.scenarios import shared_infra


def _lc(lcid, *adopts):
    steps = [{"method": "POST", "path": "/v1/things", "adopt": a}
             for a in adopts]
    steps.append({"method": "GET", "path": "/v1/things"})   # adopt 없는 스텝
    return {"id": lcid, "steps": steps}


def test_shared_needs_no_adopters_means_nothing_needed(monkeypatch):
    """networking-vpc-subnet 단독 케이스: adopt 마커가 없으면 any=False —
    공유 VPC/서브넷을 세울 이유가 없다."""
    monkeypatch.setattr(shared_infra.engine, "active_lifecycles",
                        lambda: [_lc("networking-vpc-subnet")])
    needs = shared_infra.shared_needs(only_ids={"networking-vpc-subnet"})
    assert needs == {"main": False, "db": False, "net": (), "tgw": False,
                     "igw": False, "any": False}


def test_shared_needs_field_granularity(monkeypatch):
    """db/tgw/igw adopter는 메인 공유 VPC를 함께 요구하고(main True),
    net-VPC(vpc#a/b) adopter는 main 없이도 any=True."""
    monkeypatch.setattr(shared_infra.engine, "active_lifecycles", lambda: [
        _lc("db-one", "subnet#db"),
        _lc("net-b-one", "vpc#b"),
        _lc("plain"),
    ])
    n_db = shared_infra.shared_needs(only_ids={"db-one"})
    assert n_db["db"] and n_db["main"] and n_db["any"]
    n_net = shared_infra.shared_needs(only_ids={"net-b-one"})
    assert n_net == {"main": False, "db": False, "net": ("b",), "tgw": False,
                     "igw": False, "any": True}
    n_all = shared_infra.shared_needs(only_ids=set())   # 빈 선택 = 전체
    assert n_all["db"] and n_all["net"] == ("b",) and n_all["main"]


def test_shared_needs_delegated_helpers_agree(monkeypatch):
    """기존 _needs_* 헬퍼(각자 테스트 보유)는 shared_needs 필드 위임 —
    같은 모델에서 같은 답을 내야 한다."""
    monkeypatch.setattr(shared_infra.engine, "active_lifecycles", lambda: [
        _lc("a", "igw"), _lc("b", "tgw"), _lc("c", "vpc#a"),
    ])
    monkeypatch.delenv("SCP_CRUD_IDS", raising=False)
    assert shared_infra._needs_shared_igw() is True
    assert shared_infra._needs_shared_tgw() is True
    assert shared_infra._needed_net_vpc_tags() == ("a",)
    assert shared_infra._needs_db_subnet() is False


def _native_harness(monkeypatch, lcs, provision):
    """test_native_runner의 최소 하네스 — engine을 mock해 nr.run()을 오프라인
    구동. provision 스텁으로 공유 인프라 호출 여부를 관측한다."""
    import core.budgets
    import core.config
    import core.http_client
    from regression.scenarios import engine

    monkeypatch.setattr(type(core.config.settings), "require_credentials",
                        lambda self: None, raising=False)
    monkeypatch.setattr(core.http_client, "ApiClient", lambda cfg: object())
    monkeypatch.setattr(core.budgets, "live_count", lambda kind: None)
    monkeypatch.setattr(engine, "active_lifecycles", lambda: lcs)
    monkeypatch.setattr(engine, "provision_shared_vpc", provision)
    monkeypatch.setattr(engine, "ResourceRegistry", lambda: object())
    monkeypatch.setattr(nr, "_durations", lambda: {})
    monkeypatch.setattr(nr, "_true_dependents", lambda: set())
    monkeypatch.setattr(
        engine, "run_lifecycle",
        lambda lc, client, cfg, *, budget=None, resource_registry=None,
        shared_ctx=None: {"id": lc["id"], "status": "passed",
                          "failed_groups": [], "created": 0})
    return engine


def test_native_skips_shared_infra_without_adopters(monkeypatch):
    """adopt 마커 없는 선택(self-create 전용)이면 native 러너는
    provision_shared_vpc를 아예 호출하지 않는다."""
    def boom(*a, **kw):
        raise AssertionError("provision_shared_vpc must NOT be called "
                             "for an adopter-free selection")
    _native_harness(monkeypatch, [_lc("networking-vpc-subnet")], boom)
    res = nr.run(["networking-vpc-subnet"], workers=1, log=lambda *a: None)
    assert res["by_status"] == {"passed": 1}, res


def test_native_provisions_with_selection_derived_flags(monkeypatch):
    """adopter가 있으면 provision을 호출하되, 선택이 요구하는 것만
    플래그로 넘긴다 (db만 있는 선택 → need_db_subnet=True, tgw/igw/net 없음)."""
    seen = {}

    def spy(c, cfg, **kw):
        seen.update(kw)
        return ({"shared_vpc_id": "v1", "shared_subnet_id": "s1"},
                lambda: None)
    _native_harness(monkeypatch, [_lc("db-one", "vpc", "subnet#db")], spy)
    monkeypatch.setattr(nr, "_wait_shared_subnets_active",
                        lambda client, ctx, log: None)
    res = nr.run(["db-one"], workers=1, log=lambda *a: None)
    assert res["by_status"] == {"passed": 1}, res
    assert seen.get("need_db_subnet") is True
    assert seen.get("need_net_vpcs") == ()
    assert seen.get("need_tgw") is False and seen.get("need_igw") is False
