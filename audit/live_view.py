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
    """Harvest loggingaudit to ``out``. loggingaudit intermittently returns 0
    events (503/eventual-consistency); harvest to a temp and only replace ``out``
    when the new pull is non-empty, so a flaky empty harvest never BLANKS a good
    live page (it keeps the last good data instead). Retries a couple times."""
    tmp = out + ".tmp"
    for _ in range(3):
        subprocess.run([sys.executable, "-m", "audit.harvest", "--start", start,
                        "--end", end, "--out", tmp, "--service", "loggingaudit",
                        "--max-pages", str(max_pages)], check=False, timeout=300)
        try:
            n = sum(1 for ln in open(tmp) if ln.strip())
        except FileNotFoundError:
            n = 0
        if n > 0:
            try:
                Path(tmp).replace(out)
            except OSError:
                pass
            return out
    # all attempts empty — keep whatever good data ``out`` already had
    try:
        Path(tmp).unlink()
    except OSError:
        pass
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
    {(rtype, tag, key): {start, end|None, rtype, tag, name, ops:[(ts,event)]}}.

    ``ours_only`` (default) keeps only regr*/zznet*-tagged resources — the ones a
    test run created — so pre-existing account resources don't pollute the view.
    ``terminating`` (names from :func:`fetch_terminating`) flags deferred-delete
    resources whose delete was accepted (pending-deletion) so they read as
    삭제예정 instead of lingering as testing/created.

    INSTANCE KEY = resource_id when the event stream carries one (2026-07-08
    영구 유령 수리): rename 검증 스텝들(regrsrvXu / regrdashuX / '-renamed' 류)이
    이름을 바꾸면 name-키는 스팬을 쪼개 — pre-rename 스팬이 Delete End를 영영
    못 받아 화면에 영구 생존(이번 런 실측 3쌍). loggingaudit resource_id는
    rename 불변이므로 id-우선 키로 병합한다; id가 아예 없는 이벤트는 같은
    (rtype, tag, name)이 id를 가진 적 있으면 그 id로 귀속, 아니면 name 폴백.
    표시 name은 첫-등장 이름을 유지(_lk의 8-hex 접미 규약 보존)하고 최신
    이름은 renamed_to로 실어 툴팁에서 보이게 한다."""
    inst: dict = {}
    name_to_id: dict = {}   # (rtype, tag, name) -> resource_id (id 없는 이벤트 귀속용)
    for e in sorted(events, key=lambda x: x.get("timestamp", "")):
        rt = e.get("resource_type") or "?"
        nm = e.get("resource_name") or ""
        if ours_only and not _is_ours(nm):
            continue
        tag = _tag_of(e)
        rid = str(e["resource_id"]) if e.get("resource_id") else ""
        if rid:
            name_to_id[(rt, nm)] = rid
        else:
            rid = name_to_id.get((rt, nm), "")
        # rid가 있으면 키에서 tag/name을 모두 배제 — _tag_of는 unique 포함
        # 이름 전체를 태그로 쓰므로 rename 시 tag도 같이 변해 키가 갈라진다.
        key = (rt, rid) if rid else (rt, tag, nm)
        d = inst.setdefault(key, {"rtype": rt, "tag": tag, "name": nm,
                                  "start": None, "end": None, "ops": []})
        if rid and not d.get("res_id"):
            d["res_id"] = rid                        # for the oplog origin join
        if nm and nm != d["name"]:
            d["renamed_to"] = nm                     # rename 흔적 — 표시는 툴팁에서
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
        if terminating and d["rtype"] in _DEFERRED_DELETE and (
                d["name"] in terminating or d.get("renamed_to") in terminating):
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
    "cache-store": "clusters", "search-engine": "clusters", "event-streams": "clusters",
    "sql-server": "clusters",
    "instance-group": "instance-groups", "block-storage-group": "block-storage-groups",
    "log-stream": "log-streams", "log-group": "log-groups", "security-group": "security-groups",
    "nodepool": "nodepools", "public-ip": "public-ips", "publicip": "public-ips",
    "internet-gateway": "internet-gateways",
    # networking children loggingaudit emits hyphenated; gen_dep_map keys are
    # plural. Without these they fall through to _kind_of's pluralizer and (for
    # transit-gateway, absent from the model) end up parentless at depth 0 —
    # floating, with no relationship line. Pin them to their model kind so they
    # get a parent (vpc) + a sensible depth.
    "transit-gateway": "transit-gateways", "nat-gateway": "nat-gateways",
    "cluster": "clusters", "k8s-cluster": "clusters", "ske-cluster": "clusters",
}


# Parent overrides for kinds gen_dep_map leaves parentless (the resource model
# has no plain `requires` edge for them) but which DO have an obvious live
# parent. transit-gateway attaches to a vpc; without this it has no parent edge.
_PARENT_OVERRIDE = {
    "transit-gateways": "vpcs",
}


# DB engine resource_types all collapse to the overloaded kind "clusters" in
# gen_dep_map, whose depth (6) is the longest path of the DEEPEST user of that
# name (SKE/k8s + composite quick-query/data-ops chains). A real DB cluster only
# needs vpc->subnet(->db-subnet), so it belongs at depth 2 (subnet+1). Correct the
# display depth for these without disturbing the static dep map.
# loggingaudit emits some of these HYPHENATED (cache-store / search-engine /
# event-streams) — include both forms so they don't fall through to depth 0.
_DB_ENGINES = {"mysql", "postgresql", "mariadb", "epas", "cachestore", "sqlserver",
               "vertica", "searchengine", "eventstreams",
               "cache-store", "search-engine", "event-streams", "sql-server"}

# k8s / SKE clusters collapse to the SAME overloaded kind "clusters" as DB
# engines (depth 6 = the longest composite chain). A real SKE cluster's chain is
# only vpc(0) -> subnet(1) -> cluster, so it belongs at subnet+1 (=2); its
# nodepools sit one deeper at cluster+1 (=3). Without this they render at the
# static depth 6 (the user's "단계 6" complaint) with empty 3/4/5 between.
_K8S_CLUSTERS = {"cluster", "k8s-cluster", "ske-cluster", "kubernetes-cluster"}
_NODEPOOLS = {"nodepool", "node-pool", "nodepools"}


def _depth_of(rtype: str, kind: str, depth: dict) -> int:
    """Display depth for an instance. The 'clusters' kind in gen_dep_map is
    overloaded to depth 6 (longest SKE/composite path), which pushes BOTH DB and
    k8s clusters far right and leaves the intermediate columns empty. Correct the
    display depth to each type's REAL dependency chain without disturbing the
    static dep map:

      * DB engine cluster:  vpc(0) -> subnet(1) -> cluster(2)        == subnet+1
      * k8s / SKE cluster:  vpc(0) -> subnet(1) -> cluster(2)        == subnet+1
      * nodepool:           ... -> cluster(2) -> nodepool(3)         == cluster+1

    Falls back to the static kind depth for everything else. Robust to a missing
    'subnets' key (defaults subnet depth to 1)."""
    sub = depth.get("subnets", 1)
    if rtype in _DB_ENGINES or rtype in _K8S_CLUSTERS:
        return sub + 1                         # cluster directly under its subnet
    if rtype in _NODEPOOLS:
        return sub + 2                         # nodepool one deeper than its cluster
    return depth.get(kind, 0)


def _parent_of(kind: str, parent: dict) -> str | None:
    """Parent KIND of a kind, honoring _PARENT_OVERRIDE for kinds the static dep
    map leaves parentless (e.g. transit-gateways -> vpcs)."""
    return parent.get(kind) or _PARENT_OVERRIDE.get(kind)


# Session-shared infra is named with an 'sh' infix the per-lifecycle resources
# never carry: the shared VPC is 'regrvpcsh<ts>', the shared subnet 'regrsubsh<ts>'
# and the DB-lane subnet 'regrsubshdb<ts>' (engine.provision_shared_vpc), while a
# per-lifecycle vpc/subnet is 'regrvpc<8hex>' / 'regrsub<8hex>' (no 'sh'). The
# shared VPC's INTERNET-gateway/firewall children embed the same vpc name
# (IGW_regrvpcsh<ts>), so this infix also catches them. This is the reliable
# discriminator for "is this the hub that many scenarios adopt?" — its lifecycle
# key can collide with a normal 8-hex (the ts hex is currently 8 chars), so we do
# NOT key off _lk for sharing; we key off the name.
_SHARED_INFRA = re.compile(r"regr(?:vpc|sub)sh")


def _is_shared_infra(d: dict) -> bool:
    """True if this instance is the session-shared (adopted) vpc/subnet hub."""
    return bool(_SHARED_INFRA.search(d.get("name") or ""))


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
    we show it as scheduled-for-deletion, i.e. effectively deleted. A span the
    LOCAL console records already deleted (``local_deleted`` — its run's 2xx
    DELETE step) is deleted regardless of loggingaudit lag (유령 자원 fix)."""
    names = [n for _, n in d["ops"]]
    if any("Delete" in n and "End" in n for n in names):
        return "deleted"
    if d.get("local_deleted"):
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

    # x = depth (creation order); fallback depth 0. Use the per-instance depth
    # correction so the overloaded 'clusters'/'nodepools' kinds sit near their
    # real parent (subnet+1 / +2) instead of the static SKE depth 6.
    col = {k: _depth_of(info.get("rtype", ""), k, depth) for k, info in kinds.items()}
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
        par = _parent_of(k, parent)
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


# --- origin annotation: join loggingaudit spans with the OPLOG bucket ---------
# loggingaudit events carry NO run id — the oplog bucket does (core.oplog writes
# runs/<run_id>/res/<ms>-<pid>-<seq>.json batches whose events carry action/res_id/
# name/lifecycle). Joining the two lets the runtime view say WHO made each span:
#   origin = "local:<run_id>"  — a run this console started (its run-rec id, or the
#                                bare "local" fallback core.oplog uses off-CI)
#          | "ci:<run_id>"     — a CI run (gha-* prefixed, or a bare numeric
#                                GITHUB_RUN_ID from api-test.yml)
#          | "unknown"         — no oplog event matched (or the bucket is off)
# Everything here is best-effort: an unreachable bucket returns None and the
# caller renders exactly as before (origin stays unset).

def fetch_oplog_res_events(start_ms: int, max_objects: int = 400):
    """Read runs/*/res/*.json batch objects newer than ``start_ms`` from the oplog
    bucket; every event is tagged with its run_id (from the key). Returns a list,
    or **None** when the bucket is unreachable/unconfigured (degrade gracefully)."""
    try:
        from core import oplog as _oplog
        c, cfg = _oplog._client()
    except Exception:
        return None
    if not c:
        return None
    out: list[dict] = []
    keys: list[tuple[int, str, str]] = []   # (ms, run_id, key)
    try:
        token = None
        for _ in range(40):                  # page cap — bounded work
            kw = {"Bucket": cfg["bucket"], "Prefix": "runs/", "MaxKeys": 1000}
            if token:
                kw["ContinuationToken"] = token
            resp = c.list_objects_v2(**kw)
            for obj in resp.get("Contents") or []:
                key = obj.get("Key") or ""
                parts = key.split("/")
                # runs/<run_id>/res/<ms>-<pid>-<seq>.json
                if len(parts) != 4 or parts[2] != "res":
                    continue
                try:
                    ms = int(parts[3].split("-", 1)[0])
                except ValueError:
                    continue
                if ms >= start_ms:
                    keys.append((ms, parts[1], key))
            if not resp.get("IsTruncated"):
                break
            token = resp.get("NextContinuationToken")
    except Exception:
        return None
    keys.sort()
    for _ms, rid, key in keys[-max_objects:]:
        try:
            body = c.get_object(Bucket=cfg["bucket"], Key=key)["Body"].read()
            batch = json.loads(body)
        except Exception:
            continue
        for ev in (batch.get("events") or []) if isinstance(batch, dict) else []:
            if isinstance(ev, dict):
                ev = dict(ev)
                ev["_run_id"] = rid
                out.append(ev)
    return out


def origin_of_run_id(rid: str, local_run_ids=()) -> str:
    """Classify an oplog run_id: console-local rec ids (and the bare 'local'
    fallback) -> local:<rid>; gha-* / bare-numeric GITHUB_RUN_ID -> ci:<rid>;
    anything else is unattributable -> unknown."""
    rid = str(rid or "")
    if rid in set(local_run_ids) or rid == "local":
        return f"local:{rid}"
    if rid.startswith("gha-") or rid.isdigit():
        return f"ci:{rid}"
    return "unknown"


def annotate_origins(spans, oplog_events, local_run_ids=()) -> None:
    """Set ``span['origin']`` from the oplog join — match by res_id first, then by
    resource name. ``oplog_events=None`` (bucket unreachable) leaves origin unset
    (None) so the view renders exactly as today."""
    if oplog_events is None:
        return
    by_id: dict[str, str] = {}
    by_name: dict[str, str] = {}
    for ev in oplog_events:
        rid = str(ev.get("_run_id") or ev.get("run_id") or "")
        if not rid:
            continue
        if ev.get("res_id"):
            by_id[str(ev["res_id"])] = rid
        if ev.get("name"):
            by_name[str(ev["name"])] = rid
    for d in spans.values():
        rid = by_id.get(str(d.get("res_id") or "")) or by_name.get(d.get("name") or "")
        d["origin"] = origin_of_run_id(rid, local_run_ids) if rid else "unknown"


def annotate_local_origins(spans, local_index) -> None:
    """Overlay LOCAL attribution from the console's own IN-PROCESS run records —
    the per-run console-events sink (``resource-tracked``/``resource-deleted``)
    plus the per-run ``core.registry`` manifest shards. Matches by res_id first,
    then by resource name, exactly like :func:`annotate_origins`.

    Runs AFTER :func:`annotate_origins` and WINS over it: for runs THIS console
    started, its in-process record is authoritative — the oplog-bucket join is
    best-effort and demonstrably lags/misses local runs (defect 2026-07-04:
    ``scope=mine`` blank during an ACTIVE local run because the bucket had no
    ``runs/<rec>/res/*`` objects yet). Local attribution must never depend on
    the bucket; the bucket join remains for CI (``gha-*``) badge attribution.

    ``local_index``: ``{run_id: {"ids": iterable, "names": iterable}}`` — plus
    optional ``deleted_ids``/``deleted_names`` (resources the run's own 2xx
    DELETE steps already removed). A span matching a deleted key is flagged
    ``local_deleted`` so :func:`_state_of` shows it 삭제됨 (and the default
    ``deleted=hide`` filter drops it) even while loggingaudit still lags the
    Delete event — the '유령 자원' fix (2026-07-04: already-deleted resources
    kept rendering as 생성됨/테스트중 in scope=mine).
    Empty/None index is a no-op (spans render exactly as annotate_origins left
    them)."""
    if not local_index:
        return
    by_id: dict[str, str] = {}
    by_name: dict[str, str] = {}
    del_ids: set = set()
    del_names: set = set()
    for rid, idx in local_index.items():
        for i in (idx.get("ids") or ()):
            if i:
                by_id[str(i)] = str(rid)
        for n in (idx.get("names") or ()):
            if n:
                by_name[str(n)] = str(rid)
        for i in (idx.get("deleted_ids") or ()):
            if i:
                del_ids.add(str(i))
        for n in (idx.get("deleted_names") or ()):
            if n:
                del_names.add(str(n))
    if not (by_id or by_name or del_ids or del_names):
        return
    for d in spans.values():
        rid_s = str(d.get("res_id") or "")
        name_s = d.get("name") or ""
        rid = by_id.get(rid_s) or by_name.get(name_s)
        if rid:
            d["origin"] = f"local:{rid}"
        if (rid_s and rid_s in del_ids) or (name_s and name_s in del_names):
            d["local_deleted"] = True


def filter_spans(spans, scope: str = "mine", deleted: str = "hide"):
    """Scope/visibility filter for the runtime view. ``scope=mine`` keeps only
    spans whose origin is local:*; ``deleted=hide`` drops spans already in the
    deleted state. Returns a NEW dict (input untouched)."""
    out = {}
    for k, d in spans.items():
        if scope == "mine" and not str(d.get("origin") or "").startswith("local:"):
            continue
        if deleted == "hide" and _state_of(d) == "deleted":
            continue
        out[k] = d
    return out


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


_ORIGIN_BADGE = {"local": ("내 실행", "#2b7de9"), "ci": ("CI", "#b5740b"),
                 "unknown": ("출처?", "#8b95a3")}


def _origin_badge(origin) -> tuple[str, str] | None:
    if not origin:
        return None
    kind = str(origin).split(":", 1)[0]
    return _ORIGIN_BADGE.get(kind, _ORIGIN_BADGE["unknown"])


def _runtime_chrome_html(chrome: dict) -> str:
    """Standalone /runtime page shell: a minimal Testing header (the 4-menu nav +
    '← Test Execution' back-link), the scope/hours/deleted control bar, the
    account-wide banner, and the hygiene cross-link. Self-contained (inline CSS/JS;
    controls navigate by query params so the server re-filters)."""
    scope = chrome.get("scope", "mine")
    hours = int(chrome.get("hours", 1) or 1)
    deleted = chrome.get("deleted", "hide")
    parts = ['''<style>
 .rt-shell{display:flex;align-items:center;gap:14px;background:#fff;border:1px solid #e3e8ef;
   border-radius:10px;padding:8px 14px;margin-bottom:10px;font-size:12.5px}
 .rt-shell b{font-size:13.5px} .rt-shell a{color:#2563c9;text-decoration:none;font-weight:600}
 .rt-shell a:hover{text-decoration:underline} .rt-shell .sep{color:#c3ccd9}
 .rt-ctl{display:flex;align-items:center;gap:10px;flex-wrap:wrap;background:#fff;
   border:1px solid #e3e8ef;border-radius:10px;padding:7px 14px;margin-bottom:10px;font-size:12.5px}
 .rt-ctl .lbl{color:#5b6675;font-weight:700}
 .rt-ctl a.tgl{border:1px solid #d7dee8;border-radius:16px;padding:3px 11px;color:#5b6675;
   text-decoration:none;font-weight:600}
 .rt-ctl a.tgl.on{border-color:#2b7de9;background:#e8f0fd;color:#2563c9}
 .rt-banner{background:#fdf3e2;border:1px solid #ecd9ae;color:#7a5a10;border-radius:10px;
   padding:8px 13px;font-size:12.5px;margin-bottom:10px}
 .rt-note{background:#eef4ff;border:1px solid #d3e2fb;color:#2c4d86;border-radius:10px;
   padding:8px 13px;font-size:12.5px;margin-bottom:10px}
</style>''']
    parts.append(
        '<div class="rt-shell"><b>SCP API Regression</b>'
        '<span><a href="/catalog">Catalog</a> <span class="sep">→</span> '
        '<a href="/planning/resources/map">Modeling</a> <span class="sep">→</span> '
        '<a href="/testing/embed">Testing</a> <span class="sep">→</span> '
        '<a href="/reporting">Reporting</a></span>'
        '<span style="margin-left:auto"><a href="/testing/embed">← Test Execution</a></span></div>')

    def q(s, h, dl):
        return f"/runtime?scope={s}&hours={h}&deleted={dl}"
    hours_opts = "".join(
        f'<a class="tgl {"on" if hours == h else ""}" href="{q(scope, h, deleted)}">{h}시간</a>'
        for h in (1, 6, 24))
    parts.append(
        '<div class="rt-ctl"><span class="lbl">범위</span>'
        f'<a class="tgl {"on" if scope == "mine" else ""}" href="{q("mine", hours, deleted)}">내 실행</a>'
        f'<a class="tgl {"on" if scope == "all" else ""}" href="{q("all", hours, deleted)}">계정 전체</a>'
        f'<span class="lbl" style="margin-left:8px">윈도우</span>{hours_opts}'
        f'<label style="margin-left:8px;cursor:pointer"><input type="checkbox" '
        f'{"checked" if deleted == "show" else ""} '
        f'onchange="location=\'{q(scope, hours, "show" if deleted != "show" else "hide")}\'"> '
        '삭제됨 표시</label>'
        '<span style="margin-left:auto">자원 위생 → <a href="/testing/resources">Testing ▸ 리소스</a></span></div>')
    if chrome.get("banner"):
        parts.append(f'<div class="rt-banner">⚠ {html.escape(chrome["banner"])}</div>')
    if chrome.get("note"):
        parts.append(f'<div class="rt-note">{html.escape(chrome["note"])}</div>')
    return "".join(parts)


def render_flow(spans, now: datetime, meta: dict, refresh: int = 0,
                chrome: dict | None = None) -> str:
    """v3 — per-INSTANCE topology (id shown), light theme, running pulses, deleted greys.
    ``chrome`` (optional) wraps the page in the standalone /runtime shell: Testing
    header + scope/hours/deleted controls + banner + per-box origin badges."""
    from collections import defaultdict
    try:
        from dashboard.gen_dep_map import dep_map_dict
        dm = dep_map_dict()
        depth, parent = dm.get("depth", {}), dm.get("parent", {})
    except Exception:
        depth, parent = {}, {}
    dep_kinds = set(depth)

    # column = creation-order depth of the instance's kind; group col -> kind -> instances
    insts = [d for d in spans.values() if d["start"]]
    kind_of = {}                       # id(d) -> kind
    by_kind_lk = defaultdict(list)     # (kind, lifecycle-key) -> [d]
    by_kind = defaultdict(list)        # kind -> [d]  (for adoption-edge fallback)
    raw_col = {}                       # id(d) -> raw dependency depth (pre-compaction)
    for d in insts:
        k = _kind_of(d["rtype"], dep_kinds)
        kind_of[id(d)] = k
        by_kind_lk[(k, _lk(d))].append(d)
        by_kind[k].append(d)
        raw_col[id(d)] = _depth_of(d["rtype"], k, depth)

    # Compact empty columns: depth correction + the overloaded kinds leave gaps
    # (resources land only at e.g. 0/1/2/3, never 4/5, or only 0/1/6). Remap the
    # SET of actually-used raw depths to consecutive 0..N so there are no empty
    # 단계 columns, while preserving left→right = creation/dependency order.
    used = sorted(set(raw_col.values()))
    remap = {raw: i for i, raw in enumerate(used)}
    col_kind = defaultdict(lambda: defaultdict(list))
    inst_col = {}                      # id(d) -> compacted column
    for d in insts:
        c = remap[raw_col[id(d)]]
        inst_col[id(d)] = c
        col_kind[c][d["rtype"]].append(d)
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
    nsurv = 0
    for d in insts:
        if d.get("survivor"):   # 실측 잔존 핀 (owned 스캔 오버레이) — 상태 집계와 분리
            nsurv += 1
            continue
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
 path.rel.adopt.hi{stroke:#2b7de9;stroke-dasharray:4 3}
 g.n.hi rect{stroke:#2b7de9;stroke-width:2.6}
 .hint{color:#5b6675;font-size:11px;margin:2px 0 8px}
</style></head><body>''')
    if chrome:
        P.append(_runtime_chrome_html(chrome))
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
             f'<i style="background:#ffd6d6"></i>생성실패 {nstate["failed"]}'
             + (f'<i style="background:#fff1f1;border-color:#cf222e;border-style:dashed"></i>'
                f'실측 잔존 {nsurv}' if nsurv else '')
             + f'<span style="margin-left:12px">— 실선=자체생성 · ┄점선=공유인프라 채택</span></div>')

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

    # relationship lines: parent-instance -> child-instance.
    #
    # OWN edges (solid): the engine names every resource of ONE lifecycle with the
    # same 8-hex unique, so a vpc and its subnet/server share _lk(); we connect a
    # child to the parent-KIND instance of its OWN lifecycle (which vpc owns which
    # subnet).
    #
    # ADOPTION edges (dashed): the run provisions ONE session-shared vpc+subnet
    # (regrvpcsh*/regrsubsh*, its own tag) that MANY scenarios ADOPT — each adopter
    # is a different _lk, so its child (an ske cluster, a VM, a tgw, …) never shares
    # the shared subnet's lifecycle key and would float unconnected. So when a
    # child finds NO parent-KIND instance in its own _lk, we fall back to the
    # SHARED parent instance(s) of that kind, making the shared subnet/vpc a hub
    # that fans out to every concurrent scenario. Drawn first so boxes sit on top.
    xy = {id(d): (px, py) for (px, py, d, rt) in pos.values()}
    nid = {id(d): f"n{i}" for i, (_x, _y, d, _rt) in enumerate(pos.values())}
    # shared-infra instances per kind — the hub fallback targets
    shared_by_kind = defaultdict(list)
    for d in insts:
        if _is_shared_infra(d):
            shared_by_kind[kind_of[id(d)]].append(d)
    adj = defaultdict(set)   # node-id -> connected node-ids (undirected, for click highlight)
    edges = 0
    adopt_edges = 0

    def _emit_edge(pd, d, adopt: bool):
        nonlocal edges, adopt_edges
        if pd is d or id(pd) not in nid or id(d) not in nid:
            return
        a, b = nid[id(pd)], nid[id(d)]
        if b in adj[a]:                            # de-dup parallel edges
            return
        ax, ay = xy[id(pd)]; bx, by = xy[id(d)]
        x1, y1 = ax + BW, ay + BH / 2              # parent right edge
        x2, y2 = bx, by + BH / 2                   # child left edge
        # adoption edge styled distinctly: dashed + lighter so adopt vs own-create
        # reads differently; own-create edge is the solid line as before.
        style = ('stroke="#9fb4d8" stroke-width="1.1" stroke-dasharray="4 3"'
                 if adopt else 'stroke="#c3ccd9" stroke-width="1.1"')
        cls = "rel adopt" if adopt else "rel"
        P.append(f'<path class="{cls}" id="e{edges}" data-a="{a}" data-b="{b}" '
                 f'd="M{x1:.0f},{y1:.0f} C{x1+34:.0f},{y1:.0f} {x2-34:.0f},{y2:.0f} {x2:.0f},{y2:.0f}" '
                 f'fill="none" {style} marker-end="url(#rel)"/>')
        adj[a].add(b); adj[b].add(a)
        edges += 1
        if adopt:
            adopt_edges += 1

    for d in insts:
        k = kind_of[id(d)]
        par = _parent_of(k, parent)
        if not par:
            continue
        # the shared hub itself attaches upward (shared subnet -> shared vpc) by
        # its OWN lifecycle key (regrvpcsh/regrsubsh share the ts hex), so the
        # normal own-lifecycle path links it without any self-adoption.
        own = [pd for pd in by_kind_lk.get((par, _lk(d)), []) if pd is not d]
        if own:
            for pd in own:
                _emit_edge(pd, d, adopt=False)
        elif not _is_shared_infra(d):
            # no parent in our own lifecycle, and we are not the hub — adopt the
            # shared parent hub of that kind (the fan-out the user expects).
            for pd in shared_by_kind.get(par, []):
                _emit_edge(pd, d, adopt=True)

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
        origin = d.get("origin")
        otip = f' · 출처 {origin}' if origin else ""
        tip = f'{rt} · {html.escape(d["name"] or tag)} · {_STATE_KO[st]} · {d["start"]}→{d["end"] or "LIVE"} · {dur} · {len(d["ops"])} ops{otip}{linked}'
        deco = ' text-decoration="line-through"' if st in ("deleted", "terminating") else ""
        txt_gray = st in ("deleted", "terminating")
        badge = _origin_badge(origin)
        rtext = (f'<tspan fill="{badge[1]}" font-weight="700">{html.escape(badge[0])}</tspan> {dur}'
                 if badge else dur)
        dash = ""
        if d.get("survivor"):
            # 실측 잔존 (owned 스캔 오버레이): SurvivorScan ops 뿐이라 _state_of가
            # 'creating'으로 오분류·펄스하므로 여기서 선처리 — 붉은 점선, 펄스 없음.
            # 기지(known-stuck) 항목은 앰버 — 문서화된 stuck(예: IAM-gated
            # log-group)이라 경보가 아닌 정보로 읽혀야 한다.
            fill, bd, run, dash = "#fff1f1", "#cf222e", "", ' stroke-dasharray="5 3"'
            ks = d.get("known_stuck")
            if ks is not None:
                fill, bd = "#fff8e6", "#d99413"
            rtext = f'스캔 {d.get("scan_hhmm", "")}'
            tip = (f'{rt} · {html.escape(d["name"] or tag)} · '
                   f'{"기지 잔존(문서화된 stuck)" if ks is not None else "실측 잔존"}'
                   f' — owned 스캔 {d.get("scan_hhmm", "")} 확인분 (이벤트 창 밖, '
                   f'라이브 LIST 실측) · id {html.escape(str(d.get("res_id", "")))}'
                   + (f' · {html.escape(str(ks))}' if ks else ''))
        P.append(f'<g class="n{run}" id="{myid}" onclick="hi(\'{myid}\')"><title>{html.escape(tip)}</title>'
                 f'<rect x="{x}" y="{y}" width="{BW}" height="{BH}" rx="6" fill="{fill}" stroke="{bd}" stroke-width="1.4"{dash}/>'
                 f'<circle cx="{x+10}" cy="{y+BH/2}" r="3.5" fill="{bd}"/>'
                 f'<text x="{x+20}" y="{y+16}" font-size="11" fill="{"#9aa4b2" if txt_gray else "#1f2733"}"{deco}>{html.escape(lab)}</text>'
                 f'<text x="{x+BW-6}" y="{y+16}" font-size="9.5" text-anchor="end" fill="#7a8493">{rtext}</text></g>')
    P.append('</svg>')
    P.append(f'<div class="sub" style="margin-top:8px">{len(insts)} 인스턴스 · {maxc+1} 단계 · {edges} 연관선 '
             f'(공유인프라 채택 {adopt_edges}개 점선) '
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
    if not refresh:
        # ambient refresh (2026-07-04): an OPEN runtime popup with a fresh cache
        # never refreshed again, so it silently drifted stale (deleted resources
        # kept showing as 생성됨/테스트중). A slow JS reload keeps a left-open
        # window converging without the aggressive 12s meta-refresh cadence the
        # stale/generating states use.
        P.append('<script>setTimeout(function(){location.reload();},90000)</script>')
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
