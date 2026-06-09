"""dashboard.build — render the API-regression dashboard.

Reads the unified results store FIRST via ``core.results``; falls back to the
legacy flat-file inputs (reports/smoke_status.tsv, reports/param_status.tsv,
data/conformance.json, reports/junit-crud.xml) so nothing regresses while
the migration is in flight.

Both axes are first-class in the output:
  * Axis 1 — regression: coverage %, pass/fail, response-time column, failure
    taxonomy, per-service drill-down.
  * Axis 2 — conformance: design/behavior defect column + top-of-page panel.

Outputs:
  * <out>             — self-contained HTML (default: reports/dashboard/index.html)
  * <history>         — one summary row appended per run for trends (default: dashboard/history.jsonl)
  * <out_dir>/services/<slug>.html — per-service drill-down pages
"""
from __future__ import annotations

import argparse
import html
import json
import math
import os
import time
import xml.etree.ElementTree as ET
from collections import Counter, defaultdict
from pathlib import Path

# ---------------------------------------------------------------------------
# Unified results store (core.results) — imported lazily so the module can
# still be used as a standalone script without the full core package installed.
# ---------------------------------------------------------------------------

def _try_load_unified(obs_path=None, findings_path=None):
    """Attempt to load from the unified JSONL store; return (obs, findings) or
    ([], []) if core is unavailable or the files are empty/absent."""
    try:
        from core.results import load_observations, load_findings
        obs = load_observations(obs_path)
        findings = load_findings(findings_path)
        return obs, findings
    except ImportError:
        pass
    # Direct JSONL read without core (standalone / CI mode)
    def _read_jsonl(path):
        if path is None:
            return []
        p = Path(path)
        if not p.exists():
            return []
        out = []
        for ln in p.read_text().splitlines():
            ln = ln.strip()
            if ln:
                try:
                    out.append(json.loads(ln))
                except ValueError:
                    pass
        return out

    from core.results import OBSERVATIONS, FINDINGS
    obs = _read_jsonl(obs_path or OBSERVATIONS)
    findings = _read_jsonl(findings_path or FINDINGS)
    return obs, findings


# ---------------------------------------------------------------------------
# Observation -> (status, category, key, method, path, elapsed_ms) adapter
# ---------------------------------------------------------------------------

def obs_to_tsv_row(o: dict):
    """Convert a unified Observation dict to the 6-tuple the legacy pipeline
    expects so the same compute() / per_service() functions work unchanged."""
    return (
        int(o.get("status", 0)),
        o.get("category", "ok"),
        o.get("endpoint_key", ""),
        o.get("method", "GET"),
        o.get("path", ""),
        o.get("elapsed_ms"),        # may be None — legacy pipeline handles that
    )


def findings_to_conf(findings: list[dict]) -> dict:
    """Convert a list of Finding dicts into the conformance.json structure that
    the existing renderer (render_conformance_section / conf_cell) consumes.

    Severity mapping:  red -> red (결함)  yellow -> yellow (개선)  green -> green (정상)
    """
    if not findings:
        return {"summary": {}, "systemic": [], "by_endpoint": {}}

    by_endpoint: dict[str, dict] = {}
    severity_order = {"red": 2, "yellow": 1, "green": 0}

    for f in findings:
        key = f.get("endpoint_key", "")
        sev = f.get("severity", "green")
        rec = by_endpoint.setdefault(key, {"status": "green", "items": []})
        rec["items"].append({
            "type": f.get("rule_id", ""),
            "src": f.get("source", "static"),
            "issue": f.get("issue", ""),
            "detail": f.get("detail", ""),
        })
        if severity_order.get(sev, 0) > severity_order.get(rec["status"], 0):
            rec["status"] = sev

    counts: Counter = Counter(v["status"] for v in by_endpoint.values())
    summary = {
        "red": counts.get("red", 0),
        "yellow": counts.get("yellow", 0),
        "green": counts.get("green", 0),
        "total": len(by_endpoint),
    }
    return {"summary": summary, "systemic": [], "by_endpoint": by_endpoint}


# ---------------------------------------------------------------------------
# Legacy data loading (kept verbatim from tools/build_dashboard.py)
# ---------------------------------------------------------------------------

def load_catalog(path):
    cat = json.load(open(path))
    for e in cat:
        e["_norm"] = norm_path(e["http_path"])
    return cat


def norm_path(p):
    """Normalise a path: drop query, collapse templated/concrete id segments to '*'."""
    p = p.split("?")[0].strip("/")
    return "/".join("*" if "{" in s else s for s in p.split("/"))


def smoke_tested_keys(cat):
    """Reproduce what the smoke suite calls: GET, no path params, non-mutating."""
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
    """Distinct catalog non-GET operations exercised by ENABLED CRUD lifecycles."""
    cat_by = {(e["method"], e["_norm"]) for e in cat if e["method"] != "GET"}
    hit = set()
    for lc in lifecycles:
        if not lc.get("enabled"):
            continue
        for s in lc.get("steps", []):
            if not s.get("method") or not s.get("path"):
                continue
            m = s["method"].upper()
            if m == "GET":
                continue
            key = (m, norm_path(s["path"]))
            if key in cat_by:
                hit.add(key)
    return hit


def slug(category, service):
    return f"{category}__{service}".replace("/", "-").replace(" ", "-")


def reachable_ceiling(cat, lifecycles):
    """Static coverage CEILING from the committed scenarios (no live calls).

    Mirrors spec.coverage_gap so the dashboard shows a STABLE number that reflects
    scenario-authoring work the instant it is committed — independent of whether a
    live run happened or what state the account was in. An endpoint is reachable if:
      * GET with no path params  (read-only smoke floor), OR
      * (method, normalized-path) matches an ENABLED lifecycle step
        (GET steps cover id-bound GETs via probe/read-chains; non-GET cover writes).
    Returns reachable count/%, plus the remaining gap split into write vs id-GET —
    i.e. exactly "what to improve next".
    """
    hit = set()
    for lc in lifecycles:
        if not lc.get("enabled"):
            continue
        for s in lc.get("steps", []):
            if s.get("method") and s.get("path"):
                hit.add((s["method"].upper(), norm_path(s["path"])))
    reach = gap_write = gap_getid = 0
    for e in cat:
        m, np = e["method"], e.get("_norm") or norm_path(e["http_path"])
        if (m == "GET" and "{" not in e["http_path"]) or (m, np) in hit:
            reach += 1
        elif m == "GET":
            gap_getid += 1
        else:
            gap_write += 1
    total = len(cat)
    return {"reachable": reach,
            "reachable_pct": reach / total * 100 if total else 0,
            "gap_write": gap_write, "gap_getid": gap_getid}



def endpoint_verdicts(cat, tsv_rows):
    """Map each OBSERVED catalog endpoint key -> runtime verdict.

    Restricted to catalog keys on purpose: the CRUD engine also records each
    write step under a synthetic ``"<lifecycle>:<step>"`` key (engine.py), which
    is NOT a catalog endpoint. Counting those in the coverage numerator is what
    pushed the headline past 100% (e.g. 131.5%) — they are excluded here.

      * verified — saw a 2xx at least once (the operation actually worked)
      * failed   — saw a 5xx / hard auth-fail (and never a 2xx)
      * reached  — only ever 4xx (400/403/404/409/422): the endpoint was CALLED
                   but NOT verified. A POST/DELETE that 404s created/deleted
                   nothing, so it is "도달(reached)", never "covered".
    """
    cat_keys = {e["key"] for e in cat}
    obs = defaultdict(set)
    for _status, category, key, *_rest in tsv_rows:
        if key in cat_keys:
            obs[key].add(category)
    return {k: ("verified" if "ok" in cs else "failed" if "fail" in cs else "reached")
            for k, cs in obs.items()}


def per_service(cat, tsv_rows):
    called = {}
    for status, _category, key, _method, _path, *_rest in tsv_rows:
        called[key] = (status, _rest[0] if _rest else None)
    verdict = endpoint_verdicts(cat, tsv_rows)

    groups = defaultdict(list)
    for e in cat:
        groups[(e["category"], e["service"])].append(e)

    services = []
    for (category, service), ents in groups.items():
        rows, covn, reachn = [], 0, 0
        gtot = gcov = wtot = wcov = 0
        for e in sorted(ents, key=lambda x: (x["method"], x["_norm"])):
            v = verdict.get(e["key"])          # None | verified | reached | failed
            covered = (v == "verified")        # "covered" == 검증(2xx)됨
            if e["method"] == "GET":
                gtot += 1
                gcov += covered
            else:
                wtot += 1
                wcov += covered
            covn += covered
            reachn += (v == "reached")
            st_el = called.get(e["key"]) or (None, None)
            rows.append((e["method"], e["http_path"], e.get("name", ""),
                         bool(covered), st_el[0], st_el[1], v or ""))
        services.append({
            "category": category, "service": service, "slug": slug(category, service),
            "total": len(ents), "covered": covn, "reached": reachn,
            "gtot": gtot, "gcov": gcov, "wtot": wtot, "wcov": wcov, "rows": rows})
    services.sort(key=lambda s: (s["category"], s["covered"] / (s["total"] or 1), s["service"]))
    return services


# ---------------------------------------------------------------------------
# Computation (verbatim from legacy, extended with axis-2 findings count)
# ---------------------------------------------------------------------------

def compute(cat, tsv_rows, crud, lifecycles, known, param_rows=()):
    total = len(cat)
    cat_total = Counter(e["category"] for e in cat)
    cat_get = Counter(e["category"] for e in cat if e["method"] == "GET")
    get_total = sum(cat_get.values())
    nonget_total = total - get_total

    key_cat = {e["key"]: e["category"] for e in cat}
    get_keys = {e["key"] for e in cat if e["method"] == "GET"}

    # Per-endpoint runtime verdict (catalog keys only). "covered" == verified
    # (2xx); "reached" == called but only 4xx (도달했으나 미검증) — a 404'd
    # POST/DELETE lands here, NOT in covered.
    verdict = endpoint_verdicts(cat, tsv_rows)
    verified = {k for k, v in verdict.items() if v == "verified"}
    reached = {k for k, v in verdict.items() if v == "reached"}
    touched = set(verdict)                       # any catalog endpoint observed
    get_verified = verified & get_keys
    write_verified = verified - get_keys
    write_reached = reached - get_keys           # 미검증 write 엔드포인트

    cat_tested = Counter(key_cat[k] for k in get_verified)  # 카테고리 막대 = 검증된 GET

    dist = Counter(r[0] for r in tsv_rows)
    cats = Counter(r[1] for r in tsv_rows)
    ok, soft = cats.get("ok", 0), cats.get("soft", 0)

    known_keys = {i["key"] for i in known.get("issues", [])}
    new_regressions, known_red = [], []
    for status, category, key, method, path, *_rest in tsv_rows:
        if category == "fail":
            (known_red if key in known_keys else new_regressions).append(
                (key, status, path))

    # Headline coverage = VERIFIED (real 2xx). Reachability (verified+reached)
    # is reported SEPARATELY so a 404'd write is never counted as covered.
    cov_op = len(verified) / total * 100 if total else 0
    reach_measured_pct = len(touched) / total * 100 if total else 0
    cov_get = len(get_verified) / get_total * 100 if get_total else 0
    cov_write = len(write_verified) / nonget_total * 100 if nonget_total else 0

    param_attempted = len(param_rows)
    param_accepted = sum(1 for r in param_rows if 200 <= r[0] < 300)
    cov_param = param_accepted / param_attempted * 100 if param_attempted else 0

    crud_rows = []
    for lc in lifecycles:
        if not lc.get("enabled"):
            continue
        st = crud.get(lc["id"], "skip")
        kind = "heavy" if lc.get("heavy") else "light"
        crud_rows.append((lc["id"], kind, st, len(lc.get("steps", []))))

    return {
        "total": total, "tested": len(touched),
        "verified": len(verified), "reached": len(reached),
        "reach_measured_pct": reach_measured_pct,
        "get_verified": len(get_verified),
        "write_verified": len(write_verified), "write_reached": len(write_reached),
        "cov_op": cov_op, "cov_get": cov_get, "cov_write": cov_write,
        "get_total": get_total, "nonget_total": nonget_total,
        "write_hit": len(write_verified),
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


# ---------------------------------------------------------------------------
# History
# ---------------------------------------------------------------------------

def append_history(path, d, run_type, sha):
    row = {
        "ts": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "run_type": run_type, "sha": (sha or "")[:7],
        "ok": d["ok"], "soft": d["soft"],
        "fail_new": d["fail_new"], "fail_known": d["fail_known"],
        "cov_op": round(d["cov_op"], 2), "cov_get": round(d["cov_get"], 2),
        "tested": d["tested"], "total": d["total"],
        # (2) stable ceiling + (1) run-scope so the trend reflects authoring
        # progress (monotonic), not live measurement noise.
        "reachable_pct": round(d.get("reachable_pct", 0), 2),
        "gap_write": d.get("gap_write", 0), "gap_getid": d.get("gap_getid", 0),
        "crud_ran": d.get("crud_ran", False),
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
    if d["has_results"]:
        hist.append(row)
        if path:
            os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
            with open(path, "w") as fh:
                for h in hist:
                    fh.write(json.dumps(h) + "\n")
    return hist


# ---------------------------------------------------------------------------
# Rendering (verbatim from legacy)
# ---------------------------------------------------------------------------

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
    by_cat = defaultdict(list)
    for s in services:
        by_cat[s["category"]].append(s)
    parts = []
    for category in sorted(by_cat):
        svs = by_cat[category]
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


# ---- design/behavior conformance ----------------------------------------
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
        """Format elapsed_ms as a coloured span; amber >3s, red >10s."""
        if el is None:
            return ""
        txt = f"{el / 1000:.1f}s" if el >= 1000 else f"{el:.0f}ms"
        col = "#cf222e" if el >= 10000 else "#9a6700" if el >= 3000 else "#8c959f"
        return f' <span style="font-size:11px;color:{col}">· {txt}</span>'

    def statcell(st, el=None):
        """Render HTTP status code + optional response-time span."""
        if st is None:
            return '<span class="mut">—</span>'
        col = STATUS_COLORS.get(st // 100, "#656d76")
        return f'<b style="color:{col}">{st}</b>{fmt_ms(el)}'

    conf = (meta.get("conf") or {}).get("by_endpoint", {})
    rows = []
    for method, path, title, covered, st, el, verd in s["rows"]:
        if covered:
            chk = '<span style="color:#2da44e">✓</span>'
        elif verd == "reached":
            chk = '<span style="color:#8250df" title="호출됨·4xx 미검증">◑</span>'
        elif verd == "failed":
            chk = '<span style="color:#cf222e" title="5xx/auth fail">⛔</span>'
        else:
            chk = '<span class="mut">·</span>'
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
        f'<div class="mut">검증(2xx) 커버 ({s["covered"]}/{s["total"]}) '
        f'&nbsp;·&nbsp; <span style="color:#8250df">◑ 도달·미검증 {s["reached"]}</span></div>'
        f'<div class="s-sub">읽기 GET {s["gcov"]}/{s["gtot"]} ({gpct:.0f}%) '
        f'&nbsp;·&nbsp; 쓰기 {s["wcov"]}/{s["wtot"]} ({wpct:.0f}%) &nbsp;— 모두 2xx 기준</div></div>'
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
    run_scope = "full CRUD" if d.get("crud_ran") else "read-only"
    cards = (card("New regressions", str(d["fail_new"]),
                  "vs known baseline", "#2da44e" if healthy else "#cf222e")
             + card("Pass rate", f"{pass_rate:.1f}%", f'{d["ok"]} ok / {called} calls', "#1f2328")
             + card("Reachable ceiling", f'{d.get("reachable_pct", 0):.1f}%',
                    f'{d.get("reachable", 0)} / {d["total"]} · scenarios', "#2da44e")
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
        if st == "skip" and kind == "heavy":
            label = "gated"
        return (f'<div class="crud {st}"><div class="crud-top">{icon.get(st, "·")} <b>{id_}</b></div>'
                f'<div class="crud-meta"><span class="tag {kind}">{kind}</span> '
                f'{steps} steps · {label}</div></div>')
    crudgrid = "".join(chip(*c) for c in d["crud_rows"])

    knrows = "".join(
        f'<tr><td><code>{k}</code></td><td>{s}</td><td>Product Bug</td></tr>'
        for k, s, _ in d["known_red"]) or \
        '<tr><td colspan="3" class="mut">none</td></tr>'

    pr_series = [h["ok"] / max(1, h["ok"] + h["soft"] + h["fail_new"] + h["fail_known"]) * 100
                 for h in hist[-12:]]
    # Trend uses the STABLE reachable ceiling (monotonic with authoring work),
    # not the per-run live measurement (which bounces with run scope / account
    # state). Old history rows without reachable_pct fall back to cov_op.
    cov_series = [h.get("reachable_pct", h.get("cov_op", 0)) for h in hist[-12:]]

    writeax = "◑" if d["cov_write"] > 0 else "✗"
    measured = d.get("param_attempted", 0) > 0
    paramax = "◑" if measured else "✗"
    param_stat = (f'<div><div style="font-size:22px;font-weight:700">{d["cov_param"]:.0f}%</div>'
                  f'<div class="mut">파라미터 ({d["param_accepted"]}/{d["param_attempted"]})</div></div>'
                  ) if measured else ""
    writeax_cls = "part" if d["cov_write"] > 0 else "off"
    paramax_cls = "part" if measured else "off"

    return TEMPLATE.format(
        branch=html.escape(meta["branch"]), when=meta["when"], run_type=meta["run_type"],
        services_nav=render_services_nav(services),
        conformance_section=render_conformance_section(meta.get("conf", {})),
        badge=badge, cards=cards, donut=donut(segs, called), legend=legend,
        cov_op=f'{d["cov_op"]:.1f}', tested=d["tested"], total=d["total"],
        verified=d["verified"], reach_measured=f'{d["reach_measured_pct"]:.1f}',
        get_verified=d["get_verified"], write_reached=d["write_reached"],
        reach_pct=f'{d.get("reachable_pct", 0):.1f}', reachable=d.get("reachable", 0),
        gap_write=d.get("gap_write", 0), gap_getid=d.get("gap_getid", 0),
        run_scope=run_scope,
        cov_get=f'{d["cov_get"]:.1f}', get_total=d["get_total"],
        cov_write=f'{d["cov_write"]:.1f}', write_hit=d["write_hit"],
        nonget_total=d["nonget_total"], writeax=writeax,
        paramax=paramax, paramax_cls=paramax_cls, param_stat=param_stat,
        covbars=covbars, ok=d["ok"], soft=d["soft"],
        fail_new=d["fail_new"], fail_known=d["fail_known"],
        new_cls="ok" if healthy else "bad",
        crudgrid=crudgrid, knrows=knrows,
        writeax_cls=writeax_cls,
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
<div style="display:flex;gap:28px;align-items:baseline;flex-wrap:wrap">
<div><div class="bignum" style="color:#2da44e">{reach_pct}%</div><div class="mut">도달가능 ceiling ({reachable}/{total} · 시나리오 기준, 안정)</div></div>
<div><div class="bignum" style="color:#0969da">{cov_op}%</div><div class="mut">측정·검증 2xx ({verified}/{total} · {run_scope})</div></div>
<div><div style="font-size:22px;font-weight:700;color:#8250df">{reach_measured}%</div><div class="mut">측정·도달 called ({tested}/{total})</div></div>
<div><div style="font-size:22px;font-weight:700">{cov_get}%</div><div class="mut">읽기 GET 검증 ({get_verified}/{get_total})</div></div>
<div><div style="font-size:22px;font-weight:700">{cov_write}%</div><div class="mut">쓰기 검증 ({write_hit}/{nonget_total}) · 미검증 {write_reached}</div></div>{param_stat}</div>
<div class="mut" style="margin-top:8px"><b>검증(2xx)</b>=실제로 동작 확인됨 = "covered" · <b>도달</b>=호출됐으나 4xx(404 포함, 미검증). 404 POST/DELETE는 아무것도 생성/삭제 안 했으므로 도달일 뿐 covered 아님.</div>
<div class="mut" style="margin-top:6px">남은 gap → write <b>{gap_write}</b> · id-bound GET <b>{gap_getid}</b> &nbsp;(다음 개선 대상; write=시나리오 추가, id-GET=read-chain/probe로 런타임 자동 도달)</div>
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
<tr><td>✅ 검증(2xx) 엔드포인트</td><td>{verified}</td><td>실제 동작 확인 = covered</td></tr>
<tr><td>◑ 미검증(도달) write</td><td>{write_reached}</td><td>호출됐으나 4xx(404 포함) — 생성/삭제 미발생, covered 아님</td></tr>
<tr><td>🟡 soft 호출 (4xx)</td><td>{soft}</td><td>파라미터/권한/엔타이틀먼트 한계 (call 단위)</td></tr>
<tr><td>⛔ new regression</td><td><b class="badge {new_cls}" style="border:0;background:0;padding:0">{fail_new}</b></td><td>새로 깨진 5xx/auth — 알림 대상</td></tr>
<tr><td>🔴 known-red</td><td>{fail_known}</td><td>등록된 백엔드 버그(known_issues)</td></tr></table></div>
<div class="panel"><h2>CRUD 라이프사이클</h2><div class="crudgrid">{crudgrid}</div></div>
</div></section>
<section><h2>추세 <span class="mut" style="text-transform:none">— {runs} runs 누적 (dashboard-data 브랜치)</span></h2>
<div class="panel trendgrid">
<div><div class="mut">성공률</div>{spark_pr}</div>
<div><div class="mut">도달가능 ceiling % (시나리오 기준, 단조)</div>{spark_cov}</div></div></section>
<section><h2>알려진 이슈 (data/baselines/known_issues.json)</h2><div class="panel"><table>
<tr><th>endpoint</th><th>status</th><th>유형</th></tr>{knrows}</table></div></section>
<footer>생성: <code>dashboard/build.py</code> ← unified results store (reports/results/*.jsonl) + legacy fallback (smoke_status.tsv, junit-crud.xml, api_catalog.json)
&nbsp;|&nbsp; 추세: <code>dashboard-data</code> 브랜치 <code>history.jsonl</code> &nbsp;|&nbsp; 배포: GitHub Pages</footer>
</div></body></html>"""


# ---------------------------------------------------------------------------
# Main entry point
# ---------------------------------------------------------------------------

def build(
    *,
    # Unified store paths (None = use core.results defaults)
    obs_path=None,
    findings_path=None,
    # Legacy fallback paths
    catalog: str = "data/api_catalog.json",
    tsv: str = "reports/smoke_status.tsv",
    param_tsv: str = "reports/param_status.tsv",
    crud: str = "reports/junit-crud.xml",
    lifecycles: str = "regression/scenarios/scenarios.json",
    known: str = "data/baselines/known_issues.json",
    conformance: str = "data/conformance.json",
    # Output
    out: str = "reports/dashboard/index.html",
    history: str = "dashboard/history.jsonl",
    run_type: str = "local",
    sha: str = "",
    branch: str = "",
):
    """Build the dashboard HTML.

    Reads the unified results store FIRST (core.results JSONL).  If the store
    is empty or unavailable, falls back to the legacy TSV/XML/JSON inputs so
    nothing regresses during migration.
    """
    if not branch:
        branch = os.environ.get("GITHUB_REF_NAME", "—")

    # ------------------------------------------------------------------
    # 1. Load catalog (always needed for coverage denominators)
    # ------------------------------------------------------------------
    cat = load_catalog(catalog) if os.path.exists(catalog) else []

    # ------------------------------------------------------------------
    # 2. Load regression observations — unified store FIRST, then legacy
    # ------------------------------------------------------------------
    unified_obs, unified_findings = _try_load_unified(obs_path, findings_path)

    if unified_obs:
        # Convert unified Observation records to the legacy 6-tuple format
        tsv_rows = [obs_to_tsv_row(o) for o in unified_obs]
        source_label = "unified results store"
    else:
        # Legacy fallback: read smoke TSV
        tsv_rows = parse_smoke_tsv(tsv)
        source_label = "legacy smoke_status.tsv"

    param_rows = parse_smoke_tsv(param_tsv)

    # ------------------------------------------------------------------
    # 3. Load conformance findings — unified store FIRST, then legacy JSON
    # ------------------------------------------------------------------
    if unified_findings:
        conf = findings_to_conf(unified_findings)
        # The platform-wide "systemic" findings are NOT per-endpoint, so they are
        # not in the unified findings store — they live in the conformance.json
        # the static analysis writes. Merge them back so the dashboard's
        # "플랫폼 전역 항목" banner is populated.
        if os.path.exists(conformance):
            try:
                conf["systemic"] = json.load(open(conformance)).get("systemic", [])
            except (ValueError, OSError):
                pass
    elif os.path.exists(conformance):
        conf = json.load(open(conformance))
    else:
        conf = {"summary": {}, "systemic": [], "by_endpoint": {}}

    # ------------------------------------------------------------------
    # 4. Load CRUD / lifecycle data (legacy only, no unified equivalent yet)
    # ------------------------------------------------------------------
    crud_results = parse_crud_junit(crud)
    # Merge base scenarios.json + per-service fragments (lifecycles/*.json) via
    # the shared loader so coverage counts every agent's fragment. Fall back to
    # the raw file if the loader isn't importable (keeps the dashboard standalone).
    try:
        from regression.scenarios.loader import load_lifecycles
        lc_data = load_lifecycles()
    except Exception:
        lc_data = (json.load(open(lifecycles)).get("lifecycles", [])
                   if os.path.exists(lifecycles) else [])
    known_data = json.load(open(known)) if os.path.exists(known) else {"issues": []}

    # ------------------------------------------------------------------
    # 5. Compute, history, render
    # ------------------------------------------------------------------
    d = compute(cat, tsv_rows, crud_results, lc_data, known_data, param_rows)
    # (2) stable static ceiling from committed scenarios (reflects authoring work
    # immediately, no live run needed) + remaining gap = what to improve next.
    d.update(reachable_ceiling(cat, lc_data))
    # (1) run scope: did this run actually exercise CRUD (write ops), or was it
    # read-only (smoke + read-chains)? Read-only/partial runs measure FEWER
    # endpoints, so their live cov_op must not be read as a regression of the
    # full-run number — label it and keep the ceiling as the stable headline.
    d["crud_ran"] = (any(o.get("source") == "crud_probe" for o in unified_obs)
                     or any(v == "pass" for v in crud_results.values()))
    hist = append_history(history, d, run_type, sha)
    services = per_service(cat, tsv_rows)

    meta = {
        "branch": branch,
        "when": time.strftime("%Y-%m-%d %H:%M UTC", time.gmtime()),
        "run_type": run_type,
        "conf": conf,
    }

    htm = render(d, hist, meta, services)

    # Write index.html
    outdir = os.path.dirname(os.path.abspath(out))
    os.makedirs(outdir, exist_ok=True)
    with open(out, "w") as fh:
        fh.write(htm)

    # Write per-service drill-down pages
    sdir = os.path.join(outdir, "services")
    os.makedirs(sdir, exist_ok=True)
    for s in services:
        with open(os.path.join(sdir, s["slug"] + ".html"), "w") as fh:
            fh.write(render_service_page(s, meta))

    print(
        f"dashboard -> {out}  "
        f"(source: {source_label}; "
        f"reachable {d.get('reachable_pct', 0):.1f}% ceiling (gap write {d.get('gap_write', 0)} / id-GET {d.get('gap_getid', 0)}); "
        f"measured-verified {d['cov_op']:.1f}% op ({d['verified']}/{d['total']}, {'full CRUD' if d.get('crud_ran') else 'read-only'}), "
        f"measured-reached {d['reach_measured_pct']:.1f}% ({d['tested']}/{d['total']}); "
        f"{d['cov_get']:.1f}% GET-verified, {d['cov_write']:.1f}% write-verified (write reached-but-unverified {d['write_reached']}); "
        f"ok {d['ok']} soft {d['soft']} new {d['fail_new']} known {d['fail_known']}; "
        f"{len(hist)} history rows; {len(services)} service pages)"
    )
    return out


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main():
    ap = argparse.ArgumentParser(
        description="Build the SCP API regression dashboard.")
    ap.add_argument("--obs", default=None,
                    help="Path to observations.jsonl (unified store); default: core.results.OBSERVATIONS")
    ap.add_argument("--findings", default=None,
                    help="Path to findings.jsonl (unified store); default: core.results.FINDINGS")
    ap.add_argument("--catalog", default="data/api_catalog.json")
    ap.add_argument("--tsv", default="reports/smoke_status.tsv",
                    help="Legacy fallback: smoke_status.tsv")
    ap.add_argument("--param-tsv", default="reports/param_status.tsv")
    ap.add_argument("--crud", default="reports/junit-crud.xml")
    ap.add_argument("--lifecycles", default="regression/scenarios/scenarios.json")
    ap.add_argument("--known", default="data/baselines/known_issues.json")
    ap.add_argument("--conformance", default="data/conformance.json",
                    help="Legacy fallback: conformance.json")
    ap.add_argument("--history", default="dashboard/history.jsonl")
    ap.add_argument("--out", default="reports/dashboard/index.html")
    ap.add_argument("--run-type", default="local")
    ap.add_argument("--sha", default="")
    ap.add_argument("--branch", default=os.environ.get("GITHUB_REF_NAME", "—"))
    args = ap.parse_args()

    build(
        obs_path=args.obs,
        findings_path=args.findings,
        catalog=args.catalog,
        tsv=args.tsv,
        param_tsv=args.param_tsv,
        crud=args.crud,
        lifecycles=args.lifecycles,
        known=args.known,
        conformance=args.conformance,
        history=args.history,
        out=args.out,
        run_type=args.run_type,
        sha=args.sha,
        branch=args.branch,
    )


if __name__ == "__main__":
    main()
