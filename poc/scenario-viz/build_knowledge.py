#!/usr/bin/env python3
"""Static Knowledge VIEWER generator for the SCP API Regression Platform preview console.

Run from the repo root:

    pip install -q markdown
    PYTHONPATH=. python3 poc/scenario-viz/build_knowledge.py

Collects knowledge files (md / yaml / json), renders each to a self-contained
page under ``poc/scenario-viz/kdocs/<slug>.html``, and emits an index
(``poc/scenario-viz/data/kindex.js`` -> ``window.KINDEX``) consumed by
``knowledge.html``. Pure static, file://-safe (no fetch). Tolerant: a file that
fails to render becomes a raw <pre> page rather than crashing the build.
"""
from __future__ import annotations

import glob
import html
import os
import sys

# --- locate repo root (this file lives at <root>/poc/scenario-viz/) ---------
HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.path.join(HERE, "..", ".."))
KDOCS = os.path.join(HERE, "kdocs")
DATA = os.path.join(HERE, "data")

# --- optional markdown renderer ---------------------------------------------
try:
    import markdown as _markdown  # type: ignore

    def render_md(text: str) -> str:
        return _markdown.markdown(
            text,
            extensions=["extra", "tables", "fenced_code", "sane_lists"],
        )

    MD_OK = True
except Exception:  # ImportError or any init failure -> fall back
    def render_md(text: str) -> str:
        return '<pre class="raw">' + html.escape(text) + "</pre>"

    MD_OK = False


def render_raw(text: str) -> str:
    return '<pre class="raw">' + html.escape(text) + "</pre>"


# --- source collection ------------------------------------------------------
# Each entry: (group label, list of repo-relative file paths). Order preserved.
def collect():
    def rel(p):
        return os.path.relpath(p, ROOT).replace(os.sep, "/")

    def g(*patterns):
        out = []
        for pat in patterns:
            out += sorted(glob.glob(os.path.join(ROOT, pat)))
        return [rel(p) for p in out if os.path.isfile(p)]

    sources = [
        ("knowledge/", g("knowledge/*.md")),
        ("knowledge/formal/", g("knowledge/formal/*.md")),
        ("knowledge/formal/resources/", g("knowledge/formal/resources/*.yaml")),
        ("knowledge/formal/services/", g("knowledge/formal/services/*.yaml")),
        (
            "suites/ + environments/",
            g(
                "suites/*.yaml", "suites/*.yml", "suites/*.json",
                "environments/*.yaml", "environments/*.yml", "environments/*.json",
            ),
        ),
    ]
    # drop empty groups
    return [(label, files) for label, files in sources if files]


def slug_for(relpath: str) -> str:
    return relpath.replace("/", "__").replace(".", "-")


def kind_for(relpath: str) -> str:
    ext = relpath.rsplit(".", 1)[-1].lower()
    if ext == "md":
        return "md"
    if ext in ("yaml", "yml"):
        return "yaml"
    if ext == "json":
        return "json"
    return ext


PAGE = """<!doctype html><html lang="ko"><head>
<meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>{title} — Knowledge</title>
<link rel="stylesheet" href="../assets/style.css">
<script src="../data/context.js"></script>
<style>
.ctxbar{{display:flex;gap:14px;flex-wrap:wrap;align-items:center;background:var(--panel2);
  border:1px solid var(--line);border-radius:9px;padding:7px 12px;margin:0 0 16px;font-size:12px;color:var(--muted)}}
.ctxbar .seg b{{color:var(--ink)}} .ctxbar .badge{{margin-left:auto;border-radius:12px;padding:2px 9px;font-weight:600}}
.ctxbar .snap{{background:#fdf3e2;border:1px solid #ecd09a;color:#b5740b}}
.kpath{{font-family:ui-monospace,Menlo,Consolas,monospace;color:var(--accent2);font-size:13px;margin:2px 0 14px}}
.kbody{{color:var(--ink);line-height:1.6;font-size:14px}}
.kbody h1,.kbody h2,.kbody h3,.kbody h4{{color:var(--ink);border-bottom:1px solid var(--line);padding-bottom:4px;margin:22px 0 10px}}
.kbody h1{{font-size:22px}} .kbody h2{{font-size:19px}} .kbody h3{{font-size:16px}} .kbody h4{{font-size:14px}}
.kbody p,.kbody li{{color:var(--ink)}}
.kbody a{{color:var(--accent)}}
.kbody code{{font-family:ui-monospace,Menlo,Consolas,monospace;background:var(--panel2);
  border:1px solid var(--line);border-radius:4px;padding:1px 5px;font-size:12.5px;color:var(--accent2)}}
.kbody pre{{background:#f5f7f9;border:1px solid var(--line);border-radius:8px;padding:12px 14px;overflow:auto}}
.kbody pre code{{background:none;border:0;padding:0;color:var(--ink)}}
.kbody pre.raw{{white-space:pre;color:var(--ink);font-size:12.5px}}
.kbody table{{border-collapse:collapse;margin:12px 0;font-size:13px}}
.kbody th,.kbody td{{border:1px solid var(--line);padding:6px 10px;text-align:left}}
.kbody th{{background:var(--panel2);color:var(--ink)}}
.kbody blockquote{{border-left:3px solid var(--line);margin:10px 0;padding:2px 14px;color:var(--muted)}}
.kbody ul,.kbody ol{{padding-left:22px}}
</style>
</head><body><div class="wrap">
<header class="top">
  <h1>SCP API Regression Platform · Knowledge</h1>
  <span class="crumbs">{path}</span>
  <nav class="nav">
    <a href="../../index.html">📊 대시보드</a><a href="../console.html">🧩 콘솔</a>
    <a href="../knowledge.html">📚 Knowledge</a>
  </nav>
</header>

<div class="ctxbar" id="ctxbar"></div>

<div class="panel">
  <div class="kpath">{path}</div>
  <div class="kbody">{body}</div>
</div>

<div class="foot">생성됨: <code>poc/scenario-viz/build_knowledge.py</code> · 원본: <code>{path}</code></div>
</div>
<script>
const CTX=window.CTX||{{env:"?",suite:"?",sha:"sample",mode:"SNAPSHOT"}};
function esc(s){{return (s+"").replace(/&/g,"&amp;").replace(/</g,"&lt;");}}
const live=(CTX.mode||"").toUpperCase()==="LIVE";
document.getElementById("ctxbar").innerHTML=
  `<span class="seg">env <b>${{esc(CTX.env)}}</b></span><span class="seg">× suite <b>${{esc(CTX.suite)}}</b></span>`+
  `<span class="seg">· sha <b>${{esc(CTX.sha)}}</b></span>`+(CTX.time?`<span class="seg">· ${{esc(CTX.time)}}</span>`:"")+
  `<span class="badge ${{live?'':'snap'}}">${{live?'LIVE':'SNAPSHOT'}}</span>`;
</script>
</body></html>
"""


def main() -> int:
    os.makedirs(KDOCS, exist_ok=True)
    os.makedirs(DATA, exist_ok=True)

    sources = collect()
    index = []  # {group, path, slug, title, kind}
    counts = {"md": 0, "yaml": 0, "json": 0}

    for group, files in sources:
        for relpath in files:
            slug = slug_for(relpath)
            kind = kind_for(relpath)
            title = os.path.basename(relpath)
            abspath = os.path.join(ROOT, relpath)
            try:
                with open(abspath, "r", encoding="utf-8") as fh:
                    text = fh.read()
                body = render_md(text) if kind == "md" else render_raw(text)
            except Exception as exc:  # never crash the build
                body = render_raw("[failed to read/render: %s]\n" % exc)
            page = PAGE.format(
                title=html.escape(title),
                path=html.escape(relpath),
                body=body,
            )
            with open(os.path.join(KDOCS, slug + ".html"), "w", encoding="utf-8") as out:
                out.write(page)
            index.append(
                {"group": group, "path": relpath, "slug": slug, "title": title, "kind": kind}
            )
            counts[kind] = counts.get(kind, 0) + 1

    # emit kindex.js
    import json

    with open(os.path.join(DATA, "kindex.js"), "w", encoding="utf-8") as out:
        out.write("// AUTO-GENERATED by poc/scenario-viz/build_knowledge.py — do not hand-edit.\n")
        out.write("window.KINDEX = " + json.dumps(index, ensure_ascii=False) + ";\n")

    total = len(index)
    by_kind = ", ".join("%s %d" % (k, v) for k, v in counts.items() if v)
    print(
        "build_knowledge: %d files rendered (%s) · markdown=%s · kdocs/ + data/kindex.js"
        % (total, by_kind, "on" if MD_OK else "FALLBACK<pre>")
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
