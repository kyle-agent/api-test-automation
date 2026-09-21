"""tools.events_to_observations — rebuild the unified store from an oplog artifact.

Keys mirror engine._record_smoke: every step under ``<lifecycle>:<step>``; writes and
GETs with explicit params also under the catalog key so per-endpoint dashboard
cells light up. GET without params -> lifecycle key only (bare reads arrive via the
probe path, which the artifact does not carry)."""
from __future__ import annotations

from tools import events_to_observations as E


def _ev(**k):
    base = {"kind": "step-end", "lifecycle": "lc", "step": "st", "method": "GET",
            "path": "/v1/vpcs", "service": "vpc", "status": 200, "category": "ok",
            "elapsed_ms": 12.5, "params": None, "req_body": None, "resp_snippet": "", "ts": 1.0}
    base.update(k)
    return base


def test_write_step_gets_lifecycle_and_catalog_keys():
    rows = E.convert([_ev(step="create-vpc", method="POST", path="/v1/vpcs", status=201)], "r1")
    keys = {r["endpoint_key"] for r in rows}
    assert "lc:create-vpc" in keys
    assert "networking/vpc/createvpc" in keys
    assert all(r["run"] == "r1" and r["source"] == "crud_probe" for r in rows)


def test_bare_get_only_lifecycle_key_but_get_with_params_is_catalog_keyed():
    bare = E.convert([_ev(step="list", method="GET", path="/v1/vpcs")], "r")
    assert [r["endpoint_key"] for r in bare] == ["lc:list"]
    withp = E.convert([_ev(step="list", method="GET", path="/v1/vpcs", params={"size": 1})], "r")
    assert "networking/vpc/listvpcs" in {r["endpoint_key"] for r in withp}


def test_fail_note_carries_response_and_non_step_events_ignored():
    rows = E.convert([_ev(step="x", method="POST", path="/v1/vpcs", status=400, category="fail",
                          resp_snippet='{"errors":[...]}'),
                      {"kind": "poll-progress", "lifecycle": "lc"},
                      {"kind": "lifecycle-end", "lifecycle": "lc", "status": "failed"}], "r")
    assert rows and all(r["note"] == '{"errors":[...]}' and r["category"] == "fail" for r in rows)
