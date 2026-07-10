"""/v2 라우터 — 스트랭글러 셸 + 상황실 (L1 계약 §2.1).

기존 모듈(db·dashdata·snapshots)은 읽기 전용 import. 이 라우터가 기존
화면·엔진을 수정하는 일은 없다 (V2-KICKOFF 경계 규약).
"""
from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

from fastapi import APIRouter, Request
from fastapi.responses import FileResponse, HTMLResponse
from fastapi.templating import Jinja2Templates

from controlplane import db, snapshots
from controlplane.v2 import (published, results_data, runs_data, search_data,
                             services_data, terms)

HERE = Path(__file__).resolve().parent
templates = Jinja2Templates(directory=str(HERE / "templates"))
router = APIRouter(prefix="/v2")


# ── 공용 컨텍스트 ────────────────────────────────────────────────────────────

def _ctx(request: Request, active: str) -> dict:
    return {
        "request": request,
        "active": active,
        "nav": terms.NAV,
        "terms": terms.TERMS,
        "terms_run_status": terms.RUN_STATUS,
        "sources": terms.SOURCES,
        "product": terms.PRODUCT_NAME,
        "pub": published.meta(),
    }


def _parse_db_ts(raw: str | None) -> datetime | None:
    if not raw:
        return None
    try:
        return datetime.strptime(raw, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
    except Exception:
        return None


def _timeline(limit: int = 30) -> list[dict]:
    """병합 런 타임라인 — DB(CI+로컬) ∪ 아카이브 인덱스(구형 CI), gh_run_id dedupe."""
    rows: list[dict] = []
    seen: set[str] = set()
    for r in db.list_runs(limit=limit):
        d = dict(r)
        gid = str(d.get("gh_run_id") or "")
        d["is_local"] = gid.startswith("local-")
        d["when"] = d.get("finished_at") or d.get("started_at") or d.get("requested_at")
        rows.append(d)
        if gid:
            seen.add(gid)
    try:  # 아카이브는 원격 버킷 — 실패해도 화면은 성립 (best-effort)
        for a in snapshots.archive_index(limit=limit):
            gid = str(a.get("run") or a.get("gh_run_id") or "")
            if not gid or gid in seen:
                continue
            rows.append({
                "gh_run_id": gid, "suite": a.get("suite", ""),
                "profile": a.get("profile", ""), "status": "archived",
                "trigger": "external", "is_local": False,
                "when": a.get("ts") or a.get("time") or "",
            })
    except Exception:
        pass
    rows.sort(key=lambda d: str(d.get("when") or ""), reverse=True)
    return rows[:limit]


# ── 상황실 (홈) ──────────────────────────────────────────────────────────────

@router.get("", include_in_schema=False)
@router.get("/", response_class=HTMLResponse)
def situation(request: Request):
    ctx = _ctx(request, "situation")
    head = published.headline()  # 판정성 수치의 유일한 원천 (D2)
    runs = _timeline(limit=30)
    local_runs = [r for r in runs if r.get("is_local")]

    # 보조 칩: 발행 이후 이 서버에서 끝난 런 수 (판정은 바꾸지 않음)
    chip_n = 0
    pub_dt = ctx["pub"].get("updated")
    if pub_dt is not None:
        for r in local_runs:
            dt = _parse_db_ts(r.get("finished_at")) or _parse_db_ts(r.get("requested_at"))
            if dt is not None and dt > pub_dt:
                chip_n += 1

    # 호출(C2) 커버리지 % — 발행 필드 tested/total에서 계산 (dashboard.build:433
    # 의 정의: tested = 응답이 관측된(4xx 포함) 엔드포인트 수)
    called_pct = None
    if head and head.get("tested") is not None and head.get("total"):
        called_pct = round(head["tested"] / head["total"] * 100, 1)

    ctx.update(head=head, runs=runs, local_runs=local_runs[:10], chip_n=chip_n,
               called_pct=called_pct,
               verdict_ts=published.headline_ts_label(head))
    return templates.TemplateResponse(request, "situation.html", ctx)


# ── 나머지 축 (골격 — 화면 단위로 채워진다) ─────────────────────────────────

_STUBS = {
    "model": ("Model", "테스트 정의 · 리소스 모델 — 모델 표·작업 큐·노드 에디터·인벤토리가 이 축에 들어옵니다.",
              [("Legacy Modeling screen ↗", "/planning")]),
    "tools": ("Tools", "부가 도구 — AI 초안·지식 문서·발행 대시보드 링크 모음.",
              [("Published dashboard (read-only sharing) ↗", "/reporting"),
               ("AI tools ↗", "/ai"), ("Legacy Home ↗", "/")]),
}


def _stub(request: Request, key: str):
    title, desc, links = _STUBS[key]
    ctx = _ctx(request, key)
    ctx.update(stub_title=title, stub_desc=desc, stub_links=links)
    return templates.TemplateResponse(request, "stub.html", ctx)


@router.get("/services", response_class=HTMLResponse)
def services(request: Request):
    ctx = _ctx(request, "services")
    data = services_data.get_services_data()
    ctx.update(data=data, services=(data or {}).get("services") or [])
    return templates.TemplateResponse(request, "services_list.html", ctx)


@router.get("/services/{slug}", response_class=HTMLResponse)
def service_detail(request: Request, slug: str):
    ctx = _ctx(request, "services")
    svc = services_data.get_service(slug)
    if svc is None:
        ctx.update(slug=slug)
        return templates.TemplateResponse(
            request, "service_not_found.html", ctx, status_code=404)
    ctx.update(svc=svc, rows=svc.get("endpoint_rows") or [])
    return templates.TemplateResponse(request, "service_detail.html", ctx)


@router.get("/model", response_class=HTMLResponse)
def model(request: Request):
    return _stub(request, "model")


@router.get("/run", response_class=HTMLResponse)
def run(request: Request):
    """실행 축(⑥) — 계획·모니터링·기록 (L1 계약 §2.6).

    이 라우트는 어떤 실행도 발사하지 않는다 — 조회 전용(``runs_data``) + 폼
    제출은 기존 ``/testing``으로의 GET 핸드오프뿐(경계 규약). ``?service=``
    등은 서비스 상세 딥링크의 프리필로 그대로 수용한다.
    """
    ctx = _ctx(request, "run")
    ctx.update(runs_data.get_run_data())
    ctx["prefill"] = {
        "suite": (request.query_params.get("suite") or "").strip(),
        "profile": (request.query_params.get("profile") or "").strip(),
        "service": (request.query_params.get("service") or "").strip(),
    }
    return templates.TemplateResponse(request, "runs.html", ctx)


@router.get("/results", response_class=HTMLResponse)
def results(request: Request):
    """결과 축(⑤) — 회귀·트리아지 (L1 계약 §2.5)."""
    ctx = _ctx(request, "results")
    data = results_data.get_results_data()
    ctx.update(data)
    ctx.setdefault("verdict_ts",
                   published.headline_ts_label(data.get("head")))
    return templates.TemplateResponse(request, "results.html", ctx)


@router.get("/search", response_class=HTMLResponse)
def search(request: Request):
    """전역 검색(⌕) — 서비스·엔드포인트·run (CX-IA-DESIGN §4.2, 계약 §2.8)."""
    ctx = _ctx(request, "search")
    q = (request.query_params.get("q") or "").strip()
    ctx.update(data=search_data.search(q))
    return templates.TemplateResponse(request, "search.html", ctx)


@router.get("/tools", response_class=HTMLResponse)
def tools(request: Request):
    return _stub(request, "tools")


# ── 정적 자산 (app.py에 mount 추가를 피하려고 라우트로 서빙) ────────────────

@router.get("/static/{path:path}", include_in_schema=False)
def static(path: str):
    base = (HERE / "static").resolve()
    target = (base / path).resolve()
    if not str(target).startswith(str(base)) or not target.is_file():
        return HTMLResponse(status_code=404, content="not found")
    return FileResponse(target)
