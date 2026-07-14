"""Offline tests for cleanup.run_scoped — the per-run leftover reaper
(owner 2026-07-09: filestorage 교차리전 절차 + 'vpc 등 반드시 삭제'를 테스트
전체 완료 후 정리작업에 편입)."""
from __future__ import annotations

import json

import cleanup.run_scoped as rs


class _Resp:
    def __init__(self, status, body=None):
        self.status, self.body = status, body or {}


class _FakeClient:
    """Records calls; scripted responses by (method, bare-path prefix)."""

    def __init__(self, script=None):
        self.calls, self.script = [], script or {}

    def _hit(self, method, path):
        self.calls.append((method, path))
        for (m, prefix), resp in self.script.items():
            if m == method and path.split("?")[0].startswith(prefix):
                if isinstance(resp, list):          # consume sequenced responses
                    return resp.pop(0) if len(resp) > 1 else resp[0]
                return resp
        return _Resp(200, {})

    def get(self, path, **kw):    return self._hit("GET", path)
    def delete(self, path, **kw): return self._hit("DELETE", path)
    def put(self, path, **kw):    return self._hit("PUT", path)


def _events(tmp_path, rows):
    p = tmp_path / "events.jsonl"
    p.write_text("\n".join(json.dumps(r) for r in rows))
    return p


def test_leftovers_are_tracked_minus_deleted(tmp_path):
    p = _events(tmp_path, [
        {"kind": "resource-tracked", "service": "vpc", "path": "/v1/vpcs/aaa", "lifecycle": "x"},
        {"kind": "resource-tracked", "service": "vpc", "path": "/v1/subnets/bbb", "lifecycle": "x"},
        {"kind": "resource-deleted", "service": "vpc", "path": "/v1/subnets/bbb"},
    ])
    left = rs._leftovers_from_events(p)
    assert [(l["service"], l["path"]) for l in left] == [("vpc", "/v1/vpcs/aaa")]


def test_reap_orders_children_before_vpc_and_uses_409_hint(tmp_path, monkeypatch):
    p = _events(tmp_path, [
        {"kind": "resource-tracked", "service": "vpc", "path": "/v1/vpcs/aaa", "lifecycle": "x"},
        {"kind": "resource-tracked", "service": "vpc",
         "path": "/v1/vpc-endpoints/eee", "lifecycle": "x"},
    ])
    vpc409 = _Resp(409, {"errors": [{"related_resources": [
        "srn:e::acct:kr-west1::vpc:subnet/" + "c" * 32]}]})
    cli = _FakeClient({
        ("GET", "/v1/"): _Resp(200, {}),
        ("DELETE", "/v1/vpcs/aaa"): [vpc409, _Resp(202)],
        ("DELETE", "/v1/"): _Resp(202),
    })
    monkeypatch.setattr(rs.core, "ApiClient", lambda *a, **k: cli)
    monkeypatch.setattr(rs.r, "_wait_gone", lambda *a, **k: True)
    monkeypatch.setattr(rs.r, "_wait_all_gone", lambda *a, **k: True)
    monkeypatch.setattr(rs.time, "sleep", lambda s: None)
    issued = rs.reap_run_leftovers(p, log=lambda m: None)
    deletes = [c for c in cli.calls if c[0] == "DELETE"]
    # endpoint reaped BEFORE the vpc; hidden-subnet holder deleted from the 409 hint
    assert deletes[0][1].startswith("/v1/vpc-endpoints/eee")
    assert any(d[1] == "/v1/subnets/" + "c" * 32 for d in deletes)
    assert deletes[-1][1] == "/v1/vpcs/aaa" and issued >= 2


def test_reap_runs_fs_replication_procedure_for_volumes(tmp_path, monkeypatch):
    p = _events(tmp_path, [
        {"kind": "resource-tracked", "service": "filestorage",
         "path": "/v1/volumes/6ed3a8be", "lifecycle": "fs"},
    ])
    cli = _FakeClient({("DELETE", "/v1/"): _Resp(202)})
    monkeypatch.setattr(rs.core, "ApiClient", lambda *a, **k: cli)
    called = []
    monkeypatch.setattr(rs.r, "_teardown_filestorage_replication",
                        lambda c, vid: called.append(("rep", vid)) or True)
    monkeypatch.setattr(rs.r, "_reap_filestorage_snapshots",
                        lambda c, vid: called.append(("snap", vid)))
    monkeypatch.setattr(rs.r, "_wait_gone", lambda *a, **k: True)
    monkeypatch.setattr(rs.r, "_wait_all_gone", lambda *a, **k: True)
    monkeypatch.setattr(rs.time, "sleep", lambda s: None)
    rs.reap_run_leftovers(p, log=lambda m: None)
    assert ("rep", "6ed3a8be") in called and ("snap", "6ed3a8be") in called


def test_reap_forces_gates_on_even_when_host_env_is_gated_off(tmp_path, monkeypatch):
    """콘솔 서버가 게이트 env 없이 떠 있어도 리퍼의 DELETE는 차단되면 안 된다
    (run-0099 실측 버그: 'DELETE blocked' 후 TGW/VPC 잔존)."""
    import dataclasses

    p = _events(tmp_path, [
        {"kind": "resource-tracked", "service": "vpc", "path": "/v1/vpcs/aaa", "lifecycle": "x"},
    ])
    gated_off = dataclasses.replace(
        rs.core.settings, allow_mutations=False, allow_destructive=False)
    monkeypatch.setattr(rs.core, "settings", gated_off)
    cfgs = []
    cli = _FakeClient({("DELETE", "/v1/"): _Resp(202)})
    monkeypatch.setattr(rs.core, "ApiClient",
                        lambda cfg, *a, **k: cfgs.append(cfg) or cli)
    monkeypatch.setattr(rs.r, "_wait_gone", lambda *a, **k: True)
    monkeypatch.setattr(rs.r, "_wait_all_gone", lambda *a, **k: True)
    monkeypatch.setattr(rs.time, "sleep", lambda s: None)
    issued = rs.reap_run_leftovers(p, log=lambda m: None)
    assert issued == 1
    assert cfgs and all(c.allow_mutations and c.allow_destructive for c in cfgs)


def test_reap_skips_already_gone(tmp_path, monkeypatch):
    p = _events(tmp_path, [
        {"kind": "resource-tracked", "service": "vpc", "path": "/v1/vpcs/aaa", "lifecycle": "x"},
    ])
    cli = _FakeClient({("GET", "/v1/vpcs/aaa"): _Resp(404)})
    monkeypatch.setattr(rs.core, "ApiClient", lambda *a, **k: cli)
    assert rs.reap_run_leftovers(p, log=lambda m: None) == 0
    assert not [c for c in cli.calls if c[0] == "DELETE"]
