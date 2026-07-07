"""자원 모델 폼 UI 라우트 (R2b) — /planning/resources 하위 (계약 C3).

ai_routes.py 선례 그대로: 자체 APIRouter + 자체 템플릿(resource_*.html)로
app.py를 건드리지 않고 착륙한다 — 오케스트레이터가 머지 시
``app.include_router(resource_routes.router)``를 배선한다.

  GET  /planning/resources              그룹 목록 -> 노드 표 (§3 UI 트리)
  GET  /planning/resources/compose      대상 멀티선택 + 분기/옵션 -> plan 미리보기
  POST /planning/resources/compose      plan 미리보기 / compose draft 저장 (C4)
  GET  /planning/resources/{node_id}    노드 폼 (raw YAML이 아닌 폼; 신규 노드 포함)
  POST /planning/resources/{node_id}/save  폼 -> yaml -> authoring.propose_edit

합성기(regression/scenarios/composer.py, 계약 C2)는 이제 항상 탑재되어 있어
직접 import 한다. run 연계는 기존 /runs/trigger 재사용
(crud_filter = 생성된 draft lifecycle id).
"""
from __future__ import annotations

import re
from pathlib import Path

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse, Response
from fastapi.templating import Jinja2Templates

from regression.scenarios import composer
from controlplane import common, resource_model

HERE = Path(__file__).resolve().parent
templates = Jinja2Templates(directory=str(HERE / "templates"))

router = APIRouter(prefix="/planning/resources")


def _render(request: Request, name: str, **ctx) -> HTMLResponse:
    """app._render equivalent — shared base context (common.base_ctx, P1-3)
    so the ctxbar's published-snapshot line renders here too."""
    return templates.TemplateResponse(
        request, name, {**common.base_ctx("planning"), **ctx})


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
        service = str(node.get("service") or "")
        rows.append({**_node_row(nid, node),
                     "category": service.split("/")[0] if service else "(기타)",
                     "branches": _one_of_branches(node),
                     "opts": resource_model.options_rows(node)})
    return sorted(rows, key=lambda r: (r["category"], r["code"] or "zzz", r["id"]))


# --- 모델 지도(model map) 메타 — provenance/완성도 (계약 §4 Modeling overlay) -----------
#
# 그래프는 composer.graph_view(전체 lifecycle-bearing 노드)를 공유 렌더러로 그리고,
# overlay(id)는 이 meta로 색칠한다: VALIDATED=초록 · docs=주황 · 불완전=회색+badge.
# "불완전" = create.endpoint 부재 OR requires 참조 중 모델에 없는 노드(미해결)가 있음.

def _missing_requires(node: dict, known: set) -> list[str]:
    """이 노드 requires 중 모델에 (아직) 없는 대상 — 완성도 게이지의 결손."""
    miss: list[str] = []
    for r in node.get("requires") or []:
        if isinstance(r, str):
            if r not in known:
                miss.append(r)
        elif isinstance(r, dict) and "one_of" in r:
            alts = [(a.get("ref") if isinstance(a, dict) else a)
                    for a in (r.get("one_of") or [])]
            alts = [a for a in alts if a]
            if alts and not any(a in known for a in alts):
                miss.append("one_of(" + ", ".join(str(a) for a in alts) + ")")
        elif isinstance(r, dict) and "ref" in r:
            if r["ref"] not in known:
                miss.append(str(r["ref"]))
    return miss


def _map_meta(model: dict) -> tuple[list[str], dict]:
    """(targets, meta) — targets=생성 가능한(create.endpoint 보유) 노드 = 지도의 닻.
    meta[id] = {provenance, has_endpoint, no_api, gated, complete, missing[]}
    for overlay(). `no_api: true` 노드(예: scr-image — docker push 산물)는 생성
    endpoint가 '없는 게 맞는' 노드이므로 endpoint 부재를 불완전으로 세지 않는다
    (validate.py의 no_api 허용과 같은 판정 — UI만 다르게 세면 거짓 결손)."""
    known = set(model)
    targets: list[str] = []
    meta: dict[str, dict] = {}
    for nid in sorted(model):
        node = model[nid]
        has_ep = bool((node.get("create") or {}).get("endpoint"))
        no_api = bool(node.get("no_api"))
        missing = _missing_requires(node, known)
        meta[nid] = {
            "provenance": str(node.get("provenance") or "?"),
            "has_endpoint": has_ep,
            "no_api": no_api,
            "gated": str(node.get("gated") or ""),
            "missing": missing,
            "complete": (has_ep or no_api) and not missing,
        }
        if has_ep:
            targets.append(nid)
    return targets, meta


def _dependents_index(model: dict) -> dict[str, list[str]]:
    """역방향 의존: target_id -> [이 노드를 requires 하는 노드 id …] (정렬).
    노드 폼의 "나를 require 하는 노드" + 표의 dependents 카운트에 쓴다."""
    rev: dict[str, set] = {}
    for nid, node in model.items():
        for r in node.get("requires") or []:
            tgts: list[str] = []
            if isinstance(r, str):
                tgts = [r]
            elif isinstance(r, dict) and "ref" in r:
                tgts = [str(r["ref"])]
            elif isinstance(r, dict) and "one_of" in r:
                tgts = [(a.get("ref") if isinstance(a, dict) else a)
                        for a in (r.get("one_of") or [])]
            for t in tgts:
                if t:
                    rev.setdefault(str(t), set()).add(nid)
    return {k: sorted(v) for k, v in rev.items()}


# --- 카탈로그 인라인 (2026-07-07 오너 결정: Modeling이 Catalog를 흡수) -------------------
#
# 각 서비스 그룹 행에 "API N (모델됨 M · 미모델 K)" 집계 + 엔드포인트 드로어를 단다.
# 데이터 = catalog_routes._load_catalog()(카탈로그 단일 소스 재사용) × 모델 노드들의
# endpoint 참조(create/verify/ready/delete 어디에 있든 "METHOD /path" 문자열 —
# _walk_endpoints 가 노드 정의를 재귀로 훑는다).
#
# 분류 규칙 (미모델 과대계상 금지 — 애매하면 '미매핑'):
#   정규화: method 대문자 · 쿼리스트링 제거 · path 를 '/' 세그먼트로 나누고
#   '{...}' 를 포함한 세그먼트는 이름을 버리고 자리표시자 '{}' 로 치환
#   (catalog 의 {subnetId} 와 노드의 {subnet_id}·{vpc.vpc_id}·"stg{unique}" 가
#   같은 자리로 맞춰진다).
#   · 모델됨  — 정규화 키 (method, segments) 가 어떤 노드 endpoint 참조와 정확히 일치.
#   · 미매핑  — 정확 일치는 없지만 '호환 가능'한 참조가 있음: method·세그먼트 수가
#              같고 리터럴 세그먼트끼리는 전부 같으며, 최소 1개 자리에서 한쪽만
#              자리표시자(예: catalog {stage_name} vs 노드 리터럴 "dev"). 모델이
#              그 endpoint 를 구체값으로 치는지 다른 endpoint 인지 단정할 수
#              없으므로 미모델로 세지 않고 별도 버킷으로 뺀다.
#   · 미모델  — 위 둘 다 아님 (모델 어디에도 참조가 없음).

_EP_STR_RE = re.compile(r"^([A-Z]+)\s+(\S+)$")


def _walk_endpoints(obj):
    """노드 정의(dict/list 트리)에서 모든 'endpoint' 문자열을 yield."""
    if isinstance(obj, dict):
        for k, v in obj.items():
            if k == "endpoint" and isinstance(v, str):
                yield v
            elif isinstance(v, (dict, list)):
                yield from _walk_endpoints(v)
    elif isinstance(obj, list):
        for item in obj:
            yield from _walk_endpoints(item)


def _norm_endpoint(ep: str):
    """'METHOD /path' -> (METHOD, (seg, ...)) 정규화 키 (규칙은 위 주석). 실패 None."""
    m = _EP_STR_RE.match((ep or "").strip())
    if not m:
        return None
    method, path = m.group(1), m.group(2).split("?", 1)[0]
    segs = tuple("{}" if "{" in s else s
                 for s in path.strip("/").split("/") if s)
    return (method, segs) if segs else None


def _endpoint_ref_index(model: dict) -> dict:
    """정규화 키 -> 그 endpoint 를 참조하는 (첫) 노드 id — 드로어의 편집 딥링크용.
    노드의 service 와 무관하게 전역으로 모은다(교차 서비스 verify 도 '모델됨')."""
    idx: dict = {}
    for nid in sorted(model):
        for ep in _walk_endpoints(model[nid]):
            key = _norm_endpoint(ep)
            if key and key not in idx:
                idx[key] = nid
    return idx


def _catalog_by_service() -> dict:
    """catalog -> {'category/service': [{method, path, name}, ...]} (경로·method 정렬)."""
    from controlplane import catalog_routes
    by: dict = {}
    for e in catalog_routes._load_catalog():
        skey = (f"{e.get('category') or '(uncategorized)'}/"
                f"{e.get('service') or '(unknown)'}")
        by.setdefault(skey, []).append({
            "method": str(e.get("method") or "").upper(),
            "path": str(e.get("http_path") or e.get("path") or ""),
            "name": str(e.get("name") or ""),
        })
    for rows in by.values():
        rows.sort(key=lambda r: (r["path"], r["method"]))
    return by


def _classify_endpoints(eps: list[dict], ref_idx: dict) -> list[dict]:
    """카탈로그 엔드포인트마다 status(modeled|unmapped|unmodeled) + node 를 붙인다."""
    # 호환 검사용: (method, 세그먼트 수) -> [(segs, node_id)]
    by_shape: dict = {}
    for (method, segs), nid in ref_idx.items():
        by_shape.setdefault((method, len(segs)), []).append((segs, nid))
    out = []
    for ep in eps:
        key = _norm_endpoint(f"{ep['method']} {ep['path']}")
        status, node = "unmodeled", ""
        if key and key in ref_idx:
            status, node = "modeled", ref_idx[key]
        elif key:
            for segs, nid in by_shape.get((key[0], len(key[1])), []):
                if all(a == b or a == "{}" or b == "{}"
                       for a, b in zip(key[1], segs)):
                    status, node = "unmapped", nid
                    break
        out.append({**ep, "status": status, "node": node})
    return out


def _service_endpoint_stats(model: dict) -> dict:
    """'category/service' -> {api, modeled, unmodeled, unmapped} — svc 그룹 행 집계."""
    ref_idx = _endpoint_ref_index(model)
    stats: dict = {}
    for skey, eps in _catalog_by_service().items():
        rows = _classify_endpoints(eps, ref_idx)
        stats[skey] = {
            "api": len(rows),
            "modeled": sum(1 for r in rows if r["status"] == "modeled"),
            "unmodeled": sum(1 for r in rows if r["status"] == "unmodeled"),
            "unmapped": sum(1 for r in rows if r["status"] == "unmapped"),
        }
    return stats


def _modeling_rows(model: dict, meta: dict, deps_idx: dict) -> list[dict]:
    """Modeling 표 행 — 노드별 한 줄: 무엇이 모델링됐고 무엇이 결손인지 한눈에.
    필터/정렬은 클라이언트(JS)에서 이 행들 위에서 한다(서버는 단일 source)."""
    rows = []
    for nid in sorted(model):
        node = model[nid]
        m = meta[nid]
        service = str(node.get("service") or "")
        rows.append({
            "id": nid,
            "code": str(node.get("code") or ""),
            "service": service,
            "category": service.split("/")[0] if "/" in service else "",
            "provenance": m["provenance"],
            "complete": m["complete"],
            "has_endpoint": m["has_endpoint"],
            "no_api": m["no_api"],
            "gated": m["gated"],
            "missing": m["missing"],
            "requires": _requires_summary(node),
            "n_requires": len(node.get("requires") or []),
            "dependents": len(deps_idx.get(nid, [])),
            "options": len(((node.get("create") or {}).get("options")) or {}),
        })
    return rows


def _modeling_tree(rows: list[dict]) -> list[dict]:
    """Group the flat modeling rows into category ▸ service, each carrying an
    AUTHORING tally (완성/불완전/미검증) — the Modeling-specific lens that sets this
    view apart from Catalog's read-only endpoint inventory. Sorted, counts rolled up."""
    cats: dict[str, dict] = {}
    for r in rows:
        cat = r["category"] or "(기타)"
        svc = r["service"] or "(기타)"
        c = cats.setdefault(cat, {"category": cat, "services": {},
                                  "n": 0, "val": 0, "docs": 0, "inc": 0,
                                  "gated": 0})
        s = c["services"].setdefault(svc, {"service": svc, "nodes": [],
                                           "n": 0, "val": 0, "docs": 0,
                                           "inc": 0, "gated": 0})
        s["nodes"].append(r)
        for scope in (c, s):
            scope["n"] += 1
            if not r["complete"]:
                scope["inc"] += 1
            elif r["provenance"] == "VALIDATED":
                scope["val"] += 1
            elif r["gated"]:
                scope["gated"] += 1   # 할 수 없음 (계정 게이트) ≠ 할 일(docs)
            elif r["provenance"] == "docs":
                scope["docs"] += 1
    out = []
    for cat in sorted(cats):
        c = cats[cat]
        c["services"] = [c["services"][s] for s in sorted(c["services"])]
        out.append(c)
    return out


def _worklist(model: dict) -> tuple[list[dict], list[dict]]:
    """저작 작업 큐 — 손봐야 할 노드를 (불완전, 미검증) 두 묶음으로.

    _map_meta가 이미 계산한 완성도/provenance를 그대로 재사용한다(지도와 한 소스).
      · 불완전(incomplete) = create.endpoint 부재 OR requires 참조 미해결 →
        최우선 저작 대상. why = "생성 endpoint 없음" / "미해결 참조: …".
      · 미검증(docs-only) = provenance:docs 인데 완성된 노드 → 검증(2xx) 대상.
        (불완전과 겹치면 불완전 쪽에만 넣어 중복을 피한다.)
    각 행은 편집 폼(/planning/resources/{id}) 딥링크용 id/service/why 를 담는다.
    """
    _, meta = _map_meta(model)
    incomplete: list[dict] = []
    docs_only: list[dict] = []
    for nid in sorted(model):
        m = meta[nid]
        service = str(model[nid].get("service") or "")
        if not m["complete"]:
            why = []
            if not m["has_endpoint"] and not m["no_api"]:
                why.append("생성 endpoint 없음")
            if m["missing"]:
                why.append("미해결 참조: " + ", ".join(m["missing"]))
            incomplete.append({"id": nid, "service": service,
                               "why": " · ".join(why) or "정의 미완성",
                               "provenance": m["provenance"]})
        elif m["provenance"] == "docs":
            if m["gated"]:
                why = f"게이트({m['gated']}) — 이 계정에선 검증 불가 (할 일 아님)"
            elif m["no_api"]:
                why = "API 생성 없음(no_api) — 외부 수단(docker push 등) 검증 대상"
            else:
                why = "실제 2xx 미검증 (모델만)"
            docs_only.append({"id": nid, "service": service, "why": why,
                              "gated": m["gated"], "no_api": m["no_api"],
                              "provenance": "docs"})
    return incomplete, docs_only


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
    return _render(request, "resource_list.html", plan_step="model",
                   groups=_grouped(model, groups),
                   total=len(model),
                   validated=sum(1 for n in model.values()
                                 if n.get("provenance") == "VALIDATED"),
                   files=sorted(set(sources.values())),
                   has_composer=True)


# --- 합성 (compose) — /{node_id}보다 먼저 선언해야 라우팅이 맞는다 ----------------------

def _compose_ctx(request: Request, *, selected=None, choices=None, options=None,
                 lifecycle_id="", plan=None, plan_error="", saved=None,
                 targets_error=""):
    model = resource_model.load_model()
    plan = _plan_dict(plan) if plan is not None else None
    return dict(nodes=_compose_nodes(model),
                plan_step="compose",
                selected=set(selected or []),
                choices=choices or {}, options=options or {},
                lifecycle_id=lifecycle_id,
                has_composer=True,
                plan=plan,
                plan_rows=_plan_rows(plan) if plan else [],
                plan_error=plan_error, saved=saved,
                targets_error=targets_error)


# --- graph views (R-platform P0) — JSON contract + shared renderer + demo.
#     Declared before /{node_id} so "graph.json"/"graph.js"/"graph" don't route
#     as a node id. The composer stays the single source of truth (graph_view /
#     focus_view); these routes only project + serve. -----------------------------

def _parse_choices(raw: str) -> dict:
    """`a=b,c=d` (or JSON) -> {node: branch} for the one_of branch selection."""
    if not raw:
        return {}
    raw = raw.strip()
    if raw.startswith("{"):
        import json
        try:
            return json.loads(raw)
        except Exception:
            return {}
    out = {}
    for pair in raw.split(","):
        if "=" in pair:
            k, _, v = pair.partition("=")
            out[k.strip()] = v.strip()
    return out


@router.get("/graph.json")
def graph_json(request: Request, targets: str = "", focus: str = "",
               choices: str = ""):
    """Layout-agnostic dependency-graph JSON for the graph UI.

    `?focus=<node>` -> that node's upstream closure + direct dependents.
    `?targets=a,b,c[&choices=node=branch,...]` -> the composed closure.
    """
    model = resource_model.load_model()
    try:
        if focus:
            if focus not in model:
                return JSONResponse({"error": f"unknown node '{focus}'"}, status_code=404)
            return JSONResponse(composer.focus_view(focus, model=model))
        tlist = [t for t in (targets or "").split(",") if t.strip()]
        if not tlist:
            return JSONResponse({"error": "targets 또는 focus 가 필요합니다"}, status_code=400)
        return JSONResponse(composer.graph_view(tlist, _parse_choices(choices) or None,
                                                None, model))
    except Exception as exc:  # ComposeError or bad input -> 400, never 500
        return JSONResponse({"error": str(exc)}, status_code=400)


@router.get("/graph.js")
def graph_js():
    """Serve the shared SVG renderer (no static mount needed)."""
    path = HERE / "static" / "graph.js"
    return Response(path.read_text(encoding="utf-8"),
                    media_type="application/javascript")


@router.get("/graph", include_in_schema=False)
def graph_demo():
    """Retired (IA.md WS2): the standalone resource_graph 'P0 demo' overlapped
    the real dependency graph. Redirect to its canonical home. The JSON/JS
    contract (graph.json / graph.js) stays — the composer + node forms use it."""
    from fastapi.responses import RedirectResponse
    return RedirectResponse("/planning/dependencies", status_code=301)


# --- 모델 지도(② 테스트 모델 저작 — 그래프 얼굴) -------------------------------------------
#     같은 graph_view 데이터를 공유 렌더러(resource_graph.js)로 그리고, overlay()만
#     provenance/완성도 색으로 바꾼다(계약 §1, §4 Modeling). 노드 클릭 → 그 노드의
#     기존 편집 폼(/{node_id})을 사이드패널로 연다(htmx). map.json / map 둘 다
#     "/{node_id}"보다 먼저 선언해 노드 id로 라우팅되지 않게 한다.

@router.get("/map.json")
def map_json():
    """모델 지도 데이터 — 전체 lifecycle-bearing 노드의 graph_view + per-node meta.

    `meta[id] = {provenance, has_endpoint, complete, missing[]}` 로 클라이언트
    overlay(id)가 provenance/완성도 색칠을 한다(서버가 단일 source of truth)."""
    model = resource_model.load_model()
    targets, meta = _map_meta(model)
    if not targets:
        return JSONResponse({"nodes": [], "edges": [], "levels": [0],
                             "shared": [], "peak_quota": {}, "order": [],
                             "teardown": [], "meta": {}})
    try:
        view = composer.graph_view(targets, None, None, model)
    except Exception as exc:  # ComposeError 등 -> 빈 그래프(페이지는 살아있게), 400 아님
        return JSONResponse({"error": str(exc), "nodes": [], "edges": [],
                             "levels": [0], "meta": meta}, status_code=200)
    view["meta"] = meta
    return JSONResponse(view)


@router.get("/map", response_class=HTMLResponse)
def map_page(request: Request):
    """Modeling 통합 화면 — 기본은 '표'(무엇이 모델링/불완전/미검증인지 한눈에,
    필터·정렬), '그림' 토글로 같은 데이터를 공유 SVG 그래프로. 행/노드 클릭 = 그
    노드 편집 폼을 사이드패널에서 연다."""
    model = resource_model.load_model()
    targets, meta = _map_meta(model)
    deps_idx = _dependents_index(model)
    rows = _modeling_rows(model, meta, deps_idx)
    tree = _modeling_tree(rows)
    # 카탈로그 흡수(2026-07-07): 서비스 그룹 행에 "API N (모델됨 M · 미모델 K)" 집계
    ep_stats = _service_endpoint_stats(model)
    for cat in tree:
        for svc in cat["services"]:
            svc["ep"] = ep_stats.get(svc["service"])
    total = len(model)
    validated = sum(1 for v in meta.values() if v["provenance"] == "VALIDATED")
    gated = sum(1 for v in meta.values()
                if v["gated"] and v["provenance"] != "VALIDATED")
    docs = sum(1 for v in meta.values() if v["provenance"] == "docs") - gated
    incomplete = sum(1 for v in meta.values() if not v["complete"])
    services = sorted({r["service"] for r in rows if r["service"]})
    return _render(request, "resource_map.html", plan_step="model",
                   active="modeling",  # 계약 §4: Modeling 얼굴 (lead가 nav 배선)
                   total=total, validated=validated, docs=docs, gated=gated,
                   incomplete=incomplete, anchors=len(targets),
                   tree=tree, services=services, has_composer=True)


@router.get("/map/endpoints", response_class=HTMLResponse)
def map_endpoints(request: Request, service: str = ""):
    """서비스 엔드포인트 드로어 파셜 (htmx lazy) — 카탈로그의 그 서비스 슬라이스를
    METHOD path + 상태 칩(모델됨→노드 편집 링크 / 미매핑 / 미모델)으로 렌더.
    1,372개 전체를 map 페이지에 한 번에 렌더하지 않기 위한 서버 파셜이다."""
    skey = (service or "").strip()
    eps = _catalog_by_service().get(skey)
    if eps is None:
        return templates.TemplateResponse(
            request, "resource_map_endpoints.html",
            {"endpoints": [], "service": skey,
             "error": f"카탈로그에 '{skey}' 서비스가 없습니다"})
    model = resource_model.load_model()
    rows = _classify_endpoints(eps, _endpoint_ref_index(model))
    return templates.TemplateResponse(
        request, "resource_map_endpoints.html",
        {"endpoints": rows, "service": skey, "error": ""})


@router.get("/worklist", response_class=HTMLResponse)
def worklist_page(request: Request):
    """저작 작업 큐 — 지도가 보여주는 '상태'를 '할 일'로 바꾼 목록.

    지도(/map)와 같은 완성도/provenance(_map_meta)를 써서 손봐야 할 노드만 추려
    각 노드 편집 폼으로 바로 가는 딥링크를 준다. "/{node_id}" 보다 먼저 선언."""
    model = resource_model.load_model()
    incomplete, docs_only = _worklist(model)
    n_gated = sum(1 for d in docs_only if d.get("gated"))
    return _render(request, "resource_worklist.html", plan_step="model",
                   active="modeling",  # Modeling nav 탭 강조
                   incomplete=incomplete, docs_only=docs_only,
                   n_incomplete=len(incomplete), n_docs=len(docs_only),
                   n_gated=n_gated, total=len(model))


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

    model = resource_model.load_model()
    try:
        plan = composer.plan(targets, choices or None, options or None, model=model)
    except Exception as exc:
        return page(plan_error=f"plan 계산 실패: {exc}")

    saved = None
    if action == "save":
        try:
            lifecycle = composer.compose(targets, choices or None, options or None,
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
    # M2 의존 저작 보조: 역방향(나를 require 하는 노드) + 미해결 참조(존재하지 않는 대상)
    dependents = _dependents_index(model).get(node_id, [])
    unresolved = _missing_requires(node, set(model))
    return _render(request, "resource_form.html", plan_step="model",
                   active="modeling",  # Modeling nav 탭 강조
                   node_id=node_id, node=node, is_new=is_new,
                   file=sources.get(node_id, ""),
                   node_ids=sorted(model),
                   dependents=dependents, unresolved=unresolved,
                   req_rows=resource_model.requires_rows(node),
                   opt_rows=resource_model.options_rows(node),
                   body_text=resource_model.body_text(node),
                   capture_text=resource_model.capture_text(node),
                   verify_rows=resource_model.verify_rows(node),
                   lifecycle=resource_model.lifecycle_info(node),
                   # ready may be a LIST of specs (multi-stage readiness,
                   # composer 2026-07-04, e.g. tgw-vpc-connection) — this
                   # single-spec form shows stage 1 only; edit such a node in
                   # the YAML directly (a form save would flatten the list).
                   ready=(node["ready"][0]
                          if isinstance(node.get("ready"), list)
                          and node["ready"] else node.get("ready") or {}),
                   delete=node.get("delete") or {},
                   option_types=resource_model.OPTION_TYPES,
                   has_composer=True)


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
        _apply_lifecycle_toggle(form, node_id, result)
    return templates.TemplateResponse(
        request, "resource_save_result.html",
        {"result": result, "saved": result["ok"], "node_id": node_id})


def _apply_lifecycle_toggle(form, node_id: str, result: dict) -> None:
    """노드 저장이 성공하면, 폼이 보낸 lifecycle enabled/heavy 토글을 연계
    lifecycle 프래그먼트에 반영한다(쓰기 가능할 때만 lifecycle_toggle 가 옴).
    토글 자체의 실패는 노드 저장을 무르지 않고 warning 으로만 알린다 — 두 산출물
    (노드 yaml · lifecycle json)은 서로 다른 파일/관심사라 부분 성공을 허용한다."""
    if not result.get("ok") or str(form.get("lifecycle_toggle") or "") != "1":
        return
    # source.lifecycle 은 폼이 편집하지 않으므로 현재 저장된 노드에서 가져온다.
    existing = resource_model.load_model().get(node_id) or {}
    info = resource_model.lifecycle_info(existing)
    result.setdefault("warnings", [])
    if not info["writable"]:
        result["warnings"].append(
            f"lifecycle 토글을 적용하지 못했습니다: {info['reason']}")
        return
    enabled = resource_model._yes(form.get("lifecycle_enabled") or "")
    heavy = resource_model._yes(form.get("lifecycle_heavy") or "")
    res = resource_model.set_lifecycle_flags(existing, enabled=enabled, heavy=heavy)
    if not res["ok"]:
        result["warnings"].append(
            "lifecycle 토글 실패: " + "; ".join(res["errors"]))
    elif res["changed"]:
        result["warnings"].append(
            f"lifecycle <code>{res['id']}</code>: enabled={enabled}, "
            f"heavy={heavy} 로 <code>{res['file']}</code>에 기록했습니다.")
