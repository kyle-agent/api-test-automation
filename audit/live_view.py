"""Live resource-topology viewer from SCP loggingaudit (CI-independent).

The old `ops.html` rendered run events from the Object Storage oplog that ONLY
the CI workflow wrote. With runs now hand-driven from the Claude remote env, this
builds the same kind of live picture straight from **loggingaudit** — every
resource Create/Delete the account saw — into a self-contained HTML (no server,
no external JS) showing:

  * a **Gantt timeline** of each resource's lifetime (create -> delete), so a
    parallel ``-n 6`` run shows its fan-out as overlapping bars;
  * a **concurrency** strip (how many resources were live at each moment — the
    visual proof of sum-vs-max parallelism);
  * the **current topology** — resources still live right now (no delete seen),
    grouped by run-tag, which is the account's present state.

Usage::

    # harvest a window + render in one shot
    python -m audit.live_view --start 2026-06-18T03:55:00Z --hours 6 --out reports/audit/live_view.html
    # or render an already-harvested jsonl
    python -m audit.live_view --events reports/audit/heavy.jsonl --out reports/audit/live_view.html
    # live mode: re-harvest + self-refresh every 30s
    python -m audit.live_view --hours 2 --refresh 30 --out reports/audit/live_view.html
"""
from __future__ import annotations

import argparse
import html
import json
import re
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

_TAG = re.compile(r"regr[a-z0-9]+|zznet[a-z0-9]+")
# billable resource types (cost-bearing) — highlighted distinctly
_BILLABLE = {"cluster", "nodepool", "virtual-server", "postgresql", "mysql",
             "mariadb", "epas", "cachestore", "sqlserver", "vertica",
             "searchengine", "eventstreams", "loadbalancer", "baremetal"}
# stable-ish color per resource_type (HSL hashed)
def _color(rtype: str) -> str:
    h = sum(ord(c) for c in (rtype or "?")) * 37 % 360
    return f"hsl({h},62%,55%)"


def _t(s: str) -> datetime:
    return datetime.strptime(s, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)


def _tag_of(e: dict) -> str:
    m = _TAG.search(e.get("resource_name") or "")
    return m.group(0) if m else (e.get("resource_name") or "?")


def harvest(start: str, end: str, out: str, max_pages: int = 80) -> str:
    subprocess.run([sys.executable, "-m", "audit.harvest", "--start", start,
                    "--end", end, "--out", out, "--service", "loggingaudit",
                    "--max-pages", str(max_pages)], check=False, timeout=300)
    return out


def _is_ours(name: str) -> bool:
    """Only OUR test resources carry a regr*/zznet* owner tag. loggingaudit also
    reports activity on PRE-EXISTING account resources (IAM policies/ACLs like
    AdministratorAccess_ACL, platform log-streams), which have no Create event in
    our window and would otherwise be mislabeled '생성중' and pulse forever — they
    aren't ours and aren't running. Filter them out by default."""
    return bool(_TAG.search(name or ""))


def build_spans(events: list[dict], now: datetime, ours_only: bool = True,
                terminating: set | None = None):
    """Return per-resource-instance spans:
    {(rtype, tag, name): {start, end|None, rtype, tag, name, ops:[(ts,event)]}}.

    ``ours_only`` (default) keeps only regr*/zznet*-tagged resources — the ones a
    test run created — so pre-existing account resources don't pollute the view.
    ``terminating`` (names from :func:`fetch_terminating`) flags deferred-delete
    resources whose delete was accepted (pending-deletion) so they read as
    삭제예정 instead of lingering as testing/created."""
    inst: dict = {}
    for e in sorted(events, key=lambda x: x.get("timestamp", "")):
        rt = e.get("resource_type") or "?"
        nm = e.get("resource_name") or ""
        if ours_only and not _is_ours(nm):
            continue
        key = (rt, _tag_of(e), nm)
        d = inst.setdefault(key, {"rtype": rt, "tag": _tag_of(e), "name": nm,
                                  "start": None, "end": None, "ops": []})
        ts = e.get("timestamp"); nmn = e.get("event_name") or ""
        d["ops"].append((ts, nmn))
        if "Create" in nmn and d["start"] is None:
            d["start"] = ts
        if "Delete" in nmn and "End" in nmn:
            d["end"] = ts
    # drop instances we never saw a create/first-event time for
    for d in inst.values():
        if d["start"] is None and d["ops"]:
            d["start"] = d["ops"][0][0]
        if terminating and d["rtype"] in _DEFERRED_DELETE and d["name"] in terminating:
            d["terminating"] = True
    return inst


def concurrency(spans, now: datetime, billable_only=False):
    """List of (timestamp, live_count) step points."""
    pts = []
    for d in spans.values():
        if billable_only and d["rtype"] not in _BILLABLE:
            continue
        if not d["start"]:
            continue
        pts.append((_t(d["start"]), 1))
        end = _t(d["end"]) if d["end"] else now
        pts.append((end, -1))
    pts.sort()
    series, cur, peak = [], 0, 0
    for ts, delta in pts:
        cur += delta; peak = max(peak, cur)
        series.append((ts, cur))
    return series, peak


def render(spans, now: datetime, meta: dict, refresh: int = 0) -> str:
    # order rows by start time; only rows with a real start
    rows = [d for d in spans.values() if d["start"]]
    rows.sort(key=lambda d: (d["start"], d["tag"]))
    if not rows:
        t0 = t1 = now
    else:
        t0 = min(_t(d["start"]) for d in rows)
        t1 = max((_t(d["end"]) if d["end"] else now) for d in rows)
    span_s = max((t1 - t0).total_seconds(), 1)

    PLOT_W, ROW_H, LABEL_W, PAD = 1180, 16, 260, 12
    W = LABEL_W + PLOT_W + PAD * 2
    conc_h = 90
    H = PAD * 3 + conc_h + 24 + len(rows) * ROW_H + 40

    def x(ts: datetime) -> float:
        return LABEL_W + PAD + (ts - t0).total_seconds() / span_s * PLOT_W

    live_rows = [d for d in rows if not d["end"]]
    series, peak = concurrency(spans, now)
    bseries, bpeak = concurrency(spans, now, billable_only=True)

    parts = [f'''<!doctype html><html><head><meta charset="utf-8">
<title>SCP live resource view</title>''']
    if refresh:
        parts.append(f'<meta http-equiv="refresh" content="{refresh}">')
    parts.append(f'''<style>
 body{{background:#0e1117;color:#c9d1d9;font:13px/1.4 -apple-system,Segoe UI,Roboto,sans-serif;margin:0;padding:16px}}
 h1{{font-size:18px;margin:0 0 4px}} .sub{{color:#8b949e;font-size:12px;margin-bottom:12px}}
 .cards{{display:flex;gap:10px;flex-wrap:wrap;margin-bottom:14px}}
 .card{{background:#161b22;border:1px solid #30363d;border-radius:8px;padding:8px 14px}}
 .card b{{font-size:20px;display:block}} .card span{{color:#8b949e;font-size:11px}}
 .live{{color:#3fb950}} .bill{{color:#f0883e}}
 svg{{background:#0d1117;border:1px solid #21262d;border-radius:8px}}
 rect.bar:hover{{stroke:#fff;stroke-width:1.5}}
 .lg{{display:flex;gap:8px;flex-wrap:wrap;margin:10px 0;font-size:11px;color:#8b949e}}
 .lg i{{display:inline-block;width:11px;height:11px;border-radius:2px;margin-right:3px;vertical-align:-1px}}
</style></head><body>''')

    parts.append(f'<h1>SCP live resource view <span style="color:#8b949e;font-size:12px">· loggingaudit</span></h1>')
    parts.append(f'<div class="sub">window {html.escape(meta.get("start",""))} → {html.escape(meta.get("end",""))} '
                 f'· generated {now.strftime("%Y-%m-%dT%H:%M:%SZ")}'
                 f'{" · auto-refresh "+str(refresh)+"s" if refresh else ""}</div>')

    total = len(rows)
    bill_live = [d for d in live_rows if d["rtype"] in _BILLABLE]
    parts.append('<div class="cards">')
    parts.append(f'<div class="card"><b>{total}</b><span>resources (window)</span></div>')
    parts.append(f'<div class="card"><b class="live">{len(live_rows)}</b><span>live now</span></div>')
    parts.append(f'<div class="card"><b class="bill">{len(bill_live)}</b><span>billable live</span></div>')
    parts.append(f'<div class="card"><b>{peak}</b><span>peak concurrency</span></div>')
    parts.append(f'<div class="card"><b class="bill">{bpeak}</b><span>peak billable concur. (n-parallel)</span></div>')
    parts.append('</div>')

    # concurrency strip (billable overlaid)
    def poly(series, h0, color, fill):
        if not series:
            return ""
        mx = max(c for _, c in series) or 1
        pts = []
        for ts, c in series:
            pts.append(f"{x(ts):.1f},{h0 - c / mx * (conc_h - 8):.1f}")
        # step
        d = "M" + " L".join(pts)
        return f'<polyline points="{" ".join(pts)}" fill="none" stroke="{color}" stroke-width="1.5"/>'

    parts.append(f'<svg width="{W}" height="{H}">')
    cy0 = PAD + conc_h
    parts.append(f'<text x="{LABEL_W+PAD}" y="{PAD+10}" fill="#8b949e" font-size="11">concurrency (all={peak}, billable={bpeak})</text>')
    parts.append(poly(series, cy0, "#58a6ff", None))
    parts.append(poly(bseries, cy0, "#f0883e", None))

    # time axis ticks (6)
    ty = cy0 + 16
    for i in range(7):
        tt = t0 + (t1 - t0) * (i / 6)
        xx = x(tt)
        parts.append(f'<line x1="{xx:.1f}" y1="{ty}" x2="{xx:.1f}" y2="{H-20}" stroke="#21262d"/>')
        parts.append(f'<text x="{xx:.1f}" y="{ty-2}" fill="#6e7681" font-size="10" text-anchor="middle">{tt.strftime("%H:%M")}</text>')

    # bars
    y = ty + 8
    for d in rows:
        xs = x(_t(d["start"]))
        xe = x(_t(d["end"]) if d["end"] else now)
        w = max(xe - xs, 2)
        col = _color(d["rtype"])
        live = not d["end"]
        bill = d["rtype"] in _BILLABLE
        label = f'{d["rtype"]}/{d["tag"]}'
        tip = f'{d["rtype"]} · {html.escape(d["name"][:40])} · {d["start"]} → {d["end"] or "LIVE"} · {len(d["ops"])} ops'
        parts.append(f'<rect class="bar" x="{xs:.1f}" y="{y}" width="{w:.1f}" height="{ROW_H-3}" rx="2" '
                     f'fill="{col}"><title>{tip}</title></rect>')
        if live:
            parts.append(f'<rect x="{xs:.1f}" y="{y}" width="{w:.1f}" height="{ROW_H-3}" rx="2" fill="none" stroke="#3fb950" stroke-width="1.5"/>')
        parts.append(f'<text x="{LABEL_W+PAD-6}" y="{y+ROW_H-6}" fill="{"#f0883e" if bill else "#8b949e"}" '
                     f'font-size="10" text-anchor="end">{html.escape(label[:38])}</text>')
        y += ROW_H
    parts.append('</svg>')

    # legend
    seen = {}
    for d in rows:
        seen.setdefault(d["rtype"], _color(d["rtype"]))
    parts.append('<div class="lg">')
    for rt, c in sorted(seen.items()):
        star = "★" if rt in _BILLABLE else ""
        parts.append(f'<span><i style="background:{c}"></i>{html.escape(rt)}{star}</span>')
    parts.append('<span>· green border = live now · ★ billable</span></div>')

    parts.append('</body></html>')
    return "".join(parts)


# --- v2: layered-DAG live state (console-report style, not a gantt) ----------
# map a loggingaudit resource_type -> the gen_dep_map KIND (plural collection).
_KIND_ALIAS = {
    "virtual-server": "servers", "cloud-function": "cloud-functions",
    "postgresql": "clusters", "mysql": "clusters", "mariadb": "clusters",
    "epas": "clusters", "cachestore": "clusters", "sqlserver": "clusters",
    "vertica": "clusters", "searchengine": "clusters", "eventstreams": "clusters",
    "instance-group": "instance-groups", "block-storage-group": "block-storage-groups",
    "log-stream": "log-streams", "log-group": "log-groups", "security-group": "security-groups",
    "nodepool": "nodepools", "public-ip": "public-ips", "publicip": "public-ips",
    "internet-gateway": "internet-gateways",
}


def _kind_of(rtype: str, dep_kinds: set) -> str:
    if not rtype:
        return "?"
    if rtype in _KIND_ALIAS:
        return _KIND_ALIAS[rtype]
    for cand in (rtype + "s", rtype + "es", rtype[:-1] + "ies" if rtype.endswith("y") else rtype, rtype):
        if cand in dep_kinds:
            return cand
    return rtype + "s"


# resource types whose delete is DEFERRED — the API accepts the delete request and
# the resource sits in a pending-deletion state for days before it's physically
# gone (KMS keys -> To_Be_Terminated, secrets -> "To be terminated"). The delete
# REQUEST already succeeded, and loggingaudit logs it as a plain Update (no Delete
# event), so the view must consult live state and treat that state as deleted.
_DEFERRED_DELETE = {"kms", "secret"}
_TERMINATING_STATES = {"to_be_terminated", "to be terminated", "pendingdeletion",
                       "pending deletion", "scheduled", "terminating", "to_be_terminate"}


def _state_of(d: dict) -> str:
    """현재 상태: creating(생성중) / testing(테스트중) / created(생성됨) /
    deleted(삭제됨) / failed(생성실패) / terminating(삭제예정). A 'Create Error'
    with no later Create End and no Delete is a FAILED create — the resource never
    existed (e.g. createpublicdomainname 500). A deferred-delete resource (kms /
    secret) flagged ``terminating`` had its delete accepted (pending-deletion);
    we show it as scheduled-for-deletion, i.e. effectively deleted."""
    names = [n for _, n in d["ops"]]
    if any("Delete" in n and "End" in n for n in names):
        return "deleted"
    if d.get("terminating"):
        return "terminating"
    created = any("Create" in n and "End" in n for n in names)
    if not created:
        if any(("Error" in n or "Fail" in n) for n in names):
            return "failed"
        return "creating"
    # any non-create/non-delete op AFTER create end == being exercised (API 점검중)
    seen_create_end = False
    for _, n in d["ops"]:
        if "Create" in n and "End" in n:
            seen_create_end = True; continue
        if seen_create_end and "Delete" not in n:
            return "testing"
    return "created"


_STATE_COLOR = {"creating": "#58a6ff", "testing": "#d29922", "created": "#3fb950",
                "deleted": "#6e7681", "failed": "#e5484d", "terminating": "#8b95a3"}
_STATE_KO = {"creating": "생성중", "testing": "테스트중", "created": "생성됨",
             "deleted": "삭제됨", "failed": "생성실패", "terminating": "삭제예정"}


def fetch_terminating(client=None) -> set:
    """Live-state probe for deferred-delete types: returns the set of OUR resource
    names currently in a pending-deletion state (KMS keys To_Be_Terminated, secrets
    'To be terminated'). Needs credentials; returns empty set on any failure so the
    offline render path is unaffected."""
    out: set = set()
    try:
        if client is None:
            from core.config import settings
            from core.http_client import ApiClient
            client = ApiClient(settings)
    except Exception:
        return out
    probes = [("/v1/secrets", "secretsmanager"), ("/v1/kms/transit", "kms")]
    for path, svc in probes:
        try:
            r = client.get(path, service=svc, params={"size": 1000})
            j = json.loads(r.raw_text or "{}")
        except Exception:
            continue
        for v in (j.values() if isinstance(j, dict) else []):
            if not isinstance(v, list):
                continue
            for it in v:
                if not isinstance(it, dict):
                    continue
                nm = str(it.get("name") or "")
                st = str(it.get("state") or it.get("status") or it.get("key_state") or "").strip().lower()
                if nm and _TAG.search(nm) and st in _TERMINATING_STATES:
                    out.add(nm)
    return out


def render_dag(spans, now: datetime, meta: dict, refresh: int = 0) -> str:
    from collections import defaultdict
    try:
        from dashboard.gen_dep_map import dep_map_dict
        dm = dep_map_dict()
    except Exception:
        dm = {"parent": {}, "depth": {}}
    parent, depth = dm.get("parent", {}), dm.get("depth", {})
    dep_kinds = set(depth)

    # group resource instances by kind, aggregate state
    kinds = defaultdict(lambda: {"insts": [], "states": defaultdict(int)})
    for d in spans.values():
        if not d["start"]:
            continue
        k = _kind_of(d["rtype"], dep_kinds)
        st = _state_of(d)
        kinds[k]["insts"].append((d, st))
        kinds[k]["states"][st] += 1
        kinds[k]["rtype"] = d["rtype"]

    # x = depth (creation order); fallback depth 0
    col = {k: depth.get(k, 0) for k in kinds}
    maxc = max(col.values(), default=0)
    bycol = defaultdict(list)
    for k in kinds:
        bycol[col[k]].append(k)
    for c in bycol:
        bycol[c].sort()

    COLW, ROWH, BW, BH, PADX, PADY = 230, 70, 188, 52, 30, 70
    rows = max((len(v) for v in bycol.values()), default=1)
    W = PADX * 2 + (maxc + 1) * COLW
    H = PADY + rows * ROWH + 40
    pos = {}
    for c, ks in bycol.items():
        for i, k in enumerate(ks):
            pos[k] = (PADX + c * COLW, PADY + i * ROWH)

    P = [f'<!doctype html><html lang="ko"><head><meta charset="utf-8"><title>live DAG</title>']
    if refresh:
        P.append(f'<meta http-equiv="refresh" content="{refresh}">')
    P.append('''<style>
 body{background:#0b1018;color:#c9d1d9;font:13px/1.4 -apple-system,Segoe UI,Roboto,sans-serif;margin:0;padding:14px}
 h1{font-size:17px;margin:0 0 2px} .sub{color:#8b949e;font-size:12px;margin-bottom:8px}
 .lg{font-size:12px;color:#8b949e;margin:6px 0}
 .lg i{display:inline-block;width:11px;height:11px;border-radius:50%;margin:0 3px 0 10px;vertical-align:-1px}
 svg{background:#0d1117;border:1px solid #21262d;border-radius:8px}
 g.node rect.box:hover{stroke:#fff}
</style></head><body>''')
    P.append('<h1>SCP 실행 위상 · 라이브 상태 <span style="color:#8b949e;font-size:12px">· loggingaudit</span></h1>')
    P.append(f'<div class="sub">왼→오 = 생성 순서(의존 깊이) · 같은 열 = 동시 실행 가능 · '
             f'{html.escape(meta.get("start",""))} → {html.escape(meta.get("end",""))}'
             f'{" · 자동갱신 "+str(refresh)+"s" if refresh else ""}</div>')
    P.append('<div class="lg">'
             '<i style="background:#58a6ff"></i>생성중<i style="background:#d29922"></i>테스트중'
             '<i style="background:#3fb950"></i>생성됨<i style="background:#6e7681"></i>삭제됨'
             ' · 박스 안 숫자 = 인스턴스 수</div>')

    P.append(f'<svg viewBox="0 0 {W} {H}" width="{W}" height="{H}">')
    P.append('<defs><marker id="ar" markerWidth="9" markerHeight="9" refX="8" refY="3" orient="auto">'
             '<path d="M0,0 L8,3 L0,6 z" fill="#3a4a5e"/></marker></defs>')
    # column headers (depth = order)
    for c in range(maxc + 1):
        P.append(f'<text x="{PADX + c*COLW + BW/2}" y="{PADY-22}" fill="#566" font-size="11" text-anchor="middle">단계 {c}</text>')
    # edges: parent -> child (both present)
    for k in kinds:
        par = parent.get(k)
        if par in pos and k in pos:
            ax, ay = pos[par]; bx, by = pos[k]
            x1, y1 = ax + BW, ay + BH/2; x2, y2 = bx, by + BH/2
            P.append(f'<path d="M{x1},{y1} C{x1+40},{y1} {x2-40},{y2} {x2},{y2}" fill="none" '
                     f'stroke="#3a4a5e" stroke-width="1.3" marker-end="url(#ar)"/>')
    # nodes
    for k, (x, y) in pos.items():
        info = kinds[k]; states = info["states"]
        # dominant state for border color (priority: testing > creating > created > deleted)
        dom = next((s for s in ("testing", "creating", "created", "deleted") if states.get(s)), "created")
        n = sum(states.values())
        badge = " ".join(f'<tspan fill="{_STATE_COLOR[s]}">{_STATE_KO[s]} {states[s]}</tspan>'
                         for s in ("creating", "testing", "created", "deleted") if states.get(s))
        tip = f'{k} ({info.get("rtype","")}) · {n} 인스턴스 · ' + ", ".join(f'{_STATE_KO[s]}:{states[s]}' for s in states)
        P.append(f'<g class="node"><title>{html.escape(tip)}</title>'
                 f'<rect class="box" x="{x}" y="{y}" width="{BW}" height="{BH}" rx="9" fill="#13202e" '
                 f'stroke="{_STATE_COLOR[dom]}" stroke-width="2"/>'
                 f'<text x="{x+10}" y="{y+19}" font-size="12.5" font-weight="600" fill="#e6edf3">{html.escape(k)} '
                 f'<tspan fill="#7d8896" font-size="10">×{n}</tspan></text>'
                 f'<text x="{x+10}" y="{y+37}" font-size="10">{badge}</text></g>')
    P.append('</svg>')
    P.append(f'<div class="sub" style="margin-top:8px">{len(kinds)} kinds · {sum(len(v["insts"]) for v in kinds.values())} 인스턴스 · 깊이 {maxc}</div>')
    P.append('</body></html>')
    return "".join(P)


# light-theme per-state palette (fill, border)
_FLOW = {
    "creating": ("#cfe8ff", "#2b7de9"),  # 생성중 — pulses
    "testing":  ("#ffe6ad", "#d99413"),  # 테스트중 — pulses
    "created":  ("#c8efd4", "#1f9d57"),  # 생성됨
    "deleted":  ("#e6e9ee", "#9aa4b2"),  # 삭제됨 — gray
    "failed":   ("#ffd6d6", "#e5484d"),  # 생성실패 — red, not pulsing/leaked
    "terminating": ("#d6dbe2", "#8b95a3"),  # 삭제예정 — delete accepted, pending (kms/secret)
}


_LK = re.compile(r"[0-9a-f]{8}$")  # trailing 8-hex unique == per-lifecycle key


def _lk(d: dict) -> str:
    """Lifecycle key: resources from one lifecycle share the run's 8-hex unique
    suffix (engine: ts_hex(4)+rand_hex(4)), so vpc/subnet/server of the same run
    group together — lets us draw which-belongs-to-which edges."""
    t = d["tag"] or ""
    m = _LK.search(t)
    return m.group(0) if m else t


def _dur(d: dict, now: datetime) -> str:
    if not d["start"]:
        return ""
    end = _t(d["end"]) if d["end"] else now
    s = (end - _t(d["start"])).total_seconds()
    return f"{s/60:.0f}m" if s >= 60 else f"{s:.0f}s"


def render_flow(spans, now: datetime, meta: dict, refresh: int = 0) -> str:
    """v3 — per-INSTANCE topology (id shown), light theme, running pulses, deleted greys."""
    from collections import defaultdict
    try:
        from dashboard.gen_dep_map import dep_map_dict
        dm = dep_map_dict()
        depth, parent = dm.get("depth", {}), dm.get("parent", {})
    except Exception:
        depth, parent = {}, {}
    dep_kinds = set(depth)

    # column = creation-order depth of the instance's kind; group col -> kind -> instances
    col_kind = defaultdict(lambda: defaultdict(list))
    insts = [d for d in spans.values() if d["start"]]
    kind_of = {}                       # id(d) -> kind
    by_kind_lk = defaultdict(list)     # (kind, lifecycle-key) -> [d]
    for d in insts:
        k = _kind_of(d["rtype"], dep_kinds)
        kind_of[id(d)] = k
        by_kind_lk[(k, _lk(d))].append(d)
        col_kind[depth.get(k, 0)][d["rtype"]].append(d)
    maxc = max(col_kind, default=0)

    COLW, BW, BH, GAP, KGAP, PADX, PADY, HEADY = 226, 200, 26, 5, 22, 24, 96, 60
    # lay out, compute column heights
    pos, col_h = {}, {}
    for c in range(maxc + 1):
        y = PADY
        for rt in sorted(col_kind.get(c, {})):
            y += KGAP  # kind sub-label
            for d in sorted(col_kind[c][rt], key=lambda x: x["start"]):
                pos[id(d)] = (PADX + c * COLW, y, d, rt)
                y += BH + GAP
            y += 6
        col_h[c] = y
    W = PADX * 2 + (maxc + 1) * COLW
    H = max(col_h.values(), default=PADY) + 40

    nstate = defaultdict(int)
    for d in insts:
        nstate[_state_of(d)] += 1

    P = [f'<!doctype html><html lang="ko"><head><meta charset="utf-8"><title>live flow</title>']
    if refresh:
        P.append(f'<meta http-equiv="refresh" content="{refresh}">')
    P.append('''<style>
 body{background:#f5f7fb;color:#1f2733;font:12px/1.35 -apple-system,Segoe UI,Roboto,sans-serif;margin:0;padding:14px}
 h1{font-size:17px;margin:0 0 2px;color:#0f1722} .sub{color:#5b6675;font-size:12px;margin-bottom:8px}
 .lg{font-size:12px;color:#5b6675;margin:6px 0} .lg i{display:inline-block;width:11px;height:11px;border-radius:3px;margin:0 3px 0 12px;vertical-align:-1px;border:1px solid #0002}
 svg{background:#fff;border:1px solid #e3e8ef;border-radius:10px}
 @keyframes pulse{0%,100%{opacity:1}50%{opacity:.4}}
 .run{animation:pulse 1.1s ease-in-out infinite}
 g.n{cursor:pointer} g.n rect:hover{stroke-width:2.5}
 text{fill:#22303f}
 /* click-to-highlight a relationship chain */
 svg.sel g.n{opacity:.18} svg.sel g.n.hi{opacity:1}
 svg.sel path.rel{opacity:.06} svg.sel path.rel.hi{opacity:1;stroke:#2b7de9;stroke-width:2.2}
 g.n.hi rect{stroke:#2b7de9;stroke-width:2.6}
 .hint{color:#5b6675;font-size:11px;margin:2px 0 8px}
</style></head><body>''')
    P.append('<h1>SCP 실행 흐름 · 인스턴스 라이브 상태</h1>')
    P.append(f'<div class="sub">왼→오 = 생성 순서(의존 깊이) · 같은 열 = 동시 실행 · 각 박스 = 리소스 1개(id)'
             f' · {html.escape(meta.get("start",""))}→{html.escape(meta.get("end",""))}'
             f'{" · 자동갱신 "+str(refresh)+"s" if refresh else ""}</div>')
    P.append(f'<div class="lg">'
             f'<i style="background:#cfe8ff"></i>생성중 {nstate["creating"]}(깜빡)'
             f'<i style="background:#ffe6ad"></i>테스트중 {nstate["testing"]}'
             f'<i style="background:#c8efd4"></i>생성됨 {nstate["created"]}'
             f'<i style="background:#e6e9ee"></i>삭제됨 {nstate["deleted"]}'
             f'<i style="background:#d6dbe2"></i>삭제예정 {nstate["terminating"]}'
             f'<i style="background:#ffd6d6"></i>생성실패 {nstate["failed"]}</div>')

    P.append(f'<svg viewBox="0 0 {W} {H}" width="{W}" height="{H}">')
    P.append('<defs><marker id="rel" markerWidth="7" markerHeight="7" refX="6" refY="2.5" orient="auto">'
             '<path d="M0,0 L6,2.5 L0,5 z" fill="#aab6c6"/></marker></defs>')
    for c in range(maxc + 1):
        P.append(f'<text x="{PADX + c*COLW + 4}" y="{PADY-26}" font-size="12" font-weight="700" fill="#8893a4">단계 {c}</text>')
        if c < maxc:
            xx = PADX + (c+1)*COLW - 10
            P.append(f'<line x1="{xx}" y1="{PADY-34}" x2="{xx}" y2="{H-20}" stroke="#eef1f6"/>')
    # kind sub-labels
    for c in range(maxc + 1):
        y = PADY
        for rt in sorted(col_kind.get(c, {})):
            P.append(f'<text x="{PADX + c*COLW}" y="{y+13}" font-size="10.5" font-weight="700" fill="#aab3c0">{html.escape(rt)}</text>')
            y += KGAP + (BH + GAP) * len(col_kind[c][rt]) + 6

    # relationship lines: parent-instance -> child-instance sharing one lifecycle
    # key. The engine names every resource of a lifecycle with the same 8-hex
    # unique, so a vpc and its subnet/server/etc. share _lk(); we connect a child
    # to the parent-KIND instance(s) of its same lifecycle (which vpc owns which
    # subnet). Drawn first so boxes sit on top.
    xy = {id(d): (px, py) for (px, py, d, rt) in pos.values()}
    nid = {id(d): f"n{i}" for i, (_x, _y, d, _rt) in enumerate(pos.values())}
    adj = defaultdict(set)   # node-id -> connected node-ids (undirected, for click highlight)
    edges = 0
    for d in insts:
        k = kind_of[id(d)]
        par = parent.get(k)
        if not par:
            continue
        for pd in by_kind_lk.get((par, _lk(d)), []):
            if pd is d or id(pd) not in nid:
                continue
            a, b = nid[id(pd)], nid[id(d)]
            ax, ay = xy[id(pd)]; bx, by = xy[id(d)]
            x1, y1 = ax + BW, ay + BH / 2          # parent right edge
            x2, y2 = bx, by + BH / 2               # child left edge
            P.append(f'<path class="rel" id="e{edges}" data-a="{a}" data-b="{b}" '
                     f'd="M{x1:.0f},{y1:.0f} C{x1+34:.0f},{y1:.0f} {x2-34:.0f},{y2:.0f} {x2:.0f},{y2:.0f}" '
                     f'fill="none" stroke="#c3ccd9" stroke-width="1.1" marker-end="url(#rel)"/>')
            adj[a].add(b); adj[b].add(a)
            edges += 1

    # instance boxes (each gets a node id + onclick to highlight its chain)
    for (x, y, d, rt) in pos.values():
        st = _state_of(d)
        fill, bd = _FLOW[st]
        run = ' run' if st in ("creating", "testing") else ""
        tag = d["tag"] if d["tag"] != d["name"] else (d["name"] or "?")
        lab = (d["name"] or tag)[:30]
        dur = _dur(d, now)
        myid = nid[id(d)]
        linked = " · 🔗연결" if adj.get(myid) else ""
        tip = f'{rt} · {html.escape(d["name"] or tag)} · {_STATE_KO[st]} · {d["start"]}→{d["end"] or "LIVE"} · {dur} · {len(d["ops"])} ops{linked}'
        deco = ' text-decoration="line-through"' if st in ("deleted", "terminating") else ""
        txt_gray = st in ("deleted", "terminating")
        P.append(f'<g class="n{run}" id="{myid}" onclick="hi(\'{myid}\')"><title>{html.escape(tip)}</title>'
                 f'<rect x="{x}" y="{y}" width="{BW}" height="{BH}" rx="6" fill="{fill}" stroke="{bd}" stroke-width="1.4"/>'
                 f'<circle cx="{x+10}" cy="{y+BH/2}" r="3.5" fill="{bd}"/>'
                 f'<text x="{x+20}" y="{y+16}" font-size="11" fill="{"#9aa4b2" if txt_gray else "#1f2733"}"{deco}>{html.escape(lab)}</text>'
                 f'<text x="{x+BW-6}" y="{y+16}" font-size="9.5" text-anchor="end" fill="#7a8493">{dur}</text></g>')
    P.append('</svg>')
    P.append(f'<div class="sub" style="margin-top:8px">{len(insts)} 인스턴스 · {maxc+1} 단계 · {edges} 연관선 '
             f'· <span style="color:#2b7de9">인스턴스 클릭 = 연관관계 강조</span>(빈 곳 클릭 = 해제)</div>')

    # click-highlight: BFS the connected component (vpc→subnet→server→volume…) so
    # clicking a subnet lights its parent vpc + the servers/volumes built in it.
    adj_js = json.dumps({k: sorted(v) for k, v in adj.items()})
    P.append('<script>')
    P.append(f'var ADJ={adj_js};')
    P.append('''
function comp(s){var seen={},stk=[s];while(stk.length){var x=stk.pop();if(seen[x])continue;seen[x]=1;(ADJ[x]||[]).forEach(function(y){if(!seen[y])stk.push(y);});}return seen;}
function hi(id){
 var svg=document.querySelector('svg');
 var set=comp(id);
 svg.classList.add('sel');
 document.querySelectorAll('g.n').forEach(function(g){g.classList.toggle('hi',!!set[g.id]);});
 document.querySelectorAll('path.rel').forEach(function(p){
   p.classList.toggle('hi', !!set[p.getAttribute('data-a')] && !!set[p.getAttribute('data-b')]);});
 if(window.event) window.event.stopPropagation();
}
function clr(){var svg=document.querySelector('svg');if(svg){svg.classList.remove('sel');
 document.querySelectorAll('.hi').forEach(function(e){e.classList.remove('hi');});}}
document.addEventListener('click',function(e){if(!e.target.closest('g.n'))clr();});
''')
    P.append('</script>')
    P.append('</body></html>')
    return "".join(P)


# --- v5: TEST-LOG-driven execution view -------------------------------------
# loggingaudit only sees resource Create/Delete, so it misses the read/probe
# calls and tells you nothing about pass/fail — and after teardown it shows
# almost nothing (the "only log-stream left" problem). The TEST logs
# (reports/results/observations*.jsonl) are the complete execution record: every
# call, when, how long, ok/soft/fail. So we drive the execution-stage view from
# them, and keep loggingaudit for *actual resource state* confirmation. Stages
# still deepen ONLY by dependency depth (gen_dep_map) — the test logs just say
# WHAT ran at each stage and how it went.

# test-result palette (fill, border)
_EXEC = {
    "pass":    ("#c8efd4", "#1f9d57"),  # 통과 (ok)
    "running": ("#cfe8ff", "#2b7de9"),  # 수행중 (fresh call) — pulses
    "soft":    ("#ffe6ad", "#d99413"),  # 데이터/권한 필요 (soft)
    "fail":    ("#ffd6d6", "#e5484d"),  # 오류 (fail)
}
_EXEC_KO = {"pass": "통과", "running": "수행중", "soft": "데이터필요", "fail": "오류"}
_ACTIVE_S = 90  # a call whose last activity is within this of 'now' == still running


def _path_kind(path: str, dep_kinds: set) -> str | None:
    """Map an endpoint path to its dependency KIND = the deepest path collection
    segment that gen_dep_map knows (e.g. /v1/vpcs/{id}/subnets -> subnets)."""
    segs = [s for s in (path or "").split("/")
            if s and not s.startswith("{") and not re.fullmatch(r"v\d+", s)]
    cand = [s for s in segs if s in dep_kinds]
    return cand[-1] if cand else None


def build_exec_spans(observations: list[dict], now: datetime, dep_kinds: set) -> dict:
    """One node per endpoint_key from the TEST logs: aggregate its calls into
    {kind, name, start, end, ts_last, n, ms, cats, state}. State is the test
    outcome (pass/soft/fail) + 'running' when its last call is within _ACTIVE_S."""
    from collections import defaultdict
    agg: dict = {}
    for o in observations:
        key = o.get("endpoint_key") or o.get("path") or "?"
        ts = o.get("ts")
        d = agg.setdefault(key, {"kind": _path_kind(o.get("path"), dep_kinds),
                                 "name": key.split("/")[-1][:34], "full": key,
                                 "path": o.get("path") or "", "tmin": None, "tmax": None,
                                 "n": 0, "ms": [], "cats": defaultdict(int),
                                 "source": o.get("source") or ""})
        d["n"] += 1
        d["cats"][o.get("category") or "?"] += 1
        if isinstance(o.get("elapsed_ms"), (int, float)):
            d["ms"].append(o["elapsed_ms"])
        if ts:
            d["tmin"] = ts if d["tmin"] is None else min(d["tmin"], ts)
            d["tmax"] = ts if d["tmax"] is None else max(d["tmax"], ts)
    now_ts = now.timestamp()
    for d in agg.values():
        c = d["cats"]
        # worst-of outcome, but 'running' wins if the endpoint is still active
        if d["tmax"] and (now_ts - d["tmax"]) <= _ACTIVE_S:
            d["state"] = "running"
        elif c.get("fail"):
            d["state"] = "fail"
        elif c.get("ok"):
            d["state"] = "pass"
        elif c.get("soft"):
            d["state"] = "soft"
        else:
            d["state"] = "soft"
        d["mean_ms"] = (sum(d["ms"]) / len(d["ms"])) if d["ms"] else 0
    return agg


def render_exec(agg: dict, now: datetime, meta: dict, refresh: int = 0) -> str:
    """v5 — execution stages from the TEST logs: every tested endpoint, staged by
    dependency depth, colored by pass/soft/fail (running pulses). Light theme."""
    from collections import defaultdict
    try:
        from dashboard.gen_dep_map import dep_map_dict
        depth = dep_map_dict().get("depth", {})
    except Exception:
        depth = {}

    # column = dependency depth of the endpoint's kind; group col -> kind -> nodes
    col_kind = defaultdict(lambda: defaultdict(list))
    for d in agg.values():
        k = d["kind"] or "(misc)"
        col_kind[depth.get(k, 0)][k].append(d)
    maxc = max(col_kind, default=0)

    COLW, BW, BH, GAP, KGAP, PADX, PADY = 248, 222, 24, 4, 22, 24, 96
    pos, col_h = {}, {}
    for c in range(maxc + 1):
        y = PADY
        for k in sorted(col_kind.get(c, {})):
            y += KGAP
            for d in sorted(col_kind[c][k], key=lambda x: (x["tmin"] or 0)):
                pos[id(d)] = (PADX + c * COLW, y, d, k)
                y += BH + GAP
            y += 6
        col_h[c] = y
    W = PADX * 2 + (maxc + 1) * COLW
    H = max(col_h.values(), default=PADY) + 40

    nstate = defaultdict(int)
    for d in agg.values():
        nstate[d["state"]] += 1

    P = [f'<!doctype html><html lang="ko"><head><meta charset="utf-8"><title>execution stages</title>']
    if refresh:
        P.append(f'<meta http-equiv="refresh" content="{refresh}">')
    P.append('''<style>
 body{background:#f5f7fb;color:#1f2733;font:12px/1.35 -apple-system,Segoe UI,Roboto,sans-serif;margin:0;padding:14px}
 h1{font-size:17px;margin:0 0 2px;color:#0f1722} .sub{color:#5b6675;font-size:12px;margin-bottom:8px}
 .lg{font-size:12px;color:#5b6675;margin:6px 0} .lg i{display:inline-block;width:11px;height:11px;border-radius:3px;margin:0 3px 0 12px;vertical-align:-1px;border:1px solid #0002}
 svg{background:#fff;border:1px solid #e3e8ef;border-radius:10px}
 @keyframes pulse{0%,100%{opacity:1}50%{opacity:.45}}
 .run{animation:pulse 1.1s ease-in-out infinite}
 g.n rect:hover{stroke-width:2.5}
</style></head><body>''')
    P.append('<h1>SCP 테스트 수행 단계 · 실행 로그 기반</h1>')
    P.append(f'<div class="sub">왼→오 = 의존 깊이(단계) · 각 박스 = 점검한 엔드포인트 1개 · 색 = 테스트 결과 '
             f'· 출처 reports/results/observations · {html.escape(meta.get("start",""))}'
             f'{" · 자동갱신 "+str(refresh)+"s" if refresh else ""}</div>')
    P.append(f'<div class="lg">'
             f'<i style="background:#c8efd4"></i>통과 {nstate["pass"]}'
             f'<i style="background:#cfe8ff"></i>수행중 {nstate["running"]}(깜빡)'
             f'<i style="background:#ffe6ad"></i>데이터필요 {nstate["soft"]}'
             f'<i style="background:#ffd6d6"></i>오류 {nstate["fail"]}</div>')

    P.append(f'<svg viewBox="0 0 {W} {H}" width="{W}" height="{H}">')
    for c in range(maxc + 1):
        P.append(f'<text x="{PADX + c*COLW + 4}" y="{PADY-26}" font-size="12" font-weight="700" fill="#8893a4">단계 {c}</text>')
        if c < maxc:
            xx = PADX + (c+1)*COLW - 12
            P.append(f'<line x1="{xx}" y1="{PADY-34}" x2="{xx}" y2="{H-20}" stroke="#eef1f6"/>')
    # kind sub-labels
    for c in range(maxc + 1):
        y = PADY
        for k in sorted(col_kind.get(c, {})):
            P.append(f'<text x="{PADX + c*COLW}" y="{y+13}" font-size="10.5" font-weight="700" fill="#aab3c0">{html.escape(k)}</text>')
            y += KGAP + (BH + GAP) * len(col_kind[c][k]) + 6
    # endpoint boxes
    for (x, y, d, k) in pos.values():
        st = d["state"]; fill, bd = _EXEC[st]
        run = ' class="run"' if st == "running" else ""
        dur = f'{d["mean_ms"]/1000:.1f}s' if d["mean_ms"] >= 1000 else f'{d["mean_ms"]:.0f}ms'
        cats = " ".join(f'{c}:{n}' for c, n in d["cats"].items())
        tip = (f'{d["full"]} · {d["path"]} · {_EXEC_KO[st]} · {d["n"]}회 호출 · '
               f'평균 {dur} · {cats} · src={d["source"]}')
        P.append(f'<g class="n"{run}><title>{html.escape(tip)}</title>'
                 f'<rect x="{x}" y="{y}" width="{BW}" height="{BH}" rx="6" fill="{fill}" stroke="{bd}" stroke-width="1.4"/>'
                 f'<circle cx="{x+10}" cy="{y+BH/2}" r="3.5" fill="{bd}"/>'
                 f'<text x="{x+20}" y="{y+16}" font-size="10.5">{html.escape(d["name"])}</text>'
                 f'<text x="{x+BW-6}" y="{y+16}" font-size="9" text-anchor="end" fill="#7a8493">{dur}{("·"+str(d["n"])) if d["n"]>1 else ""}</text></g>')
    P.append('</svg>')
    P.append(f'<div class="sub" style="margin-top:8px">{len(agg)} 엔드포인트 · {maxc+1} 단계 · '
             f'통과 {nstate["pass"]} / 오류 {nstate["fail"]} / 데이터필요 {nstate["soft"]}</div>')
    P.append('</body></html>')
    return "".join(P)


def _load_observations(path: str) -> list[dict]:
    import glob as _glob
    rows, seen = [], set()
    files = sorted(_glob.glob("reports/results/observations-gw*.jsonl")) + [path] \
        if path == "reports/results/observations.jsonl" else [path]
    for f in files:
        try:
            for line in open(f):
                line = line.strip()
                if not line:
                    continue
                try:
                    o = json.loads(line)
                except Exception:
                    continue
                key = (o.get("endpoint_key"), o.get("status"), round(o.get("ts") or 0, 3))
                if key in seen:
                    continue
                seen.add(key); rows.append(o)
        except FileNotFoundError:
            continue
    return rows


def main(argv=None):
    ap = argparse.ArgumentParser(description="Live resource-topology viewer from loggingaudit.")
    ap.add_argument("--events", help="pre-harvested loggingaudit JSONL (skip harvest)")
    ap.add_argument("--start", help="window start ISO8601 Z")
    ap.add_argument("--end", help="window end ISO8601 Z (default now)")
    ap.add_argument("--hours", type=float, default=6.0, help="window = now-<hours> when --start absent")
    ap.add_argument("--out", default="reports/audit/live_view.html")
    ap.add_argument("--refresh", type=int, default=0, help="HTML auto-refresh seconds (live mode)")
    ap.add_argument("--mode", choices=["flow", "dag", "gantt", "exec"], default="flow",
                    help="flow = per-instance resource topology (loggingaudit); "
                         "exec = test-log execution stages (pass/soft/fail); "
                         "dag = kind-level; gantt = timeline")
    ap.add_argument("--from", dest="source", choices=["audit", "obs"], default=None,
                    help="audit = loggingaudit resource state (default for flow/dag/gantt); "
                         "obs = test logs reports/results/observations (default for exec)")
    ap.add_argument("--obs", default="reports/results/observations.jsonl",
                    help="observations jsonl for --from obs / --mode exec")
    ap.add_argument("--all-resources", action="store_true",
                    help="include PRE-EXISTING account resources (untagged IAM "
                         "policies/ACLs, platform log-streams); default = ours only")
    ap.add_argument("--live-state", action="store_true",
                    help="cross-check live API for deferred-delete types (kms/secret): "
                         "pending-deletion (To_Be_Terminated) shows as 삭제예정 not lingering")
    a = ap.parse_args(argv)

    now = datetime.now(timezone.utc)
    end = a.end or now.strftime("%Y-%m-%dT%H:%M:%SZ")

    # TEST-LOG source: execution stages straight from observations (complete
    # record incl. probes + pass/fail), no harvest, no API. loggingaudit stays
    # the source for *resource state* (flow/dag/gantt).
    source = a.source or ("obs" if a.mode == "exec" else "audit")
    if source == "obs" or a.mode == "exec":
        try:
            from dashboard.gen_dep_map import dep_map_dict
            dep_kinds = set(dep_map_dict().get("depth", {}))
        except Exception:
            dep_kinds = set()
        obs = _load_observations(a.obs)
        if not obs:
            print(f"no observations in {a.obs}")
            return 1
        # window filter: --start ISO, else now-<hours> (--hours 0 = full history).
        win_lo = None
        if a.start:
            win_lo = _t(a.start).timestamp()
        elif a.hours and a.hours > 0:
            win_lo = now.timestamp() - a.hours * 3600
        if win_lo is not None:
            kept = [o for o in obs if (o.get("ts") or 0) >= win_lo]
            obs = kept or obs  # don't blank the view if the window misses everything
        agg = build_exec_spans(obs, now, dep_kinds)
        htmlout = render_exec(agg, now, {"start": f"{a.obs} ({len(obs)} obs)", "end": end}, refresh=a.refresh)
        Path(a.out).parent.mkdir(parents=True, exist_ok=True)
        Path(a.out).write_text(htmlout)
        from collections import Counter
        sc = Counter(d["state"] for d in agg.values())
        print(f"exec_view: {len(obs)} obs, {len(agg)} endpoints "
              f"(pass {sc['pass']} / running {sc['running']} / soft {sc['soft']} / fail {sc['fail']}) -> {a.out}")
        return 0

    if a.events:
        ev_path = a.events
        start = a.start or "(file)"
    else:
        from datetime import timedelta
        start = a.start or (now - timedelta(hours=a.hours)).strftime("%Y-%m-%dT%H:%M:%SZ")
        ev_path = "reports/audit/_live_view.jsonl"
        harvest(start, end, ev_path)

    events = []
    try:
        for line in open(ev_path):
            line = line.strip()
            if line:
                events.append(json.loads(line))
    except FileNotFoundError:
        print(f"no events file {ev_path}")
        return 1

    terminating = fetch_terminating() if a.live_state else None
    spans = build_spans(events, now, ours_only=not a.all_resources, terminating=terminating)
    _render = {"flow": render_flow, "dag": render_dag, "gantt": render}[a.mode]
    htmlout = _render(spans, now, {"start": start, "end": end}, refresh=a.refresh)
    Path(a.out).parent.mkdir(parents=True, exist_ok=True)
    Path(a.out).write_text(htmlout)
    live = sum(1 for d in spans.values() if d["start"] and not d["end"])
    print(f"live_view: {len(events)} events, {len(spans)} resources, {live} live -> {a.out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
