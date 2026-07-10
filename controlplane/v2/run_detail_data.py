"""런 상세(③) 데이터 계층 — L1 데이터 계약 §2.3·§2.4 (출처 S3: 런 스냅샷).

이 페이지의 모든 값은 "이 런 1건"에 고정된다 — 판정 재계산 금지, 과거형
표기(계약 §1-S3). 헤더(run id·suite·trigger·시각)는 두 종류(CI/로컬) 공통으로
``controlplane.db.get_run()``(runs 테이블)에서 온다. 결과 요약은 종류별로
갈린다:

  * CI 런(숫자 ``gh_run_id``)  -> ``controlplane.snapshots``
    (오plog 버킷의 ``meta.json``/``observations.jsonl`` — 원격이라 실패는
    흔한 경로. 그 섹션만 empty-state로 접는다, 페이지 전체를 죽이지 않는다).
  * 로컬 런(``local-`` 접두)   -> ``controlplane.console_api.local_run_summary``
    (기존 콘솔 엔진 헬퍼의 공개 재수출 — 여기서 엔진 내부(``tools.console2_server``)
    를 다시 파고들지 않는다).

로컬 런에 한해 §2.4 fold(공식 반영) 동선의 판정도 이 모듈이 계산한다.
**fold를 실행하지 않는다** — 계산은 "미반영 증거 몇 건"이라는 안내용
수치일 뿐, ``verified_endpoints.json``/main에 쓰기는 전혀 하지 않는다.

모든 하위 조회는 실패를 삼키고 그 섹션만 ``None``/빈 값으로 접는다(계약
§3) — db 행도 스냅샷/로컬 요약도 전혀 없을 때만 전체 ``None`` (호출부가
404로 렌더).
"""
from __future__ import annotations

from datetime import datetime, timezone

# 로컬 런의 "발행 미반영" 근사 시간창에 더하는 여유 — 서버 시계 오차·로그
# 반영 지연을 감안한다(계약 §1-S2 "약 N건" 근사 취지 그대로). 정밀 조인이
# 아니라 안내용 근사이므로 값 자체보다 "약"이라는 정직한 표기가 더 중요하다.
_FOLD_WINDOW_MARGIN_S = 30.0
_FOLD_PREVIEW_LIMIT = 20


def _row_to_dict(row) -> dict | None:
    return dict(row) if row is not None else None


def _get_db_row(gh_run_id: str) -> dict | None:
    try:
        from controlplane import db
        return _row_to_dict(db.get_run(gh_run_id))
    except Exception:
        return None


def _parse_db_ts(raw: str | None) -> datetime | None:
    """runs 테이블 타임스탬프(``db.now()`` 포맷: ``%Y-%m-%dT%H:%M:%SZ``) 파싱."""
    if not raw:
        return None
    try:
        return datetime.strptime(raw, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
    except Exception:
        return None


# ── CI 런 (숫자 gh_run_id) ───────────────────────────────────────────────────

def _ci_meta(gh_run_id: str) -> dict | None:
    try:
        from controlplane import snapshots
        return snapshots.meta(gh_run_id)
    except Exception:
        return None


def _ci_observations(gh_run_id: str) -> list[dict]:
    try:
        from controlplane import snapshots
        return snapshots.observations(gh_run_id)
    except Exception:
        return []


def _ci_result_summary(observations: list[dict]) -> dict | None:
    """관측 리스트 -> {"counts": {ok,soft,fail}, "fails": [{endpoint_key,status,
    method,path}]}. 관측이 비어있으면 None(그 섹션 empty-state)."""
    if not observations:
        return None
    counts = {"ok": 0, "soft": 0, "fail": 0}
    fails = []
    for o in observations:
        cat = o.get("category")
        if cat in counts:
            counts[cat] += 1
        if cat == "fail":
            fails.append({
                "endpoint_key": o.get("endpoint_key", ""),
                "status": o.get("status"),
                "method": o.get("method", ""),
                "path": o.get("path", ""),
            })
    return {"counts": counts, "total": len(observations), "fails": fails}


def _ci_detail(gh_run_id: str) -> dict:
    meta = _ci_meta(gh_run_id)
    obs = _ci_observations(gh_run_id)
    return {
        "is_local": False,
        "snapshot_found": meta is not None or bool(obs),
        "meta": meta,
        "result": _ci_result_summary(obs),
    }


# ── 로컬 런 (local- 접두) ────────────────────────────────────────────────────

def _local_summary(gh_run_id: str) -> dict | None:
    """``console_api.local_run_summary``의 얇은 재사용 — pass/fail/skip +
    api ok/soft/fail + 실패 lifecycle id (``events_summary`` 모양 그대로)."""
    try:
        from controlplane import console_api
        return console_api.local_run_summary(gh_run_id)
    except Exception:
        return None


def _fold_status(row: dict | None) -> dict | None:
    """§2.4 — 런 시간창(started~ended±여유) 내 2xx 관측의 ``endpoint_key``
    집합 - 발행본 ``verified_endpoints.json`` 키 집합. 시간창을 못 잡거나
    관측 파일이 없으면 None(섹션 자체 empty-state) — "0건"과는 다른 상태다.
    """
    if row is None:
        return None
    started = _parse_db_ts(row.get("started_at") or row.get("requested_at"))
    if started is None:
        return None
    ended_dt = _parse_db_ts(row.get("finished_at"))
    ended = ended_dt.timestamp() if ended_dt else datetime.now(timezone.utc).timestamp()
    lo = started.timestamp() - _FOLD_WINDOW_MARGIN_S
    hi = ended + _FOLD_WINDOW_MARGIN_S

    try:
        from core.results import load_observations
        obs = load_observations()
    except Exception:
        return None
    if not obs:
        return None

    in_window_2xx: dict[str, dict] = {}
    for o in obs:
        try:
            ts = float(o.get("ts"))
        except (TypeError, ValueError):
            continue
        if not (lo <= ts <= hi):
            continue
        key = o.get("endpoint_key") or ""
        if not key:
            continue
        try:
            is_2xx = 200 <= int(o.get("status")) <= 299
        except (TypeError, ValueError):
            is_2xx = False
        if not is_2xx:
            continue
        in_window_2xx[key] = {"endpoint_key": key, "status": o.get("status")}

    if not in_window_2xx:
        return {"count": 0, "preview": [], "truncated": False}

    try:
        import json
        from controlplane import dashdata
        got = dashdata.file("verified_endpoints.json")
        verified_doc = json.loads(got[0].decode(errors="replace")) if got else {}
    except Exception:
        verified_doc = {}
    published_keys = set(verified_doc.get("verified") or [])

    missing = sorted(set(in_window_2xx) - published_keys)
    return {
        "count": len(missing),
        "preview": [in_window_2xx[k] for k in missing[:_FOLD_PREVIEW_LIMIT]],
        "truncated": len(missing) > _FOLD_PREVIEW_LIMIT,
    }


def _local_detail(gh_run_id: str, row: dict | None) -> dict:
    summary = _local_summary(gh_run_id)
    return {
        "is_local": True,
        "snapshot_found": summary is not None,
        "summary": summary,
        "fold": _fold_status(row),
    }


# ── 헤더 + 재실행 딥링크 ─────────────────────────────────────────────────────

def _header(row: dict | None, meta: dict | None) -> dict:
    """run id·suite·trigger·시각 — 우선 db 행, 없으면 CI meta.json으로 보강."""
    row = row or {}
    meta = meta or {}
    return {
        "suite": row.get("suite") or meta.get("suite") or "",
        "profile": row.get("profile") or meta.get("profile") or "",
        "trigger": row.get("trigger") or meta.get("event") or "",
        "status": row.get("status") or "",
        "when": (row.get("finished_at") or row.get("started_at")
                 or row.get("requested_at") or meta.get("ts") or ""),
    }


def _rerun_link(header: dict) -> str:
    """"CI로 재실행" 딥링크 — 기존 ``/testing?...`` prefill 계약 재사용
    (services_data.py의 ``/testing?service=`` 와 같은 규약). 이 런의
    suite/profile로 조합 — 자동 발사 아님, 기존 콘솔의 pre-flight 확인이
    그대로 유지된다."""
    from urllib.parse import urlencode
    params = {}
    if header.get("suite"):
        params["suite"] = header["suite"]
    if header.get("profile"):
        params["profile"] = header["profile"]
    qs = urlencode(params)
    return "/testing" + (f"?{qs}" if qs else "")


# ── 화면 컨텍스트 조립 ───────────────────────────────────────────────────────

def get_run_detail(gh_run_id: str) -> dict | None:
    """``/v2/runs/{gh_run_id}`` 화면 컨텍스트 전부, 또는 완전히 없을 때 None
    (호출부가 404로 렌더). db 행이 없어도 스냅샷/로컬 요약이 있으면 성립한다
    (계약: "공통: db.get_run(). 없고 스냅샷도 없으면 None")."""
    row = _get_db_row(gh_run_id)
    is_local = gh_run_id.startswith("local-")

    if is_local:
        detail = _local_detail(gh_run_id, row)
    else:
        detail = _ci_detail(gh_run_id)

    if row is None and not detail.get("snapshot_found"):
        return None

    header = _header(row, detail.get("meta"))
    out = {
        "gh_run_id": gh_run_id,
        "is_local": is_local,
        "row": row,
        "header": header,
        "rerun_link": _rerun_link(header),
    }
    out.update(detail)
    return out
