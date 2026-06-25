#!/usr/bin/env python3
"""console2 — local execution console (browse → plan → run → live report).

A zero-dependency (stdlib ``http.server``) backend for ``console2/``. Run it on a
machine that has the repo + working SCP creds; it serves the console AND drives
the real composition/scheduling engine so you can watch a run unfold **in DAG
order, "which API is being tested right now"**.

    python tools/console2_server.py                 # http://127.0.0.1:9100/
    PORT=9200 python tools/console2_server.py        # override the default port

What it exposes (all JSON unless noted):
  GET  /                       -> console2/index.html (+ static bundle)
  GET  /api/model              -> the resource-task model: categories, services,
                                  resources (+ per-resource deps & API endpoints),
                                  and the runnable lifecycles (+ their step lists).
  POST /api/graph {selection}  -> the composition DAG (composer.graph_view) for a
                                  selection: resource nodes with longest-path levels,
                                  is_target/shared(dedup) flags, edges, create order,
                                  teardown order, peak quota. Empty selection -> empty
                                  graph. Read-only projection (no cloud, no schedule).
  POST /api/plan  {selection}  -> the REAL dag_planner schedule for a selection
                                  (shared roots, topological waves, VPC-slot
                                  accounting) + a per-lifecycle step preview.
  POST /api/run   {lifecycle_ids, mode, heavy, mutations, destructive}
                               -> start a run. mode=simulate replays the plan
                                  (no cloud, deterministic); mode=live runs
                                  ``pytest tests/crud`` with the safety gates from
                                  the request and SCP_CONSOLE_EVENTS pointed at the
                                  run's event file (same live view as simulate).
  GET  /api/runs               -> run records (newest first)
  GET  /api/runs/<id>          -> one record (+ log tail)
  GET  /api/runs/<id>/events   -> the structured live-event stream (JSONL parsed)
  POST /api/cleanup            -> FORCE reconciler sweep (delete all owned, ignore TTL)
  POST /api/verify             -> read-only owned-resource inventory (proof of clean)
  POST /api/owned              -> read-only owned-resource inventory as a STRUCTURED
                                  list (service · path · total) for the run-screen
                                  "남은 자원(잔존)" pre-flight panel. LIST calls only.
  GET  /api/suites             -> named suites (suites/*.yaml via core.suites) as
                                  {id,label,request,gates,scope,builtin} — the Suite ▾
                                  presets (smoke/full/full-heavy/conformance + saved).
  POST /api/suites {id,label,request,scope?}
                               -> save the current selection as a suite: writes
                                  suites/<id>.yaml (validated by core.suites). The
                                  optional `scope` preserves console2's exact
                                  selection and is ignored by core.suites / CI.

Safety: identical opt-in to console_server / chat-heavy — mutation/destructive/
heavy gates are set PER RUN from the request only, never globally. Simulate makes
no cloud calls at all. Reuses the proven provision-shared-VPC → pytest → teardown
→ reconciler-sweep flow (same ``-m`` module invocations as console_server.py).
"""
from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import threading
import time
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
WEB = ROOT / "console2"
RUN_DIR = ROOT / "reports" / "console2-runs"
PORT = int(os.environ.get("PORT", "9100"))

_RUNS: dict[str, dict] = {}
_LOCK = threading.Lock()
_MODEL: dict | None = None
# lazy-built map "METHOD norm(path)" -> endpoint parameter schema (built once from
# data/api_catalog.json + data/api_catalog_params.json on first /api/model or
# /api/endpoint-params request). None until built.
_EP_PARAMS: dict | None = None
# simulate pacing — make the dry-run VISIBLY step through 생성→테스트→삭제 in DAG
# order (not flash by in ~1s). Per-HTTP-step delay + a short beat around a
# resource create/delete. Env-overridable so tests can run them at 0 (hermetic +
# fast); the UI default is a watchable ~0.35s/step. read at call time.
_SIM_STEP_DELAY = float(os.environ.get("SCP_SIM_STEP_DELAY", "0.35"))
_SIM_BEAT = float(os.environ.get("SCP_SIM_BEAT", "0.18"))
_CT = {".html": "text/html; charset=utf-8", ".js": "text/javascript; charset=utf-8",
       ".css": "text/css; charset=utf-8", ".json": "application/json",
       ".svg": "image/svg+xml", ".ico": "image/x-icon"}


# --------------------------------------------------------------------------- #
# model: categories -> services -> resources (+ deps & endpoints) + lifecycles
# --------------------------------------------------------------------------- #
def _norm_requires(task: dict):
    """Mirror poc/scenario-viz/build_data.norm_requires + composer keying so the
    same viz.js consumes this model: AND refs, one_of branch groups, credentials."""
    and_deps, groups, creds = [], [], []
    for entry in task.get("requires") or []:
        if isinstance(entry, str):
            and_deps.append({"ref": entry, "count": 1})
        elif isinstance(entry, dict) and "one_of" in entry:
            branches = [b if isinstance(b, str) else b.get("ref") for b in entry["one_of"]]
            groups.append({"bind": entry.get("bind"), "branches": branches})
        elif isinstance(entry, dict) and "credential" in entry:
            creds.append(str(entry["credential"]))
        elif isinstance(entry, dict) and "ref" in entry:
            and_deps.append({"ref": entry["ref"], "count": int(entry.get("count", 1))})
    return and_deps, groups, creds


_STEP_KINDS = (  # name/method heuristics -> a coarse kind for the UI badge
    ("action", lambda s: s.get("action")),
    ("adopt", lambda s: s.get("adopt")),
    ("probe", lambda s: (s.get("name") or "").startswith("probe")),
    ("create", lambda s: (s.get("method") or "").upper() == "POST"),
    ("delete", lambda s: (s.get("method") or "").upper() == "DELETE"),
    ("update", lambda s: (s.get("method") or "").upper() in ("PUT", "PATCH")),
    ("read", lambda s: (s.get("method") or "").upper() == "GET"),
)


def _step_kind(step: dict) -> str:
    nm = (step.get("name") or "").lower()
    if "wait" in nm or "active" in nm or "ready" in nm:
        return "wait"
    for kind, pred in _STEP_KINDS:
        try:
            if pred(step):
                return kind
        except Exception:
            pass
    return "step"


def _build_model() -> dict:
    """Load the M5 resource-task model (knowledge/formal/resources/*.yaml) into the
    viz.js node shape, plus the runnable lifecycles (loader) with their step lists.
    Pure offline read — no creds, no network."""
    import yaml  # local import: only needed when (re)building the model
    res_dir = ROOT / "knowledge" / "formal" / "resources"

    groups_meta = {}
    gpath = res_dir / "_groups.yaml"
    if gpath.exists():
        groups_meta = (yaml.safe_load(gpath.read_text()) or {}).get("groups", {}) or {}

    nodes: dict[str, dict] = {}
    for path in sorted(res_dir.glob("*.yaml")):
        if path.name.startswith("_"):
            continue
        data = yaml.safe_load(path.read_text()) or {}
        for nid, task in (data.get("resources") or {}).items():
            if not isinstance(task, dict):
                continue
            service = task.get("service", "")
            cat = service.split("/")[0] if "/" in service else path.name.split("__")[0]
            and_deps, one_of, creds = _norm_requires(task)
            create = task.get("create") or {}
            opts = []
            for oname, ospec in (create.get("options") or {}).items():
                if not isinstance(ospec, dict):
                    opts.append({"name": oname, "type": "?", "required": False})
                    continue
                opts.append({"name": oname, "type": ospec.get("type", "?"),
                             "required": bool(ospec.get("required", False)),
                             "values": ospec.get("values"),
                             "ref_target": ospec.get("target") if ospec.get("type") == "ref" else None})
            code = task.get("code") or ""
            group = task.get("group") or ("-".join(code.split("-")[:2]) if code else cat)
            ready = task.get("ready") or {}
            verify = task.get("verify") or []
            delete = task.get("delete") or {}
            src = task.get("source") if isinstance(task.get("source"), dict) else {}
            # the API endpoints this resource touches (for the "which API" detail)
            api = []
            if create.get("endpoint"):
                api.append({"phase": "create", "endpoint": create["endpoint"]})
            for v in verify:
                if isinstance(v, dict) and v.get("endpoint"):
                    api.append({"phase": "verify", "endpoint": v["endpoint"], "name": v.get("name")})
            if delete.get("endpoint"):
                api.append({"phase": "delete", "endpoint": delete["endpoint"]})
            nodes[nid] = {
                "id": nid, "code": code, "service": service, "category": cat,
                "group": group,
                "group_label": (groups_meta.get(group) or {}).get("label", group),
                "provenance": task.get("provenance", "?"),
                "heavy": bool(task.get("heavy", False)),
                "adopt": task.get("adopt"), "quota": task.get("quota"),
                "endpoint": create.get("endpoint", ""),
                "and": and_deps, "one_of": one_of, "creds": creds, "options": opts,
                "ready_timeout": ready.get("timeout"), "verify_n": len(verify),
                "has_delete": bool(delete), "api": api,
                "lifecycle": src.get("lifecycle"),
            }

    present_groups = {}
    for n in nodes.values():
        present_groups.setdefault(n["group"], {
            "label": (groups_meta.get(n["group"]) or {}).get("label", n["group"]),
            "category": (groups_meta.get(n["group"]) or {}).get("category", n["category"])})

    # runnable lifecycles + their step lists (the engine executes THESE)
    from regression.scenarios.loader import load_lifecycles
    lcs, sources = load_lifecycles(with_sources=True)
    lifecycles: dict[str, dict] = {}
    for lc in lcs:
        steps = []
        for s in lc.get("steps", []):
            steps.append({"name": s.get("name", ""), "method": s.get("method"),
                          "path": s.get("path"), "optional": bool(s.get("optional")),
                          "kind": _step_kind(s)})
        lifecycles[lc["id"]] = {
            "id": lc["id"], "service": lc.get("service", ""),
            "enabled": bool(lc.get("enabled")), "heavy": bool(lc.get("heavy")),
            "n_steps": len(steps), "steps": steps, "source": sources.get(lc["id"], ""),
        }

    categories = sorted({n["category"] for n in nodes.values()})
    services = sorted({n["service"] for n in nodes.values() if n["service"]})
    return {"nodes": nodes, "groups": present_groups, "categories": categories,
            "services": services, "lifecycles": lifecycles,
            "node_count": len(nodes), "lifecycle_count": len(lifecycles),
            "validated": sum(1 for n in nodes.values() if n["provenance"] == "VALIDATED")}


def _model() -> dict:
    global _MODEL
    if _MODEL is None:
        _MODEL = _build_model()
    return _MODEL


# --------------------------------------------------------------------------- #
# read-only DEFINITION views — surface the per-service test definition (lifecycle
# steps + resource endpoints/options/deps, all already in the model) and the
# accumulated knowledge facts (knowledge/*.md). Pure offline reads — no creds, no
# network, no mutation. Served by GET /api/lifecycles and GET /api/knowledge so the
# console can show "📖 정의" for a service; later a POST sibling can make it editable.
# --------------------------------------------------------------------------- #
def _lifecycles_view(service: str) -> dict:
    """For one ``cat/svc`` service: its resource tasks (code, provenance, the
    create/verify/delete endpoints, request-body options, dependencies) + the
    runnable lifecycles (id + ordered steps). Projected from the built model."""
    m = _model()
    resources = []
    lc_ids: set[str] = set()
    for nid, n in m["nodes"].items():
        if n.get("service") != service:
            continue
        if n.get("lifecycle"):
            lc_ids.add(n["lifecycle"])
        resources.append({
            "id": nid, "code": n.get("code", ""), "provenance": n.get("provenance", "?"),
            "heavy": bool(n.get("heavy")), "quota": n.get("quota"),
            "endpoint": n.get("endpoint", ""), "api": n.get("api") or [],
            "options": n.get("options") or [],
            "deps": {"and": n.get("and") or [], "one_of": n.get("one_of") or [],
                     "creds": n.get("creds") or []},
            "lifecycle": n.get("lifecycle"),
        })
    resources.sort(key=lambda r: (not r["code"], r["code"] or r["id"]))
    lifecycles = [lc for lid, lc in m["lifecycles"].items()
                  if lc.get("service") == service or lid in lc_ids]
    lifecycles.sort(key=lambda lc: lc["id"])
    return {"service": service, "resources": resources, "lifecycles": lifecycles,
            "n_resources": len(resources), "n_lifecycles": len(lifecycles)}


def _knowledge_view(service: str, cap: int = 24) -> dict:
    """Best-effort: pull the paragraphs in ``knowledge/*.md`` that mention this
    service — by its ``cat/svc`` path, its short name, or any of its resource
    codes — each tagged with the nearest ``#`` heading. Read-only; the markdown is
    the source of truth (this is a filtered view, not a copy)."""
    import re
    m = _model()
    short = service.split("/")[-1]
    codes = sorted({n["code"] for n in m["nodes"].values()
                    if n.get("service") == service and n.get("code")})
    # specific tokens (service path + resource codes) rank above the bare short name
    specific = [service] + codes
    toks = specific + ([short] if short else [])
    pat = re.compile(r"(?<![\w/-])(" + "|".join(re.escape(t) for t in toks if t) + r")(?![\w-])")
    spec_pat = re.compile(r"(?<![\w/-])(" + "|".join(re.escape(t) for t in specific if t) + r")(?![\w-])") if specific else None
    facts = []
    seen = set()
    kdir = ROOT / "knowledge"
    for path in sorted(kdir.glob("*.md")):
        if path.name == "README.md":
            continue
        lines = path.read_text(encoding="utf-8").splitlines()
        heading = ""
        i = 0
        while i < len(lines):
            ln = lines[i]
            if ln.lstrip().startswith("#"):
                heading = ln.lstrip("#").strip()
                i += 1
                continue
            if not ln.strip():
                i += 1
                continue
            # gather one block (contiguous non-blank, non-heading lines)
            block = []
            while i < len(lines) and lines[i].strip() and not lines[i].lstrip().startswith("#"):
                block.append(lines[i])
                i += 1
            text = "\n".join(block)
            if pat.search(text):
                key = (path.name, heading, text[:60])
                if key in seen:
                    continue
                seen.add(key)
                facts.append({"file": path.name, "anchor": heading,
                              "snippet": text if len(text) <= 900 else text[:900] + " …",
                              "_spec": bool(spec_pat and spec_pat.search(text))})
    # specific (path/code) matches first, then short-name-only; cap the total
    facts.sort(key=lambda f: not f["_spec"])
    for f in facts:
        f.pop("_spec", None)
    return {"service": service, "facts": facts[:cap], "n_facts": min(len(facts), cap),
            "truncated": len(facts) > cap, "tokens": toks}



# --------------------------------------------------------------------------- #
# RUNTIME view — the account's CURRENT live resource topology (each instance + its
# relationships), reusing audit.live_view.render_flow over a recent loggingaudit
# window. Served as standalone self-contained HTML so the console can open it in a
# 별도 popup (기존 live.html 처럼). A cold harvest is slow (loggingaudit pagination)
# and flaky, so generation runs in a BACKGROUND thread and the request returns
# immediately — fresh cache → topology; stale cache → that (still useful); nothing
# yet → a 수집 중 placeholder that auto-refreshes. Needs creds (no creds → error
# card). One harvest at a time (the `generating` flag).
# --------------------------------------------------------------------------- #
_RUNTIME_CACHE: dict = {"html": None, "ts": 0.0, "hours": None, "generating": False}
_RUNTIME_TTL = 60.0
_RUNTIME_LOCK = threading.Lock()


def _runtime_error_html(msg: str) -> str:
    esc = msg.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
    return ("<!doctype html><meta charset=utf-8><title>런타임 뷰</title>"
            "<body style='font-family:system-ui;padding:40px;color:#1f2328'>"
            "<h2>런타임 뷰를 생성하지 못했습니다</h2>"
            f"<p style='color:#cf222e'>{esc}</p>"
            "<p>현재 계정의 라이브 토폴로지는 <b>loggingaudit</b>에서 수집합니다 — "
            ".env에 SCP 자격증명이 필요합니다.</p></body>")


def _runtime_wait_html(hours: float) -> str:
    return ("<!doctype html><meta charset=utf-8><title>런타임 뷰 · 수집 중</title>"
            "<meta http-equiv='refresh' content='4'>"
            "<body style='font-family:system-ui;display:flex;align-items:center;"
            "justify-content:center;height:90vh;flex-direction:column;gap:14px;color:#57606a'>"
            "<div style='font-size:15px'>🌐 현재 계정 토폴로지 수집 중… <b>loggingaudit</b></div>"
            f"<div style='font-size:12px;color:#8c959f'>최근 {hours:g}시간 · 자동 새로고침</div>"
            "<div style='width:38px;height:38px;border:4px solid #d0d7de;border-top-color:#2563c9;"
            "border-radius:50%;animation:spin 1s linear infinite'></div>"
            "<style>@keyframes spin{to{transform:rotate(360deg)}}</style></body>")


def _runtime_generate(hours: float) -> None:
    """The slow part (harvest + render_flow) — runs in a bg thread, updates cache."""
    from datetime import datetime, timezone, timedelta
    try:
        from audit import live_view as lv
        now = datetime.now(timezone.utc)
        end = now.strftime("%Y-%m-%dT%H:%M:%SZ")
        start = (now - timedelta(hours=hours)).strftime("%Y-%m-%dT%H:%M:%SZ")
        ev_path = str(ROOT / "reports" / "audit" / "_runtime.jsonl")
        events: list[dict] = []
        lv.harvest(start, end, ev_path)             # loggingaudit → jsonl (needs creds)
        for line in open(ev_path, encoding="utf-8"):
            line = line.strip()
            if line:
                events.append(json.loads(line))
        spans = lv.build_spans(events, now, ours_only=True)
        html_out = lv.render_flow(spans, now, {"start": start, "end": end}, refresh=0)
    except Exception as exc:                         # noqa: BLE001
        html_out = _runtime_error_html(str(exc))
    with _RUNTIME_LOCK:
        _RUNTIME_CACHE.update(html=html_out, ts=time.monotonic(), hours=hours, generating=False)


def _runtime_view(hours: float = 6.0):
    """Return ``(html_or_None, ready)`` without blocking — kick a bg harvest when the
    cache is stale so the popup never hangs on a cold load."""
    with _RUNTIME_LOCK:
        if (_RUNTIME_CACHE["html"] and _RUNTIME_CACHE["hours"] == hours
                and (time.monotonic() - _RUNTIME_CACHE["ts"]) < _RUNTIME_TTL):
            return _RUNTIME_CACHE["html"], True
        if not _RUNTIME_CACHE["generating"]:
            _RUNTIME_CACHE["generating"] = True
            threading.Thread(target=_runtime_generate, args=(hours,), daemon=True).start()
        stale = _RUNTIME_CACHE["html"] if _RUNTIME_CACHE["hours"] == hours else None
        return stale, False


# --------------------------------------------------------------------------- #
# endpoint parameter SCHEMA (per-endpoint param defs for the API-tab coverage hint)
# --------------------------------------------------------------------------- #
def _ep_norm_path(p: str) -> str:
    """Collapse templated id segments to '*' and drop the leading slash + query —
    MUST mirror regression.scenarios.engine._norm_path so a lifecycle step's
    templated path (e.g. ``/v1/subnets/{subnet_id}``) maps to the catalog's
    ``/v1/subnets/{subnet_id}`` regardless of the concrete id used."""
    p = (p or "").split("?")[0].strip("/")
    return "/".join("*" if "{" in s else s for s in p.split("/"))


def _ep_key(method: str, path: str) -> str:
    return (method or "").upper() + " " + _ep_norm_path(path)


def _build_endpoint_params() -> dict:
    """Join data/api_catalog.json (METHOD + http_path + catalog key) with
    data/api_catalog_params.json (per-key path_params/query_params) into a map
    keyed by ``"METHOD norm(path)"``. This lets the console map an observed api
    call ``(method, templated path)`` to that endpoint's available parameters, so
    the API tab can show "what params COULD be tested" next to what was actually
    sent. Pure offline read; first-occurrence wins on a key collision."""
    cat_path = ROOT / "data" / "api_catalog.json"
    par_path = ROOT / "data" / "api_catalog_params.json"
    out: dict[str, dict] = {}
    if not cat_path.exists():
        return out
    catalog = json.loads(cat_path.read_text(encoding="utf-8"))
    params = json.loads(par_path.read_text(encoding="utf-8")) if par_path.exists() else {}
    for e in catalog:
        hp = e.get("http_path")
        if not hp:
            continue
        k = _ep_key(e.get("method"), hp)
        if k in out:
            continue  # first occurrence wins (stable, deterministic)
        pinfo = params.get(e.get("key")) or {}
        out[k] = {
            "key": e.get("key"),
            "method": (e.get("method") or "").upper(),
            "path": hp,
            "category": e.get("category"),
            "service": e.get("service"),
            "path_params": pinfo.get("path_params") or [],
            "query_params": pinfo.get("query_params") or [],
        }
    return out


def _endpoint_params() -> dict:
    global _EP_PARAMS
    if _EP_PARAMS is None:
        _EP_PARAMS = _build_endpoint_params()
    return _EP_PARAMS


def _lookup_endpoint_params(method: str, path: str) -> dict | None:
    """Map an api call's ``(method, templated path)`` to its endpoint parameter
    schema, or None when the endpoint isn't in the catalog."""
    return _endpoint_params().get(_ep_key(method, path))


# --------------------------------------------------------------------------- #
# selection -> lifecycle leaf set -> dag_planner plan
# --------------------------------------------------------------------------- #
def _resolve_lifecycle_ids(sel: dict) -> list[str]:
    """A selection can name node_ids, services, categories, and/or lifecycle_ids.
    Resolve all of them to the union of source lifecycle ids (deduped, sorted)."""
    m = _model()
    nodes, lcs = m["nodes"], m["lifecycles"]
    want = set(sel.get("lifecycle_ids") or [])
    node_ids = set(sel.get("node_ids") or [])
    svcs = set(sel.get("services") or [])
    cats = set(sel.get("categories") or [])
    for nid, n in nodes.items():
        if not n.get("lifecycle"):
            continue
        if nid in node_ids or n["service"] in svcs or n["category"] in cats:
            want.add(n["lifecycle"])
    return sorted(lid for lid in want if lid in lcs)


def _graph_targets(sel: dict) -> list[str]:
    """A selection (node_ids / services / categories) -> the set of resource-node
    ids to feed ``composer.graph_view`` as targets. A selected service contributes
    all of its ``lifecycle != null`` resource nodes; explicit node_ids are taken as
    given (still filtered to ones that actually have a lifecycle). Only ids that are
    real model nodes survive (graph_view raises on an unknown target)."""
    m = _model()
    nodes = m["nodes"]
    node_ids = set(sel.get("node_ids") or [])
    svcs = set(sel.get("services") or [])
    cats = set(sel.get("categories") or [])
    out: set[str] = set()
    for nid, n in nodes.items():
        if not n.get("lifecycle"):
            continue  # lookup / dep-only resources are never standalone targets
        if nid in node_ids or n["service"] in svcs or n["category"] in cats:
            out.add(nid)
    # an explicit node id that has a lifecycle but wasn't matched above (defensive)
    for nid in node_ids:
        if nid in nodes and nodes[nid].get("lifecycle"):
            out.add(nid)
    return sorted(out)


def _graph(sel: dict) -> dict:
    """Composition-DAG projection of a selection, via composer.graph_view — the SAME
    closure/branch/dedup the real composer uses. Resolve the selection to target
    resource nodes, then return graph_view's dict as-is:
        {nodes:[{id,service,provenance,quota,heavy,options,level,is_target,shared}],
         edges:[{from,to}], levels, shared, peak_quota, order, teardown}
    An empty selection returns an empty graph (no error) so the UI can render
    "nothing selected" without special-casing the HTTP status."""
    targets = _graph_targets(sel)
    if not targets:
        return {"nodes": [], "edges": [], "levels": [0], "shared": [],
                "peak_quota": {}, "order": [], "teardown": []}
    from regression.scenarios import composer
    return composer.graph_view(targets, sel.get("choices") or None)


def _plan(lifecycle_ids: list[str]) -> dict:
    """The REAL offline schedule for a leaf set, via regression.scenarios.dag_planner."""
    from regression.scenarios import dag_planner, validate_dag
    deps = validate_dag._load_deps()
    all_lcs = validate_dag._load_lifecycles()
    enabled_ids = {lc["id"] for lc in all_lcs if lc.get("enabled")}
    runnable = [lid for lid in lifecycle_ids if lid in enabled_ids]
    # leaf set = the runnable subset of the SELECTION. Fall back to None (= all
    # enabled, the dag_planner CLI default) ONLY when no selection was given —
    # NOT when a non-empty selection happens to be all-disabled (that must yield
    # an empty plan, never "plan the whole platform").
    leaf_set = runnable if lifecycle_ids else None
    p = dag_planner.plan(leaf_set=leaf_set, deps=deps, lifecycles=all_lcs)
    m = _model()
    # per-lifecycle step preview (which APIs each leaf will exercise)
    preview = {}
    for lid in p.leaf_set:
        lc = m["lifecycles"].get(lid, {})
        preview[lid] = {"service": lc.get("service", ""), "heavy": lc.get("heavy", False),
                        "n_steps": lc.get("n_steps", 0), "steps": lc.get("steps", [])}
    return {
        "requested": lifecycle_ids,
        "runnable": runnable,
        "skipped_disabled": sorted(set(lifecycle_ids) - enabled_ids),
        "plan": p.to_dict(),
        "summary": dag_planner.format_plan(p),
        "preview": preview,
        # peak concurrent VPCs = the persistent shared VPC + the largest self-create
        # wave (self-create slots are concurrent only within their wave; provision/
        # free/adopt waves hold no self-created VPC). Avoids double-counting the
        # shared VPC, whose slot the provision wave already carries.
        "peak_vpcs": p.shared_vpc_count + max(
            [w.get("vpc_slots", 0) for w in p.to_dict()["waves"]
             if w.get("kind") == "self-create"] + [0]),
    }


# --------------------------------------------------------------------------- #
# live event stream (JSONL the engine appends to via core.console_events)
# --------------------------------------------------------------------------- #
def _read_events(path: str) -> list[dict]:
    out = []
    try:
        with open(path, encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                try:
                    out.append(json.loads(line))
                except Exception:
                    pass  # skip a torn line (rare interleave under heavy -n)
    except FileNotFoundError:
        pass
    return out


def _emit_event(evpath: str, evkind: str, **fields) -> None:
    """Append one event line (server-side, used by the simulate worker). Same shape
    as core.console_events so the live view is identical for simulate vs real runs.
    Params are underscore-free but distinct from event field names (``path``/``kind``
    ARE valid fields, e.g. a step's ``path``) so a field never collides with a param."""
    rec = {"ts": round(time.time(), 3), "kind": evkind}
    rec.update(fields)
    with open(evpath, "a", encoding="utf-8") as fh:
        fh.write(json.dumps(rec, ensure_ascii=False, default=str) + "\n")


# --------------------------------------------------------------------------- #
# run records + workers
# --------------------------------------------------------------------------- #
def _new_rec(kind: str, **extra) -> dict:
    rid = time.strftime("%Y%m%d-%H%M%S") + "-" + uuid.uuid4().hex[:4]
    RUN_DIR.mkdir(parents=True, exist_ok=True)
    rec = {"id": rid, "kind": kind, "mode": extra.get("mode", kind), "status": "running",
           "lifecycle_ids": extra.get("lifecycle_ids", []),
           "heavy": extra.get("heavy", False), "mutations": extra.get("mutations", False),
           "destructive": extra.get("destructive", False),
           "started": time.time(), "ended": None, "rc": None,
           "log": str(RUN_DIR / f"{rid}.log"),
           "events": str(RUN_DIR / f"{rid}.events.jsonl")}
    Path(rec["events"]).write_text("", encoding="utf-8")
    with _LOCK:
        _RUNS[rid] = rec
    return rec


def _start(kind: str, worker, **extra) -> dict:
    rec = _new_rec(kind, **extra)
    threading.Thread(target=worker, args=(rec,), daemon=True).start()
    return rec


# --------------------------------------------------------------------------- #
# cross-run VPC admission + wait queue
# --------------------------------------------------------------------------- #
# Several runs can be in flight at once (one user, multiple runs). Each run needs
# up to ``peak_vpcs`` VPC slots (dag_planner); the account caps total VPCs
# (core.budgets, =5 VALIDATED). A run is ADMITTED when it fits under the cap and
# otherwise QUEUED (FIFO, head-of-line), admitted later when an in-flight run
# finishes and frees slots. ``_BASELINE`` = account VPCs NOT from our in-flight
# runs (resynced from a live LIST whenever nothing is in flight), so
# effective_used = baseline + Σ(reserved peaks), headroom = cap − that.
# Reservations gate BOTH simulate and live runs (simulate reserves the same slots
# so the queue can be exercised with zero billing).
_ADMIT = threading.Lock()
_RESERVED: dict[str, int] = {}     # run_id -> reserved VPC slots (in flight)
_QUEUE: list[str] = []             # run_ids waiting, FIFO
_PENDING: dict[str, object] = {}   # run_id -> worker fn (for queued runs)
_BASELINE = 0                      # account VPCs not attributable to our runs
_VPCCNT = {"n": 0, "ts": 0.0}      # cached live account VPC count


def _vpc_cap() -> int:
    from core import budgets
    return int(budgets.Budget().limits.get("vpc", 5))


def _account_vpc_count(ttl: float = 12.0) -> int:
    """Live account VPC count via a read-only LIST (cached; best-effort)."""
    now = time.time()
    if now - _VPCCNT["ts"] < ttl:
        return _VPCCNT["n"]
    try:
        from core.config import Settings
        from core.http_client import ApiClient
        r = ApiClient(Settings()).get("/v1/vpcs", params={"size": 1}, service="vpc",
                                      timeout=10, retry=False)
        if getattr(r, "ok", False):
            body = r.body if isinstance(r.body, dict) else {}
            n = body.get("totalCount")
            if n is None:
                items = body.get("contents") or body.get("vpcs") or []
                n = len(items) if isinstance(items, list) else 0
            _VPCCNT.update(n=int(n), ts=now)
    except Exception:  # noqa: BLE001 — budget view is best-effort, never crash a run
        pass
    return _VPCCNT["n"]


def _selection_is_heavy(ids: list[str]) -> bool:
    """True iff any selected lifecycle is heavy/billable (auto-derives the gate)."""
    lcs = _model().get("lifecycles", {})
    return any(lcs.get(i, {}).get("heavy") for i in ids)


def _run_peak_vpcs(ids: list[str]) -> int:
    try:
        return int(_plan(ids).get("peak_vpcs", 0) or 0)
    except Exception:  # noqa: BLE001
        return 0


def _capacity_view() -> dict:
    global _BASELINE
    cap = _vpc_cap()
    acct = _account_vpc_count()
    with _ADMIT:
        if not _RESERVED:
            _BASELINE = acct
        base, reserved = _BASELINE, sum(_RESERVED.values())
        running, queued = list(_RESERVED.keys()), list(_QUEUE)

    def _v(rid: str) -> dict:
        r = _RUNS.get(rid)
        return _rec_view(r) if r else {"id": rid}
    return {"cap": cap, "baseline": base, "reserved": reserved, "account_live": acct,
            "headroom": max(0, cap - base - reserved),
            "running": [_v(r) for r in running], "queued": [_v(r) for r in queued]}


def _spawn_run(rec: dict, worker) -> None:
    def _run():
        try:
            worker(rec)
        finally:
            _on_run_finish(rec["id"])
    threading.Thread(target=_run, daemon=True).start()


def _admit_or_queue(rec: dict, worker) -> dict:
    """Reserve VPC slots and start the run if it fits the cap, else enqueue it."""
    global _BASELINE
    peak = int(rec.get("peak_vpcs", 0) or 0)
    base_now = _account_vpc_count()
    spawn = False
    with _ADMIT:
        if not _RESERVED:
            _BASELINE = base_now
        head = max(0, _vpc_cap() - _BASELINE - sum(_RESERVED.values()))
        if head >= peak:
            _RESERVED[rec["id"]] = peak
            rec["status"], rec["queued"] = "running", False
            spawn = True
        else:
            rec["status"], rec["queued"] = "queued", True
            _PENDING[rec["id"]] = worker
            _QUEUE.append(rec["id"])
    if spawn:
        _spawn_run(rec, worker)
    return rec


def _try_admit_queue() -> None:
    """Admit queued runs (FIFO, head-of-line) that now fit under the cap."""
    global _BASELINE
    base_now = _account_vpc_count()
    ready = []
    with _ADMIT:
        if not _RESERVED:
            _BASELINE = base_now
        while _QUEUE:
            rid = _QUEUE[0]
            rec = _RUNS.get(rid)
            if not rec:
                _QUEUE.pop(0)
                _PENDING.pop(rid, None)
                continue
            peak = int(rec.get("peak_vpcs", 0) or 0)
            head = max(0, _vpc_cap() - _BASELINE - sum(_RESERVED.values()))
            if head >= peak:
                _QUEUE.pop(0)
                worker = _PENDING.pop(rid)
                _RESERVED[rid] = peak
                rec["status"], rec["queued"] = "running", False
                ready.append((rec, worker))
            else:
                break  # head-of-line: don't skip ahead of a waiting bigger run
    for rec, worker in ready:
        _spawn_run(rec, worker)


def _on_run_finish(rid: str) -> None:
    with _ADMIT:
        _RESERVED.pop(rid, None)
    _try_admit_queue()


def _provision_shared(env: dict, f) -> dict:
    """Provision ONE session-shared VPC+subnet so adopter lifecycles don't skip under
    -n (identical to console_server / chat-heavy). Best-effort: on failure adopters
    self-skip and self-creators still run."""
    f.write("\n=== provision shared VPC (adopters need this under -n) ===\n")
    f.flush()
    out = subprocess.run([sys.executable, "-m", "regression.scenarios.shared_infra", "--provision"],
                         cwd=str(ROOT), env=env, stdout=subprocess.PIPE,
                         stderr=subprocess.STDOUT, text=True)
    f.write(out.stdout or "")
    shared = {}
    for line in (out.stdout or "").splitlines():
        if line.startswith("SCP_SHARED_") and "=" in line:
            k, _, v = line.partition("=")
            if v.strip():
                shared[k.strip()] = v.strip()
    if shared.get("SCP_SHARED_VPC_ID"):
        shared["SCP_VPC_SHARED_RESERVED"] = "1"
        f.write(f"\n[provision] shared VPC ready: {shared['SCP_SHARED_VPC_ID']}\n")
    else:
        f.write("\n[provision] no shared VPC id — adopters will skip (self-creators still run)\n")
    f.flush()
    return shared


def _teardown_shared(env: dict, shared: dict, f) -> None:
    if not shared.get("SCP_SHARED_VPC_ID"):
        return
    f.write("\n=== teardown shared VPC (precise, by id) ===\n")
    f.flush()
    subprocess.run([sys.executable, "-m", "regression.scenarios.shared_infra", "--teardown"],
                   cwd=str(ROOT), env={**env, **shared}, stdout=f, stderr=subprocess.STDOUT)


def _pytest_did_not_run(rc: int, pytest_out: str) -> bool:
    """True when the pytest *runner itself* never executed (so there are no test
    results to trust) — most commonly because pytest isn't installed in the
    interpreter that launched the run. ``python -m pytest`` with no pytest prints
    ``No module named pytest`` and exits rc=1; we also treat the conventional
    "usage error / internal error" exit codes (4 = usage, 3 = internal) as
    didn't-run when no test outcome line is present."""
    low = (pytest_out or "").lower()
    if "no module named pytest" in low or "no module named 'pytest'" in low:
        return True
    # rc 4 (usage) / 3 (internal) with no per-test outcome summary => runner bailed
    has_outcome = bool(re.search(r"\d+\s+(passed|failed|skipped|error|xfailed|deselected)",
                                 pytest_out or ""))
    return rc in (3, 4) and not has_outcome


def _run_worker(rec: dict) -> None:
    """REAL run: provision shared VPC (heavy) -> pytest tests/crud with SCP_CRUD_IDS +
    the per-run safety gates + SCP_CONSOLE_EVENTS -> teardown shared.

    Per-run cleanup is **teardown-scoped** (Hard Rule 3 + the run-owner rule): the
    lifecycle's OWN teardown already deletes exactly what this run created, and the
    account-wide ``cleanup.reconciler`` does NOT support scoping a sweep to a single
    run (it only filters by the static ``owner=apitest`` tag, reaping unrelated OLD
    leftovers from other runs). So we do NOT auto-run the reconciler here — the
    precise shared-VPC teardown handles the heavy session resource, and account-wide
    reaping stays the explicit 강제 클린업 button (POST /api/cleanup). The engine
    appends step-level events to rec['events'] (core.console_events)."""
    logp = Path(rec["log"])
    env = {**os.environ, "PYTHONPATH": str(ROOT),
           "SCP_CRUD_IDS": ",".join(rec["lifecycle_ids"]),
           "SCP_CONSOLE_EVENTS": rec["events"],
           "SCP_ALLOW_MUTATIONS": "true" if rec["mutations"] else "false",
           "SCP_ALLOW_DESTRUCTIVE": "true" if rec["destructive"] else "false",
           "SCP_RUN_HEAVY": "true" if rec["heavy"] else "false"}
    n = str(max(1, min(6, len(rec["lifecycle_ids"]) or 2)))
    try:
        with open(logp, "w", encoding="utf-8") as f:
            f.write(f"# console2 run {rec['id']}  lifecycle_ids={rec['lifecycle_ids']}\n"
                    f"# gates: mutations={rec['mutations']} destructive={rec['destructive']} "
                    f"heavy={rec['heavy']}  parallel={n}\n")
            f.flush()
            shared = _provision_shared(env, f) if rec["heavy"] else {}
            f.write("\n=== pytest ===\n")
            f.flush()
            pos = f.tell()      # remember where the pytest output begins
            rc = subprocess.run(
                [sys.executable, "-m", "pytest", "tests/crud", "-m", "crud",
                 "-n", n, "-o", "addopts=", "-q"],
                cwd=str(ROOT), env={**env, **shared}, stdout=f, stderr=subprocess.STDOUT).returncode
            f.flush()
            # Read back just the pytest output to detect "the runner never ran"
            # (e.g. pytest not installed in this venv). When it didn't run there are
            # no results to trust AND nothing was created — so skip cleanup entirely.
            try:
                with open(logp, encoding="utf-8") as rf:
                    rf.seek(pos)
                    pytest_out = rf.read()
            except Exception:  # noqa: BLE001
                pytest_out = ""
            runner_missing = _pytest_did_not_run(rc, pytest_out)
            if runner_missing:
                f.write("\n⚠ 테스트 러너 없음 — pytest 가 실행되지 않았습니다 "
                        "(pip install -r requirements.txt; venv 활성화).\n"
                        "  생성된 자원이 없으므로 teardown/sweep 을 건너뜁니다.\n")
                f.flush()
            else:
                _teardown_shared(env, shared, f)
                # Per-run cleanup is teardown-scoped: the lifecycle teardown above
                # already deleted what THIS run created. We deliberately do NOT run
                # the account-wide reconciler sweep here (it can't be scoped to one
                # run and would reap unrelated OLD leftovers, flooding this run's
                # log). Account-wide reaping = the manual 강제 클린업 button
                # (POST /api/cleanup).
                f.write("\n=== per-run cleanup: teardown-scoped ===\n"
                        "  이 실행이 만든 자원은 라이프사이클 teardown 으로 이미 삭제됨.\n"
                        "  계정 전체 reconciler 청소는 자동 실행하지 않음 — '강제 클린업'(POST "
                        "/api/cleanup) 버튼으로 수동 실행하세요 (run-scoped 청소를 reconciler 가 "
                        "지원하지 않기 때문).\n")
                f.flush()
        with _LOCK:
            rec["status"], rec["rc"], rec["ended"] = "done", rc, time.time()
            rec["runner_missing"] = runner_missing
    except Exception as exc:  # noqa: BLE001 — surface to the UI, never crash the server
        with _LOCK:
            rec["status"], rec["error"], rec["ended"] = "error", str(exc), time.time()


def _sim_resource_type(path: str) -> str:
    """Coarse resource type from a step path, e.g. ``/v1/queues/{queue_id}`` -> ``queues``.
    First non-version, non-template segment. Synthetic (simulate-only) labelling."""
    for seg in (path or "").strip("/").split("/"):
        if not seg or seg.startswith("{") or (seg.startswith("v") and seg[1:].isdigit()):
            continue
        return seg
    return "resource"


def _resource_kind_from_path(path: str) -> str | None:
    """Resource KIND (singular) from a create/delete path: the collection segment
    right after the version, singularized — ``/v1/vpcs/{id}`` -> ``vpc``,
    ``/v1/subnets/{subnet_id}`` -> ``subnet``, ``/v1/nat-gateways`` -> ``nat-gateway``.
    Returns None when no collection segment exists (caller falls back to the service
    name). This is the canonical derivation; console2.js ``kindFromPath`` mirrors it
    so the 자원 tab shows the resource kind, not the service name."""
    coll = None
    for seg in (path or "").split("?")[0].strip("/").split("/"):
        if not seg or re.match(r"^v\d", seg):   # skip version (v1, v2, v1.1, v2025-…)
            continue
        coll = seg
        break
    if not coll:
        return None
    # singularize: strip a trailing 's' (but not 'ss'); leave singular names alone.
    if len(coll) > 1 and coll.endswith("s") and not coll.endswith("ss"):
        coll = coll[:-1]
    return coll


def _simulate_worker(rec: dict) -> None:
    """DRY-RUN: replay the plan to the event stream (no cloud, deterministic) via the
    SHARED ``regression.scenarios.local_run.simulate_run`` — the SAME replay the
    control-plane ``local`` executor uses (convergence S2). Walks the dag_planner
    waves in DAG order + each lifecycle's HTTP steps so the live view shows the real
    creation order + API sequence; synthetic ``resource-tracked``/``-deleted`` (ids
    ``sim-…``) on create/delete render the resource view without any cloud."""
    from regression.scenarios import local_run
    evp, logp = rec["events"], Path(rec["log"])
    try:
        plan = _plan(rec["lifecycle_ids"])
        with open(logp, "w", encoding="utf-8") as f:
            f.write(f"# console2 SIMULATE {rec['id']} — replay of the dag_planner plan "
                    f"(no cloud calls)\n{plan['summary']}\n")
        local_run.simulate_run(
            plan["plan"]["waves"], plan["preview"],
            lambda kind, **fields: _emit_event(evp, kind, **fields),
            step_delay=_SIM_STEP_DELAY, beat=_SIM_BEAT, sleep=time.sleep,
            new_id=lambda: "sim-" + uuid.uuid4().hex[:8],
            meta={"runnable": plan["runnable"]})
        with _LOCK:
            rec["status"], rec["rc"], rec["ended"] = "done", 0, time.time()
    except Exception as exc:  # noqa: BLE001
        try:
            _emit_event(evp, "run-end", status="error", error=str(exc))
        except Exception:
            pass
        with _LOCK:
            rec["status"], rec["error"], rec["ended"] = "error", str(exc), time.time()


def _cleanup_worker(rec: dict) -> None:
    logp = Path(rec["log"])
    env = {**os.environ, "PYTHONPATH": str(ROOT), "SCP_ALLOW_MUTATIONS": "true",
           "SCP_ALLOW_DESTRUCTIVE": "true", "SCP_SWEEP_IGNORE_TTL": "true",
           "SCP_SWEEP_NOWAIT": "true"}
    try:
        with open(logp, "w", encoding="utf-8") as f:
            f.write(f"# console2 FORCE cleanup {rec['id']}\n\n"
                    "=== reconciler sweep (FORCE: delete ALL owned, ignore TTL) ===\n")
            f.flush()
            rc = subprocess.run([sys.executable, "-m", "cleanup.reconciler"], cwd=str(ROOT),
                                env=env, stdout=f, stderr=subprocess.STDOUT).returncode
        with _LOCK:
            rec["status"], rec["rc"], rec["ended"] = "done", rc, time.time()
    except Exception as exc:  # noqa: BLE001
        with _LOCK:
            rec["status"], rec["error"], rec["ended"] = "error", str(exc), time.time()


def _verify_worker(rec: dict) -> None:
    logp = Path(rec["log"])
    env = {**os.environ, "PYTHONPATH": str(ROOT), "SCP_ALLOW_DESTRUCTIVE": "false"}
    try:
        with open(logp, "w", encoding="utf-8") as f:
            f.write(f"# console2 cleanup VERIFY {rec['id']} (read-only owned inventory)\n\n"
                    "=== verify_clean (no deletes; counts surviving owned resources) ===\n")
            f.flush()
            rc = subprocess.run([sys.executable, "-m", "cleanup.verify_clean"], cwd=str(ROOT),
                                env=env, stdout=f, stderr=subprocess.STDOUT).returncode
        with _LOCK:
            rec["status"], rec["rc"], rec["ended"] = "done", rc, time.time()
    except Exception as exc:  # noqa: BLE001
        with _LOCK:
            rec["status"], rec["error"], rec["ended"] = "error", str(exc), time.time()


def _owned_worker(rec: dict) -> None:
    """Read-only owned-resource inventory via cleanup.verify_clean.scan_owned — runs
    in-process (no subprocess) and stores the structured list on the record so the
    run-screen pre-flight panel can show service · path · count. Makes only LIST
    calls (no mutations); never deletes. On any error the record carries it but the
    server stays up."""
    logp = Path(rec["log"])
    try:
        # read-only-ness is GUARANTEED by scan_owned stubbing the reconciler's
        # _delete/_wait_gone (so no DELETE can fire); this default is just a hint.
        os.environ.setdefault("SCP_ALLOW_DESTRUCTIVE", "false")
        from cleanup.verify_clean import scan_owned
        owned = scan_owned()
        from collections import Counter
        by_svc = Counter(o["service"] for o in owned)
        with open(logp, "w", encoding="utf-8") as f:
            f.write(f"# console2 owned-resource scan {rec['id']} (read-only LIST inventory)\n\n")
            if not owned:
                f.write("NONE — every swept collection is empty of owned resources ✅\n")
            for svc, n in by_svc.most_common():
                f.write(f"  {svc:18} {n:3}\n")
            f.write(f"\nTOTAL owned survivors across all collections: {len(owned)}\n")
        with _LOCK:
            rec["owned"] = owned
            rec["owned_total"] = len(owned)
            rec["status"], rec["rc"], rec["ended"] = "done", 0, time.time()
    except Exception as exc:  # noqa: BLE001 — surface to the UI, never crash the server
        try:
            with open(logp, "a", encoding="utf-8") as f:
                f.write(f"\nERROR: {exc}\n")
        except Exception:
            pass
        with _LOCK:
            rec["status"], rec["error"], rec["ended"] = "error", str(exc), time.time()


def _summarize(rec: dict, log: str) -> str:
    kind = rec.get("kind")
    # A live run whose pytest runner never ran (e.g. pytest not installed) gets a
    # clear, actionable summary instead of a bare "0 passed" — surfaced from the
    # flag _run_worker set when it detected the runner was missing.
    if rec.get("runner_missing"):
        return "⚠ 테스트 러너 없음 — pip install -r requirements.txt (venv)"
    if kind == "owned":
        if rec.get("status") == "error":
            return f"⚠️ 스캔 실패: {str(rec.get('error'))[:60]}"
        n = rec.get("owned_total")
        if n is None:
            return ""
        return "없음 ✅ — 남은 자원 0건" if n == 0 else f"⚠️ 남은 자원 {n}건"
    if kind == "simulate":
        evs = _read_events(rec["events"])
        nl = sum(1 for e in evs if e.get("kind") == "lifecycle-end")
        ns = sum(1 for e in evs if e.get("kind") == "step-end")
        return f"▶ simulated {nl} lifecycle(s) · {ns} API step(s)"
    if kind == "verify":
        if "NONE — every swept collection is empty" in log:
            return "✅ clean — owned survivors: 0"
        m = re.search(r"TOTAL owned survivors across all collections:\s*(\d+)", log)
        if m:
            return "✅ clean — owned survivors: 0" if m.group(1) == "0" else f"⚠️ {m.group(1)} owned survivors"
        return ""
    if kind == "cleanup":
        m = re.findall(r"sweep done:\s*(\d+) resource\(s\) deleted", log)
        return f"🧹 {sum(int(x) for x in m)} resource(s) deleted" if m else ""
    m = re.findall(r"\d+ (?:passed|failed|skipped|error)[^\n]*", log)  # pytest summary
    return m[-1] if m else ""


def _rec_view(rec: dict, full: bool = False) -> dict:
    v = {k: rec.get(k) for k in ("id", "kind", "mode", "status", "lifecycle_ids",
                                 "heavy", "mutations", "destructive", "rc", "started",
                                 "ended", "error", "runner_missing", "peak_vpcs", "queued")}
    if rec.get("kind") == "owned":   # expose the structured owned-resource inventory
        v["owned"] = rec.get("owned", [])
        v["owned_total"] = rec.get("owned_total")
    log = ""
    if Path(rec["log"]).exists():
        try:
            log = open(rec["log"], encoding="utf-8").read()
        except Exception:
            log = ""
    v["summary"] = _summarize(rec, log)
    if full:
        v["log"] = "".join(log.splitlines(keepends=True)[-250:])
    return v


# --------------------------------------------------------------------------- #
# suites (named run shapes) — reuse core.suites (suites/*.yaml, CI-shared)
# --------------------------------------------------------------------------- #
# A suite = a named (scope × safety-gates) preset. The canonical store is
# suites/*.yaml driven by core.suites — the SAME data the CI run-request reads,
# so a suite saved here is runnable by the workflow with no new concept. The
# `request:` block holds exactly the run-request options (gates + a single
# category/service/crud_filter). console2's finer, multi-service / resource-level
# selection is preserved in an OPTIONAL top-level `scope:` block that core.suites
# ignores (it validates only `request`), so the file stays CI-valid while still
# round-tripping faithfully in the console.

_BUILTIN_SUITES = ("smoke", "full", "full-heavy", "conformance")
_SUITE_ID_RE = re.compile(r"^[a-z0-9][a-z0-9._-]{0,48}$")


def _suite_view(data: dict, *, builtin: bool) -> dict:
    """A core.suites record -> console2 shape (gates + scope split out)."""
    from core import suites as _s
    req = dict(data.get("request") or {})
    gates = {k: bool(req.get(k, False)) for k in _s.BOOL_KEYS}
    scope = dict(data.get("scope") or {})          # console2 extension (CI-ignored)
    for k in _s.STR_KEYS:                           # surface the CI-coarse filter too
        if req.get(k) and k not in scope:
            scope[k] = req[k]
    return {"id": data.get("id"), "label": data.get("label", ""),
            "request": req, "gates": gates, "scope": scope, "builtin": builtin}


def _list_suites_view() -> list[dict]:
    from core import suites as _s
    return [_suite_view(s, builtin=(s.get("id") in _BUILTIN_SUITES))
            for s in _s.list_suites()]


def _save_suite(body: dict) -> dict:
    """Validate + write suites/<id>.yaml; return the saved suite view.

    Raises ValueError(msg) on any rejection (bad id, gate inconsistency, builtin
    overwrite without force) so the route can map it to a 400.
    """
    import yaml
    from core import suites as _s

    sid = str(body.get("id") or "").strip().lower()
    if not _SUITE_ID_RE.match(sid):
        raise ValueError("id must be a slug: a-z 0-9 . _ - (1–49 chars), no spaces/paths")
    if sid in _BUILTIN_SUITES and not body.get("force"):
        raise ValueError(f"{sid!r} is a built-in suite — choose another id (or pass force=true)")
    label = str(body.get("label") or sid).strip()
    req_in = dict(body.get("request") or {})
    request: dict = {}
    for k in _s.BOOL_KEYS:                           # gates -> bool
        if k in req_in:
            request[k] = bool(req_in[k])
    for k in _s.STR_KEYS:                            # coarse filter -> str
        if req_in.get(k):
            request[k] = str(req_in[k])
    data: dict = {"id": sid, "label": label, "request": request}
    scope = body.get("scope")
    if isinstance(scope, dict):                      # console2 fidelity (CI ignores it)
        kept = {k: scope[k] for k in ("category", "service", "crud_filter",
                                      "categories", "services", "node_ids")
                if scope.get(k)}
        if kept:
            data["scope"] = kept
    path = _s.suite_path(sid)
    errs = _s.validate_suite(data, path)
    if errs:
        raise ValueError("; ".join(errs))
    header = ("# console2-authored suite (saved selection). `request:` is the CI\n"
              "# run-request block; the optional `scope:` preserves console2's exact\n"
              "# selection and is ignored by core.suites / the workflow.\n")
    path.write_text(header + yaml.safe_dump(data, sort_keys=False, allow_unicode=True))
    return _suite_view(data, builtin=False)


# --------------------------------------------------------------------------- #
# HTTP
# --------------------------------------------------------------------------- #
class Handler(BaseHTTPRequestHandler):
    def log_message(self, *a):  # quiet
        pass

    def _json(self, code: int, obj: dict) -> None:
        body = json.dumps(obj, ensure_ascii=False, default=str).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _body(self) -> dict:
        try:
            n = int(self.headers.get("Content-Length") or 0)
            return json.loads(self.rfile.read(n) or b"{}")
        except Exception:
            return {}

    def do_GET(self):
        p = urlparse(self.path).path
        if p in ("/", "/index.html", "/console2.html"):
            return self._file(WEB / "index.html")
        if p == "/api/model":
            try:
                # ship the per-endpoint parameter SCHEMA alongside the model so the
                # API tab can map an observed call (method, path) -> its available
                # params (coverage hint) with no extra round-trip. Keyed by
                # "METHOD norm(path)"; lazily built + cached.
                m = dict(_model())
                m["endpoint_params"] = _endpoint_params()
                return self._json(200, m)
            except Exception as exc:  # noqa: BLE001
                return self._json(500, {"error": f"model build failed: {exc}"})
        if p == "/api/lifecycles":
            # read-only DEFINITION: /api/lifecycles?service=cat/svc -> resources
            # (endpoints/options/deps) + runnable lifecycles (steps).
            svc = (parse_qs(urlparse(self.path).query).get("service") or [""])[0]
            if not svc:
                return self._json(400, {"error": "service query param required (cat/svc)"})
            try:
                return self._json(200, _lifecycles_view(svc))
            except Exception as exc:  # noqa: BLE001
                return self._json(500, {"error": f"lifecycles view failed: {exc}"})
        if p == "/api/knowledge":
            # read-only FACTS: /api/knowledge?service=cat/svc -> knowledge/*.md
            # paragraphs mentioning the service (filtered view, source = the .md).
            svc = (parse_qs(urlparse(self.path).query).get("service") or [""])[0]
            if not svc:
                return self._json(400, {"error": "service query param required (cat/svc)"})
            try:
                return self._json(200, _knowledge_view(svc))
            except Exception as exc:  # noqa: BLE001
                return self._json(500, {"error": f"knowledge view failed: {exc}"})
        if p == "/runtime" or p == "/api/runtime":
            # RUNTIME view (HTML, for a 별도 popup): current account live-resource
            # topology + relationships, from loggingaudit via audit.live_view. Non-
            # blocking — a cold load returns the 수집 중 placeholder (auto-refresh).
            q = parse_qs(urlparse(self.path).query)
            try:
                hours = float((q.get("hours") or ["6"])[0] or 6)
            except ValueError:
                hours = 6.0
            out, _ready = _runtime_view(hours)
            if not out:
                out = _runtime_wait_html(hours)
            body = out.encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-cache")
            self.end_headers()
            self.wfile.write(body)
            return
        if p == "/api/endpoint-params":
            # on-demand single lookup: /api/endpoint-params?method=GET&path=/v1/vpcs
            q = parse_qs(urlparse(self.path).query)
            method = (q.get("method") or [""])[0]
            path = (q.get("path") or [""])[0]
            if not path:
                return self._json(400, {"error": "path query param required"})
            try:
                hit = _lookup_endpoint_params(method, path)
            except Exception as exc:  # noqa: BLE001
                return self._json(500, {"error": f"endpoint-params failed: {exc}"})
            if not hit:
                return self._json(404, {"error": "endpoint not in catalog",
                                        "method": (method or "").upper(),
                                        "path": path})
            return self._json(200, hit)
        if p == "/api/suites":
            try:
                return self._json(200, {"suites": _list_suites_view()})
            except Exception as exc:  # noqa: BLE001
                return self._json(500, {"error": f"suites list failed: {exc}"})
        if p == "/api/capacity":
            # VPC budget + admission state: cap, baseline, reserved, headroom,
            # running[], queued[] — drives the 실행 screen capacity bar.
            try:
                return self._json(200, _capacity_view())
            except Exception as exc:  # noqa: BLE001
                return self._json(500, {"error": f"capacity failed: {exc}"})
        if p == "/api/runs":
            with _LOCK:
                rows = [_rec_view(r) for r in sorted(_RUNS.values(),
                                                     key=lambda x: x["started"], reverse=True)]
            return self._json(200, {"runs": rows})
        if p.startswith("/api/runs/") and p.endswith("/events"):
            rid = p[len("/api/runs/"):-len("/events")]
            with _LOCK:
                rec = _RUNS.get(rid)
            if not rec:
                return self._json(404, {"error": "no such run"})
            return self._json(200, {"id": rid, "status": rec["status"],
                                    "events": _read_events(rec["events"])})
        if p.startswith("/api/runs/"):
            rid = p.rsplit("/", 1)[-1]
            with _LOCK:
                rec = _RUNS.get(rid)
            if not rec:
                return self._json(404, {"error": "no such run"})
            return self._json(200, _rec_view(rec, full=True))
        # static bundle (assets/, etc.) with path-escape guard
        target = (WEB / p.lstrip("/")).resolve()
        if str(target).startswith(str(WEB.resolve())) and target.is_file():
            return self._file(target)
        self._json(404, {"error": "not found"})

    def do_POST(self):
        p = urlparse(self.path).path
        if p == "/api/graph":
            try:
                return self._json(200, _graph(self._body()))
            except Exception as exc:  # noqa: BLE001
                return self._json(500, {"error": f"graph failed: {exc}"})
        if p == "/api/plan":
            sel = self._body()
            ids = sel.get("lifecycle_ids") if "lifecycle_ids" in sel and not (
                sel.get("node_ids") or sel.get("services") or sel.get("categories")) \
                else _resolve_lifecycle_ids(sel)
            try:
                return self._json(200, {"lifecycle_ids": ids, **_plan(ids)})
            except Exception as exc:  # noqa: BLE001
                return self._json(500, {"error": f"plan failed: {exc}"})
        if p == "/api/run":
            b = self._body()
            ids = [str(x).strip() for x in (b.get("lifecycle_ids") or []) if str(x).strip()]
            if not ids:
                # allow a selection instead of explicit lifecycle_ids
                ids = _resolve_lifecycle_ids(b)
            if not ids:
                return self._json(400, {"error": "no lifecycles selected"})
            mode = b.get("mode", "live")
            heavy = _selection_is_heavy(ids)
            peak = _run_peak_vpcs(ids)
            if mode == "live":
                # Gates are DERIVED from the selection (no UI axis): CRUD lifecycles
                # need mutations+destructive; heavy auto-enables iff the selected
                # closure contains a heavy (billable) lifecycle. The deliberate
                # opt-in (Hard Rule 1) is the selection itself + the client's
                # pre-flight confirm — not a separate toggle.
                rec = _new_rec("lifecycle", mode="live", lifecycle_ids=ids,
                               heavy=heavy, mutations=True, destructive=True)
                worker = _run_worker
            else:
                # simulate stays a server capability (no UI toggle): no cloud calls,
                # used to exercise the admission queue with zero billing.
                rec = _new_rec("simulate", mode="simulate", lifecycle_ids=ids, heavy=heavy)
                worker = _simulate_worker
            rec["peak_vpcs"], rec["queued"] = peak, False
            # admit now (reserve VPC slots) or enqueue if it would exceed the cap
            _admit_or_queue(rec, worker)
            return self._json(202, _rec_view(rec))
        if p == "/api/suites":
            b = self._body()
            try:
                view = _save_suite(b)
            except ValueError as exc:
                return self._json(400, {"error": str(exc)})
            except Exception as exc:  # noqa: BLE001
                return self._json(500, {"error": f"suite save failed: {exc}"})
            return self._json(201, {"suite": view, "suites": _list_suites_view()})
        if p == "/api/cleanup":
            # account-wide reconciler reaps by owner-tag (NOT per run) — running it
            # while other runs are in flight would delete their resources too. Block
            # it until nothing is running/queued (per-run teardown still cleans each
            # finished run; this button is only for account-wide leftovers).
            with _ADMIT:
                busy = bool(_RESERVED or _QUEUE)
            if busy:
                return self._json(409, {"error":
                    "진행 중(또는 대기 중) 실행이 있어 계정 전체 강제 클린업을 막았습니다 — "
                    "reconciler 는 owner-tag 로 전체를 reap 하므로 다른 실행이 만든 자원까지 "
                    "삭제됩니다. 모든 실행이 끝난 뒤 다시 시도하세요."})
            return self._json(202, _rec_view(_start("cleanup", _cleanup_worker)))
        if p == "/api/verify":
            return self._json(202, _rec_view(_start("verify", _verify_worker)))
        if p == "/api/owned":
            # read-only owned-resource inventory (LIST calls only) -> list + total
            return self._json(202, _rec_view(_start("owned", _owned_worker)))
        self._json(404, {"error": "not found"})

    def _file(self, path: Path) -> None:
        try:
            data = path.read_bytes()
        except Exception:
            return self._json(404, {"error": "not found"})
        self.send_response(200)
        self.send_header("Content-Type", _CT.get(path.suffix, "application/octet-stream"))
        self.send_header("Content-Length", str(len(data)))
        # dev/preview server: never let the browser keep a stale console2.js/css after
        # a `git pull` + restart — always serve fresh assets (avoids "I changed the
        # code but the page shows old behavior" confusion).
        self.send_header("Cache-Control", "no-cache, no-store, must-revalidate")
        self.end_headers()
        self.wfile.write(data)


def main():
    if not (WEB / "index.html").exists():
        sys.exit(f"console2 not found at {WEB} — run from the repo root")
    try:
        m = _model()
        print(f"  model: {m['node_count']} resources / {len(m['categories'])} categories / "
              f"{m['lifecycle_count']} lifecycles ({m['validated']} VALIDATED)")
    except Exception as exc:  # noqa: BLE001
        print(f"  warning: model build failed at startup ({exc}); /api/model will retry")
    srv = ThreadingHTTPServer(("127.0.0.1", PORT), Handler)
    print(f"console2 + executor: http://127.0.0.1:{PORT}/")
    print("  browse → plan (real dag_planner) → run (simulate | live) → live DAG-order report")
    print(f"  run logs + events: {RUN_DIR}")
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        print("\nbye")


if __name__ == "__main__":
    main()
