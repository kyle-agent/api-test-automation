"""retry_on_error_code — HTTP 상태가 아니라 바디 에러 코드로 판정하는 조건부 사다리.

2026-08-01 run 3ebe 실측이 계기: 같은 초에 병렬 발사된 DBaaS POST /v1/clusters
5발 중 2발이 400 `Dbaas.RbacCreateError`("Try again.")로 거절되고 3발은 정상
생성 — 서버측 RBAC 프로비저닝 동시성 레이스가 **400**(클라이언트 잘못 클래스)
으로 표면화되는 클래스다. blanket retry_on_status[400]은 진짜 검증 400까지
재시도해 결함을 가리므로, 명시된 코드일 때만 사다리를 태운다.
"""
from __future__ import annotations

from regression.scenarios import engine

RBAC_400 = {"errors": [{"code": "Dbaas.RbacCreateError",
                        "detail": "Create Rbac Error. Try again.",
                        "status": 400, "title": "BadRequest"}]}
VALIDATION_400 = {"errors": [{"code": "ValidationError",
                              "detail": "Field required", "status": 400}]}


class _Resp:
    def __init__(self, status, body=None):
        self.status, self.body = status, body
        self.raw_text = ""


class _FakeClient:
    def __init__(self, seq):
        self.seq = list(seq)
        self.calls = 0

    def request(self, *a, **k):
        self.calls += 1
        return self.seq.pop(0) if len(self.seq) > 1 else self.seq[0]


def _no_sleep(monkeypatch):
    slept = []
    monkeypatch.setattr(engine.time, "sleep", lambda s: slept.append(s))
    return slept


def test_error_code_match_retries_until_2xx(monkeypatch):
    _no_sleep(monkeypatch)
    client = _FakeClient([_Resp(400, RBAC_400), _Resp(400, RBAC_400), _Resp(201, {})])
    step = {"name": "create-cluster", "method": "POST",
            "retry_on_error_code": ["Dbaas.RbacCreateError"],
            "retries": 3, "retry_interval": 0.01}
    resp = engine._run_step(client, step, "/v1/clusters", {}, "postgresql", {})
    assert resp.status == 201 and client.calls == 3


def test_non_matching_code_not_retried(monkeypatch):
    """일반 검증 400은 코드 불일치 — 사다리 미발동 (결함 은폐 방지)."""
    _no_sleep(monkeypatch)
    client = _FakeClient([_Resp(400, VALIDATION_400), _Resp(201, {})])
    step = {"name": "create-cluster", "method": "POST",
            "retry_on_error_code": ["Dbaas.RbacCreateError"],
            "retries": 3, "retry_interval": 0.01}
    resp = engine._run_step(client, step, "/v1/clusters", {}, "postgresql", {})
    assert resp.status == 400 and client.calls == 1


def test_without_key_400_untouched(monkeypatch):
    _no_sleep(monkeypatch)
    client = _FakeClient([_Resp(400, RBAC_400), _Resp(201, {})])
    step = {"name": "create-cluster", "method": "POST",
            "retry_on_status": [500, 503], "retries": 2, "retry_interval": 0.01}
    resp = engine._run_step(client, step, "/v1/clusters", {}, "postgresql", {})
    assert resp.status == 400 and client.calls == 1


def test_status_ladder_keeps_fixed_interval_code_ladder_jitters(monkeypatch):
    """상태-사다리는 결정적 간격 유지, 코드-사다리는 ±25% 지터 — 같은 초에
    거절된 병렬 create들이 재시도에서 또 충돌하지 않게 (run 3ebe: 5발 중
    2발 동시 거절)."""
    slept = _no_sleep(monkeypatch)
    client = _FakeClient([_Resp(500, None), _Resp(201, {})])
    step = {"name": "s", "method": "POST", "retry_on_status": [500],
            "retries": 1, "retry_interval": 8}
    engine._run_step(client, step, "/v1/clusters", {}, "postgresql", {})
    assert slept == [8]

    slept2 = _no_sleep(monkeypatch)
    client2 = _FakeClient([_Resp(400, RBAC_400), _Resp(201, {})])
    step2 = {"name": "s", "method": "POST",
             "retry_on_error_code": ["Dbaas.RbacCreateError"],
             "retries": 1, "retry_interval": 8}
    engine._run_step(client2, step2, "/v1/clusters", {}, "postgresql", {})
    assert len(slept2) == 1 and 6.0 <= slept2[0] <= 10.0 and slept2[0] != 8
