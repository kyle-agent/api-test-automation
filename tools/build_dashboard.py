#!/usr/bin/env python3
"""Render the API-regression dashboard from real run artifacts.

Inputs (all best-effort; the script degrades gracefully when one is missing):
  * framework/api_catalog.json   -> coverage denominators + the reproducible
                                     smoke-tested set (non-mutating GETs w/o path params)
  * reports/smoke_status.tsv      -> per-endpoint real result (status, category, key)
  * reports/junit-crud.xml        -> CRUD lifecycle pass/skip/fail
  * tests/crud/lifecycles.json    -> lifecycle metadata (heavy/light)
  * known_issues.json             -> baseline to split NEW regressions from known-red

Outputs:
  * dashboard/index.html          -> self-contained page (inline SVG/CSS, no CDN)
  * dashboard/history.jsonl       -> one summary row appended per run (for trends)

Coverage is operation-level (method+path), the swagger-coverage / ReadyAPI
convention: an operation is "covered" once it has been called at least once.
"""
from __future__ import annotations
import argparse, json, math, os, time, xml.etree.ElementTree as ET
from collections import Counter, defaultdict


# ----------------------------- data loading -----------------------------
def load_catalog(path):
    cat = json.load(open(path))
    for e in cat:
        e["_norm"] = norm_path(e["http_path"])
    return cat


def norm_path(p):
    """Normalise a path for operation matching: drop query, collapse any
    templated/concrete id segment to '*' (so /v1/x/{id} == /v1/x/regr{unique})."""
    p = p.split("?")[0].strip("/")
    return "/".join("*" if "{" in s else s for s in p.split("/"))


def smoke_tested_keys(cat):
    """Reproduce exactly what the smoke suite calls: GET, no path params,
    non-mutating. Deterministic from the catalog -> real coverage without a run."""
    return {e["key"] for e in cat
            if e["method"] == "GET" and "{" not in e["http_path"]}


def parse_smoke_tsv(path):
    """rows: (status:int, category:str, key:str, method, path). May be absent."""
    rows = []
    if path and os.path.exists(path):
        for ln in open(path):
            p = ln.rstrip("\n").split("\t")
            if len(p) >= 5:
                try:
                    rows.append((int(p[0]), p[1], p[2], p[3], p[4]))
                except ValueError:
                    pass
    return rows


def parse_crud_junit(path):
    """-> {lifecycle_id: 'pass'|'fail'|'skip'}"""
    out = {}
    if not (path and os.path.exists(path)):
        return out
    try:
        root = ET.parse(path).getroot()
    except ET.ParseError:
        return out
    for tc in root.iter("testcase"):
        name = tc.get("name", "")
        lid = name[name.find("[") + 1:name.rfind("]")] if "[" in name else name
        st = "pass"
        if tc.find("failure") is not None or tc.find("error") is not None:
            st = "fail"
        elif tc.find("skipped") is not None:
            st = "skip"
        out[lid] = st
    return out


def crud_write_ops(lifecycles, cat):
    """Distinct catalog non-GET operations exercised by CRUD steps (normalised
    method+path match) -> for write-coverage."""
    cat_by = {(e["method"], e["_norm"]) for e in cat if e["method"] != "GET"}
    hit = set()
    for lc in lifecycles:
        for s in lc.get("steps", []):
            m = s["method"].upper()
            if m == "GET":
                continue
            key = (m, norm_path(s["path"]))
            if key in cat_by:
                hit.add(key)
    return hit


# ----------------------------- computation -----------------------------
def compute(cat, tsv_rows, crud, lifecycles, known):
    total = len(cat)
    cat_total = Counter(e["category"] for e in cat)
    cat_get = Counter(e["category"] for e in cat if e["method"] == "GET")
    get_total = sum(cat_get.values())
    nonget_total = total - get_total

    # tested set: prefer the real tsv keys, else reproduce from catalog
    tested_keys = {r[2] for r in tsv_rows} or smoke_tested_keys(cat)
    key_cat = {e["key"]: e["category"] for e in cat}
    cat_tested = Counter(key_cat.get(k, "?") for k in tested_keys)

    # results (need the tsv; aggregate + per-status)
    dist = Counter(r[0] for r in tsv_rows)
    cats = Counter(r[1] for r in tsv_rows)
    ok, soft = cats.get("ok", 0), cats.get("soft", 0)

    known_keys = {i["key"] for i in known.get("issues", [])}
    new_regressions, known_red = [], []
    for status, category, key, method, path in tsv_rows:
        if category == "fail":
            (known_red if key in known_keys else new_regressions).append(
                (key, status, path))

    write_hit = crud_write_ops(lifecycles, cat)
    cov_op = len(tested_keys) / total * 100 if total else 0
    cov_get = len(tested_keys) / get_total * 100 if get_total else 0
    cov_write = len(write_hit) / nonget_total * 100 if nonget_total else 0

    # crud rollup
    crud_rows = []
    for lc in lifecycles:
        if not lc.get("enabled"):
            continue
        st = crud.get(lc["id"], "skip")
        kind = "heavy" if lc.get("heavy") else "light"
        crud_rows.append((lc["id"], kind, st, len(lc.get("steps", []))))

    return {
        "total": total, "tested": len(tested_keys),
        "cov_op": cov_op, "cov_get": cov_get, "cov_write": cov_write,
        "get_total": get_total, "nonget_total": nonget_total,
        "write_hit": len(write_hit),
        "ok": ok, "soft": soft,
        "fail_new": len(new_regressions), "fail_known": len(known_red),
        "new_regressions": new_regressions, "known_red": known_red,
        "dist": dict(dist),
        "cat_rows": [(c, cat_tested.get(c, 0), cat_get.get(c, 0), cat_total[c])
                     for c in sorted(cat_total, key=lambda x: -cat_total[x])],
        "crud_rows": crud_rows,
        "has_results": bool(tsv_rows),
    }


# ----------------------------- history -----------------------------
def append_history(path, d, run_type, sha):
    row = {
        "ts": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "run_type": run_type, "sha": (sha or "")[:7],
        "ok": d["ok"], "soft": d["soft"],
        "fail_new": d["fail_new"], "fail_known": d["fail_known"],
        "cov_op": round(d["cov_op"], 2), "cov_get": round(d["cov_get"], 2),
        "tested": d["tested"], "total": d["total"],
    }
    hist = []
    if path and os.path.exists(path):
        for ln in open(path):
            ln = ln.strip()
            if ln:
                try:
                    hist.append(json.loads(ln))
                except json.JSONDecodeError:
                    pass
    # only append a row that carries real results (avoid empty CI no-op rows)
    if d["has_results"]:
        hist.append(row)
        if path:
            with open(path, "w") as fh:
                for h in hist:
                    fh.write(json.dumps(h) + "\n")
    return hist


# ----------------------------- rendering -----------------------------
def donut(segments, called, size=180, stroke=34):
    r = (size - stroke) / 2; cx = cy = size / 2; C = 2 * math.pi * r
    tot = sum(v for _, v, _ in segments) or 1
    out = [f'<svg viewBox="0 0 {size} {size}" width="{size}" height="{size}">']
    off = 0
    for _, v, col in segments:
        dash = v / tot * C
        out.append(f'<circle cx="{cx}" cy="{cy}" r="{r}" fill="none" stroke="{col}" '
                   f'stroke-width="{stroke}" stroke-dasharray="{dash:.2f} {C-dash:.2f}" '
                   f'stroke-dashoffset="{-off:.2f}" transform="rotate(-90 {cx} {cy})"/>')
        off += dash
    out.append(f'<text x="{cx}" y="{cy-4}" text-anchor="middle" font-size="26" '
               f'font-weight="700" fill="#1f2328">{called}</text>')
    out.append(f'<text x="{cx}" y="{cy+16}" text-anchor="middle" font-size="11" '
               f'fill="#656d76">calls</text></svg>')
    return "".join(out)


def spark(series, w=520, h=120, color="#0969da"):
    if len(series) < 2:
        return ('<div style="color:#656d76;font-size:13px;padding:30px 0;text-align:center">'
                'collecting… (need ≥2 runs)</div>')
    mx = max(series) or 1; mn = min(series); rng = (mx - mn) or 1; n = len(series); pad = 10
    pts = [(pad + i * (w - 2 * pad) / (n - 1),
            h - pad - (v - mn) / rng * (h - 2 * pad)) for i, v in enumerate(series)]
    poly = " ".join(f"{x:.1f},{y:.1f}" for x, y in pts)
    area = f"{pad},{h-pad} " + poly + f" {w-pad},{h-pad}"
    dots = "".join(f'<circle cx="{x:.1f}" cy="{y:.1f}" r="2.5" fill="{color}"/>' for x, y in pts)
    return (f'<svg viewBox="0 0 {w} {h}" width="100%" height="{h}">'
            f'<polygon points="{area}" fill="{color}1a"/>'
            f'<polyline points="{poly}" fill="none" stroke="{color}" stroke-width="2"/>'
            f'{dots}</svg>')


STATUS_COLORS = {2: "#2da44e", 4: "#bf8700", 5: "#cf222e"}


def render(d, hist, meta):
    called = d["ok"] + d["soft"] + d["fail_new"] + d["fail_known"]
    pass_rate = (d["ok"] / called * 100) if called else 0
    healthy = d["fail_new"] == 0
    badge = ('<span class="badge ok">● HEALTHY — 0 new regressions</span>' if healthy
             else f'<span class="badge bad">● {d["fail_new"]} NEW REGRESSION(S)</span>')

    segs = sorted(((str(c), n, STATUS_COLORS.get(c // 100, "#656d76"))
                   for c, n in d["dist"].items()), key=lambda x: x[0])
    legend = "".join(f'<div class="lg"><span class="dot" style="background:{col}"></span>'
                     f'{code} · {v}</div>' for code, v, col in segs) or \
        '<div class="mut">no smoke results in this run</div>'

    def card(label, value, sub, color):
        return (f'<div class="card"><div class="card-val" style="color:{color}">{value}</div>'
                f'<div class="card-lbl">{label}</div><div class="card-sub">{sub}</div></div>')
    cards = (card("New regressions", str(d["fail_new"]),
                  "vs known baseline", "#2da44e" if healthy else "#cf222e")
             + card("Pass rate", f"{pass_rate:.1f}%", f'{d["ok"]} ok / {called} calls', "#1f2328")
             + card("Operation coverage", f'{d["cov_op"]:.1f}%',
                    f'{d["tested"]} / {d["total"]} ops', "#0969da")
             + card("Known-red", str(d["fail_known"]),
                    "tracked backend bugs", "#cf222e" if d["fail_known"] else "#2da44e"))

    def covbar(c, tested, g, tot):
        pct = (tested / g * 100) if g else 0
        zero = "zero" if tested == 0 else ""
        col = ("#cf222e" if pct == 0 else "#bf8700" if pct < 25
               else "#2da44e" if pct >= 50 else "#0969da")
        return (f'<div class="cb {zero}"><div class="cb-h"><span>{c}</span>'
                f'<span class="cb-n">{tested}/{g} GET · {tot} ops</span></div>'
                f'<div class="cb-track"><div class="cb-fill" style="width:{pct:.0f}%;'
                f'background:{col}"></div></div></div>')
    covbars = "".join(covbar(*r) for r in d["cat_rows"])

    icon = {"pass": "✅", "skip": "🔒", "fail": "⛔"}
    def chip(id_, kind, st, steps):
        label = st
        cls = st
        if st == "skip" and kind == "heavy":
            label = "gated"
        return (f'<div class="crud {cls}"><div class="crud-top">{icon.get(st,"·")} <b>{id_}</b></div>'
                f'<div class="crud-meta"><span class="tag {kind}">{kind}</span> '
                f'{steps} steps · {label}</div></div>')
    crudgrid = "".join(chip(*c) for c in d["crud_rows"])

    knrows = "".join(
        f'<tr><td><code>{k}</code></td><td>{s}</td><td>Product Bug</td></tr>'
        for k, s, _ in d["known_red"]) or \
        '<tr><td colspan="3" class="mut">none</td></tr>'

    pr_series = [h["ok"] / max(1, h["ok"] + h["soft"] + h["fail_new"] + h["fail_known"]) * 100
                 for h in hist[-12:]]
    cov_series = [h["cov_op"] for h in hist[-12:]]

    writeax = "◑" if d["cov_write"] > 0 else "✗"
    return TEMPLATE.format(
        branch=meta["branch"], when=meta["when"], run_type=meta["run_type"],
        badge=badge, cards=cards, donut=donut(segs, called), legend=legend,
        cov_op=f'{d["cov_op"]:.1f}', tested=d["tested"], total=d["total"],
        cov_get=f'{d["cov_get"]:.1f}', get_total=d["get_total"],
        cov_write=f'{d["cov_write"]:.1f}', write_hit=d["write_hit"],
        nonget_total=d["nonget_total"], writeax=writeax,
        covbars=covbars, ok=d["ok"], soft=d["soft"],
        fail_new=d["fail_new"], fail_known=d["fail_known"],
        new_cls="ok" if healthy else "bad",
        crudgrid=crudgrid, knrows=knrows,
        writeax_cls="part" if d["cov_write"] > 0 else "off",
        spark_pr=spark(pr_series, color="#2da44e"),
        spark_cov=spark(cov_series, color="#0969da"),
        runs=len(hist))


TEMPLATE = """<!doctype html><html lang="ko"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>SCP API Regression — Dashboard</title><style>
:root{{--bg:#f6f8fa;--fg:#1f2328;--mut:#656d76;--bd:#d0d7de;--cardbg:#fff}}
*{{box-sizing:border-box}}body{{margin:0;font-family:-apple-system,Segoe UI,Roboto,'Noto Sans KR',sans-serif;background:var(--bg);color:var(--fg)}}
.wrap{{max-width:1080px;margin:0 auto;padding:24px}}
header{{display:flex;justify-content:space-between;align-items:flex-end;flex-wrap:wrap;gap:8px;border-bottom:1px solid var(--bd);padding-bottom:14px}}
h1{{font-size:20px;margin:0}}.mut{{color:var(--mut);font-size:13px}}
.badge{{display:inline-block;padding:2px 10px;border-radius:20px;font-size:12px;font-weight:600}}
.badge.ok{{background:#dafbe1;color:#1a7f37;border:1px solid #2da44e44}}
.badge.bad{{background:#ffebe9;color:#a40e26;border:1px solid #cf222e44}}
section{{margin-top:22px}}h2{{font-size:14px;text-transform:uppercase;letter-spacing:.04em;color:var(--mut);margin:0 0 12px}}
.cards{{display:grid;grid-template-columns:repeat(4,1fr);gap:12px}}
.card{{background:var(--cardbg);border:1px solid var(--bd);border-radius:10px;padding:16px}}
.card-val{{font-size:30px;font-weight:800;line-height:1}}.card-lbl{{font-weight:600;margin-top:6px}}.card-sub{{color:var(--mut);font-size:12px;margin-top:2px}}
.grid2{{display:grid;grid-template-columns:1fr 1.4fr;gap:16px}}
.panel{{background:var(--cardbg);border:1px solid var(--bd);border-radius:10px;padding:16px}}
.donutwrap{{display:flex;gap:16px;align-items:center}}
.lg{{font-size:12px;margin:3px 0;display:flex;align-items:center;gap:6px}}.dot{{width:10px;height:10px;border-radius:50%}}
.axes{{display:flex;gap:8px;flex-wrap:wrap;margin-top:6px}}
.ax{{font-size:11px;padding:3px 8px;border-radius:6px;border:1px solid var(--bd)}}
.ax.on{{background:#dafbe1;border-color:#2da44e55;color:#1a7f37}}.ax.off{{background:#ffebe9;border-color:#cf222e44;color:#a40e26}}.ax.part{{background:#fff8c5;border-color:#d4a72c55;color:#7d4e00}}
.cb{{margin:7px 0}}.cb-h{{display:flex;justify-content:space-between;font-size:12px;margin-bottom:3px}}.cb-n{{color:var(--mut)}}
.cb-track{{height:8px;background:#eaeef2;border-radius:6px;overflow:hidden}}.cb-fill{{height:100%}}
.cb.zero .cb-h span:first-child::after{{content:" ⚠ 사각지대";color:#cf222e;font-size:11px}}
.crudgrid{{display:grid;grid-template-columns:repeat(2,1fr);gap:10px}}
.crud{{border:1px solid var(--bd);border-radius:8px;padding:10px 12px;background:#fff}}
.crud.pass{{border-left:3px solid #2da44e}}.crud.skip{{border-left:3px solid #8250df}}.crud.fail{{border-left:3px solid #cf222e}}
.crud-top{{font-size:13px}}.crud-meta{{font-size:11px;color:var(--mut);margin-top:4px}}
.tag{{font-size:10px;padding:1px 6px;border-radius:10px;font-weight:600}}.tag.light{{background:#ddf4ff;color:#0969da}}.tag.heavy{{background:#fbefff;color:#8250df}}
table{{width:100%;border-collapse:collapse;font-size:13px}}td,th{{text-align:left;padding:6px 8px;border-bottom:1px solid var(--bd)}}th{{color:var(--mut);font-weight:600}}
.bignum{{font-size:34px;font-weight:800}}
.trendgrid{{display:grid;grid-template-columns:1fr 1fr;gap:16px}}
footer{{margin-top:28px;color:var(--mut);font-size:12px;border-top:1px solid var(--bd);padding-top:12px}}
@media(max-width:760px){{.cards,.grid2,.crudgrid,.trendgrid{{grid-template-columns:1fr}}}}
</style></head><body><div class="wrap">
<header><div><h1>SCP API Regression</h1>
<div class="mut">branch <code>{branch}</code> · 최근 실행 {when} · {run_type}</div></div>
<div style="text-align:right">{badge}</div></header>
<section><h2>건강도</h2><div class="cards">{cards}</div></section>
<section><div class="grid2">
<div class="panel"><h2>응답 코드 분포</h2><div class="donutwrap">{donut}<div>{legend}</div></div></div>
<div class="panel"><h2>커버리지</h2>
<div style="display:flex;gap:28px;align-items:baseline">
<div><div class="bignum" style="color:#0969da">{cov_op}%</div><div class="mut">operation ({tested}/{total})</div></div>
<div><div style="font-size:22px;font-weight:700">{cov_get}%</div><div class="mut">읽기 GET ({tested}/{get_total})</div></div>
<div><div style="font-size:22px;font-weight:700">{cov_write}%</div><div class="mut">쓰기 CRUD ({write_hit}/{nonget_total})</div></div></div>
<div class="mut" style="margin-top:12px">측정 축 (swagger-coverage/ReadyAPI 기준)</div>
<div class="axes"><span class="ax on">✓ operation</span><span class="ax part">◑ status-code</span>
<span class="ax {writeax_cls}">{writeax} write/CRUD</span><span class="ax off">✗ parameter</span><span class="ax off">✗ schema</span></div></div>
</div></section>
<section><h2>카테고리별 커버리지 (사각지대 탐지)</h2><div class="panel">{covbars}</div></section>
<section><div class="grid2">
<div class="panel"><h2>실패 분류</h2><table>
<tr><th>분류</th><th>수</th><th>의미</th></tr>
<tr><td>✅ ok (2xx)</td><td>{ok}</td><td>정상</td></tr>
<tr><td>🟡 soft (4xx)</td><td>{soft}</td><td>파라미터/권한/엔타이틀먼트 한계</td></tr>
<tr><td>⛔ new regression</td><td><b class="badge {new_cls}" style="border:0;background:0;padding:0">{fail_new}</b></td><td>새로 깨진 5xx/auth — 알림 대상</td></tr>
<tr><td>🔴 known-red</td><td>{fail_known}</td><td>등록된 백엔드 버그(known_issues)</td></tr></table></div>
<div class="panel"><h2>CRUD 라이프사이클</h2><div class="crudgrid">{crudgrid}</div></div>
</div></section>
<section><h2>추세 <span class="mut" style="text-transform:none">— {runs} runs 누적 (dashboard-data 브랜치)</span></h2>
<div class="panel trendgrid">
<div><div class="mut">성공률</div>{spark_pr}</div>
<div><div class="mut">operation 커버리지 %</div>{spark_cov}</div></div></section>
<section><h2>알려진 이슈 (known_issues.json)</h2><div class="panel"><table>
<tr><th>endpoint</th><th>status</th><th>유형</th></tr>{knrows}</table></div></section>
<footer>생성: <code>tools/build_dashboard.py</code> ← smoke_status.tsv + junit-crud.xml + api_catalog.json
&nbsp;|&nbsp; 추세: <code>dashboard-data</code> 브랜치 <code>history.jsonl</code> &nbsp;|&nbsp; 배포: GitHub Pages</footer>
</div></body></html>"""


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--catalog", default="framework/api_catalog.json")
    ap.add_argument("--tsv", default="reports/smoke_status.tsv")
    ap.add_argument("--crud", default="reports/junit-crud.xml")
    ap.add_argument("--lifecycles", default="tests/crud/lifecycles.json")
    ap.add_argument("--known", default="known_issues.json")
    ap.add_argument("--history", default="dashboard/history.jsonl")
    ap.add_argument("--out", default="dashboard/index.html")
    ap.add_argument("--run-type", default="local")
    ap.add_argument("--sha", default="")
    ap.add_argument("--branch", default=os.environ.get("GITHUB_REF_NAME", "—"))
    args = ap.parse_args()

    cat = load_catalog(args.catalog)
    tsv = parse_smoke_tsv(args.tsv)
    crud = parse_crud_junit(args.crud)
    lifecycles = json.load(open(args.lifecycles)).get("lifecycles", []) \
        if os.path.exists(args.lifecycles) else []
    known = json.load(open(args.known)) if os.path.exists(args.known) else {"issues": []}

    d = compute(cat, tsv, crud, lifecycles, known)
    d["new_regressions"]  # noqa  (kept for clarity)
    hist = append_history(args.history, d, args.run_type, args.sha)

    meta = {"branch": args.branch,
            "when": time.strftime("%Y-%m-%d %H:%M UTC", time.gmtime()),
            "run_type": args.run_type}
    html = render(d, hist, meta)
    # TEMPLATE references writeax_cls; inject it
    html = html.replace("{writeax_cls}", "part" if d["cov_write"] > 0 else "off")
    os.makedirs(os.path.dirname(args.out) or ".", exist_ok=True)
    open(args.out, "w").write(html)
    print(f"dashboard -> {args.out}  (coverage {d['cov_op']:.1f}% op, "
          f"{d['cov_get']:.1f}% GET, {d['cov_write']:.1f}% write; "
          f"ok {d['ok']} soft {d['soft']} new {d['fail_new']} known {d['fail_known']}; "
          f"{len(hist)} history rows)")


if __name__ == "__main__":
    main()
