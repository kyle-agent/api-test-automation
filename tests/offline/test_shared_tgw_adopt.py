"""공유 TGW adopt (2026-07-13 오너: "TGW adopt(B)로 해줘").

TGW 계정 캡 3인데 self-create가 3개(vpc-transit-gateway-children·gen-private-nat·
heavy-shared-networking) → 헤드룸 0, 잔재 1개면 exceed-max 레이스. children만
TGW를 소유(CRUD 주인공)하고, 나머지 둘은 전제조건 용도라 공유 TGW를 adopt →
동시 TGW 3→2. 공유 VPC와 동일 패턴(엔진 adopt 기계 재사용).
"""
from __future__ import annotations

from regression.scenarios import engine, shared_infra
from tests.offline.test_command_channel import FakeClient, _cfg, _r


def test_tgw_registered_in_adopt_shared():
    assert engine._ADOPT_SHARED.get("tgw") == "shared_tgw_id"


def test_needs_shared_tgw_detection(monkeypatch):
    # adopt:tgw 있는 선택 → True; 없으면 False
    monkeypatch.setenv("SCP_CRUD_IDS", "gen-private-nat")
    assert shared_infra._needs_shared_tgw() is True
    monkeypatch.setenv("SCP_CRUD_IDS", "iam-role-full")
    assert shared_infra._needs_shared_tgw() is False


def test_children_still_self_creates_tgw():
    """vpc-transit-gateway-children는 TGW CRUD 주인공 → adopt 안 붙어야(self)."""
    lcs = {lc["id"]: lc for lc in engine.active_lifecycles()}
    ch = lcs["vpc-transit-gateway-children"]
    assert not any(s.get("adopt") == "tgw" for s in ch.get("steps", []))
    # 반대로 두 전제조건 사용자는 adopt:tgw
    for lid in ("gen-private-nat", "heavy-shared-networking"):
        assert any(s.get("adopt") == "tgw" for s in lcs[lid].get("steps", [])), lid


def test_adopt_tgw_skips_create_mutate_delete_and_seeds_id(monkeypatch):
    """adopt:tgw + shared_tgw_id → create(POST) skip+id 시딩, set(PUT)·delete skip.
    공유 TGW를 만들지도 mutate하지도 삭제하지도 않는다(retain)."""
    monkeypatch.setattr(engine, "_commands", None)
    monkeypatch.setattr(engine.time, "sleep", lambda s: None)
    engine._ADOPT_ACTIVE_SEEN.discard("shared-tgw-1")   # 캐시 클리어(폴 1회 보장)
    lc = {"id": "tgw-adopt-test", "service": "vpc", "enabled": True, "steps": [
        {"name": "create-transit-gateway", "method": "POST", "path": "/v1/transit-gateways",
         "adopt": "tgw", "json": {"name": "x", "tags": []},
         "capture": {"transit_gateway_id": "$.transit_gateway.id"},
         "expect_status": [200, 201, 202]},
        {"name": "use-tgw", "method": "GET",
         "path": "/v1/transit-gateways/{transit_gateway_id}", "expect_status": [200]},
        {"name": "set-tgw", "method": "PUT",
         "path": "/v1/transit-gateways/{transit_gateway_id}",
         "adopt": "tgw", "json": {"description": "x"}, "expect_status": [200, 202]},
        {"name": "delete-transit-gateway", "method": "DELETE",
         "path": "/v1/transit-gateways/{transit_gateway_id}",
         "adopt": "tgw", "expect_status": [200, 202, 204]},
    ]}
    client = FakeClient({
        ("GET", "/v1/transit-gateways/"):
            _r(200, {"transit_gateway": {"id": "shared-tgw-1", "state": "ACTIVE"}}),
    })
    res = engine.run_lifecycle(lc, client, _cfg(), shared_ctx={"shared_tgw_id": "shared-tgw-1"})
    assert res["status"] == "passed", res
    methods = {m for m, _ in client.calls}
    assert "POST" not in methods, "create-tgw는 adopt로 skip돼야"
    assert "PUT" not in methods, "set-tgw는 공유 mutate 방지로 skip돼야"
    assert "DELETE" not in methods, "delete-tgw는 retain으로 skip돼야"
    # transit_gateway_id가 공유로 시딩 → use-tgw가 shared id로 GET
    assert ("GET", "/v1/transit-gateways/shared-tgw-1") in client.calls


def test_adopt_tgw_falls_back_to_self_create_without_shared(monkeypatch):
    """shared_tgw_id 없으면 adopt는 no-op → self-create(현행 폴백, 무회귀)."""
    monkeypatch.setattr(engine, "_commands", None)
    monkeypatch.setattr(engine.time, "sleep", lambda s: None)
    lc = {"id": "tgw-selfcreate-test", "service": "vpc", "enabled": True, "steps": [
        {"name": "create-transit-gateway", "method": "POST", "path": "/v1/transit-gateways",
         "adopt": "tgw", "json": {"name": "x", "tags": []},
         "capture": {"transit_gateway_id": "$.transit_gateway.id"},
         "expect_status": [200, 201, 202],
         "cleanup": {"method": "DELETE", "path": "/v1/transit-gateways/{transit_gateway_id}",
                     "service": "vpc"}},
    ]}
    client = FakeClient({
        ("POST", "/v1/transit-gateways"):
            _r(201, {"transit_gateway": {"id": "own-tgw", "state": "ACTIVE"}}),
    })
    res = engine.run_lifecycle(lc, client, _cfg(), shared_ctx={})   # no shared TGW
    assert res["status"] == "passed", res
    assert ("POST", "/v1/transit-gateways") in client.calls, "공유 없으면 self-create"
