"""Offline tests for core/events_contract.py (S1a — the live-event seam).

Pure consumer-side adapter: no engine telemetry is touched, no cloud, no files.
Verifies that the two channels (console_events fine/local, oplog coarse/cloud)
both normalize to ONE canonical vocabulary, and that the stream folds into the
per-lifecycle live state the graph overlay paints.
"""
from __future__ import annotations

from core import events_contract as ec


# --- to_epoch ---------------------------------------------------------------
def test_to_epoch_float_passthrough():
    assert ec.to_epoch(1.75e9) == 1.75e9
    assert ec.to_epoch(1700000000) == 1700000000.0


def test_to_epoch_iso_string():
    # 1970-01-01T00:00:00Z is epoch 0; a known later instant round-trips via UTC.
    assert ec.to_epoch("1970-01-01T00:00:00Z") == 0.0
    assert ec.to_epoch("2021-01-01T00:00:00Z") == 1609459200.0


def test_to_epoch_junk_is_zero():
    assert ec.to_epoch("not-a-date") == 0.0
    assert ec.to_epoch(None) == 0.0
    assert ec.to_epoch(True) == 0.0          # bool must not be read as epoch 1


# --- normalize_console (already canonical) ----------------------------------
def test_normalize_console_is_identity_with_ts_coerced():
    line = {"ts": 1.7e9, "kind": "step-start", "lifecycle": "nw-vpc",
            "step": "create-vpc", "method": "POST", "path": "/v1/vpcs"}
    out = ec.normalize_console(line)
    assert out["kind"] == "step-start"
    assert out["lifecycle"] == "nw-vpc" and out["method"] == "POST"
    assert isinstance(out["ts"], float)
    # input is not mutated
    assert line["ts"] == 1.7e9


def test_normalize_console_guarantees_kind():
    assert ec.normalize_console({"ts": 1.0})["kind"] == ""
    assert ec.normalize_console({})["ts"] == 0.0


# --- normalize_oplog: milestone ---------------------------------------------
def test_normalize_oplog_milestone():
    payload = {"kind": "milestone", "run_id": "r1", "ts": "2021-01-01T00:00:00Z",
               "stage": "run-start", "status": "ok", "detail": "go", "job": "A"}
    out = ec.normalize_oplog(payload)
    assert len(out) == 1
    ev = out[0]
    assert ev["kind"] == ec.MILESTONE
    assert ev["stage"] == "run-start" and ev["status"] == "ok"
    assert ev["run_id"] == "r1" and ev["job"] == "A"
    assert ev["ts"] == 1609459200.0          # ISO -> epoch


# --- normalize_oplog: resources (the overload fixes) ------------------------
def test_normalize_oplog_resources_renames_kind_and_maps_action():
    payload = {"kind": "resources", "run_id": "r2", "events": [
        {"ts": "2021-01-01T00:00:00Z", "t": 1609459200000, "action": "create",
         "kind": "vpcs", "service": "networking/vpc", "res_id": "vpc-1",
         "lifecycle": "nw-vpc", "status": "202"},
        {"ts": "2021-01-01T00:01:00Z", "action": "delete", "kind": "subnets",
         "res_id": "sub-9", "lifecycle": "nw-vpc"},
    ]}
    out = ec.normalize_oplog(payload)
    assert len(out) == 2
    create, delete = out
    # the overloaded `kind` (resource TYPE) is moved to resource_kind...
    assert create["resource_kind"] == "vpcs"
    # ...and the canonical event kind comes from the verb
    assert create["kind"] == ec.RESOURCE_TRACKED and create["action"] == "create"
    assert delete["kind"] == ec.RESOURCE_DELETED and delete["resource_kind"] == "subnets"
    assert create["res_id"] == "vpc-1" and create["run_id"] == "r2"
    # ISO ts wins; ms `t` must never be read as seconds
    assert create["ts"] == 1609459200.0


def test_normalize_oplog_resource_falls_back_to_ms_t():
    payload = {"kind": "resources", "events": [
        {"t": 1609459200000, "action": "create", "kind": "vpcs"}]}
    out = ec.normalize_oplog(payload)
    assert out[0]["ts"] == 1609459200.0      # t(ms)/1000, not 1.6e12


def test_normalize_oplog_ignores_unknown_and_bad_input():
    assert ec.normalize_oplog({"kind": "weird"}) == []
    assert ec.normalize_oplog("nope") == []
    assert ec.normalize_oplog({"kind": "resources", "events": ["junk", 3]}) == []


# --- normalize() dispatch ----------------------------------------------------
def test_normalize_dispatch():
    assert ec.normalize({"kind": "step-end", "ts": 1.0}, "console")[0]["kind"] == "step-end"
    assert ec.normalize({"kind": "milestone", "ts": 1.0}, "oplog")[0]["kind"] == "milestone"
    assert ec.normalize({}, "bogus") == []


# --- lifecycle_states reducer (the graph-overlay input) ---------------------
def test_lifecycle_states_running_then_done():
    events = [
        {"kind": "lifecycle-start", "lifecycle": "a"},
        {"kind": "step-end", "lifecycle": "a", "category": "ok", "status": 202},
        {"kind": "lifecycle-end", "lifecycle": "a", "status": "passed"},
        {"kind": "lifecycle-start", "lifecycle": "b"},   # still running, no end
    ]
    st = ec.lifecycle_states(events)
    assert st == {"a": ec.DONE, "b": ec.RUNNING}


def test_lifecycle_states_fail_is_sticky():
    events = [
        {"kind": "lifecycle-start", "lifecycle": "a"},
        {"kind": "step-end", "lifecycle": "a", "category": "error", "status": 500},
        {"kind": "step-end", "lifecycle": "a", "category": "ok", "status": 200},
        {"kind": "lifecycle-end", "lifecycle": "a", "status": "passed"},
    ]
    # one error sticks even though later steps + end say passed
    assert ec.lifecycle_states(events)["a"] == ec.FAIL


def test_lifecycle_states_http_4xx_is_fail():
    # no category to trust → fall back to the raw status
    events = [
        {"kind": "lifecycle-start", "lifecycle": "a"},
        {"kind": "step-end", "lifecycle": "a", "category": "", "status": 404},
    ]
    assert ec.lifecycle_states(events)["a"] == ec.FAIL


def test_lifecycle_states_soft_non_2xx_is_not_fail():
    # regression (found by a LIVE run): a classified "soft" 404 — e.g. the
    # GET-after-delete that confirms teardown — must NOT fail the lifecycle. The
    # engine's category is authoritative over the raw HTTP status.
    events = [
        {"kind": "lifecycle-start", "lifecycle": "a"},
        {"kind": "step-end", "lifecycle": "a", "category": "ok", "status": 204},
        {"kind": "step-end", "lifecycle": "a", "category": "soft", "status": 404},
        {"kind": "lifecycle-end", "lifecycle": "a", "status": "passed"},
    ]
    assert ec.lifecycle_states(events)["a"] == ec.DONE


def test_lifecycle_states_end_non_pass_is_fail():
    events = [
        {"kind": "lifecycle-start", "lifecycle": "a"},
        {"kind": "lifecycle-end", "lifecycle": "a", "status": "errored"},
    ]
    assert ec.lifecycle_states(events)["a"] == ec.FAIL


def test_lifecycle_states_unseen_is_absent():
    # events with no lifecycle id are ignored; unseen lifecycles stay absent
    st = ec.lifecycle_states([{"kind": "milestone", "stage": "run-start"}])
    assert st == {}


def test_oplog_then_states_end_to_end():
    # a cloud resource batch normalizes and folds without error (coarse channel
    # carries no lifecycle-start/end, so resource events alone leave state empty)
    payload = {"kind": "resources", "events": [
        {"action": "create", "kind": "vpcs", "lifecycle": "nw-vpc", "t": 1000}]}
    norm = ec.normalize_oplog(payload)
    assert norm and norm[0]["kind"] == ec.RESOURCE_TRACKED
    assert ec.lifecycle_states(norm) == {}      # no start/step-end/end -> no state
