"""Offline regression tests for the teardown-cleanup ladder (run-2b leak root).

run-2b field case: a mid-chain hard fail triggered ``_teardown()``, whose
one-shot cleanup DELETE hit 409/invalid-state on the still-EDITING resource
and the old code (a) never retried, (b) recorded 'deleted' in the oplog even
for a 4xx, and (c) emitted nothing to the console event channel — the owner
watched leftovers appear with no visible cause (LB + TGW leaks).

The fix: bounded 409/invalid-state retries, 'deleted' only on real success
(2xx or 404=already-gone), 'delete-failed' + a loud print otherwise.
"""
from __future__ import annotations

import types

import pytest

from core.http_client import Response
from regression.scenarios import engine
from tests.offline.test_command_channel import FakeClient, _cfg, _r


class SeqClient(FakeClient):
    """FakeClient whose routes may map to a LIST of responses (consumed in
    order; the last one repeats once exhausted)."""

    def request(self, method, path, *, json=None, service=None, params=None, headers=None):
        self.calls.append((method.upper(), path))
        for (m, pfx), resp in self.routes.items():
            if method.upper() == m and path.startswith(pfx):
                if isinstance(resp, list):
                    return resp.pop(0) if len(resp) > 1 else resp[0]
                return resp
        return Response(200, 1.0, {}, {}, "{}")


def _leaky_lc():
    """create (registers cleanup) then a hard-failing step -> teardown path."""
    return {
        "id": "cleanup-ladder-test", "service": "loadbalancer", "enabled": True,
        "steps": [
            {"name": "create-lb", "method": "POST", "path": "/v1/loadbalancers",
             "json": {"name": "regrlb{unique}"},
             "expect_status": [201], "capture": {"lb_id": "$.loadbalancer.id"},
             "cleanup": {"method": "DELETE", "path": "/v1/loadbalancers/{lb_id}",
                          "service": "loadbalancer"}},
            {"name": "boom", "method": "POST", "path": "/v1/lb-members",
             "expect_status": [201]},  # returns 403 -> hard fail -> teardown
        ],
    }


def test_cleanup_retries_409_until_deleted(monkeypatch):
    monkeypatch.setattr(engine, "_commands", None)
    slept = []
    monkeypatch.setattr(engine.time, "sleep", lambda s: slept.append(s))
    client = SeqClient({
        ("POST", "/v1/loadbalancers"): _r(201, {"loadbalancer": {"id": "lb1"}}),
        ("POST", "/v1/lb-members"): _r(403, {"errors": []}),
        ("DELETE", "/v1/loadbalancers/lb1"): [
            _r(409, {"errors": []}), _r(409, {"errors": []}), _r(204, None)],
    })
    with pytest.raises(AssertionError):   # _finish re-raises for pytest entrypoints
        engine.run_lifecycle(_leaky_lc(), client, _cfg())
    dels = [c for c in client.calls if c[0] == "DELETE"]
    assert len(dels) == 3, f"expected a 409 retry ladder, saw {dels}"
    assert slept, "ladder must space its retries"


def test_cleanup_4xx_is_not_recorded_as_deleted(monkeypatch):
    """A cleanup DELETE stuck on 400 invalid-state must surface delete-failed
    (never 'deleted' — the old behaviour that hid the run-2b leaks)."""
    monkeypatch.setattr(engine, "_commands", None)
    monkeypatch.setattr(engine.time, "sleep", lambda s: None)
    emitted = []
    fake_oplog = types.SimpleNamespace(
        emit_resource=lambda kind, **kw: emitted.append((kind, kw)),
        flush_resources=lambda: None)
    monkeypatch.setattr(engine, "_oplog", fake_oplog)
    client = SeqClient({
        ("POST", "/v1/loadbalancers"): _r(201, {"loadbalancer": {"id": "lb1"}}),
        ("POST", "/v1/lb-members"): _r(403, {"errors": []}),
        ("DELETE", "/v1/loadbalancers/lb1"):
            Response(400, 1.0, {}, {"errors": []},
                     '{"title":"InvalidDeletableStateError","detail":"state is not deletable"}'),
    })
    with pytest.raises(AssertionError):
        engine.run_lifecycle(_leaky_lc(), client, _cfg())
    kinds = [k for k, kw in emitted if kw.get("path", "").startswith("/v1/loadbalancers/")]
    assert "delete-failed" in kinds, emitted
    assert "deleted" not in kinds, "4xx cleanup must not be recorded as deleted"


def test_cleanup_404_counts_as_already_gone(monkeypatch):
    monkeypatch.setattr(engine, "_commands", None)
    monkeypatch.setattr(engine.time, "sleep", lambda s: None)
    emitted = []
    fake_oplog = types.SimpleNamespace(
        emit_resource=lambda kind, **kw: emitted.append((kind, kw)),
        flush_resources=lambda: None)
    monkeypatch.setattr(engine, "_oplog", fake_oplog)
    client = SeqClient({
        ("POST", "/v1/loadbalancers"): _r(201, {"loadbalancer": {"id": "lb1"}}),
        ("POST", "/v1/lb-members"): _r(403, {"errors": []}),
        ("DELETE", "/v1/loadbalancers/lb1"): _r(404, {"errors": []}),
    })
    with pytest.raises(AssertionError):
        engine.run_lifecycle(_leaky_lc(), client, _cfg())
    kinds = [k for k, kw in emitted if kw.get("path", "").startswith("/v1/loadbalancers/")]
    assert "deleted" in kinds and "delete-failed" not in kinds, emitted
    # 404 must not trigger the ladder — one call only
    assert len([c for c in client.calls if c[0] == "DELETE"]) == 1
