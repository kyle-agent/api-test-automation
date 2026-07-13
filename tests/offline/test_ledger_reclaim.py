"""Offline regression for the ledger-reclaim reconciler pass (2026-07-13).

Root cause it fixes: an ABORTED run leaks resources whose create SUCCEEDED (id
captured + tracked to reports/registry/*.jsonl) but whose delete step never ran.
The tag/name sweep can't reclaim them when the LIST API won't return the id —
the exemplar is queueservice (listqueue returns only names; delete needs a
32-char id; no name→id resolver). The engine has always written a durable
create-manifest with a RESOLVED delete_path; nothing consumed it. This pass does.

Hermetic: a FakeClient records DELETEs; a tmp registry dir stands in for
reports/registry. No network.
"""
from __future__ import annotations

import json

import pytest

import cleanup.reconciler as recon


class _Resp:
    def __init__(self, status=204):
        self.status = status
        self.body = {}
        self.raw_text = ""


class FakeClient:
    def __init__(self, delete_status=None):
        # delete_status: {path: status}; default 204
        self.delete_status = delete_status or {}
        self.calls: list[tuple[str, str]] = []

    def delete(self, path, service=None, json=None, **kw):
        self.calls.append(("DELETE", path))
        return _Resp(self.delete_status.get(path, 204))


@pytest.fixture(autouse=True)
def _isolate(monkeypatch, tmp_path):
    monkeypatch.setattr(recon.time, "sleep", lambda *a, **k: None)
    # point the pass at a tmp registry dir + zero min-age so shards are eligible
    monkeypatch.setattr(recon, "_REGISTRY_DIR", tmp_path)
    monkeypatch.setattr(recon, "_LEDGER_MIN_AGE_S", 0.0)
    recon._DELETED_THIS_SWEEP.clear()
    yield


def _shard(tmp_path, name, records):
    p = tmp_path / name
    p.write_text("\n".join(json.dumps(r) for r in records) + "\n")
    return p


def test_reclaims_queue_by_recorded_id(tmp_path):
    """The core case: an orphan queue whose id lives only in the manifest is
    deleted by its recorded resolved delete_path (what listqueue can't give)."""
    _shard(tmp_path, "20260712.jsonl", [
        {"service": "queueservice",
         "delete_path": "/v1/queues/5cdd8bf1000000000000000000000000",
         "resource_id": "5cdd8bf1000000000000000000000000", "kind": "queue"},
    ])
    c = FakeClient()
    n = recon._pass_ledger_reclaim(c)
    assert n == 1
    assert ("DELETE", "/v1/queues/5cdd8bf1000000000000000000000000") in c.calls
    # every record gone → shard pruned
    assert not (tmp_path / "20260712.jsonl").exists()


def test_404_counts_as_reclaimed_and_prunes(tmp_path):
    """A recorded id already gone (404) is success, not a survivor — prune."""
    _shard(tmp_path, "old.jsonl", [
        {"service": "queueservice", "delete_path": "/v1/queues/deadbeef" + "0"*24,
         "resource_id": "deadbeef" + "0"*24, "kind": "queue"},
    ])
    c = FakeClient(delete_status={"/v1/queues/deadbeef" + "0"*24: 404})
    n = recon._pass_ledger_reclaim(c)
    assert n == 0                     # 404 is not a genuine delete
    assert len(c.calls) == 1          # but it WAS attempted
    assert not (tmp_path / "old.jsonl").exists()   # pruned (confirmed gone)


def test_skips_unresolved_placeholder(tmp_path):
    """A record whose delete_path still holds a {token} can't be addressed —
    never issue a DELETE with a literal brace, and keep the shard for audit."""
    _shard(tmp_path, "bad.jsonl", [
        {"service": "queueservice", "delete_path": "/v1/queues/{queue_id}",
         "resource_id": "", "kind": "queue"},
    ])
    c = FakeClient()
    n = recon._pass_ledger_reclaim(c)
    assert n == 0 and c.calls == []
    assert (tmp_path / "bad.jsonl").exists()       # kept (not all gone)


def test_skips_recent_shard_active_run(tmp_path, monkeypatch):
    """A shard younger than the min-age is an ACTIVE run's manifest — deleting
    its in-flight resources would trample a concurrent run (Hard Rule 4)."""
    monkeypatch.setattr(recon, "_LEDGER_MIN_AGE_S", 10_000.0)  # everything "recent"
    _shard(tmp_path, "active.jsonl", [
        {"service": "queueservice", "delete_path": "/v1/queues/" + "a"*32,
         "resource_id": "a"*32, "kind": "queue"},
    ])
    c = FakeClient()
    n = recon._pass_ledger_reclaim(c)
    assert n == 0 and c.calls == []
    assert (tmp_path / "active.jsonl").exists()     # untouched


def test_children_before_parents_within_shard(tmp_path):
    """Records are deleted newest-first (create order is parent→child), so a
    subnet is reaped before its VPC — avoids a 409 on the parent."""
    _shard(tmp_path, "vpc.jsonl", [
        {"service": "vpc", "delete_path": "/v1/vpcs/vpc1",
         "resource_id": "vpc1", "kind": "vpc"},
        {"service": "vpc", "delete_path": "/v1/subnets/sub1",
         "resource_id": "sub1", "kind": "subnet"},
    ])
    c = FakeClient()
    recon._pass_ledger_reclaim(c)
    order = [p for _, p in c.calls]
    assert order.index("/v1/subnets/sub1") < order.index("/v1/vpcs/vpc1")


def test_retryable_status_keeps_shard(tmp_path):
    """A 409 (child still draining) is not 'gone' — keep the shard so the next
    sweep round retries; the fixed-point loop converges."""
    _shard(tmp_path, "keep.jsonl", [
        {"service": "vpc", "delete_path": "/v1/vpcs/vpc9",
         "resource_id": "vpc9", "kind": "vpc"},
    ])
    c = FakeClient(delete_status={"/v1/vpcs/vpc9": 409})
    n = recon._pass_ledger_reclaim(c)
    assert n == 0
    assert (tmp_path / "keep.jsonl").exists()
