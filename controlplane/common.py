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
    }
