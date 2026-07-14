"""공유 IGW adopt (2026-07-14 오너: "igw 지금 반영").

IGW는 VPC당 1개 배타. 메인 공유 VPC(adopt:vpc)를 여러 lifecycle이 나눠 쓰므로
각자 create-igw하면 2번째부터 400 already-associated → gen-heavy-lb-members가
wait-internet-gateway에서 실패했다. 공유 VPC에 IGW 1개를 상주시키고 lb-members·
vs-netops·vpn-gateway·pilot-net-basics가 adopt→skip한다. IGW create/PUT/delete
커버리지는 net-VPC IGW 소유자(vpc-subnet-vip-nat=A·gen-wave5-fw=B)가 유지.
TGW adopt와 동일 패턴(엔진 adopt 기계 재사용) — 차이는 IGW create가 400 관용이라
id를 capture_soft로 잡는다는 점(엔진 시딩이 `.id` 경로 capture_soft도 커버).
"""
from __future__ import annotations

from regression.scenarios import engine, shared_infra
from tests.offline.test_command_channel import FakeClient, _cfg, _r


def test_igw_registered_in_adopt_shared():
    assert engine._ADOPT_SHARED.get("igw") == "shared_igw_id"


def test_needs_shared_igw_detection(monkeypatch):
    # adopt:igw 있는 선택 → True; 없으면 False
    monkeypatch.setenv("SCP_CRUD_IDS", "gen-heavy-lb-members")
    assert shared_infra._needs_shared_igw() is True
    monkeypatch.setenv("SCP_CRUD_IDS", "iam-role-full")
    assert shared_infra._needs_shared_igw() is False


def test_igw_users_adopt_and_net_owners_self_create():
    """메인 공유 VPC 사용자 4개는 adopt:igw; net-VPC IGW 소유자(vip-nat=A·fw=B)는
    자기 IGW를 소유하므로 adopt:igw가 붙지 않아야(커버리지 담당)."""
    lcs = {lc["id"]: lc for lc in engine.active_lifecycles()}
    for lid in ("gen-heavy-lb-members", "gen-heavy-vs-netops",
                "networking-vpn-gateway-tunnel", "gen-pilot-net-basics"):
        assert any(s.get("adopt") == "igw" for s in lcs[lid].get("steps", [])), lid
    for lid in ("vpc-subnet-vip-nat", "gen-wave5-fw"):
        assert not any(s.get("adopt") == "igw"
                       for s in lcs[lid].get("steps", [])), \
            f"{lid}는 net-VPC IGW 소유자 — adopt:igw 붙으면 CRUD 커버리지 소실"


def test_adopt_igw_skips_create_mutate_delete_and_seeds_id_via_capture_soft(monkeypatch):
    """adopt:igw + shared_igw_id → create(POST, capture_soft) skip+id 시딩,
    set(PUT)·delete skip. 공유 IGW를 만들지도 mutate하지도 삭제하지도 않는다.
    id가 capture_soft(400 관용)로 잡히는 IGW 특성을 반영."""
    monkeypatch.setattr(engine, "_commands", None)
    monkeypatch.setattr(engine.time, "sleep", lambda s: None)
    engine._ADOPT_ACTIVE_SEEN.discard("shared-igw-1")
    lc = {"id": "igw-adopt-test", "service": "vpc", "enabled": True, "steps": [
        {"name": "create-internet-gateway", "method": "POST",
         "path": "/v1/internet-gateways", "adopt": "igw", "optional": True,
         "json": {"type": "IGW", "vpc_id": "{vpc_id}", "tags": []},
         "capture_soft": {"internet_gateway_id": "$.internet_gateway.id",
                          "owned_igw_id": "$.internet_gateway.id"},
         "expect_status": [200, 201, 202, 400],
         "cleanup": {"method": "DELETE",
                     "path": "/v1/internet-gateways/{owned_igw_id}",
                     "service": "vpc"}},
        {"name": "wait-internet-gateway", "method": "GET",
         "path": "/v1/internet-gateways/{internet_gateway_id}",
         "expect_status": [200]},
        {"name": "set-igw", "method": "PUT",
         "path": "/v1/internet-gateways/{internet_gateway_id}",
         "adopt": "igw", "json": {"firewall_enabled": True},
         "expect_status": [200, 202]},
        {"name": "delete-internet-gateway", "method": "DELETE",
         "path": "/v1/internet-gateways/{owned_igw_id}",
         "adopt": "igw", "expect_status": [200, 202, 204]},
    ]}
    client = FakeClient({
        ("GET", "/v1/internet-gateways/"):
            _r(200, {"internet_gateway": {"id": "shared-igw-1", "state": "ACTIVE"}}),
    })
    res = engine.run_lifecycle(lc, client, _cfg(),
                               shared_ctx={"shared_igw_id": "shared-igw-1"})
    assert res["status"] == "passed", res
    methods = {m for m, _ in client.calls}
    assert "POST" not in methods, "create-igw는 adopt로 skip돼야"
    assert "PUT" not in methods, "set-igw는 공유 mutate 방지로 skip돼야"
    assert "DELETE" not in methods, "delete-igw는 retain으로 skip돼야"
    # internet_gateway_id가 공유로 시딩(capture_soft `.id`) → wait가 shared id로 GET
    assert ("GET", "/v1/internet-gateways/shared-igw-1") in client.calls


def test_adopt_igw_capture_soft_only_seeds_dot_id_paths(monkeypatch):
    """capture_soft 중 소스가 `.id`가 아닌 것(account_id=$..account_id)은 공유
    id로 시딩하지 않는다 — vpc-peering create-vpc-b의 account_id 오염 방지 규약."""
    monkeypatch.setattr(engine, "_commands", None)
    monkeypatch.setattr(engine.time, "sleep", lambda s: None)
    engine._ADOPT_ACTIVE_SEEN.discard("shared-igw-2")
    captured = {}
    lc = {"id": "igw-seed-filter-test", "service": "vpc", "enabled": True, "steps": [
        {"name": "create-internet-gateway", "method": "POST",
         "path": "/v1/internet-gateways", "adopt": "igw", "optional": True,
         "json": {"type": "IGW", "vpc_id": "{vpc_id}", "tags": []},
         "capture_soft": {"internet_gateway_id": "$.internet_gateway.id",
                          "igw_account_id": "$.internet_gateway.account_id"},
         "expect_status": [200, 201, 202, 400]},
        {"name": "probe", "method": "GET",
         "path": "/v1/internet-gateways/{internet_gateway_id}",
         "expect_status": [200]},
    ]}
    client = FakeClient({
        ("GET", "/v1/internet-gateways/"):
            _r(200, {"internet_gateway": {"id": "shared-igw-2", "state": "ACTIVE"}}),
    })
    res = engine.run_lifecycle(lc, client, _cfg(),
                               shared_ctx={"shared_igw_id": "shared-igw-2"})
    assert res["status"] == "passed", res
    # id는 시딩(GET가 shared id로), account_id는 미시딩(→ 공유 id로 오염 안 됨)
    assert ("GET", "/v1/internet-gateways/shared-igw-2") in client.calls
    # account_id가 공유 id로 시딩됐다면 그 값으로 새는 흔적이 없어야: 미해석 토큰은
    # 어떤 호출 경로에도 shared-igw-2로 안 나타난다(그 var는 어떤 스텝도 안 씀).
    assert not any("account" in p for _, p in client.calls)


def test_adopt_igw_falls_back_to_self_create_without_shared(monkeypatch):
    """shared_igw_id 없으면 adopt는 no-op → self-create(현행 find-or-create 폴백,
    무회귀)."""
    monkeypatch.setattr(engine, "_commands", None)
    monkeypatch.setattr(engine.time, "sleep", lambda s: None)
    lc = {"id": "igw-selfcreate-test", "service": "vpc", "enabled": True, "steps": [
        {"name": "create-internet-gateway", "method": "POST",
         "path": "/v1/internet-gateways", "adopt": "igw", "optional": True,
         "json": {"type": "IGW", "vpc_id": "vpc-x", "tags": []},
         "capture_soft": {"internet_gateway_id": "$.internet_gateway.id",
                          "owned_igw_id": "$.internet_gateway.id"},
         "expect_status": [200, 201, 202, 400],
         "cleanup": {"method": "DELETE",
                     "path": "/v1/internet-gateways/{owned_igw_id}",
                     "service": "vpc"}},
    ]}
    client = FakeClient({
        ("POST", "/v1/internet-gateways"):
            _r(201, {"internet_gateway": {"id": "own-igw", "state": "ACTIVE"}}),
    })
    res = engine.run_lifecycle(lc, client, _cfg(), shared_ctx={})   # no shared IGW
    assert res["status"] == "passed", res
    assert ("POST", "/v1/internet-gateways") in client.calls, \
        "공유 없으면 self-create(POST 발행)"


def test_provision_creates_shared_igw_in_vpc_and_tears_it_before_vpc(monkeypatch):
    """provision_shared_vpc(need_igw=True)는 공유 VPC ACTIVE 직후 IGW를 그 VPC에
    붙여 만들고 shared_igw_id를 반환한다. teardown은 IGW를 VPC보다 먼저 삭제한다
    (IGW attached면 VPC DELETE가 409)."""
    monkeypatch.setattr(engine.time, "sleep", lambda s: None)
    client = FakeClient({
        ("POST", "/v1/vpcs"):
            _r(201, {"vpc": {"id": "shared-vpc-1", "state": "ACTIVE"}}),
        ("GET", "/v1/vpcs/"): _r(200, {"vpc": {"state": "ACTIVE"}}),
        ("POST", "/v1/subnets"):
            _r(201, {"subnet": {"id": "sub-1", "state": "ACTIVE"}}),
        ("GET", "/v1/subnets/"): _r(404, {}),   # teardown gone-wait 즉시 종료
        ("POST", "/v1/internet-gateways"):
            _r(201, {"internet_gateway": {"id": "shared-igw-1", "state": "ACTIVE"}}),
    })
    ctx, teardown = engine.provision_shared_vpc(
        client, _cfg(), need_db_subnet=False, need_igw=True)
    assert ctx.get("shared_igw_id") == "shared-igw-1", ctx
    assert ("POST", "/v1/internet-gateways") in client.calls, "공유 IGW POST 발행"
    client.calls.clear()
    teardown()
    dels = [p for (m, p) in client.calls if m == "DELETE"]
    igw_i = next((i for i, p in enumerate(dels)
                  if p == "/v1/internet-gateways/shared-igw-1"), -1)
    vpc_i = next((i for i, p in enumerate(dels)
                  if p == "/v1/vpcs/shared-vpc-1"), -1)
    assert igw_i >= 0, f"IGW delete 발행돼야: {dels}"
    assert vpc_i >= 0, f"VPC delete 발행돼야: {dels}"
    assert igw_i < vpc_i, f"IGW가 VPC보다 먼저 삭제돼야: igw@{igw_i} vpc@{vpc_i}"
