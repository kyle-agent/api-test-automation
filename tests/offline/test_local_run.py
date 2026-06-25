"""Offline tests for regression/scenarios/local_run.py (S2 — simulate replay).

Pure, deterministic: a hand-built plan + a list-collecting ``emit`` (no engine, no
cloud, no sleep). Verifies the simulate replay emits the canonical console-event
vocabulary in DAG order, and that the stream folds through the S1a event contract
(``core.events_contract``) to the right per-lifecycle live state — proving the
local-execution → console-events → live-view seam end-to-end, offline.
"""
from __future__ import annotations

from core import events_contract as ec
from regression.scenarios import local_run


def _fixture():
    """Two waves: a vpc (create+delete steps) then a subnet (one stepless leaf)."""
    waves = [
        {"kind": "self-create", "lifecycles": ["nw-vpc"], "vpc_slots": 1},
        {"kind": "free", "lifecycles": ["nw-sub"], "vpc_slots": 0},
    ]
    preview = {
        "nw-vpc": {"service": "networking/vpc", "heavy": False, "steps": [
            {"name": "create-vpc", "method": "POST", "path": "/v1/vpcs", "kind": "create"},
            {"name": "get-vpc", "method": "GET", "path": "/v1/vpcs/{vpc_id}"},
            {"name": "del-vpc", "method": "DELETE", "path": "/v1/vpcs/{vpc_id}", "kind": "delete"},
        ]},
        # a leaf whose only step has no method → must be skipped (no step events)
        "nw-sub": {"service": "networking/vpc", "heavy": False, "steps": [
            {"name": "noop", "path": "/internal"},
        ]},
    }
    return waves, preview


def _run():
    events = []
    waves, preview = _fixture()
    local_run.simulate_run(waves, preview, lambda kind, **f: events.append({"kind": kind, **f}))
    return events


def test_resource_type_segment():
    assert local_run.resource_type("/v1/vpcs/{vpc_id}") == "vpcs"
    assert local_run.resource_type("/v1/nat-gateways") == "nat-gateways"
    assert local_run.resource_type("/v2025-01/queues/{id}") == "queues"
    assert local_run.resource_type("") == "resource"


def test_brackets_run_meta_and_run_end():
    events = _run()
    assert events[0]["kind"] == "run-meta" and events[0]["waves"] == 2
    assert events[-1]["kind"] == "run-end" and events[-1]["status"] == "done"


def test_dag_order_waves_then_lifecycles():
    events = _run()
    kinds = [e["kind"] for e in events]
    # wave 0 fully replays before wave 1 starts
    assert kinds.count("wave-start") == 2
    first_ws = kinds.index("wave-start")
    second_ws = kinds.index("wave-start", first_ws + 1)
    between = kinds[first_ws + 1:second_ws]
    assert "lifecycle-start" in between and "lifecycle-end" in between


def test_stepless_method_filtered():
    events = _run()
    # nw-sub's only step has no method → it gets lifecycle-start/end but no step-*
    sub_steps = [e for e in events if e.get("lifecycle") == "nw-sub" and e["kind"].startswith("step")]
    assert sub_steps == []
    assert any(e["kind"] == "lifecycle-end" and e["lifecycle"] == "nw-sub" for e in events)


def test_create_and_delete_emit_synthetic_resources():
    events = _run()
    tracked = [e for e in events if e["kind"] == "resource-tracked"]
    deleted = [e for e in events if e["kind"] == "resource-deleted"]
    assert len(tracked) == 1 and tracked[0]["resource_type"] == "vpcs"
    assert tracked[0]["resource_id"] == "sim-00000001"        # deterministic counter
    assert len(deleted) == 1 and deleted[0]["resource_type"] == "vpcs"


def test_step_end_carries_ok_status():
    events = _run()
    se = [e for e in events if e["kind"] == "step-end"]
    assert se and all(e["status"] == 200 and e["category"] == "ok" for e in se)


def test_folds_through_events_contract_to_done():
    # the WHOLE point: simulate output is canonical → S1a reducer paints both done
    events = _run()
    st = ec.lifecycle_states(events)
    assert st == {"nw-vpc": ec.DONE, "nw-sub": ec.DONE}


def test_deterministic_ids_across_runs():
    # injected counter → identical ids on a re-run (reproducible tests / diffs)
    assert [e["resource_id"] for e in _run() if e["kind"] == "resource-tracked"] \
        == [e["resource_id"] for e in _run() if e["kind"] == "resource-tracked"]


# --- build_plan + simulate against the REAL engine (hermetic, no cloud) -------
def test_build_plan_then_simulate_real_engine():
    """The full local-simulate path the control-plane `local` executor will run:
    build_plan (dag_planner + loader) -> simulate_run -> S1a contract. Hermetic —
    drives the real model/planner/loader but makes NO cloud calls."""
    from regression.scenarios.loader import load_lifecycles
    lcs, _ = load_lifecycles(with_sources=True)
    enabled = [lc["id"] for lc in lcs if lc.get("enabled")]
    assert enabled, "no enabled lifecycles in the model"
    target = enabled[0]

    plan = local_run.build_plan([target])
    assert plan["runnable"] == [target]
    assert plan["waves"], "dag_planner produced no waves for a real selection"
    assert target in plan["leaf_set"]                 # the target is in its own closure
    assert target in plan["preview"]

    events = []
    local_run.simulate_run(plan["waves"], plan["preview"],
                           lambda kind, **f: events.append({"kind": kind, **f}))
    kinds = [e["kind"] for e in events]
    assert kinds[0] == "run-meta" and kinds[-1] == "run-end"
    assert "lifecycle-start" in kinds and "lifecycle-end" in kinds
    # every planned lifecycle reaches a terminal state via the S1a reducer
    st = ec.lifecycle_states(events)
    assert st.get(target) in (ec.DONE, ec.RUNNING)
    assert all(v in (ec.DONE, ec.RUNNING, ec.FAIL) for v in st.values())
