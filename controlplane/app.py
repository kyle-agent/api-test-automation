"""SCP API Regression Test Platform — control-plane server (M1 MVP).

Server-rendered FastAPI + htmx (docs/PLATFORM-PLAN.md §3). Suites and
environment profiles are read live from the repo files (suites/,
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

from controlplane import db, dispatch, scheduler, snapshots, triage
from core import profiles as core_profiles
from core import suites as core_suites

HERE = Path(__file__).resolve().parent
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


# --- pages -------------------------------------------------------------------

@app.get("/", response_class=HTMLResponse)
def index(request: Request):
    return templates.TemplateResponse(request, "index.html", {
        **_catalog(),
        "runs": db.list_runs(limit=15),
        "schedules": db.list_schedules(),
    })


@app.get("/runs", response_class=HTMLResponse)
def runs_page(request: Request):
    return templates.TemplateResponse(request, "runs.html", {
        **_catalog(),
        "runs": db.list_runs(limit=100),
        "archive": snapshots.archive_index(limit=100),
    })


@app.get("/partials/runs", response_class=HTMLResponse)
def runs_partial(request: Request, limit: int = 15):
    return templates.TemplateResponse(request, "_runs_table.html",
                                      {"runs": db.list_runs(limit=limit)})


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
    return templates.TemplateResponse(request, "run_detail.html", {
        **_catalog(),
        "gh_run_id": gh_run_id,
        "run": run,
        "meta": snapshots.meta(gh_run_id),
        "milestones": db.list_events(gh_run_id, kind="milestone"),
        "triage": tri,
        "triage_detail": tri_detail,
    })


# --- actions -----------------------------------------------------------------

@app.post("/runs/trigger")
def trigger_run(suite: str = Form(""), profile: str = Form("")):
    ok, msg = dispatch.dispatch_run(suite, profile)
    db.create_run(suite, profile, trigger="manual", detail="" if ok else msg)
    return RedirectResponse("/", status_code=303)


@app.post("/runs/{gh_run_id}/triage", response_class=HTMLResponse)
def trigger_triage(request: Request, gh_run_id: str):
    if not triage.enabled():
        raise HTTPException(400, "triage disabled — set ANTHROPIC_API_KEY")
    try:
        triage.run_triage(gh_run_id)
    except Exception as exc:
        raise HTTPException(502, f"triage failed: {exc}")
    return RedirectResponse(f"/runs/{gh_run_id}", status_code=303)


@app.post("/schedules")
def add_schedule(cron: str = Form(...), suite: str = Form(...),
                 profile: str = Form(""), note: str = Form("")):
    from croniter import croniter
    if not croniter.is_valid(cron):
        raise HTTPException(400, f"invalid cron expression: {cron!r}")
    db.add_schedule(cron, suite, profile, note)
    return RedirectResponse("/", status_code=303)


@app.post("/schedules/{schedule_id}/toggle")
def schedule_toggle(schedule_id: int):
    db.toggle_schedule(schedule_id)
    return RedirectResponse("/", status_code=303)


@app.post("/schedules/{schedule_id}/delete")
def schedule_delete(schedule_id: int):
    db.delete_schedule(schedule_id)
    return RedirectResponse("/", status_code=303)


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
