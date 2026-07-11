"""트리아지 상세 데이터 계층 — Reporting 개선 A (donor: v2 results_data.py,
V2-L1-DATA-CONTRACT §2.5). "신규 fail N건"의 정체(어느 엔드포인트인지)를
트리아지 탭에 목록으로 — 페르소나 P2의 최대 막힘(집계만 있고 목록이 없어
순환 참조) 해소.

원천 (전부 S1 발행본 또는 저장소 파일 — 로컬 관측 아님):

  * ``fail_new.json`` (dashboard/build.py 가 이번 개선에서 신설 발행) — 정공법.
  * 폴백: 발행 ``index.html`` 회귀 배너 파싱 (v2의 임시 우회 이식 — 다음 발행
    전까지의 다리. 배너는 [:6] 상한이라 화면에 상한을 밝힌다).
  * 현재 상태: ``endpoint_status.json`` 같은 키의 최신 status — "당시 500 →
    지금 201(복구 관측)" 분리 표기 (누적 최신값이 재시도 복구를 숨기는 문제).
  * known: ``data/baselines/known_issues.json`` (저장소).

각 함수는 실패를 삼키고 None — 화면은 그 섹션만 empty-state (계약 §3).
"""
from __future__ import annotations

import html
import json
import re
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
KNOWN_ISSUES_PATH = REPO_ROOT / "data" / "baselines" / "known_issues.json"

# 발행 index.html의 회귀 배너 블록 (폴백 전용 — donor 주석 그대로:
# dashboard/build.py 출력 문자열이 계약이라 포맷이 바뀌면 조용히 깨질 수 있어
# 실패 시 예외 없이 None).
_BANNER_RE = re.compile(r'<div class="action bad">(.*?)</div>\s*</div>', re.DOTALL)
_ITEM_RE = re.compile(r'<code>(.*?)</code>\s*→\s*(\d+)', re.DOTALL)


def _load_dd_json(name: str) -> dict:
    from controlplane import dashdata
    got = dashdata.file(name)
    if not got:
        return {}
    try:
        return json.loads(got[0].decode(errors="replace"))
    except ValueError:
        return {}


def _from_fail_new_json() -> tuple[list[tuple[str, int]], bool] | None:
    """정공법 — 발행 fail_new.json. (items, capped=False)."""
    doc = _load_dd_json("fail_new.json")
    if not doc or not isinstance(doc.get("new"), list):
        return None
    items = [(str(it.get("key") or ""), int(it.get("status") or 0))
             for it in doc["new"] if it.get("key")]
    return items, False


def _from_index_banner() -> tuple[list[tuple[str, int]], bool] | None:
    """폴백 — index.html 배너 파싱 (최대 6건, capped=True).

    구분(실측 2026-07-11): index.html 접근 불가 → None(계산 불가) ·
    접근됐는데 배너 없음 → ([], True) = **그 발행 시점 기준 회귀 0건**.
    후자는 판정 카운트(history 마지막 줄)와 다를 수 있다 — 수동 발행이 결과
    없는 갱신을 하면 index는 앞서가고 판정은 이전 런에 머문다(v2 published.py
    실측과 같은 어긋남). 화면이 이 차이를 명시한다."""
    from controlplane import dashdata
    got = dashdata.file("index.html")
    if not got:
        return None
    m = _BANNER_RE.search(got[0].decode(errors="replace"))
    if not m:
        return [], True
    items: list[tuple[str, int]] = []
    for raw_key, raw_status in _ITEM_RE.findall(m.group(1)):
        key = html.unescape(raw_key).strip()
        if key:
            try:
                items.append((key, int(raw_status)))
            except ValueError:
                continue
    return items, True


def _split_catalog_key(key: str) -> tuple[str, str, str] | None:
    parts = key.split("/")
    if len(parts) == 3 and all(parts):
        return parts[0], parts[1], parts[2]
    return None


def _enrich(parsed: list[tuple[str, int]]) -> list[dict]:
    """항목별 보강: 현재 상태(당시/현재 분리) · 딥링크 · 이중 기록 힌트."""
    from dashboard.build import slug  # 발행 서비스 페이지와 동일 슬러그 규칙
    status_map = _load_dd_json("endpoint_status.json").get("status") or {}
    rows: list[dict] = []
    for key, status in parsed:
        row: dict = {"key": key, "status": status,
                     "current_status": None, "current_changed": False,
                     "current_note": None, "kind": "unknown", "link": None,
                     "service_label": None, "lifecycle_label": None, "dup_of": []}
        cur = status_map.get(key)
        if isinstance(cur, (list, tuple)) and cur and isinstance(cur[0], int):
            row["current_status"] = cur[0]
            row["current_changed"] = cur[0] != status
            if row["current_changed"]:
                row["current_note"] = ("복구 관측" if 200 <= cur[0] < 300
                                       else "값 변동 관측")
        cso = _split_catalog_key(key)
        if cso:
            cat, svc, op = cso
            row.update(kind="catalog", cat=cat, svc=svc, op=op,
                       link=f"/dashboard/services/{slug(cat, svc)}.html",
                       service_label=f"{cat}/{svc}")
        elif ":" in key:
            lifecycle, _, step = key.partition(":")
            row.update(kind="synthetic", lifecycle=lifecycle, step=step,
                       lifecycle_label=(f"라이프사이클 단계 — {lifecycle} / {step}"
                                        if step else f"라이프사이클 단계 — {lifecycle}"))
        rows.append(row)
    # 이중 기록 힌트 (donor 그대로): 같은 status의 (합성, 카탈로그) 쌍 — 병합
    # 대신 정직하게 둘 다 표시 + 안내만.
    catalog_rows = [r for r in rows if r["kind"] == "catalog"]
    for s in (r for r in rows if r["kind"] == "synthetic"):
        for c in catalog_rows:
            if c["status"] == s["status"] and f'{c["cat"]}-{c["svc"]}' in s["lifecycle"]:
                s["dup_of"].append(c["key"])
                c["dup_of"].append(s["key"])
    return rows


def get_new_regressions() -> dict | None:
    """{'items': [...], 'capped': bool, 'source': 'file'|'banner'} 또는 None."""
    try:
        got = _from_fail_new_json()
        source = "file"
        if got is None:
            got = _from_index_banner()
            source = "banner"
        if got is None:
            return None
        items, capped = got
        return {"items": _enrich(items), "capped": capped, "source": source}
    except Exception:
        return None


def get_known_issues() -> list[dict] | None:
    """data/baselines/known_issues.json → 화면용 목록 (donor 이식)."""
    try:
        if not KNOWN_ISSUES_PATH.exists():
            return None
        data = json.loads(KNOWN_ISSUES_PATH.read_text())
        out = []
        for it in data.get("issues") or []:
            key = it.get("key") or ""
            if not key:
                continue
            out.append({"key": key, "status": it.get("status"),
                        "type": it.get("type") or it.get("classification") or "",
                        "since": it.get("since") or it.get("added") or "",
                        "note": it.get("note") or ""})
        return out
    except Exception:
        return None
