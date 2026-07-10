"""실행 축(⑥) 데이터 계층 — 계획·모니터링·기록 (L1 데이터 계약 §2.6).

안전 원칙(계약 §2.6 그대로): (1) 실효 게이트는 실제 설정에서 읽는다 —
하드코딩·추측 금지(기존 콘솔의 오표시가 반면교사). (2) read-only 기동 = 열람
모드. (3) v1은 발사 없음 — 이 모듈도 어떤 실행도 트리거하지 않는다(읽기 전용
조회뿐). 각 조회 함수는 실패 시 예외를 삼키고 안전한 빈 값을 반환한다 —
화면은 그 필드만 empty-state로 성립해야 한다.
"""
from __future__ import annotations

import os


# ── 실효 게이트 ──────────────────────────────────────────────────────────────

def get_gate_status() -> dict:
    """core.config.settings(읽기 전용 import)에서 읽은 실효 게이트.

    이 서버가 콘솔 실행(``tools/console2_server.py`` -> 궁극적으로
    ``core.http_client.ApiClient``)에서 참조하는 것과 **같은 싱글턴**
    (``core.config.settings``)이다 — 별도 계산이면 "게이트 상태 거짓말"을
    만든다(계약 §2.6 안전 원칙 1). ``allow_mutations``/``run_heavy``/
    ``allow_destructive``는 이미 ``SCP_PROFILE_FORBID`` 베토까지 반영된
    실효값(``core/config.py``) — ``controlplane/resources.py``의
    ``destructive_enabled()``가 이미 이 필드로 게이트를 표시하는 선례를
    그대로 따른다. 프로파일 식별자는 ``core.profiles.export_pairs``가 심는
    ``SCP_PROFILE_ID`` 환경변수를 그대로 읽는다(추측·재계산 없음).
    """
    try:
        from core.config import settings
        forbid = [g.strip() for g in
                  os.environ.get("SCP_PROFILE_FORBID", "").split(",") if g.strip()]
        return {
            "ok": True,
            "mutations": bool(settings.allow_mutations),
            "heavy": bool(settings.run_heavy),
            "destructive": bool(settings.allow_destructive),
            "profile_id": os.environ.get("SCP_PROFILE_ID", "").strip(),
            "forbid": forbid,
        }
    except Exception:
        return {"ok": False, "mutations": False, "heavy": False,
                "destructive": False, "profile_id": "", "forbid": []}


# ── 스위트/프로파일 목록 (기존 로더 재사용) ──────────────────────────────────

def get_suites_and_profiles() -> dict:
    """``controlplane.common.base_ctx()``가 이미 만드는 suites/profiles를
    재사용한다 — core.suites/core.profiles를 이 모듈이 직접 다시 부르지
    않는다(계약 §2.6: "카탈로그/스위트 목록 (기존 로더)")."""
    try:
        from controlplane.common import base_ctx
        b = base_ctx("run")
        return {"suites": list(b.get("suites") or []),
                "profiles": list(b.get("profiles") or [])}
    except Exception:
        return {"suites": [], "profiles": []}


# ── 라이브 런/큐/용량 (console2 엔진 함수 직접 호출 — HTTP 자기호출 아님) ────

def get_live_runs(limit: int = 20) -> list[dict]:
    """``controlplane.console_api.api_runs()``가 감싸는 것과 정확히 같은 조회
    (``c2._LOCK`` 아래 ``c2._RUNS`` 정렬 + ``c2._rec_view``)를 파이썬 함수로
    직접 호출한다. 콘솔 엔진이 사실상 기동되지 않은 상태(모듈은 import되나
    아직 런이 없음)여도 빈 리스트로 성립한다."""
    try:
        from tools import console2_server as c2
        with c2._LOCK:
            recs = sorted(c2._RUNS.values(), key=lambda x: x["started"], reverse=True)
            rows = [c2._rec_view(r) for r in recs[:limit]]
        return rows
    except Exception:
        return []


def get_capacity() -> dict | None:
    """``controlplane.console_api.api_capacity()``가 감싸는 ``c2._capacity_view()``를
    직접 호출한다. 크리덴셜/리전 미설정 등으로 실패해도(그 함수 내부가 이미
    best-effort지만, 한 겹 더) 예외를 삼키고 None -> empty-state."""
    try:
        from tools import console2_server as c2
        return c2._capacity_view()
    except Exception:
        return None


# ── 기록 (Overview 병합 타임라인과 동일 가공) ────────────────────────────────

def get_history(limit: int = 100) -> list[dict]:
    """``db.list_runs(limit)`` — Overview 타임라인(``controlplane/v2/routes.py``
    의 ``_timeline``)과 동일한 행별 가공(``is_local``/``when``)만 적용한다."""
    try:
        from controlplane import db
        rows = []
        for r in db.list_runs(limit=limit):
            d = dict(r)
            gid = str(d.get("gh_run_id") or "")
            d["is_local"] = gid.startswith("local-")
            d["when"] = d.get("finished_at") or d.get("started_at") or d.get("requested_at")
            rows.append(d)
        return rows
    except Exception:
        return []


# ── 화면 컨텍스트 조립 ───────────────────────────────────────────────────────

def get_run_data() -> dict:
    """/v2/run 화면 컨텍스트 전부 — 필드별로 독립 실패해도 페이지는 성립."""
    sp = get_suites_and_profiles()
    return {
        "gate": get_gate_status(),
        "suites": sp["suites"],
        "profiles": sp["profiles"],
        "live_runs": get_live_runs(),
        "capacity": get_capacity(),
        "history": get_history(),
    }
