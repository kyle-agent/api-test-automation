"""전역 검색 데이터 계층 — CX-IA-DESIGN-2026-07-09.md §4.2 ("⌕ 전역 검색(서비스·
엔드포인트·run)"), V2-L1-DATA-CONTRACT.md §1·§3.

세 표면을 한 화면에서 검색한다:

  * Services  — ``services_data.get_services_data()`` 재사용 (계약 §2.2, S1
    발행본 기준 — 로직 복제 금지, HTML 파싱 금지 원칙을 그대로 상속).
  * Endpoints — ``data/api_catalog.json``을 이 모듈이 직접(모듈 캐시로) 읽는다.
    분모(1372건)가 정적 저장소 파일이라 발행본(S1) 계약과 무관 — 카탈로그
    자체는 이미 서비스 축(§2.2)도 저장소 HEAD에서 읽는 입력이라 재사용 대신
    직접 로드해도 이중 소스가 아니다 (같은 파일, 다른 인덱스 관점일 뿐).
  * Runs      — ``db.list_runs()`` 그대로 재사용 (S2, "이 서버" 로컬 관측).
    런 상세 화면(``/v2/runs/<id>``)은 이 배치의 다른 개발자가 병렬 구현 중이라
    존재를 보장하지 않는다 — 링크만 걸고 화면 쪽 코멘트로 표기한다.

모든 함수는 실패를 삼키고 빈 리스트/None으로 성립한다 (empty-state 원칙,
계약 §3). 이 모듈은 어떤 공유 파일도 import에서 수정하지 않는다 — 읽기 전용.
"""
from __future__ import annotations

import json
import time
from pathlib import Path

MIN_QUERY_LEN = 2

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
CATALOG_PATH = REPO_ROOT / "data" / "api_catalog.json"

ENDPOINT_LIMIT = 50
RUN_LIMIT = 20
RUN_SCAN = 200  # db.list_runs(200) — 계약 그대로

# ── 카탈로그 모듈 캐시 (mtime 기준 — 요청마다 1372건 JSON 재파싱을 피한다) ──
_catalog_cache: dict = {"mtime": None, "rows": None}


def _load_catalog() -> list[dict]:
    """data/api_catalog.json -> list[dict]. 모듈 레벨 캐시(파일 mtime 키).

    ``services_data``/``dashboard.build``와 달리 여기서는 원본 카탈로그
    엔트리(``key``/``category``/``service``/``name``/``method``/``http_path``)를
    그대로 쓴다 — 서비스별 재집계(``per_service``)가 필요 없는 평면 검색이라
    그 함수를 거칠 이유가 없다(로직 복제가 아니라: 애초에 계산이 없다)."""
    try:
        mtime = CATALOG_PATH.stat().st_mtime
    except OSError:
        return []
    if _catalog_cache["rows"] is not None and _catalog_cache["mtime"] == mtime:
        return _catalog_cache["rows"]
    try:
        rows = json.loads(CATALOG_PATH.read_text())
    except (OSError, ValueError):
        return []
    if not isinstance(rows, list):
        rows = []
    _catalog_cache.update(mtime=mtime, rows=rows)
    return rows


def _search_services(q: str) -> dict:
    """name/category 부분일치. services_data 재사용 — 커버리지 재계산 없음."""
    from controlplane.v2 import services_data

    data = services_data.get_services_data()
    if not data:
        return {"ok": False, "items": [], "total": 0}
    ql = q.lower()
    hits = []
    for s in data.get("services") or []:
        if ql in (s.get("service") or "").lower() or ql in (s.get("category") or "").lower():
            hits.append({
                "slug": s["slug"], "service": s["service"], "category": s["category"],
                "covered": s["covered"], "total": s["total"], "pct_label": s["pct_label"],
                "defect_red": s.get("defect_red", 0), "defect_yellow": s.get("defect_yellow", 0),
                "untestable": s.get("untestable"),
            })
    return {"ok": True, "items": hits, "total": len(hits)}


def _search_endpoints(q: str) -> dict:
    """path/name/method 부분일치. 상한 50 + 전체 매치 수(total)."""
    cat = _load_catalog()
    ql = q.lower()
    matched = []
    for e in cat:
        hay = " ".join((
            e.get("http_path") or "", e.get("name") or "", e.get("method") or "",
        )).lower()
        if ql in hay:
            matched.append({
                "method": e.get("method", ""),
                "path": e.get("http_path", ""),
                "api": e.get("name", ""),
                "category": e.get("category", ""),
                "service": e.get("service", ""),
                "slug": f'{e.get("category", "")}__{e.get("service", "")}'.replace("/", "-").replace(" ", "-"),
            })
    total = len(matched)
    return {"ok": True, "items": matched[:ENDPOINT_LIMIT], "total": total,
            "more": max(0, total - ENDPOINT_LIMIT)}


def _search_runs(q: str) -> dict:
    """gh_run_id/suite 부분일치, 상한 20. 런 상세 화면은 병렬 구현 중이라
    링크(``/v2/runs/<id>``)만 걸고 존재를 보장하지 않는다."""
    try:
        from controlplane import db
        rows = db.list_runs(limit=RUN_SCAN)
    except Exception:
        return {"ok": False, "items": [], "total": 0}
    ql = q.lower()
    matched = []
    for r in rows:
        d = dict(r)
        gid = str(d.get("gh_run_id") or "")
        suite = str(d.get("suite") or "")
        if ql in gid.lower() or ql in suite.lower():
            matched.append({
                "id": d.get("id"), "gh_run_id": gid, "suite": suite,
                "profile": d.get("profile") or "", "status": d.get("status") or "",
                "when": d.get("finished_at") or d.get("started_at") or d.get("requested_at"),
                "is_local": gid.startswith("local-"),
            })
    total = len(matched)
    matched.sort(key=lambda d: str(d.get("when") or ""), reverse=True)
    return {"ok": True, "items": matched[:RUN_LIMIT], "total": total,
            "more": max(0, total - RUN_LIMIT)}


def search(q: str | None) -> dict:
    """3키 dict(services/endpoints/runs) + q/too_short 메타. 실패를 삼킨다."""
    q = (q or "").strip()
    if len(q) < MIN_QUERY_LEN:
        return {
            "q": q, "too_short": True,
            "services": {"ok": True, "items": [], "total": 0},
            "endpoints": {"ok": True, "items": [], "total": 0},
            "runs": {"ok": True, "items": [], "total": 0},
        }
    try:
        services = _search_services(q)
    except Exception:
        services = {"ok": False, "items": [], "total": 0}
    try:
        endpoints = _search_endpoints(q)
    except Exception:
        endpoints = {"ok": False, "items": [], "total": 0}
    try:
        runs = _search_runs(q)
    except Exception:
        runs = {"ok": False, "items": [], "total": 0}
    return {"q": q, "too_short": False,
            "services": services, "endpoints": endpoints, "runs": runs}
