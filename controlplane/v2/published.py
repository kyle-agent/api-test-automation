"""발행본(S1) 메타 로더 — L1 계약 §1·§4. dashdata 위의 얇은 층.

발행 시각·식별자·노후 판정을 이 모듈 한 곳에서만 구현한다.
root 파일만 노출 — preview-v2/·platform/ 등 비-root 트랙 접근 금지 (L1 계약:
같은 파일명이라도 별도 빌드 산출물이라 수치가 다르다).
"""
from __future__ import annotations

import json
import subprocess
from datetime import datetime, timedelta, timezone
from pathlib import Path

from controlplane import dashdata

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
KST = timezone(timedelta(hours=9))
STALE_AFTER_HOURS = 24


def _dd_sha() -> str | None:
    """발행 식별자 = dashboard-data 브랜치 HEAD 단축 sha."""
    try:
        p = subprocess.run(
            ["git", "rev-parse", "--short", "origin/dashboard-data"],
            capture_output=True, text=True, timeout=10, cwd=REPO_ROOT)
        return p.stdout.strip() or None
    except Exception:
        return None


def _updated_at() -> datetime | None:
    """발행 시각 정본: endpoint_status.json.updated → 폴백: history 마지막 ts."""
    got = dashdata.file("endpoint_status.json")
    if got:
        try:
            raw = json.loads(got[0]).get("updated", "")
            # 예: "2026-07-09 19:27 KST"
            return datetime.strptime(
                raw.replace(" KST", "").strip(), "%Y-%m-%d %H:%M"
            ).replace(tzinfo=KST)
        except Exception:
            pass
    hist = dashdata.history(limit=1)
    if hist:
        ts = hist[-1].get("ts")
        try:
            if isinstance(ts, (int, float)):
                return datetime.fromtimestamp(float(ts), tz=timezone.utc)
            if isinstance(ts, str) and ts:
                return datetime.fromisoformat(ts.replace("Z", "+00:00"))
        except Exception:
            return None
    return None


def meta() -> dict:
    """발행 배지에 필요한 전부. 접근 실패 시 ok=False (empty-state 규칙 §3)."""
    dt = _updated_at()
    age_h = None
    if dt is not None:
        age_h = (datetime.now(timezone.utc) - dt).total_seconds() / 3600
    return {
        "ok": dt is not None,
        "updated": dt,
        "updated_label": dt.astimezone(KST).strftime("%m-%d %H:%M") if dt else None,
        "sha": _dd_sha(),
        "age_hours": age_h,
        "stale": bool(age_h is not None and age_h >= STALE_AFTER_HOURS),
    }


def headline() -> dict | None:
    """판정 헤드라인 원천 — history.jsonl 마지막 줄 (fail_new/fail_known/cov_*)."""
    return dashdata.latest_coverage()


def headline_ts_label(head: dict | None) -> str | None:
    """판정 자체의 시각 라벨 (history 행 ts) — 발행 시각과 다를 수 있다.

    실측(2026-07-10): 수동 발행이 결과 없는 갱신을 하면 endpoint_status.updated
    (발행 시각)는 앞서가고 history 마지막 줄(판정)은 이전 런에 머문다. 판정
    배지에 발행 시각을 달면 시점이 어긋나므로 판정 배지는 이 값을 쓴다."""
    if not head:
        return None
    ts = head.get("ts")
    try:
        if isinstance(ts, (int, float)):
            dt = datetime.fromtimestamp(float(ts), tz=timezone.utc)
        elif isinstance(ts, str) and ts:
            dt = datetime.fromisoformat(ts.replace("Z", "+00:00"))
        else:
            return None
        return dt.astimezone(KST).strftime("%m-%d %H:%M")
    except Exception:
        return None
