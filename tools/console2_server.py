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

Safety: identical opt-in to console_server / chat-heavy — mutation/destructive/
heavy gates are set PER RUN from the request only, never globally. Simulate makes
no cloud calls at all. Reuses the proven provision-shared-VPC → pytest → teardown
→ reconciler-sweep flow (same ``-m`` module invocations as console_server.py).
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import threading
import time
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
WEB = ROOT / "console2"
RUN_DIR = ROOT / "reports" / "console2-runs"
PORT = int(os.environ.get("PORT", "9100"))

_RUNS: dict[str, dict] = {}
_LOCK = threading.Lock()
_MODEL: dict | None = None
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


def _run_worker(rec: dict) -> None:
    """REAL run: provision shared VPC (heavy) -> pytest tests/crud with SCP_CRUD_IDS +
    the per-run safety gates + SCP_CONSOLE_EVENTS -> teardown shared -> reconciler sweep.
    The engine appends step-level events to rec['events'] (core.console_events)."""
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
            rc = subprocess.run(
                [sys.executable, "-m", "pytest", "tests/crud", "-m", "crud",
                 "-n", n, "-o", "addopts=", "-q"],
                cwd=str(ROOT), env={**env, **shared}, stdout=f, stderr=subprocess.STDOUT).returncode
            _teardown_shared(env, shared, f)
            f.write("\n=== reconciler sweep (cleanup) ===\n")
            f.flush()
            subprocess.run([sys.executable, "-m", "cleanup.reconciler"], cwd=str(ROOT),
                           env={**env, "SCP_SWEEP_NOWAIT": "true"}, stdout=f, stderr=subprocess.STDOUT)
        with _LOCK:
            rec["status"], rec["rc"], rec["ended"] = "done", rc, time.time()
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


def _simulate_worker(rec: dict) -> None:
    """DRY-RUN: replay the plan to the event stream (no cloud, deterministic). Walks
    the dag_planner waves in order and, within each wave, each lifecycle's HTTP steps,
    so the live view shows the real DAG order + the real API call sequence. Used to
    confirm ordering quickly before a real run. Also emits clearly-synthetic
    ``resource-tracked``/``resource-deleted`` events (ids prefixed ``sim-``) on
    create/delete steps so the resource-inventory report renders without any cloud."""
    evp, logp = rec["events"], Path(rec["log"])
    try:
        plan = _plan(rec["lifecycle_ids"])
        waves = plan["plan"]["waves"]
        preview = plan["preview"]
        with open(logp, "w", encoding="utf-8") as f:
            f.write(f"# console2 SIMULATE {rec['id']} — replay of the dag_planner plan "
                    f"(no cloud calls)\n{plan['summary']}\n")
        _emit_event(evp, "run-meta", mode="simulate", waves=len(waves),
                    runnable=plan["runnable"])
        # lifecycles in wave order (DAG order); inside a wave, sequential replay.
        for wi, w in enumerate(waves):
            _emit_event(evp, "wave-start", wave=wi, wave_kind=w["kind"],
                        lifecycles=w["lifecycles"], vpc_slots=w.get("vpc_slots", 0))
            for lid in w["lifecycles"]:
                pv = preview.get(lid) or {"steps": [], "service": "", "heavy": False}
                steps = [s for s in pv["steps"] if s.get("method")]  # HTTP steps only
                _emit_event(evp, "lifecycle-start", lifecycle=lid,
                            service=pv["service"], heavy=pv["heavy"],
                            n_steps=len(steps), wave=wi)
                for s in steps:
                    _emit_event(evp, "step-start", lifecycle=lid, step=s["name"],
                                method=s["method"], path=s["path"])
                    # Pace each HTTP step so the live view is WATCHABLE — the user
                    # can see 생성 중 → 테스트 중 → 삭제 중 advance through DAG order
                    # rather than the whole run flashing by in ~1s. Tunable via
                    # SCP_SIM_STEP_DELAY (seconds); default 0.35s per step.
                    time.sleep(_SIM_STEP_DELAY)
                    _emit_event(evp, "step-end", lifecycle=lid, step=s["name"],
                                method=s["method"], path=s["path"],
                                status=200, category="ok",
                                elapsed_ms=int(_SIM_STEP_DELAY * 1000))
                    # synthetic resource tracking (simulate-only; ids prefixed sim-)
                    # so the "자원 (실자원 id)" report renders without any cloud call.
                    # A short extra beat around create/delete so the resource view
                    # visibly steps create → test → delete (not all at once).
                    if s.get("kind") == "create":
                        rtype = _sim_resource_type(s["path"])
                        _emit_event(evp, "resource-tracked", lifecycle=lid,
                                    resource_type=rtype,
                                    resource_id="sim-" + uuid.uuid4().hex[:8],
                                    path=s["path"])
                        time.sleep(_SIM_BEAT)
                    elif s.get("kind") == "delete":
                        _emit_event(evp, "resource-deleted", lifecycle=lid,
                                    resource_type=_sim_resource_type(s["path"]),
                                    path=s["path"])
                        time.sleep(_SIM_BEAT)
                _emit_event(evp, "lifecycle-end", lifecycle=lid, status="passed")
        _emit_event(evp, "run-end", status="done")
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
    import re
    kind = rec.get("kind")
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
                                 "ended", "error")}
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
                return self._json(200, _model())
            except Exception as exc:  # noqa: BLE001
                return self._json(500, {"error": f"model build failed: {exc}"})
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
            mode = b.get("mode", "simulate")
            if mode == "live":
                # safety gates are explicit per-run opt-ins (Hard Rule 1): default
                # OFF so a bare live POST never mutates/deletes by omission — the
                # client must set each gate true.
                rec = _start("lifecycle", _run_worker, mode="live", lifecycle_ids=ids,
                             heavy=bool(b.get("heavy", False)),
                             mutations=bool(b.get("mutations", False)),
                             destructive=bool(b.get("destructive", False)))
            else:
                rec = _start("simulate", _simulate_worker, mode="simulate", lifecycle_ids=ids)
            return self._json(202, _rec_view(rec))
        if p == "/api/cleanup":
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
