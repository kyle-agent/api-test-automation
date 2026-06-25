"""Offline tests for controlplane/local_executor.py (S2 — in-process local runs).

No web server, no fastapi, no cloud: drives the real engine (build_plan) through a
daemon-thread simulate and verifies the per-run file normalizes (S1a) to the live
view + per-lifecycle states the control-plane `local` executor will serve.
"""
from __future__ import annotations

from controlplane import local_executor as lx
from core import events_contract as ec
from regression.scenarios.loader import load_lifecycles


def _a_target() -> str:
    lcs, _ = load_lifecycles(with_sources=True)
    enabled = [lc["id"] for lc in lcs if lc.get("enabled")]
    assert enabled, "no enabled lifecycles in the model"
    return enabled[0]


def test_start_simulate_runs_to_done_and_normalizes():
    target = _a_target()
    started = lx.start_simulate([target])                 # step_delay=0 → near-instant
    assert started["status"] == "running" and started["id"].startswith("local-")
    assert "_thread" not in started                       # internals not leaked

    done = lx.join(started["id"])
    assert done["status"] == "done" and done["runnable"] == [target]

    res = lx.read_events(started["id"])
    kinds = [e["kind"] for e in res["events"]]
    assert kinds and kinds[0] == "run-meta" and kinds[-1] == "run-end"
    assert "lifecycle-start" in kinds and "step-end" in kinds
    # the fine events fold to a terminal state for the graph overlay
    assert res["states"].get(target) in (ec.DONE, ec.RUNNING)
    assert res["run"]["status"] == "done"


def test_run_shows_in_list_and_get():
    target = _a_target()
    started = lx.start_simulate([target])
    lx.join(started["id"])
    assert any(r["id"] == started["id"] for r in lx.list_runs())
    assert lx.get(started["id"])["status"] == "done"


def test_unknown_run_is_none():
    assert lx.read_events("local-does-not-exist") is None
    assert lx.get("local-does-not-exist") is None
    assert lx.join("local-does-not-exist") is None


def test_two_runs_do_not_interleave():
    # per-run files isolate concurrent simulates — each stream is self-contained
    target = _a_target()
    a = lx.start_simulate([target])
    b = lx.start_simulate([target])
    lx.join(a["id"]); lx.join(b["id"])
    ea, eb = lx.read_events(a["id"]), lx.read_events(b["id"])
    assert ea["events"][0]["kind"] == "run-meta" and ea["events"][-1]["kind"] == "run-end"
    assert eb["events"][0]["kind"] == "run-meta" and eb["events"][-1]["kind"] == "run-end"
    assert a["id"] != b["id"]
