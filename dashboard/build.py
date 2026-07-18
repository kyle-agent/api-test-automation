"""dashboard.build — render the API-regression dashboard.

Reads the unified results store (``core.results`` JSONL — reports/results/*.jsonl)
as the SINGLE source of truth. With no store present the build still renders
(all-zero coverage) and does not crash. Platform-wide systemic conformance
findings (not per-endpoint) are still merged from data/conformance.json when the
static-analysis axis writes one.

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
    # Per-endpoint dedup index: (rule_id, detail) -> position in rec["items"].
    # The findings store is append-only and re-records the same defect on every
    # conformance run, and static + runtime independently report the same defect
    # (differing only in issue-ref / source) — without this the dashboard shows
    # the identical improvement line 2-3× per API.
    seen: dict[str, dict] = {}

    for f in findings:
        key = f.get("endpoint_key", "")
        sev = f.get("severity", "green")
        rec = by_endpoint.setdefault(key, {"status": "green", "items": []})
        item = {
            "type": f.get("rule_id", ""),
            "src": f.get("source", "static"),
            "issue": f.get("issue", ""),
            "detail": f.get("detail", ""),
        }
        dkey = (key, item["type"], item["detail"])
        prev = seen.get(dkey)
        if prev is None:
            seen[dkey] = item
            rec["items"].append(item)
        elif not prev["issue"] and item["issue"]:
            # Keep the more informative variant: prefer the one carrying an
            # issue reference (typically the static finding) over a bare runtime
            # duplicate of the same defect.
            prev.update(issue=item["issue"], src=item["src"])
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
    # Catalog entries whose doc page failed to resolve (e.g. a live 404 on the
    # docs site) carry http_path=None; they have no route to normalise, so skip
    # them here rather than crash (mirrors spec.coverage_gap.load_catalog —
    # spec.summary already surfaces them separately as "unresolved").
    cat = [e for e in cat if e.get("http_path")]
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


def _load_untestable():
    """owner 2026-06-13: 라이선스/물리자원 부재 서비스 — 기능 테스트 제외,
    접근성(smoke)만. 대시보드에서 회색 + 사유 배지로 구분."""
    try:
        with open(Path(__file__).parent.parent / "data" / "baselines"
                  / "untestable_services.json") as f:
            return json.load(f).get("services", {})
    except Exception:
        return {}


_UNTESTABLE = _load_untestable()


def per_service(cat, tsv_rows, prior_verified=None, prior_status=None, sha="",
                durable_evidence=None):
    """prior_status: cumulative key -> [status, elapsed_ms, run_sha] from past
    runs (endpoint_status.json on dashboard-data). The cumulative-verified
    overlay marks endpoints ✓ that THIS run never called; without this map
    their status/time cells go blank on every scoped run (field report:
    'covered checked but no HTTP status/time for POST/PUT/DELETE').

    durable_evidence: data/baselines/verified_endpoints.json (endpoint_key ->
    {method,path,first_run,last_run,count}) — the cumulative cross-session 2xx
    evidence store. A key can land here (e.g. via a local console2 run) WITHOUT
    a matching dashboard-data endpoint_status.json refresh (that only happens on
    a full `dashboard.build` publish) — so the last-known-observation cell can
    go stale and show an old 4xx/5xx even though the endpoint IS durably
    verified (owner 2026-07-10 field report: container/ske/createclusterkubeconfig
    showed "400 @fe79059" while data/baselines/verified_endpoints.json already
    carried a local-run 2xx). Rule: durable evidence wins the COVERED verdict
    (never contradicted by a stale cell), and the stale observation is kept
    alongside as a second, dated data point rather than silently dropped —
    CX-IA-DESIGN principle 1 (every number carries its source + timestamp)."""
    durable_evidence = durable_evidence or {}
    called = {}
    for status, _category, key, _method, _path, *_rest in tsv_rows:
        called[key] = (status, _rest[0] if _rest else None)
    verdict = endpoint_verdicts(cat, tsv_rows)
    # cumulative overlay (same rule as compute()): verified by any past run
    # stays verified unless THIS run hard-failed it — so the drill-down pages
    # agree with the cumulative C3 headline.
    for k in (prior_verified or ()):
        if verdict.get(k) != "failed":
            verdict[k] = "verified"
    cat_keys = {e["key"] for e in cat}
    # merged cumulative observation map (persisted back to dashboard-data):
    # last-known status/elapsed per CATALOG endpoint, current run wins.
    merged_status = {k: list(v) for k, v in (prior_status or {}).items()
                     if k in cat_keys}
    for k, (st, el) in called.items():
        if k in cat_keys and st is not None:
            merged_status[k] = [st, el, (sha or "")[:7]]

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
            st_el = called.get(e["key"])
            src = ""                      # "" = observed THIS run
            if st_el is None:
                pst = merged_status.get(e["key"])
                if pst:                   # fall back to the last-known observation
                    st_el, src = (pst[0], pst[1]), (pst[2] or "이전 런")
                else:
                    st_el = (None, None)
            # durable-evidence override (owner 2026-07-10): if this endpoint has
            # cross-session 2xx evidence (covered==True, incl. via the
            # prior_verified union upstream) but the last-known OBSERVATION cell
            # is stale/disagreeing (missing, or not itself 2xx), carry the
            # evidence run id so the UI can show "✅ 검증 (<run>)" alongside the
            # stale reading instead of letting the stale 4xx/5xx stand alone.
            ev_run = ""
            if covered:
                dur = durable_evidence.get(e["key"])
                if dur and (st_el[0] is None or not (200 <= st_el[0] < 300)):
                    ev_run = dur.get("last_run") or dur.get("first_run") or "evidence"
            rows.append((e["method"], e["http_path"], e.get("name", ""),
                         bool(covered), st_el[0], st_el[1], v or "", src, ev_run))
        unt_reason = _UNTESTABLE.get(f"{category}/{service}")
        services.append({
            "category": category, "service": service, "slug": slug(category, service),
            "total": len(ents), "covered": covn, "reached": reachn,
            "untestable": unt_reason,
            "gtot": gtot, "gcov": gcov, "wtot": wtot, "wcov": wcov, "rows": rows})
    services.sort(key=lambda s: (s["category"], s["covered"] / (s["total"] or 1), s["service"]))
    return services, merged_status


# ---------------------------------------------------------------------------
# Computation (verbatim from legacy, extended with axis-2 findings count)
# ---------------------------------------------------------------------------

def compute(cat, tsv_rows, crud, lifecycles, known, param_rows=(), waivers=None,
            prior_verified=None, prior_touched=None):
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
    # CUMULATIVE merge happens HERE so every downstream stat (GET/write split,
    # category bars, C3) sees the same verified set — an endpoint verified by
    # any past run stays verified unless THIS run hard-failed it.
    cat_keys_all = {e["key"] for e in cat}
    # `touched` is cumulative on the same principle: an endpoint REACHED by any
    # past run stays reached on a scoped rebuild (drives reachability-covered).
    # Without this, a rebuild that didn't re-run the reachability-waived services
    # would collapse reach_covered to this run's scope and regress the headline.
    touched = touched | ((prior_touched or set()) & cat_keys_all)
    prior_set = (prior_verified or set()) & cat_keys_all
    failed_now = {k for k, v in verdict.items() if v == "failed"}
    verified = verified | (prior_set - failed_now)
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

    # C0-C4 ladder (docs/COVERAGE-CRITERIA.md): the 100% goal is C3 (verified)
    # for every endpoint EXCEPT the explicit, human-approved waiver list. Two
    # waiver dispositions exist (the ``class`` field decides which):
    #
    #   * EXCLUDED waivers (blast-radius | entitlement | unsatisfiable-flow |
    #     billing-prohibitive | owner-exclusion): the endpoint can NEVER 2xx on
    #     the shared account by design, AND verifying it isn't the goal — it
    #     leaves BOTH the C3 numerator and denominator. It must still be C2
    #     (called); a verified excluded-waiver means the waiver is obsolete
    #     (surfaced separately as a removal candidate).
    #
    #   * REACHABILITY waivers (class: "reachability"): for owner-designated
    #     reachability-only services (archivestorage / iam-identity-center /
    #     parallel-filestorage / searchengine / vertica / sqlserver — license/
    #     entitlement-gated so a real 2xx is impossible without a key) the owner
    #     definition-of-done is "the endpoint is REACHED" (any response incl.
    #     4xx = access confirmed). These STAY in the C3 denominator and count as
    #     COVERED-WHEN-REACHED: a reachability waiver that was touched (any
    #     status) is added to the C3 numerator. This is tracked in a SEPARATE
    #     bucket (reach_covered) and is NEVER folded into ``verified`` — a 4xx is
    #     reported as "reachability-covered", honestly distinct from verified-2xx.
    by_key = {w["key"]: w for w in (waivers or {}).get("waivers", [])}
    waived = set(by_key) & cat_keys_all
    reach_waived = {k for k in waived if by_key[k].get("class") == "reachability"}
    excl_waived = waived - reach_waived          # excluded from C3 entirely

    # reachability-covered = reachability waiver that was REACHED (any status,
    # incl. 4xx). Verified-2xx reachability waivers also count (a real 2xx
    # trivially satisfies "reached") but are still NOT promoted into `verified`.
    reach_covered = reach_waived & touched
    reach_uncovered = reach_waived - touched     # not yet reached → still a gap

    # C3 denominator drops only the EXCLUDED waivers; reachability waivers remain
    # in the target. C3 numerator = verified-2xx (minus excluded waivers) PLUS
    # the reachability-covered set (honest distinct disposition, same target).
    c3_denom = total - len(excl_waived)
    c3_verified = (verified - excl_waived) | reach_covered
    cov_c3 = len(c3_verified) / c3_denom * 100 if c3_denom else 0
    # "waived" reporting below covers the EXCLUDED waivers (the obsolete-removal
    # candidate logic only makes sense for those); reachability waivers report
    # via reach_* keys.
    waived_called = len(excl_waived & touched)
    waived_verified = len(excl_waived & verified)   # waiver candidates for removal

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
        "cov_c3": cov_c3, "c3_denom": c3_denom, "c3_verified": len(c3_verified),
        # honest split of the C3 numerator: verified-2xx vs reachability-covered
        "c3_verified_2xx": len((verified - excl_waived)),
        "reach_covered": len(reach_covered), "reach_waived": len(reach_waived),
        "reach_uncovered": len(reach_uncovered),
        "verified_keys": sorted(verified),
        "waived_total": len(excl_waived), "waived_called": waived_called,
        "waived_verified": waived_verified,
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
        "cov_c3": round(d.get("cov_c3", d["cov_op"]), 2),
        "waived": d.get("waived_total", 0),
        "reach_covered": d.get("reach_covered", 0),
        "reach_waived": d.get("reach_waived", 0),
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


def spark_multi(series_list, w=520, h=120):
    """Overlay several (series, color) lines on ONE shared scale, so the gap
    between C1 (plannable) and C3 (verified) is visible at a glance."""
    series_list = [(s, c) for s, c in series_list if len(s) >= 2]
    if not series_list:
        return ('<div style="color:#656d76;font-size:13px;padding:30px 0;text-align:center">'
                'collecting… (need ≥2 runs)</div>')
    allv = [v for s, _ in series_list for v in s]
    mx = max(allv) or 1; mn = min(allv); rng = (mx - mn) or 1; pad = 10
    out = [f'<svg viewBox="0 0 {w} {h}" width="100%" height="{h}">']
    for series, color in series_list:
        n = len(series)
        pts = [(pad + i * (w - 2 * pad) / (n - 1),
                h - pad - (v - mn) / rng * (h - 2 * pad)) for i, v in enumerate(series)]
        poly = " ".join(f"{x:.1f},{y:.1f}" for x, y in pts)
        out.append(f'<polyline points="{poly}" fill="none" stroke="{color}" stroke-width="2"/>')
        out.append("".join(f'<circle cx="{x:.1f}" cy="{y:.1f}" r="2.5" fill="{color}"/>' for x, y in pts))
    out.append("</svg>")
    return "".join(out)


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
            if s.get("untestable"):
                cards.append(
                    f'<a class="svc unt" href="services/{s["slug"]}.html" '
                    f'title="{html.escape(s["untestable"])}">'
                    f'<div class="svc-n">{html.escape(s["service"])}'
                    f' <span class="unt-badge">접근성만</span></div>'
                    f'<div class="svc-bar"><div style="width:100%;background:#d0d7de"></div></div>'
                    f'<div class="svc-m">기능 테스트 제외 — {html.escape(s["untestable"])}</div></a>')
                continue
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
    ring_col = ("var(--red)" if pct < 25 else "var(--amber)" if pct < 55
                else "var(--green)")

    conf = (meta.get("conf") or {}).get("by_endpoint", {})
    # 스펙 변경 마크 (key -> added|changed) — NEW/UPD 배지 (owner 2026-07-14)
    chg_marks = (meta.get("spec_diff") or {}).get("marks") or {}
    chg_when = (meta.get("spec_diff") or {}).get("generated_at") or ""
    rows, n_def_items, n_red, def_count = [], 0, 0, Counter()
    n_failed = 0
    for method, path, title, covered, st, el, verd, src, ev_run in s["rows"]:
        key = f'{s["category"]}/{s["service"]}/{title}'
        rec = conf.get(key, {})
        crit = rec.get("status") == "red"
        items = [{"label": i.get("type", ""), "issue": str(i.get("issue", "")),
                  "kind": i.get("src", ""), "detail": i.get("detail", ""),
                  "crit": crit}
                 for i in rec.get("items", [])]
        n_def_items += len(items)
        n_red += crit
        for i in items:
            def_count[i["label"]] += 1
        c = ("y" if covered else "f" if verd == "failed"
             else "p" if verd == "reached" else "n")
        n_failed += (verd == "failed")
        rows.append({"m": method, "p": path, "api": title or "", "c": c,
                     "s": st, "t": round(el / 1000, 1) if el is not None else None,
                     "src": src, "d": items, "ev": ev_run or None,
                     # 스펙 변경 배지: added|changed (미변경은 키 자체를 생략해
                     # ROWSJSON 크기를 안 늘림). chw = 배지 툴팁용 diff 시각.
                     **({"chg": chg_marks[key], "chw": chg_when}
                        if key in chg_marks else {})})

    # ---- untestable service: reachability-only framing ------------------
    if s.get("untestable"):
        n_reachable = sum(1 for r in rows if r["s"] is not None)
        action = (f"<b>기능 테스트 제외 서비스</b> — {html.escape(s['untestable'])} "
                  f"(owner 2026-06-13). smoke가 각 API의 <b>접근성만</b> 확인한다: "
                  f"{n_reachable}/{s['total']}개 엔드포인트가 응답(4xx 포함 = 도달). "
                  "커버리지 분모에서는 waiver로 제외되어 있다.")
        out = SVC_TEMPLATE
        for k, v in {
            "@@SVC@@": html.escape(s["service"]),
            "@@CAT@@": html.escape(s["category"]),
            "@@RINGPCT@@": "—", "@@RINGCOL@@": "#d0d7de",
            # untestable: verified(2xx) coverage is not the framing — show "—"
            # and let @@REACHED@@ + the action banner carry the reachability signal.
            "@@COV@@": "—", "@@TOT@@": str(s["total"]),
            "@@REACHED@@": str(s["reached"]),
            "@@GETPCT@@": "—", "@@GCOV@@": str(s["gcov"]), "@@GTOT@@": str(s["gtot"]),
            "@@WPCT@@": "—", "@@WCOV@@": str(s["wcov"]), "@@WTOT@@": str(s["wtot"]),
            "@@GETCOL@@": "#d0d7de", "@@WCOL@@": "#d0d7de",
            "@@NDEF@@": "0", "@@NRED@@": "0",
            "@@ACTION@@": action,
            "@@ROWSJSON@@": json.dumps(rows, ensure_ascii=False),
            "@@CONFEVAL@@": "true" if (meta.get("conf") or {}).get("evaluated") else "false",
            "@@WHEN@@": str(meta["when"]), "@@BRANCH@@": html.escape(meta["branch"]),
        }.items():
            out = out.replace(k, v)
        # h1만 회색 (title 태그는 평문 유지) + 사유 태그
        out = out.replace(
            f"<h1>{html.escape(s['service'])} <span class=\"tag\">",
            f"<h1><span style='color:#8b949e'>{html.escape(s['service'])}</span> "
            f"<span class=\"tag\">기능 테스트 제외</span> <span class=\"tag\">")
        return out

    # ---- auto action banner: weakest axis + dominant defect types ----
    bits = []
    if n_failed:
        bits.append(f"<b>신규 5xx/auth 실패 {n_failed}건 — 조치 필요.</b>")
    axis = (f"쓰기 커버리지 {wpct:.0f}%가 약점" if wpct <= gpct
            else f"읽기 커버리지 {gpct:.0f}%가 약점")
    bits.append(f"<b>다음 작업 후보:</b> {axis}. 미검증 쓰기 대부분은 부모 리소스 "
                "ID가 없어 404(probe 한계 = 정상) — CRUD 시나리오를 추가하면 도달 가능."
                if wpct <= gpct else
                f"<b>다음 작업 후보:</b> {axis} — read-chain/probe 보강 대상.")
    if not n_failed:
        bits.append("회귀 위험은 없음(신규 5xx/auth 0).")
    top = def_count.most_common(2)
    if top:
        bits.append("문서/설계 결함 중 가장 흔한 건 "
                    + "와 ".join(f"<b>{html.escape(t)}({n})</b>" for t, n in top)
                    + ".")
    action = " ".join(bits)

    out = SVC_TEMPLATE
    for k, v in {
        "@@SVC@@": html.escape(s["service"]),
        "@@CAT@@": html.escape(s["category"]),
        "@@RINGPCT@@": f"{pct:.0f}", "@@RINGCOL@@": ring_col,
        "@@COV@@": str(s["covered"]), "@@TOT@@": str(s["total"]),
        "@@REACHED@@": str(s["reached"]),
        "@@GETPCT@@": f"{gpct:.0f}", "@@GCOV@@": str(s["gcov"]), "@@GTOT@@": str(s["gtot"]),
        "@@WPCT@@": f"{wpct:.0f}", "@@WCOV@@": str(s["wcov"]), "@@WTOT@@": str(s["wtot"]),
        "@@GETCOL@@": "var(--green)" if gpct >= 55 else "var(--amber)" if gpct >= 25 else "var(--red)",
        "@@WCOL@@": "var(--green)" if wpct >= 55 else "var(--amber)" if wpct >= 25 else "var(--red)",
        "@@NDEF@@": str(n_def_items), "@@NRED@@": str(n_red),
        "@@ACTION@@": action,
        "@@ROWSJSON@@": json.dumps(rows, ensure_ascii=False),
        "@@CONFEVAL@@": "true" if (meta.get("conf") or {}).get("evaluated") else "false",
        "@@WHEN@@": str(meta["when"]), "@@BRANCH@@": html.escape(meta["branch"]),
    }.items():
        out = out.replace(k, v)
    return out


SVC_TEMPLATE = """<!doctype html><html lang="ko"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>@@SVC@@ · @@CAT@@ — coverage</title><style>
:root{--bg:#fbfcfd;--surface:#fff;--surface2:#f5f7f9;--border:#e4e7eb;
  --text:#1d2530;--muted:#6b7480;--faint:#9aa3ad;
  --green:#15924f;--green-bg:#e7f6ed;--amber:#b5740b;--amber-bg:#fdf3e2;
  --red:#c63434;--red-bg:#fbe9e9;--blue:#2563c9;--blue-bg:#e8f0fd;
  --grey:#7a838d;--grey-bg:#eef1f4;--purple:#7a45c2;--purple-bg:#f1e9fb;
  --shadow:0 1px 2px rgba(20,30,45,.05),0 1px 6px rgba(20,30,45,.04)}
*{box-sizing:border-box}
body{margin:0;background:var(--bg);color:var(--text);
  font-family:-apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,"Apple SD Gothic Neo","Malgun Gothic",'Noto Sans KR',sans-serif;
  font-size:13.5px;line-height:1.45;-webkit-font-smoothing:antialiased}
code,.mono{font-family:ui-monospace,SFMono-Regular,Menlo,Consolas,monospace}
a{color:var(--blue);text-decoration:none}a:hover{text-decoration:underline}
.wrap{max-width:1240px;margin:0 auto;padding:20px 22px 80px}
.crumb{color:var(--muted);font-size:12.5px;margin-bottom:14px}
.crumb b{color:var(--text)}
.hero{background:var(--surface);border:1px solid var(--border);border-radius:14px;
  box-shadow:var(--shadow);padding:18px 20px;margin-bottom:16px;
  display:flex;flex-wrap:wrap;gap:22px;align-items:center}
.hero h1{margin:0;font-size:21px;letter-spacing:-.2px;display:flex;align-items:center;gap:9px}
.tag{font-size:11px;font-weight:600;padding:2px 8px;border-radius:20px;background:var(--blue-bg);color:var(--blue)}
.gauge{display:flex;align-items:center;gap:14px}
.ring{width:62px;height:62px;border-radius:50%;flex:none;
  background:conic-gradient(@@RINGCOL@@ calc(@@RINGPCT@@*1%),var(--grey-bg) 0);
  display:grid;place-items:center;position:relative}
.ring::after{content:"";position:absolute;inset:7px;background:var(--surface);border-radius:50%}
.ring span{position:relative;font-weight:700;font-size:15px}
.gauge .lbl{font-size:12.5px;color:var(--muted)}
.gauge .lbl b{color:var(--text);font-size:13.5px}
.split{margin-left:auto;display:flex;gap:26px;flex-wrap:wrap}
.split .k{font-size:11.5px;color:var(--muted)}
.split .v{font-size:18px;font-weight:700;letter-spacing:-.3px}
.v .sub{font-size:11.5px;font-weight:500;color:var(--muted)}
.action{display:flex;gap:10px;align-items:flex-start;background:var(--amber-bg);
  border:1px solid #f0dcb6;border-radius:11px;padding:11px 14px;margin-bottom:16px;font-size:13px}
.action b{color:#8a5a08}
.controls{background:var(--bg);padding:10px 0 11px;margin-bottom:2px;border-bottom:1px solid var(--border)}
.row1{display:flex;flex-wrap:wrap;gap:10px;align-items:center}
.seg{display:inline-flex;border:1px solid var(--border);border-radius:9px;overflow:hidden;background:var(--surface)}
.seg button{border:0;background:transparent;padding:6px 11px;font-size:12.5px;font-weight:600;
  cursor:pointer;color:var(--muted);border-right:1px solid var(--border)}
.seg button:last-child{border-right:0}
.seg button[aria-pressed="true"]{color:#fff}
.seg button.m-get[aria-pressed="true"]{background:var(--blue)}
.seg button.m-post[aria-pressed="true"]{background:var(--green)}
.seg button.m-put[aria-pressed="true"]{background:var(--amber)}
.seg button.m-delete[aria-pressed="true"]{background:var(--red)}
select,.search input{border:1px solid var(--border);border-radius:9px;background:var(--surface);
  padding:6px 10px;font-size:12.5px;color:var(--text);font-family:inherit}
.search{position:relative}
.search input{width:230px;padding-left:30px}
.search .mag{position:absolute;left:9px;top:7px;color:var(--faint)}
.chiptog{display:inline-flex;align-items:center;gap:6px;border:1px solid var(--border);
  border-radius:9px;background:var(--surface);padding:6px 11px;font-size:12.5px;font-weight:600;
  color:var(--muted);cursor:pointer;user-select:none}
.chiptog input{accent-color:var(--blue);margin:0}
.chiptog.on{border-color:var(--blue);color:var(--blue);background:var(--blue-bg)}
.spacer{flex:1}
.reset{color:var(--muted);font-size:12px;cursor:pointer;border:0;background:none;text-decoration:underline}
.row2{display:flex;gap:16px;align-items:center;margin-top:9px;font-size:12px;color:var(--muted);flex-wrap:wrap}
.count b{color:var(--text);font-size:13px}
.legend{display:flex;gap:13px;flex-wrap:wrap;margin-left:auto}
.legend span{display:inline-flex;align-items:center;gap:5px}
.dot{width:9px;height:9px;border-radius:50%;display:inline-block}
.dot.g{background:var(--green)}.dot.a{background:var(--amber)}
.dot.r{background:var(--red)}.dot.s{background:var(--faint)}
.grp{margin-top:14px;border:1px solid var(--border);border-radius:12px;overflow:hidden;background:var(--surface);box-shadow:var(--shadow)}
.grp-h{display:flex;align-items:center;gap:11px;padding:10px 14px;cursor:pointer;
  background:var(--surface2);border-bottom:1px solid var(--border);user-select:none}
.grp-h .caret{transition:transform .15s;color:var(--muted);font-size:11px}
.grp.collapsed .caret{transform:rotate(-90deg)}
.grp.collapsed .grp-body{display:none}
.grp-h .name{font-weight:700;font-size:13.5px}
.mini{height:6px;width:130px;border-radius:4px;background:var(--grey-bg);overflow:hidden;display:inline-block}
.mini i{display:block;height:100%;background:var(--green)}
.grp-h .frac{font-size:12px;color:var(--muted)}
.grp-h .gd{margin-left:auto;font-size:11.5px;color:var(--amber);font-weight:600}
.tbl-scroll{overflow-x:auto;-webkit-overflow-scrolling:touch}
table{width:100%;border-collapse:collapse;table-layout:fixed;min-width:640px}
thead th{background:var(--surface2);text-align:left;font-size:11px;font-weight:600;color:var(--muted);
  padding:7px 12px;border-bottom:2px solid var(--border);text-transform:uppercase;letter-spacing:.3px}
thead th:nth-child(1){width:4px;padding:0}
thead th:nth-child(2){width:84px}
thead th:nth-child(3){width:30%}
thead th:nth-child(4){width:280px}
thead th:nth-child(5){width:50px}
thead th:nth-child(6){width:150px}
thead th:nth-child(7){width:110px}
tbody td{padding:8px 12px;border-bottom:1px solid #eef1f3;vertical-align:top}
tbody tr:last-child td{border-bottom:0}
tbody tr:hover{background:#fafbfc}
.cbar{width:4px;padding:0!important}
tr.cov-y .cbar{background:var(--green)}tr.cov-p .cbar{background:var(--amber)}
tr.cov-n .cbar{background:#dde2e7}tr.cov-f .cbar{background:var(--red)}
.mb{font-family:ui-monospace,monospace;font-size:11px;font-weight:800;letter-spacing:.4px;padding:3px 8px;border-radius:5px;display:inline-block;min-width:62px;text-align:center;color:#fff}
.mb.GET{background:#2f78c4}.mb.POST{background:#2c9a5e}.mb.PUT{background:#d97a0a}
.mb.DELETE{background:#d63a3a}.mb.PATCH{background:#7a45c2}
td.path code{font-size:12px;color:var(--text);word-break:normal;overflow-wrap:break-word}
td.api{font-family:ui-monospace,monospace;font-size:12px;color:var(--text);font-weight:600;word-break:normal;overflow-wrap:break-word}
.defbtn{border:0;background:var(--amber-bg);color:var(--amber);font-size:11px;font-weight:700;
  padding:3px 10px;border-radius:20px;cursor:pointer;display:inline-flex;align-items:center;gap:5px}
.defbtn .ca{font-size:9px;transition:transform .15s}
.defbtn.open .ca{transform:rotate(180deg)}
.defbtn:hover{filter:brightness(.97)}
.defbtn.crit{background:var(--red-bg);color:var(--red)}
.detailrow{display:none}
.detailrow.open{display:table-row}
.detailcell{background:var(--surface2);padding:9px 14px 12px;border-bottom:1px solid #eef1f3}
.defitem{padding:7px 10px;background:#fff;border:1px solid var(--border);border-left:3px solid var(--amber);
  border-radius:0 6px 6px 0;margin:5px 0;font-size:11.5px;max-width:860px}
.defitem.crit{border-left-color:var(--red)}
.defitem b{font-family:ui-monospace,monospace;color:var(--text);font-size:11.5px}
.defitem .meta{color:var(--faint);font-size:10.5px;margin-left:5px}
.defitem .dd{color:var(--muted);margin-top:3px;line-height:1.4;word-break:break-word}
.cov{font-size:14px;font-weight:700}
.cov.y{color:var(--green)}.cov.p{color:var(--amber)}.cov.n{color:var(--faint)}.cov.f{color:var(--red)}
.st{font-family:ui-monospace,monospace;font-size:11.5px;font-weight:700;padding:2px 7px;border-radius:6px;display:inline-block}
.st.s2{background:var(--green-bg);color:var(--green)}
.st.s404{background:var(--grey-bg);color:var(--grey)}
.st.s4{background:var(--amber-bg);color:var(--amber)}
.st.s5{background:var(--red-bg);color:var(--red)}
.st.none{color:var(--faint);font-weight:500}
.ms{font-size:11px;color:var(--muted);margin-left:6px}
.ms.warn{color:var(--amber);font-weight:600}
.ms.slow{color:var(--red);font-weight:700}
.probe{font-size:10.5px;color:var(--faint);margin-left:6px}
.prev{font-size:10px;color:var(--faint);margin-left:5px}
.chgbadge{display:inline-block;font-size:9.5px;font-weight:700;letter-spacing:.4px;
  padding:1px 6px;border-radius:9px;margin-left:6px;vertical-align:1px}
.chgbadge.added{background:#dafbe1;color:#116329;border:1px solid #aceebb}
.chgbadge.changed{background:#fff1c2;color:#7a5901;border:1px solid #f0d879}
.ok{color:var(--green);font-size:12px}
.empty{padding:40px;text-align:center;color:var(--muted)}
.foot{margin-top:22px;font-size:11.5px;color:var(--faint);border-top:1px solid var(--border);padding-top:12px}
@media (max-width:640px){
  table{table-layout:auto}
  thead th,tbody td{padding:6px 8px}
  /* MOBILE: keep 경로/API on one line so the wide table scrolls horizontally inside
     .tbl-scroll. Some mobile browsers (Samsung Internet / WebKit) ignore the table
     min-width, which let the break-anywhere cells collapse to 1 character per line
     ('경로'→경/로, path→one char per row). nowrap forces real content width. */
  td.path code,td.api{white-space:nowrap}
}
</style></head><body><div class="wrap">

<div class="crumb"><a href="../index.html">← 대시보드</a> / @@CAT@@ / <b>@@SVC@@</b></div>

<div class="hero">
  <div class="gauge">
    <div class="ring"><span>@@RINGPCT@@%</span></div>
    <div class="lbl">검증(2xx) 커버<br><b>@@COV@@ / @@TOT@@</b> ops · <span style="color:var(--amber)">◑ 도달·미검증 @@REACHED@@</span></div>
  </div>
  <h1>@@SVC@@ <span class="tag">@@CAT@@</span></h1>
  <div class="split">
    <div><div class="k">읽기 GET</div><div class="v" style="color:@@GETCOL@@">@@GETPCT@@% <span class="sub">@@GCOV@@/@@GTOT@@</span></div></div>
    <div><div class="k">쓰기 (POST/PUT/DELETE)</div><div class="v" style="color:@@WCOL@@">@@WPCT@@% <span class="sub">@@WCOV@@/@@WTOT@@</span></div></div>
    <div><div class="k">설계/동작 결함</div><div class="v">@@NDEF@@ <span class="sub">개선 · 결함 @@NRED@@</span></div></div>
  </div>
</div>

<div class="action"><div>@@ACTION@@</div></div>

<div class="controls">
  <div class="row1">
    <div class="seg" id="methodSeg">
      <button class="m-get" data-m="GET" aria-pressed="false">GET</button>
      <button class="m-post" data-m="POST" aria-pressed="false">POST</button>
      <button class="m-put" data-m="PUT" aria-pressed="false">PUT</button>
      <button class="m-delete" data-m="DELETE" aria-pressed="false">DELETE</button>
    </div>
    <select id="covSel">
      <option value="">커버 상태: 전체</option>
      <option value="y">✓ 검증(2xx)</option>
      <option value="p">◑ 도달·미검증</option>
      <option value="f">⛔ fail(5xx/auth)</option>
      <option value="n">· 미관측</option>
    </select>
    <select id="stSel">
      <option value="">status: 전체</option>
      <option value="2">2xx</option>
      <option value="404">404 (probe 미도달)</option>
      <option value="4">기타 4xx</option>
      <option value="5">5xx</option>
      <option value="none">미관측(빈칸)</option>
    </select>
    <label class="chiptog" id="defTog"><input type="checkbox"> 결함만</label>
    <label class="chiptog" id="slowTog"><input type="checkbox"> 느린 호출(≥3s)</label>
    <div class="search"><span class="mag">⌕</span><input id="q" placeholder="경로·API명 검색"></div>
    <div class="spacer"></div>
    <label class="chiptog on" id="grpTog"><input type="checkbox" checked> 리소스 그룹</label>
    <button class="reset" id="reset">초기화</button>
  </div>
  <div class="row2">
    <span class="count"><b id="shown"></b> / @@TOT@@ endpoints</span>
    <span class="legend">
      <span><i class="dot g"></i>검증 2xx</span>
      <span><i class="dot a"></i>도달·미검증</span>
      <span><i class="dot s"></i>미관측</span>
      <span><i class="dot r"></i>fail / 계약 위반</span>
    </span>
  </div>
</div>

<div id="out"></div>

<div class="foot">생성 @@WHEN@@ · branch <code>@@BRANCH@@</code> · 커버 기준: GET=실제 호출(smoke/CRUD probe), 쓰기=CRUD 스텝이 해당 method+path 실행 · @prev = 이번 런 미호출, 이전 런의 마지막 관측값</div>
</div>

<script>
var R=@@ROWSJSON@@;
var CONF_EVAL=@@CONFEVAL@@;   // 이 빌드 머신에서 per-endpoint 컨포먼스 점검이 실행됐는가
R.forEach(function(r){r.res=r.p.split('/')[2]||'기타';});
var state={methods:new Set(),cov:'',st:'',def:false,slow:false,q:'',group:true};
var out=document.getElementById('out');
function stClass(s){if(s==null)return'none';if(s>=200&&s<300)return's2';if(s===404)return's404';if(s>=500)return's5';return's4';}
function msClass(t){if(t==null)return'';if(t>=3)return'slow';if(t>=2)return'warn';return'';}
function matches(r){
  if(state.methods.size&&!state.methods.has(r.m))return false;
  if(state.cov&&r.c!==state.cov)return false;
  if(state.st){
    if(state.st==='2'&&!(r.s>=200&&r.s<300))return false;
    if(state.st==='404'&&r.s!==404)return false;
    if(state.st==='4'&&!(r.s>=400&&r.s<500&&r.s!==404))return false;
    if(state.st==='5'&&!(r.s>=500))return false;
    if(state.st==='none'&&r.s!=null)return false;
  }
  if(state.def&&r.d.length===0)return false;
  if(state.slow&&!(r.t>=3))return false;
  if(state.q){var q=state.q.toLowerCase();
    if(r.p.toLowerCase().indexOf(q)<0&&r.api.toLowerCase().indexOf(q)<0)return false;}
  return true;
}
function esc(t){return String(t).replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;');}
function defBtn(r){
  if(!r.d.length)return CONF_EVAL
    ?'<span class="ok">✓ 정상</span>'
    :'<span class="mut" title="이 머신에서 컨포먼스 점검(python -m conformance.static)이 실행된 적이 없어 판정 근거가 없습니다 — 근거 없음 ≠ 정상">미평가</span>';
  var crit=r.d.some(function(i){return i.crit;});
  return '<button class="defbtn'+(crit?' crit':'')+'" onclick="toggleDetail(this)">'
    +(crit?'결함':'개선')+' '+r.d.length+'<span class="ca">▾</span></button>';
}
function detailRowHTML(r){
  if(!r.d.length)return '';
  var items=r.d.map(function(i){
    return '<div class="defitem'+(i.crit?' crit':'')+'"><b>'+esc(i.label)+'</b>'
      +'<span class="meta">#'+esc(i.issue)+' · '+esc(i.kind)+'</span>'
      +'<div class="dd">'+esc(i.detail)+'</div></div>';
  }).join('');
  return '<tr class="detailrow"><td class="cbar"></td><td colspan="6" class="detailcell">'+items+'</td></tr>';
}
function toggleDetail(btn){
  var dr=btn.closest('tr').nextElementSibling;
  if(dr&&dr.classList.contains('detailrow')){dr.classList.toggle('open');btn.classList.toggle('open');}
}
function rowHTML(r){
  var covSym={y:'✓',p:'◑',n:'·',f:'⛔'}[r.c];
  var st;
  if(r.ev){
    // durable-evidence override (owner 2026-07-10): a cross-session 2xx exists
    // in data/baselines/verified_endpoints.json even though the last-known
    // OBSERVATION cell below is stale/missing/non-2xx — show BOTH, dated, per
    // CX-IA-DESIGN principle 1 (every number carries its source + timestamp).
    st='<span class="st s2" title="durable evidence — data/baselines/verified_endpoints.json">✅ 검증 ('+esc(r.ev)+')</span>';
    if(r.s!=null&&!(r.s>=200&&r.s<300)){
      st+='<span class="prev" title="대시보드 관측 이력(endpoint_status.json)에는 아직 이 값이 남아있음 — durable evidence가 우선">· 직전 관측 '+r.s+(r.src?' @'+esc(r.src):'')+'</span>';
    }
  } else {
    st=r.s==null?'<span class="st none">—</span>':'<span class="st '+stClass(r.s)+'">'+r.s+'</span>';
  }
  var ms=r.t!=null?'<span class="ms '+msClass(r.t)+'">'+(r.t<1?Math.round(r.t*1000)+'ms':r.t+'s')+'</span>':'';
  var probe=(r.c==='n'&&r.s===404)?'<span class="probe">probe 미도달</span>':'';
  var prev=(!r.ev&&r.src&&r.s!=null)?'<span class="prev" title="이번 런 미호출 — 마지막 관측 런">@'+esc(r.src)+'</span>':'';
  return '<tr class="cov-'+r.c+'">'
    +'<td class="cbar"></td>'
    +'<td><span class="mb '+r.m+'">'+r.m+'</span></td>'
    +'<td class="path"><code>'+esc(r.p)+'</code>'
    +(r.chg?'<span class="chgbadge '+r.chg+'" title="스펙 변경분 (diff @ '+esc(r.chw||'')+')">'+(r.chg==='added'?'NEW':'UPD')+'</span>':'')
    +'</td>'
    +'<td class="api">'+esc(r.api)+'</td>'
    +'<td><span class="cov '+r.c+'">'+covSym+'</span></td>'
    +'<td>'+st+ms+probe+prev+'</td>'
    +'<td class="defcell">'+defBtn(r)+'</td>'
    +'</tr>'+detailRowHTML(r);
}
var thead='<thead><tr><th class="cbar"></th><th>메서드</th><th>경로</th><th>API</th><th>커버</th><th>최근 status · 응답시간</th><th>설계/동작 결함</th></tr></thead>';
function render(){
  var rows=R.filter(matches);
  document.getElementById('shown').textContent=rows.length;
  if(rows.length===0){out.innerHTML='<div class="empty">조건에 맞는 엔드포인트가 없습니다.</div>';return;}
  if(!state.group){
    out.innerHTML='<div class="grp"><div class="tbl-scroll"><table>'+thead+'<tbody>'+rows.map(rowHTML).join('')+'</tbody></table></div></div>';
    return;
  }
  var groups={};
  rows.forEach(function(r){(groups[r.res]=groups[r.res]||[]).push(r);});
  var order=Object.keys(groups).sort(function(a,b){return groups[b].length-groups[a].length;});
  out.innerHTML=order.map(function(res){
    var g=groups[res];
    var cov=g.filter(function(r){return r.c==='y';}).length;
    var pct=Math.round(cov/g.length*100);
    var defs=g.reduce(function(n,r){return n+r.d.length;},0);
    return '<div class="grp"><div class="grp-h" onclick="this.parentNode.classList.toggle(\\'collapsed\\')">'
      +'<span class="caret">▼</span>'
      +'<span class="name">'+esc(res)+'</span>'
      +'<span class="frac">'+cov+'/'+g.length+' 검증</span>'
      +'<span class="mini"><i style="width:'+pct+'%"></i></span>'
      +(defs?'<span class="gd">개선 '+defs+'</span>':'')
      +'</div><div class="grp-body"><div class="tbl-scroll"><table>'+thead+'<tbody>'+g.map(rowHTML).join('')+'</tbody></table></div></div></div>';
  }).join('');
}
document.querySelectorAll('#methodSeg button').forEach(function(b){b.onclick=function(){
  var m=b.dataset.m,on=b.getAttribute('aria-pressed')==='true';
  b.setAttribute('aria-pressed',String(!on));on?state.methods.delete(m):state.methods.add(m);render();
};});
document.getElementById('covSel').onchange=function(e){state.cov=e.target.value;render();};
document.getElementById('stSel').onchange=function(e){state.st=e.target.value;render();};
function tog(id,key){var el=document.getElementById(id);el.querySelector('input').onchange=function(e){
  state[key]=e.target.checked;el.classList.toggle('on',e.target.checked);render();};}
tog('defTog','def');tog('slowTog','slow');
document.getElementById('grpTog').querySelector('input').onchange=function(e){
  state.group=e.target.checked;document.getElementById('grpTog').classList.toggle('on',e.target.checked);render();};
document.getElementById('q').oninput=function(e){state.q=e.target.value;render();};
document.getElementById('reset').onclick=function(){
  state.methods.clear();state.cov='';state.st='';state.def=false;state.slow=false;state.q='';
  document.querySelectorAll('#methodSeg button').forEach(function(b){b.setAttribute('aria-pressed','false');});
  document.getElementById('covSel').value='';document.getElementById('stSel').value='';
  document.getElementById('q').value='';
  ['defTog','slowTog'].forEach(function(id){var el=document.getElementById(id);
    el.querySelector('input').checked=false;el.classList.remove('on');});
  render();
};
render();
</script>
</body></html>"""


def _dist_color(code: int) -> str:
    fam = code // 100
    if fam == 2:
        return {200: "#15924f", 201: "#7cc69b", 202: "#3aa86a",
                204: "#a9d9bf"}.get(code, "#57b87f")
    if fam == 4:
        return {400: "#b5740b", 401: "#d28a1f", 403: "#c63434",
                404: "#cf8a3a", 409: "#9a6700"}.get(code, "#b5740b")
    if fam == 5:
        return "#c63434"
    return "#7a838d"


def _bar_color(pct: float) -> str:
    return "var(--red)" if pct < 25 else "var(--amber)" if pct < 55 else "var(--green)"


def render(d, hist, meta, services):
    called = d["ok"] + d["soft"] + d["fail_new"] + d["fail_known"]
    pass_rate = (d["ok"] / called * 100) if called else 0
    # 관측 0 가드 (오너 발견 2026-07-15): 결과 없는 재발행(0 calls)이 fail_new==0
    # 을 타고 HEALTHY '배포 안전'을 자동 선언하던 결함 — "관측 없음 ≠ 0" 규율
    # 위반. 관측이 없으면 판정을 유보하고 직전 실측 판정(history 마지막 행,
    # append 는 has_results 가드라 실측 런만 있음)을 인용한다.
    no_obs = not d.get("has_results")
    prev = hist[-1] if (no_obs and hist) else None
    healthy = d["fail_new"] == 0 and not no_obs
    if no_obs:
        pill = '<span class="pill" style="background:#f0f2f5;color:#57606a">NO NEW OBSERVATIONS</span>'
    elif healthy:
        pill = '<span class="pill"><span class="d"></span>HEALTHY</span>'
    else:
        pill = f'<span class="pill bad"><span class="d"></span>{d["fail_new"]} NEW REGRESSION(S)</span>'

    # ---- category aggregates (verified ops / total ops, services as source)
    cat_agg = {}
    for s in services:
        # untestable services (license/resource-blocked) are waived from the
        # coverage denominator — same as the JS drill-down's catMeta — so the
        # headline category bars don't get systematically depressed and stay
        # consistent with the drill-down (field report: storage 5% vs 8%).
        if s.get("untestable"):
            continue
        a = cat_agg.setdefault(s["category"], {"cov": 0, "tot": 0})
        a["cov"] += s["covered"]
        a["tot"] += s["total"]
    cats = sorted(({"name": c, "cov": a["cov"], "tot": a["tot"],
                    "pct": round(a["cov"] / a["tot"] * 100) if a["tot"] else 0}
                   for c, a in cat_agg.items()), key=lambda x: x["pct"])

    # ---- action banner ----
    if no_obs:
        # 판정 유보 — "배포 안전"도 "회귀"도 주장하지 않는다. 직전 실측 판정 인용.
        if prev:
            pf = prev.get("fail_new", 0)
            pv = (f'<b style="color:var(--red,#cf222e)">실패 · 신규 {pf}건</b>'
                  if pf else "통과 (신규 0건)")
            action = (f'<div class="action"><div><b>판정 불가 — 이번 발행에는 테스트 관측이 없습니다'
                      f' (0 calls).</b> 직전 실측 판정 유지: {pv}'
                      f' <span class="hint">({html.escape(str(prev.get("ts", "")))} 런 기준)</span>. '
                      f'수치 없는 갱신(스펙/문서)일 뿐 회귀가 해소된 것이 아닙니다.</div></div>')
        else:
            action = ('<div class="action"><div><b>판정 불가 — 테스트 관측이 없습니다.</b> '
                      '아직 실측 런이 발행되지 않았습니다 (관측 없음 ≠ 회귀 0).</div></div>')
    elif healthy:
        low = ", ".join(f'<b>{c["name"]} {c["pct"]}%</b>({c["cov"]}/{c["tot"]})'
                        for c in cats[:3])
        action = (f'<div class="action"><div><b>새 회귀 {d["fail_new"]}건 — 배포 안전.</b> '
                  f'신규 5xx/auth 실패 없음, known-red {d["fail_known"]}. '
                  f'<span class="next">다음 우선순위 →</span> 커버리지가 가장 낮은 곳은 '
                  f'{low}. 쓰기 시나리오 추가가 가장 큰 레버.</div></div>')
    else:
        items = "".join(f'<div><code>{html.escape(k)}</code> → {st}</div>'
                        for k, st, _ in d["new_regressions"][:6])
        action = (f'<div class="action bad"><div><b>새 회귀 {d["fail_new"]}건 — 조치 필요.</b>'
                  f'{items}</div></div>')

    # ---- health cards ----
    def card(value, vcls, label, sub, ok=False):
        return (f'<div class="card hc{" ok" if ok else ""}"><div class="n {vcls}">{value}</div>'
                f'<div class="t">{label}</div><div class="s">{sub}</div></div>')
    run_scope = "full CRUD" if d.get("crud_ran") else "read-only"
    # no_obs 면 '0'이 아니라 '—' — 관측 없음을 0으로 위장하지 않는다 (위 가드와 짝).
    cards = (card("—" if no_obs else d["fail_new"], "g" if healthy else "r",
                  "신규 회귀", "관측 없음 — 직전 판정 참조" if no_obs else "vs known baseline",
                  ok=healthy)
             + card(f"{pass_rate:.1f}%", "", "Pass rate", f'{d["ok"]} ok / {called} calls')
             + card(f'{d.get("cov_c3", 0):.1f}%', "a", "C3 검증 커버리지",
                    f'{d.get("c3_verified", 0)} / {d.get("c3_denom", d["total"])} · 목표 100%')
             + card(d["fail_known"], "r" if d["fail_known"] else "g", "Known-red",
                    "tracked backend bugs"))

    # ---- coverage ladder ----
    c3p = d.get("cov_c3", 0)
    c2p = d.get("reach_measured_pct", 0)
    c1p = d.get("reachable_pct", 0)
    rc = d.get("reach_covered", 0)
    # honest C3 sub-label: of the C3-satisfied set, how many are verified-2xx
    # vs reachability-covered (reached-only, license-gated services).
    c3_split = (f' (검증2xx {d.get("c3_verified_2xx", d.get("c3_verified", 0))} '
                f'+ 도달충족 {rc})' if rc else '')
    ladder = f'''
    <div class="lad"><span class="lv">C3</span><div class="bar"><i style="width:{c3p:.1f}%;background:var(--green)"></i></div><span class="pct" style="color:var(--green)">{c3p:.1f}%</span>
      <span class="desc"><b class="tip" title="2xx 동작 확인(검증) 또는 reachability-only 서비스의 도달충족(4xx 포함 응답=접근 확인). 도달충족은 검증2xx로 둔갑하지 않고 별도 집계.">충족</b> · {d.get("c3_verified", 0)}/{d.get("c3_denom", d["total"])}{c3_split} · {run_scope} · 목표 100%</span></div>
    <div class="lad"><span class="lv">C2</span><div class="bar"><i style="width:{c2p:.1f}%;background:var(--amber)"></i></div><span class="pct" style="color:var(--amber)">{c2p:.1f}%</span>
      <span class="desc"><b class="tip" title="4xx 포함 응답 수신 — 호출은 된다(404 POST/DELETE는 생성/삭제 안 함)">호출됨</b> · {d["tested"]}/{d["total"]} (이번 run)</span></div>
    <div class="lad"><span class="lv">C1</span><div class="bar"><i style="width:{c1p:.1f}%;background:var(--blue)"></i></div><span class="pct" style="color:var(--blue)">{c1p:.1f}%</span>
      <span class="desc"><b class="tip" title="시나리오 정적 도달 가능">도달가능</b> · {d.get("reachable", 0)}/{d["total"]}</span></div>
    <div class="lad-foot">읽기 GET 2xx <b>{d["cov_get"]:.1f}%</b> ({d["get_verified"]}/{d["get_total"]}) · 쓰기 2xx <b>{d["cov_write"]:.1f}%</b> ({d["write_hit"]}/{d["nonget_total"]}) · C2뿐인 write {d["write_reached"]} · waiver {d.get("waived_total", 0)}개(C2 충족 {d.get("waived_called", 0)}, 해제 후보 {d.get("waived_verified", 0)}) · reachability-only 도달충족 <b>{rc}</b>/{d.get("reach_waived", 0)}(미도달 {d.get("reach_uncovered", 0)}, 검증2xx 아님) · 남은 gap → write <b>{d.get("gap_write", 0)}</b> · id-bound GET <b>{d.get("gap_getid", 0)}</b></div>'''

    # ---- response code distribution ----
    segs = sorted(d["dist"].items())
    tot_calls = sum(d["dist"].values()) or 1
    distbar = "".join(
        f'<div style="flex:{n};background:{_dist_color(int(c))}" title="{c} · {n}">'
        f'{n if n / tot_calls > 0.06 else ""}</div>' for c, n in segs) or \
        '<div style="flex:1;background:var(--grey-bg);color:var(--muted)">no calls</div>'
    distleg = "".join(
        f'<span><i style="background:{_dist_color(int(c))}"></i><code>{c}</code> · {n}</span>'
        for c, n in segs)

    # ---- category bars (ascending = backlog) ----
    catrows = "".join(
        f'<div class="catrow"><div class="cn">{html.escape(c["name"])}'
        f'<span class="ops">{c["tot"]} ops</span>'
        f'{"<span class=blind>사각지대</span>" if c["cov"] == 0 else ""}</div>'
        f'<div class="cbar"><i style="width:{c["pct"]}%;background:{_bar_color(c["pct"])}"></i></div>'
        f'<div class="cp">{c["pct"]}% <span class="frac">{c["cov"]}/{c["tot"]}</span></div></div>'
        for c in cats)

    # ---- spec change panel (owner 2026-07-14 "변경된 api는 update로 표시") ----
    # data/spec_diff_latest.json 이 있으면 인덱스 상단에 변경 요약 패널을 붙인다.
    # 상세(NEW/UPD 배지)는 서비스 페이지 행에서 확인.
    sd = meta.get("spec_diff") or {}
    sds = sd.get("summary") or {}
    if sds and (sds.get("added") or sds.get("changed") or sds.get("removed")):
        _sd_rows = "".join(
            f'<tr><td><span class="chgbadge added">NEW</span></td>'
            f'<td><code>{html.escape(e["key"])}</code> '
            f'<span class="scope">{html.escape(e.get("sig", ""))}</span></td></tr>'
            for e in (sd.get("added") or [])[:8]) + "".join(
            f'<tr><td><span class="chgbadge changed">UPD</span></td>'
            f'<td><code>{html.escape(c["key"])}</code> '
            f'<span class="scope">{html.escape(", ".join(c.get("fields", {})))}'
            f'</span></td></tr>'
            for c in (sd.get("changed") or [])[:8])
        _sd_more = max(0, (sds.get("added", 0) + sds.get("changed", 0)) - 16)
        specdiff = f'''<h2>스펙 변경 <span class="hint">diff @ {html.escape(str(sd.get("generated_at") or ""))} · 서비스 페이지 행의 NEW/UPD 배지로 상세 확인</span></h2>
  <div class="card di">
    <div class="di-stat">
      <div><span class="din green">{sds.get("added", 0)}</span>추가<small>NEW 엔드포인트</small></div>
      <div><span class="din amber">{sds.get("changed", 0)}</span>변경<small>method/path/버전</small></div>
      <div><span class="din red">{sds.get("removed", 0)}</span>제거<small>retire 대상</small></div>
      <div class="ditot">기준 {sds.get("total_old", 0)} → {sds.get("total_new", 0)} API</div>
    </div>
    <table class="di-tbl"><tbody>{_sd_rows}</tbody></table>
    {f'<p class="dinote">… 외 {_sd_more}개 (서비스 페이지에서 배지로 확인)</p>' if _sd_more else ''}
  </div>'''
    else:
        specdiff = ""

    # ---- design integrity ----
    conf = meta.get("conf", {}) or {}
    cs = conf.get("summary", {})
    if cs:
        sys_rows = "".join(
            f'<tr><td class="dic amber">개선 {it.get("count") or ""}</td>'
            f'<td><code>{html.escape(it["type"])}</code> '
            f'<span class="scope">#{it["issue"]} · {html.escape(it["scope"])}</span><br>'
            f'<span class="desc">{html.escape(it["detail"])}</span></td></tr>'
            for it in conf.get("systemic", []))
        di = f'''<h2>설계/동작 정합성 <span class="hint">회귀(호출 성공)와 별개 · 정적+런타임 점검에서 찾은 설계·구현 결함</span></h2>
  <div class="card di">
    <div class="di-stat">
      <div><span class="din red">{cs.get("red", 0)}</span>결함<small>계약 위반 구현버그</small></div>
      <div><span class="din amber">{cs.get("yellow", 0)}</span>개선<small>설계/문서 결함</small></div>
      <div><span class="din green">{cs.get("green", 0)}</span>정상<small>API별 고유 이슈 없음</small></div>
      <div class="ditot">총 {cs.get("total", 0)} API</div>
    </div>
    <p class="dinote">※ "정상"이어도 아래 <b>플랫폼 전역 항목</b>은 공통 적용됩니다. 서비스 클릭 → API별 <b>설계/동작 결함</b> 열에서 상세 확인.</p>
    <table class="di-tbl"><thead><tr><th>건수</th><th>플랫폼 전역 점검 항목 (모든 서비스 공통)</th></tr></thead>
    <tbody>{sys_rows}</tbody></table></div>'''
    else:
        di = ""

    # ---- services / crud JSON for the client-side controls ----
    svc_json = json.dumps([
        {"n": s["service"], "c": s["category"], "cov": s["covered"],
         "tot": s["total"], "u": f'services/{s["slug"]}.html',
         "rch": s["covered"] + s["reached"], "unt": s["untestable"]}
        for s in services], ensure_ascii=False)
    crud_states = []
    for id_, kind, st, steps in d["crud_rows"]:
        state = st
        if st == "skip" and kind == "heavy":
            state = "gated"
        crud_states.append({"name": id_, "w": kind, "steps": steps, "state": state})
    crud_json = json.dumps(crud_states, ensure_ascii=False)
    n_fail = sum(1 for c in crud_states if c["state"] == "fail")

    # ---- known issues + trends (kept from the original design) ----
    knrows = "".join(
        f'<tr><td><code>{html.escape(k)}</code></td><td>{s}</td><td>Product Bug</td></tr>'
        for k, s, _ in d["known_red"]) or \
        '<tr><td colspan="3" class="kn-none">none</td></tr>'
    pr_series = [h["ok"] / max(1, h["ok"] + h["soft"] + h["fail_new"] + h["fail_known"]) * 100
                 for h in hist[-12:]]
    c1_series = [h.get("reachable_pct", h.get("cov_op", 0)) for h in hist[-12:]]
    c3_series = [h.get("cov_c3", h.get("cov_op", 0)) for h in hist[-12:]]

    out = TEMPLATE
    for k, v in {
        "@@BRANCH@@": html.escape(meta["branch"]), "@@WHEN@@": str(meta["when"]),
        "@@RUNTYPE@@": str(meta["run_type"]), "@@PILL@@": pill,
        "@@ACTION@@": action, "@@CARDS@@": cards, "@@LADDER@@": ladder,
        "@@NCALLS@@": str(called), "@@DISTBAR@@": distbar, "@@DISTLEG@@": distleg,
        "@@CATROWS@@": catrows, "@@DI@@": specdiff + di,
        "@@SVCJSON@@": svc_json, "@@CRUDJSON@@": crud_json,
        "@@CRUDFAIL@@": str(n_fail),
        "@@KNROWS@@": knrows, "@@RUNS@@": str(len(hist)),
        "@@SPARKPR@@": spark(pr_series, color="#15924f"),
        "@@SPARKCOV@@": spark_multi([(c1_series, "#15924f"), (c3_series, "#2563c9")]),
    }.items():
        out = out.replace(k, v)
    return out


TEMPLATE = """<!doctype html><html lang="ko"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>SCP API Regression — Dashboard</title><style>
:root{--bg:#fbfcfd;--surface:#fff;--surface2:#f5f7f9;--border:#e4e7eb;
  --text:#1d2530;--muted:#6b7480;--faint:#9aa3ad;
  --green:#15924f;--green-bg:#e7f6ed;--amber:#b5740b;--amber-bg:#fdf3e2;
  --red:#c63434;--red-bg:#fbe9e9;--blue:#2563c9;--blue-bg:#e8f0fd;
  --grey:#7a838d;--grey-bg:#eef1f4;
  --shadow:0 1px 2px rgba(20,30,45,.05),0 1px 6px rgba(20,30,45,.04)}
*{box-sizing:border-box}
body{margin:0;background:var(--bg);color:var(--text);
  font-family:-apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,"Apple SD Gothic Neo","Malgun Gothic",'Noto Sans KR',sans-serif;
  font-size:13.5px;line-height:1.45;-webkit-font-smoothing:antialiased}
code,.mono{font-family:ui-monospace,SFMono-Regular,Menlo,Consolas,monospace}
a{color:var(--blue);text-decoration:none}a:hover{text-decoration:underline}
.wrap{max-width:1180px;margin:0 auto;padding:20px 22px 80px}
h2{font-size:14px;letter-spacing:.2px;margin:30px 0 12px;display:flex;align-items:center;gap:8px}
h2 .hint{font-size:11.5px;font-weight:400;color:var(--muted)}
.card{background:var(--surface);border:1px solid var(--border);border-radius:13px;box-shadow:var(--shadow)}
.top{display:flex;align-items:baseline;gap:12px;flex-wrap:wrap;margin-bottom:6px}
.top h1{margin:0;font-size:22px;letter-spacing:-.3px}
.top .meta{color:var(--muted);font-size:12.5px}
.pill{display:inline-flex;align-items:center;gap:6px;font-size:12px;font-weight:700;
  padding:4px 11px;border-radius:20px;background:var(--green-bg);color:var(--green)}
.pill .d{width:8px;height:8px;border-radius:50%;background:var(--green)}
.pill.bad{background:var(--red-bg);color:var(--red)}.pill.bad .d{background:var(--red)}
.action{display:flex;gap:11px;align-items:flex-start;background:var(--green-bg);
  border:1px solid #c2e6d0;border-radius:12px;padding:13px 16px;margin:14px 0 8px;font-size:13.5px}
.action b{color:#0d6e3a}.action .next{color:var(--amber)}
.action.bad{background:var(--red-bg);border-color:#eec3c3}.action.bad b{color:var(--red)}
.health{display:grid;grid-template-columns:repeat(4,1fr);gap:12px;margin-top:14px}
.hc{padding:15px 16px}
.hc .n{font-size:30px;font-weight:800;letter-spacing:-1px;line-height:1}
.hc .n.g{color:var(--green)}.hc .n.a{color:var(--amber)}.hc .n.r{color:var(--red)}
.hc .t{font-size:12.5px;font-weight:600;margin-top:7px}
.hc .s{font-size:11.5px;color:var(--muted);margin-top:2px}
.hc.ok{background:linear-gradient(180deg,#f3fbf6,#fff)}
.ladder{padding:16px 18px}
.lad{display:flex;align-items:center;gap:13px;margin:11px 0}
.lad .lv{font-family:ui-monospace,monospace;font-size:11px;font-weight:700;color:var(--muted);width:30px}
.lad .bar{flex:1;height:22px;border-radius:6px;background:var(--grey-bg);overflow:hidden;position:relative}
.lad .bar i{display:block;height:100%;border-radius:6px}
.lad .pct{width:54px;text-align:right;font-weight:700;font-size:13px}
.lad .desc{width:340px;font-size:12px;color:var(--muted)}
.lad .desc b{color:var(--text)}
.lad-foot{font-size:11.5px;color:var(--muted);margin-top:10px;border-top:1px solid var(--border);padding-top:10px}
.dist{padding:16px 18px}
.distbar{display:flex;height:26px;border-radius:7px;overflow:hidden;border:1px solid var(--border)}
.distbar div{display:flex;align-items:center;justify-content:center;color:#fff;font-size:11px;font-weight:700;min-width:24px}
.distleg{display:flex;gap:16px;flex-wrap:wrap;margin-top:11px;font-size:12px}
.distleg span{display:inline-flex;align-items:center;gap:6px;color:var(--muted)}
.distleg i{width:10px;height:10px;border-radius:3px;display:inline-block}
.cats{padding:8px 18px 16px}
.catrow{display:flex;align-items:center;gap:12px;padding:8px 0;border-bottom:1px solid #eef1f3}
.catrow:last-child{border:0}
.catrow .cn{width:200px;font-size:12.5px;font-weight:600}
.catrow .cn .ops{font-weight:400;color:var(--faint);font-size:11px;margin-left:4px}
.catrow .cbar{flex:1;height:16px;border-radius:5px;background:var(--grey-bg);overflow:hidden}
.catrow .cbar i{display:block;height:100%;border-radius:5px}
.catrow .cp{width:96px;text-align:right;font-size:12px;font-weight:700}
.catrow .cp .frac{font-weight:400;color:var(--muted);font-size:11px}
.blind{font-size:11px;font-weight:700;color:var(--red);background:var(--red-bg);padding:1px 7px;border-radius:10px;margin-left:8px}
.svc-controls{display:flex;gap:10px;align-items:center;margin-bottom:10px;flex-wrap:wrap}
.svc-controls select,.svc-controls input{border:1px solid var(--border);border-radius:9px;background:var(--surface);padding:6px 10px;font-size:12.5px;font-family:inherit;color:var(--text)}
.svc-controls input{width:200px}
.svcgrid{display:grid;grid-template-columns:repeat(auto-fill,minmax(225px,1fr));gap:10px}
.svc-cell{display:flex;flex-direction:column}
.svc{display:block;padding:11px 13px;background:var(--surface);border:1px solid var(--border);border-radius:11px;box-shadow:var(--shadow);transition:.12s}
.svc.unt .svc-n,.svc.unt .sn{color:#8b949e}
.svc.unt{background:#fafbfc;border-style:dashed}
.unt-badge{display:inline-block;font-size:10px;color:#8b949e;border:1px solid #d0d7de;border-radius:8px;padding:0 6px;vertical-align:1px}
.svc:hover{border-color:var(--blue);text-decoration:none;transform:translateY(-1px)}
.svc .sh{display:flex;justify-content:space-between;align-items:baseline;gap:8px}
.svc .sn{font-weight:700;font-size:13px;color:var(--text)}
.svc .sp{font-size:13px;font-weight:800}
.svc .sbar{height:7px;border-radius:4px;background:var(--grey-bg);overflow:hidden;margin:8px 0 4px}
.svc .sbar i{display:block;height:100%;border-radius:4px}
.svc .sfrac{font-size:11px;color:var(--muted)}
.catsec{margin-bottom:16px}
.catsec-h{display:flex;align-items:center;gap:12px;padding:7px 4px 9px;border-bottom:2px solid var(--border);margin-bottom:11px}
.catsec-h .csn{font-weight:800;font-size:14.5px;letter-spacing:-.2px}
.catsec-h .cso{font-family:ui-monospace,monospace;font-size:12px;color:var(--muted)}
.catsec-h .csbar{width:130px;height:8px;border-radius:5px;background:var(--grey-bg);overflow:hidden}
.catsec-h .csbar i{display:block;height:100%;border-radius:5px}
.catsec-h .csp{font-size:14px;font-weight:800}
.catsec-h .cscount{margin-left:auto;font-size:11.5px;color:var(--faint)}
.crud-sum{display:flex;gap:10px;flex-wrap:wrap;margin-bottom:10px}
.badge{display:inline-flex;align-items:center;gap:7px;border:1px solid var(--border);border-radius:9px;
  background:var(--surface);padding:7px 12px;font-size:12.5px;font-weight:600;cursor:pointer;color:var(--muted)}
.badge .c{font-size:15px;font-weight:800;color:var(--text)}
.badge.on{border-color:var(--blue);background:var(--blue-bg)}
.badge.pass .c{color:var(--green)}.badge.gated .c{color:var(--amber)}.badge.skip .c{color:var(--grey)}.badge.fail .c{color:var(--red)}
#crudList{display:none;margin-top:4px}
#crudList.show{display:block}
.crud-tbl{width:100%;border-collapse:collapse;font-size:12.5px}
.crud-tbl td{padding:7px 12px;border-bottom:1px solid #eef1f3}
.crud-tbl tr:hover{background:#fafbfc}
.cstate{font-size:10.5px;font-weight:700;padding:2px 8px;border-radius:20px}
.cstate.pass{background:var(--green-bg);color:var(--green)}
.cstate.gated{background:var(--amber-bg);color:var(--amber)}
.cstate.skip{background:var(--grey-bg);color:var(--grey)}
.cstate.fail{background:var(--red-bg);color:var(--red)}
.wt{font-size:10.5px;color:var(--faint);text-transform:uppercase}
.toggle{font-size:12px;color:var(--blue);cursor:pointer;background:none;border:0;text-decoration:underline;padding:0}
.di{padding:16px 18px}
.di-stat{display:flex;gap:26px;flex-wrap:wrap;align-items:baseline;padding-bottom:12px;border-bottom:1px solid var(--border)}
.di-stat>div{font-size:12.5px;color:var(--muted)}
.din{font-size:24px;font-weight:800;letter-spacing:-.5px;margin-right:5px}
.din.red{color:var(--red)}.din.amber{color:var(--amber)}.din.green{color:var(--green)}
.di-stat small{display:block;font-size:11px;color:var(--faint);margin-top:1px}
.di-stat .ditot{margin-left:auto;font-weight:700;color:var(--text)}
.dinote{font-size:12px;color:var(--muted);margin:11px 0 13px}
.di-tbl{width:100%;border-collapse:collapse;font-size:12.5px}
.chgbadge{display:inline-block;font-size:9.5px;font-weight:700;letter-spacing:.4px;
  padding:1px 6px;border-radius:9px;vertical-align:1px}
.chgbadge.added{background:#dafbe1;color:#116329;border:1px solid #aceebb}
.chgbadge.changed{background:#fff1c2;color:#7a5901;border:1px solid #f0d879}
.di-tbl th{text-align:left;font-size:11px;font-weight:600;color:var(--muted);padding:6px 10px;text-transform:uppercase;letter-spacing:.3px;border-bottom:1px solid var(--border)}
.di-tbl td{padding:8px 10px;border-bottom:1px solid #eef1f3;vertical-align:top}
.di-tbl tr:last-child td{border-bottom:0}
.dic{font-weight:700;white-space:nowrap}.dic.amber{color:var(--amber)}
.di-tbl code{font-size:11.5px;color:var(--text);font-weight:600}
.di-tbl .scope{font-size:11px;color:var(--faint)}
.di-tbl .desc{color:var(--muted);font-size:12px}
.kn-tbl{width:100%;border-collapse:collapse;font-size:12.5px}
.kn-tbl th{text-align:left;font-size:11px;font-weight:600;color:var(--muted);padding:6px 12px;text-transform:uppercase;letter-spacing:.3px;border-bottom:1px solid var(--border)}
.kn-tbl td{padding:7px 12px;border-bottom:1px solid #eef1f3}
.kn-none{color:var(--faint)}
.trendgrid{display:grid;grid-template-columns:1fr 1fr;gap:14px}
.trend{padding:14px 16px}.trend .tl{font-size:12px;color:var(--muted);margin-bottom:6px}
.tip{cursor:help;border-bottom:1px dotted var(--faint)}
.foot{margin-top:26px;font-size:11.5px;color:var(--faint);border-top:1px solid var(--border);padding-top:12px}
@media(max-width:720px){.health{grid-template-columns:repeat(2,1fr)}.lad .desc{display:none}.catrow .cn{width:130px}.di-stat{gap:16px}.trendgrid{grid-template-columns:1fr}}
</style></head><body><div class="wrap">

<div class="top">
  <h1>SCP API Regression</h1>
  @@PILL@@
  <span class="meta">branch <code>@@BRANCH@@</code> · 최근 실행 @@WHEN@@ · @@RUNTYPE@@</span>
  <div class="links" style="margin-top:7px;font-size:13px">
    플랫폼:
    <a href="console2/app/" title="Platform — SCP API 테스트 실행 콘솔 (서비스 선택 → 합성 DAG → LIVE 실행 · VPC 동시 한도/대기 큐). 이 대시보드가 그 실행 결과입니다.">🧩 Platform 실행 콘솔 →</a> ·
    <a href="platform/knowledge.html">📚 Knowledge</a>
    &nbsp;|&nbsp; <a href="ops.html">Ops</a> ·
    <a href="platform/console.html" title="legacy 자원 모델 콘솔 (Catalog·Plan·Run·Report)">🗂 legacy 콘솔</a>
  </div>
</div>

@@ACTION@@

<div class="health">@@CARDS@@</div>

<h2>커버리지 사다리 <span class="hint">C1 도달 → C2 호출 → C3 검증 (docs/COVERAGE-CRITERIA.md)</span></h2>
<div class="card ladder">@@LADDER@@</div>

<h2>응답 코드 분포 <span class="hint">@@NCALLS@@ calls</span></h2>
<div class="card dist">
  <div class="distbar">@@DISTBAR@@</div>
  <div class="distleg">@@DISTLEG@@</div>
</div>

<h2>카테고리별 커버리지 <span class="hint">검증 ops 기준 · 낮은 순 = 작업 백로그</span></h2>
<div class="card cats">@@CATROWS@@</div>

@@DI@@

<h2>서비스 드릴다운 <span class="hint">카테고리별 · 클릭 → API별 커버 현황 + 설계/동작 결함</span></h2>
<div class="svc-controls">
  <select id="svcSort">
    <option value="pct">서비스: 커버리지 낮은 순</option>
    <option value="pctd">서비스: 커버리지 높은 순</option>
    <option value="ops">서비스: 규모(ops) 큰 순</option>
    <option value="name">서비스: 이름순</option>
  </select>
  <select id="catOrder">
    <option value="name">카테고리: 이름순</option>
    <option value="pct">카테고리: 커버리지 낮은 순</option>
    <option value="ops">카테고리: 규모(ops) 큰 순</option>
  </select>
  <input id="svcSearch" placeholder="서비스 검색">
  <span style="font-size:12px;color:var(--muted)"><b id="svcCount"></b>개 서비스 · <b id="catCount"></b>개 카테고리</span>
</div>
<div id="svcWrap"></div>

<h2>CRUD 라이프사이클 <span class="hint">대부분 skip/gated — 기본 접힘</span></h2>
<div class="crud-sum" id="crudSum"></div>
<button class="toggle" id="crudToggle">전체 시나리오 펼치기 ▾</button>
<div class="card" id="crudList"><table class="crud-tbl"><tbody id="crudBody"></tbody></table></div>

<h2>추세 <span class="hint">@@RUNS@@ runs 누적 (dashboard-data 브랜치)</span></h2>
<div class="trendgrid">
  <div class="card trend"><div class="tl">성공률</div>@@SPARKPR@@</div>
  <div class="card trend"><div class="tl">커버리지 — <span style="color:var(--green)">C1 도달가능</span> · <span style="color:var(--blue)">C3 검증(목표)</span></div>@@SPARKCOV@@</div>
</div>

<h2>알려진 이슈 <span class="hint">data/baselines/known_issues.json</span></h2>
<div class="card"><table class="kn-tbl">
<thead><tr><th>endpoint</th><th>status</th><th>유형</th></tr></thead>
<tbody>@@KNROWS@@</tbody></table></div>

<div class="foot">생성 <code>dashboard/build.py</code> ← unified results store (reports/results/*.jsonl) · 추세 <code>dashboard-data</code> 브랜치 · 배포 GitHub Pages</div>
</div>

<script>
var SVCS=@@SVCJSON@@;
var CRUD=@@CRUDJSON@@;
function barColor(p){return p<25?'var(--red)':p<55?'var(--amber)':'var(--green)';}
var catMeta={};
SVCS.forEach(function(s){
  s.pct=s.tot?Math.round(s.cov/s.tot*100):0;
  var m=catMeta[s.c]||(catMeta[s.c]={cov:0,tot:0});
  // untestable 서비스(라이선스/자원 부재)는 커버리지 분모에서 waiver 제외 —
  // 카테고리 %를 끌어내리거나 '사각지대'로 오인되지 않게 집계에서 뺀다.
  if(s.unt)return;
  m.cov+=s.cov;m.tot+=s.tot;
});
Object.keys(catMeta).forEach(function(c){var m=catMeta[c];m.pct=m.tot?Math.round(m.cov/m.tot*100):0;});
function svcCard(s){
  if(s.unt){
    // 라이선스/자원 부재로 기능 테스트 제외 — 각 API의 접근성(도달)만 확인.
    return '<div class="svc-cell"><a class="svc unt" href="'+s.u+'" title="기능 테스트 제외 — '+s.unt+'">'
      +'<div class="sh"><span class="sn">'+s.n+'</span><span class="unt-badge">접근성만</span></div>'
      +'<div class="sbar"><i style="width:100%;background:#d0d7de"></i></div>'
      +'<div class="sfrac">기능 테스트 제외 · '+s.rch+'/'+s.tot+' API 도달</div></a></div>';
  }
  return '<div class="svc-cell"><a class="svc" href="'+s.u+'">'
    +'<div class="sh"><span class="sn">'+s.n+'</span><span class="sp" style="color:'+barColor(s.pct)+'">'+s.pct+'%</span></div>'
    +'<div class="sbar"><i style="width:'+s.pct+'%;background:'+barColor(s.pct)+'"></i></div>'
    +'<div class="sfrac">'+s.cov+'/'+s.tot+' ops 검증</div></a></div>';
}
function renderSvcs(){
  var q=document.getElementById('svcSearch').value.toLowerCase();
  var sort=document.getElementById('svcSort').value;
  var corder=document.getElementById('catOrder').value;
  var categories=Object.keys(catMeta);
  categories.sort(function(a,b){
    if(corder==='pct')return catMeta[a].pct-catMeta[b].pct;
    if(corder==='ops')return catMeta[b].tot-catMeta[a].tot;
    return a.localeCompare(b);
  });
  var htmlOut='',totSvc=0,totCat=0;
  categories.forEach(function(cat){
    var list=SVCS.filter(function(s){return s.c===cat&&(!q||s.n.indexOf(q)>=0);});
    if(!list.length)return;
    list.sort(function(a,b){
      if(sort==='pct')return a.pct-b.pct;
      if(sort==='pctd')return b.pct-a.pct;
      if(sort==='ops')return b.tot-a.tot;
      return a.n.localeCompare(b.n);
    });
    totSvc+=list.length;totCat++;
    var m=catMeta[cat];
    htmlOut+='<div class="catsec"><div class="catsec-h">'
      +'<span class="csn">'+cat+'</span>'
      +'<span class="cso">'+m.cov+'/'+m.tot+' ops</span>'
      +'<span class="csbar"><i style="width:'+m.pct+'%;background:'+barColor(m.pct)+'"></i></span>'
      +'<span class="csp" style="color:'+barColor(m.pct)+'">'+m.pct+'%</span>'
      +(m.cov===0?'<span class="blind">사각지대</span>':'')
      +'<span class="cscount">'+list.length+' svc</span>'
      +'</div><div class="svcgrid">'+list.map(svcCard).join('')+'</div></div>';
  });
  document.getElementById('svcWrap').innerHTML=htmlOut||'<div style="color:var(--muted)">검색 결과 없음</div>';
  document.getElementById('svcCount').textContent=totSvc;
  document.getElementById('catCount').textContent=totCat;
}
document.getElementById('svcSort').onchange=renderSvcs;
document.getElementById('catOrder').onchange=renderSvcs;
document.getElementById('svcSearch').oninput=renderSvcs;
renderSvcs();

var counts={pass:0,gated:0,skip:0,fail:0};
CRUD.forEach(function(c){counts[c.state]=(counts[c.state]||0)+1;});
var crudFilter=null;
var ORDER={fail:0,pass:1,gated:2,skip:3};
function showList(){document.getElementById('crudList').classList.add('show');
  document.getElementById('crudToggle').textContent='전체 시나리오 접기 ▴';}
function renderSum(){
  var defs=[['fail','fail',counts.fail],['pass','통과',counts.pass],
            ['gated','gated (heavy)',counts.gated],['skip','skip (light)',counts.skip]];
  var el=document.getElementById('crudSum');
  el.innerHTML=defs.filter(function(d){return d[2]>0;}).map(function(d){
    return '<span class="badge '+d[0]+(crudFilter===d[0]?' on':'')+'" data-k="'+d[0]+'">'
      +'<span class="c">'+d[2]+'</span> '+d[1]+'</span>';
  }).join('')+'<span class="badge'+(crudFilter===null?' on':'')+'" data-k="all">'
    +'<span class="c">'+CRUD.length+'</span> 전체</span>';
  el.querySelectorAll('.badge').forEach(function(b){
    b.onclick=function(){crudFilter=b.dataset.k==='all'?null:b.dataset.k;
      showList();renderSum();renderCrud();};
  });
}
function renderCrud(){
  var list=CRUD.filter(function(c){return !crudFilter||c.state===crudFilter;})
    .slice().sort(function(a,b){return (ORDER[a.state]-ORDER[b.state])||(b.steps-a.steps);});
  document.getElementById('crudBody').innerHTML=list.map(function(c){
    return '<tr><td><span class="cstate '+c.state+'">'+c.state+'</span></td>'
      +'<td><code>'+c.name+'</code></td>'
      +'<td class="wt">'+c.w+' · '+c.steps+' steps</td></tr>';
  }).join('');
}
document.getElementById('crudToggle').onclick=function(){
  var el=document.getElementById('crudList');el.classList.toggle('show');
  this.textContent=el.classList.contains('show')?'전체 시나리오 접기 ▴':'전체 시나리오 펼치기 ▾';
};
renderSum();renderCrud();
if(@@CRUDFAIL@@>0){crudFilter='fail';showList();renderSum();renderCrud();}
</script>
</body></html>"""


# ---------------------------------------------------------------------------
# Ops viewer (ops.html) — build-time DEP-map injection
# ---------------------------------------------------------------------------

# dashboard/ops.html is the SOURCE template (markers + a last-known map). The
# build injects a FRESH kind→parent dependency map (computed from the resource
# model) between these markers and writes the result to <outdir>/ops.html, so
# the published viewer can never go stale (retires the old manual gen_dep_map
# paste — see docs/IA.md WS3 / docs/OPS-DASHBOARD.md).

OPS_TEMPLATE = os.path.join(os.path.dirname(__file__), "ops.html")
_DEP_BEGIN = "// ---- DEP-MAP"
_DEP_END = "// ---- /DEP-MAP ----"


def emit_ops_html(outdir, src=OPS_TEMPLATE):
    """Render <outdir>/ops.html from the source template with a freshly computed
    DEP map injected between the DEP-MAP markers. Tolerant: any failure
    (missing template / unparseable model) falls back to copying the template
    verbatim and never crashes the build."""
    try:
        with open(src, encoding="utf-8") as fh:
            page = fh.read()
    except OSError:
        return None  # no template -> nothing to publish (workflow tolerates this)

    try:
        from dashboard.gen_dep_map import dep_map_js
        dep_line = dep_map_js()
        b = page.find(_DEP_BEGIN)
        e = page.find(_DEP_END, b + 1) if b != -1 else -1
        if b != -1 and e != -1:
            # keep the begin-marker line, replace everything up to the end marker
            nl = page.find("\n", b)
            if nl != -1 and nl < e:
                page = page[:nl + 1] + dep_line + "\n" + page[e:]
    except Exception:
        pass  # leave the template's last-known map in place; never break the build

    dst = os.path.join(outdir, "ops.html")
    with open(dst, "w", encoding="utf-8") as fh:
        fh.write(page)
    return dst


# ---------------------------------------------------------------------------
# Main entry point
# ---------------------------------------------------------------------------

def build(
    *,
    # Unified store paths (None = use core.results defaults)
    obs_path=None,
    findings_path=None,
    catalog: str = "data/api_catalog.json",
    lifecycles: str = "regression/scenarios/scenarios.json",
    known: str = "data/baselines/known_issues.json",
    waivers: str = "data/baselines/coverage_waivers.json",
    prior: str = "data/verified_endpoints.json",
    # Durable cross-session 2xx evidence (IB-041, tools/derive_verified) —
    # committed to main independently of a dashboard publish, so it can be
    # AHEAD of `prior` (owner 2026-07-10: local console2 runs land evidence
    # here without a matching dashboard-data endpoint_status.json refresh,
    # which only happens on the next full publish). Consulted so a stale
    # last-known-observation cell never contradicts a durably-verified key.
    evidence: str = "data/baselines/verified_endpoints.json",
    # Platform-wide systemic conformance findings (not per-endpoint; the static
    # axis writes them here — merged into the unified findings for the banner).
    conformance: str = "data/conformance.json",
    # Output
    out: str = "reports/dashboard/index.html",
    history: str = "dashboard/history.jsonl",
    run_type: str = "local",
    sha: str = "",
    branch: str = "",
):
    """Build the dashboard HTML.

    Reads the unified results store (core.results JSONL) as the SINGLE source.
    With no store present coverage is all-zero and the build still renders.
    """
    if not branch:
        branch = os.environ.get("GITHUB_REF_NAME", "—")

    # ------------------------------------------------------------------
    # 1. Load catalog (always needed for coverage denominators)
    # ------------------------------------------------------------------
    cat = load_catalog(catalog) if os.path.exists(catalog) else []

    # ------------------------------------------------------------------
    # 2. Load regression observations from the unified store (sole source).
    #    Empty store -> empty rows -> all-zero coverage (still renders).
    # ------------------------------------------------------------------
    unified_obs, unified_findings = _try_load_unified(obs_path, findings_path)
    tsv_rows = [obs_to_tsv_row(o) for o in unified_obs]
    source_label = "unified results store"
    param_rows = ()

    # ------------------------------------------------------------------
    # 3. Conformance findings — per-endpoint from the unified store; merge the
    #    platform-wide "systemic" findings (not per-endpoint, so absent from the
    #    unified store) from conformance.json when the static axis writes one.
    # ------------------------------------------------------------------
    conf = findings_to_conf(unified_findings)
    if os.path.exists(conformance):
        try:
            conf["systemic"] = json.load(open(conformance)).get("systemic", [])
        except (ValueError, OSError):
            pass

    # ------------------------------------------------------------------
    # 4. Load CRUD / lifecycle data (lifecycle definitions only).
    # ------------------------------------------------------------------
    crud_results = {}
    # Merge base scenarios.json + per-service fragments (lifecycles/*.json) via
    # the shared loader so coverage counts every agent's fragment. Fall back to
    # the raw file if the loader isn't importable (keeps the dashboard standalone).
    try:
        from regression.scenarios.loader import load_lifecycles
        lc_data = load_lifecycles()
    except Exception:
        lc_data = (json.load(open(lifecycles)).get("lifecycles", [])
                   if os.path.exists(lifecycles) else [])
    # per-environment baseline: profile-suffixed sibling wins (core/baselines.py)
    from core import baselines as _baselines
    known = str(_baselines.resolve(known))
    known_data = json.load(open(known)) if os.path.exists(known) else {"issues": []}
    waiver_data = json.load(open(waivers)) if os.path.exists(waivers) else {"waivers": []}

    # 3b. Honest per-endpoint conformance (owner 2026-07-08 "왜 전부 정상이냐"):
    #     the unified findings store is machine-local (gitignored), so a machine
    #     that never ran `python -m conformance.static` built an all-green 결함
    #     column — "no evidence" rendered as "정상". Two fixes:
    #     (a) conf["evaluated"] tells the service page whether ANY per-endpoint
    #         findings exist here; when not, rows with no items render 미평가.
    #     (b) baselined PRODUCT bugs (known_issues.json — committed in git, e.g.
    #         PF-21 createvolumetransfer 500) merge into by_endpoint as 결함 so
    #         they show on every machine. Automation-side bugs are OUR bugs, not
    #         API design defects — excluded.
    conf["evaluated"] = bool(unified_findings)
    for it in known_data.get("issues", []):
        ik = it.get("key") or ""
        typ = (it.get("type") or "").strip()
        if not ik or "product" not in typ.lower():
            continue
        rec = conf["by_endpoint"].setdefault(ik, {"status": "green", "items": []})
        rec["items"].append({
            "type": f"{typ} (baselined)", "src": "known_issues",
            "issue": it.get("since", ""),
            "detail": ((f"live {it.get('status')} · " if it.get("status") else "")
                       + (it.get("note") or "")),
        })
        rec["status"] = "red"
    if conf["by_endpoint"]:
        _cnt = Counter(v["status"] for v in conf["by_endpoint"].values())
        conf["summary"] = {"red": _cnt.get("red", 0), "yellow": _cnt.get("yellow", 0),
                           "green": _cnt.get("green", 0),
                           "total": len(conf["by_endpoint"])}
    # cumulative verified set (pulled from the dashboard-data branch by CI)
    prior_verified = set()
    if prior and os.path.exists(prior):
        try:
            prior_verified = set(json.load(open(prior)).get("verified", []))
        except (ValueError, OSError):
            pass

    # Durable cross-session evidence store (data/baselines/verified_endpoints.json)
    # can be AHEAD of the dashboard-data `prior` snapshot — it is updated by
    # tools/derive_verified independently of a dashboard publish (e.g. a local
    # console2 run). Union its catalog-scoped keys into prior_verified so the
    # COVERED verdict (and the stale-cell override below) never lags behind
    # evidence already committed to main (owner 2026-07-10 field report).
    durable_evidence = {}
    if evidence and os.path.exists(evidence):
        try:
            durable_evidence = json.load(open(evidence))
        except (ValueError, OSError):
            durable_evidence = {}
    cat_keys_for_evidence = {e["key"] for e in cat}
    durable_cat_keys = set(durable_evidence) & cat_keys_for_evidence
    prior_verified |= durable_cat_keys

    # cumulative last-known observation per endpoint (status/elapsed/run). Loaded
    # BEFORE compute() so the reachability-covered tally (which keys on "touched")
    # is cumulative like `verified` — a reachability waiver REACHED by any past
    # run stays reach-covered on a scoped rebuild, instead of collapsing to this
    # run's scope. Used again below for the drill-down status cells.
    prior_status = {}
    _ps_path = os.path.join(os.path.dirname(prior or "data/x"), "endpoint_status.json")
    if os.path.exists(_ps_path):
        try:
            prior_status = json.load(open(_ps_path)).get("status", {})
        except (ValueError, OSError):
            pass

    # ------------------------------------------------------------------
    # 5. Compute, history, render
    # ------------------------------------------------------------------
    d = compute(cat, tsv_rows, crud_results, lc_data, known_data, param_rows,
                waivers=waiver_data, prior_verified=prior_verified,
                prior_touched=set(prior_status))
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
    # prior_status (loaded above) also fills the drill-down status cells for
    # endpoints this run didn't call.
    services, merged_status = per_service(cat, tsv_rows, prior_verified=prior_verified,
                                          prior_status=prior_status, sha=sha,
                                          durable_evidence=durable_evidence)

    # 스펙 변경 마커 (owner 2026-07-14 "대쉬보드에 변경된 api는 update로 표시"):
    # spec.diff --mark 가 영속화한 data/spec_diff_latest.json 을 읽어 per-endpoint
    # NEW/UPD 배지 + 인덱스 변경 패널의 원천으로 쓴다. 파일이 없으면(변경 diff가
    # 아직 없음) 대시보드는 종전과 동일하게 렌더된다.
    spec_diff = {}
    _sd_path = os.path.join(os.path.dirname(os.path.abspath(catalog)),
                            "spec_diff_latest.json")
    if os.path.exists(_sd_path):
        try:
            spec_diff = json.load(open(_sd_path))
        except (ValueError, OSError):
            spec_diff = {}

    meta = {
        "branch": branch,
        "when": time.strftime("%Y-%m-%d %H:%M KST",
                              time.gmtime(time.time() + 9 * 3600)),
        "run_type": run_type,
        "conf": conf,
        "spec_diff": spec_diff,
    }

    htm = render(d, hist, meta, services)

    # Write index.html
    outdir = os.path.dirname(os.path.abspath(out))
    os.makedirs(outdir, exist_ok=True)
    with open(out, "w") as fh:
        fh.write(htm)

    # Persist the CUMULATIVE verified set (published to dashboard-data so the
    # next run's C3 builds on it instead of resetting to run scope).
    with open(os.path.join(outdir, "verified_endpoints.json"), "w") as fh:
        json.dump({"verified": d.get("verified_keys", []),
                   "updated": meta["when"], "run_type": run_type}, fh)
    # …and the cumulative last-known observation map (same publish cycle), so
    # status/elapsed cells survive scoped runs instead of going blank.
    with open(os.path.join(outdir, "endpoint_status.json"), "w") as fh:
        json.dump({"status": merged_status, "updated": meta["when"]}, fh)
    # …and the regression DETAIL lists as a first-class artifact (엔진 요청 #1 /
    # Reporting 개선 A). Until now the only published detail was the index.html
    # banner (capped at [:6]) — consumers had to parse presentation HTML (the
    # v2 session's results_data did exactly that, flagged as a fragile stopgap).
    # Full lists, machine-readable, same publish cycle as the headline counts.
    # 관측 0 발행은 fail_new.json 을 덮지 않는다 (관측 0 가드와 짝): 발행 잡이
    # 파일별 cp 오버레이라 skip-write 하면 dashboard-data 의 직전 상세 목록이
    # 살아남는다 — 0-call 재발행이 25건 상세를 빈 목록으로 지우던 것 방지.
    if d.get("has_results"):
        with open(os.path.join(outdir, "fail_new.json"), "w") as fh:
            json.dump({
                "new": [{"key": k, "status": st, "path": p}
                        for k, st, p in d.get("new_regressions", [])],
                "known": [{"key": k, "status": st, "path": p}
                          for k, st, p in d.get("known_red", [])],
                "updated": meta["when"], "run_type": run_type,
            }, fh)

    # Write per-service drill-down pages
    sdir = os.path.join(outdir, "services")
    os.makedirs(sdir, exist_ok=True)
    for s in services:
        with open(os.path.join(sdir, s["slug"] + ".html"), "w") as fh:
            fh.write(render_service_page(s, meta))

    # Emit the static ops viewer with a build-time DEP map injected (the
    # published copy lives at <outdir>/ops.html — the workflow copies THIS one,
    # not the source template, so the kind→parent map is always current).
    emit_ops_html(outdir)

    print(
        f"dashboard -> {out}  "
        f"(source: {source_label}; "
        f"reachable {d.get('reachable_pct', 0):.1f}% ceiling (gap write {d.get('gap_write', 0)} / id-GET {d.get('gap_getid', 0)}); "
        f"C3 {d.get('cov_c3', 0):.1f}% ({d.get('c3_verified', 0)}/{d.get('c3_denom', 0)} "
        f"= verified-2xx {d.get('c3_verified_2xx', 0)} + reachability-covered {d.get('reach_covered', 0)}"
        f"/{d.get('reach_waived', 0)}, excl-waived {d.get('waived_total', 0)}, "
        f"{'full CRUD' if d.get('crud_ran') else 'read-only'}), "
        f"C2-called {d['reach_measured_pct']:.1f}% ({d['tested']}/{d['total']}); "
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
    ap.add_argument("--lifecycles", default="regression/scenarios/scenarios.json")
    ap.add_argument("--known", default="data/baselines/known_issues.json")
    ap.add_argument("--waivers", default="data/baselines/coverage_waivers.json",
                    help="C3 waiver list (docs/COVERAGE-CRITERIA.md)")
    ap.add_argument("--prior", default="data/verified_endpoints.json",
                    help="cumulative verified set from the dashboard-data branch")
    ap.add_argument("--evidence", default="data/baselines/verified_endpoints.json",
                    help="durable cross-session 2xx evidence store (IB-041) — "
                         "unioned into --prior so it can never lag behind main")
    ap.add_argument("--conformance", default="data/conformance.json",
                    help="Platform-wide systemic conformance findings (banner)")
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
        lifecycles=args.lifecycles,
        known=args.known,
        waivers=args.waivers,
        prior=args.prior,
        evidence=args.evidence,
        conformance=args.conformance,
        history=args.history,
        out=args.out,
        run_type=args.run_type,
        sha=args.sha,
        branch=args.branch,
    )


if __name__ == "__main__":
    main()
