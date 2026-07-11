"""전역 검색 데이터 계층 — v2 접목 6a (donor: controlplane/v2/search_data.py,
V2-L1-DATA-CONTRACT §2.8). 세 표면을 한 화면에서 검색한다:

  * Services  — ``data/api_catalog.json``의 (category, service) 그룹 집계.
    v2 는 발행본 커버리지(services_data)를 병기했지만 v1 에는 서비스 상세
    화면이 없으므로, 저장소 카탈로그 기준(엔드포인트 수)만 정직하게 표기하고
    카탈로그/Modeling/Testing prefill 로 연결한다 (링크가 곧 다음 행동).
  * Endpoints — 카탈로그 평면 검색 (path/API명/메서드 부분일치, 상한 50).
  * Runs      — ``db.list_runs()`` (S2, "이 서버" 로컬 기록). 상한 20.

모든 함수는 실패를 삼키고 빈 리스트로 성립한다 (empty-state 원칙 §3).
읽기 전용 — import/호출 어디서도 공유 상태를 수정하지 않는다.
"""
from __future__ import annotations

import json
from pathlib import Path

MIN_QUERY_LEN = 2
ENDPOINT_LIMIT = 50
RUN_LIMIT = 20
RUN_SCAN = 200

REPO_ROOT = Path(__file__).resolve().parent.parent
CATALOG_PATH = REPO_ROOT / "data" / "api_catalog.json"

# 카탈로그 모듈 캐시 (mtime 키 — 요청마다 1,372건 JSON 재파싱 방지, donor 그대로)
_catalog_cache: dict = {"mtime": None, "rows": None}


def _load_catalog() -> list[dict]:
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
    """(category, service) 그룹 부분일치 + 엔드포인트 수 집계 (저장소 기준)."""
    cat = _load_catalog()
    if not cat:
        return {"ok": False, "items": [], "total": 0}
    ql = q.lower()
    groups: dict[tuple[str, str], int] = {}
    for e in cat:
        key = (e.get("category") or "", e.get("service") or "")
        groups[key] = groups.get(key, 0) + 1
    hits = [{"category": c, "service": s, "endpoints": n}
            for (c, s), n in sorted(groups.items())
            if ql in s.lower() or ql in c.lower()]
    return {"ok": True, "items": hits, "total": len(hits)}


def _search_endpoints(q: str) -> dict:
    """path/API명/메서드 부분일치, 상한 50 + 전체 매치 수 (donor 그대로)."""
    cat = _load_catalog()
    if not cat:
        return {"ok": False, "items": [], "total": 0}
    ql = q.lower()
    matched = []
    for e in cat:
        hay = " ".join((e.get("http_path") or "", e.get("name") or "",
                        e.get("method") or "")).lower()
        if ql in hay:
            matched.append({"method": e.get("method", ""),
                            "path": e.get("http_path", ""),
                            "api": e.get("name", ""),
                            "category": e.get("category", ""),
                            "service": e.get("service", "")})
    total = len(matched)
    return {"ok": True, "items": matched[:ENDPOINT_LIMIT], "total": total,
            "more": max(0, total - ENDPOINT_LIMIT)}


def _search_runs(q: str) -> dict:
    """gh_run_id/suite/profile 부분일치, 최신순 상한 20 (S2 — 이 서버)."""
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
        profile = str(d.get("profile") or "")
        if ql in gid.lower() or ql in suite.lower() or ql in profile.lower():
            matched.append({
                "id": d.get("id"), "gh_run_id": gid, "suite": suite,
                "profile": profile, "status": d.get("status") or "",
                "when": d.get("finished_at") or d.get("requested_at") or "",
                "is_local": gid.startswith("local-")})
    total = len(matched)
    matched.sort(key=lambda d: str(d.get("when") or ""), reverse=True)
    return {"ok": True, "items": matched[:RUN_LIMIT], "total": total,
            "more": max(0, total - RUN_LIMIT)}


def search(q: str | None) -> dict:
    """3키 dict(services/endpoints/runs) + q/too_short 메타. 실패를 삼킨다."""
    q = (q or "").strip()
    if len(q) < MIN_QUERY_LEN:
        empty = {"ok": True, "items": [], "total": 0}
        return {"q": q, "too_short": True, "services": dict(empty),
                "endpoints": dict(empty), "runs": dict(empty)}
    out: dict = {"q": q, "too_short": False}
    for key, fn in (("services", _search_services),
                    ("endpoints", _search_endpoints),
                    ("runs", _search_runs)):
        try:
            out[key] = fn(q)
        except Exception:
            out[key] = {"ok": False, "items": [], "total": 0}
    return out
