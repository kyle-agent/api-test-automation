"""SCP API Regression Test Platform — control-plane server (M1 MVP).

Server-rendered FastAPI + htmx (docs/PLATFORM-PLAN.md §3), organized in the
plan's three areas:

  Planning   environments · suites · scenario catalog · knowledge (read-only
             views now; M3 adds editing on top)
  Testing    manual runs · schedules · live progress
  Reporting  run history · per-run dashboard snapshots · AI triage

Suites and environment profiles are read live from the repo files (suites/,
environments/); runs/schedules/events/triage live in SQLite (db.py).

Run from the repo root:
  pip install -r controlplane/requirements.txt
  uvicorn controlplane.app:app --host 0.0.0.0 --port 8800

Config (env): see controlplane/README.md.
"""
from __future__ import annotations

import json
import os
from contextlib import asynccontextmanager
from pathlib import Path
from urllib.parse import urlencode

from fastapi import FastAPI, Form, HTTPException, Request, Response
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates

from controlplane import (authoring, compare, dashdata, db, dispatch,
                          resources, scheduler, snapshots, triage)
from core import profiles as core_profiles
from core import suites as core_suites

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
templates = Jinja2Templates(directory=str(HERE / "templates"))


@asynccontextmanager
async def lifespan(app: FastAPI):
    scheduler.start()
    yield


app = FastAPI(title="SCP API Regression Test Platform", lifespan=lifespan)

# AI pipelines (M3 A1~A3) live in their own router — see controlplane/ai_routes.py
from controlplane import ai_routes  # noqa: E402  (import after app to match layout)
app.include_router(ai_routes.router)

# Resource-task-model form UI + composer screens (M5 R2b) — resource_routes.py
from controlplane import resource_routes  # noqa: E402
app.include_router(resource_routes.router)


def _catalog() -> dict:
    """Suites + profiles for the trigger forms (live from the repo files).
    ctx_snapshot feeds the header ctxbar — which published snapshot the
    numbers on screen come from (best-effort, degrades to None)."""
    return {
        "suites": [s.get("id") for s in core_suites.list_suites()],
        "profiles": [p.get("id") for p in core_profiles.list_profiles()],
        "dispatch_ok": dispatch.configured(),
        "triage_ok": triage.enabled(),
        "ctx_snapshot": dashdata.latest_coverage(),
    }


def _render(request: Request, name: str, active: str, **ctx) -> HTMLResponse:
    return templates.TemplateResponse(request, name,
                                      {**_catalog(), "active": active, **ctx})


# --- home ----------------------------------------------------------------------

@app.get("/", response_class=HTMLResponse)
def home(request: Request):
    runs = db.list_runs(limit=50)
    running = [r for r in runs
               if r["status"] in ("running", "dispatched")]
    today = db.now()[:10]
    runs_today = sum(1 for r in runs
                     if (r["requested_at"] or "").startswith(today))
    return _render(request, "home.html", "home",
                   runs=runs[:5], running=running, runs_today=runs_today,
                   schedules=db.list_schedules(),
                   coverage=dashdata.latest_coverage(),
                   scenario_stats=_scenario_stats())


# --- Planning ------------------------------------------------------------------

def _scenario_stats() -> dict:
    try:
        from regression.scenarios.loader import load_lifecycles
        ls = load_lifecycles()
        services = {l.get("service", "") for l in ls}
        return {"total": len(ls),
                "enabled": sum(1 for l in ls if l.get("enabled")),
                "heavy": sum(1 for l in ls if l.get("heavy")),
                "enabled_heavy": sum(1 for l in ls
                                     if l.get("enabled") and l.get("heavy")),
                "services": len(services)}
    except Exception:
        return {"total": 0, "enabled": 0, "heavy": 0, "enabled_heavy": 0,
                "services": 0}


# directories the read-only knowledge browser may serve
_BROWSE_DIRS = ("knowledge", "suites", "environments", "docs")


def _safe_repo_file(rel: str) -> Path | None:
    try:
        path = (ROOT / rel).resolve()
        path.relative_to(ROOT)
    except (ValueError, OSError):
        return None
    if not any(path.relative_to(ROOT).as_posix().startswith(d + "/")
               or path.relative_to(ROOT).as_posix() == d for d in _BROWSE_DIRS):
        return None
    return path if path.is_file() else None


def _model_stats() -> dict:
    """자원 모델 요약 — Plan 흐름 스트립의 ① 재료 칸."""
    try:
        from controlplane import resource_model
        nodes = resource_model.load_model() or {}
        groups = resource_model.load_groups() or {}
        validated = sum(1 for n in nodes.values()
                        if (n or {}).get("provenance") == "VALIDATED")
        return {"nodes": len(nodes), "validated": validated,
                "docs": len(nodes) - validated, "groups": len(groups)}
    except Exception:
        return {"nodes": 0, "validated": 0, "docs": 0, "groups": 0}


@app.get("/planning", response_class=HTMLResponse)
def planning(request: Request):
    rows = _scenario_rows()
    return _render(request, "planning.html", "planning",
                   profile_list=core_profiles.list_profiles(),
                   suite_list=core_suites.list_suites(),
                   scenario_stats=_scenario_stats(),
                   scenario_rows=rows,
                   model_stats=_model_stats(),
                   gen_count=sum(1 for r in rows if r["id"].startswith("gen-")),
                   disabled_count=sum(1 for r in rows if not r["enabled"]))


def _fragment_rel(source_name: str) -> str:
    """loader.load_lifecycles(with_sources=True) filename -> repo-relative path
    (the merge in regression/scenarios/loader.py is the mapping's source of
    truth: the base scenarios.json plus one fragment file per service)."""
    if source_name == "scenarios.json":
        return "regression/scenarios/scenarios.json"
    return f"regression/scenarios/lifecycles/{source_name}"


def _scenario_rows(service: str = "", note_chars: int = 300) -> list[dict]:
    """Catalog rows for the Plan-area tables (shared by /planning and
    /planning/scenarios)."""
    from regression.scenarios.loader import load_lifecycles
    lifecycles, sources = load_lifecycles(with_sources=True)
    if service:
        lifecycles = [l for l in lifecycles if service in (l.get("service") or "")]
    return [{
        "id": l.get("id"), "service": l.get("service", ""),
        "enabled": bool(l.get("enabled")), "heavy": bool(l.get("heavy")),
        "adopt": l.get("adopt", ""), "steps": len(l.get("steps") or []),
        "note": (l.get("_note") or "")[:note_chars],
        "file": _fragment_rel(sources.get(l.get("id"), "scenarios.json")),
    } for l in lifecycles]


@app.get("/planning/scenarios", response_class=HTMLResponse)
def planning_scenarios(request: Request, service: str = ""):
    return _render(request, "scenarios.html", "planning",
                   rows=_scenario_rows(service, note_chars=160), service=service)


@app.get("/knowledge", response_class=HTMLResponse)
@app.get("/planning/knowledge", response_class=HTMLResponse)
def planning_knowledge(request: Request):
    def listing(pattern: str) -> list[dict]:
        out = []
        for p in sorted(ROOT.glob(pattern)):
            if p.is_file():
                rel = p.relative_to(ROOT).as_posix()
                out.append({"rel": rel, "kb": round(p.stat().st_size / 1024, 1)})
        return out
    return _render(request, "knowledge.html", "knowledge",
                   narrative=listing("knowledge/*.md"),
                   formal=listing("knowledge/formal/*.yaml")
                          + listing("knowledge/formal/*.md")
                          + listing("knowledge/formal/services/*.yaml"),
                   suite_files=listing("suites/*.yaml"),
                   env_files=listing("environments/*.yaml"))


@app.get("/planning/view", response_class=HTMLResponse)
def planning_view(request: Request, path: str):
    f = _safe_repo_file(path)
    if not f:
        raise HTTPException(404, "file not found (or outside the browsable dirs)")
    try:
        content = f.read_text(errors="replace")
    except OSError:
        raise HTTPException(500, "unreadable")
    return _render(request, "file_view.html", "planning",
                   rel=path, content=content[:400_000],
                   editable=authoring.editable_path(path) is not None)


# --- Planning: 저작 편집기 (M3 §3.1 — 검증 → 쓰기 → 로컬 git 커밋) -----------------

@app.get("/planning/edit", response_class=HTMLResponse)
def planning_edit(request: Request, path: str, find: str = ""):
    f = authoring.editable_path(path)
    if not f or not f.is_file():
        raise HTTPException(404, "file not found (or outside the editable dirs)")
    if f.stat().st_size > 2_000_000:
        raise HTTPException(413, "file too large for the textarea editor")
    rel = f.relative_to(ROOT).as_posix()
    return _render(request, "editor.html", "planning",
                   rel=rel, content=f.read_text(errors="replace"),
                   find=find[:200], push=authoring.push_enabled())


@app.post("/planning/edit/validate", response_class=HTMLResponse)
def planning_edit_validate(request: Request, path: str = Form(...),
                           content: str = Form("")):
    result = authoring.propose_edit(path, content, validate_only=True)
    return templates.TemplateResponse(request, "_edit_result.html",
                                      {"result": result, "saved": False})


@app.post("/planning/edit/save", response_class=HTMLResponse)
def planning_edit_save(request: Request, path: str = Form(...),
                       content: str = Form("")):
    result = authoring.propose_edit(path, content)
    return templates.TemplateResponse(request, "_edit_result.html",
                                      {"result": result, "saved": result["ok"]})


# --- Planning: 의존 그래프 뷰 (M3 §2.3 — read-only, 편집은 원본 파일 편집기로) -------

def _cross_graph(cross: dict) -> dict:
    """cross-service.yaml resources -> layered boxes/arrows for an inline SVG
    (column = requires-depth; arrows point prerequisite -> dependent)."""
    res = {k: (v or {}) for k, v in (cross.get("resources") or {}).items()}
    memo: dict[str, int] = {}

    def depth(name: str, seen: tuple = ()) -> int:
        if name in memo:
            return memo[name]
        if name in seen:           # cycle — validator rejects it, degrade here
            return 0
        reqs = [r for r in res[name].get("requires") or [] if r in res]
        memo[name] = 0 if not reqs else 1 + max(
            depth(r, seen + (name,)) for r in reqs)
        return memo[name]

    cols: dict[int, list[str]] = {}
    for name in res:
        cols.setdefault(depth(name), []).append(name)
    BW, BH, XGAP, YGAP = 190, 38, 252, 50
    pos, nodes = {}, []
    for d in sorted(cols):
        for i, name in enumerate(sorted(cols[d],
                                        key=lambda n: (res[n].get("service", ""), n))):
            x, y = 12 + d * XGAP, 12 + i * YGAP
            pos[name] = (x, y)
            nodes.append({"id": name, "x": x, "y": y,
                          "service": res[name].get("service", ""),
                          "quota": res[name].get("quota", ""),
                          "provenance": res[name].get("provenance", ""),
                          "notes": (res[name].get("notes") or "")[:200]})
    edges = []
    for name, r in res.items():
        for req in r.get("requires") or []:
            if req in pos:
                (x1, y1), (x2, y2) = pos[req], pos[name]
                edges.append({"x1": x1 + BW, "y1": y1 + BH // 2,
                              "x2": x2, "y2": y2 + BH // 2})
    return {"nodes": nodes, "edges": edges, "bw": BW, "bh": BH,
            "w": 24 + (max(cols, default=0) + 1) * XGAP,
            "h": 24 + max((len(v) for v in cols.values()), default=1) * YGAP}


@app.get("/planning/dependencies", response_class=HTMLResponse)
def planning_dependencies(request: Request):
    import yaml
    deps, cross, load_errs = {}, {}, []
    try:
        deps = json.loads((ROOT / "regression" / "scenarios"
                           / "dependencies.json").read_text())
    except Exception as exc:
        load_errs.append(f"dependencies.json 읽기 실패: {exc}")
    try:
        cross = yaml.safe_load((ROOT / "knowledge" / "formal"
                                / "cross-service.yaml").read_text()) or {}
    except Exception as exc:
        load_errs.append(f"cross-service.yaml 읽기 실패: {exc}")
    try:
        from regression.scenarios.loader import load_lifecycles
        lifecycles = load_lifecycles()
    except Exception as exc:
        lifecycles, load_errs = [], load_errs + [f"lifecycle 로드 실패: {exc}"]
    sched = deps.get("vpc_schedule") or {}
    sim = authoring.vpc_peak(deps, lifecycles)
    vpc_paths = {str(p).split("?")[0].rstrip("/") for p, k
                 in (deps.get("budget_paths") or {"/v1/vpcs": "vpc"}).items()
                 if k == "vpc"}
    by_id = {l.get("id"): l for l in lifecycles}
    crud_rows = [{"id": lid, "creates": sum(
        1 for s in (by_id.get(lid, {}).get("steps") or [])
        if isinstance(s, dict) and str(s.get("method", "")).upper() == "POST"
        and str(s.get("path") or "").split("?")[0].rstrip("/") in vpc_paths
        and not s.get("adopt"))}
        for lid in sched.get("vpc_crud_lifecycles") or []]
    return _render(request, "dependencies.html", "planning",
                   load_errs=load_errs, sched=sched, sim=sim,
                   sim_warnings=authoring.vpc_quota_warnings(deps, lifecycles),
                   crud_rows=crud_rows,
                   fixed_ip_map={k: v for k, v in
                                 (sched.get("fixed_ip_map") or {}).items()
                                 if not k.startswith("_")},
                   quota_kinds=deps.get("quota_kinds") or {},
                   budget_paths=deps.get("budget_paths") or {},
                   graph=_cross_graph(cross),
                   cross_constraints=cross.get("cross_constraints") or [])


# --- Testing -------------------------------------------------------------------

def _run_preview_data() -> dict:
    """Per-suite/per-profile facts the RUN 조립 preview renders client-side.
    All numbers come from the real suite definitions + scenario catalog;
    durations are coarse buckets and labelled 대략치 in the UI."""
    stats = _scenario_stats()
    suites = {}
    for s in core_suites.list_suites():
        req = s.get("request") or {}
        mut, heavy = bool(req.get("mutations")), bool(req.get("heavy"))
        if not mut:                       # read-only probes sweep the catalog
            targets = stats["total"]
        elif heavy:
            targets = stats["enabled"]
        else:
            targets = stats["enabled"] - stats["enabled_heavy"]
        gates = [k for k in ("mutations", "destructive", "heavy", "conformance")
                 if req.get(k)]
        suites[s.get("id")] = {
            "label": s.get("label", ""), "targets": targets,
            "heavy": stats["enabled_heavy"] if heavy else 0,
            "gates": " + ".join(gates) if gates else "read-only",
            "eta": "~3–4시간" if heavy else ("~1시간" if mut else "~15–20분"),
        }
    profiles = {p.get("id"): {"label": p.get("label", ""),
                              "forbid": list(p.get("forbid") or [])}
                for p in core_profiles.list_profiles()}
    return {"suites": suites, "profiles": profiles}


@app.get("/testing", response_class=HTMLResponse)
def testing(request: Request):
    runs = db.list_runs(limit=15)
    running = [r for r in runs if r["status"] in ("running", "dispatched")]
    live = []
    for r in running:
        if r["gh_run_id"]:
            evs = db.list_events(r["gh_run_id"], kind="milestone", limit=50)
            live.append({"run": r, "milestones": evs})
    return _render(request, "testing.html", "testing",
                   runs=runs, live=live, schedules=db.list_schedules(),
                   preview=_run_preview_data())


@app.get("/partials/runs", response_class=HTMLResponse)
def runs_partial(request: Request, limit: int = 15):
    return templates.TemplateResponse(request, "_runs_table.html",
                                      {"runs": db.list_runs(limit=limit)})


@app.post("/runs/trigger")
def trigger_run(suite: str = Form(""), profile: str = Form(""),
                service: str = Form(""), crud_filter: str = Form("")):
    ok, msg = dispatch.dispatch_run(suite, profile, service, crud_filter)
    # narrowing options ride in detail as KEY=VALUE lines: traceability in the
    # UI, and in worker mode the worker merges them over the suite expansion
    lines = [f"{k}={v}" for k, v in (("service", service),
                                     ("crud_filter", crud_filter)) if v]
    if not ok:
        lines.append(msg)
    db.create_run(suite, profile, trigger="manual", detail="\n".join(lines))
    return RedirectResponse("/testing", status_code=303)


@app.post("/schedules")
def add_schedule(cron: str = Form(...), suite: str = Form(...),
                 profile: str = Form(""), note: str = Form("")):
    from croniter import croniter
    if not croniter.is_valid(cron):
        raise HTTPException(400, f"invalid cron expression: {cron!r}")
    db.add_schedule(cron, suite, profile, note)
    return RedirectResponse("/testing", status_code=303)


@app.post("/schedules/{schedule_id}/toggle")
def schedule_toggle(schedule_id: int):
    db.toggle_schedule(schedule_id)
    return RedirectResponse("/testing", status_code=303)


@app.post("/schedules/{schedule_id}/delete")
def schedule_delete(schedule_id: int):
    db.delete_schedule(schedule_id)
    return RedirectResponse("/testing", status_code=303)


# --- Testing: 리소스 인벤토리 + 단일 리소스 삭제 (M2 §2.5) -------------------------

@app.get("/testing/resources", response_class=HTMLResponse)
def testing_resources(request: Request, gh_run_id: str = "", msg: str = ""):
    rows = resources.inventory(gh_run_id or None)
    run_ids = [r["gh_run_id"] for r in db.list_runs(limit=50) if r["gh_run_id"]]
    return _render(request, "resources.html", "testing",
                   rows=rows, gh_run_id=gh_run_id, msg=msg[:300],
                   live_count=sum(1 for r in rows if r["live"]),
                   destructive=resources.destructive_enabled(),
                   run_ids=run_ids)


@app.post("/testing/resources/delete")
def testing_resource_delete(gh_run_id: str = Form(""), service: str = Form(""),
                            kind: str = Form(""), res_id: str = Form(...),
                            name: str = Form(""), lifecycle: str = Form(""),
                            filter_run: str = Form("")):
    if not resources.destructive_enabled():
        msg = ("SCP_ALLOW_DESTRUCTIVE=true 미설정 — 삭제가 차단되었습니다 "
               "(서버 환경변수로 활성화 후 재시도).")
    else:
        ok, msg = resources.delete_resource(service, kind, res_id, name=name)
        # the attempt itself is part of the run's resource history
        resources.record_attempt(gh_run_id, service=service, kind=kind,
                                 res_id=res_id, name=name, lifecycle=lifecycle,
                                 ok=ok, message=msg)
        msg = f"{kind} {name or res_id}: {msg}"
    q = urlencode({"gh_run_id": filter_run, "msg": msg})
    return RedirectResponse(f"/testing/resources?{q}", status_code=303)


# --- 개입 명령 (M2 명령 채널 — UI가 쌓고 엔진이 폴링/ack) ---------------------------

COMMAND_ACTIONS = ("abort_run", "skip_scenario", "stop_polling")


@app.post("/runs/{gh_run_id}/commands")
def add_run_command(gh_run_id: str, action: str = Form(...), target: str = Form("")):
    if action not in COMMAND_ACTIONS:
        raise HTTPException(400, f"unknown command action {action!r}")
    if action == "skip_scenario" and not target.strip():
        raise HTTPException(400, "skip_scenario 명령에는 lifecycle id(target)가 필요합니다")
    db.add_command(gh_run_id, action, target.strip())
    return RedirectResponse(f"/runs/{gh_run_id}", status_code=303)


# --- Reporting -----------------------------------------------------------------

REPORT_TABS = (("summary", "Summary"), ("coverage", "Coverage"),
               ("conformance", "Conformance"), ("trends", "Runs & Trends"),
               ("triage", "Triage"))


def _spark_paths(hist: list[dict], w: int = 600, h: int = 90) -> dict:
    """history rows (oldest first) -> SVG path strings for the trend
    sparkline: pass rate (ok/tested), C3 verified, C1 reachable."""
    def series(fn):
        vals = [fn(r) for r in hist]
        vals = [v for v in vals if v is not None]
        if len(vals) < 2:
            return ""
        n = len(vals)
        return " ".join(f"{'M' if i == 0 else 'L'}{i / (n - 1) * w:.1f},"
                        f"{h - (min(max(v, 0), 100) / 100 * h):.1f}"
                        for i, v in enumerate(vals))
    def pass_rate(r):
        tested = r.get("tested") or 0
        return (r.get("ok") or 0) * 100.0 / tested if tested else None
    return {"pass": series(pass_rate),
            "c3": series(lambda r: r.get("cov_c3")),
            "c1": series(lambda r: r.get("reachable_pct"))}


@app.get("/reporting", response_class=HTMLResponse)
def reporting(request: Request, tab: str = "summary"):
    if tab not in {t for t, _ in REPORT_TABS}:
        tab = "summary"
    hist = dashdata.history(limit=30)
    return _render(request, "reporting.html", "reporting",
                   tab=tab, tabs=REPORT_TABS,
                   coverage=dashdata.latest_coverage(),
                   trend=list(reversed(hist[-10:])),
                   spark=_spark_paths(hist), spark_n=len(hist),
                   conformance=dashdata.conformance_summary(),
                   catbars=dashdata.category_coverage(),
                   runs=db.list_runs(limit=100),
                   archive=snapshots.archive_index(limit=100))


@app.get("/dashboard/{rel_path:path}")
def current_dashboard(rel_path: str = ""):
    """The CURRENT published dashboard (dashboard-data branch) — coverage
    included — served inside the platform so all screens live in one place."""
    got = dashdata.file(rel_path or "index.html")
    if not got:
        raise HTTPException(404, "dashboard-data branch unavailable (or no such file)")
    body, ctype = got
    return Response(content=body, media_type=ctype)


@app.get("/reporting/compare", response_class=HTMLResponse)
def reporting_compare(request: Request, a: str = "", b: str = ""):
    """Run A vs B diff (M2 §2.6) — joins both snapshots' observations."""
    run_ids = [r["gh_run_id"] for r in db.list_runs(limit=100) if r["gh_run_id"]]
    for row in snapshots.archive_index(limit=100):
        rid = str(row.get("run_id", ""))
        if rid and rid not in run_ids:
            run_ids.append(rid)
    result = a_missing = b_missing = None
    if a and b:
        a_obs = snapshots.observations(a)
        b_obs = snapshots.observations(b)
        a_missing, b_missing = not a_obs, not b_obs
        result = compare.diff(a_obs, b_obs)
    return _render(request, "compare.html", "reporting",
                   a=a, b=b, run_ids=run_ids, result=result,
                   a_missing=a_missing, b_missing=b_missing)


@app.get("/runs", include_in_schema=False)
def runs_legacy():
    return RedirectResponse("/reporting", status_code=307)


@app.get("/runs/{gh_run_id}", response_class=HTMLResponse)
def run_detail(request: Request, gh_run_id: str):
    run = db.get_run(gh_run_id)
    tri = db.get_triage(gh_run_id)
    tri_detail = None
    if tri and tri["detail"]:
        try:
            tri_detail = json.loads(tri["detail"])
        except ValueError:
            pass
    return _render(request, "run_detail.html", "reporting",
                   gh_run_id=gh_run_id, run=run,
                   meta=snapshots.meta(gh_run_id),
                   milestones=db.list_events(gh_run_id, kind="milestone"),
                   commands=db.list_commands(gh_run_id),
                   triage=tri, triage_detail=tri_detail)


@app.post("/runs/{gh_run_id}/triage", response_class=HTMLResponse)
def trigger_triage(request: Request, gh_run_id: str):
    if not triage.enabled():
        raise HTTPException(400, "triage disabled — set ANTHROPIC_API_KEY")
    try:
        triage.run_triage(gh_run_id)
    except Exception as exc:
        raise HTTPException(502, f"triage failed: {exc}")
    return RedirectResponse(f"/runs/{gh_run_id}", status_code=303)


# --- snapshot serving (per-run dashboard restore) ------------------------------

@app.get("/runs/{gh_run_id}/snapshot/{rel_path:path}")
def snapshot_file(gh_run_id: str, rel_path: str):
    got = snapshots.fetch(gh_run_id, rel_path)
    if not got:
        raise HTTPException(404, "snapshot file not found (or bucket unconfigured)")
    body, ctype = got
    return Response(content=body, media_type=ctype)


# --- engine-facing API (oplog 미러 ingest + M2 명령 채널) ------------------------

def _require_ingest_token(request: Request) -> None:
    """Shared bearer check — PLATFORM_INGEST_TOKEN guards every engine-facing
    endpoint (ingest / command poll / ack) with the same token."""
    token = os.environ.get("PLATFORM_INGEST_TOKEN", "").strip()
    if token:
        auth = request.headers.get("authorization", "")
        if auth != f"Bearer {token}":
            raise HTTPException(401, "bad ingest token")


@app.get("/api/runs/{gh_run_id}/commands")
def api_pending_commands(request: Request, gh_run_id: str):
    """Pending (un-acked) intervention commands — the engine polls this at
    step boundaries (PLATFORM-PLAN §2.5 명령 채널)."""
    _require_ingest_token(request)
    return {"commands": [
        {"id": c["id"], "action": c["action"], "target": c["target"]}
        for c in db.pending_commands(gh_run_id)]}


@app.post("/api/commands/{command_id}/ack")
def api_ack_command(request: Request, command_id: int):
    _require_ingest_token(request)
    if not db.ack_command(command_id):
        raise HTTPException(404, "no such command")
    return {"ok": True}


@app.post("/api/ingest/events")
async def ingest(request: Request):
    _require_ingest_token(request)
    try:
        payload = await request.json()
    except ValueError:
        raise HTTPException(400, "invalid JSON")
    kind = payload.get("kind", "")
    gh_run_id = str(payload.get("run_id", "")) or "unknown"
    db.attach_run(gh_run_id)
    if kind == "milestone":
        stage, status = payload.get("stage", ""), payload.get("status", "")
        db.insert_event(gh_run_id, "milestone", payload.get("ts", db.now()),
                        payload.get("job", ""), stage, status,
                        payload.get("detail", ""))
        db.apply_milestone(gh_run_id, stage, status, payload.get("detail", ""))
        if stage == "dashboard":
            triage.auto_triage(gh_run_id)
    elif kind == "resources":
        for ev in payload.get("events", [])[:500]:
            db.insert_event(
                gh_run_id, "resource", ev.get("ts", db.now()),
                stage=ev.get("action", ""), status=ev.get("status", ""),
                detail=json.dumps(ev, ensure_ascii=False))
    else:
        raise HTTPException(400, f"unknown kind {kind!r}")
    return {"ok": True}
