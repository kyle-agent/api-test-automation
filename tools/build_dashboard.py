#!/usr/bin/env python3
"""Render the API-regression dashboard from real run artifacts.

Inputs (all best-effort; the script degrades gracefully when one is missing):
  * data/api_catalog.json   -> coverage denominators + the reproducible
                                     smoke-tested set (non-mutating GETs w/o path params)
  * reports/smoke_status.tsv      -> per-endpoint real result (status, category, key)
  * reports/junit-crud.xml        -> CRUD lifecycle pass/skip/fail
  * tests/crud/lifecycles.json    -> lifecycle metadata (heavy/light)
  * data/baselines/known_issues.json             -> baseline to split NEW regressions from known-red

Outputs:
  * dashboard/index.html          -> self-contained page (inline SVG/CSS, no CDN)
  * dashboard/history.jsonl       -> one summary row appended per run (for trends)

Coverage is operation-level (method+path), the swagger-coverage / ReadyAPI
convention: an operation is "covered" once it has been called at least once.
"""
from __future__ import annotations
import argparse, html, json, math, os, time, xml.etree.ElementTree as ET
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
    """rows: (status:int, category:str, key:str, method, path, elapsed_ms:float|None).
    The 6th column (response time) is optional and may be absent on older rows."""
    rows = []
    if path and os.path.exists(path):
        for ln in open(path):
            p = ln.rstrip("\n").split("\t")
            if len(p) >= 5:
                try:
                    ems = float(p[5]) if len(p) >= 6 and p[5] != "" else None
                    rows.append((int(p[0]), p[1], p[2], p[3], p[4], ems))
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
    """Distinct catalog non-GET operations exercised by ENABLED CRUD lifecycles
    (normalised method+path match) -> for write-coverage. Disabled lifecycles
    never run, so they must not inflate the number."""
    cat_by = {(e["method"], e["_norm"]) for e in cat if e["method"] != "GET"}
    hit = set()
    for lc in lifecycles:
        if not lc.get("enabled"):
            continue
        for s in lc.get("steps", []):
            if not s.get("method") or not s.get("path"):
                continue  # e.g. probe-reads steps carry no single method/path
            m = s["method"].upper()
            if m == "GET":
                continue
            key = (m, norm_path(s["path"]))
            if key in cat_by:
                hit.add(key)
    return hit


def slug(category, service):
    """Filesystem/URL-safe id for a service's drill-down page."""
    return f"{category}__{service}".replace("/", "-").replace(" ", "-")


def per_service(cat, tsv_rows, write_hit):
    """Group every catalog operation by (category, service) and mark each as
    covered: a GET is covered once its key was actually called (smoke OR a CRUD
    probe-read, both recorded in the smoke tsv); a write op is covered once a
    lifecycle step exercised its method+normalised-path. Returns a list of
    service dicts (sorted by category, then ascending coverage so blind spots
    surface first) for the index nav and the per-service pages."""
    called = {}                       # key -> (last observed status, elapsed_ms)
    for status, _category, key, _method, _path, *_rest in tsv_rows:
        called[key] = (status, _rest[0] if _rest else None)
    tested_keys = set(called) or smoke_tested_keys(cat)

    groups = defaultdict(list)
    for e in cat:
        groups[(e["category"], e["service"])].append(e)

    services = []
    for (category, service), ents in groups.items():
        rows, covn = [], 0
        gtot = gcov = wtot = wcov = 0
        for e in sorted(ents, key=lambda x: (x["method"], x["_norm"])):
            if e["method"] == "GET":
                gtot += 1
                covered = e["key"] in tested_keys
                gcov += covered
            else:
                wtot += 1
                covered = (e["method"], e["_norm"]) in write_hit
                wcov += covered
            covn += covered
            st_el = called.get(e["key"]) or (None, None)
            rows.append((e["method"], e["http_path"], e.get("name", ""),
                         bool(covered), st_el[0], st_el[1]))
        services.append({
            "category": category, "service": service, "slug": slug(category, service),
            "total": len(ents), "covered": covn,
            "gtot": gtot, "gcov": gcov, "wtot": wtot, "wcov": wcov, "rows": rows})
    services.sort(key=lambda s: (s["category"], s["covered"] / (s["total"] or 1), s["service"]))
    return services


# ----------------------------- computation -----------------------------
def compute(cat, tsv_rows, crud, lifecycles, known, param_rows=()):
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
    for status, category, key, method, path, *_rest in tsv_rows:
        if category == "fail":
            (known_red if key in known_keys else new_regressions).append(
                (key, status, path))

    write_hit = crud_write_ops(lifecycles, cat)
    cov_op = len(tested_keys) / total * 100 if total else 0
    cov_get = len(tested_keys) / get_total * 100 if get_total else 0
    cov_write = len(write_hit) / nonget_total * 100 if nonget_total else 0

    # parameter coverage: OK GETs re-issued with pagination params (param_status.tsv)
    param_attempted = len(param_rows)
    param_accepted = sum(1 for r in param_rows if 200 <= r[0] < 300)
    cov_param = param_accepted / param_attempted * 100 if param_attempted else 0

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
        "cov_param": cov_param, "param_attempted": param_attempted,
        "param_accepted": param_accepted,
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


def cov_color(pct):
    return ("#cf222e" if pct == 0 else "#bf8700" if pct < 25
            else "#2da44e" if pct >= 50 else "#0969da")


def render_services_nav(services):
    """Index drill-down: services grouped by category (each links to its page),
    sorted with the lowest-coverage services first so blind spots stand out."""
    by_cat = defaultdict(list)
    for s in services:
        by_cat[s["category"]].append(s)
    parts = []
    for category in sorted(by_cat):
        svs = by_cat[category]
        ctot = sum(s["total"] for s in svs) or 1
        ccov = sum(s["covered"] for s in svs)
        cards = []
        for s in svs:
            pct = s["covered"] / s["total"] * 100 if s["total"] else 0
            cards.append(
                f'<a class="svc" href="services/{s["slug"]}.html">'
                f'<div class="svc-n">{html.escape(s["service"])}</div>'
                f'<div class="svc-bar"><div style="width:{pct:.0f}%;background:{cov_color(pct)}"></div></div>'
                f'<div class="svc-m">{s["covered"]}/{s["total"]} ops · {pct:.0f}%</div></a>')
        parts.append(
            f'<div class="svc-cat"><h3>{html.escape(category)} '
            f'<span class="mut">{ccov}/{sum(s["total"] for s in svs)} ops</span></h3>'
            f'<div class="svc-grid">{"".join(cards)}</div></div>')
    return "".join(parts)


SVC_CSS = """
:root{--bg:#f6f8fa;--fg:#1f2328;--mut:#656d76;--bd:#d0d7de;--cardbg:#fff}
*{box-sizing:border-box}body{margin:0;font-family:-apple-system,Segoe UI,Roboto,'Noto Sans KR',sans-serif;background:var(--bg);color:var(--fg)}
.wrap{max-width:1080px;margin:0 auto;padding:24px}
.bc{font-size:13px;margin-bottom:10px}.bc a{color:#0969da;text-decoration:none}
h1{font-size:22px;margin:0 0 4px}h1 .mut{font-size:13px;font-weight:400;text-transform:uppercase;letter-spacing:.04em}
.mut{color:var(--mut);font-size:13px}
.sum{background:var(--cardbg);border:1px solid var(--bd);border-radius:10px;padding:16px;margin:14px 0 18px}
.s-big{font-size:34px;font-weight:800;color:#0969da;line-height:1}.s-sub{font-size:13px;color:var(--mut);margin-top:8px}
table{width:100%;border-collapse:collapse;font-size:13px;background:var(--cardbg);border:1px solid var(--bd);border-radius:10px;overflow:hidden}
th,td{text-align:left;padding:7px 10px;border-bottom:1px solid var(--bd)}th{color:var(--mut);font-weight:600;background:#f6f8fa}
tr.unc{background:#fcfcfd;color:#8a929b}tr.unc code{color:#8a929b}
code{font-family:ui-monospace,SFMono-Regular,Menlo,monospace;font-size:12px}
.ti{color:var(--mut)}
.m{font-size:10px;font-weight:700;padding:1px 6px;border-radius:5px;color:#fff}
.m-GET{background:#1a7f37}.m-POST{background:#0969da}.m-PUT{background:#9a6700}.m-DELETE{background:#cf222e}.m-PATCH{background:#8250df}
footer{margin-top:18px;font-size:12px}
"""


# ---- design/behavior conformance (separate dimension from the HTTP result) ---
# IMPORTANT: this is NOT the regression pass/fail. The "최근 status" column is
# whether the API answered the test call; this is a SEPARATE design-defect audit.
_CONF_WORD = {"red": "결함", "yellow": "개선", "green": "정상"}
_CONF_STYLE = {
    "red": "background:#ffebe9;color:#cf222e;border:1px solid #ff818266",
    "yellow": "background:#fff8c5;color:#9a6700;border:1px solid #d4a72c66",
    "green": "background:#eaeef2;color:#656d76;border:1px solid #d0d7de",
}


def conf_chip(status, n):
    word = _CONF_WORD.get(status, "—")
    txt = word + (f" {n}" if n else "")
    return (f'<span style="display:inline-block;padding:1px 9px;border-radius:10px;'
            f'font-size:12px;font-weight:600;white-space:nowrap;{_CONF_STYLE.get(status, "")}">{txt}</span>')


def conf_cell(rec):
    its = rec.get("items", [])
    chip = conf_chip(rec.get("status", "green"), len(its))
    if not its:
        return chip
    lis = "".join(
        f'<li><b>{html.escape(i["type"])}</b> '
        f'<span class="mut">[{i["src"]} · #{i["issue"]}]</span><br>{html.escape(i["detail"])}</li>'
        for i in its)
    return (f'<details><summary style="cursor:pointer">{chip}</summary>'
            f'<ul style="margin:6px 0 0;padding-left:18px;font-size:12px;line-height:1.5">{lis}</ul>'
            f'</details>')


def render_conformance_section(conf):
    """Top-of-dashboard panel: design-defect summary (kept visually distinct from
    the regression stats) + the platform-wide systemic findings banner."""
    s = conf.get("summary", {})
    if not s:
        return ""
    g, y, r, tot = s.get("green", 0), s.get("yellow", 0), s.get("red", 0), s.get("total", 0)
    cells = (f'{conf_chip("red", r)} &nbsp; {conf_chip("yellow", y)} &nbsp; {conf_chip("green", g)}')
    sys_rows = "".join(
        f'<tr><td>{conf_chip("yellow", it.get("count") or "")}</td>'
        f'<td><b>{html.escape(it["type"])}</b> <span class="mut">#{it["issue"]} · {html.escape(it["scope"])}</span><br>'
        f'{html.escape(it["detail"])}</td></tr>'
        for it in conf.get("systemic", []))
    return (
        '<section><h2>설계/동작 정합성 <span class="mut" style="text-transform:none">— '
        '회귀(호출 성공 여부)와 별개로, 정적+런타임 점검에서 찾은 설계·구현 결함</span></h2>'
        '<div class="panel">'
        f'<p style="margin:0 0 8px">API별 판정: {cells} '
        f'<span class="mut">(총 {tot}개 · "결함"=계약 위반 구현버그, "개선"=설계/문서 결함, "정상"=API별 고유 이슈 없음)</span></p>'
        '<p class="mut" style="margin:0 0 10px">※ "정상"이어도 아래 <b>플랫폼 전역 항목</b>은 공통 적용됩니다. '
        '서비스 클릭 → API별 <b>설계/동작 결함</b> 열에서 상세 확인.</p>'
        '<table><thead><tr><th>건수</th><th>플랫폼 전역 점검 항목 (모든 서비스 공통)</th></tr></thead>'
        f'<tbody>{sys_rows}</tbody></table></div></section>')


def render_service_page(s, meta):
    pct = s["covered"] / s["total"] * 100 if s["total"] else 0
    gpct = s["gcov"] / s["gtot"] * 100 if s["gtot"] else 0
    wpct = s["wcov"] / s["wtot"] * 100 if s["wtot"] else 0

    def fmt_ms(el):
        if el is None:
            return ""
        txt = f"{el / 1000:.1f}s" if el >= 1000 else f"{el:.0f}ms"
        # surface slow calls (esp. POST creates): amber >3s, red >10s
        col = "#cf222e" if el >= 10000 else "#9a6700" if el >= 3000 else "#8c959f"
        return f' <span style="font-size:11px;color:{col}">· {txt}</span>'

    def statcell(st, el=None):
        if st is None:
            return '<span class="mut">—</span>'
        col = STATUS_COLORS.get(st // 100, "#656d76")
        return f'<b style="color:{col}">{st}</b>{fmt_ms(el)}'

    conf = (meta.get("conf") or {}).get("by_endpoint", {})
    rows = []
    for method, path, title, covered, st, el in s["rows"]:
        chk = ('<span style="color:#2da44e">✓</span>' if covered
               else '<span class="mut">·</span>')
        rcls = "" if covered else ' class="unc"'
        key = f'{s["category"]}/{s["service"]}/{title}'
        cc = conf_cell(conf.get(key, {"status": "green", "items": []}))
        rows.append(
            f'<tr{rcls}><td><span class="m m-{method}">{method}</span></td>'
            f'<td><code>{html.escape(path)}</code></td>'
            f'<td class="ti">{html.escape(title or "")}</td>'
            f'<td style="text-align:center">{chk}</td><td>{statcell(st, el)}</td>'
            f'<td>{cc}</td></tr>')
    svc = html.escape(s["service"]); category = html.escape(s["category"])
    return (
        f'<!doctype html><html lang="ko"><head><meta charset="utf-8">'
        f'<meta name="viewport" content="width=device-width,initial-scale=1">'
        f'<title>{category}/{svc} — coverage</title><style>{SVC_CSS}</style></head>'
        f'<body><div class="wrap">'
        f'<div class="bc"><a href="../index.html">← 대시보드</a> &nbsp;/&nbsp; {category} &nbsp;/&nbsp; <b>{svc}</b></div>'
        f'<h1>{svc} <span class="mut">{category}</span></h1>'
        f'<div class="sum"><div class="s-big">{pct:.0f}%</div>'
        f'<div class="mut">operation 커버 ({s["covered"]}/{s["total"]})</div>'
        f'<div class="s-sub">읽기 GET {s["gcov"]}/{s["gtot"]} ({gpct:.0f}%) '
        f'&nbsp;·&nbsp; 쓰기 {s["wcov"]}/{s["wtot"]} ({wpct:.0f}%)</div></div>'
        f'<p class="mut" style="margin:4px 0 10px">컬럼 구분 — <b>최근 status</b>: 회귀 테스트의 '
        f'실제 호출 응답(테스트 수행 성공 여부) · <b>설계/동작 결함</b>: 호출 성공 여부와 별개로 '
        f'정적+런타임 점검에서 찾은 개선/결함(클릭 시 상세). 빈칸 status는 미호출(커버 X).</p>'
        f'<table><thead><tr><th>메서드</th><th>경로</th><th>API</th>'
        f'<th>커버</th><th>최근 status<br><span class="mut" style="font-weight:400">회귀 호출결과 · 응답시간</span></th>'
        f'<th>설계/동작 결함<br><span class="mut" style="font-weight:400">별도 점검</span></th>'
        f'</tr></thead><tbody>{"".join(rows)}</tbody></table>'
        f'<footer class="mut">생성 {meta["when"]} · branch <code>{html.escape(meta["branch"])}</code>'
        f' · 커버 기준: GET=실제 호출(smoke 또는 CRUD probe), 쓰기=CRUD 스텝이 해당 method+path 실행</footer>'
        f'</div></body></html>')


def render(d, hist, meta, services):
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
    measured = d.get("param_attempted", 0) > 0
    paramax = "◑" if measured else "✗"
    param_stat = (f'<div><div style="font-size:22px;font-weight:700">{d["cov_param"]:.0f}%</div>'
                  f'<div class="mut">파라미터 ({d["param_accepted"]}/{d["param_attempted"]})</div></div>'
                  ) if measured else ""
    return TEMPLATE.format(
        branch=meta["branch"], when=meta["when"], run_type=meta["run_type"],
        services_nav=render_services_nav(services),
        conformance_section=render_conformance_section(meta.get("conf", {})),
        badge=badge, cards=cards, donut=donut(segs, called), legend=legend,
        cov_op=f'{d["cov_op"]:.1f}', tested=d["tested"], total=d["total"],
        cov_get=f'{d["cov_get"]:.1f}', get_total=d["get_total"],
        cov_write=f'{d["cov_write"]:.1f}', write_hit=d["write_hit"],
        nonget_total=d["nonget_total"], writeax=writeax,
        paramax=paramax, paramax_cls="part" if measured else "off", param_stat=param_stat,
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
.svc-cat{{margin:16px 0}}.svc-cat h3{{font-size:13px;margin:0 0 8px}}
.svc-grid{{display:grid;grid-template-columns:repeat(auto-fill,minmax(190px,1fr));gap:10px}}
.svc{{display:block;text-decoration:none;color:inherit;background:var(--cardbg);border:1px solid var(--bd);border-radius:8px;padding:10px 12px}}
.svc:hover{{border-color:#0969da;box-shadow:0 1px 5px #0969da22}}
.svc-n{{font-weight:600;font-size:13px;margin-bottom:6px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}}
.svc-bar{{height:6px;background:#eaeef2;border-radius:6px;overflow:hidden}}.svc-bar>div{{height:100%}}
.svc-m{{font-size:11px;color:var(--mut);margin-top:5px}}
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
<div><div style="font-size:22px;font-weight:700">{cov_write}%</div><div class="mut">쓰기 CRUD ({write_hit}/{nonget_total})</div></div>{param_stat}</div>
<div class="mut" style="margin-top:12px">측정 축 (swagger-coverage/ReadyAPI 기준)</div>
<div class="axes"><span class="ax on">✓ operation</span><span class="ax part">◑ status-code</span>
<span class="ax {writeax_cls}">{writeax} write/CRUD</span><span class="ax {paramax_cls}">{paramax} parameter</span><span class="ax off">✗ schema</span></div></div>
</div></section>
<section><h2>카테고리별 커버리지 (사각지대 탐지)</h2><div class="panel">{covbars}</div></section>
{conformance_section}
<section><h2>서비스별 드릴다운 <span class="mut" style="text-transform:none">— 서비스 클릭 시 해당 서비스의 API별 커버 현황 + 설계/동작 결함</span></h2>{services_nav}</section>
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
<section><h2>알려진 이슈 (data/baselines/known_issues.json)</h2><div class="panel"><table>
<tr><th>endpoint</th><th>status</th><th>유형</th></tr>{knrows}</table></div></section>
<footer>생성: <code>tools/build_dashboard.py</code> ← smoke_status.tsv + junit-crud.xml + api_catalog.json
&nbsp;|&nbsp; 추세: <code>dashboard-data</code> 브랜치 <code>history.jsonl</code> &nbsp;|&nbsp; 배포: GitHub Pages</footer>
</div></body></html>"""


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--catalog", default="data/api_catalog.json")
    ap.add_argument("--tsv", default="reports/smoke_status.tsv")
    ap.add_argument("--param-tsv", default="reports/param_status.tsv")
    ap.add_argument("--crud", default="reports/junit-crud.xml")
    ap.add_argument("--lifecycles", default="tests/crud/lifecycles.json")
    ap.add_argument("--known", default="data/baselines/known_issues.json")
    ap.add_argument("--conformance", default="data/conformance.json")
    ap.add_argument("--history", default="dashboard/history.jsonl")
    ap.add_argument("--out", default="dashboard/index.html")
    ap.add_argument("--run-type", default="local")
    ap.add_argument("--sha", default="")
    ap.add_argument("--branch", default=os.environ.get("GITHUB_REF_NAME", "—"))
    args = ap.parse_args()

    cat = load_catalog(args.catalog)
    tsv = parse_smoke_tsv(args.tsv)
    param_rows = parse_smoke_tsv(args.param_tsv)
    crud = parse_crud_junit(args.crud)
    lifecycles = json.load(open(args.lifecycles)).get("lifecycles", []) \
        if os.path.exists(args.lifecycles) else []
    known = json.load(open(args.known)) if os.path.exists(args.known) else {"issues": []}
    conf = json.load(open(args.conformance)) if os.path.exists(args.conformance) \
        else {"summary": {}, "systemic": [], "by_endpoint": {}}

    d = compute(cat, tsv, crud, lifecycles, known, param_rows)
    hist = append_history(args.history, d, args.run_type, args.sha)
    services = per_service(cat, tsv, crud_write_ops(lifecycles, cat))

    meta = {"branch": args.branch,
            "when": time.strftime("%Y-%m-%d %H:%M UTC", time.gmtime()),
            "run_type": args.run_type, "conf": conf}
    htm = render(d, hist, meta, services)
    # TEMPLATE references writeax_cls; inject it
    htm = htm.replace("{writeax_cls}", "part" if d["cov_write"] > 0 else "off")
    outdir = os.path.dirname(args.out) or "."
    os.makedirs(outdir, exist_ok=True)
    open(args.out, "w").write(htm)

    # per-service drill-down pages under <outdir>/services/
    sdir = os.path.join(outdir, "services")
    os.makedirs(sdir, exist_ok=True)
    for s in services:
        open(os.path.join(sdir, s["slug"] + ".html"), "w").write(
            render_service_page(s, meta))

    print(f"dashboard -> {args.out}  (coverage {d['cov_op']:.1f}% op, "
          f"{d['cov_get']:.1f}% GET, {d['cov_write']:.1f}% write; "
          f"ok {d['ok']} soft {d['soft']} new {d['fail_new']} known {d['fail_known']}; "
          f"{len(hist)} history rows; {len(services)} service pages)")


if __name__ == "__main__":
    main()
