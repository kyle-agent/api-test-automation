"""Shared page-render context for every controlplane router (UIUX-AUDIT P1-3).

base.html's header (nav + ctxbar) needs the SAME context on every page:
suites/profiles for the trigger forms, dispatch/triage flags, and the
published-snapshot line (``ctx_snapshot`` — "같은 sha를 모든 화면에", IA 계약).
Before this module each router built its own subset — catalog / modeling
(resource_routes) / reporting-coverage / ai forgot ``ctx_snapshot`` and the
ctxbar showed the false "발행 스냅샷 정보 없음". One builder, every router
imports it; the "no snapshot" message now appears only when the
dashboard-data branch genuinely can't be read (dashdata degrades to None).

Also computes the snapshot AGE (P2-10): a relative "N일 전" label next to the
absolute timestamp, plus a ``snap_stale`` flag when the snapshot is older than
``STALE_AFTER_H`` hours so the ctxbar/home can show a subtle 노후 chip.
"""
from __future__ import annotations

import datetime as _dt
import os as _os

from controlplane import dashdata, dispatch, triage
from core import profiles as core_profiles
from core import suites as core_suites

#: snapshots older than this many hours get the 노후 (stale) warning chip
STALE_AFTER_H = 48


def snapshot_age(snap: dict | None) -> dict:
    """``{"label": "N일 전", "stale": bool}`` from the snapshot's ``ts``
    (``%Y-%m-%dT%H:%M:%SZ`` — the history.jsonl format). Best-effort: an
    unparsable/absent ts degrades to an empty label, never an error."""
    ts = str((snap or {}).get("ts") or "")
    try:
        t = _dt.datetime.strptime(ts, "%Y-%m-%dT%H:%M:%SZ").replace(
            tzinfo=_dt.timezone.utc)
    except ValueError:
        return {"label": "", "stale": False}
    sec = max(0, int((_dt.datetime.now(_dt.timezone.utc) - t).total_seconds()))
    if sec < 3600:
        label = f"{sec // 60}분 전"
    elif sec < 86400:
        label = f"{sec // 3600}시간 전"
    else:
        label = f"{sec // 86400}일 전"
    return {"label": label, "stale": sec >= STALE_AFTER_H * 3600}


def snap_ts_short(snap: dict | None) -> str:
    """발행 판정 시각의 짧은 KST 라벨 ("MM-DD HH:MM") — 출처 배지용 (v2 접목 1).

    원천은 history.jsonl 행의 ``ts`` = **판정 런 시각** (v2 published.py 실측:
    발행 저장소 갱신 시각과 다를 수 있고, 판정 배지에는 이 값이 맞다).
    파싱 실패는 빈 문자열로 강등 — 배지는 시각 없이 렌더된다."""
    ts = str((snap or {}).get("ts") or "")
    try:
        t = _dt.datetime.strptime(ts, "%Y-%m-%dT%H:%M:%SZ").replace(
            tzinfo=_dt.timezone.utc)
    except ValueError:
        return ""
    return t.astimezone(_dt.timezone(_dt.timedelta(hours=9))).strftime(
        "%m-%d %H:%M")


# 발행 저장소(dashboard-data) HEAD 캐시 — 헤더 배지가 페이지 렌더마다 subprocess
# 를 돌리지 않게 60s TTL (dashdata 의 fetch TTL 과 동일한 감각). sha 와 커밋 시각을
# 한 번에 담는다 (v2 접목 5 — 판정 런 시각 ≠ 발행 갱신 시각을 병기하려면 둘 다 필요).
_DD_HEAD_CACHE: dict = {"ts": 0.0, "sha": None, "committed": None}


def _dd_head() -> dict:
    """dashboard-data 브랜치 HEAD 의 ``{"sha", "committed"}`` (60s 캐시).

    ``sha`` = 단축 커밋 해시 (발행 식별자). ``committed`` = 그 커밋의 committer
    시각(``%Y-%m-%dT%H:%M:%SZ`` UTC 문자열) = **발행 저장소가 마지막으로 갱신된
    시각**. 결과 없는 재발행이면 판정 런 시각(history ts)보다 나중일 수 있다 —
    이 어긋남을 배지가 정직하게 병기한다 (v2 접목 5). best-effort: git 접근 실패는
    None 으로 강등, 배지는 해당 세그먼트를 그냥 생략한다."""
    import subprocess
    import time as _time
    from pathlib import Path
    now = _time.time()
    if now - _DD_HEAD_CACHE["ts"] < 60:
        return _DD_HEAD_CACHE
    root = str(Path(__file__).resolve().parent.parent)
    sha = committed = None
    try:
        # %h·%cd 한 번에 — committer date 는 UTC ISO(%cd:format 지정)로 받아
        # snap_ts_short 와 같은 파서를 재사용한다.
        p = subprocess.run(
            ["git", "show", "-s", "--format=%h%x00%cd",
             "--date=format-local:%Y-%m-%dT%H:%M:%SZ", "origin/dashboard-data"],
            capture_output=True, text=True, timeout=10,
            cwd=root, env={**_os.environ, "TZ": "UTC"})
        out = (p.stdout or "").strip()
        if p.returncode == 0 and "\x00" in out:
            sha, committed = out.split("\x00", 1)
            sha, committed = (sha.strip() or None), (committed.strip() or None)
    except Exception:
        sha = committed = None
    _DD_HEAD_CACHE.update(ts=now, sha=sha, committed=committed)
    return _DD_HEAD_CACHE


def dd_sha() -> str | None:
    """발행 식별자 = dashboard-data 브랜치 HEAD 단축 sha (v2 접목 — v2
    published.py._dd_sha 이식). 헤더 Published 배지의 ident 로 쓴다:
    같은 발행본을 보고 있는지 서버·사람끼리 대조하는 용도."""
    return _dd_head()["sha"]


def dd_ts_short() -> str:
    """발행 갱신 시각(dashboard-data HEAD committer date)의 짧은 KST 라벨
    ("MM-DD HH:MM") — 판정 런 시각(``snap_ts_short``)과 **병기**해 결과 없는
    재발행으로 벌어진 시점 차를 드러낸다 (v2 접목 5). 접근 실패는 빈 문자열."""
    return snap_ts_short({"ts": _dd_head()["committed"]})


def base_ctx(active: str) -> dict:
    """Everything base.html needs, for ANY page-rendering route."""
    snap = dashdata.latest_coverage()
    age = snapshot_age(snap)
    # 환경정보 스트립 (2026-07-09 owner GO — legacy /platform 콘솔 이식): env 는
    # 호스트/계정을 가르는 SCP_ENV (core settings.env_code); suite 라벨은 스냅샷의
    # run_type 을 템플릿이 그대로 읽는다. settings 접근 실패는 빈 값으로 강등
    # (스트립은 best-effort — 페이지 렌더를 막지 않는다).
    try:
        from core.config import settings as _settings
        ctx_env = str(_settings.env_code or "")
    except Exception:
        ctx_env = ""
    return {
        "active": active,
        "suites": [s.get("id") for s in core_suites.list_suites()],
        "profiles": [p.get("id") for p in core_profiles.list_profiles()],
        "dispatch_ok": dispatch.configured(),
        "triage_ok": triage.enabled(),
        "ctx_snapshot": snap,
        "ctx_env": ctx_env,
        "snap_age": age["label"],
        "snap_stale": age["stale"],
        "snap_ts_label": snap_ts_short(snap),
        "dd_sha": dd_sha(),
        "dd_ts_label": dd_ts_short(),  # 발행 갱신 시각 (판정 런 시각과 병기 — 접목 5)
    }
