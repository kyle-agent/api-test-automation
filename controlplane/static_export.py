"""Static export of the platform's read-only views for GitHub Pages.

The full platform needs a live server (DB, dispatch, S3, AI), but the
Planning-area views and the coverage home are pure functions of repo files —
so the dashboard CI job renders them through the real FastAPI app (TestClient)
and publishes the HTML next to the existing dashboard on the dashboard-data
branch:  https://<pages>/platform/

View-only by design: action buttons/forms stay visible but a banner explains
they need the live server; links to dynamic routes are neutralized.

CLI:
  python -m controlplane.static_export --out reports/platform-static
"""
from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

# route -> output file (also used to rewrite internal links)
PAGES = {
    "/": "index.html",
    "/planning": "planning.html",
    "/planning/scenarios": "scenarios.html",
    "/planning/dependencies": "dependencies.html",
    "/planning/knowledge": "knowledge.html",
}

BANNER = (
    '<div style="background:#fff3cd;border-bottom:1px solid #e8d9a0;'
    'padding:6px 24px;font-size:13px">📄 <b>정적 뷰</b> — GitHub Pages 발행본'
    '입니다. 실행·편집·개입 버튼은 플랫폼 서버(uvicorn)에서만 동작합니다. '
    '<a href="../index.html">현재 대시보드</a> · '
    '<a href="../ops.html">ops 뷰어</a></div>')

# dynamic routes that make no sense on Pages — neutralize their links
_DEAD_PREFIXES = ("/testing", "/reporting", "/runs", "/ai", "/planning/edit",
                  "/schedules", "/partials")


def _file_views() -> dict[str, str]:
    """planning/view targets (knowledge, suites, environments) -> static names."""
    out = {}
    for pattern in ("knowledge/*.md", "knowledge/formal/*.yaml",
                    "knowledge/formal/*.md", "knowledge/formal/services/*.yaml",
                    "suites/*.yaml", "environments/*.yaml"):
        for p in sorted(ROOT.glob(pattern)):
            rel = p.relative_to(ROOT).as_posix()
            out[rel] = "view/" + rel.replace("/", "__") + ".html"
    return out


def _rewrite(html: str, views: dict[str, str], depth: int = 0) -> str:
    """Rewrite live-server links to the static file layout."""
    up = "../" * depth
    # file viewer links (do these BEFORE the plain-route pass)
    def view_sub(m):
        rel = m.group(1)
        target = views.get(rel)
        return f'href="{up}{target}"' if target else 'href="#"'
    html = re.sub(r'href="/planning/view\?path=([^"&]+)"', view_sub, html)
    # scenario service filter (query forms/links) — drop to the full list
    html = re.sub(r'href="/planning/scenarios\?[^"]*"',
                  f'href="{up}scenarios.html"', html)
    for route, fname in sorted(PAGES.items(), key=lambda kv: -len(kv[0])):
        html = html.replace(f'href="{route}"', f'href="{up}{fname}"')
    # the in-platform dashboard proxy -> the Pages root copies
    html = html.replace('href="/dashboard/index.html"', f'href="{up}../index.html"')
    html = html.replace('href="/dashboard/ops.html"', f'href="{up}../ops.html"')
    for prefix in _DEAD_PREFIXES:
        html = re.sub(r'href="' + re.escape(prefix) + r'[^"]*"', 'href="#"', html)
    # banner after the header
    html = html.replace("</header>", "</header>" + BANNER, 1)
    return html


def export(out_dir: str) -> int:
    from fastapi.testclient import TestClient
    from controlplane.app import app

    out = Path(out_dir)
    (out / "view").mkdir(parents=True, exist_ok=True)
    views = _file_views()
    written = 0
    with TestClient(app) as client:
        for route, fname in PAGES.items():
            resp = client.get(route)
            if resp.status_code != 200:
                print(f"[static-export] skip {route}: HTTP {resp.status_code}")
                continue
            (out / fname).write_text(_rewrite(resp.text, views), encoding="utf-8")
            written += 1
        for rel, fname in views.items():
            resp = client.get("/planning/view", params={"path": rel})
            if resp.status_code != 200:
                continue
            (out / fname).write_text(_rewrite(resp.text, views, depth=1),
                                     encoding="utf-8")
            written += 1
    print(f"[static-export] wrote {written} page(s) -> {out}")
    return written


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="static platform views for Pages")
    ap.add_argument("--out", default="reports/platform-static")
    args = ap.parse_args(argv)
    export(args.out)
    return 0  # best-effort: never fail the dashboard job


if __name__ == "__main__":
    sys.exit(main())
