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
# Keep simulate fast + hermetic in tests: the UI pacing (~0.35s/step) would make
# each simulate test take seconds. The worker reads these module globals at call
# time, so zeroing them here drops the sleeps without changing the event shape.
C2._SIM_STEP_DELAY = 0.0
C2._SIM_BEAT = 0.0


# --------------------------------------------------------------------------- #
# core.console_events — the additive, env-gated local sink
# --------------------------------------------------------------------------- #
def test_console_events_noop_when_disabled(monkeypatch):
    from core import console_events as cev
    monkeypatch.delenv(cev.ENV, raising=False)
    assert cev.enabled() is False
    cev.emit("step-start", lifecycle="x", step="y")  # must not raise / write


def test_console_events_resource_tracked_emit(tmp_path, monkeypatch):
    """The engine hook emits ``resource-tracked`` with the REAL resource id +
    service/path so the console2 자원 view populates for LIVE runs exactly like
    simulate's synthetic ids. This pins the event SHAPE the engine emit relies on
    (no engine import / no cloud — just the additive sink contract)."""
    from core import console_events as cev
    sink = tmp_path / "ev.jsonl"
    monkeypatch.setenv(cev.ENV, str(sink))
    # the exact call the engine makes right after reg.track(ResourceRecord(...))
    cev.emit("resource-tracked", lifecycle="networking-vpc-subnet",
             resource_id="vpc-abc123", resource_type="networking/vpc",
             service="networking/vpc", path="/v1/vpcs/vpc-abc123")
    cev.emit("resource-deleted", lifecycle="networking-vpc-subnet",
             resource_type="networking/vpc", service="networking/vpc",
             path="/v1/vpcs/vpc-abc123")
    lines = sink.read_text(encoding="utf-8").strip().splitlines()
    assert len(lines) == 2
    tracked = json.loads(lines[0])
    assert tracked["kind"] == "resource-tracked"
    assert tracked["resource_id"] == "vpc-abc123"
    assert tracked["resource_type"] == "networking/vpc"
    assert tracked["path"] == "/v1/vpcs/vpc-abc123" and "ts" in tracked
    assert json.loads(lines[1])["kind"] == "resource-deleted"


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
# graph: selection -> composer.graph_view (composition DAG for the console2 ①)
# --------------------------------------------------------------------------- #
def test_graph_for_service_has_levels_targets_shared_and_edges():
    """POST /api/graph (via _graph) for a single service returns the composition
    DAG: nodes carrying level/is_target/shared, a non-empty edge set, and the
    dependency closure pulls vpc (subnet's root)."""
    g = C2._graph({"services": ["networking/vpc"]})
    assert g["nodes"], "expected resource nodes for networking/vpc"
    assert g["edges"], "expected a non-empty edge set (subnet→…→vpc etc.)"
    # every node carries the layout/role fields the light DAG renderer needs
    for n in g["nodes"]:
        assert "level" in n and "is_target" in n and "shared" in n
        assert isinstance(n["level"], int)
    ids = {n["id"] for n in g["nodes"]}
    assert "vpc" in ids, "closure must pull vpc"
    # an edge is {from,to} between two nodes that are present
    assert all(e["from"] in ids and e["to"] in ids for e in g["edges"])
    # at least one target (a selected service's lifecycle-bearing resource)
    assert any(n["is_target"] for n in g["nodes"])
    # graph_view's accounting fields are passed through as-is
    assert "order" in g and "teardown" in g and "peak_quota" in g


def test_graph_empty_selection_is_empty_graph_not_error():
    """An empty selection returns an empty graph (no 500) so the UI renders
    'nothing selected' without special-casing."""
    g = C2._graph({})
    assert g["nodes"] == [] and g["edges"] == []
    assert g["order"] == [] and g["teardown"] == []


def test_graph_targets_skips_lookup_resources():
    """A selected service contributes only its lifecycle-bearing resource nodes;
    lookup / no-lifecycle resources are never standalone targets."""
    m = C2._model()
    # pick any service that has BOTH a lifecycle-bearing node and a no-lifecycle
    # (lookup / dep-only) node — robust to model drift.
    by_svc: dict[str, dict] = {}
    for nid, n in m["nodes"].items():
        svc = n.get("service")
        if not svc:
            continue
        b = by_svc.setdefault(svc, {"lc": [], "nolc": []})
        b["lc" if n.get("lifecycle") else "nolc"].append(nid)
    mixed = next((s for s, b in by_svc.items() if b["lc"] and b["nolc"]), None)
    assert mixed, "model expected at least one service with a lookup resource"
    targets = set(C2._graph_targets({"services": [mixed]}))
    # every resolved target actually has a lifecycle in the model
    assert targets and all(m["nodes"][t].get("lifecycle") for t in targets)
    # the service's no-lifecycle resources are excluded as standalone targets
    assert not (set(by_svc[mixed]["nolc"]) & targets)


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


def test_simulate_worker_emits_resource_tracked(tmp_path):
    """Simulate now emits clearly-synthetic resource ids (prefix ``sim-``) on
    create steps so the resource-inventory report renders with no cloud calls."""
    rec = C2._new_rec("simulate", mode="simulate",
                      lifecycle_ids=C2._resolve_lifecycle_ids(
                          {"services": ["application-service/queueservice"]}))
    C2._simulate_worker(rec)
    assert rec["status"] == "done", rec.get("error")
    evs = C2._read_events(rec["events"])
    tracked = [e for e in evs if e["kind"] == "resource-tracked"]
    assert tracked, "simulate must emit resource-tracked on create steps"
    one = tracked[0]
    assert one["resource_id"].startswith("sim-")  # clearly synthetic, no cloud id
    assert one.get("resource_type") and one.get("path") and one.get("lifecycle")
    # a lifecycle with a delete step also reports the teardown
    assert any(e["kind"] == "resource-deleted" for e in evs)


# --------------------------------------------------------------------------- #
# scan_owned: structured owned-resource inventory (read-only LIST sweep)
# --------------------------------------------------------------------------- #
def test_scan_owned_returns_a_list(monkeypatch):
    """``cleanup.verify_clean.scan_owned`` returns ``[{"service","path"}, ...]``.
    Hermetic: monkeypatch the reconciler sweep so NO cloud/LIST call is made — we
    only assert the structured-list contract (shape + per-entry keys), tolerating
    an empty inventory. A passed-in client is honoured (never builds an ApiClient)."""
    import cleanup.verify_clean as vc
    import cleanup.reconciler as recon

    # stub the sweep to "find" two owned resources via the patched _delete the
    # scanner installs — i.e. exercise scan_owned's collection of (service, path)
    # without touching the network. scan_owned swaps recon._delete for its own
    # collector, then calls run_sweep(client); our fake run_sweep invokes that
    # collector exactly like the real sweep would for two owned resources.
    def fake_run_sweep(client):
        recon._delete(client, "networking/vpc", "/v1/vpcs/vpc-1")
        recon._delete(client, "networking/subnet", "/v1/subnets/sn-1")

    monkeypatch.setattr(recon, "run_sweep", fake_run_sweep)
    owned = vc.scan_owned(client=object())  # passed-in client => no ApiClient build
    assert isinstance(owned, list)
    assert all(isinstance(o, dict) and "service" in o and "path" in o for o in owned)
    services = {o["service"] for o in owned}
    assert services == {"networking/vpc", "networking/subnet"}
    # restoring _delete after the call: a second scan with an empty sweep yields []
    monkeypatch.setattr(recon, "run_sweep", lambda client: None)
    assert vc.scan_owned(client=object()) == []


def test_owned_worker_records_structured_list(monkeypatch):
    """``_owned_worker`` stores the scan_owned list + total on the run record and
    marks it done (no cloud — scan_owned is monkeypatched)."""
    monkeypatch.setattr(
        "cleanup.verify_clean.scan_owned",
        lambda client=None: [{"service": "networking/vpc", "path": "/v1/vpcs/vpc-1"}])
    rec = C2._new_rec("owned")
    C2._owned_worker(rec)
    assert rec["status"] == "done", rec.get("error")
    assert rec["owned_total"] == 1
    assert rec["owned"] == [{"service": "networking/vpc", "path": "/v1/vpcs/vpc-1"}]
    view = C2._rec_view(rec)
    assert view["owned_total"] == 1 and view["owned"][0]["service"] == "networking/vpc"


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
