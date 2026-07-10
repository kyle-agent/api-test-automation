"""런 상세(③) 데이터 계층 — L1 데이터 계약 §2.3·§2.4·§2.9 (출처 S3: 런 스냅샷).

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

§2.9 (실행 뷰 3상태: running/queued/done) — 로컬 런의 ``bucket``을 이 서버의
인메모리 rec(``tools.console2_server._RUNS``)에서 직접 판정한다. ``runs_data.py``
(§2.6)가 이미 "``console_api.api_runs()``가 감싸는 것과 정확히 같은 조회를
파이썬 함수로 직접 호출한다"는 선례를 세워뒀다 — 이 모듈도 같은 경계
(``controlplane/v2/**``, HTTP 자기호출 아님)를 그대로 따른다. PLAN 재계산도
``POST /api/plan``이 서버 쪽에서 하는 것과 완전히 같은 호출
(``tools.console2_server._plan``)을 이 프로세스 안에서 직접 한다 — 페이지
렌더 도중 자기 자신에게 HTTP 요청을 보내는 것은 하지 않는다(요청 지시
명시).
"""
from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

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


# ── 실행 뷰(§2.9) — 이 서버 인메모리 rec 직접 조회/재계산 ────────────────────

_LIVE_STATUSES = {"running", "queued"}
# LifecycleSkip의 쿼터/세마포어 사유는 항상 이 두 접두 패턴 중 하나로
# 시작한다 — 실측(regression/scenarios/engine.py:1107-1119 budget 소진,
# :1136-1139 VPC 세마포어 타임아웃). Hard Rule 6: 이 사유의 스킵은 실패가
# 아니다(⊘ 글리프로 구분, fail 카운트에 넣지 않는다).
_QUOTA_SKIP_MARKERS = ("budget '", "VPC quota semaphore")


def _bare_rid(gh_run_id: str) -> str:
    return gh_run_id[len("local-"):] if gh_run_id.startswith("local-") else gh_run_id


def _live_rec(gh_run_id: str) -> dict | None:
    """이 서버가 지금 기억하는 rec(``tools.console2_server._RUNS``)의
    view — 서버가 재기동돼 이 run이 인메모리에 없으면(즉 이미 끝난 지
    오래된 로컬 런) None, 호출부는 그걸 'done'으로 취급한다(과거 요약
    경로는 이미 있음)."""
    try:
        from tools import console2_server as c2
        rid = _bare_rid(gh_run_id)
        with c2._LOCK:
            rec = c2._RUNS.get(rid)
            return c2._rec_view(rec, full=False) if rec else None
    except Exception:
        return None


def _exec_bucket(rec: dict | None) -> str:
    """3상태 버킷(§2.9) — running/queued만 '살아있음'. 그 외(done/error/
    aborted/unknown/rec 없음)는 전부 기존 스냅샷 요약 경로('done')로 접는다."""
    if not rec:
        return "done"
    st = rec.get("status")
    return st if st in _LIVE_STATUSES else "done"


def _plan_direct(lifecycle_ids: list[str]) -> dict | None:
    """PLAN 스트립 재계산 — ``POST /api/plan``과 완전히 같은 서버 로직
    (``tools.console2_server._plan``)을 **직접** 호출한다(HTTP 자기호출
    금지 — 요청 지시 명시). ``console_api.api_plan()``이 하는 일과 동일,
    HTTP 계층만 건너뛴다."""
    try:
        from tools import console2_server as c2
        return c2._plan([str(x) for x in (lifecycle_ids or [])])
    except Exception:
        return None


def _capacity_direct() -> dict | None:
    """``runs_data.get_capacity()``(§2.6, 이미 이 서버의 ``c2._capacity_view()``를
    직접 호출하는 선례)를 그대로 재사용한다 — 새 구현 금지."""
    try:
        from controlplane.v2 import runs_data
        return runs_data.get_capacity()
    except Exception:
        return None


def _queue_position(rid: str) -> int | None:
    """대기열 순번(1-base) — ``c2._QUEUE``(FIFO)에서의 위치."""
    try:
        from tools import console2_server as c2
        with c2._ADMIT:
            if rid in c2._QUEUE:
                return c2._QUEUE.index(rid) + 1
    except Exception:
        pass
    return None


def _lifecycle_states(events: list[dict]) -> dict[str, str]:
    """id -> queued|running|passed|failed|skipped — ``_events_summary``의
    lc_state 폴딩과 같은 규칙이지만 그 함수는 집계만 반환하고 per-id 맵을
    주지 않는다(``tools/console2_server.py:_events_summary``); 아래 ETA
    추정에는 '이 라이프사이클이 아직 안 끝났나'를 물어야 해서 여기 따로
    포팅한다(원형 그대로, 새 판정 규칙 아님)."""
    state: dict[str, str] = {}
    for e in events or []:
        lid = e.get("lifecycle")
        if not lid:
            continue
        if e.get("kind") == "lifecycle-start":
            state.setdefault(lid, "running")
        elif e.get("kind") == "lifecycle-end":
            st = e.get("status")
            state[lid] = "passed" if st == "passed" else "skipped" if st == "skipped" else "failed"
    return state


def _rec_remaining_eta_s(rec: dict) -> float | None:
    """RUNNING인 rec 하나의 대략적 잔여 시간 — console2.js ``runProgress()``
    (console2/assets/console2.js:354-373)의 ETA 계산(미종결 lifecycle의
    실측 평균(durations.json) 합 / 병렬 가정 6)을 그대로 포팅해, queued
    화면의 "선행 런 잔여 ETA" 최초 렌더(서버 사이드 seed)에 쓴다. 실측
    이력이 전혀 없으면 None(화면은 '계산할 수 없음'으로 정직하게 접는다) —
    이 서버가 여러 런을 순차 admit하는 정확한 시뮬레이션은 하지 않는
    근사치임을 호출부 문구에 명시한다."""
    try:
        from tools import console2_server as c2
        rid = rec.get("id")
        if not rid:
            return None
        ev_path = c2.RUN_DIR / f"{rid}.events.jsonl"
        events = c2._read_events(str(ev_path))
        states = _lifecycle_states(events)
        ids = rec.get("lifecycle_ids") or []
        durations = c2._durations_view()
        rem, known = 0.0, 0
        for lid in ids:
            st = states.get(lid, "queued")
            if st in ("queued", "running"):
                d = durations.get(lid)
                if d and d.get("avg_s"):
                    rem += d["avg_s"]
                    known += 1
        if not known:
            return None
        eta_parallel = 6   # ETA_PARALLEL — console2.js:353 과 같은 가정
        return rem / min(eta_parallel, known)
    except Exception:
        return None


def _queued_context(rec: dict, capacity: dict | None) -> dict:
    """WHY QUEUED — 여유(headroom) < 이 런의 요구(peak_vpcs) + 예상 시작
    (실행 중인 런들 중 가장 늦게 끝날 것으로 보이는 것 기준, 근사)."""
    running = (capacity or {}).get("running") or []
    etas = [(e, r.get("id")) for r, e in
            ((r, _rec_remaining_eta_s(r)) for r in running) if e is not None]
    blocking_eta_s, blocking_id = max(etas, key=lambda t: t[0]) if etas else (None, None)
    return {
        "peak_vpcs": int(rec.get("peak_vpcs") or 0),
        "headroom": (capacity or {}).get("headroom"),
        "running_count": len(running),
        "blocking_run_id": blocking_id,
        "blocking_eta_s": blocking_eta_s,
    }


def _raw_events_for_local(gh_run_id: str) -> list[dict]:
    """로컬 런의 원시 이벤트 스트림 — 인메모리 rec을 먼저 찾고(방금 끝난
    런), 없으면 이 콘솔이 실제로 쓰는 경로(``RUN_DIR`` = ``reports/
    console2-runs``)로 폴백한다. **실측 차이**: L1 계약 §1-S3은 로컬 런
    이벤트의 실체를 ``reports/controlplane-local/<rid>.events.jsonl``로
    적고 있으나, 이 코드베이스가 실제로 쓰는 경로는
    ``tools/console2_server.py:80`` ``RUN_DIR = ROOT/"reports"/"console2-runs"``
    이다(``_local_run_summary``도 같은 경로를 최우선으로 본다) — 계약
    문서가 가리키는 경로는 이 코드베이스에 존재하지 않는다."""
    try:
        from tools import console2_server as c2
        rid = _bare_rid(gh_run_id)
        with c2._LOCK:
            rec = c2._RUNS.get(rid)
        path = Path(rec["events"]) if rec else c2.RUN_DIR / f"{rid}.events.jsonl"
        return c2._read_events(str(path))
    except Exception:
        return []


def _skip_reason_is_quota(reason: str) -> bool:
    r = reason or ""
    return any(m in r for m in _QUOTA_SKIP_MARKERS)


def _skip_details(events: list[dict]) -> list[dict]:
    """쿼터/세마포어 스킵 vs 그 외 스킵 목록 — ``lifecycle-end``
    (status=='skipped')의 ``reason`` 문자열로 구분한다(실측:
    regression/scenarios/engine.py:1464-1478가 항상 이 kind로, ``reason``에
    LifecycleSkip 메시지 원문을 싣는다 — 사유 구분이 실제로 가능함을 확인)."""
    out = []
    for e in events or []:
        if e.get("kind") == "lifecycle-end" and e.get("status") == "skipped":
            reason = e.get("reason") or ""
            out.append({"lifecycle": e.get("lifecycle"), "reason": reason,
                        "quota": _skip_reason_is_quota(reason)})
    return out


def _resource_tally(events: list[dict]) -> dict:
    """생성/삭제 대조 — ``resource-tracked``/``resource-deleted`` 이벤트
    총계(console2.js ``groupEventsByLifecycle``의 createN 카운트 로직,
    console2/assets/console2.js:2205-2210 원형 — run 전체 합계만 필요해
    lifecycle별 매칭 없이 단순 카운트로 축약)."""
    created = sum(1 for e in events if e.get("kind") == "resource-tracked")
    deleted = sum(1 for e in events if e.get("kind") == "resource-deleted")
    return {"created": created, "deleted": deleted, "balanced": created == deleted}


# ── 헤더 + 재실행 딥링크 ─────────────────────────────────────────────────────

def _header(row: dict | None, meta: dict | None, rec: dict | None = None) -> dict:
    """run id·suite·trigger·시각 — 우선 db 행, 없으면 CI meta.json으로 보강.
    ``status``는 살아있는 rec이 있으면(§2.9) 그 rec의 상태(running/queued/…)를
    우선한다 — db 행은 로컬 런이 '끝났을 때'만 기록되므로(``_record_run_to_db``),
    진행 중에는 db 행 자체가 아직 없을 수 있다."""
    row = row or {}
    meta = meta or {}
    return {
        "suite": row.get("suite") or meta.get("suite") or "",
        "profile": row.get("profile") or meta.get("profile") or "",
        "trigger": row.get("trigger") or meta.get("event") or "",
        "status": (rec or {}).get("status") or row.get("status") or "",
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
    (계약: "공통: db.get_run(). 없고 스냅샷도 없으면 None").

    §2.9: 로컬 런이면 이 서버의 살아있는 rec을 먼저 조회해 ``bucket``
    (running/queued/done)을 판정한다 — running/queued면 rec 자체가 곧
    "스냅샷이 있다"는 신호이므로(아직 db 행도, events_summary도 없을 수
    있는 갓 시작한 런도 404가 아니라 실행 뷰로 성립해야 한다)."""
    row = _get_db_row(gh_run_id)
    is_local = gh_run_id.startswith("local-")
    rec = _live_rec(gh_run_id) if is_local else None
    bucket = _exec_bucket(rec) if is_local else "done"

    if is_local:
        detail = _local_detail(gh_run_id, row)
    else:
        detail = _ci_detail(gh_run_id)

    if row is None and not detail.get("snapshot_found") and rec is None:
        return None

    header = _header(row, detail.get("meta"), rec)
    out = {
        "gh_run_id": gh_run_id,
        "is_local": is_local,
        "row": row,
        "header": header,
        "rerun_link": _rerun_link(header),
        "bucket": bucket,
    }
    out.update(detail)

    if is_local and bucket in _LIVE_STATUSES:
        ids = list(rec.get("lifecycle_ids") or [])
        capacity = _capacity_direct()
        exec_ctx = {
            "rid": _bare_rid(gh_run_id),
            "lifecycle_ids": ids,
            "plan": _plan_direct(ids),
            "capacity": capacity,
            "started": rec.get("started"),
            "heavy": rec.get("heavy"),
        }
        if bucket == "queued":
            exec_ctx["queued"] = _queued_context(rec, capacity)
            exec_ctx["queue_position"] = _queue_position(exec_ctx["rid"])
        out["exec"] = exec_ctx
    elif is_local and bucket == "done":
        events = _raw_events_for_local(gh_run_id)
        out["skip_details"] = _skip_details(events)
        out["resource_tally"] = _resource_tally(events) if events else None

    return out
