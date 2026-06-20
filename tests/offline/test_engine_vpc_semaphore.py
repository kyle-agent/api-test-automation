"""Offline tests for the engine's cross-process VPC throttle wiring (ADR v0.5).

These prove the engine *uses* core.budgets.CrossProcessSemaphore correctly when
SCP_VPC_SEMAPHORE=true — the contract the workflow cutover will depend on:

  * a VPC self-create ACQUIRES a slot before the create (held during it) and
    RELEASES it when the lifecycle deletes the VPC via its own step (happy path),
    so a created-then-deleted VPC never leaks its slot for the whole run;
  * when the cap is already held by a peer process, the create BLOCKS and then,
    on timeout, environmentally SKIPS (never fails) — the throttle, Hard Rule 6;
  * with the flag OFF (default) the engine never consults the semaphore — today's
    per-process behaviour is unchanged, so this can't disturb the live run until
    the workflow is cut over.

No network: a fake client returns canned responses. The semaphore state is the
real file-backed one, resolved through SCP_BUDGET_SEM_DIR.
"""
from __future__ import annotations

import types

import pytest

from core.budgets import CrossProcessSemaphore
from core.http_client import Response
from regression.scenarios import engine


class FakeClient:
    """Canned responses by (METHOD, path-prefix); a route value may be a
    Response or a 0-arg callable returning one (to observe live state mid-call).
    Accepts **kwargs so it tolerates headers= etc. that the engine passes."""

    def __init__(self, routes: dict):
        self.routes = routes
        self.calls: list[tuple[str, str]] = []

    def request(self, method, path, *, json=None, service=None, params=None,
                **kwargs):
        self.calls.append((method.upper(), path))
        for (m, pfx), resp in self.routes.items():
            if method.upper() == m and path.startswith(pfx):
                return resp() if callable(resp) else resp
        return Response(200, 1.0, {}, {}, "{}")

    def has(self, method, pfx):
        return any(m == method and p.startswith(pfx) for m, p in self.calls)


def _cfg(**over):
    base = dict(allow_mutations=True, allow_destructive=True, run_heavy=True,
                region="kr-west1")
    base.update(over)
    return types.SimpleNamespace(**base)


def _r(status, body):
    return Response(status, 1.0, {}, body, "{}")


def _vpc_lc(lid="vpc-crud-test"):
    """A VPC-CRUD-shaped lifecycle: self-create a VPC (no adopt), then delete it
    via its own step — exactly the VPC_CRUD_K class the throttle targets."""
    return {
        "id": lid, "service": "vpc", "heavy": True, "enabled": True,
        "steps": [
            {"name": "create-vpc", "method": "POST", "path": "/v1/vpcs",
             "json": {"name": "x", "cidr": "10.99.0.0/20", "tags": []},
             "capture": {"vpc_id": "$.vpc.id"}, "expect_status": [200, 201, 202],
             "cleanup": {"method": "DELETE", "path": "/v1/vpcs/{vpc_id}",
                         "service": "vpc"}},
            {"name": "delete-vpc", "method": "DELETE", "path": "/v1/vpcs/{vpc_id}",
             "destructive": True, "expect_status": [200, 202, 204]},
        ],
    }


@pytest.fixture(autouse=True)
def _hermetic(monkeypatch, tmp_path):
    # never write the results store; isolate the semaphore state to this test;
    # never inherit an xdist-worker env that would route to the IB-049 skip.
    monkeypatch.setattr(engine, "_record_smoke", lambda *a, **k: None)
    monkeypatch.setenv("SCP_BUDGET_SEM_DIR", str(tmp_path / "locks"))
    monkeypatch.delenv("PYTEST_XDIST_WORKER", raising=False)


def test_create_holds_slot_then_releases_on_own_delete(monkeypatch):
    monkeypatch.setenv("SCP_VPC_SEMAPHORE", "true")
    monkeypatch.setenv("SCP_VPC_SHARED_RESERVED", "0")   # limit = full cap

    seen = {}

    def _on_create():
        # acquired just before this call -> a slot is held DURING the create.
        seen["used_during_create"] = CrossProcessSemaphore("vpc").used()
        return _r(201, {"vpc": {"id": "vpc-1"}})

    client = FakeClient({("POST", "/v1/vpcs"): _on_create})
    res = engine.run_lifecycle(_vpc_lc(), client, _cfg())

    assert res["status"] == "passed", res
    assert seen["used_during_create"] >= 1               # slot held across the create
    assert CrossProcessSemaphore("vpc").used() == 0      # freed by the own DELETE
    assert client.has("DELETE", "/v1/vpcs/")


def test_create_skips_when_cap_held_by_peer(monkeypatch):
    monkeypatch.setenv("SCP_VPC_SEMAPHORE", "true")
    monkeypatch.setenv("SCP_VPC_SHARED_RESERVED", "4")   # limit = 5 - 4 = 1
    monkeypatch.setenv("SCP_VPC_SEMAPHORE_TIMEOUT", "0.3")
    monkeypatch.setenv("SCP_VPC_SEMAPHORE_POLL", "0.05")

    peer = CrossProcessSemaphore("vpc")
    held = peer.try_acquire(limit=1)                     # this (live) process takes the only slot
    assert held is not None

    client = FakeClient({("POST", "/v1/vpcs"): _r(201, {"vpc": {"id": "vpc-x"}})})
    res = engine.run_lifecycle(_vpc_lc(), client, _cfg())

    assert res["status"] == "skipped", res
    assert "semaphore" in res["reason"].lower()
    assert not client.has("POST", "/v1/vpcs")            # throttled BEFORE the create

    peer.release(held)                                   # free the cap
    client2 = FakeClient({("POST", "/v1/vpcs"): _r(201, {"vpc": {"id": "vpc-y"}})})
    res2 = engine.run_lifecycle(_vpc_lc(), client2, _cfg())
    assert res2["status"] == "passed", res2              # now it runs


def test_disabled_by_default_ignores_semaphore(monkeypatch):
    monkeypatch.delenv("SCP_VPC_SEMAPHORE", raising=False)

    # A peer "holds" the only slot — but with the flag OFF the engine must not
    # consult the semaphore at all, so the create proceeds exactly as today.
    peer = CrossProcessSemaphore("vpc")
    assert peer.try_acquire(limit=1) is not None

    client = FakeClient({("POST", "/v1/vpcs"): _r(201, {"vpc": {"id": "vpc-z"}})})
    res = engine.run_lifecycle(_vpc_lc(), client, _cfg())

    assert res["status"] == "passed", res
    assert client.has("POST", "/v1/vpcs")


# -- multi-VPC precision (the Medium fix: id-keyed release) ------------------

def _two_vpc_lc(delete_both=True):
    """A peering-shaped lifecycle: self-create TWO VPCs (a, b) then delete a (and
    optionally b) via own steps — exercises id-keyed slot release."""
    steps = [
        {"name": "create-vpc-a", "method": "POST", "path": "/v1/vpcs",
         "json": {"name": "a", "cidr": "10.1.0.0/20", "tags": []},
         "capture": {"vpc_a": "$.vpc.id"}, "expect_status": [201],
         "cleanup": {"method": "DELETE", "path": "/v1/vpcs/{vpc_a}", "service": "vpc"}},
        {"name": "create-vpc-b", "method": "POST", "path": "/v1/vpcs",
         "json": {"name": "b", "cidr": "10.2.0.0/20", "tags": []},
         "capture": {"vpc_b": "$.vpc.id"}, "expect_status": [201],
         "cleanup": {"method": "DELETE", "path": "/v1/vpcs/{vpc_b}", "service": "vpc"}},
        {"name": "delete-vpc-a", "method": "DELETE", "path": "/v1/vpcs/{vpc_a}",
         "destructive": True, "expect_status": [200, 202, 204]},
    ]
    if delete_both:
        steps.append({"name": "delete-vpc-b", "method": "DELETE",
                      "path": "/v1/vpcs/{vpc_b}", "destructive": True,
                      "expect_status": [200, 202, 204]})
    return {"id": "two-vpc", "service": "vpc", "heavy": True, "enabled": True,
            "steps": steps}


def _seq_vpc_client(peak_box):
    """POST /v1/vpcs returns vpc-a, vpc-b, ...; each call records the live slot
    count so we can assert both VPCs are held concurrently at the peak."""
    ids = iter(["vpc-a", "vpc-b", "vpc-c"])

    def _post():
        peak_box[0] = max(peak_box[0], CrossProcessSemaphore("vpc").used())
        return _r(201, {"vpc": {"id": next(ids)}})

    return FakeClient({("POST", "/v1/vpcs"): _post})


def test_two_vpcs_both_held_then_both_freed(monkeypatch):
    monkeypatch.setenv("SCP_VPC_SEMAPHORE", "true")
    monkeypatch.setenv("SCP_VPC_SHARED_RESERVED", "0")   # limit = full cap (5)

    peak = [0]
    client = _seq_vpc_client(peak)
    res = engine.run_lifecycle(_two_vpc_lc(delete_both=True), client, _cfg())

    assert res["status"] == "passed", res
    assert peak[0] == 2, "both VPCs must hold a slot concurrently"
    assert CrossProcessSemaphore("vpc").used() == 0   # both freed by their own DELETEs


def test_partial_delete_frees_only_the_deleted_vpc(monkeypatch):
    # Create two VPCs, own-delete ONLY vpc-a, then succeed. The id-keyed release
    # frees exactly vpc-a's slot; vpc-b (still live) keeps its slot — mirroring
    # reality, and proving release is per-id, not pop-an-arbitrary-token.
    monkeypatch.setenv("SCP_VPC_SEMAPHORE", "true")
    monkeypatch.setenv("SCP_VPC_SHARED_RESERVED", "0")

    peak = [0]
    client = _seq_vpc_client(peak)
    res = engine.run_lifecycle(_two_vpc_lc(delete_both=False), client, _cfg())

    assert res["status"] == "passed", res
    assert peak[0] == 2
    assert CrossProcessSemaphore("vpc").used() == 1   # vpc-a freed; vpc-b still held


def test_pending_slot_freed_when_create_fails_capture(monkeypatch):
    # A VPC create that returns an expected status but whose capture yields None
    # raises out of the step (not via a handled branch) -> the slot was acquired
    # but never bound to an id. run_lifecycle re-raises a genuine assert (after
    # best-effort teardown), but the lifecycle-exit invariant must STILL free the
    # pending slot before it does — no run-wide leak.
    monkeypatch.setenv("SCP_VPC_SEMAPHORE", "true")
    monkeypatch.setenv("SCP_VPC_SHARED_RESERVED", "0")

    # 201 OK, but body lacks $.vpc.id so capture {"vpc_id": ...} -> None.
    client = FakeClient({("POST", "/v1/vpcs"): _r(201, {"nope": {}})})
    with pytest.raises(AssertionError, match="could not capture"):
        engine.run_lifecycle(_vpc_lc(), client, _cfg())

    assert CrossProcessSemaphore("vpc").used() == 0   # pending slot not leaked
