"""자원 모델 폼 UI 라우트 (R2b) — /planning/resources 하위 (계약 C3).

ai_routes.py 선례 그대로: 자체 APIRouter + 자체 템플릿(resource_*.html)로
app.py를 건드리지 않고 착륙한다 — 오케스트레이터가 머지 시
``app.include_router(resource_routes.router)``를 배선한다.

  GET  /planning/resources              그룹 목록 -> 노드 표 (§3 UI 트리)
  GET  /planning/resources/compose      대상 멀티선택 + 분기/옵션 -> plan 미리보기
  POST /planning/resources/compose      plan 미리보기 / compose draft 저장 (C4)
  GET  /planning/resources/{node_id}    노드 폼 (raw YAML이 아닌 폼; 신규 노드 포함)
  POST /planning/resources/{node_id}/save  폼 -> yaml -> authoring.propose_edit

합성기(regression/scenarios/composer.py, 계약 C2)는 R2a가 병렬 작업 중 —
import 실패 시 "합성기 미탑재"로 degrade한다. run 연계는 기존 /runs/trigger
재사용 (crud_filter = 생성된 draft lifecycle id).
"""
from __future__ import annotations

import importlib
from pathlib import Path

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates

from controlplane import dispatch, resource_model, triage
from core import profiles as core_profiles
from core import suites as core_suites

HERE = Path(__file__).resolve().parent
templates = Jinja2Templates(directory=str(HERE / "templates"))

router = APIRouter(prefix="/planning/resources")


def _render(request: Request, name: str, **ctx) -> HTMLResponse:
    """app._render equivalent — base.html nav context, active=planning."""
    base = {
        "suites": [s.get("id") for s in core_suites.list_suites()],
        "profiles": [p.get("id") for p in core_profiles.list_profiles()],
        "dispatch_ok": dispatch.configured(),
        "triage_ok": triage.enabled(),
        "active": "planning",
    }
    return templates.TemplateResponse(request, name, {**base, **ctx})


# --- 합성기 (R2a 병렬 — import 가드) ------------------------------------------------

def _composer():
    """regression.scenarios.composer 모듈 또는 None (미탑재 degrade)."""
    try:
        return importlib.import_module("regression.scenarios.composer")
    except Exception:
        return None


# --- 표시용 변환 ---------------------------------------------------------------------

def _requires_summary(node: dict) -> str:
    parts = []
    for row in resource_model.requires_rows(node):
        if row["type"] == "one_of":
            parts.append(" | ".join(t.strip() for t in row["target"].split(",")))
        elif row["type"] == "count":
            parts.append(f"{row['target']}×{row['count']}")
        else:
            parts.append(row["target"])
    return ", ".join(parts) or "(없음)"


def _node_row(nid: str, node: dict) -> dict:
    return {"id": nid, "code": str(node.get("code") or ""),
            "service": str(node.get("service") or ""),
            "requires": _requires_summary(node),
            "options": len(((node.get("create") or {}).get("options")) or {}),
            "provenance": str(node.get("provenance") or "")}


def _grouped(model: dict, groups: dict) -> list[dict]:
    """그룹 키 -> {gid, label, category, nodes[]} 목록 (gid 정렬, 노드는 code순)."""
    by_gid: dict[str, list[dict]] = {}
    for nid in sorted(model):
        gid = resource_model.group_of(nid, model[nid])
        by_gid.setdefault(gid, []).append(_node_row(nid, model[nid]))
    out = []
    for gid in sorted(set(by_gid) | set(groups)):
        meta = groups.get(gid) or {}
        nodes = sorted(by_gid.get(gid, []), key=lambda r: (r["code"], r["id"]))
        out.append({"gid": gid, "label": str(meta.get("label") or ""),
                    "category": str(meta.get("category") or ""), "nodes": nodes})
    return out


def _one_of_branches(node: dict) -> list[str]:
    """one_of 분기 후보(노드별 합집합) — compose 화면의 분기 select."""
    branches: list[str] = []
    for r in node.get("requires") or []:
        if isinstance(r, dict) and "one_of" in r:
            for alt in r.get("one_of") or []:
                rid = alt.get("ref", "") if isinstance(alt, dict) else str(alt)
                if rid and rid not in branches:
                    branches.append(rid)
    return branches


def _compose_nodes(model: dict) -> list[dict]:
    rows = []
    for nid in sorted(model):
        node = model[nid]
        rows.append({**_node_row(nid, node),
                     "branches": _one_of_branches(node),
                     "opts": resource_model.options_rows(node)})
    return sorted(rows, key=lambda r: (r["code"] or "zzz", r["id"]))


def _plan_dict(plan) -> dict:
    """C2 Plan — dict 계약이지만 dataclass류여도 표시용으로 degrade."""
    if isinstance(plan, dict):
        return plan
    return dict(getattr(plan, "__dict__", {}) or {})


def _plan_rows(plan: dict) -> list[dict]:
    """order 항목 -> {action, node, detail} (모양을 모르는 채로도 표가 되게)."""
    rows = []
    for entry in plan.get("order") or []:
        if isinstance(entry, dict):
            action = str(entry.get("action") or entry.get("phase")
                         or entry.get("op") or "")
            node = str(entry.get("node") or entry.get("id")
                       or entry.get("target") or "")
            rest = {k: v for k, v in entry.items()
                    if k not in ("action", "phase", "op", "node", "id", "target")}
            detail = ", ".join(f"{k}={v}" for k, v in rest.items())
        else:
            action, node, detail = "", str(entry), ""
        rows.append({"action": action, "node": node, "detail": detail})
    return rows


# --- 그룹/노드 목록 -------------------------------------------------------------------

@router.get("", response_class=HTMLResponse)
@router.get("/", response_class=HTMLResponse)
def resource_list(request: Request):
    model, sources = resource_model.load_model(with_sources=True)
    groups = resource_model.load_groups()
    return _render(request, "resource_list.html",
                   groups=_grouped(model, groups),
                   total=len(model),
                   validated=sum(1 for n in model.values()
                                 if n.get("provenance") == "VALIDATED"),
                   files=sorted(set(sources.values())),
                   has_composer=_composer() is not None)


# --- 합성 (compose) — /{node_id}보다 먼저 선언해야 라우팅이 맞는다 ----------------------

def _compose_ctx(request: Request, *, selected=None, choices=None, options=None,
                 lifecycle_id="", plan=None, plan_error="", saved=None,
                 targets_error=""):
    model = resource_model.load_model()
    plan = _plan_dict(plan) if plan is not None else None
    return dict(nodes=_compose_nodes(model),
                selected=set(selected or []),
                choices=choices or {}, options=options or {},
                lifecycle_id=lifecycle_id,
                has_composer=_composer() is not None,
                plan=plan,
                plan_rows=_plan_rows(plan) if plan else [],
                plan_error=plan_error, saved=saved,
                targets_error=targets_error)


@router.get("/compose", response_class=HTMLResponse)
def compose_page(request: Request):
    preselected = request.query_params.getlist("targets")
    return _render(request, "resource_compose.html",
                   **_compose_ctx(request, selected=preselected))


@router.post("/compose", response_class=HTMLResponse)
async def compose_run(request: Request):
    form = await request.form()
    targets = [str(t) for t in form.getlist("targets")]
    choices: dict = {}
    options: dict = {}
    for key in form.keys():
        val = str(form.get(key) or "").strip()
        if not val:
            continue
        if key.startswith("choice__"):
            choices[key[len("choice__"):]] = val
        elif key.startswith("opt__"):
            parts = key.split("__", 2)
            if len(parts) == 3:
                options.setdefault(parts[1], {})[parts[2]] = val
    lifecycle_id = str(form.get("lifecycle_id") or "").strip()
    action = str(form.get("action") or "plan")

    def page(**kw):
        return _render(request, "resource_compose.html",
                       **_compose_ctx(request, selected=targets, choices=choices,
                                      options=options, lifecycle_id=lifecycle_id,
                                      **kw))

    if not targets:
        return page(targets_error="대상 노드를 1개 이상 선택하세요")
    mod = _composer()
    if mod is None:
        return page()  # 템플릿이 '합성기 미탑재' 안내를 보여준다

    model = resource_model.load_model()
    try:
        plan = mod.plan(targets, choices or None, options or None, model=model)
    except Exception as exc:
        return page(plan_error=f"plan 계산 실패: {exc}")

    saved = None
    if action == "save":
        try:
            lifecycle = mod.compose(targets, choices or None, options or None,
                                    model=model,
                                    lifecycle_id=lifecycle_id or None)
        except Exception as exc:
            return page(plan=plan, plan_error=f"compose 실패: {exc}")
        name, errs = resource_model.save_lifecycle_draft(lifecycle)
        if errs:
            return page(plan=plan, plan_error="; ".join(errs))
        saved = {"name": name,
                 "lifecycle_id": str(lifecycle.get("id") or ""),
                 "steps": len(lifecycle.get("steps") or [])}
    return page(plan=plan, saved=saved)


# --- 노드 폼 + 저장 -------------------------------------------------------------------

@router.get("/{node_id}", response_class=HTMLResponse)
def resource_form(request: Request, node_id: str, service: str = ""):
    if not resource_model.NODE_ID_RE.match(node_id):
        raise HTTPException(404, "잘못된 노드 id")
    model, sources = resource_model.load_model(with_sources=True)
    node = model.get(node_id)
    is_new = node is None
    node = node or {"service": service.strip(), "provenance": "docs"}
    return _render(request, "resource_form.html",
                   node_id=node_id, node=node, is_new=is_new,
                   file=sources.get(node_id, ""),
                   node_ids=sorted(model),
                   req_rows=resource_model.requires_rows(node),
                   opt_rows=resource_model.options_rows(node),
                   body_text=resource_model.body_text(node),
                   capture_text=resource_model.capture_text(node),
                   ready=node.get("ready") or {},
                   delete=node.get("delete") or {},
                   option_types=resource_model.OPTION_TYPES,
                   has_composer=_composer() is not None)


@router.post("/{node_id}/save", response_class=HTMLResponse)
async def resource_save(request: Request, node_id: str):
    if not resource_model.NODE_ID_RE.match(node_id):
        raise HTTPException(404, "잘못된 노드 id")
    form = await request.form()
    node, errors = resource_model.parse_form(form)
    if errors:
        result = {"ok": False, "errors": errors, "warnings": [],
                  "rel": "", "commit": "", "pushed": False, "file": ""}
    else:
        result = resource_model.save_node(node_id, node)
    return templates.TemplateResponse(
        request, "resource_save_result.html",
        {"result": result, "saved": result["ok"], "node_id": node_id})
