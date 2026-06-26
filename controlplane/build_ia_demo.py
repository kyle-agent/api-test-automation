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

import shutil
import warnings
from pathlib import Path

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
        ('href="/testing/embed"', 'href="testing/index.html"'),
        ('href="/reporting/coverage"', 'href="reporting.html"'),
        ('href="/catalog"', 'href="index.html"'),
        ('class="brand" href="/"', 'class="brand" href="index.html"'),
        # --- links with no offline target -> '#' (kept clickable, no 404) ---
        ('href="/knowledge"', 'href="#"'),
        ('href="/dashboard/index.html"', 'href="#"'),
        # --- Plan stepper sub-nav (_plan_steps.html, modeling page) ---
        ('href="/planning?step=catalog"', 'href="index.html"'),
        ('href="/planning?step=model"', 'href="modeling.html"'),
        ('href="/planning?step=compose"', 'href="#"'),
        ('href="/planning/validate"', 'href="#"'),
        # --- modeling map page's "목록 보기 →" link to the (offline-absent) list ---
        ('href="/planning/resources"', 'href="#"'),
    ]
    for a, b in repl:
        html = html.replace(a, b)
    return html


def _rewrite_recipe_links(html: str) -> str:
    """Catalog only: the per-service ``✏️ 레시피 편집 →`` deep-links point at the
    Modeling node-edit form (``/planning/resources/<node_id>``), which has no
    offline page. Land them on the Modeling map instead. Done as a blunt prefix
    replace AFTER ``_nav_rewrite`` has already fixed the exact ``…/map`` nav link,
    so only the remaining node deep-links are caught."""
    return html.replace('href="/planning/resources/', 'href="modeling.html" data-offline-recipe="')


def _build_catalog(c, *, htmx: bool) -> None:
    """① Catalog — pure server-rendered (no fetch). nav-rewrite + recipe-link
    rewrite + htmx + banner. Written as BOTH catalog.html and index.html (landing)."""
    html = c.get("/catalog").text
    html = _nav_rewrite(html)
    html = _rewrite_recipe_links(html)
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
    html = _inject_banner(html)
    # node-click opens an iframe to /planning/resources/<id> (the edit form), which
    # does not exist offline -> the side panel shows the iframe's 404. We leave the
    # click wired (graph focus still works); noted in the report + README.
    (OUT / "modeling.html").write_text(html, encoding="utf-8")


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
        "- **Testing** (`testing/`): the console2 static bundle "
        "(`console2/build_static.py`), which keeps its own chrome.\n"
        "- **Reporting** (`reporting.html`): fetches `reporting.map.json`, same "
        "shared renderer.\n\n"
        "## Publish\n"
        "Copy this directory to the Pages branch: `dashboard-data:/ia-demo/`.\n\n"
        "## Files\n" + "".join(f"- `{f}`\n" for f in files),
        encoding="utf-8",
    )


def build() -> Path:
    from fastapi.testclient import TestClient
    from controlplane.app import app

    if OUT.exists():
        shutil.rmtree(OUT)
    OUT.mkdir(parents=True, exist_ok=True)

    htmx = _vendor_htmx_file()  # best-effort: download htmx -> relative sibling
    c = TestClient(app)
    _build_catalog(c, htmx=htmx)
    _build_modeling(c, htmx=htmx)
    _build_reporting(c, htmx=htmx)
    _build_renderer(c)
    _build_testing()
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

        for f in ["index.html", "modeling.html", "reporting.html"]:
            errors.clear()
            pg.goto((OUT / f).as_uri())
            pg.wait_for_timeout(2500)
            page_report: dict = {"errors": list(errors)}
            # the graph pages must have drawn SVG node groups (.rg-unit) from the
            # relative *.json + renderer; a count > 0 proves the fetch+render path.
            if f in ("modeling.html", "reporting.html"):
                svg_id = "#map-svg" if f == "modeling.html" else "#cov-svg"
                page_report["rg_units"] = pg.eval_on_selector_all(
                    f"{svg_id} g.rg-unit", "els => els.length")
                shot = OUT / ("_verify_" + f.replace(".html", "") + ".png")
                pg.screenshot(path=str(shot))
                page_report["screenshot"] = str(shot)
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
        units = r.get("rg_units")
        extra = f" · rg-units={units}" if units is not None else ""
        status = "OK" if not errs and (units is None or units > 0) else "CHECK"
        if status != "OK":
            ok = False
        print(f"  {page:16s} {status}{extra}"
              + (f"  errors={errs}" if errs else ""))
    print("verify:", "clean ✅" if ok else "issues ⚠ (see above)")


if __name__ == "__main__":
    main()
