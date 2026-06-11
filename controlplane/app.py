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

from fastapi import FastAPI, Form, HTTPException, Request, Response
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates

from controlplane import dashdata, db, dispatch, scheduler, snapshots, triage
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


def _catalog() -> dict:
    """Suites + profiles for the trigger forms (live from the repo files)."""
    return {
        "suites": [s.get("id") for s in core_suites.list_suites()],
        "profiles": [p.get("id") for p in core_profiles.list_profiles()],
        "dispatch_ok": dispatch.configured(),
        "triage_ok": triage.enabled(),
    }


def _render(request: Request, name: str, active: str, **ctx) -> HTMLResponse:
    return templates.TemplateResponse(request, name,
                                      {**_catalog(), "active": active, **ctx})


# --- home ----------------------------------------------------------------------

@app.get("/", response_class=HTMLResponse)
def home(request: Request):
    runs = db.list_runs(limit=5)
    running = [r for r in runs if r["status"] == "running"]
    return _render(request, "home.html", "home",
                   runs=runs, running=running,
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
                "services": len(services)}
    except Exception:
        return {"total": 0, "enabled": 0, "heavy": 0, "services": 0}


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


@app.get("/planning", response_class=HTMLResponse)
def planning(request: Request):
    return _render(request, "planning.html", "planning",
                   profile_list=core_profiles.list_profiles(),
                   suite_list=core_suites.list_suites(),
                   scenario_stats=_scenario_stats())


@app.get("/planning/scenarios", response_class=HTMLResponse)
def planning_scenarios(request: Request, service: str = ""):
    from regression.scenarios.loader import load_lifecycles
    lifecycles = load_lifecycles()
    if service:
        lifecycles = [l for l in lifecycles if service in (l.get("service") or "")]
    rows = [{
        "id": l.get("id"), "service": l.get("service", ""),
        "enabled": bool(l.get("enabled")), "heavy": bool(l.get("heavy")),
        "adopt": l.get("adopt", ""), "steps": len(l.get("steps") or []),
        "note": (l.get("_note") or "")[:160],
    } for l in lifecycles]
    return _render(request, "scenarios.html", "planning",
                   rows=rows, service=service)


@app.get("/planning/knowledge", response_class=HTMLResponse)
def planning_knowledge(request: Request):
    def listing(pattern: str) -> list[dict]:
        out = []
        for p in sorted(ROOT.glob(pattern)):
            if p.is_file():
                rel = p.relative_to(ROOT).as_posix()
                out.append({"rel": rel, "kb": round(p.stat().st_size / 1024, 1)})
        return out
    return _render(request, "knowledge.html", "planning",
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
                   rel=path, content=content[:400_000])


# --- Testing -------------------------------------------------------------------

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
                   runs=runs, live=live, schedules=db.list_schedules())


@app.get("/partials/runs", response_class=HTMLResponse)
def runs_partial(request: Request, limit: int = 15):
    return templates.TemplateResponse(request, "_runs_table.html",
                                      {"runs": db.list_runs(limit=limit)})


@app.post("/runs/trigger")
def trigger_run(suite: str = Form(""), profile: str = Form("")):
    ok, msg = dispatch.dispatch_run(suite, profile)
    db.create_run(suite, profile, trigger="manual", detail="" if ok else msg)
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


# --- Reporting -----------------------------------------------------------------

@app.get("/reporting", response_class=HTMLResponse)
def reporting(request: Request):
    hist = dashdata.history(limit=10)
    return _render(request, "reporting.html", "reporting",
                   coverage=dashdata.latest_coverage(),
                   trend=list(reversed(hist)),
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


# --- ingest (core/oplog.py mirror: APITEST_PLATFORM_URL) -----------------------

@app.post("/api/ingest/events")
async def ingest(request: Request):
    token = os.environ.get("PLATFORM_INGEST_TOKEN", "").strip()
    if token:
        auth = request.headers.get("authorization", "")
        if auth != f"Bearer {token}":
            raise HTTPException(401, "bad ingest token")
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
