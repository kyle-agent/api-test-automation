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
from controlplane.v2 import published, terms

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

    ctx.update(head=head, runs=runs, local_runs=local_runs[:10], chip_n=chip_n)
    return templates.TemplateResponse(request, "situation.html", ctx)


# ── 나머지 축 (골격 — 화면 단위로 채워진다) ─────────────────────────────────

_STUBS = {
    "services": ("서비스", "이 서비스는 지금 어떤가? — 서비스 목록·상세가 다음 단계로 여기 들어옵니다.",
                 [("발행 대시보드의 서비스 드릴다운", "/reporting")]),
    "model": ("모델", "테스트를 어떻게 정의했나? — 모델 표·작업 큐·노드 에디터·인벤토리가 여기로 옵니다.",
              [("기존 Modeling 화면", "/planning")]),
    "run": ("실행", "돌리자 — 계획(선택→DAG→사전 확인)→실행·모니터→기록이 여기로 옵니다.",
            [("기존 Testing 콘솔", "/testing")]),
    "results": ("결과", "무엇이 나왔나? — 요약·회귀/트리아지·런 타임라인·비교가 여기로 옵니다.",
                [("기존 Reporting 화면", "/reporting")]),
    "tools": ("도구", "AI 초안·지식 문서·발행 대시보드로 가는 관문입니다.",
              [("AI 도구", "/ai"), ("기존 홈", "/")]),
}


def _stub(request: Request, key: str):
    title, desc, links = _STUBS[key]
    ctx = _ctx(request, key)
    ctx.update(stub_title=title, stub_desc=desc, stub_links=links)
    return templates.TemplateResponse(request, "stub.html", ctx)


@router.get("/services", response_class=HTMLResponse)
def services(request: Request):
    return _stub(request, "services")


@router.get("/model", response_class=HTMLResponse)
def model(request: Request):
    return _stub(request, "model")


@router.get("/run", response_class=HTMLResponse)
def run(request: Request):
    return _stub(request, "run")


@router.get("/results", response_class=HTMLResponse)
def results(request: Request):
    return _stub(request, "results")


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
