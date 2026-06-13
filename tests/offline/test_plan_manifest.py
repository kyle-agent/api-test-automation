"""Offline tests for the plan-manifest (M6d, ticket T4).

core.oplog.build_plan_manifest() serializes composer.plan() over every ENABLED
composed lifecycle (gen-*/bundle-*) so the ops viewer can draw the INTENDED
dependency chain. emit_plan() uploads it to runs/<id>/plan.json — best-effort,
a no-op (returns False) when the oplog is disabled (no credentials).

These run with NO network and NO S3 credentials: build over the real model +
lifecycles, assert manifest shape, assert gated lifecycles are recorded as
errors (not crashing), and assert emit_plan() no-ops to False without creds.
"""
from __future__ import annotations

from core import oplog


def test_build_manifest_shape():
    m = oplog.build_plan_manifest()
    assert isinstance(m, dict)
    assert "generated_at" in m
    assert isinstance(m["lifecycles"], list)
    assert m["lifecycles"], "expected at least one composed lifecycle"


def test_each_entry_has_order_and_peak_quota():
    m = oplog.build_plan_manifest()
    ok = [e for e in m["lifecycles"] if "error" not in e]
    assert ok, "expected at least one successfully-planned lifecycle"
    for e in ok:
        assert "id" in e
        assert isinstance(e["order"], list) and e["order"]
        assert isinstance(e["teardown"], list)
        assert isinstance(e["peak_quota"], dict)
        assert isinstance(e["dedup"], dict)
        assert isinstance(e["kinds"], dict)
        # every ordered instance maps to a kind (first /v1 segment)
        for inst in e["order"]:
            assert inst in e["kinds"]


def test_gated_lifecycle_skipped_with_error_not_crash(monkeypatch):
    """A lifecycle whose plan() raises must be recorded as {id, error}, never
    abort the whole manifest."""
    from regression.scenarios import composer

    real_plan = composer.plan
    calls = {"n": 0}

    def boom(targets, *a, **kw):
        calls["n"] += 1
        if calls["n"] == 1:
            raise composer.ComposeError("simulated gate")
        return real_plan(targets, *a, **kw)

    # build_plan_manifest imports composer locally, so patch the module object
    monkeypatch.setattr(composer, "plan", boom)
    m = oplog.build_plan_manifest()
    errored = [e for e in m["lifecycles"] if "error" in e]
    assert errored, "expected the simulated gate to produce an error entry"
    assert any("simulated gate" in e["error"] for e in errored)
    # the rest still planned fine -> manifest did not crash
    assert any("error" not in e for e in m["lifecycles"])


def test_kind_of_maps_known_endpoint():
    assert oplog._plan_kind_of("POST /v1/vpcs") == "vpcs"
    assert oplog._plan_kind_of("GET /v1/subnets/{id}") == "subnets"
    assert oplog._plan_kind_of("DELETE /v1/redis-caches/{redis_id}") == "redis-caches"
    assert oplog._plan_kind_of("") == ""


def test_emit_plan_noops_without_credentials(monkeypatch):
    """No creds -> _cfg() returns None -> emit_plan returns False, no raise."""
    for var in ("SCP_OPLOG_ACCESS_KEY", "SCP_ACCESS_KEY",
                "SCP_OPLOG_SECRET_KEY", "SCP_SECRET_KEY",
                "APITEST_PLATFORM_URL"):
        monkeypatch.delenv(var, raising=False)
    assert oplog._cfg() is None
    assert oplog.emit_plan({"generated_at": "x", "lifecycles": []}) is False


def test_recover_targets_handles_instance_suffix_and_real_digit_node():
    """Trailing -<N> is an instance suffix UNLESS the full name is a real node
    (e.g. epas-engine-version-16)."""
    model = {"iam-policy": {}, "epas-engine-version-16": {}, "vpc": {}}
    lc = {"steps": [
        {"name": "create-vpc"},
        {"name": "create-iam-policy-2"},          # -> iam-policy (instance #2)
        {"name": "create-epas-engine-version-16"},  # real node, keep full name
        {"name": "create-method"},                # sub-resource, dropped
        {"name": "verify-vpc-x"},                 # not a create step
    ]}
    got = oplog._recover_targets(lc, model)
    assert got == ["vpc", "iam-policy", "epas-engine-version-16"]
