"""fallback_on_error_code — 바디 에러 코드가 일치하면 바디를 바꿔 1회 재전송.

2026-09-17 run 89de ↔ 2026-08-20 run 3e67 실측이 계기: direct-connect create가
multi-zone VPC에서는 uplink_active/standby_zone **필수**(400 required-zone),
단일존 VPC에서는 **금지**(400 active-standby-zone-not-allowed). 존 수를
read-only로 알 수 없어 서버의 거절 코드를 신호로 바디 변형을 태운다.
같은 커밋의 DBaaS DATA 디스크 계약 수리도 여기서 정적으로 고정한다.
"""
from __future__ import annotations

import json
import pathlib

from regression.scenarios import engine

ROOT = pathlib.Path(engine.__file__).resolve().parent
REQ_ZONE = {"errors": [{"code": "scp-network.direct-connect.required-zone",
                        "detail": "uplink_active_zone and uplink_standby_zone are required "
                                  "when VPC(x) is in multi zone mode.", "status": 400}]}
NOT_ALLOWED = {"errors": [{"code": "scp-network.direct-connect.active-standby-zone-not-allowed",
                           "detail": "VPC(x) has only one zone.", "status": 400}]}
OTHER_400 = {"errors": [{"code": "ValidationError", "detail": "Field required", "status": 400}]}

DC_FALLBACK = [{"codes": ["scp-network.direct-connect.required-zone"],
                "merge": {"uplink_active_zone": "{zone}", "uplink_standby_zone": "{zone_alt}"}}]


class _Resp:
    def __init__(self, status, body=None):
        self.status, self.body, self.raw_text = status, body, ""


class _FakeClient:
    def __init__(self, seq):
        self.seq, self.bodies = list(seq), []

    def request(self, method, path, *, json=None, **k):
        self.bodies.append(json)
        return self.seq.pop(0) if len(self.seq) > 1 else self.seq[0]


CTX = {"zone": "kr-west1-b", "zone_alt": "kr-west1-a"}
BASE = {"name": "regrdc", "vpc_id": "v1"}


def test_required_zone_resends_with_zones_from_ctx():
    c = _FakeClient([_Resp(400, REQ_ZONE), _Resp(202, {"direct_connect": {"id": "d1"}})])
    step = {"name": "create-direct-connect", "method": "POST", "fallback_on_error_code": DC_FALLBACK}
    resp = engine._run_step(c, step, "/v1/direct-connects", dict(BASE), "direct-connect", CTX)
    assert resp.status == 202 and len(c.bodies) == 2
    assert "uplink_active_zone" not in c.bodies[0]
    assert c.bodies[1]["uplink_active_zone"] == "kr-west1-b"
    assert c.bodies[1]["uplink_standby_zone"] == "kr-west1-a"
    assert c.bodies[1]["vpc_id"] == "v1"             # 원본 필드 보존


def test_single_zone_vpc_accepts_base_body_no_resend():
    c = _FakeClient([_Resp(202, {"direct_connect": {"id": "d1"}})])
    step = {"name": "create-direct-connect", "method": "POST", "fallback_on_error_code": DC_FALLBACK}
    resp = engine._run_step(c, step, "/v1/direct-connects", dict(BASE), "direct-connect", CTX)
    assert resp.status == 202 and len(c.bodies) == 1


def test_non_matching_code_is_not_resent():
    c = _FakeClient([_Resp(400, OTHER_400), _Resp(202, {})])
    step = {"name": "create-direct-connect", "method": "POST", "fallback_on_error_code": DC_FALLBACK}
    resp = engine._run_step(c, step, "/v1/direct-connects", dict(BASE), "direct-connect", CTX)
    assert resp.status == 400 and len(c.bodies) == 1


def test_drop_variant_and_single_shot(monkeypatch):
    """drop 변형 + 항목당 1회: 재전송이 다시 같은 코드로 거절돼도 무한 반복하지 않는다."""
    monkeypatch.setattr(engine.time, "sleep", lambda s: None)
    c = _FakeClient([_Resp(400, NOT_ALLOWED), _Resp(400, NOT_ALLOWED)])
    step = {"name": "create-direct-connect", "method": "POST",
            "fallback_on_error_code": {"codes": "scp-network.direct-connect.active-standby-zone-not-allowed",
                                       "drop": ["uplink_active_zone", "uplink_standby_zone"]}}
    body = {**BASE, "uplink_active_zone": "kr-west1-b", "uplink_standby_zone": "kr-west1-a"}
    resp = engine._run_step(c, step, "/v1/direct-connects", body, "direct-connect", CTX)
    assert resp.status == 400 and len(c.bodies) == 2
    assert "uplink_active_zone" not in c.bodies[1] and c.bodies[1]["vpc_id"] == "v1"


def test_fallback_then_status_ladder_continues(monkeypatch):
    """변형 재전송이 4xx면 기존 retry_on_status 사다리가 변형된 바디로 이어받는다."""
    monkeypatch.setattr(engine.time, "sleep", lambda s: None)
    c = _FakeClient([_Resp(400, REQ_ZONE), _Resp(409, {}), _Resp(202, {})])
    step = {"name": "create-direct-connect", "method": "POST", "fallback_on_error_code": DC_FALLBACK,
            "retry_on_status": [409], "retries": 2, "retry_interval": 0.01}
    resp = engine._run_step(c, step, "/v1/direct-connects", dict(BASE), "direct-connect", CTX)
    assert resp.status == 202 and len(c.bodies) == 3
    assert all("uplink_active_zone" in b for b in c.bodies[1:])


# ---- scenario invariants fixed by the same repair ---------------------------
def _all_steps():
    files = sorted((ROOT / "lifecycles").glob("*.json")) + [ROOT / "scenarios.json"]
    for p in files:
        d = json.loads(p.read_text(encoding="utf-8"))
        lcs = d.get("lifecycles") if isinstance(d, dict) else d
        for lc in lcs or []:
            for st in lc.get("steps", []):
                yield p.name, lc, st


def test_direct_connect_creates_send_zones_only_as_fallback():
    seen = 0
    for fname, lc, st in _all_steps():
        if st.get("method") == "POST" and st.get("path") == "/v1/direct-connects":
            if not any(200 <= s < 300 for s in st.get("expect_status") or []):
                continue          # negative step (duplicate-must-4xx) — 성공 경로가 아님
            seen += 1
            body = st.get("json") or {}
            assert "uplink_active_zone" not in body, f"{fname}:{lc['id']}:{st['name']} 기본 바디에 존 리터럴"
            fbs = st.get("fallback_on_error_code")
            assert fbs, f"{fname}:{lc['id']}:{st['name']} fallback_on_error_code 누락"
            fb = fbs[0] if isinstance(fbs, list) else fbs
            assert "scp-network.direct-connect.required-zone" in fb["codes"]
            assert fb["merge"] == {"uplink_active_zone": "{zone}", "uplink_standby_zone": "{zone_alt}"}
    assert seen >= 3


def test_dbaas_engine_creates_provision_a_data_disk():
    """mysql/postgresql/mariadb/epas: OS-only instance group은 400
    InvalidBlockStorageDataDiskCount (run 89de, 13 lifecycle). DATA 56 SSD 동반."""
    engines = {"mysql", "postgresql", "mariadb", "epas"}
    checked = 0
    for fname, lc, st in _all_steps():
        body = st.get("json")
        if not isinstance(body, dict) or not body.get("instance_groups"):
            continue
        svc = (st.get("service") or lc.get("service") or "").split("/")[-1]
        if svc not in engines:
            continue
        for ig in body["instance_groups"]:
            bsg = ig.get("block_storage_groups") or []
            roles = [b.get("role_type") for b in bsg]
            if "OS" not in roles:
                continue
            checked += 1
            assert "DATA" in roles, f"{fname}:{lc['id']}:{st['name']} DATA 디스크 누락"
            data = next(b for b in bsg if b["role_type"] == "DATA")
            assert data["size_gb"] % 8 == 0 and data["volume_type"] == "SSD"
    assert checked >= 30
