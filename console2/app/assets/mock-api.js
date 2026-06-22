// mock-api.js — STATIC DEMO fetch shim for console2.
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
