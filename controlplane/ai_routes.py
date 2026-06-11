"""AI pipeline routes (M3 §4-A1/A2/A3) — mounted under /ai.

Kept in its own APIRouter + templates (ai_*.html) so it can land without
touching app.py: the orchestrator wires ``app.include_router(ai_routes.router)``
at merge time. A local ``_render`` builds the same base-template context
(suites/profiles/dispatch_ok/triage_ok/active) that app._render provides.

Each POST runs its pipeline SYNCHRONOUSLY — a Claude call can take ~1 minute;
the UI says so next to each button.
"""
from __future__ import annotations

from pathlib import Path
from urllib.parse import urlencode

from fastapi import APIRouter, Form, HTTPException, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates

from controlplane import ai_pipelines, db, dispatch, triage
from core import profiles as core_profiles
from core import suites as core_suites

HERE = Path(__file__).resolve().parent
templates = Jinja2Templates(directory=str(HERE / "templates"))

router = APIRouter(prefix="/ai")


def _render(request: Request, name: str, **ctx) -> HTMLResponse:
    """app._render equivalent — base.html nav context, active=planning."""
    base = {
        "suites": [s.get("id") for s in core_suites.list_suites()],
        "profiles": [p.get("id") for p in core_profiles.list_profiles()],
        "dispatch_ok": dispatch.configured(),
        "triage_ok": triage.enabled(),
        "active": "planning",
        "ai_ok": ai_pipelines.enabled(),
    }
    return templates.TemplateResponse(request, name, {**base, **ctx})


def _rerun_query(ai: dict | None) -> str:
    """'이 범위로 실행' query string for /testing from an A1 rerun block."""
    rerun = (ai or {}).get("rerun") or {}
    params = []
    suites = rerun.get("suites") or []
    if suites:
        params.append(("suite", suites[0]))
    if rerun.get("service_filters"):
        params.append(("service_filters", ",".join(rerun["service_filters"])))
    if rerun.get("crud_filters"):
        params.append(("crud_filters", ",".join(rerun["crud_filters"])))
    return urlencode(params)


# --- entry page ------------------------------------------------------------------

@router.get("", response_class=HTMLResponse)
@router.get("/", response_class=HTMLResponse)
def ai_home(request: Request):
    run_ids = [r["gh_run_id"] for r in db.list_runs(limit=50) if r["gh_run_id"]]
    return _render(request, "ai_home.html",
                   services=ai_pipelines.list_catalog_services(),
                   run_ids=run_ids,
                   drafts=ai_pipelines.list_drafts())


# --- A1 spec-diff 영향 분석 --------------------------------------------------------

@router.post("/spec-impact", response_class=HTMLResponse)
def spec_impact(request: Request, rev: str = Form(""), old_path: str = Form("")):
    rev, old_path = rev.strip(), old_path.strip()
    if not rev and not old_path:
        raise HTTPException(400, "git rev 또는 이전 카탈로그 파일 경로가 필요합니다")
    try:
        result = ai_pipelines.spec_impact(old_path=old_path or None,
                                          rev=rev or None)
    except ValueError as exc:
        raise HTTPException(400, str(exc))
    return _render(request, "ai_spec_impact.html", r=result,
                   rerun_query=_rerun_query(result.get("ai")))


# --- A2 시나리오 초안 ---------------------------------------------------------------

@router.post("/scenario-draft", response_class=HTMLResponse)
def scenario_draft(request: Request, service: str = Form(...)):
    try:
        result = ai_pipelines.scenario_draft(service.strip())
    except ValueError as exc:
        raise HTTPException(400, str(exc))
    return _render(request, "ai_scenario_draft.html", r=result)


# --- A3 fact 추출 -------------------------------------------------------------------

@router.post("/extract-facts", response_class=HTMLResponse)
def extract_facts(request: Request, run_id: str = Form(...)):
    try:
        result = ai_pipelines.extract_facts(run_id.strip())
    except ValueError as exc:
        raise HTTPException(400, str(exc))
    return _render(request, "ai_facts.html", r=result)


# --- saved drafts ------------------------------------------------------------------

_KIND_TEMPLATE = {"spec-impact": "ai_spec_impact.html",
                  "scenario-draft": "ai_scenario_draft.html",
                  "facts": "ai_facts.html"}


@router.get("/drafts/{name}", response_class=HTMLResponse)
def view_draft(request: Request, name: str):
    draft = ai_pipelines.load_draft(name)
    if draft is None:
        raise HTTPException(404, "draft not found")
    draft.setdefault("draft_name", name)
    template = _KIND_TEMPLATE.get(draft.get("kind", ""))
    if not template:
        raise HTTPException(404, f"unknown draft kind {draft.get('kind')!r}")
    ctx = {"r": draft}
    if draft.get("kind") == "spec-impact":
        ctx["rerun_query"] = _rerun_query(draft.get("ai"))
    return _render(request, template, **ctx)
