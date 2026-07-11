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


def test_step_end_event_carries_request_response_detail(tmp_path, monkeypatch):
    """The engine enriches step-end with params/req_body/resp_snippet (console2 API
    -tab detail). This pins the event SHAPE the enriched emit relies on — additive
    fields that round-trip through the sink for the UI's request/response panel."""
    from core import console_events as cev
    sink = tmp_path / "ev.jsonl"
    monkeypatch.setenv(cev.ENV, str(sink))
    # exactly the enriched call the engine makes at its step-end site
    cev.emit("step-end", lifecycle="networking-vpc-subnet", step="create-vpc",
             method="POST", path="/v1/vpcs", service="networking/vpc",
             status=202, category="ok", elapsed_ms=812,
             params={"size": 50}, req_body='{"name":"regr-vpc","cidr":"10.0.0.0/16"}',
             resp_snippet='{"resource_id":"vpc-abc123","state":"CREATING"}')
    rec = json.loads(sink.read_text(encoding="utf-8").strip())
    assert rec["kind"] == "step-end"
    assert rec["params"] == {"size": 50}
    assert rec["req_body"].startswith('{"name":"regr-vpc"')
    assert "vpc-abc123" in rec["resp_snippet"]


def test_engine_step_end_emit_includes_enrichment_kwargs():
    """Guard the engine's enriched step-end emit at the source: the call must pass
    params, req_body and resp_snippet (so a refactor that drops them — losing the
    API-tab detail — fails here). Also confirms it's gated by _cev.enabled()."""
    src = (ROOT / "regression" / "scenarios" / "engine.py").read_text(encoding="utf-8")
    assert "_cev and _cev.enabled()" in src, "step-end enrichment must be env-gated"
    # the enriched emit passes all three new fields
    assert "params=_cev_params" in src
    assert "req_body=_cev_req" in src
    assert "resp_snippet=_cev_resp" in src
    # truncation keeps the event small (no unbounded bodies / responses)
    assert "[:400]" in src


# --------------------------------------------------------------------------- #
# endpoint parameter SCHEMA — (method, path) -> the catalog's param definitions
# --------------------------------------------------------------------------- #
def test_endpoint_params_lookup_returns_schema_for_known_endpoint():
    """``_lookup_endpoint_params`` maps (METHOD, templated path) to the catalog
    endpoint's parameter schema (path_params + query_params), so the API tab can
    show 'what params COULD be tested'. GET /v1/vpcs (listvpcs) carries query
    params; the id-addressed DELETE carries a path param."""
    hit = C2._lookup_endpoint_params("GET", "/v1/vpcs")
    assert hit is not None, "GET /v1/vpcs must resolve to a catalog endpoint"
    assert hit["key"] == "networking/vpc/listvpcs"
    assert hit["method"] == "GET" and hit["path"] == "/v1/vpcs"
    qnames = {p["name"] for p in hit["query_params"]}
    assert qnames, "listvpcs should expose filter query params"
    # a templated path normalizes ({subnet_id} -> *) and resolves to its path param
    d = C2._lookup_endpoint_params("DELETE", "/v1/subnets/{subnet_id}")
    assert d and {p["name"] for p in d["path_params"]} == {"subnet_id"}
    # an unknown endpoint is a clean miss (None), not an error
    assert C2._lookup_endpoint_params("GET", "/v1/definitely-not-a-real-collection") is None


def test_model_payload_includes_endpoint_params_map():
    """The /api/model handler ships endpoint_params keyed 'METHOD norm(path)' so the
    client maps an observed call to its schema with no extra round-trip."""
    m = C2._endpoint_params()
    assert isinstance(m, dict) and m, "expected a non-empty endpoint-params map"
    assert "GET v1/vpcs" in m   # key form = METHOD + normalized (slash-stripped) path
    assert m["GET v1/vpcs"]["key"] == "networking/vpc/listvpcs"


# --------------------------------------------------------------------------- #
# resource KIND from a create/delete path (자원 tab TYPE column)
# --------------------------------------------------------------------------- #
def test_resource_kind_from_path_singularizes_collection():
    """The 자원 tab shows the resource KIND (vpc/subnet/port) derived from the
    create/delete path — not the service name. ``_resource_kind_from_path`` is the
    canonical derivation that console2.js kindFromPath mirrors."""
    assert C2._resource_kind_from_path("/v1/subnets/{subnet_id}") == "subnet"
    assert C2._resource_kind_from_path("/v1/vpcs/vpc-abc123") == "vpc"
    assert C2._resource_kind_from_path("/v1/ports") == "port"
    assert C2._resource_kind_from_path("/v1/nat-gateways/{id}") == "nat-gateway"
    # version segment is skipped; an unparseable path falls back to None
    assert C2._resource_kind_from_path("/v1/vpcs") == "vpc"
    assert C2._resource_kind_from_path("") is None


# --------------------------------------------------------------------------- #
# live run: pytest-runner-missing detection (clear status + skip sweep)
# --------------------------------------------------------------------------- #
def test_pytest_did_not_run_detects_missing_runner():
    """When pytest isn't installed the live worker must recognise the runner never
    ran (so it shows a clear message + skips cleanup) rather than reporting a bogus
    '0 passed'. Detected via the 'No module named pytest' marker or a usage/internal
    exit code with no test-outcome line."""
    assert C2._pytest_did_not_run(1, "/usr/bin/python: No module named pytest\n")
    assert C2._pytest_did_not_run(4, "ERROR: usage: pytest ...\n")        # usage error, no outcome
    # a real run with outcomes is NOT flagged, even on a non-zero rc (tests failed)
    assert not C2._pytest_did_not_run(1, "= 2 failed, 3 passed in 4.2s =\n")
    assert not C2._pytest_did_not_run(0, "= 5 passed in 1.0s =\n")


def test_runner_missing_summary_is_actionable():
    """A run record flagged runner_missing summarises with the install hint."""
    rec = C2._new_rec("lifecycle", mode="live", lifecycle_ids=["networking-vpc-subnet"])
    rec["runner_missing"] = True
    rec["status"], rec["rc"] = "done", 1
    assert "테스트 러너 없음" in C2._summarize(rec, "")
    assert C2._rec_view(rec)["runner_missing"] is True


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
    # C-6 (_scope_exclude): scope expansion skips an owner-deferred lifecycle,
    # but explicit selection still runs it (owner escape hatch).
    by_svc = set(C2._resolve_lifecycle_ids({"services": ["ai-ml/aimlops-platform"]}))
    assert "gen-heavy-aimlops" not in by_svc
    explicit = set(C2._resolve_lifecycle_ids({"lifecycle_ids": ["gen-heavy-aimlops"]}))
    assert "gen-heavy-aimlops" in explicit


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


def test_graph_nodes_carry_fields_the_dag_at_scale_scene_groups_by():
    """The DAG-at-scale (B2) scene in resource_graph.js groups the composition DAG
    by CATEGORY derived from ``service.split('/')[0]`` and ranks/colors collapsed
    groups from each node's level/heavy/quota/provenance. Pin that every /api/graph
    node still carries those exact fields and that the category derivation is
    well-formed for the whole selection, so a server-side shape change that would
    silently break collapse-by-category fails HERE (offline, no DOM) instead of in
    the browser.

    A LARGE selection (everything runnable) must also span MANY categories — this is
    the 'gen 1마일' case the scene collapses by default (>~25 nodes)."""
    m = C2._model()
    runnable = [nid for nid, n in m["nodes"].items() if n.get("lifecycle")]
    g = C2._graph({"node_ids": runnable})
    assert len(g["nodes"]) > 25, "select-all is the large graph the scene collapses"
    cats = set()
    for n in g["nodes"]:
        # the precise field set the scene reads per node (resource_graph.js groupNodes
        # + the unit renderer). A missing field would break grouping/coloring.
        for f in ("id", "service", "provenance", "quota", "heavy", "level",
                  "is_target", "shared"):
            assert f in n, f"graph node missing {f!r} the B2 scene needs"
        assert isinstance(n["level"], int)
        # category = the segment before the first '/' — must be non-empty for grouping
        cat = (n["service"] or "").split("/")[0]
        assert cat, f"node {n['id']} has no derivable category from {n['service']!r}"
        cats.add(cat)
    # the at-scale selection genuinely spans many categories (collapse buys a lot)
    assert len(cats) >= 8, f"expected a broad category spread, got {sorted(cats)}"
    # group/category info is ALSO available richer on /api/model for the scene's labels
    assert "groups" in m and m["groups"], "model exposes per-group metadata for labels"


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
        lambda client=None, list_errors=None:
            [{"service": "networking/vpc", "path": "/v1/vpcs/vpc-1"}])
    rec = C2._new_rec("owned")
    C2._owned_worker(rec)
    assert rec["status"] == "done", rec.get("error")
    assert rec["owned_total"] == 1
    assert rec["owned"] == [{"service": "networking/vpc", "path": "/v1/vpcs/vpc-1"}]
    view = C2._rec_view(rec)
    assert view["owned_total"] == 1 and view["owned"][0]["service"] == "networking/vpc"


# --------------------------------------------------------------------------- #
# B1 report master→detail: event → lifecycle grouping (per-lifecycle 자원/API/로그)
# --------------------------------------------------------------------------- #
def _group_events_by_lifecycle(events):
    """Python mirror of console2.js ``groupEventsByLifecycle`` — the pure core of the
    B1 drill-down. Buckets the event stream BY LIFECYCLE so the detail pane can scope
    자원 (resource-tracked/-deleted) · API (step-start/-end) · status to ONE lifecycle
    or aggregate across all. Kept in lock-step with the JS so a server event-shape
    change that would break the drill-down fails HERE (offline) — see the assertions
    in test_simulate_events_group_into_per_lifecycle_detail."""
    lcs, order = {}, []

    def ensure(lid):
        if lid not in lcs:
            lcs[lid] = {"id": lid, "status": "queued", "service": "", "heavy": False,
                        "resources": [], "api": [], "_api_by_step": {},
                        "softN": 0, "failN": 0, "createN": 0}
            order.append(lid)
        return lcs[lid]

    for e in events:
        kind, lid = e.get("kind"), e.get("lifecycle")
        if kind == "run-meta":
            for runnable_id in e.get("runnable", []):
                ensure(runnable_id)
        elif kind == "wave-start":
            for wave_id in e.get("lifecycles", []):
                ensure(wave_id)
        elif kind == "lifecycle-start":
            b = ensure(lid)
            b["status"] = "running"
            if e.get("service"):
                b["service"] = e["service"]
            if e.get("heavy"):
                b["heavy"] = True
        elif kind == "lifecycle-end":
            b = ensure(lid)
            b["status"] = ("done" if e.get("status") == "passed"
                           else "skip" if e.get("status") == "skipped" else "fail")
        elif kind == "step-start":
            b = ensure(lid)
            step = e.get("step")
            c = {"step": step, "lifecycle": lid, "method": e.get("method"),
                 "path": e.get("path"), "status": None, "category": "run", "ms": None}
            b["_api_by_step"][step] = c
            b["api"].append(c)
        elif kind == "step-end":
            b = ensure(lid)
            step = e.get("step")
            c = b["_api_by_step"].get(step)
            if c is None:
                c = {"step": step, "lifecycle": lid, "method": e.get("method"),
                     "path": e.get("path")}
                b["_api_by_step"][step] = c
                b["api"].append(c)
            c["status"] = e.get("status")
            c["category"] = e.get("category")
            c["ms"] = e.get("elapsed_ms")
            if e.get("category") == "soft":
                b["softN"] += 1
            elif e.get("category") == "fail":
                b["failN"] += 1
        elif kind == "resource-tracked":
            b = ensure(lid)
            b["resources"].append({"id": e.get("resource_id"), "type": e.get("resource_type"),
                                   "lifecycle": lid, "path": e.get("path"),
                                   "created": True, "deleted": False})
            b["createN"] += 1
        elif kind == "resource-deleted":
            b = ensure(lid)
            cand = [r for r in b["resources"] if r["type"] == e.get("resource_type") and not r["deleted"]]
            if cand:
                cand[-1]["deleted"] = True
    return {"lcs": lcs, "order": order}


def test_simulate_events_group_into_per_lifecycle_detail():
    """A MULTI-lifecycle simulate (the whole networking/vpc closure) produces events
    that group cleanly into per-lifecycle 자원/API buckets — the data the B1 detail
    pane scopes to one lifecycle. Pins: (1) >1 lifecycle (so there IS a master→detail
    drill, not a single-row run); (2) each bucket carries ordered api calls with
    method/path/status + tracked resources flipped created→deleted by teardown; (3)
    the aggregate (sum of buckets) equals the flat event totals (the 전체 escape hatch
    is loss-less)."""
    rec = C2._new_rec("simulate", mode="simulate",
                      lifecycle_ids=C2._resolve_lifecycle_ids({"services": ["networking/vpc"]}))
    C2._simulate_worker(rec)
    assert rec["status"] == "done", rec.get("error")
    evs = C2._read_events(rec["events"])
    grouped = _group_events_by_lifecycle(evs)
    order, lcs = grouped["order"], grouped["lcs"]
    # (1) the closure spans MORE THAN ONE lifecycle → a real master→detail drill
    assert len(order) > 1, f"expected a multi-lifecycle run, got {order}"
    assert "networking-vpc-subnet" in order
    # (2) every lifecycle that ran reaches a terminal state + has API calls with detail
    ran = [lid for lid in order if lcs[lid]["status"] != "queued"]
    assert ran, "at least one lifecycle should have started"
    for lid in ran:
        b = lcs[lid]
        assert b["status"] in {"running", "done", "fail", "skip"}
        for c in b["api"]:
            assert c["method"] and c["path"]            # the API tab row needs these
        # a lifecycle with a create step tracked a resource (자원 tab populates)
        if b["createN"]:
            assert b["resources"] and b["resources"][0]["id"]
    # the vpc lifecycle created then deleted its resources (생성→삭제 in the 자원 tab)
    vpc = lcs["networking-vpc-subnet"]
    assert vpc["resources"], "vpc lifecycle must track resources"
    assert any(r["deleted"] for r in vpc["resources"]), "teardown should flip created→deleted"
    # (3) aggregate over buckets == flat event totals (전체 view is loss-less)
    flat_tracked = sum(1 for e in evs if e["kind"] == "resource-tracked")
    flat_steps = len({(e["lifecycle"], e["step"]) for e in evs
                      if e["kind"] in ("step-start", "step-end")})
    agg_res = sum(len(lcs[lid]["resources"]) for lid in order)
    agg_api = sum(len(lcs[lid]["api"]) for lid in order)
    assert agg_res == flat_tracked
    assert agg_api == flat_steps


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


# --------------------------------------------------------------------------- #
# suites (named run shapes) — /api/suites is backed by core.suites + the
# console2 `scope:` extension. Pin: the built-in 4 surface with parsed gates,
# a saved suite is CI-valid (render ignores scope), and the guards reject.
# --------------------------------------------------------------------------- #
def test_list_suites_view_surfaces_builtins_with_parsed_gates():
    view = {s["id"]: s for s in C2._list_suites_view()}
    for sid in ("smoke", "full", "full-heavy", "conformance"):
        assert sid in view and view[sid]["builtin"] is True, f"missing builtin {sid}"
    # gates are parsed out of the request block into a flat map (all BOOL_KEYS)
    assert all(v is False for v in view["smoke"]["gates"].values())  # read-only
    assert view["full"]["gates"]["mutations"] and view["full"]["gates"]["destructive"]
    assert view["full"]["gates"]["heavy"] is False
    assert view["full-heavy"]["gates"]["heavy"] and view["full-heavy"]["gates"]["conformance"]


def test_save_suite_writes_ci_valid_file_and_render_ignores_scope(tmp_path, monkeypatch):
    """A console2-saved suite must (a) write suites/<id>.yaml, (b) pass core.suites
    validation, and (c) render to a run-request that carries ONLY the request
    block — the console2 `scope:` extension stays invisible to CI."""
    from core import suites as S
    monkeypatch.setattr(S, "SUITE_DIR", tmp_path)
    out = C2._save_suite({
        "id": "net-core", "label": "core networking",
        "request": {"mutations": True, "destructive": True},
        "scope": {"node_ids": ["vpc", "subnet"], "services": ["networking/vpc"]},
    })
    assert out["id"] == "net-core" and out["builtin"] is False
    path = tmp_path / "net-core.yaml"
    assert path.exists(), "suite file not written"
    data = S.load_suite("net-core")
    assert S.validate_suite(data, path) == [], "saved suite is not CI-valid"
    assert data["scope"]["node_ids"] == ["vpc", "subnet"]  # console2 fidelity preserved
    rendered = S.render(data)
    assert "mutations=true" in rendered and "destructive=true" in rendered
    assert "scope" not in rendered and "node_ids" not in rendered  # CI never sees scope


def test_save_suite_rejects_bad_id_builtin_overwrite_and_gate_inconsistency(tmp_path, monkeypatch):
    import pytest
    from core import suites as S
    monkeypatch.setattr(S, "SUITE_DIR", tmp_path)
    with pytest.raises(ValueError):                       # path-traversal / non-slug id
        C2._save_suite({"id": "../evil", "request": {}})
    with pytest.raises(ValueError):                       # built-in overwrite without force
        C2._save_suite({"id": "smoke", "request": {}})
    with pytest.raises(ValueError):                       # mutations without destructive (core rule)
        C2._save_suite({"id": "lonely-mut", "request": {"mutations": True}})
    assert not list(tmp_path.glob("*.yaml")), "no file should be written on rejection"


# --------------------------------------------------------------------------- #
# cross-run VPC admission + wait queue — the concurrent-execution model:
# several runs in flight, each reserving peak_vpcs slots against the account cap;
# a run that would exceed the cap QUEUES (FIFO) and is admitted when a slot frees.
# Hermetic + synchronous: the account VPC count + cap are monkeypatched (no
# network) and ``_spawn_run`` is stubbed so NO worker thread runs — completion is
# driven explicitly via ``_on_run_finish``. (The real threaded path is covered by
# the live integration run.) This isolates the admission DECISION logic with zero
# timing flakiness.
# --------------------------------------------------------------------------- #
def _reset_admission(monkeypatch, spawned):
    monkeypatch.setattr(C2, "_account_vpc_count", lambda ttl=0.0: 0)   # baseline 0, no LIST
    monkeypatch.setattr(C2, "_vpc_cap", lambda: 5)                     # cap 5 regardless of env
    monkeypatch.setattr(C2, "_spawn_run", lambda rec, worker: spawned.append(rec["id"]))
    C2._RESERVED.clear()
    C2._QUEUE.clear()
    C2._PENDING.clear()
    monkeypatch.setattr(C2, "_BASELINE", 0, raising=False)


def test_admission_reserves_queues_and_auto_dequeues(monkeypatch):
    spawned: list = []
    _reset_admission(monkeypatch, spawned)

    def launch(peak):
        rec = C2._new_rec("simulate", mode="simulate", lifecycle_ids=["x"])
        rec["peak_vpcs"], rec["queued"] = peak, False
        C2._admit_or_queue(rec, lambda r: None)
        return rec

    a, b, c, d = launch(1), launch(1), launch(2), launch(2)   # 1+1+2=4 fit; +2 > cap 5
    assert a["status"] == b["status"] == c["status"] == "running"
    assert d["status"] == "queued"
    cap = C2._capacity_view()
    assert cap["reserved"] == 4 and cap["headroom"] == 1
    assert [q["id"] for q in cap["queued"]] == [d["id"]]
    assert spawned == [a["id"], b["id"], c["id"]]            # only admitted runs spawn

    # A finishes -> a slot frees -> D is admitted (FIFO head-of-line)
    C2._on_run_finish(a["id"])
    assert C2._RUNS[d["id"]]["status"] == "running"
    assert C2._RUNS[d["id"]]["queued"] is False
    assert d["id"] in spawned and a["id"] not in C2._RESERVED
    cap2 = C2._capacity_view()
    assert cap2["reserved"] == 5 and cap2["headroom"] == 0   # b(1)+c(2)+d(2) now hold the cap

    # finish the rest -> drains to empty
    for rid in (b["id"], c["id"], d["id"]):
        C2._on_run_finish(rid)
    assert not C2._RESERVED and not C2._QUEUE


def test_peak_zero_run_never_queues(monkeypatch):
    """A run that needs no VPC (peak 0 — e.g. a light service) is always admitted,
    even when the cap is fully reserved by others."""
    spawned: list = []
    _reset_admission(monkeypatch, spawned)
    C2._RESERVED["other"] = 5                                  # cap fully reserved
    try:
        rec = C2._new_rec("simulate", mode="simulate", lifecycle_ids=["x"])
        rec["peak_vpcs"], rec["queued"] = 0, False
        C2._admit_or_queue(rec, lambda r: None)
        assert rec["status"] == "running", "peak-0 run must not queue"
        assert rec["id"] in spawned
    finally:
        C2._RESERVED.pop("other", None)
        C2._RESERVED.pop(rec["id"], None)


# --------------------------------------------------------------------------- #
# CX 재배치 (2026-07-07) — ② Test Execution: 현재 실행 전면 (frontend contract)
# --------------------------------------------------------------------------- #
def test_execution_cx_relayout_frontend_contract():
    """Pin the CX-relayout UI contract in the shipped frontend files (the JS is
    not executed here — these are the load-bearing strings a refactor must keep):

      1. 실행 기록 is a DEFAULT-COLLAPSED section (toggle header + hidden body,
         fold state persisted in sessionStorage) — the hero (현재 실행) leads.
      2. plan↔run continuity: the run-bound graph carries the "① 폐쇄집합
         그대로" chip and the ② copy of the 생성·검증·삭제 순서표 with the
         in-progress row highlight (.ordnow).
      3. 런타임 is an INLINE 4th detail tab embedding the EXISTING /runtime
         page (scope=mine) — single source, popup kept but labeled 새 창."""
    html = (ROOT / "console2" / "index.html").read_text(encoding="utf-8")
    js = (ROOT / "console2" / "assets" / "console2.js").read_text(encoding="utf-8")
    css = (ROOT / "console2" / "assets" / "console2.css").read_text(encoding="utf-8")
    # 1) history demoted to a collapsed fold
    assert 'id="hist-toggle"' in html
    assert '<div id="report-side" class="hidden">' in html
    assert "c2.histOpen.v1" in js, "fold state must persist in sessionStorage"
    assert "runsOnly.find" in js  # 최근 종료 1건 요약 행 (실행 없을 때 상시 노출)
    # 2) plan↔run continuity chip + run-side order table w/ live-row highlight
    assert "폐쇄집합 그대로" in js and "생성 순서 동일" in js
    assert 'id="r1-order-tbl"' in js and "ordnow" in js
    assert "tr.ordnow td" in css
    # 3) inline runtime tab reuses the one runtime URL (no logic duplication)
    assert 'data-d="rt"' in html
    assert 'id="rt-frame"' in js
    # runtimeUrl() is the ONE place the runtime URL lives (popup, pf link, iframe)
    assert js.count('"/runtime?scope=mine"') == 1 and "function runtimeUrl()" in js
    assert "새 창" in html  # popup 유지 + 라벨


# --------------------------------------------------------------------------- #
# P2C-22 (2026-07-09) — ② 실행 뷰 2-pane: 좌 rail(전체+시나리오) + 우 상세
# --------------------------------------------------------------------------- #
def test_execution_rail_master_detail_contract():
    """Pin the P2C-22 two-pane relayout (owner 2026-07-09: 세로 카드 스택 ↔ 하단
    상세의 스크롤 왕복 제거 — 좌측 전체+시나리오 목록, 우측 상세). Layout only;
    the drill-down semantics (selectScope / default 전체 scope) are untouched:

      1. index.html: a rail <aside> hosts the lc-picker BEFORE the detail pane;
         the master strip keeps its full-width slot and gains a fold toggle.
      2. css: 2-column grid + full-width master row + sticky rail whose LIST
         (not the page) scrolls; single-column fallback at the shared 1180px
         breakpoint (same pattern as .treepanel).
      3. js: 전체(집계) card pinned FIRST with a done/total progress ring,
         status filter chips, compact rows (counts demoted to title tooltip),
         pending rows folded into the rail, follow-active with a user-scroll
         hold, and master fold state persisted in sessionStorage."""
    html = (ROOT / "console2" / "index.html").read_text(encoding="utf-8")
    js = (ROOT / "console2" / "assets" / "console2.js").read_text(encoding="utf-8")
    css = (ROOT / "console2" / "assets" / "console2.css").read_text(encoding="utf-8")
    # 1) rail hosts the picker, left of (= before) the detail pane; master folds
    assert '<aside class="md-rail" id="report-rail">' in html
    assert html.index('id="report-rail"') < html.index('id="report-detail"')
    assert html.index('id="lc-picker"') > html.index('id="report-rail"')
    assert 'id="master-fold"' in html
    # 2) 2-pane grid, full-width master, sticky rail + internal list scroll
    assert "grid-template-columns:minmax(230px,260px) minmax(0,1fr)" in css
    assert ".md-master{grid-column:1/-1}" in css
    assert ".md-rail{position:sticky" in css
    lclist_rule = css.split(".lclist{", 1)[1].split("}")[0]
    assert "overflow-y:auto" in lclist_rule, "the rail LIST must scroll internally"
    assert "@media(max-width:1180px){.md-report{grid-template-columns:1fr}" in css
    # 3) rail renderer: agg-first w/ ring, filter chips, tooltip counts,
    #    pending rows, follow-active hold, master fold persistence
    assert 'class="aggitem top' in js and 'class="ring"' in js
    agg_pos = js.index('class="aggitem top')
    assert agg_pos < js.index('<div class="lclist">'), "전체 카드가 목록 위 (rail 최상단)"
    assert "railFilter" in js and 'chip("fail"' in js and 'chip("queued"' in js
    assert " API · " in js  # 카운트는 행 title 툴팁으로 (1줄 압축 행)
    assert 'class="lcitem pend"' in js  # 대기 행 rail 통합 (구 lcqueue 대체)
    assert "lcqueue" not in js
    assert "RAIL_FOLLOW_HOLD_MS" in js and ".lcitem.now" in js
    assert "c2.masterOpen.v1" in js


# --------------------------------------------------------------------------- #
# P2C-24 (2026-07-09) — 폴링 다이어트 + 무깜빡 렌더 + 진행률 + per-lifecycle 중단
# --------------------------------------------------------------------------- #
def test_events_view_incremental_offset(tmp_path):
    """서버 반쪽: /api/runs/{id}/events?offset=N 증분 계약 (_events_view).

    offset = 클라이언트가 이미 가진 이벤트 개수 → tail 만 응답. next_offset 은
    다음 요청에 보낼 값. 범위 초과/쓰레기 offset 은 0 강등 = 전체 재전송(응답
    offset==0 이 '교체' 신호 — 클라이언트 재동기화)."""
    p = tmp_path / "events.jsonl"
    rows = [{"kind": "run-meta", "runnable": ["a"]},
            {"kind": "lifecycle-start", "lifecycle": "a"},
            {"kind": "lifecycle-end", "lifecycle": "a", "status": "passed"}]
    p.write_text("\n".join(json.dumps(r) for r in rows) + "\n", encoding="utf-8")
    rec = {"status": "running", "lifecycle_ids": ["a"], "events": str(p)}
    full = C2._events_view(rec, 0)
    assert full["offset"] == 0 and full["next_offset"] == 3
    assert [e["kind"] for e in full["events"]] == [
        "run-meta", "lifecycle-start", "lifecycle-end"]
    assert full["status"] == "running" and full["lifecycle_ids"] == ["a"]
    tail = C2._events_view(rec, 2)          # 증분: 이미 2개 보유 → tail 1개만
    assert tail["offset"] == 2 and tail["next_offset"] == 3
    assert [e["kind"] for e in tail["events"]] == ["lifecycle-end"]
    same = C2._events_view(rec, 3)          # 신규 없음 → 빈 tail (호출은 가볍다)
    assert same["events"] == [] and same["next_offset"] == 3
    over = C2._events_view(rec, 99)         # 파일 교체/리셋 → 0 강등 전체 재전송
    assert over["offset"] == 0 and len(over["events"]) == 3
    junk = C2._events_view(rec, "x")        # 쓰레기 입력은 0 취급
    assert junk["offset"] == 0 and len(junk["events"]) == 3


def test_polling_diet_and_flickerfree_frontend_contract():
    """P2C-24 프런트 계약 (owner 2026-07-09 — "백엔드에 api가 너무 많이 날아감" +
    "깜빡거려서 클릭이 안됨" + "run 진행률" + "특정 라이프사이클 중단"):

      1. 폴링 다이어트 — 단일 tick 2s + 증분 fetch(?offset=) + capacity 30s
         (대기열 시 5s) + /api/runs 는 시작/종료/감시로만 + 숨은 탭 정지.
      2. 무깜빡 렌더 — setHtmlIfChanged + 키 기반 syncUnits + 위임 클릭.
      3. 런 진행률 — runProgress() + now-playing 진행률 바 (rail 링과 동일 소스).
      4. per-lifecycle 중단 — 스코프바 ⏸ → POST /api/runs/{rid}/skip-lifecycle."""
    js = (ROOT / "console2" / "assets" / "console2.js").read_text(encoding="utf-8")
    css = (ROOT / "console2" / "assets" / "console2.css").read_text(encoding="utf-8")
    # 1) 폴링 다이어트
    assert "EV_TICK_MS = 2000" in js
    assert "/events?offset=" in js and "next_offset" in js
    assert "setTimeout(pollEvents, 700)" not in js, "구 700ms 폴 복귀 금지"
    assert "CAP_MS = 30000" in js and "CAP_QUEUED_MS = 5000" in js
    assert "RUNS_WATCH_MS" in js and "function startRunsWatch" in js
    assert "document.hidden" in js and "visibilitychange" in js
    # capacity tick 의 /api/runs 동승(폭주 원인)이 사라졌는지 — startCapPoll 본문에
    # loadRunRecords 호출이 없어야 한다
    cap_body = js.split("function startCapPoll", 1)[1].split("function stopCapPoll")[0]
    assert "loadRunRecords" not in cap_body
    # drawReport 라이브 경로에서도 제거 (유휴 no-run 분기의 1회 호출만 허용)
    draw_body = js.split("function drawReport", 1)[1].split("function groupEventsByLifecycle")[0]
    live_path = draw_body.split("a run owns the")[1]        # no-run 분기 이후
    assert "loadRunRecords" not in live_path
    # 2) 무깜빡 렌더
    assert "function setHtmlIfChanged" in js and "function syncUnits" in js
    assert "function wireReportDelegation" in js
    assert 'data-k="lc:' in js and 'data-apik=' in js
    # 3) 진행률
    assert "function runProgress" in js and 'id="np-prog-fill"' in js
    assert "잔여 ~" in js and ".np-prog{" in css
    # 4) per-lifecycle 중단
    assert '"/skip-lifecycle"' in js and 'id="scope-skip"' in js
    assert "이 라이프사이클 중단" in js


# --------------------------------------------------------------------------- #
# v2 접목 2 (2026-07-11) — PLAN vs ACTUAL 스트립 (계획↔실행 연속성, §2.9 B층)
# --------------------------------------------------------------------------- #
def test_plan_actual_strip_frontend_contract():
    """Pin the v2→v1 graft #2 (V2-WRAP-AND-PIVOT §3-2 · V2-L1-DATA-CONTRACT
    §2.9 B층 — donor: controlplane/v2/static/run_exec.js): the run screen shows
    a PLAN↔ACTUAL strip above now-playing while a run is in flight.

      1. index.html: #planactual container sits BEFORE #nowplaying.
      2. PLAN is recomputed server-side — POST /api/plan with rec.lifecycle_ids
         (never a client-side re-derivation of the schedule).
      3. ACTUAL aggregates run events (resource-tracked/-deleted) + capacity
         slot meter; queued state renders WHY QUEUED (여유 < 필요 peak).
      4. Deviation is conservative: '예측 초과' chip only — 지연 의심 판정은
         접목 4(엔진 요청 #5 세마포어 이벤트) 전에는 하지 않는다.
      5. 예측 단일 소스 (콘솔 간트 cf8792b3 과의 정합): 스트립의 시간 예측은
         '예측 vs 실제 타임라인' 패널과 같은 schedule-sim 캐시(pvaSim)를 공유
         — 자체 ETA 재계산 금지, simFetch POST 도 코드베이스에 1곳뿐."""
    html = (ROOT / "console2" / "index.html").read_text(encoding="utf-8")
    js = (ROOT / "console2" / "assets" / "console2.js").read_text(encoding="utf-8")
    css = (ROOT / "console2" / "assets" / "console2.css").read_text(encoding="utf-8")
    # 1) container above now-playing
    assert 'id="planactual"' in html
    assert html.index('id="planactual"') < html.index('id="nowplaying"')
    # 2) server-recomputed plan, keyed to the bound run
    assert "function ensureRunPlan" in js and "function renderPlanActual" in js
    assert 'JSON.stringify({ lifecycle_ids: runSelIds })' in js
    # 3) actual side: event aggregation + slot meter + WHY QUEUED
    assert 'e.kind === "resource-tracked"' in js and 'e.kind === "resource-deleted"' in js
    assert "function slotMeterHtml" in js and ".slotmeter" in css
    assert "WHY QUEUED" in js and "필요 peak" in js
    # 4) conservative deviation only (no 지연 의심 before engine request #5) —
    #    용어·기준은 간트 패널의 amber 와 동일한 '예측 초과'(makespan 초과)
    assert "예측 초과" in js and "ETA 초과" not in js
    assert "avg * 3" not in js, "지연 의심(×3) 판정은 접목 4 전 금지 (세마포어 오탐)"
    # 5) 예측 단일 소스 — 스트립은 pvaSim(schedule-sim)을 공유, 자체 ETA 계산 없음
    assert "function ensurePvaSim" in js
    assert js.count("simFetch({ lifecycle_ids") == 1, \
        "schedule-sim 예측 fetch 는 ensurePvaSim 한 곳이어야 한다 (중복 POST/이중 예측 금지)"
    strip = js.split("function renderPlanActual", 1)[1].split("function renderNowPlaying")[0]
    assert "makespan_s" in strip and "ensurePvaSim()" in strip
    assert 'id="pa-tl"' in strip  # 요약(스트립) → 상세(간트 패널) 딥링크


# --------------------------------------------------------------------------- #
# v2 접목 3 (2026-07-11) — 종료 후 다음 행동 카드 (§2.9 C층)
# --------------------------------------------------------------------------- #
def test_done_card_frontend_contract():
    """Pin the v2→v1 graft #3 (V2-WRAP-AND-PIVOT §3-3): on run end the console
    renders a persistent next-action card (토스트 대신) with the three rows —
    fail→상세, +검증→fold 안내, 잔존→재스캔 — plus a plan-vs-actual retro line.

      1. index.html: #donecard container exists (strip area, above the screens).
      2. onRunEnded renders the card; a new in-flight run hides it.
      3. fold row is ADVISORY only — the console never executes fold: the modal
         shows the derive_verified→promote_validated→커밋 procedure as text.
      4. 잔존 row reuses the EXISTING scanOwned() (no second scan path)."""
    html = (ROOT / "console2" / "index.html").read_text(encoding="utf-8")
    js = (ROOT / "console2" / "assets" / "console2.js").read_text(encoding="utf-8")
    css = (ROOT / "console2" / "assets" / "console2.css").read_text(encoding="utf-8")
    # 1) container + styles
    assert 'id="donecard"' in html and ".donecard" in css
    # 2) card lifecycle: rendered on end, hidden when a new run is in flight
    assert "function renderDoneCard" in js
    assert "renderDoneCard(" in js.split("function onRunEnded", 1)[1].split("function renderDoneCard")[0]
    pa = js.split("function renderPlanActual", 1)[1].split("function renderNowPlaying")[0]
    assert 'dc.classList.add("hidden")' in pa
    # 3) fold advisory (Hard Rule: 콘솔이 fold를 실행하지 않는다)
    assert "/fold-evidence" in js and "function foldHowModal" in js
    assert "tools.derive_verified" in js and "tools.promote_validated" in js
    assert "약 ${j.count}건" in js  # 시간창 근사는 반드시 '약'으로 표기
    # 4) 잔존 row reuses the one scan path
    assert 'id="dc-owned"' in js
    assert "scanOwned()" in js.split('$("dc-owned").onclick', 1)[1][:80]


def test_fold_evidence_endpoint_counts_unpublished_2xx(tmp_path, monkeypatch):
    """/api/runs/{rid}/fold-evidence — 런 시간창 내 2xx 관측 − 발행본 verified
    집합 (계약 §2.4). 계산 전용: 어떤 파일에도 쓰지 않는다. available=False
    (관측 파일 없음) 는 count 0 과 구분되는 별도 상태다."""
    import json
    import time as _t

    from fastapi.testclient import TestClient

    from controlplane.app import app
    from tools import console2_server as c2

    client = TestClient(app)
    now = _t.time()
    rec = {"id": "t-fold-1", "kind": "lifecycle", "status": "done",
           "started": now - 600, "ended": now - 10,
           "log": str(tmp_path / "x.log"), "events": str(tmp_path / "x.events.jsonl")}
    with c2._LOCK:
        c2._RUNS["t-fold-1"] = rec
    try:
        # 관측 파일: 시간창 안 2xx 2건(1건은 발행본에 이미 있음) + 창 밖 1건 + 4xx 1건
        obs = [
            {"ts": now - 300, "endpoint_key": "net/vpc/create", "status": 201},
            {"ts": now - 300, "endpoint_key": "net/vpc/list", "status": 200},
            {"ts": now - 7200, "endpoint_key": "net/vpc/old", "status": 200},
            {"ts": now - 300, "endpoint_key": "net/vpc/soft", "status": 404},
        ]
        monkeypatch.setattr("core.results.load_observations", lambda *a, **k: obs)
        import controlplane.dashdata as dashdata
        monkeypatch.setattr(dashdata, "file", lambda p: (
            json.dumps({"verified": ["net/vpc/list"]}).encode(), "application/json")
            if p == "verified_endpoints.json" else None)
        r = client.get("/api/runs/t-fold-1/fold-evidence").json()
        assert r["available"] is True and r["count"] == 1, r
        assert r["preview"][0]["endpoint_key"] == "net/vpc/create"
        # 관측 파일 없음 → available=False (0건과 다른 상태)
        monkeypatch.setattr("core.results.load_observations", lambda *a, **k: [])
        r2 = client.get("/api/runs/t-fold-1/fold-evidence").json()
        assert r2["available"] is False
        # 없는 run → 404
        assert client.get("/api/runs/no-such/fold-evidence").status_code == 404
    finally:
        with c2._LOCK:
            c2._RUNS.pop("t-fold-1", None)


# --------------------------------------------------------------------------- #
# 성능 수리 (2026-07-11, 오너 실측 제보) — 1,500+ 호출 런에서 클릭/폴 멈춤
# --------------------------------------------------------------------------- #
def test_large_run_render_diet_frontend_contract():
    """Pin the large-run render diet (오너 제보: 'soft 건수를 누르면 화면이
    멈춤'). 실측 재현: 2,200 호출 라이브 런에서 폴 틱당 54–274ms 롱태스크 +
    soft 타일은 클릭해도 무동작. 수리 3종 — 수리 후 롱태스크 0 실측:

      1. groupedRun() 메모이즈 — runEvents 배열 참조가 캐시 키 (폴마다 새
         배열). 한 틱의 여러 소비자(drawReport·runProgress·rail·간트)가 전체
         이벤트를 각자 재스캔하지 않는다. lifecycleStates()도 캐시를 읽는다.
      2. kpi 타일(api 호출/ok/soft/fail) = 결과 필터 토글. 타일 필터는
         dup-hide 를 무시하고 원본에서 거른다 (soft N 타일 vs 0행 모순 방지).
      3. API 표 행 상한(API_ROW_CAP) — 초과분은 최신순으로 자르고 생략 수 +
         [전체 표시] opt-in 을 표 첫 행에 명시 (묵살 금지)."""
    js = (ROOT / "console2" / "assets" / "console2.js").read_text(encoding="utf-8")
    css = (ROOT / "console2" / "assets" / "console2.css").read_text(encoding="utf-8")
    # 1) 메모이즈 — 재스캔 제거
    assert "_grCache" in js and "_grCache.ref !== runEvents" in js
    lc = js.split("function lifecycleStates", 1)[1].split("function liveProgress")[0]
    assert "groupedRun()" in lc and "runEvents.forEach" not in lc
    # 2) kpi 타일 필터 (+ dup-hide 무시 규칙)
    assert "apiCatFilter" in js and '.selcat[data-cat]' in js
    assert "calls.filter(c => c.category === apiCatFilter)" in js
    assert ".kpi .s.selcat" in css and ".selcat.on" in css
    # 3) 행 상한 + 명시적 해제
    assert "API_ROW_CAP = 500" in js and 'id="api-showall"' in js
    assert "생략 (성능 보호)" in js
    # 스코프/런 전환 시 필터·상한 상태 리셋 (stale 필터로 빈 화면 방지)
    assert js.count('apiCatFilter = "all"; apiShowAll = false;') >= 3
