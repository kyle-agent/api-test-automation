"""shared_infra --teardown 순서 보증 (2026-07-12 관측 수리).

종전: subnet DELETE 발행 직후 곧바로 VPC DELETE → subnet이 아직 DELETING이라
409 → 공유 VPC만 잔존. 두 런 연속 같은 패턴으로 남았고, 잔존 VPC가 다음 런의
admission 큐를 막았다. 이제 teardown은 (1) subnet 삭제 상태코드를 확인하고
(2) subnet들이 실제로 사라질 때까지(404) 기다린 뒤 (3) VPC를 409 재시도
사다리로 지운다.
"""
from __future__ import annotations

import types

import pytest

import regression.scenarios.shared_infra as si
from regression.scenarios import engine


class _Resp:
    def __init__(self, status):
        self.status = status
        self.ok = 200 <= status < 300
        self.body = {}


class FakeClient:
    """subnet GET: gone_after회 조회까지는 200, 이후 404. VPC DELETE:
    subnet이 모두 사라지기 전엔 409, 후엔 204."""

    def __init__(self, gone_after=2, vpc_409_first=1):
        self.calls: list[tuple[str, str]] = []
        self._sub_reads = {}
        self._gone_after = gone_after
        self._vpc_409_left = vpc_409_first
        self.subnets_gone_when_vpc_deleted = None

    def _sub_gone(self, sid):
        return self._sub_reads.get(sid, 0) >= self._gone_after

    def request(self, method, path, service=None, **kw):
        self.calls.append((method, path))
        if method == "DELETE" and path.startswith(engine._SUBNET_CREATE_PATH):
            return _Resp(202)
        if method == "DELETE" and path.startswith(engine._VPC_CREATE_PATH):
            all_gone = all(self._sub_gone(s) for s in self._sub_reads) \
                if self._sub_reads else False
            if self.subnets_gone_when_vpc_deleted is None:
                self.subnets_gone_when_vpc_deleted = all_gone
            if self._vpc_409_left > 0:
                self._vpc_409_left -= 1
                return _Resp(409)
            return _Resp(204)
        return _Resp(200)

    def get(self, path, service=None, **kw):
        self.calls.append(("GET", path))
        sid = path.rsplit("/", 1)[-1]
        self._sub_reads[sid] = self._sub_reads.get(sid, 0) + 1
        return _Resp(404 if self._sub_gone(sid) else 200)


@pytest.fixture()
def _fast(monkeypatch):
    monkeypatch.setattr(si.time, "sleep", lambda s: None)


def _run_teardown(monkeypatch, client, vpc="vpcX", sub="subA", db="subB"):
    cfg = types.SimpleNamespace(allow_destructive=True)
    monkeypatch.setattr(si, "_build_client", lambda: (cfg, client))
    monkeypatch.setenv(engine._ENV_SHARED_VPC, vpc)
    monkeypatch.setenv(engine._ENV_SHARED_SUBNET, sub)
    monkeypatch.setenv(engine._ENV_SHARED_DB_SUBNET, db)
    assert si.teardown() == 0


def test_vpc_delete_waits_for_subnets_gone(monkeypatch, _fast):
    c = FakeClient(gone_after=2, vpc_409_first=0)
    _run_teardown(monkeypatch, c)
    # VPC DELETE는 subnet 2개가 모두 404가 된 뒤에만 발행됐다
    assert c.subnets_gone_when_vpc_deleted is True
    deletes = [p for m, p in c.calls if m == "DELETE"]
    assert deletes[-1].startswith(engine._VPC_CREATE_PATH)
    assert len([p for p in deletes if p.startswith(engine._SUBNET_CREATE_PATH)]) == 2


def test_vpc_409_retried_until_gone(monkeypatch, _fast):
    c = FakeClient(gone_after=1, vpc_409_first=2)
    _run_teardown(monkeypatch, c)
    vpc_deletes = [p for m, p in c.calls
                   if m == "DELETE" and p.startswith(engine._VPC_CREATE_PATH)]
    assert len(vpc_deletes) == 3  # 409, 409, 204


def test_vpc_only_env_still_deletes_vpc(monkeypatch, _fast):
    c = FakeClient(gone_after=1, vpc_409_first=0)
    cfg = types.SimpleNamespace(allow_destructive=True)
    monkeypatch.setattr(si, "_build_client", lambda: (cfg, c))
    monkeypatch.setenv(engine._ENV_SHARED_VPC, "vpcX")
    monkeypatch.delenv(engine._ENV_SHARED_SUBNET, raising=False)
    monkeypatch.delenv(engine._ENV_SHARED_DB_SUBNET, raising=False)
    assert si.teardown() == 0
    assert ("DELETE", f"{engine._VPC_CREATE_PATH}/vpcX") in c.calls
