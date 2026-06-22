"""Offline tests for console2 — the local execution console backend.

Hermetic: drives the real resource model + dag_planner + loader, but makes NO
network calls (model build, plan, and a SIMULATE run are all pure offline). Also
pins the additive ``core.console_events`` sink contract (no-op when the env var is
unset; appends JSONL when set) the engine relies on.
"""
from __future__ import annotations

import importlib.util
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def _load_server():
    """Load tools/console2_server.py as a module (tools/ is not a package)."""
    spec = importlib.util.spec_from_file_location(
        "console2_server", ROOT / "tools" / "console2_server.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


C2 = _load_server()


# --------------------------------------------------------------------------- #
# core.console_events — the additive, env-gated local sink
# --------------------------------------------------------------------------- #
def test_console_events_noop_when_disabled(monkeypatch):
    from core import console_events as cev
    monkeypatch.delenv(cev.ENV, raising=False)
    assert cev.enabled() is False
    cev.emit("step-start", lifecycle="x", step="y")  # must not raise / write


def test_console_events_writes_jsonl_when_enabled(tmp_path, monkeypatch):
    from core import console_events as cev
    sink = tmp_path / "ev.jsonl"
    monkeypatch.setenv(cev.ENV, str(sink))
    assert cev.enabled() is True
    cev.emit("lifecycle-start", lifecycle="networking-vpc-subnet", service="vpc")
    cev.emit("step-end", lifecycle="networking-vpc-subnet", step="create-vpc",
             status=202, category="ok", elapsed_ms=10)
    lines = sink.read_text(encoding="utf-8").strip().splitlines()
    assert len(lines) == 2
    first = json.loads(lines[0])
    assert first["kind"] == "lifecycle-start" and "ts" in first
    assert json.loads(lines[1])["status"] == 202


# --------------------------------------------------------------------------- #
# model: categories -> services -> resources (+ deps & endpoints) + lifecycles
# --------------------------------------------------------------------------- #
def test_build_model_shape():
    m = C2._build_model()
    assert m["node_count"] > 200 and m["lifecycle_count"] > 100
    assert "networking" in m["categories"]
    # a known resource node carries the deps + endpoints the UI needs
    vpc = m["nodes"]["vpc"]
    assert vpc["service"] == "networking/vpc" and vpc["category"] == "networking"
    assert any(a["endpoint"].startswith("POST") for a in vpc["api"])
    # a dependent resource lists its required resources (resource-level deps)
    assert {d["ref"] for d in m["nodes"]["subnet"]["and"]} == {"vpc"}
    # a runnable lifecycle has a step list with method/path
    lc = m["lifecycles"]["networking-vpc-subnet"]
    assert lc["n_steps"] == len(lc["steps"]) > 0
    assert any(s["method"] == "POST" and s["path"] for s in lc["steps"])


def test_resolve_lifecycle_ids_by_service_and_category():
    by_service = set(C2._resolve_lifecycle_ids({"services": ["application-service/queueservice"]}))
    assert "application-queueservice-queue" in by_service
    by_cat = set(C2._resolve_lifecycle_ids({"categories": ["networking"]}))
    # category selection is a superset of any single service in it
    assert by_cat >= set(C2._resolve_lifecycle_ids({"services": ["networking/vpc"]}))


# --------------------------------------------------------------------------- #
# plan: selection -> the REAL dag_planner schedule
# --------------------------------------------------------------------------- #
def test_plan_returns_real_dag_waves():
    ids = C2._resolve_lifecycle_ids({"categories": ["networking"]})
    p = C2._plan(ids)
    waves = p["plan"]["waves"]
    assert waves, "expected a non-empty wave schedule"
    kinds = {w["kind"] for w in waves}
    assert kinds <= {"provision", "free", "adopt", "self-create"}
    # every runnable leaf gets a step preview (which APIs it will exercise)
    assert set(p["preview"]) == set(p["plan"]["leaf_set"])
    assert p["peak_vpcs"] >= 1


# --------------------------------------------------------------------------- #
# simulate: replay the plan to the event stream (no cloud) — also a regression
# guard for the field/param name collision (path/kind ARE event fields).
# --------------------------------------------------------------------------- #
def test_simulate_worker_emits_dag_ordered_events(tmp_path):
    rec = C2._new_rec("simulate", mode="simulate",
                      lifecycle_ids=C2._resolve_lifecycle_ids(
                          {"services": ["application-service/queueservice"]}))
    C2._simulate_worker(rec)
    assert rec["status"] == "done", rec.get("error")
    evs = C2._read_events(rec["events"])
    kinds = [e["kind"] for e in evs]
    assert kinds[0] == "run-meta" and kinds[-1] == "run-end"
    assert "wave-start" in kinds and "lifecycle-start" in kinds
    steps = [e for e in evs if e["kind"] == "step-start"]
    assert steps, "simulate must replay the lifecycle's API steps"
    # the bug we fixed: a step event carries both `path` and (wave) `wave_kind`
    assert all("method" in s and "path" in s for s in steps)
    assert all("wave_kind" in e for e in evs if e["kind"] == "wave-start")


def test_plan_all_disabled_selection_is_empty_not_everything():
    """A selection that resolves to only DISABLED lifecycles must yield an EMPTY
    plan — never the all-enabled fallback (that fallback is only for 'no selection')."""
    m = C2._model()
    disabled = [lid for lid, lc in m["lifecycles"].items() if not lc["enabled"]]
    assert disabled, "fixture expects at least one disabled lifecycle"
    p = C2._plan([disabled[0]])
    assert p["runnable"] == []
    assert p["plan"]["leaf_set"] == []
    # 'no selection at all' still falls back to the full enabled set
    assert len(C2._plan([])["plan"]["leaf_set"]) > 50
