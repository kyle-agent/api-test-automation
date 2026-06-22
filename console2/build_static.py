#!/usr/bin/env python3
"""build_static.py — assemble a self-contained STATIC demo snapshot of console2.

The REAL console2 app (``console2/index.html`` + ``console2/assets/*``) is a
live-backend SPA: ``console2.js`` fetches ``/api/model`` / ``/api/graph`` /
``/api/run`` / ``/api/runs/...`` from ``tools/console2_server.py``. A GitHub Pages
host has no backend, so this script produces a bundle that runs the *unchanged*
front-end against **baked data + a fetch-mock shim**:

  1) BAKE — import the server's pure builder functions (no running server, no
     creds, no cloud) and snapshot them to JSON-able Python:
       * ``model``   = ``_model()`` + ``endpoint_params`` (the full /api/model).
       * ``graphs``  = ``_graph(...)`` per single service (+ a few multi-service
         examples), keyed by a normalized selection signature so a click in the
         demo resolves to a REAL ``composer.graph_view`` DAG.
       * ``run``     = one hermetic SIMULATE (``_new_rec`` + ``_simulate_worker``
         with the pacing env at 0) read back as ``record`` / ``events`` / ``log``
         so the 흐름·자원·API·로그 report tabs are fully populated. A couple of
         clearly-marked DEMO step events carry ``params``/``req_body``/
         ``resp_snippet`` so the API-row detail panel can demo a request/response.

  2) ASSEMBLE — copy ``index.html`` + ``assets/*`` into ``reports/console2-static/``
     (the real source is never touched), write ``data/static-data.js`` (the baked
     ``window.__C2_STATIC__``) and ``assets/mock-api.js`` (monkeypatches
     ``window.fetch`` to serve ``/api/*`` from the bake BEFORE console2.js loads),
     and inject — into the COPY of index.html only — the two shim scripts plus a
     fixed DEMO banner, with the default selection pre-applied.

  3) The caller publishes ``reports/console2-static/`` to the Pages branch
     (``dashboard-data:/console2/app/``). This script only builds the bundle.

Run:  python3 console2/build_static.py        # writes reports/console2-static/
"""
from __future__ import annotations

import json
import os
import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "console2"
OUT = ROOT / "reports" / "console2-static"

# The demo's default selection — the 구성 DAG + a runnable simulate are visible on
# load. networking/vpc is a rich, dependency-heavy service (good first impression).
DEFAULT_SERVICE = "networking/vpc"

# A small set of multi-service example selections so the demo isn't single-service
# only — each becomes a baked graph keyed by its signature. Kept tiny + cheap.
MULTI_EXAMPLES: list[list[str]] = [
    ["networking/vpc", "networking/loadbalancer"],
    ["compute/virtualserver", "networking/vpc"],
]

# Specific NODE-ID selections to bake verbatim (in addition to the per-service node
# sets). The real ``console2.js init()`` seeds ``targets`` with the literal pair
# ``["vpc","subnet"]`` (a partial, sub-service selection) and POSTs exactly that on
# load — so we bake that exact node_ids signature to guarantee the very first DAG
# the user sees is a correct composition, not the graceful default-fallback. Any
# OTHER hand-built node subset still falls back to the default graph by design.
NODE_ID_EXAMPLES: list[list[str]] = [
    ["vpc", "subnet"],          # == console2.js init() default targets
    ["vpc"],                    # a lone root
]


# --------------------------------------------------------------------------- #
# selection signature — MUST match what assets/mock-api.js computes client-side
# so a POST /api/graph body finds its baked graph. The real client sends
# ``{node_ids:[...]}``; we additionally key by ``{services:[...]}`` so a baked
# service graph is reachable either way. Signature = sorted, comma-joined ids.
# --------------------------------------------------------------------------- #
def _sig(kind: str, items: list[str]) -> str:
    return kind + ":" + ",".join(sorted(str(i) for i in items if str(i).strip()))


def _service_nodes(model: dict, service: str) -> list[str]:
    """The lifecycle-bearing resource-node ids a selected service contributes —
    mirrors the server's ``_graph_targets`` for a ``{services:[svc]}`` selection.
    Lets us also key a baked graph by its node-id signature (what the client
    actually POSTs as ``node_ids``)."""
    return sorted(
        nid for nid, n in model["nodes"].items()
        if n.get("lifecycle") and n.get("service") == service
    )


def _bake_graph(server, sel: dict) -> dict:
    """Resolve a selection to the real composer.graph_view payload (the /api/graph
    response). Pure offline projection — no cloud, no schedule."""
    return server._graph(sel)


def _synthesize_api_detail_events(events: list[dict]) -> list[dict]:
    """Attach clearly-marked DEMO ``params``/``req_body``/``resp_snippet`` to a
    couple of step events so the API-row detail panel can demo a request/response
    panel. Simulate emits no live params (that's expected — the API tab still shows
    the catalog schema); these are *synthetic, labelled* values for the snapshot
    only. We enrich the first create (POST) step-end and the first read (GET)
    step-end we find, leaving the event stream otherwise identical."""
    DEMO = "DEMO"  # marker so the UI/inspectors can tell these are synthetic
    enriched_post = enriched_get = False
    for e in events:
        if e.get("kind") != "step-end":
            continue
        method = (e.get("method") or "").upper()
        if method == "POST" and not enriched_post:
            e["params"] = {"name": "demo-vpc", "cidr": "10.0.0.0/16", "_demo": DEMO}
            e["req_body"] = {
                "name": "demo-vpc",
                "cidrBlock": "10.0.0.0/16",
                "description": "static-demo snapshot (synthetic request body)",
                "_demo": DEMO,
            }
            e["resp_snippet"] = {
                "id": "sim-vpc-DEMO0001",
                "state": "CREATING",
                "name": "demo-vpc",
                "_demo": DEMO,
            }
            enriched_post = True
        elif method == "GET" and not enriched_get:
            e["params"] = {"size": 20, "page": 0, "_demo": DEMO}
            e["resp_snippet"] = {
                "contents": [{"id": "sim-vpc-DEMO0001", "state": "ACTIVE"}],
                "totalCount": 1,
                "_demo": DEMO,
            }
            enriched_get = True
        if enriched_post and enriched_get:
            break
    return events


def bake() -> dict:
    """Run the pure builders + one hermetic simulate; return the baked payload
    ``{model, graphs, run, defaultSelection}`` ready to serialize into
    ``window.__C2_STATIC__``."""
    # Make the simulate hermetic + instant (no watch-pacing for a snapshot).
    os.environ["SCP_SIM_STEP_DELAY"] = "0"
    os.environ["SCP_SIM_BEAT"] = "0"
    sys.path.insert(0, str(ROOT))
    import tools.console2_server as server  # noqa: E402 — after env + sys.path setup

    # ----- model (full /api/model, incl. endpoint_params) -----
    model = dict(server._model())
    model["endpoint_params"] = server._endpoint_params()

    services = list(model.get("services") or [])
    if DEFAULT_SERVICE not in services:
        raise SystemExit(
            f"default service {DEFAULT_SERVICE!r} not in model.services "
            f"({len(services)} services) — pick another DEFAULT_SERVICE"
        )

    # ----- graphs: one per single service, keyed by BOTH service-sig and
    # node-id-sig (the client POSTs node_ids; service-sig is a convenience key). -----
    graphs: dict[str, dict] = {}
    empty_services: list[str] = []
    for svc in services:
        sel = {"services": [svc]}
        g = _bake_graph(server, sel)
        if not g.get("nodes"):
            empty_services.append(svc)  # nothing runnable for this service (no lifecycle)
            continue
        graphs[_sig("svc", [svc])] = g
        nids = _service_nodes(model, svc)
        if nids:
            graphs[_sig("nodes", nids)] = g  # what selectionPayload() actually sends

    # a couple of multi-service example selections (real composition across services)
    multi_baked: list[dict] = []
    for combo in MULTI_EXAMPLES:
        if not all(s in services for s in combo):
            continue
        sel = {"services": combo}
        g = _bake_graph(server, sel)
        if not g.get("nodes"):
            continue
        graphs[_sig("svc", combo)] = g
        nids = sorted({n for s in combo for n in _service_nodes(model, s)})
        if nids:
            graphs[_sig("nodes", nids)] = g
        multi_baked.append({"services": combo, "n_nodes": len(g["nodes"])})

    # explicit node-id selections (e.g. the app's literal ["vpc","subnet"] default)
    node_baked: list[dict] = []
    for nids in NODE_ID_EXAMPLES:
        present = [n for n in nids if n in model["nodes"] and model["nodes"][n].get("lifecycle")]
        if not present:
            continue
        g = _bake_graph(server, {"node_ids": present})
        if not g.get("nodes"):
            continue
        graphs[_sig("nodes", present)] = g
        node_baked.append({"node_ids": present, "n_nodes": len(g["nodes"])})

    # the default graph (so the demo never shows an empty DAG even on a miss)
    default_graph = graphs[_sig("svc", [DEFAULT_SERVICE])]

    # ----- a sample SIMULATE run (instant, hermetic) -> record/events/log -----
    ids = server._resolve_lifecycle_ids({"services": [DEFAULT_SERVICE]})
    rec = server._new_rec("simulate", mode="simulate", lifecycle_ids=ids)
    server._simulate_worker(rec)
    if rec.get("status") != "done":
        raise SystemExit(f"sample simulate did not complete cleanly: {rec.get('status')} "
                         f"({rec.get('error')})")
    record = server._rec_view(rec, full=True)        # incl. log (last 250 lines)
    events = server._read_events(rec["events"])
    events = _synthesize_api_detail_events(events)   # demo a request/response panel
    log = rec["log"] and Path(rec["log"]).read_text(encoding="utf-8")

    run_payload = {
        "record": record,                 # GET /api/runs/<id>  (+ log)
        "events": events,                 # GET /api/runs/<id>/events
        "log": log or record.get("log", ""),
    }

    # ----- suites (GET /api/suites): the named run-shape presets the Suite ▾
    # picker loads. Baked from suites/*.yaml so load+apply work offline; POST
    # (save) is served in-memory by the shim (no persistence in a snapshot). -----
    suites = server._list_suites_view()

    return {
        "model": model,
        "graphs": graphs,
        "run": run_payload,
        "suites": suites,
        # The app's init() seeds targets with ["vpc","subnet"] and POSTs that on
        # load; we record the SAME here so the documented default matches what the
        # unchanged front-end actually selects (its DAG is baked verbatim above).
        "defaultSelection": {"services": [DEFAULT_SERVICE],
                             "node_ids": ["vpc", "subnet"]},
        # build-time stats (handy for the report / verification; the app ignores it)
        "_meta": {
            "default_service": DEFAULT_SERVICE,
            "n_services": len(services),
            "n_graphs_single": sum(1 for k in graphs if k.startswith("svc:")),
            "n_graph_keys": len(graphs),
            "empty_services": empty_services,
            "multi_examples": multi_baked,
            "node_examples": node_baked,
            "run_lifecycles": ids,
            "run_events": len(events),
            "run_steps": sum(1 for e in events if e.get("kind") == "step-end"),
            "default_graph_nodes": len(default_graph["nodes"]),
            "n_suites": len(suites),
        },
    }


# --------------------------------------------------------------------------- #
# the fetch-mock shim — a standalone JS file (node --check-able). It monkeypatches
# window.fetch to serve /api/* from window.__C2_STATIC__ BEFORE console2.js runs.
# Kept dependency-free + defensive: anything it can't match falls back gracefully
# (default graph / benign stub) so no button ever throws.
# --------------------------------------------------------------------------- #
MOCK_API_JS = r"""// mock-api.js — STATIC DEMO fetch shim for console2.
// Loaded BEFORE console2.js. Monkeypatches window.fetch so the UNCHANGED real app
// (console2.js) talks to baked data in window.__C2_STATIC__ instead of a live
// backend. No network, no cloud — this is a snapshot. Every /api/* route below
// is served from the bake; unknown routes fall through to the real fetch.
(function () {
  "use strict";
  var DATA = window.__C2_STATIC__ || {};
  var MODEL = DATA.model || {};
  var GRAPHS = DATA.graphs || {};
  var RUN = DATA.run || {};
  var REC = RUN.record || {};
  var RUN_ID = REC.id || "demo-run";
  var SUITES = (DATA.suites || []).slice();   // named run-shape presets (Suite ▾)
  var realFetch = window.fetch ? window.fetch.bind(window) : null;

  function jsonResponse(obj, status) {
    var body = JSON.stringify(obj);
    if (typeof Response === "function") {
      return Promise.resolve(new Response(body, {
        status: status || 200,
        headers: { "Content-Type": "application/json; charset=utf-8" }
      }));
    }
    // very-old-browser fallback: a minimal Response-like object
    return Promise.resolve({
      ok: (status || 200) < 400, status: status || 200,
      json: function () { return Promise.resolve(JSON.parse(body)); },
      text: function () { return Promise.resolve(body); }
    });
  }

  // signature builder — MUST match build_static.py _sig(): "<kind>:<sorted,csv>".
  function sig(kind, items) {
    var arr = (items || []).map(String).filter(function (s) { return s.trim() !== ""; });
    arr.sort();
    return kind + ":" + arr.join(",");
  }

  // resolve a POST /api/graph selection {node_ids|services|...} to a baked graph.
  // Try node-id signature first (what the app sends), then service signature,
  // then the default graph. Empty selection -> the empty-graph shape the app
  // renders as "nothing selected". Never throws.
  function graphFor(sel) {
    sel = sel || {};
    var nodeIds = sel.node_ids || sel.nodeIds || [];
    var services = sel.services || [];
    if ((!nodeIds || !nodeIds.length) && (!services || !services.length)) {
      return { nodes: [], edges: [], levels: [0], shared: [],
               peak_quota: {}, order: [], teardown: [] };
    }
    if (nodeIds && nodeIds.length) {
      var gk = GRAPHS[sig("nodes", nodeIds)];
      if (gk) return gk;
    }
    if (services && services.length) {
      var gs = GRAPHS[sig("svc", services)];
      if (gs) return gs;
    }
    // graceful fallback: the default service graph (so the DAG is never broken).
    var def = GRAPHS[sig("svc", [(DATA.defaultSelection || {}).services
      ? DATA.defaultSelection.services[0] : ""])];
    return def || { nodes: [], edges: [], levels: [0], shared: [],
                    peak_quota: {}, order: [], teardown: [] };
  }

  // endpoint-params single lookup (mirror server _ep_norm_path/_ep_key); the model
  // already ships endpoint_params so this is only the GET /api/endpoint-params path.
  function normPath(p) {
    p = (p || "").split("?")[0].replace(/^\/+|\/+$/g, "");
    return p.split("/").map(function (s) { return s.indexOf("{") >= 0 ? "*" : s; }).join("/");
  }
  function endpointParam(method, path) {
    var map = MODEL.endpoint_params || {};
    return map[(method || "").toUpperCase() + " " + normPath(path)] || null;
  }

  // route a request URL+init to a baked response, or null to pass through.
  function route(url, init) {
    var u;
    try { u = new URL(url, window.location.href); }
    catch (e) { return null; }
    var path = u.pathname;
    var method = ((init && init.method) || "GET").toUpperCase();
    if (path.indexOf("/api/") !== 0) return null;     // not an API call -> real fetch

    // ---- GET /api/model ----
    if (path === "/api/model" && method === "GET") return jsonResponse(MODEL);

    // ---- GET /api/suites -> baked presets; POST -> in-memory add (no persistence) ----
    if (path === "/api/suites" && method === "GET") return jsonResponse({ suites: SUITES });
    if (path === "/api/suites" && method === "POST") {
      var sb = {};
      try { sb = init && init.body ? JSON.parse(init.body) : {}; } catch (e) {}
      var req = sb.request || {}, gates = {};
      ["mutations", "destructive", "heavy", "sweep_force", "conformance"]
        .forEach(function (k) { gates[k] = !!req[k]; });
      var view = { id: sb.id || "demo", label: sb.label || "", request: req,
                   gates: gates, scope: sb.scope || {}, builtin: false, _demo: true };
      SUITES = SUITES.filter(function (s) { return s.id !== view.id; }).concat([view]);
      return jsonResponse({ suite: view, suites: SUITES }, 201);
    }

    // ---- GET /api/endpoint-params?method=&path= ----
    if (path === "/api/endpoint-params" && method === "GET") {
      var m = u.searchParams.get("method") || "";
      var pth = u.searchParams.get("path") || "";
      if (!pth) return jsonResponse({ error: "path query param required" }, 400);
      var hit = endpointParam(m, pth);
      if (!hit) return jsonResponse({ error: "endpoint not in catalog",
        method: m.toUpperCase(), path: pth }, 404);
      return jsonResponse(hit);
    }

    // ---- POST /api/graph {selection} ----
    if (path === "/api/graph" && method === "POST") {
      var sel = {};
      try { sel = init && init.body ? JSON.parse(init.body) : {}; } catch (e) {}
      return jsonResponse(graphFor(sel));
    }

    // ---- POST /api/plan {selection} ---- (not used by the current UI, but kept
    // benign so a future/edge caller doesn't error). Return an empty-ish plan.
    if (path === "/api/plan" && method === "POST") {
      return jsonResponse({ lifecycle_ids: [], requested: [], runnable: [],
        skipped_disabled: [], plan: { waves: [] }, summary: "(static demo · plan not baked)",
        preview: {}, peak_vpcs: 0 });
    }

    // ---- POST /api/run -> the baked run id (simulate OR live both show the bake) ----
    if (path === "/api/run" && method === "POST") {
      return jsonResponse(REC, 202);
    }

    // ---- GET /api/runs -> [the baked record] ----
    if (path === "/api/runs" && method === "GET") {
      return jsonResponse({ runs: [REC] });
    }

    // ---- GET /api/runs/<id>/events -> all events at once (status done) ----
    var mEv = path.match(/^\/api\/runs\/([^/]+)\/events$/);
    if (mEv && method === "GET") {
      return jsonResponse({ id: mEv[1], status: REC.status || "done",
        events: RUN.events || [] });
    }

    // ---- GET /api/runs/<id> -> the record (+ log) ----
    var mRec = path.match(/^\/api\/runs\/([^/]+)$/);
    if (mRec && method === "GET") {
      var rec = Object.assign({}, REC, { log: RUN.log || REC.log || "" });
      return jsonResponse(rec);
    }

    // ---- POST /api/owned -> benign empty inventory (so 남은 자원 확인 doesn't error) ----
    if (path === "/api/owned" && method === "POST") {
      return jsonResponse({ id: "demo-owned", kind: "owned", status: "done",
        owned: [], owned_total: 0,
        summary: "없음 ✅ — 남은 자원 0건 (정적 데모)" }, 202);
    }
    // ---- POST /api/cleanup -> benign stub ----
    if (path === "/api/cleanup" && method === "POST") {
      return jsonResponse({ id: "demo-cleanup", kind: "cleanup", status: "done",
        rc: 0, summary: "🧹 정적 데모 — 삭제할 실자원 없음" }, 202);
    }
    // ---- POST /api/verify -> benign clean stub ----
    if (path === "/api/verify" && method === "POST") {
      return jsonResponse({ id: "demo-verify", kind: "verify", status: "done",
        rc: 0, summary: "✅ clean — owned survivors: 0 (정적 데모)" }, 202);
    }

    // any other /api/* -> a benign 404 (never a thrown network error)
    return jsonResponse({ error: "static demo — endpoint not baked", path: path }, 404);
  }

  window.fetch = function (input, init) {
    var url = (typeof input === "string") ? input
      : (input && input.url) ? input.url : String(input);
    // a Request object carries its own method/body; surface it to route().
    if (!init && input && typeof input === "object" && input.method) {
      init = { method: input.method };
    }
    var r = route(url, init || {});
    if (r) return r;
    if (realFetch) return realFetch(input, init);
    return jsonResponse({ error: "no backend (static demo)" }, 404);
  };

  // mark that the shim is live (handy for the verification grep + debugging).
  window.__C2_MOCK_API__ = true;
})();
"""


# --------------------------------------------------------------------------- #
# the DEMO banner + script injection (applied to the COPY of index.html only)
# --------------------------------------------------------------------------- #
BANNER_HTML = (
    '<div id="c2-demo-banner" style="position:fixed;top:0;left:0;right:0;z-index:9999;'
    'background:#1f6feb;color:#fff;font:600 12px/1.5 -apple-system,BlinkMacSystemFont,'
    '\'Segoe UI\',sans-serif;padding:5px 12px;text-align:center;'
    'box-shadow:0 1px 4px rgba(0,0,0,.25)">'
    "DEMO · 정적 스냅샷 (백엔드 없이 "
    "미리 구운 데이터 · 실행은 "
    "모의)</div>"
    # nudge the page down a touch so the fixed banner doesn't cover the header.
    '<style>body{padding-top:30px!important}</style>'
)

INJECT_SCRIPTS = (
    '<script src="data/static-data.js"></script>\n'
    '<script src="assets/mock-api.js"></script>\n'
)


def _inject_index(html: str) -> str:
    """Into the COPY of index.html: add the DEMO banner right after <body>, and the
    two shim <script>s (static-data + mock-api) BEFORE the first console2 script so
    window.__C2_STATIC__ + the fetch monkeypatch exist before console2.js runs.
    Idempotent-ish and defensive: raises if the expected anchors aren't found."""
    # 1) banner — right after the opening <body ...> tag.
    body_open = html.find("<body")
    if body_open == -1:
        raise SystemExit("index.html: no <body> tag to inject the banner after")
    body_gt = html.find(">", body_open)
    html = html[: body_gt + 1] + "\n" + BANNER_HTML + html[body_gt + 1:]

    # 2) shim scripts — immediately before the resource_graph.js / console2.js block.
    anchor = '<script src="assets/resource_graph.js"'
    idx = html.find(anchor)
    if idx == -1:
        anchor = '<script src="assets/console2.js"'
        idx = html.find(anchor)
    if idx == -1:
        raise SystemExit("index.html: no console2 <script> include to inject before")
    html = html[:idx] + INJECT_SCRIPTS + html[idx:]
    return html


def assemble(baked: dict) -> None:
    """Write the self-contained bundle into reports/console2-static/."""
    if OUT.exists():
        shutil.rmtree(OUT)
    (OUT / "assets").mkdir(parents=True, exist_ok=True)
    (OUT / "data").mkdir(parents=True, exist_ok=True)

    # copy the REAL assets verbatim (console2.js runs unchanged)
    for f in sorted((SRC / "assets").glob("*")):
        if f.is_file():
            shutil.copy2(f, OUT / "assets" / f.name)

    # baked data -> window.__C2_STATIC__ (strip the build-only _meta from the app's view)
    app_data = {k: v for k, v in baked.items() if k != "_meta"}
    static_js = ("// static-data.js — baked console2 snapshot (generated by "
                 "console2/build_static.py).\n// window.__C2_STATIC__ feeds "
                 "assets/mock-api.js, which serves /api/* offline.\n"
                 "window.__C2_STATIC__ = "
                 + json.dumps(app_data, ensure_ascii=False, default=str)
                 + ";\n")
    (OUT / "data" / "static-data.js").write_text(static_js, encoding="utf-8")

    # the fetch-mock shim
    (OUT / "assets" / "mock-api.js").write_text(MOCK_API_JS, encoding="utf-8")

    # the COPY of index.html with the banner + shim scripts injected
    src_html = (SRC / "index.html").read_text(encoding="utf-8")
    (OUT / "index.html").write_text(_inject_index(src_html), encoding="utf-8")

    # a short README note in the bundle (what this is / how it was built)
    readme = (
        "# console2 — STATIC DEMO SNAPSHOT\n\n"
        "This is a backend-free snapshot of the REAL console2 app, for GitHub Pages.\n"
        "The front-end (`assets/console2.js`) is the UNCHANGED production app; it is\n"
        "served baked data via a `window.fetch` monkeypatch (`assets/mock-api.js`)\n"
        "that answers `/api/*` from `data/static-data.js` (`window.__C2_STATIC__`).\n\n"
        "Built by `console2/build_static.py` (no creds, no cloud, no running server).\n"
        "Simulate AND live both surface the same pre-baked SIMULATE run — there is no\n"
        "real execution in the snapshot.\n\n"
        f"Baked: model={baked['_meta']['n_services']} services / "
        f"{baked['model']['node_count']} resources; "
        f"graphs={baked['_meta']['n_graphs_single']} single-service "
        f"(+{len(baked['_meta']['multi_examples'])} multi) ; "
        f"sample run={len(baked['_meta']['run_lifecycles'])} lifecycle(s) / "
        f"{baked['_meta']['run_steps']} API steps.\n"
    )
    (OUT / "README.md").write_text(readme, encoding="utf-8")


def main() -> None:
    if not (SRC / "index.html").exists():
        raise SystemExit(f"console2 source not found at {SRC} — run from the repo root")
    print("baking model + graphs + a hermetic simulate run …")
    baked = bake()
    assemble(baked)
    m = baked["_meta"]
    print(f"  model       : {baked['model']['node_count']} resources / "
          f"{m['n_services']} services / {baked['model']['lifecycle_count']} lifecycles "
          f"({len(baked['model'].get('endpoint_params') or {})} endpoint param schemas)")
    print(f"  graphs      : {m['n_graphs_single']} single-service "
          f"(+{len(m['multi_examples'])} multi-service) -> {m['n_graph_keys']} keys; "
          f"default '{m['default_service']}' = {m['default_graph_nodes']} nodes")
    if m["empty_services"]:
        print(f"  (no-graph services, skipped gracefully: {len(m['empty_services'])})")
    print(f"  sample run  : {len(m['run_lifecycles'])} lifecycle(s), "
          f"{m['run_events']} events, {m['run_steps']} API step(s)")
    print(f"  bundle      : {OUT}")
    print(f"  files       : {', '.join(sorted(str(p.relative_to(OUT)) for p in OUT.rglob('*') if p.is_file()))}")


if __name__ == "__main__":
    main()
