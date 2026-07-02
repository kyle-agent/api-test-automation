"""Build a backend-free STATIC demo of the 4-stage IA console for GitHub Pages.

The IA console is the linear ``Catalog → Modeling → Testing → Reporting`` flow.
On Pages there is no backend, so this script bakes each page into a self-contained
bundle a viewer can click through with **no server**.

Approach — REAL production HTML + RELATIVE static files (robust; NOT a
``window.fetch`` monkeypatch). The app runs in-process via Starlette
``TestClient``; we fetch each route's REAL rendered HTML, then post-process it:
rewrite the absolute fetch / asset / nav URLs to **relative files written next to
the HTML**. On Pages a relative ``*.json`` fetch (``modeling.map.json`` etc.) just
works off the file tree — far more robust than intercepting ``window.fetch``.

  python -m controlplane.build_ia_demo          # -> reports/ia-demo/ (gitignored)

Output (``reports/ia-demo/``):
  index.html / catalog.html   ① Catalog   (server-rendered, no fetch)
  modeling.html               ② Modeling  (fetches modeling.map.json)
  reporting.html              ④ Reporting (fetches reporting.map.json)
  testing/                    ③ Testing   (console2 static bundle, baked offline)
  resource_graph.js           the shared SVG DAG renderer (relative)
  modeling.map.json           baked /planning/resources/map.json
  reporting.map.json          baked /reporting/coverage/map.json
  _verify_*.png               offline file:// render screenshots (verification)
  README.md

Publish: the lead copies ``reports/ia-demo/`` to the Pages branch
(``dashboard-data:/ia-demo/``). This script only builds the bundle — it does not
touch app.py / base.html / the routers, and it does not publish or commit.
"""
from __future__ import annotations

import re
import shutil
import warnings
from contextlib import contextmanager
from pathlib import Path

# strip the live-only dep-graph block from node form pages (it fetch()es graph.json
# from the server). Marked in resource_form.html with IA_STRIP_START/END.
_STRIP_RE = re.compile(r"<!--IA_STRIP_START-->.*?<!--IA_STRIP_END-->", re.S)
# a bare node deep-link  href="/planning/resources/<id>"  where <id> is node-id
# shaped ([A-Za-z0-9_-], closing quote right after — so query links like
# compose?… never match): the catalog recipe links + the form's reverse-deps +
# the modeling table rows. Run AFTER _nav_rewrite (so …/resources/map|worklist are
# already rewritten and 'map'/'worklist' aren't mistaken for nodes).
_NODE_LINK_RE = re.compile(r'href="/planning/resources/([A-Za-z0-9][A-Za-z0-9_-]*)"')

# TestClient pulls in a noisy httpx deprecation warning under starlette; mute it
# so the build log stays readable (purely cosmetic).
warnings.filterwarnings("ignore")

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "reports" / "ia-demo"

# ---- the literal URLs each rendered page emits, verified against c.get(...).text.
# (see the module docstring of each *_routes.py for where these originate.)
RG_SRC = "/static/resource_graph.js"          # shared renderer <script src> (modeling+reporting)
MODELING_FETCH = "/planning/resources/map.json"  # modeling map page fetch()
REPORTING_FETCH = "/reporting/coverage/map.json"  # reporting coverage page fetch()
HTMX_CDN = "https://unpkg.com/htmx.org@1.9.12"   # base.html <script src> (CDN; vendored offline)


# --------------------------------------------------------------------------- #
# fetch shim — the page JS does ``fetch("modeling.map.json").then(r=>r.json())``.
# The relative ``*.json`` siblings are written for GitHub Pages (https), where a
# relative fetch works fine. But over ``file://`` Chromium blocks cross-origin
# fetch (origin "null") — so for a ROBUST, server-free open-anywhere bundle we
# ALSO inline the baked JSON as a data island (``window.__IA_DATA__[name]``) and
# replace ``fetch(name)`` with ``__iaFetch(name)``: it resolves the inlined data
# first (works on file:// AND Pages), and falls back to a real relative fetch if
# the island is ever absent. The .json files still exist as siblings.
# --------------------------------------------------------------------------- #
IA_FETCH_JS = r"""<script>/* IA demo — fetch shim (inlined data preferred; relative fetch fallback) */
window.__iaFetch = function (name) {
  var D = window.__IA_DATA__ || {};
  if (Object.prototype.hasOwnProperty.call(D, name)) {
    return Promise.resolve({ ok: true, status: 200,
      json: function () { return Promise.resolve(D[name]); } });
  }
  return fetch(name);  // Pages (https): the relative *.json sibling resolves.
};
</script>
"""


# --------------------------------------------------------------------------- #
# DEMO banner — a fixed top strip injected right after <body> on every page,
# with a body padding nudge so it never covers the (sticky) nav.
# --------------------------------------------------------------------------- #
BANNER_HTML = (
    '<div id="ia-demo-banner" style="position:fixed;top:0;left:0;right:0;z-index:9999;'
    'background:#1d2530;color:#fff;text-align:center;font-size:12px;font-weight:600;'
    'padding:5px 12px;box-shadow:0 1px 4px rgba(0,0,0,.25);'
    "font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif\">"
    "DEMO · 정적 스냅샷(백엔드 없이 미리 구운 데이터) — 실제 실행은 control-plane 호스트에서"
    "</div>"
    # push the page down so the fixed 28px banner doesn't hide the sticky header.
    '<style>body{padding-top:30px!important}</style>'
)


def _inject_banner(html: str) -> str:
    """Insert the DEMO banner immediately after the opening <body ...> tag."""
    body_open = html.find("<body")
    if body_open == -1:
        raise SystemExit("page HTML: no <body> tag to inject the DEMO banner after")
    body_gt = html.find(">", body_open)
    return html[: body_gt + 1] + "\n" + BANNER_HTML + html[body_gt + 1:]


def _inline_data(html: str, name: str, raw_json: str) -> str:
    """Inject the data island (``window.__IA_DATA__[name] = <json>``) + the fetch
    shim right after <head>, and repoint the page's ``fetch("<name>")`` at the
    shim ``__iaFetch("<name>")``. Both scripts must precede the page's own inline
    graph script (which runs at parse time inside <body>), so <head> is the anchor.
    """
    island = (
        "<script>/* IA demo — baked data island */\n"
        "window.__IA_DATA__ = window.__IA_DATA__ || {};\n"
        f"window.__IA_DATA__[{name!r}] = {raw_json};\n</script>\n"
    )
    html = html.replace("</head>", island + IA_FETCH_JS + "</head>", 1)
    return html.replace(f'fetch("{name}")', f'window.__iaFetch("{name}")')


def _vendor_htmx(html: str, *, available: bool) -> str:
    """base.html loads htmx from a CDN; offline that's an ERR_CONNECTION_CLOSED.
    If we managed to vendor it as a relative ``htmx.min.js`` sibling, repoint the
    <script src> there; otherwise leave the CDN URL (htmx only drives the modeling
    side-panel, not the graph, so the demo still renders)."""
    if available:
        return html.replace(HTMX_CDN, "htmx.min.js")
    return html


def _nav_rewrite(html: str) -> str:
    """Rewrite the base.html top-nav (+ brand) absolute hrefs to the relative demo
    files, and neutralize the in-app links that have no offline target.

    Specific (longer) paths first so a prefix replace can't clobber a longer one.
    Each href is matched WITH its surrounding ``"`` quotes so we only touch real
    ``href="..."`` attributes, never substrings inside other URLs.
    """
    repl = [
        # --- the 4-stage nav + brand (base.html) ---
        ('href="/planning/resources/map"', 'href="modeling.html"'),
        ('href="/testing/embed"', 'href="testing.html"'),
        ('href="/reporting/coverage"', 'href="reporting.html"'),
        ('href="/catalog"', 'href="index.html"'),
        ('class="brand" href="/"', 'class="brand" href="index.html"'),
        # --- links with no offline target -> '#' (kept clickable, no 404) ---
        ('href="/knowledge"', 'href="#"'),
        ('href="/dashboard/index.html"', 'href="../"'),  # demo: 면② public dashboard = Pages root
        # --- Plan stepper sub-nav (_plan_steps.html, modeling page) ---
        ('href="/planning?step=catalog"', 'href="index.html"'),
        ('href="/planning?step=model"', 'href="modeling.html"'),
        ('href="/planning?step=compose"', 'href="#"'),
        ('href="/planning/validate"', 'href="#"'),
        # --- modeling: 작업 큐 + the (offline-absent) list/breadcrumb -> '#' ---
        ('href="/planning/resources/worklist"', 'href="#"'),
        ('href="/planning/resources/"', 'href="#"'),
        ('href="/planning/resources"', 'href="#"'),
    ]
    for a, b in repl:
        html = html.replace(a, b)
    return html


def _rewrite_node_links(html: str) -> str:
    """Repoint bare node deep-links ``href="/planning/resources/<id>"`` at the baked
    per-node detail page ``node-<id>.html``. Used by Catalog (✏️ recipe links) AND
    the node form pages (reverse-deps links). MUST run after ``_nav_rewrite`` so the
    ``…/resources/map`` menu link is already rewritten (not seen as node 'map')."""
    return _NODE_LINK_RE.sub(lambda m: f'href="node-{m.group(1)}.html"', html)


def _strip_marked(html: str) -> str:
    """Remove the IA_STRIP_START..END block(s) — the form's live dep-graph that
    fetch()es from the server (no offline target). The node's recipe + reverse-deps
    list (server-rendered) stay; the visual DAG lives on the Modeling 그림 toggle."""
    return _STRIP_RE.sub(
        '<p class="muted" style="font-size:12px">의존 그래프는 라이브 콘솔 또는 '
        'Modeling 표→그림 토글에서 확인 (정적 데모에서는 생략).</p>', html)


def _build_catalog(c, *, htmx: bool) -> None:
    """① Catalog — pure server-rendered (no fetch). nav-rewrite + recipe-link
    rewrite + htmx + banner. Written as BOTH catalog.html and index.html (landing)."""
    html = c.get("/catalog").text
    html = _nav_rewrite(html)
    html = _rewrite_node_links(html)  # ✏️ recipe deep-links -> node-<id>.html
    html = _vendor_htmx(html, available=htmx)
    html = _inject_banner(html)
    (OUT / "catalog.html").write_text(html, encoding="utf-8")
    (OUT / "index.html").write_text(html, encoding="utf-8")


def _build_modeling(c, *, htmx: bool) -> None:
    """② Modeling — bake map.json (as a relative sibling AND an inlined data island
    so it renders over file:// too), repoint fetch() + renderer at relative files,
    nav-rewrite + htmx + banner."""
    map_json = c.get(MODELING_FETCH).text
    (OUT / "modeling.map.json").write_text(map_json, encoding="utf-8")
    html = c.get("/planning/resources/map").text
    # <script src="/static/resource_graph.js"> -> relative sibling
    html = html.replace(RG_SRC, "resource_graph.js")
    # fetch("/planning/resources/map.json") -> fetch("modeling.map.json"), then the
    # shim rewrites that to __iaFetch + inlines the data island.
    html = html.replace(MODELING_FETCH, "modeling.map.json")
    html = _inline_data(html, "modeling.map.json", map_json)
    html = _vendor_htmx(html, available=htmx)
    html = _nav_rewrite(html)
    html = _rewrite_node_links(html)  # table row id/편집 links -> node-<id>.html
    # the graph pane navigates via ``NODE_URL + encodeURIComponent(id)`` — repoint at
    # the baked ``node-<id>.html`` so a node click opens the full detail offline.
    html = html.replace("NODE_URL + encodeURIComponent(id)",
                        '"node-" + encodeURIComponent(id) + ".html"')
    html = _inject_banner(html)
    (OUT / "modeling.html").write_text(html, encoding="utf-8")


def _build_node_pages(c, *, htmx: bool) -> int:
    """Per-node DETAIL pages — the Modeling table/graph + Catalog recipe links open
    these. Each node's REAL edit form (server-rendered recipe: requires/options/body/
    verify/capture/delete/flags + the M2 reverse-deps list), with the live dep-graph
    block stripped and node/nav links repointed at relative ``node-<id>.html``. This
    is what makes '상세' viewable offline (was a 404)."""
    from controlplane import resource_model
    model = resource_model.load_model()
    n = 0
    for nid in sorted(model):
        try:
            html = c.get(f"/planning/resources/{nid}").text
        except Exception:
            continue
        html = _strip_marked(html)        # drop the live dep-graph fetch block
        html = _vendor_htmx(html, available=htmx)
        html = _nav_rewrite(html)         # top nav + breadcrumb -> relative/#
        html = _rewrite_node_links(html)  # reverse-deps links -> node-<id>.html (after nav!)
        # any remaining server-only /planning link (compose, edit, save, …) -> '#'
        # (node links already became node-<id>.html above, so they're safe from this).
        html = re.sub(r'href="/planning[^"]*"', 'href="#"', html)
        html = _inject_banner(html)
        (OUT / f"node-{nid}.html").write_text(html, encoding="utf-8")
        n += 1
    return n


def _build_reporting(c, *, htmx: bool) -> None:
    """④ Reporting — same shape as modeling: bake the coverage map.json (sibling +
    inlined island), repoint fetch() + renderer, nav-rewrite + htmx + banner."""
    map_json = c.get(REPORTING_FETCH).text
    (OUT / "reporting.map.json").write_text(map_json, encoding="utf-8")
    html = c.get("/reporting/coverage").text
    html = html.replace(RG_SRC, "resource_graph.js")
    html = html.replace(REPORTING_FETCH, "reporting.map.json")
    html = _inline_data(html, "reporting.map.json", map_json)
    html = _vendor_htmx(html, available=htmx)
    html = _nav_rewrite(html)
    html = _inject_banner(html)
    (OUT / "reporting.html").write_text(html, encoding="utf-8")


def _build_renderer(c) -> None:
    """The shared SVG DAG renderer, written once as a relative sibling that both
    modeling.html and reporting.html load (their <script src> was rewritten to it)."""
    (OUT / "resource_graph.js").write_text(c.get(RG_SRC).text, encoding="utf-8")


def _build_testing() -> None:
    """③ Testing — reuse console2/build_static.py (it bakes console2 fully offline)
    and copy its tree into testing/. Keeps console2's own chrome (acceptable for the
    demo); rewrite its top-nav 대시보드 link to ../index.html if present."""
    import console2.build_static as c2

    c2.main()  # writes reports/console2-static/
    src = c2.OUT
    dst = OUT / "testing"
    if dst.exists():
        shutil.rmtree(dst)
    shutil.copytree(src, dst)
    # best-effort: point console2's "대시보드" top-nav back-link at the IA landing.
    # The real link is the relative ``<a href="../../" id="dashLink">``; absolute
    # forms are covered too in case the source ever changes.
    idx = dst / "index.html"
    if idx.exists():
        html = idx.read_text(encoding="utf-8")
        for needle in ('href="../../" id="dashLink"', 'href="/dashboard/index.html"',
                       'href="/dashboard"'):
            if needle in html:
                repl = ('href="../index.html" id="dashLink"'
                        if "dashLink" in needle else 'href="../index.html"')
                html = html.replace(needle, repl)
        idx.write_text(html, encoding="utf-8")


def _build_testing_shell(c, *, htmx: bool) -> None:
    """③ Testing — the embed SHELL (base.html nav + an iframe of the console2 bundle),
    so the demo's Testing keeps the 4-stage nav instead of jumping to raw console2.
    Mirrors the live ``/testing/embed`` page; the iframe is repointed at the static
    ``testing/`` bundle with ``?embed=1`` (console2 hides its own brand/nav and shows
    the Test Planning | Test Execution toggle). Build AFTER ``_build_testing``."""
    html = c.get("/testing/embed").text
    html = html.replace('src="/testing/console/?embed=1"', 'src="testing/index.html?embed=1"')
    html = _vendor_htmx(html, available=htmx)
    html = _nav_rewrite(html)
    html = _inject_banner(html)
    (OUT / "testing.html").write_text(html, encoding="utf-8")


def _write_readme() -> None:
    files = sorted(str(p.relative_to(OUT)) for p in OUT.rglob("*") if p.is_file())
    (OUT / "README.md").write_text(
        "# IA Console — STATIC DEMO (4-stage)\n\n"
        "Backend-free snapshot of the linear **Catalog → Modeling → Testing → "
        "Reporting** console, for GitHub Pages. Built by "
        "`python -m controlplane.build_ia_demo` — no creds, no cloud, no running "
        "server.\n\n"
        "## How it was built\n"
        "Each page's REAL production HTML is fetched in-process via Starlette "
        "`TestClient`, then post-processed: the absolute `fetch()` / asset / nav "
        "URLs are rewritten to **relative static files written next to the HTML** "
        "(`modeling.map.json`, `reporting.map.json`, `resource_graph.js`). On "
        "Pages a relative `*.json` fetch just works off the file tree — no "
        "`window.fetch` monkeypatch.\n\n"
        "- **Catalog** (`index.html` = `catalog.html`): server-rendered, no fetch.\n"
        "- **Modeling** (`modeling.html`): fetches `modeling.map.json`, draws the "
        "DAG via `resource_graph.js`. (Node-click opens the edit form, which has "
        "no offline page — graph focus still works.)\n"
        "- **Testing** (`testing.html`): the console2 bundle (`testing/`) embedded in "
        "the spine shell via an iframe (`?embed=1`), so the 4-stage nav stays put and "
        "the toggle reads Test Planning | Test Execution.\n"
        "- **Reporting** (`reporting.html`): fetches `reporting.map.json`, same "
        "shared renderer.\n\n"
        "## Publish\n"
        "Copy this directory to the Pages branch: `dashboard-data:/ia-demo/`.\n\n"
        "## Files\n" + "".join(f"- `{f}`\n" for f in files),
        encoding="utf-8",
    )


@contextmanager
def _model_cache():
    """Memoize the pure model/lifecycle loaders for the DURATION OF THE BUILD only.

    Every page render re-reads + re-parses all ``resources/*.yaml`` via
    ``load_model()`` (~0.7s/call) because the LIVE console must always see fresh
    edits — but this build fires ~280 GET-only requests (275 node forms + catalog/
    modeling/reporting) against a frozen tree, so that freshness re-read is pure
    waste (~4min of the build). Install an argument-keyed memo over the module
    attributes and ALWAYS restore the originals, so the live app and every other
    caller keep the uncached loaders. Safe because the build path is read-only:
    no route mutates the returned model/lifecycle objects.
    """
    from controlplane import resource_model
    from regression.scenarios import composer, loader

    targets = [(resource_model, "load_model"), (composer, "load_model"),
               (loader, "load_lifecycles")]
    originals = [(mod, name, getattr(mod, name)) for mod, name in targets]

    def _memo(fn):
        cache: dict = {}

        def wrapper(*args, **kwargs):
            key = (args, tuple(sorted(kwargs.items())))
            if key not in cache:
                cache[key] = fn(*args, **kwargs)
            return cache[key]
        return wrapper

    try:
        for mod, name, fn in originals:
            setattr(mod, name, _memo(fn))
        yield
    finally:
        for mod, name, fn in originals:
            setattr(mod, name, fn)


def build() -> Path:
    from fastapi.testclient import TestClient
    from controlplane.app import app

    if OUT.exists():
        shutil.rmtree(OUT)
    OUT.mkdir(parents=True, exist_ok=True)

    htmx = _vendor_htmx_file()  # best-effort: download htmx -> relative sibling
    c = TestClient(app)
    with _model_cache():  # load the resource model once, not once per page
        _build_catalog(c, htmx=htmx)
        _build_modeling(c, htmx=htmx)
        nodes = _build_node_pages(c, htmx=htmx)
        print(f"  node detail pages: {nodes}")
        _build_reporting(c, htmx=htmx)
        _build_renderer(c)
        _build_testing()
        _build_testing_shell(c, htmx=htmx)
    _write_readme()
    return OUT


def _vendor_htmx_file() -> bool:
    """Download htmx once to ``reports/ia-demo/htmx.min.js`` so the offline render
    has no CDN dependency. Best-effort: returns False (leave the CDN URL in the
    HTML) if the download fails — htmx only drives the modeling side-panel, not the
    graph, so a CDN miss never breaks the demo's core."""
    try:
        import urllib.request

        with urllib.request.urlopen(HTMX_CDN, timeout=20) as resp:  # noqa: S310 — fixed CDN URL
            data = resp.read()
        if not data:
            return False
        (OUT / "htmx.min.js").write_bytes(data)
        return True
    except Exception as exc:  # noqa: BLE001 — vendoring is optional
        print(f"  note: htmx not vendored ({exc}); leaving CDN URL (graph still renders)")
        return False


# --------------------------------------------------------------------------- #
# Verification — render the OUTPUT files offline over file:// with Playwright
# (NO server). Asserts each page loads and modeling/reporting draw the SVG DAG
# with no uncaught console errors, and saves a screenshot of each graph page.
# --------------------------------------------------------------------------- #
PW_CHROME = "/opt/pw-browsers/chromium-1194/chrome-linux/chrome"


def verify() -> dict:
    from playwright.sync_api import sync_playwright

    report: dict = {}
    with sync_playwright() as p:
        b = p.chromium.launch(executable_path=PW_CHROME, args=["--no-sandbox"])
        pg = b.new_page()
        errors: list[str] = []
        # capture page-level console errors AND uncaught exceptions per page.
        pg.on("console", lambda m: errors.append(f"{m.type}: {m.text}")
              if m.type in ("error", "warning") else None)
        pg.on("pageerror", lambda e: errors.append(f"pageerror: {e}"))

        # spot-check one node detail page too (the formerly-404 '상세').
        sample_node = sorted(OUT.glob("node-*.html"))
        pages = ["index.html", "modeling.html", "reporting.html"]
        if sample_node:
            pages.append(sample_node[0].name)
        for f in pages:
            errors.clear()
            pg.goto((OUT / f).as_uri())
            pg.wait_for_timeout(2500)
            page_report: dict = {"errors": list(errors)}
            if f == "modeling.html":
                # table-first now: prove the server-rendered rows are present (the
                # graph is a lazy toggle, so rg-units is 0 until clicked — not a fault).
                page_report["rows"] = pg.eval_on_selector_all(
                    "#model-table tbody tr", "els => els.length")
                shot = OUT / "_verify_modeling.png"
                pg.screenshot(path=str(shot))
                page_report["screenshot"] = str(shot)
            elif f == "reporting.html":
                page_report["rg_units"] = pg.eval_on_selector_all(
                    "#cov-svg g.rg-unit", "els => els.length")
                shot = OUT / "_verify_reporting.png"
                pg.screenshot(path=str(shot))
                page_report["screenshot"] = str(shot)
            elif f.startswith("node-"):
                # the recipe form must be present (proves '상세' renders offline).
                page_report["recipe"] = pg.eval_on_selector_all(
                    "section h2", "els => els.some(e => e.textContent.indexOf('전제조건') >= 0)")
            report[f] = page_report
        b.close()
    return report


def main() -> None:
    out = build()
    files = sorted(out.rglob("*"))
    print(f"built IA demo bundle -> {out}")
    for p in files:
        if p.is_file():
            print(f"  {p.relative_to(out)}  ({p.stat().st_size:,} B)")

    print("\nverifying offline (file://, no server) …")
    rep = verify()
    ok = True
    for page, r in rep.items():
        errs = r.get("errors") or []
        # each page proves itself by a different signal: graph pages by rg-units,
        # modeling by table rows, node detail by the recipe form, index by no-errors.
        metric = None
        good = True
        if "rg_units" in r:
            metric = f"rg-units={r['rg_units']}"
            good = r["rg_units"] > 0
        elif "rows" in r:
            metric = f"rows={r['rows']}"
            good = r["rows"] > 0
        elif "recipe" in r:
            metric = f"recipe={r['recipe']}"
            good = bool(r["recipe"])
        extra = f" · {metric}" if metric else ""
        status = "OK" if (not errs and good) else "CHECK"
        if status != "OK":
            ok = False
        print(f"  {page:24s} {status}{extra}" + (f"  errors={errs}" if errs else ""))
    print("verify:", "clean ✅" if ok else "issues ⚠ (see above)")


if __name__ == "__main__":
    main()
