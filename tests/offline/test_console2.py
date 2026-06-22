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
        lambda client=None: [{"service": "networking/vpc", "path": "/v1/vpcs/vpc-1"}])
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
