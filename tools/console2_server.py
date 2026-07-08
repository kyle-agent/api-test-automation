#!/usr/bin/env python3
"""console2 — local execution console ENGINE (browse → plan → run → live report).

RETIRED as a standalone server (convergence S4): the stdlib ``http.server`` is no
longer started. The console is served by the control-plane spine
(``controlplane/console_api.py`` answers the same ``/api/*`` contract by delegating to
the builder/worker functions below) and the ``console2/`` frontend is embedded under
controlplane **Testing**. This module is kept as the shared ENGINE LIBRARY — imported
by ``console_api`` + ``console2.build_static``; ``main()`` only prints how to run the
console now (``Handler``/``ThreadingHTTPServer`` below are kept, unused, so the
retirement stays trivially reversible).

Originally a zero-dependency (stdlib ``http.server``) backend for ``console2/``. Run it on a
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
import signal
import subprocess
import sys
import threading
import time
import uuid
from http.server import BaseHTTPRequestHandler
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
            # ready may be a LIST of specs (multi-stage readiness, composer
            # 2026-07-04); the waits run sequentially, so the display timeout
            # is their sum.
            if isinstance(ready, list):
                ready = {"timeout": sum(int(r.get("timeout", 180))
                                        for r in ready if isinstance(r, dict))}
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
            # loader-derived role (HEAVY-PREMISE CONTRACT §1): "verify" | "probe" —
            # consumed by scope resolution below and by the UI/CI.
            "role": lc.get("role"),
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
_RUNTIME_CACHE: dict = {"events": None, "oplog": None, "meta": None, "error": None,
                        "ts": 0.0, "wall": 0.0, "hours": None, "generating": False}
# 신선도 (신규5): a cached window older than this gets a "데이터 기준: N분 전"
# chip + an auto-refreshing page so the popup converges to the regenerated view.
_RUNTIME_STALE_S = 120.0
_RUNTIME_TTL = 60.0
_RUNTIME_LOCK = threading.Lock()
_RUNTIME_HOURS = (1, 6, 24)          # UI window choices (default 1)
# memo for _local_res_index (bounded rescan of run events/registry shards)
_LOCAL_RES_CACHE: dict = {"ts": 0.0, "val": None}
_LOCAL_RES_TTL_S = 5.0
# a local run younger than this with no tracked resources yet is NORMAL
# startup, not an attribution failure (review finding 2026-07-04)
_ATTRIB_GRACE_S = 180.0


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
            "<div style='font-size:12px;color:#8c959f'>첫 수집은 보통 30~90초 걸립니다 "
            "(loggingaudit 페이지네이션 + oplog 조인) — 이 창은 준비되는 대로 자동 표시됩니다.</div>"
            "<style>@keyframes spin{to{transform:rotate(360deg)}}</style></body>")


def _runtime_generate(hours: float) -> None:
    """The slow part (loggingaudit harvest + oplog-bucket read) — runs in a bg
    thread, updates the cache with RAW events (+ the run-id-tagged oplog events);
    rendering/filtering is cheap and happens per request in _runtime_view."""
    from datetime import datetime, timezone, timedelta
    from audit import live_view as lv
    now = datetime.now(timezone.utc)
    end = now.strftime("%Y-%m-%dT%H:%M:%SZ")
    start = (now - timedelta(hours=hours)).strftime("%Y-%m-%dT%H:%M:%SZ")
    error = None
    events: list[dict] = []
    oplog_events = None
    try:
        ev_path = str(ROOT / "reports" / "audit" / "_runtime.jsonl")
        lv.harvest(start, end, ev_path)             # loggingaudit → jsonl (needs creds)
        for line in open(ev_path, encoding="utf-8"):
            line = line.strip()
            if line:
                events.append(json.loads(line))
        # oplog join (origin annotation) — best-effort; None = bucket unreachable
        start_ms = int((now - timedelta(hours=hours)).timestamp() * 1000)
        oplog_events = lv.fetch_oplog_res_events(start_ms)
    except Exception as exc:                         # noqa: BLE001
        error = str(exc)
    with _RUNTIME_LOCK:
        _RUNTIME_CACHE.update(events=events, oplog=oplog_events, error=error,
                              meta={"start": start, "end": end},
                              ts=time.monotonic(), wall=time.time(),
                              hours=hours, generating=False)


def _local_run_ids() -> list[str]:
    """This console's OWN run-rec ids (the local origin set for the oplog join)."""
    with _LOCK:
        return list(_RUNS.keys())


def _local_res_index() -> dict:
    """``{run-rec id: {"ids": set, "names": set}}`` — the resources THIS console's
    own runs created, from purely LOCAL sources (no bucket, no network):

      * the per-run console-events sink (``rec['events']`` JSONL — the same
        ``resource-tracked``/``resource-deleted`` events the run detail's 자원 탭
        renders), carrying the captured resource_id (+ name where the engine had
        the create body); and
      * the per-run ``core.registry`` manifest shards
        (``reports/registry/<rec id>*.jsonl`` — one per xdist worker), carrying
        resource_id per crash-safe teardown record.

    This is the mine-set source for /runtime ``scope=mine``. It exists because the
    oplog-bucket join alone went BLANK during an active local run (defect
    2026-07-04): 'created' res events only reach the bucket after each create's
    polling completes (minutes after loggingaudit shows Create Start), and a
    console server process predating the APITEST_RUN_ID stamp files them under
    ``runs/local/`` — so the bucket must never be the only attribution source.

    Bounded + TTL-cached (review finding 2026-07-04): this runs on every /runtime
    request and ``_RUNS`` is never pruned, so an unbounded rescan of every run's
    full events file would make the view progressively slower over a long console
    session. Scope = active runs + runs that ended within the widest view window
    (24h), newest 50; whole result memoized for a few seconds."""
    now = time.time()
    with _RUNTIME_LOCK:
        c = _LOCAL_RES_CACHE
        if c["val"] is not None and now - c["ts"] < _LOCAL_RES_TTL_S:
            return c["val"]
    with _LOCK:
        recs = [(rid, r.get("events")) for rid, r in sorted(
                    _RUNS.items(), key=lambda kv: kv[1].get("started") or 0,
                    reverse=True)
                if r.get("status") in ("running", "queued")
                or (r.get("ended") or now) > now - 24 * 3600][:50]
    out: dict[str, dict] = {}
    for rid, evp in recs:
        ids: set[str] = set()
        names: set[str] = set()
        del_ids: set[str] = set()      # locally-KNOWN deleted (2xx DELETE step)
        del_names: set[str] = set()
        for e in (_read_events(evp) if evp else []):
            kind = e.get("kind")
            if kind not in ("resource-tracked", "resource-deleted"):
                continue
            if e.get("resource_id"):
                ids.add(str(e["resource_id"]))
                if kind == "resource-deleted":
                    del_ids.add(str(e["resource_id"]))
            if e.get("name"):
                names.add(str(e["name"]))
                if kind == "resource-deleted":
                    del_names.add(str(e["name"]))
            if kind == "resource-deleted" and not e.get("resource_id"):
                # the live engine's resource-deleted carries only the resolved
                # DELETE path — its trailing concrete segment IS the id (or the
                # name, e.g. keypairs). Record it so /runtime can grey out /
                # drop resources we KNOW this run already deleted, instead of
                # showing them as 생성됨/테스트중 while loggingaudit lags
                # (유령 자원, persona 2차 수용 2026-07-04).
                seg = (e.get("path") or "").split("?")[0].rstrip("/") \
                    .rsplit("/", 1)[-1]
                if seg and "{" not in seg and len(seg) >= 6:
                    del_ids.add(seg)
                    del_names.add(seg)
        for shard in (ROOT / "reports" / "registry").glob(f"{rid}*.jsonl"):
            try:
                for line in shard.read_text(encoding="utf-8").splitlines():
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        r = json.loads(line)
                    except ValueError:
                        continue
                    if r.get("resource_id"):
                        ids.add(str(r["resource_id"]))
            except OSError:
                continue
        if ids or names:
            out[rid] = {"ids": ids, "names": names,
                        "deleted_ids": del_ids, "deleted_names": del_names}
    with _RUNTIME_LOCK:
        _LOCAL_RES_CACHE.update(ts=now, val=out)
    return out


def _local_run_active() -> bool:
    with _LOCK:
        return any(r.get("status") in ("running", "queued") for r in _RUNS.values())


def _other_run_active(my_rec_id: str | None) -> bool:
    """True if a RESOURCE-CHANGING local run OTHER than ``my_rec_id`` is
    running/queued. Used by the post-run rescan H2 guard, whose only concern is
    "could another run's resources pollute an account-wide scan_owned?" — so
    only live lifecycle runs (create/delete real resources) and force cleanups
    (delete account-wide) count. Read-only records (owned/verify scans) and
    cloudless simulate replays must NOT trip the guard: they were making the
    +0/+5m rescans skip themselves (persona 2차 수용, 2026-07-04 — the rescan
    daemon saw its own concurrent owned-scan record as '다른 실행')."""
    with _LOCK:
        return any(r.get("status") in ("running", "queued")
                   and (r.get("kind") == "cleanup"
                        or (r.get("kind") == "lifecycle"
                            and r.get("mode") == "live"))
                   for rid, r in _RUNS.items() if rid != my_rec_id)


def _active_live_run(exclude: str | None = None) -> dict | None:
    """The first live lifecycle run currently running/queued (or None).

    Dup-admit guard (persona 2차 수용, 2026-07-04): two identical heavy
    configurations were admitted 2.2s apart with no warning — POST /api/run now
    409s a new LIVE run while another live run is in flight. The VPC admission
    queue still exists for simulate replays and post-abort re-admission."""
    with _LOCK:
        for r in _RUNS.values():
            if (r.get("id") != exclude and r.get("kind") == "lifecycle"
                    and r.get("mode") == "live"
                    and r.get("status") in ("running", "queued")):
                return r
    return None


def _local_run_youngest_age() -> float:
    """Seconds since the most recently STARTED active local run (inf if none)."""
    now = time.time()
    with _LOCK:
        ages = [now - (r.get("started") or 0) for r in _RUNS.values()
                if r.get("status") in ("running", "queued")]
    return min(ages) if ages else float("inf")


def _latest_owned_scan():
    """Newest COMPLETED owned scan -> ``(scan_epoch, items)`` or None. items =
    ``[{"service","path",("json")}]`` exactly as verify_clean.scan_owned returned
    them (one entry per delete the sweep WOULD issue; json = bulk-id body)."""
    with _LOCK:
        recs = [r for r in _RUNS.values()
                if r.get("kind") == "owned" and r.get("status") == "done"
                and isinstance(r.get("owned"), list)]
        if not recs:
            return None
        r = max(recs, key=lambda x: x.get("ended") or x.get("started") or 0)
        return (r.get("ended") or r.get("started") or time.time(),
                list(r["owned"]))


def _runtime_view(hours: float = 1.0, scope: str = "mine", deleted: str = "hide"):
    """Return ``(html_or_None, ready)`` without blocking — kick a bg harvest when
    the cache is stale so the popup never hangs on a cold load.

    scope=mine (default) keeps spans attributable to a run THIS console started —
    attributed from the console's own in-process records first
    (:func:`_local_res_index`, bucket-independent) with the oplog-bucket join kept
    for CI badge attribution. Fallbacks when mine resolves to zero spans:

      * a local run IS active  → scope=all + '내 실행 귀속 실패' banner (a blank
        page during an active run is the worst outcome — defect 2026-07-04);
      * nothing in flight      → scope=all + the informational note (as before).

    deleted=hide (default) drops already-deleted spans; hours ∈ {1,6,24}."""
    from datetime import datetime, timezone
    from audit import live_view as lv
    scope = scope if scope in ("mine", "all") else "mine"
    deleted = deleted if deleted in ("hide", "show") else "hide"
    with _RUNTIME_LOCK:
        fresh = (_RUNTIME_CACHE["events"] is not None
                 and _RUNTIME_CACHE["hours"] == hours
                 and (time.monotonic() - _RUNTIME_CACHE["ts"]) < _RUNTIME_TTL)
        if not fresh and not _RUNTIME_CACHE["generating"]:
            _RUNTIME_CACHE["generating"] = True
            threading.Thread(target=_runtime_generate, args=(hours,), daemon=True).start()
        # fresh cache → render it; stale-but-same-window cache → still useful
        usable = (_RUNTIME_CACHE["events"] is not None
                  and _RUNTIME_CACHE["hours"] == hours)
        events = list(_RUNTIME_CACHE["events"] or []) if usable else None
        oplog_events = _RUNTIME_CACHE["oplog"] if usable else None
        meta = dict(_RUNTIME_CACHE["meta"] or {}) if usable else {}
        error = _RUNTIME_CACHE["error"] if usable else None
        cache_wall = _RUNTIME_CACHE.get("wall") or 0.0
    if events is None:
        return None, False
    # freshness (신규5): how old is the rendered WINDOW? Beyond ~2min the page
    # says so explicitly and auto-refreshes until the bg regen lands — a popup
    # must never silently show a 25-minutes-ago window as "now".
    age_s = max(0.0, time.time() - cache_wall) if cache_wall else None
    stale = age_s is not None and age_s > _RUNTIME_STALE_S
    refresh = 12 if (stale or not fresh) else 0
    if error and not events:
        return _runtime_error_html(error), fresh
    now = datetime.now(timezone.utc)
    spans = lv.build_spans(events, now, ours_only=True)
    lv.annotate_origins(spans, oplog_events, local_run_ids=_local_run_ids())
    # LOCAL overlay (wins over the bucket join): attribute spans from this
    # console's own in-process run records, so scope=mine works even when the
    # bucket is unreachable, lagging, or filing events under runs/local/.
    lv.annotate_local_origins(spans, _local_res_index())
    banner = note = None
    eff_scope = scope
    if scope == "mine":
        mine = lv.filter_spans(spans, scope="mine", deleted=deleted)
        if mine:
            shown = mine
        elif _local_run_active():
            # A local run IS in flight but nothing attributed. NEVER render a
            # blank page here (defect 2026-07-04): degrade to the account view.
            # Within the startup grace window this is NORMAL (the engine emits
            # resource-tracked only after the first create's polling captures
            # an id — minutes on heavy chains), so don't cry defect yet
            # (review finding 2026-07-04).
            eff_scope = "all"
            if _local_run_youngest_age() < _ATTRIB_GRACE_S:
                banner = ("내 실행 준비 중 — 자원 이벤트 대기 (계정 전체 표시 중, "
                          "첫 자원이 잡히면 '내 실행'으로 자동 표시)")
            else:
                banner = "내 실행 귀속 실패 — 계정 전체 표시 중, 귀속 로직 점검 필요"
            shown = lv.filter_spans(spans, scope="all", deleted=deleted)
        else:
            # nothing attributable to my runs and nothing in flight → show the
            # account instead of an empty page, and SAY so.
            eff_scope = "all"
            note = ("내 실행으로 귀속되는 자원이 없어 계정 전체 뷰로 전환했습니다 "
                    "(로컬 실행이 시작되면 기본 '내 실행' 범위로 보세요).")
            shown = lv.filter_spans(spans, scope="all", deleted=deleted)
    else:
        shown = lv.filter_spans(spans, scope="all", deleted=deleted)
    if eff_scope == "all" and not banner:
        banner = "계정 전체 뷰 — 다른 run·CI 자원 포함"
    if oplog_events is None:
        note = ((note + " · ") if note else "") + \
            "oplog 버킷에 접근할 수 없어 CI 출처(origin) 배지가 불완전할 수 있습니다."
    if age_s is not None and age_s >= 60:
        chip = f"🕒 데이터 기준: {int(age_s // 60)}분 전 윈도우"
        if stale:
            chip += " — 재수집 중, 자동 새로고침"
        note = chip + ((" · " + note) if note else "")
    # 실측 잔존 핀 고정 (owner GO 2026-07-08): 최근 완료된 owned 스캔이 확인한
    # 잔존 자원은 이벤트 창(기본 1h) 밖이어도 항상 보여야 한다 — 창 밖 잔존
    # 5건이 라이브 뷰에서 통째로 안 보였던 목격이 계기. 화면의 어떤 스팬
    # (res_id/name)과도 매칭되지 않는 스캔 항목만 합성 스팬으로 덧붙인다
    # (읽기 전용 오버레이; 스캔 시각을 배지·노트로 명시해 실시간과 구분).
    surv = _latest_owned_scan()
    if surv:
        scan_ep, items = surv
        # loggingaudit 스팬과 같은 ts 어휘(초 단위 Z, 마이크로초 없음) — live_view._t
        # 가 이 포맷만 파싱한다 (isoformat()의 +00:00/micros는 ValueError).
        scan_iso = datetime.fromtimestamp(scan_ep, tz=timezone.utc).strftime(
            "%Y-%m-%dT%H:%M:%SZ")
        hhmm = time.strftime("%H:%M", time.localtime(scan_ep))
        seen = set()
        for d in shown.values():
            for f in ("res_id", "name"):
                if d.get(f):
                    seen.add(str(d[f]))
        n_surv = 0
        for it in items:
            path = str(it.get("path") or "").split("?", 1)[0].rstrip("/")
            segs = [s for s in path.split("/") if s]
            body = it.get("json")
            rids, rtype = [], "?"
            if isinstance(body, dict):   # bulk delete: id 리스트가 body에 실림
                for v in body.values():
                    if isinstance(v, list) and v and all(isinstance(x, str) for x in v):
                        rids, rtype = list(v), (segs[-1] if segs else "?")
                        break
            if not rids and segs:
                rids, rtype = [segs[-1]], (segs[-2] if len(segs) >= 2 else segs[-1])
            for rid in rids:
                if not rid or rid in seen:
                    continue
                seen.add(rid)
                disp = f"(잔존) {rid[:24]}"
                shown[(rtype, "regr-survivor", disp)] = {
                    "rtype": rtype, "tag": "regr-survivor", "name": disp,
                    "start": scan_iso, "end": None,
                    "ops": [(scan_iso, "SurvivorScan")],
                    "res_id": rid, "survivor": True, "scan_hhmm": hhmm}
                n_surv += 1
        if n_surv:
            extra = (f"⚠ 실측 잔존 {n_surv}건 핀 고정 — owned 스캔({hhmm}) 확인분, "
                     f"이벤트 창 밖 포함 (붉은 점선 박스)")
            note = (note + " · " + extra) if note else extra
    chrome = {"scope": scope, "hours": int(hours), "deleted": deleted,
              "banner": banner, "note": note}
    html_out = lv.render_flow(shown, now, meta, refresh=refresh, chrome=chrome)
    return html_out, fresh


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
    Resolve all of them to the union of source lifecycle ids (deduped, sorted).

    HEAVY-PREMISE CONTRACT §2: SCOPE selections (service / category / group —
    i.e. anything reached via node_ids/services/categories) expand only to
    ``enabled AND role=="verify"`` lifecycles — write-reachability probes are
    CI-sweep material and never join a scope expansion. Heavy is NOT filtered
    here (heavy-premise: it stays pre-flight display metadata). EXPLICIT
    ``lifecycle_ids`` win: kept regardless of role, so a probe can still be
    run deliberately by naming it."""
    m = _model()
    nodes, lcs = m["nodes"], m["lifecycles"]
    explicit = {lid for lid in (sel.get("lifecycle_ids") or []) if lid in lcs}
    node_ids = set(sel.get("node_ids") or [])
    svcs = set(sel.get("services") or [])
    cats = set(sel.get("categories") or [])
    scoped: set[str] = set()
    for nid, n in nodes.items():
        if not n.get("lifecycle"):
            continue
        if nid in node_ids or n["service"] in svcs or n["category"] in cats:
            scoped.add(n["lifecycle"])
    # 서비스/카테고리 scope는 노드-source lifecycle뿐 아니라 그 서비스로 태그된
    # lifecycle 전부를 포함한다 (§2 "서비스 풀 테스트") — 어떤 노드도 가리키지 않는
    # 합성/추가 lifecycle(예: vs-server-actions-verify)이 빠지지 않도록.
    if svcs or cats:
        # 표기 정규화: lifecycle 태그는 "virtualserver"/"compute/virtualserver"
        # 두 형태가 혼재할 수 있다 — 서비스 코드는 카탈로그 전역에서 유일하므로
        # 짧은 이름 비교가 안전하고, 태그 표기 차이로 union 에서 조용히 빠지는
        # 일이 없어야 한다 (2026-07-08 모달 2행 분리의 원인).
        svcs_short = {s.split("/")[-1] for s in svcs}
        for lid, lc in lcs.items():
            svc = lc.get("service") or ""
            if not svc:
                continue
            cat = svc.split("/", 1)[0] if "/" in svc else ""
            if svc in svcs or svc.split("/")[-1] in svcs_short or cat in cats:
                scoped.add(lid)
    scoped = {lid for lid in scoped
              if lid in lcs and lcs[lid].get("enabled")
              and lcs[lid].get("role") == "verify"}
    return sorted(explicit | scoped)


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


_EMPTY_GRAPH = {"nodes": [], "edges": [], "levels": [0], "shared": [],
                "peak_quota": {}, "order": [], "teardown": []}


def _run_graph(rec: dict) -> dict:
    """The composition DAG for a RUN's lifecycle closure — same
    ``composer.graph_view`` + ``resource_graph.js`` contract as the 구성 graph
    (IA-BUILD-CONTRACT: same graph, different overlay). Lets the master 흐름
    scene rebind to any run (active or history row) instead of whatever the
    구성 selection happens to be [F1·F2]."""
    ids = set(rec.get("lifecycle_ids") or [])
    if not ids:
        return dict(_EMPTY_GRAPH)
    m = _model()
    targets = sorted(nid for nid, n in m["nodes"].items()
                     if n.get("lifecycle") in ids)
    if not targets:
        return dict(_EMPTY_GRAPH)
    from regression.scenarios import composer
    return composer.graph_view(targets)


def _durations_view() -> dict:
    """Measured per-lifecycle wall durations (data/optimizer/durations.json) —
    feeds the now-playing bar's '평균 ~12m' expectation [신규4]."""
    try:
        from regression.scenarios.schedule_optimizer import load_durations
        return {k: {"avg_s": v.get("avg_s"), "n": v.get("n")}
                for k, v in load_durations().items() if isinstance(v, dict)}
    except Exception:  # noqa: BLE001
        return {}


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
    # measured wall durations (rolling avg per lifecycle, learned across runs) —
    # data/optimizer/durations.json via schedule_optimizer; absent → None (UI: 미측정)
    try:
        from regression.scenarios.schedule_optimizer import load_durations
        durations = load_durations()
    except Exception:  # noqa: BLE001
        durations = {}
    # per-lifecycle step preview (which APIs each leaf will exercise) + the
    # pre-flight blast-radius facts: est creates/deletes (POST/DELETE steps) and
    # the measured duration when we have one.
    preview = {}
    for lid in p.leaf_set:
        lc = m["lifecycles"].get(lid, {})
        steps = lc.get("steps", [])
        dur = durations.get(lid) or {}
        preview[lid] = {"service": lc.get("service", ""), "heavy": lc.get("heavy", False),
                        "n_steps": lc.get("n_steps", 0), "steps": steps,
                        "est_creates": sum(1 for s in steps if s.get("kind") == "create"),
                        "est_deletes": sum(1 for s in steps if s.get("kind") == "delete"),
                        "duration_s": (round(float(dur["avg_s"]), 1)
                                       if dur.get("avg_s") else None),
                        "duration_n": int(dur.get("n") or 0)}
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
# pre-flight (HEAVY-PREMISE-CONTRACT §3) — 실행 전 confirm의 정보원: 무엇이
# 만들어지고(자원·과금), 얼마나 걸릴지(측정 히스토리 makespan). 순수 조립 —
# composer graph(자원·peak_quota) + dag plan(runnable) + duration_stats(est).
# --------------------------------------------------------------------------- #
def _preflight(sel: dict) -> dict:
    ids = (sel.get("lifecycle_ids") if "lifecycle_ids" in sel and not (
        sel.get("node_ids") or sel.get("services") or sel.get("categories"))
        else _resolve_lifecycle_ids(sel))
    ids = [str(x).strip() for x in (ids or []) if str(x).strip()]
    plan = _plan(ids)
    runnable = plan.get("runnable") or ids
    m = _model()

    # 생성 자원 = 선택의 합성 그래프 노드들 (UI 미리보기와 같은 소스). 그래프가 없으면
    # (lifecycle-only 선택 등) plan의 peak_vpcs로 강등 — 페이지는 항상 응답을 받는다.
    resources: list[dict] = []
    peak_quota: dict = {}
    try:
        g = _graph(sel)
        for n in g.get("nodes") or []:
            resources.append({"node": n.get("id", "?"), "service": n.get("service", ""),
                              "count": 1, "billable": bool(n.get("heavy"))})
        peak_quota = dict(g.get("peak_quota") or {})
    except Exception:  # noqa: BLE001 — graph 실패는 견적을 막지 않는다
        resources = []
    if "vpc" not in peak_quota:
        peak_quota["vpc"] = plan.get("peak_vpcs", 0)
    billable_count = sum(1 for r in resources if r["billable"])
    if not resources:
        # 자원 그래프가 없으면 heavy lifecycle 수를 과금 신호로 사용 (보수적 표기)
        billable_count = sum(1 for lid in runnable
                             if (m["lifecycles"].get(lid) or {}).get("heavy"))

    try:
        from tools import duration_stats
        est = duration_stats.estimate(runnable, model={"lifecycles": m["lifecycles"]})
    except Exception as exc:  # noqa: BLE001 — 견적 실패도 confirm은 가능해야 한다
        est = {"p50_s": None, "p90_s": None, "basis": "unavailable",
               "per_lifecycle": {}, "error": str(exc)[:120]}

    warnings: list[str] = []
    if est.get("basis") == "default":
        warnings.append("예상 시간이 전부 기본값입니다 (측정 이력 없음)")
    elif est.get("basis") == "mixed":
        warnings.append("일부 lifecycle은 예상 시간이 기본값입니다")
    if plan.get("skipped_disabled"):
        warnings.append(f"비활성 lifecycle {len(plan['skipped_disabled'])}개 제외")

    return {"lifecycles": runnable, "resources": resources, "peak_quota": peak_quota,
            "billable_count": billable_count, "est": est, "warnings": warnings}


# --------------------------------------------------------------------------- #
# soft 3분류 (HEAVY-PREMISE-CONTRACT §4) — step-end 이벤트의 soft를 중복/갭/정책으로.
# 폴링마다 불리므로 스토어(카탈로그/waiver/verified)는 모듈 싱글턴으로 1회 로드.
# 분류 실패는 절대 리포트를 막지 않는다 (chip만 안 뜰 뿐).
# --------------------------------------------------------------------------- #
_SOFTCLS_DATA: dict = {}


def _softcls_data() -> dict:
    if not _SOFTCLS_DATA:
        from regression import soft_classify as sc
        _SOFTCLS_DATA.update(sc=sc, catalog=sc.load_catalog(),
                             waivers=sc.load_waivers(), verified=sc.load_verified())
    return _SOFTCLS_DATA


def _enrich_soft_classes(events: list[dict]) -> list[dict]:
    try:
        if not any(e.get("kind") == "step-end" and e.get("category") == "soft"
                   for e in events):
            return events
        d = _softcls_data()
        sc = d["sc"]
        idxs: list[int] = []
        obs: list[dict] = []
        for i, e in enumerate(events):
            if e.get("kind") == "step-end":
                idxs.append(i)
                obs.append({"endpoint_key": f"{e.get('lifecycle', '?')}:{e.get('step', '?')}",
                            "method": e.get("method"), "path": e.get("path"),
                            "status": e.get("status"), "category": e.get("category")})
        run2xx = sc.build_run_2xx(obs)
        cmap = sc.classify(obs, verified=d["verified"], waivers=d["waivers"],
                           run_endpoint_2xx=run2xx, catalog=d["catalog"])
        # 후처리 (계약 §4 보강, 2026-07-08 owner):
        #  · confirm(삭제확인): 같은 lifecycle에서 앞선 DELETE 2xx가 지운 경로의 404
        #    읽기 = teardown 검증 성공 신호이지 miss가 아님. policy보다 우선.
        #  · duplicate 2분할: dup_run(이번 런에서 같은 endpoint 2xx 이미 땀) vs
        #    dup_store(과거 기록만 — 이번 런에서는 직접 확인 안 됨). 회귀 관점의
        #    "오늘 검증 안 된 것"이 dup_store로 드러난다.
        # confirm 매칭 키 = (lifecycle, 원시 경로 템플릿). norm_path(*) 로 접으면
        # 같은 컬렉션의 다른 인스턴스({vol2_id})까지 confirm 으로 오인한다 — 레시피는
        # 인스턴스를 placeholder 이름으로 구분하므로 원시 템플릿이 정확한 identity.
        deleted: set = set()
        for oi, o in enumerate(obs):
            rpath = o.get("path") or ""
            lc = (o.get("endpoint_key") or ":").split(":", 1)[0]
            st = o.get("status")
            if oi in cmap:
                if st == 404 and (lc, rpath) in deleted:
                    cmap[oi] = "confirm"
                elif cmap[oi] == "duplicate":
                    tok = sc.endpoint_token(o.get("method"), o.get("path"))
                    raw = o.get("endpoint_key")
                    cmap[oi] = ("dup_run" if (tok in run2xx or raw in run2xx)
                                else "dup_store")
            if (isinstance(st, int) and 200 <= st < 300
                    and (o.get("method") or "").upper() == "DELETE"):
                deleted.add((lc, rpath))
        for oi, cls in cmap.items():
            events[idxs[oi]]["soft_class"] = cls
    except Exception:  # noqa: BLE001
        pass
    return events


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
# run-history observability (2026-07-04 Run 관측성 개편):
#   * _events_summary  — fold an event stream to the run verdict (lifecycle
#     pass/fail + api ok/soft/fail; still-open steps of a FAILED lifecycle are
#     closed as fail so a timeout never leaves a phantom ⏳ row — mirrors
#     console2.js groupEventsByLifecycle).
#   * _rehydrate_runs  — rebuild _RUNS from reports/console2-runs/*.log/.events
#     on server start (신규2: a console restart used to erase all run history).
#   * _record_run_to_db / _backfill_runs_db — mirror finished local runs into
#     the controlplane runs DB (gh_run_id = "local-<rec id>") so Reporting ▸
#     실행 기록 and /runs/{id} show them (P2-9 완결).
#   * _post_run_rescans — owned re-scans at +0/+5m/+15m after a run ends
#     (신규1: run 64b5's timed-out image-create materialized 2 images +
#     4 snapshots ~20min AFTER the '0건' scan — the +0 scan alone is false
#     comfort). A later scan finding MORE than +0 raises rec["late_alert"].
# --------------------------------------------------------------------------- #
def _events_summary(events: list[dict]) -> dict:
    """Pure fold: event stream -> {"lifecycles": {...}, "api": {...}}. Steps that
    were started but never ended when their lifecycle ends count as ``fail``
    (timeout/중단) — the in-flight row must CLOSE on failure [F3]."""
    lc_state: dict[str, str] = {}
    api = {"ok": 0, "soft": 0, "fail": 0}
    open_steps: dict[tuple, dict] = {}
    for e in events or []:
        k = e.get("kind")
        lid = e.get("lifecycle")
        if k == "lifecycle-start":
            lc_state.setdefault(lid, "running")
        elif k == "step-start":
            open_steps[(lid, e.get("step"))] = e
        elif k == "step-end":
            open_steps.pop((lid, e.get("step")), None)
            cat = e.get("category")
            if cat in api:
                api[cat] += 1
        elif k == "lifecycle-end":
            st = e.get("status")
            lc_state[lid] = ("passed" if st == "passed"
                             else "skipped" if st == "skipped" else "failed")
            # close this lifecycle's still-open steps as fail (timeout/중단)
            for key in [key for key in open_steps if key[0] == lid]:
                open_steps.pop(key)
                api["fail"] += 1
    failed_ids = sorted(k for k, v in lc_state.items() if v == "failed")
    return {
        "lifecycles": {
            "total": len(lc_state),
            "passed": sum(1 for v in lc_state.values() if v == "passed"),
            "failed": len(failed_ids),
            "skipped": sum(1 for v in lc_state.values() if v == "skipped"),
            "unfinished": sum(1 for v in lc_state.values() if v == "running"),
            "failed_ids": failed_ids,
        },
        "api": api,
    }


_REHY_KIND = (  # log-header marker -> rec kind (see the workers' first write)
    ("# console2 SIMULATE", "simulate"),
    ("# console2 FORCE cleanup", "cleanup"),
    ("# console2 cleanup VERIFY", "verify"),
    ("# console2 owned-resource scan", "owned"),
    ("# console2 run ", "lifecycle"),
)
# any pytest summary token (deselected/xfailed/xpassed/... included — an
# unrecognized token must not defeat the whole line; M2 review 2026-07-04)
_PYTEST_SUMMARY_RE = re.compile(
    r"(?m)^((?:\d+ [a-z]+,? ?)+) ?in [\d.]+s")


def _rehydrate_one(rid: str, logp: Path, evp: Path) -> dict | None:
    """One run-id's on-disk remains -> a rec dict (or None when unparseable)."""
    import ast
    head = txt = ""
    try:
        if logp.exists():
            txt = logp.read_text(encoding="utf-8", errors="replace")
            head = "\n".join(txt.splitlines()[:3])
    except OSError:
        txt = head = ""
    kind = "lifecycle"
    for marker, k in _REHY_KIND:
        if marker in head:
            kind = k
            break
    lifecycle_ids: list = []
    m = re.search(r"lifecycle_ids=(\[[^\n]*\])", head)
    if m:
        try:
            lifecycle_ids = list(ast.literal_eval(m.group(1)))
        except Exception:  # noqa: BLE001
            lifecycle_ids = []
    gates = {"mutations": "mutations=True" in head,
             "destructive": "destructive=True" in head,
             "heavy": "heavy=True" in head}
    # verdict from the pytest tail (lifecycle runs) / worker markers (utilities)
    status, rc = "unknown", None
    ms = _PYTEST_SUMMARY_RE.search(txt or "")
    # ABORT marker (실행 중단 버튼) — carries the abort epoch so the duration
    # stays honest even after rescan lines bump the log's mtime.
    ma = re.search(r"=== 실행 중단\(aborted\)(?: ts=([\d.]+))?", txt or "")
    if kind == "lifecycle":
        if ma:
            status, rc = "aborted", 1
        elif ms:
            status = "done"
            # errors count as failure too — '1 passed, 2 errors' is NOT a
            # success (M2 review 2026-07-04)
            rc = 1 if re.search(r"\d+ (?:failed|errors?)\b", ms.group(1)) else 0
    elif txt:
        status, rc = "done", 0
    runner_missing = "테스트 러너 없음" in (txt or "")
    # timestamps: event stream first/last ts, else file mtimes
    started = ended = None
    events: list[dict] = []
    if evp.exists():
        events = _read_events(str(evp))
        if events:
            started = events[0].get("ts")
            ended = events[-1].get("ts")
    try:
        mt = logp.stat().st_mtime if logp.exists() else (
            evp.stat().st_mtime if evp.exists() else None)
    except OSError:
        mt = None
    if not txt and not events:
        return None                # 0-byte remains (aborted starts) — pure noise
    if started is None:
        started = mt or 0.0
    if ended is None:
        ended = mt
    # duration honesty for ABNORMAL endings (지속시간 보정): a crashed/aborted
    # run's event stream stops early (e.g. 57s) while the log keeps recording
    # until the real end (393s). Prefer the abort marker's own epoch; else the
    # log mtime — but never when post-run rescan lines already inflated it.
    if ma and ma.group(1):
        try:
            ended = max(float(ended or 0), float(ma.group(1)))
        except ValueError:
            pass
    elif (status in ("unknown", "aborted") and mt
          and "종료 후 재스캔" not in (txt or "")):
        ended = max(float(ended or 0), float(mt))
    rec = {"id": rid, "kind": kind, "mode": "live" if kind == "lifecycle" else kind,
           "status": status, "lifecycle_ids": lifecycle_ids,
           "heavy": gates["heavy"], "mutations": gates["mutations"],
           "destructive": gates["destructive"],
           "started": started, "ended": ended, "rc": rc,
           "log": str(logp), "events": str(evp), "rehydrated": True}
    if runner_missing:
        rec["runner_missing"] = True
    if kind == "lifecycle" and events:
        rec["events_summary"] = _events_summary(events)
    # rescan sidecar (results + plan survive a restart — 신규 persist)
    try:
        sc = _rescan_sidecar(rec)
        if sc is not None and sc.exists():
            data = json.loads(sc.read_text(encoding="utf-8"))
            if isinstance(data, dict):
                rec["rescans"] = [e for e in (data.get("rescans") or [])
                                  if isinstance(e, dict)]
                if data.get("late_alert"):
                    rec["late_alert"] = data["late_alert"]
                if data.get("planned_offsets"):
                    rec["rescan_offsets"] = [float(o) for o
                                             in data["planned_offsets"]]
                if data.get("anchor"):
                    rec["rescan_anchor"] = float(data["anchor"])
    except Exception:  # noqa: BLE001 — a torn sidecar never blocks rehydration
        pass
    return rec


def _rehydrate_runs(run_dir: Path | None = None) -> int:
    """Rebuild _RUNS from the per-run files on disk (server start). Never raises;
    existing in-memory recs win. Returns the number of rehydrated recs."""
    rd = run_dir or RUN_DIR
    found: dict[str, dict] = {}
    try:
        for p in rd.glob("*.log"):
            found.setdefault(p.name[:-len(".log")], {})["log"] = p
        for p in rd.glob("*.events.jsonl"):
            found.setdefault(p.name[:-len(".events.jsonl")], {})["events"] = p
    except OSError:
        return 0
    n = 0
    for rid, files in sorted(found.items()):
        with _LOCK:
            if rid in _RUNS:
                continue
        try:
            rec = _rehydrate_one(rid, files.get("log") or rd / f"{rid}.log",
                                 files.get("events") or rd / f"{rid}.events.jsonl")
        except Exception:  # noqa: BLE001 — one bad file never blocks the rest
            rec = None
        if not rec:
            continue
        with _LOCK:
            _RUNS.setdefault(rid, rec)
        n += 1
    return n


def _iso_utc(ts) -> str | None:
    try:
        return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(float(ts)))
    except Exception:  # noqa: BLE001
        return None


def _record_run_to_db(rec: dict) -> None:
    """Best-effort mirror of a FINISHED local lifecycle run into the controlplane
    runs DB (gh_run_id = ``local-<rec id>``) so Reporting ▸ 실행 기록 and
    /runs/{id} can show it. Silent no-op on any failure (the console must never
    die on DB trouble); only runs with a verdict (rc known) are recorded."""
    if rec.get("kind") != "lifecycle" or rec.get("rc") is None:
        return
    try:
        from controlplane import db as _cdb
        summ = rec.get("events_summary")
        if summ is None:
            summ = _events_summary(_read_events(rec.get("events", "")))
            rec["events_summary"] = summ
        detail = json.dumps({"source": "console2-local",
                             "lifecycle_ids": rec.get("lifecycle_ids") or [],
                             "summary": summ}, ensure_ascii=False)
        _cdb.record_local_run(
            "local-" + rec["id"],
            status=("aborted" if rec.get("status") == "aborted"
                    else "done" if rec.get("rc") == 0 else "failed"),
            requested_at=_iso_utc(rec.get("started")),
            finished_at=_iso_utc(rec.get("ended")),
            detail=detail)
    except Exception:  # noqa: BLE001
        pass


def _backfill_runs_db() -> int:
    """Record every rehydrated finished lifecycle run into the controlplane DB
    (idempotent — record_local_run upserts on gh_run_id)."""
    with _LOCK:
        recs = [r for r in _RUNS.values()
                if r.get("rehydrated") and r.get("kind") == "lifecycle"
                and r.get("rc") is not None]
    for r in recs:
        _record_run_to_db(r)
    return len(recs)


def _local_run_summary(gh_run_id: str) -> dict | None:
    """Pass/fail summary for a ``local-*`` run id, from its events file — used by
    the controlplane run-detail page (P2-9 잔여). Looks in this console's RUN_DIR
    (id minus the ``local-`` prefix) and the worker executor's sink."""
    rid = gh_run_id[len("local-"):] if gh_run_id.startswith("local-") else gh_run_id
    with _LOCK:
        rec = _RUNS.get(rid)
    candidates = []
    if rec:
        if rec.get("events_summary"):
            return rec["events_summary"]
        if rec.get("events"):
            candidates.append(Path(rec["events"]))
    candidates += [RUN_DIR / f"{rid}.events.jsonl",
                   ROOT / "reports" / "controlplane-local" / f"{gh_run_id}.jsonl"]
    for p in candidates:
        try:
            if p.exists() and p.stat().st_size:
                return _events_summary(_read_events(str(p)))
        except OSError:
            continue
    return None


# ---- 종료 후 지연 재스캔 (신규1) ---------------------------------------------
_RESCAN_OFFSETS_S = (0.0, 300.0, 900.0)     # +0 · +5m · +15m
# how long after a run's end an unexecuted rescan offset is still worth running
# late (server was down at its scheduled time) — beyond this, don't bother.
_RESCAN_RESUME_GRACE_S = 3600.0


def _default_owned_scan() -> list[dict]:
    # read-only-ness guaranteed by scan_owned stubbing _delete/_wait_gone
    os.environ.setdefault("SCP_ALLOW_DESTRUCTIVE", "false")
    from cleanup.verify_clean import scan_owned
    errs: list = []
    owned = scan_owned(list_errors=errs)
    if not owned and errs:
        # every collection came back empty BUT some LISTs failed — a "0건"
        # verdict here would be false comfort (persona 2차 수용: the rescan
        # reported total 0 while 6 resources survived). Raise so the caller
        # records '스캔 실패', clearly distinct from a genuine 0.
        first = errs[0]
        raise RuntimeError(
            f"스캔 불완전 — {len(errs)}개 컬렉션 LIST 실패 "
            f"(첫 실패: {first.get('service')} {first.get('path')} "
            f"{first.get('error')})")
    return owned


def _rescan_sidecar(rec: dict) -> Path | None:
    """Disk twin of rec['rescans']/'late_alert' — <run log>.rescans.json. The
    rescan results/schedule used to live only in this process's memory, so a
    server restart lost both (persona 2차 수용: +15m 일정 소실, rescans=null)."""
    logp = rec.get("log")
    if not logp:
        return None
    return Path(logp).with_suffix(".rescans.json")


def _persist_rescans(rec: dict) -> None:
    """Write the rescan state (results + plan) next to the run log. Best-effort
    atomic (tmp + rename); never raises."""
    p = _rescan_sidecar(rec)
    if p is None:
        return
    try:
        with _LOCK:
            data = {"rescans": list(rec.get("rescans") or []),
                    "late_alert": rec.get("late_alert"),
                    "planned_offsets": list(rec.get("rescan_offsets")
                                            or _RESCAN_OFFSETS_S),
                    "anchor": rec.get("rescan_anchor")}
        tmp = p.with_suffix(".rescans.json.tmp")
        tmp.write_text(json.dumps(data, ensure_ascii=False, default=str),
                       encoding="utf-8")
        tmp.replace(p)
    except Exception:  # noqa: BLE001 — persistence is best-effort
        pass


def _post_run_rescans(rec: dict, offsets=_RESCAN_OFFSETS_S,
                      scan=None, sleep=time.sleep, anchor=None) -> None:
    """Owned re-scans at the given offsets after a run ends. Results accrue on
    ``rec["rescans"]`` (and are PERSISTED to the run's ``.rescans.json`` sidecar
    so a restart can't lose them); a later scan finding MORE items than the +0
    scan sets ``rec["late_alert"]`` (비동기 생성물 의심) — the UI surfaces it
    prominently. ``scan``/``sleep`` are injectable so tests drive this with a
    fake clock. ``anchor`` (epoch seconds) schedules offsets relative to the
    run's END instead of "now" — the restart-resume path; offsets already
    recorded on the rec are skipped, and overdue ones run immediately."""
    if scan is None:
        scan = _default_owned_scan
    with _LOCK:
        rec.setdefault("rescans", [])       # keep rehydrated partial results
        rec["rescan_offsets"] = [float(o) for o in offsets] \
            if anchor is None else sorted(
                {float(o) for o in list(rec.get("rescan_offsets") or [])
                 + [float(o) for o in offsets]})
        rec.setdefault("rescan_anchor",
                       float(anchor) if anchor is not None else time.time())
        done_offsets = {float(e.get("offset_s", -1))
                        for e in rec.get("rescans") or []}
        # base for the late-alert comparison survives a restart too
        base = next((e["total"] for e in rec.get("rescans") or []
                     if e.get("total") is not None), None)
    _persist_rescans(rec)
    prev = 0.0
    for off in offsets:
        if anchor is not None:
            wait = (float(anchor) + float(off)) - time.time()
        else:
            wait = float(off) - prev
            prev = float(off)
        if float(off) in done_offsets:
            continue                        # already ran (before the restart)
        if wait > 0:
            sleep(wait)
        label = f"+{int(off // 60)}m" if off >= 60 else f"+{int(off)}s"
        # H2 guard (review 2026-07-04): scan_owned is ACCOUNT-WIDE — if another
        # run is executing during this round, its fresh resources would read as
        # this run's "늦출현" (false positive, near-guaranteed with a queue).
        # Skip and label the round instead of comparing apples to oranges.
        if _other_run_active(rec.get("id")):
            entry = {"offset_s": float(off), "ts": time.time(), "total": None,
                     "skipped": "다른 실행 진행 중 — 계정 전체 스캔 비교 불가"}
            with _LOCK:
                rec.setdefault("rescans", []).append(entry)
            _persist_rescans(rec)
            try:
                with open(rec["log"], "a", encoding="utf-8") as f:
                    f.write(f"\n=== 종료 후 재스캔 {label}: 건너뜀 "
                            "(다른 실행 진행 중) ===\n")
            except OSError:
                pass
            continue
        try:
            owned = scan()
            from collections import Counter
            entry = {"offset_s": float(off), "ts": time.time(),
                     "total": len(owned),
                     "by_service": dict(Counter(o.get("service", "?")
                                                for o in owned))}
        except Exception as exc:  # noqa: BLE001 — record the failure, keep going
            entry = {"offset_s": float(off), "ts": time.time(),
                     "total": None, "error": str(exc)}
        with _LOCK:
            rec.setdefault("rescans", []).append(entry)
        line = (f"\n=== 종료 후 재스캔 {label}: "
                + (f"{entry['total']}건" if entry.get("total") is not None
                   else f"실패 ({entry.get('error', '')[:80]})") + " ===\n")
        if entry.get("total") is not None:
            if base is None:
                base = entry["total"]
            elif entry["total"] > base:
                delta = entry["total"] - base
                with _LOCK:
                    rec["late_alert"] = {
                        "delta": delta, "base": base, "found": entry["total"],
                        "offset_s": float(off),
                        "msg": (f"⚠ 종료 후 자원 늦출현 {delta}건 — "
                                "비동기 생성물 의심 (+0 스캔 이후 나타남)")}
                line += (f"⚠ 종료 후 자원 늦출현 {delta}건 — 비동기 생성물 의심 "
                         f"(+0 스캔 {base}건 → {entry['total']}건)\n")
        _persist_rescans(rec)
        try:
            with open(rec["log"], "a", encoding="utf-8") as f:
                f.write(line)
        except OSError:
            pass


def _schedule_post_run_rescans(rec: dict, offsets=_RESCAN_OFFSETS_S,
                               anchor=None) -> None:
    if os.environ.get("SCP_POST_RUN_RESCAN", "true").strip().lower() == "false":
        return
    threading.Thread(target=_post_run_rescans, args=(rec, offsets),
                     kwargs={"anchor": anchor}, daemon=True).start()


def _resume_pending_rescans() -> int:
    """Re-arm the +0/+5m/+15m rescan offsets a server restart dropped (persona
    2차 수용: the +15m scan silently never ran — ``_rehydrate_runs`` restored the
    run but nothing re-scheduled its rescans). For each rehydrated finished live
    run whose end is recent enough that unexecuted offsets are still meaningful
    (< offset + grace), schedule the MISSING offsets anchored to the run's end —
    overdue ones fire immediately, future ones at their original wall time.
    Returns the number of runs re-armed."""
    if os.environ.get("SCP_POST_RUN_RESCAN", "true").strip().lower() == "false":
        return 0
    now = time.time()
    with _LOCK:
        recs = [r for r in _RUNS.values()
                if r.get("rehydrated") and r.get("kind") == "lifecycle"
                and r.get("mode") == "live" and r.get("rc") is not None
                and not r.get("runner_missing") and r.get("ended")]
    n = 0
    for rec in recs:
        ended = float(rec.get("ended") or 0)
        done = {float(e.get("offset_s", -1)) for e in rec.get("rescans") or []}
        planned = [float(o) for o in (rec.get("rescan_offsets")
                                      or _RESCAN_OFFSETS_S)]
        missing = [o for o in planned if o not in done
                   and now - (ended + o) < _RESCAN_RESUME_GRACE_S]
        if not missing:
            continue
        _schedule_post_run_rescans(rec, offsets=tuple(missing), anchor=ended)
        n += 1
    return n


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


def _kill_proc_tree(proc, grace_s: float = 10.0) -> None:
    """SIGTERM the child's whole process group (it was started with
    ``start_new_session=True``, so pgid == pid == every xdist worker), give it a
    short grace to flush, then SIGKILL whatever survives. Never raises."""
    try:
        pgid = os.getpgid(proc.pid)
    except Exception:  # noqa: BLE001 — already gone
        pgid = None
    try:
        if pgid is not None:
            os.killpg(pgid, signal.SIGTERM)
        else:
            proc.terminate()
    except Exception:  # noqa: BLE001
        pass
    deadline = time.monotonic() + grace_s
    while time.monotonic() < deadline:
        if proc.poll() is not None:
            return
        time.sleep(0.25)
    try:
        if pgid is not None:
            os.killpg(pgid, signal.SIGKILL)
        else:
            proc.kill()
    except Exception:  # noqa: BLE001
        pass


def _abort_run(rid: str) -> tuple[int, dict]:
    """중단 버튼의 서버 반쪽 — abort a LOCAL run (persona 2차 수용: 로컬 run 은
    실행 중 중단 수단이 전혀 없었다). Returns ``(http_code, payload)``.

    * queued run  → dequeue + mark aborted (nothing was started).
    * running LIVE lifecycle run → set ``abort_requested`` and kill the pytest
      process tree; ``_run_worker`` then runs the teardown paths (shared-VPC
      teardown + run-scoped reconciler sweep) and records status ``aborted``.
    * anything else (simulate/cleanup/verify/owned, already-ended) → 409.
    """
    with _LOCK:
        rec = _RUNS.get(rid)
    if not rec:
        return 404, {"error": "no such run"}
    status = rec.get("status")
    if status == "queued":
        with _ADMIT:
            if rid in _QUEUE:
                _QUEUE.remove(rid)
            _PENDING.pop(rid, None)
        with _LOCK:
            rec["abort_requested"] = True
            rec["status"], rec["ended"] = "aborted", time.time()
            if rec.get("rc") is None:
                rec["rc"] = 1
        try:
            with open(rec["log"], "a", encoding="utf-8") as f:
                f.write(f"\n=== 실행 중단(aborted) ts={time.time():.3f} — "
                        "대기 큐에서 제거됨 (시작 전) ===\n")
        except OSError:
            pass
        _record_run_to_db(rec)
        _try_admit_queue()
        return 202, _rec_view(rec)
    if status != "running":
        return 409, {"error": f"이미 종료된 실행입니다 (status={status}) — "
                              "중단할 수 없습니다."}
    if rec.get("kind") != "lifecycle" or rec.get("mode") != "live":
        return 409, {"error": "이 기록 종류는 중단을 지원하지 않습니다 — "
                              "스캔/시뮬레이션/클린업은 짧은 읽기·정리 작업입니다."}
    with _LOCK:
        already = bool(rec.get("abort_requested"))
        rec["abort_requested"] = True
        proc = rec.get("_proc")
    if proc is not None:
        threading.Thread(target=_kill_proc_tree, args=(proc,),
                         daemon=True).start()
    return 202, {"id": rid, "status": "aborting",
                 "already_requested": already,
                 "note": ("pytest 프로세스 트리 종료 중 — teardown 스윕 후 "
                          "'중단됨(aborted)' 으로 기록됩니다."
                          if proc is not None else
                          "provision 단계 — pytest 시작 전에 중단됩니다.")}


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
_VPCCNT = {"n": 0, "ts": 0.0, "rows": []}   # cached live account VPC count (+rows)


def _vpc_cap() -> int:
    from core import budgets
    return int(budgets.Budget().limits.get("vpc", 5))


def _account_vpc_count(ttl: float = 12.0) -> int:
    """Live account VPC count via a read-only LIST (cached; best-effort). Also
    caches the first page's ``{id, name}`` rows so the capacity view can key
    '내 실행' attribution on the run's known resource ids (신규10)."""
    now = time.time()
    if now - _VPCCNT["ts"] < ttl:
        return _VPCCNT["n"]
    try:
        from core.config import Settings
        from core.http_client import ApiClient
        r = ApiClient(Settings()).get("/v1/vpcs", params={"size": 100}, service="vpc",
                                      timeout=10, retry=False)
        if getattr(r, "ok", False):
            body = r.body if isinstance(r.body, dict) else {}
            items = body.get("contents") or body.get("vpcs") or []
            if not isinstance(items, list):
                items = []
            n = body.get("totalCount")
            if n is None:
                n = len(items)
            rows = [{"id": str(it.get("id", "")), "name": str(it.get("name", ""))}
                    for it in items if isinstance(it, dict)]
            _VPCCNT.update(n=int(n), ts=now, rows=rows)
    except Exception:  # noqa: BLE001 — budget view is best-effort, never crash a run
        pass
    return _VPCCNT["n"]


def _mine_resource_keys() -> tuple[set, set]:
    """(ids, names) attributable to THIS console's runs: the per-run local
    resource index (rec events + registry shards) plus each run's shared-VPC id
    (the provision step stamps ``rec["shared_vpc_id"]``). Keys the capacity
    view's '내 실행' classification so a run-held shared VPC never drifts to
    '기존' (신규10)."""
    ids: set = set()
    names: set = set()
    try:
        for entry in _local_res_index().values():
            ids |= set(entry.get("ids") or ())
            names |= set(entry.get("names") or ())
    except Exception:  # noqa: BLE001
        pass
    with _LOCK:
        for r in _RUNS.values():
            if r.get("shared_vpc_id"):
                ids.add(str(r["shared_vpc_id"]))
    return ids, names


def _mine_live_vpcs() -> int:
    """How many of the account's live VPCs belong to MY runs (id/name join
    against the cached VPC rows). Best-effort — 0 when rows are unavailable."""
    ids, names = _mine_resource_keys()
    rows = _VPCCNT.get("rows") or []
    return sum(1 for row in rows
               if (row.get("id") and row["id"] in ids)
               or (row.get("name") and row["name"] in names))


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
    # '내 실행' 귀속 (신규10): live VPCs whose id/name matches MY runs' known
    # resources never count as '기존' — even between runs (a still-deleting
    # shared VPC used to be absorbed into the baseline on resync).
    mine_live = _mine_live_vpcs()
    with _ADMIT:
        if not _RESERVED:
            _BASELINE = max(0, acct - mine_live)
        base, reserved = _BASELINE, sum(_RESERVED.values())
        running, queued = list(_RESERVED.keys()), list(_QUEUE)

    def _v(rid: str) -> dict:
        r = _RUNS.get(rid)
        return _rec_view(r) if r else {"id": rid}
    # headroom: my footprint = max(reservations, actually-live mine) — a lingering
    # (still-deleting) my-VPC occupies a real slot even with nothing reserved.
    return {"cap": cap, "baseline": base, "reserved": reserved, "account_live": acct,
            "mine_live": mine_live,
            "headroom": max(0, cap - base - max(reserved, mine_live)),
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
    -n. Delegates to the SHARED pipeline (regression.scenarios.local_run) — one
    implementation for this dev server and the controlplane 'local' executor. That
    version streams the provision output line-by-line into the log (no more frozen
    '=== provision shared VPC ===' header for the whole ACTIVE wait) and narrates
    provision-start/-end into the run's live-event stream."""
    from regression.scenarios import local_run
    return local_run.provision_shared(env, f)


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
           # line-buffer the child's stdout so the 로그 tab live-tails step output
           # instead of seeing block-buffered bursts (F3 · 로그 라이브 tail)
           "PYTHONUNBUFFERED": "1",
           # stamp the run-rec id so oplog resource events land under
           # runs/<rec id>/res/* — the /runtime origin join (scope=mine) keys off it.
           #
           # Root-caused 2026-07-04 ("local runs don't mirror res events"): they DO
           # once this stamp is in the worker env — live-verified: run
           # 20260704-113744-7350 wrote runs/20260704-113744-7350/res/*.json batches
           # (compute-virtualserver-full creates incl. res_id+name). The blank
           # mine-scope came from two inherent lags, not a missing emitter:
           #   (a) a console server process started BEFORE this stamp existed keeps
           #       serving runs whose oplog events file under runs/local/ instead;
           #   (b) the engine emits 'created' only after the create's polling
           #       completes and the id is captured — minutes after loggingaudit
           #       already shows Create Start — so early in a run the bucket has
           #       nothing to join yet.
           # Both are why /runtime attribution now reads the IN-PROCESS records
           # (_local_res_index) first and treats the bucket join as CI-only garnish.
           "APITEST_RUN_ID": rec["id"],
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
            if shared.get("SCP_SHARED_VPC_ID"):
                # run-keyed shared-VPC ownership: the capacity view uses this to
                # keep the shared VPC under '내 실행' (신규10), never '기존'.
                with _LOCK:
                    rec["shared_vpc_id"] = shared["SCP_SHARED_VPC_ID"]
            f.write("\n=== pytest === (수집 → xdist 워커 기동 — 첫 step 로그까지 보통 수십 초)\n")
            f.flush()
            pos = f.tell()      # remember where the pytest output begins
            if rec.get("abort_requested"):
                # aborted while provisioning — never start pytest
                rc = -1
                f.write("(중단 요청됨 — pytest 시작 전 중단)\n")
                f.flush()
            else:
                # Popen in its OWN session (process group) so the 중단 버튼 can
                # kill the whole pytest tree (xdist workers included), not just
                # the leader — subprocess.run gave us no handle at all.
                proc = subprocess.Popen(
                    [sys.executable, "-m", "pytest", "tests/crud", "-m", "crud",
                     "-n", n, "-o", "addopts=", "-q"],
                    cwd=str(ROOT), env={**env, **shared}, stdout=f,
                    stderr=subprocess.STDOUT, start_new_session=True)
                with _LOCK:
                    rec["_proc"] = proc
                try:
                    rc = proc.wait()
                finally:
                    with _LOCK:
                        rec.pop("_proc", None)
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
            aborted = bool(rec.get("abort_requested"))
            runner_missing = (not aborted) and _pytest_did_not_run(rc, pytest_out)
            if aborted:
                # 실행 중단 (사용자 요청): the pytest tree was killed mid-flight,
                # so the lifecycles' own teardown never finished — run the
                # EXISTING cleanup paths: precise shared-VPC teardown + a
                # run-scoped reconciler sweep (the own-run TTL override in
                # cleanup.reconciler._is_deletable reaps exactly this run's
                # tagged leftovers via APITEST_RUN_ID; other runs' live
                # resources keep their TTL protection — Hard Rule 3).
                f.write(f"\n=== 실행 중단(aborted) ts={time.time():.3f} — "
                        "pytest 프로세스 트리 종료됨 ===\n")
                f.flush()
                _teardown_shared(env, shared, f)
                f.write("\n=== teardown 스윕 (run-scoped: 이 run 의 잔존 정리) ===\n")
                f.flush()
                subprocess.run(
                    [sys.executable, "-m", "cleanup.reconciler"], cwd=str(ROOT),
                    env={**env, "SCP_ALLOW_MUTATIONS": "true",
                         "SCP_ALLOW_DESTRUCTIVE": "true",
                         "SCP_SWEEP_NOWAIT": "true"},
                    stdout=f, stderr=subprocess.STDOUT)
                f.write("\n=== 중단 처리 완료 — 실측 재스캔 예약됨 ===\n")
                f.flush()
            elif runner_missing:
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
                # honest wording (신규1): teardown was ATTEMPTED — async creations
                # can still materialize later, so the measured re-scans below are
                # the actual verdict, not this line.
                f.write("\n=== per-run cleanup: teardown-scoped ===\n"
                        "  teardown 시도 완료 — 이 실행이 만든 자원의 라이프사이클 teardown 을 "
                        "수행했습니다. 실측 재스캔 예약됨 (+0 · +5m · +15m; 비동기 생성물 감시).\n"
                        "  계정 전체 reconciler 청소는 자동 실행하지 않음 — '강제 클린업'(POST "
                        "/api/cleanup) 버튼으로 수동 실행하세요 (run-scoped 청소를 reconciler 가 "
                        "지원하지 않기 때문).\n")
                f.flush()
        with _LOCK:
            rec["status"] = "aborted" if aborted else "done"
            rec["rc"], rec["ended"] = rc, time.time()
            rec["runner_missing"] = runner_missing
        rec["events_summary"] = _events_summary(_read_events(rec["events"]))
        _record_run_to_db(rec)                     # Reporting ▸ 실행 기록 (P2-9)
        if not runner_missing:
            _schedule_post_run_rescans(rec)        # +0/+5m/+15m owned re-scans
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
           "SCP_SWEEP_NOWAIT": "true", "APITEST_RUN_ID": rec["id"]}
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


def _known_stuck_entries() -> list[dict]:
    """data/baselines/known_issues.json ``stuck_resources`` — documented residues
    that CANNOT be deleted via API. Same source /testing/resources folds (신규8)."""
    try:
        data = json.loads((ROOT / "data" / "baselines" / "known_issues.json")
                          .read_text(encoding="utf-8"))
        return [e for e in data.get("stuck_resources", []) if isinstance(e, dict)]
    except Exception:  # noqa: BLE001
        return []


def _annotate_known_stuck(owned: list[dict]) -> int:
    """Tag owned items that match a documented stuck resource (by id or name in
    the delete path / bulk-ids body) with ``known_stuck``; return the match count.
    The UI folds these ('기지 항목') and keeps them OUT of the red count — same
    folding /testing/resources applies (신규8)."""
    stuck = _known_stuck_entries()
    n = 0
    for o in owned:
        path = o.get("path", "") or ""
        body = o.get("json") if isinstance(o.get("json"), dict) else {}
        ids = [str(x) for x in (body.get("ids") or [])]
        for e in stuck:
            eid, nm = str(e.get("id", "")), str(e.get("name", ""))
            if (eid and (eid in path or eid in ids)) or (nm and nm in path):
                o["known_stuck"] = {"name": nm, "reason": e.get("reason", "")}
                n += 1
                break
    return n


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
        errs: list = []
        owned = scan_owned(list_errors=errs)
        if not owned and errs:
            # 0건 could be "the LISTs failed" — never present that as clean
            raise RuntimeError(
                f"스캔 불완전 — {len(errs)}개 컬렉션 LIST 실패 "
                f"(첫 실패: {errs[0].get('service')} {errs[0].get('error')})")
        n_stuck = _annotate_known_stuck(owned)     # 기지 항목 (folded, not red)
        from collections import Counter
        by_svc = Counter(o["service"] for o in owned)
        with open(logp, "w", encoding="utf-8") as f:
            f.write(f"# console2 owned-resource scan {rec['id']} (read-only LIST inventory)\n\n")
            if not owned:
                f.write("NONE — every swept collection is empty of owned resources ✅\n")
            for svc, n in by_svc.most_common():
                f.write(f"  {svc:18} {n:3}\n")
            f.write(f"\nTOTAL owned survivors across all collections: {len(owned)}\n")
            if n_stuck:
                f.write(f"  (documented known-stuck among them: {n_stuck})\n")
        with _LOCK:
            rec["owned"] = owned
            rec["owned_total"] = len(owned)
            rec["owned_known_stuck"] = n_stuck
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
    if rec.get("status") == "aborted":
        return "⏹ 중단됨 — 사용자 요청 (teardown 스윕 수행)"
    if kind == "owned":
        if rec.get("status") == "error":
            return f"⚠️ 스캔 실패: {str(rec.get('error'))[:60]}"
        n = rec.get("owned_total")
        if n is None:
            return ""
        stuck = int(rec.get("owned_known_stuck") or 0)
        active = max(0, n - stuck)                # red count EXCLUDES 기지 항목
        note = f" (기지 {stuck}건 제외)" if stuck else ""
        return (f"없음 ✅ — 남은 자원 0건{note}" if active == 0
                else f"⚠️ 남은 자원 {active}건{note}")
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
        # count only GENUINELY-removed resources (신규7): the per-round
        # "sweep done: N" tally can be inflated by deceptive 2xx deletes that
        # re-list next round; prefer the reconciler's genuine-removed lines.
        g = re.findall(r"genuine-removed:\s*(\d+)", log)
        if g:
            return f"🧹 {sum(int(x) for x in g)} resource(s) deleted (실측)"
        m = re.findall(r"sweep done:\s*(\d+) resource\(s\) deleted", log)
        return f"🧹 {sum(int(x) for x in m)} resource(s) deleted" if m else ""
    m = re.findall(r"\d+ (?:passed|failed|skipped|error)[^\n]*", log)  # pytest summary
    return m[-1] if m else ""


_REC_VIEW_KEYS = ("id", "kind", "mode", "status", "lifecycle_ids",
                  "heavy", "mutations", "destructive", "rc", "started",
                  "ended", "error", "runner_missing", "peak_vpcs", "queued",
                  "rehydrated", "rescans", "late_alert", "rescan_offsets",
                  "abort_requested")


def _rec_view(rec: dict, full: bool = False) -> dict:
    v = {k: rec.get(k) for k in _REC_VIEW_KEYS}
    if rec.get("kind") == "owned":   # expose the structured owned-resource inventory
        v["owned"] = rec.get("owned", [])
        v["owned_total"] = rec.get("owned_total")
        v["owned_known_stuck"] = rec.get("owned_known_stuck")
    if rec.get("kind") == "lifecycle" and rec.get("events_summary"):
        v["events_summary"] = rec.get("events_summary")
    # a terminal run's summary is stable — cache it so /api/runs doesn't re-read
    # every (possibly rehydrated) run's whole log on each 2s poll.
    if not full and rec.get("_summary_cache") is not None:
        v["summary"] = rec["_summary_cache"]
        return v
    log = ""
    if Path(rec["log"]).exists():
        try:
            log = open(rec["log"], encoding="utf-8").read()
        except Exception:
            log = ""
    v["summary"] = _summarize(rec, log)
    # summary text is rescan-independent (pytest tail / worker markers), so a
    # terminal rec can cache unconditionally — rescans only append log lines.
    if rec.get("status") in ("done", "error", "unknown", "aborted"):
        rec["_summary_cache"] = v["summary"]
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
                m["durations"] = _durations_view()   # now-playing 평균 ETA
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
                hours = float((q.get("hours") or ["1"])[0] or 1)
            except ValueError:
                hours = 1.0
            hours = hours if hours in _RUNTIME_HOURS else 1.0
            scope = (q.get("scope") or ["mine"])[0]
            deleted = (q.get("deleted") or ["hide"])[0]
            out, _ready = _runtime_view(hours, scope=scope, deleted=deleted)
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
        if p.startswith("/api/runs/") and p.endswith("/graph"):
            rid = p[len("/api/runs/"):-len("/graph")]
            with _LOCK:
                rec = _RUNS.get(rid)
            if not rec:
                return self._json(404, {"error": "no such run"})
            try:
                return self._json(200, {"id": rid, **_run_graph(rec)})
            except Exception as exc:  # noqa: BLE001
                return self._json(500, {"error": f"run graph failed: {exc}"})
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
                act = _active_live_run()
                if act:
                    return self._json(409, {
                        "error": ("이미 진행 중(또는 대기 중)인 LIVE 실행이 "
                                  f"있습니다 — run {act['id']} "
                                  f"({', '.join(act.get('lifecycle_ids') or [])[:120]}). "
                                  "동시 LIVE 실행은 자원 스캔·재스캔 판정을 "
                                  "오염시키므로 차단됩니다. 완료(또는 중단) 후 "
                                  "다시 시작하세요."),
                        "active_run": act["id"]})
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
        if p.startswith("/api/runs/") and p.endswith("/abort"):
            rid = p[len("/api/runs/"):-len("/abort")]
            code, payload = _abort_run(rid)
            return self._json(code, payload)
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
    # RETIRED (convergence S4): the standalone stdlib server is no longer started. The
    # console's engine (the builder/worker functions above) is served by the control-
    # plane spine via controlplane/console_api.py, and the console2 frontend is embedded
    # under controlplane Testing. Handler/ThreadingHTTPServer above are kept (unused) so
    # this retirement is trivially reversible; main() just prints how to run it now.
    print(
        "tools/console2_server.py — STANDALONE console2 server RETIRED (convergence S4).\n"
        "Run the console from the control-plane spine instead:\n\n"
        "    uvicorn controlplane.app:app --host 0.0.0.0 --port 8800\n"
        "    -> Testing (this console, embedded):  http://localhost:8800/testing/embed\n\n"
        "This module's builder/worker functions remain the shared engine — imported by\n"
        "controlplane.console_api (the /api/* routes) and console2.build_static."
    )


# Rehydrate run history from disk at import (신규2 · P2-9): a console/server
# restart used to erase all run records — /api/runs went blank even though the
# per-run .log/.events.jsonl files survive under reports/console2-runs/. Recs
# rehydrated here carry ``rehydrated: true`` (UI chip '복원됨'). Opt out with
# SCP_CONSOLE_REHYDRATE=false (tests use explicit run_dir args instead).
try:
    if os.environ.get("SCP_CONSOLE_REHYDRATE", "true").strip().lower() != "false":
        _rehydrate_runs()
        # restart-resume: re-arm the +5m/+15m rescans a restart dropped (신규1
        # follow-up — 일정 소실). Gated by SCP_POST_RUN_RESCAN like the original
        # scheduling; only offsets still within the grace window are re-armed.
        _resume_pending_rescans()
except Exception:  # noqa: BLE001 — history is a convenience, never block startup
    pass


if __name__ == "__main__":
    main()
