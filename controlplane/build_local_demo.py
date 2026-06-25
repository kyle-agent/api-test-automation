"""Build a backend-free STATIC demo of the Local Run screen for GitHub Pages.

Mirrors ``console2/build_static.py``: renders ``local_run.html`` via Jinja (no
server), bakes the ``/api/local/*`` responses (lifecycle picker, a demo plan, and
one hermetic simulate run's events/states), and injects a ``window.fetch``
monkeypatch so the REAL page JS runs **unchanged** offline. The result is a single
self-contained ``index.html`` a viewer can open on Pages — no backend, no cloud.

    python -m controlplane.build_local_demo            # -> reports/local-run-demo/ (gitignored)

Publish: copy ``reports/local-run-demo/`` to the Pages branch
(``dashboard-data:/local-run-demo/``).
"""
from __future__ import annotations

import json
from pathlib import Path

from jinja2 import Environment, FileSystemLoader

from core import events_contract
from regression.scenarios import local_run
from regression.scenarios.loader import load_lifecycles

ROOT = Path(__file__).resolve().parent.parent
TPL_DIR = ROOT / "controlplane" / "templates"
OUT = ROOT / "reports" / "local-run-demo"

# a selection that shows the parallel structure: 3 concurrent (wave 0) + 1 (wave 1).
DEMO_SEL = ["networking-vpc-subnet", "application-queueservice-queue",
            "apigateway-api-write-coverage", "cloud-ml-write-coverage"]

MOCK_JS = r"""
(function () {
  var D = window.__DEMO__ || {};
  var real = window.fetch ? window.fetch.bind(window) : null;
  function ok(obj) {
    return Promise.resolve({ ok: true, status: 200,
      json: function () { return Promise.resolve(obj); } });
  }
  window.fetch = function (url, opts) {
    url = "" + url;
    if (url.indexOf("/api/local/lifecycles") >= 0) return ok({ lifecycles: D.lifecycles || [] });
    if (url.indexOf("/api/local/graph") >= 0) return ok(D.graph || { nodes: [], edges: [], node_lifecycle: {} });
    if (url.indexOf("/api/local/plan") >= 0) return ok(D.plan || { waves: [] });
    if (url.indexOf("/api/local/run") >= 0 && opts && opts.method === "POST")
      return ok({ ok: true, run: { id: "demo-run", mode: "simulate", status: "running" } });
    if (url.indexOf("/api/local/runs/") >= 0 && url.indexOf("/events") >= 0)
      return ok({ run: { id: "demo-run", mode: "simulate", status: "done" },
                  events: D.events || [], states: D.states || {} });
    return real ? real(url, opts) : ok({});
  };
  // clean-URL demo: if no query, auto-select the demo + auto-run once.
  if (!location.search) {
    var q = "?pick=" + encodeURIComponent((D.sel || []).join(",")) + "&auto=1";
    history.replaceState(null, "", location.pathname + q);
  }
})();
"""


def bake() -> dict:
    lcs, _ = load_lifecycles(with_sources=True)
    lifecycles = sorted(
        ({"id": lc["id"], "service": lc.get("service", ""), "heavy": bool(lc.get("heavy"))}
         for lc in lcs if lc.get("enabled")),
        key=lambda r: (r["service"], r["id"]))
    plan = local_run.build_plan(DEMO_SEL)
    raw: list = []
    # step_delay only stamps elapsed_ms (sleep stays a no-op) → instant build, but
    # the baked events carry realistic per-step timing so the seq-vs-parallel stat
    # shows a real speedup in the demo.
    local_run.simulate_run(plan["waves"], plan["preview"],
                           lambda k, **f: raw.append({"kind": k, **f}), step_delay=0.12)
    norm = [e for ev in raw for e in events_contract.normalize(ev, "console")]
    states = events_contract.lifecycle_states(norm)
    # composition DAG (scene renderer) + node->lifecycle, same as /api/local/graph
    from regression.scenarios import composer
    model = composer.load_model()
    sel = set(DEMO_SEL)
    targets = sorted(nid for nid, task in model.items()
                     if ((task.get("source") or {}).get("lifecycle")) in sel)
    graph = composer.graph_view(targets, model=model) if targets else {"nodes": [], "edges": []}
    graph["node_lifecycle"] = {
        n["id"]: (((model.get(n["id"]) or {}).get("source") or {}).get("lifecycle") or "")
        for n in graph.get("nodes", [])}
    return {"lifecycles": lifecycles, "plan": plan, "events": norm, "states": states,
            "graph": graph, "sel": [s for s in DEMO_SEL if s in plan["runnable"]]}


def build() -> Path:
    OUT.mkdir(parents=True, exist_ok=True)
    env = Environment(loader=FileSystemLoader(str(TPL_DIR)), autoescape=True)
    html = env.get_template("local_run.html").render(active="testing")
    data = bake()
    inject = (
        '<script>window.__DEMO__=' + json.dumps(data, ensure_ascii=False) + ';</script>\n'
        '<script>/* static demo — fetch monkeypatch (no backend) */' + MOCK_JS + '</script>\n'
        '<div style="position:fixed;top:0;left:0;right:0;z-index:99;background:#1d2530;color:#fff;'
        'text-align:center;font-size:12px;padding:4px">DEMO · 정적 스냅샷(백엔드 없이 미리 구운 데이터 · '
        '실행은 모의) — 실제 실행은 control plane 호스트에서</div>\n')
    # mock-api must monkeypatch fetch BEFORE the page script runs -> inject in <head>.
    html = html.replace("</head>", inject + "</head>", 1)
    # inline the scene renderer (the absolute "/resource_graph.js" src won't resolve
    # under the Pages sub-path) so the demo is fully self-contained.
    rgjs = (ROOT / "controlplane" / "static" / "resource_graph.js").read_text(encoding="utf-8")
    html = html.replace('<script src="/resource_graph.js"></script>',
                        "<script>/* inlined resource_graph.js */\n" + rgjs + "</script>", 1)
    (OUT / "index.html").write_text(html, encoding="utf-8")
    (OUT / "README.md").write_text(
        "# Local Run — static demo\n\nBackend-free snapshot built by "
        "`python -m controlplane.build_local_demo` (baked /api/local/* via a fetch "
        "monkeypatch; the real page JS is unchanged). Publish to "
        "`dashboard-data:/local-run-demo/`.\n", encoding="utf-8")
    return OUT


if __name__ == "__main__":
    out = build()
    idx = out / "index.html"
    print("built:", idx, "(%d KB)" % (idx.stat().st_size // 1024))
