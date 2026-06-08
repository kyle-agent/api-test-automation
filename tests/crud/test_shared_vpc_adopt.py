"""Offline unit tests for shared-VPC adoption (no live gateway).

Covers the durable VPC-reuse fix (knowledge/vpc-scheduling-strategy.md):
  * provision_shared_vpc creates ONE vpc and tears it down;
  * a lifecycle's {"adopt": "vpc"} steps reuse the shared id instead of
    creating/deleting their own;
  * with NO shared vpc the same lifecycle falls back to self-create (so the
    mechanism can never regress CRUD).
"""
from __future__ import annotations

import types

import pytest

from core.http_client import Response
from regression.scenarios import engine


class FakeClient:
    """Records calls; returns canned responses by (METHOD, path-startswith)."""

    def __init__(self, routes: dict):
        self.routes = routes
        self.calls: list[tuple[str, str]] = []

    def request(self, method, path, *, json=None, service=None, params=None):
        self.calls.append((method.upper(), path))
        for (m, pfx), resp in self.routes.items():
            if method.upper() == m and path.startswith(pfx):
                return resp
        return Response(200, 1.0, {}, {}, "{}")

    def methods_on(self, pfx):
        return [m for (m, p) in self.calls if p.startswith(pfx)]


def _cfg(**over):
    base = dict(allow_mutations=True, allow_destructive=True, run_heavy=True,
                region="kr-west1")
    base.update(over)
    return types.SimpleNamespace(**base)


def _r(status, body):
    return Response(status, 1.0, {}, body, "{}")


@pytest.fixture(autouse=True)
def _no_disk(monkeypatch):
    # keep the unit test hermetic: don't write the results store
    monkeypatch.setattr(engine, "_record_smoke", lambda *a, **k: None)


def _heavy_lc():
    """Minimal heavy lifecycle shaped like the real VPC ones: adopt vpc, make a
    subnet under it, then delete subnet + (adopt) vpc."""
    return {
        "id": "test-heavy", "service": "vpc", "heavy": True, "enabled": True,
        "steps": [
            {"name": "create-vpc", "method": "POST", "path": "/v1/vpcs",
             "adopt": "vpc", "json": {"name": "x", "cidr": "10.99.0.0/20", "tags": []},
             "capture": {"vpc_id": "$.vpc.id"}, "expect_status": [200, 201, 202],
             "cleanup": {"method": "DELETE", "path": "/v1/vpcs/{vpc_id}", "service": "vpc"}},
            {"name": "create-subnet", "method": "POST", "path": "/v1/subnets",
             "json": {"cidr": "10.124.1.0/24", "vpc_id": "{vpc_id}", "tags": []},
             "capture": {"subnet_id": "$.subnet.id"}, "expect_status": [200, 201, 202],
             "cleanup": {"method": "DELETE", "path": "/v1/subnets/{subnet_id}", "service": "vpc"}},
            {"name": "delete-subnet", "method": "DELETE", "path": "/v1/subnets/{subnet_id}",
             "destructive": True, "expect_status": [200, 202, 204]},
            {"name": "delete-vpc", "method": "DELETE", "path": "/v1/vpcs/{vpc_id}",
             "adopt": "vpc", "destructive": True, "expect_status": [200, 202, 204]},
        ],
    }


def test_adopt_reuses_shared_vpc_and_skips_create_delete():
    client = FakeClient({
        ("POST", "/v1/subnets"): _r(201, {"subnet": {"id": "sub-1"}}),
    })
    res = engine.run_lifecycle(_heavy_lc(), client, _cfg(),
                               shared_ctx={"shared_vpc_id": "shared-9"})
    assert res["status"] == "passed", res
    # the shared VPC is neither created nor deleted by the lifecycle
    assert client.methods_on("/v1/vpcs") == [], client.calls
    # the subnet IS created — and under the shared vpc id
    subnet_posts = [c for c in client.calls if c == ("POST", "/v1/subnets")]
    assert subnet_posts, client.calls
    assert ("DELETE", "/v1/subnets/sub-1") in client.calls


def test_no_shared_vpc_falls_back_to_self_create():
    client = FakeClient({
        ("POST", "/v1/vpcs"): _r(201, {"vpc": {"id": "own-1"}}),
        ("POST", "/v1/subnets"): _r(201, {"subnet": {"id": "sub-2"}}),
    })
    res = engine.run_lifecycle(_heavy_lc(), client, _cfg(), shared_ctx={})
    assert res["status"] == "passed", res
    # with no shared vpc the lifecycle creates AND deletes its own
    assert ("POST", "/v1/vpcs") in client.calls
    assert ("DELETE", "/v1/vpcs/own-1") in client.calls


def test_provision_shared_vpc_creates_and_tears_down():
    client = FakeClient({
        ("POST", "/v1/vpcs"): _r(201, {"vpc": {"id": "shared-1", "state": "ACTIVE"}}),
        ("GET", "/v1/vpcs/"): _r(200, {"vpc": {"id": "shared-1", "state": "ACTIVE"}}),
    })
    shared, teardown = engine.provision_shared_vpc(client, _cfg())
    assert shared == {"shared_vpc_id": "shared-1"}
    assert ("POST", "/v1/vpcs") in client.calls
    teardown()
    assert ("DELETE", "/v1/vpcs/shared-1") in client.calls


def test_provision_shared_vpc_noop_without_mutations():
    client = FakeClient({})
    shared, teardown = engine.provision_shared_vpc(client, _cfg(allow_mutations=False))
    assert shared == {}
    teardown()  # must be safe
    assert client.calls == []
