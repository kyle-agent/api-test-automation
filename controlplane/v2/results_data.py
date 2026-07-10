"""결과 축(⑤) 데이터 계층 — L1 데이터 계약 §2.5.

전부 S1(발행본, ``controlplane.dashdata`` — root 파일만) 또는 저장소(repo HEAD)
파일에서만 읽는다. 각 조회 함수는 실패 시 예외를 삼키고 ``None``을 반환한다 —
화면은 그 필드만 empty-state로 성립해야 한다(계약 §2.5 규칙 6, §3).
"""
from __future__ import annotations

import html
import json
import re
from pathlib import Path

from dashboard.build import slug  # 슬러그 포맷 재사용 — services_data.py와 동일 관례

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
KNOWN_ISSUES_PATH = REPO_ROOT / "data" / "baselines" / "known_issues.json"

# 발행 index.html의 회귀 배너 블록. dashboard/build.py:1150-1154 —
#   <div class="action bad"><div><b>새 회귀 N건 — 조치 필요.</b>
#     <div><code>KEY</code> → STATUS</div>...(최대 6건, new_regressions[:6])
#   </div></div>
# 항목 구분자(<code>...</code> → 뒤에 붙는 개별 <div>)는 서로 인접해 닫히지
# 않으므로(다음 항목은 여는 태그로 시작), 최초 "</div></div>"가 정확히 이
# 배너 블록의 끝이다.
_BANNER_RE = re.compile(r'<div class="action bad">(.*?)</div>\s*</div>', re.DOTALL)
_ITEM_RE = re.compile(r'<code>(.*?)</code>\s*→\s*(\d+)', re.DOTALL)


def _load_dd_json(name: str) -> dict:
    """dashdata의 root 파일 하나 -> dict. 실패/부재 시 {} (empty-state 원칙)."""
    from controlplane import dashdata
    got = dashdata.file(name)
    if not got:
        return {}
    try:
        return json.loads(got[0].decode(errors="replace"))
    except ValueError:
        return {}


def _parse_new_regressions_raw() -> list[tuple[str, int]] | None:
    """임시 우회 — V2-REQUESTS-TO-ENGINE.md #1 (fail_new.json 발행) 처리 시 제거.

    발행 파이프라인에 회귀 상세 전용 발행 파일이 없어(실측, L1 계약 §2.5),
    발행 index.html의 회귀 배너(``class="action bad"``) 블록을 정규식으로
    파싱해 (key, status) 목록을 추출한다. HTML 구조 자체가 계약이 아니라
    dashboard/build.py의 출력 문자열이 계약이라 이 파서는 그 문자열 포맷이
    바뀌면 조용히 깨질 수 있다(그래서 실패 시 예외 없이 None). 발행 배너는
    최대 6건까지만 노출하므로(``d["new_regressions"][:6]``) 이 목록도 그
    상한을 물려받는다 — 화면에 "최대 6건"임을 밝혀야 한다.
    """
    from controlplane import dashdata
    got = dashdata.file("index.html")
    if not got:
        return None
    try:
        text = got[0].decode(errors="replace")
    except Exception:
        return None
    m = _BANNER_RE.search(text)
    if not m:
        return None
    items: list[tuple[str, int]] = []
    for raw_key, raw_status in _ITEM_RE.findall(m.group(1)):
        key = html.unescape(raw_key).strip()
        if not key:
            continue
        try:
            items.append((key, int(raw_status)))
        except ValueError:
            continue
    return items or None


def _split_catalog_key(key: str) -> tuple[str, str, str] | None:
    """cat/svc/op 형식(슬래시 정확히 2개)이면 (cat, svc, op), 아니면 None."""
    parts = key.split("/")
    if len(parts) == 3 and all(parts):
        return parts[0], parts[1], parts[2]
    return None


def _enrich_new_regressions(parsed: list[tuple[str, int]]) -> list[dict]:
    """각 항목에 링크·라이프사이클 라벨·현재 상태·이중 기록 힌트를 보강한다."""
    status_map = _load_dd_json("endpoint_status.json").get("status") or {}
    rows: list[dict] = []
    for key, status in parsed:
        row: dict = {
            "key": key, "status": status,
            "current_status": None, "current_changed": False, "current_note": None,
            "kind": "unknown", "link": None, "service_label": None,
            "lifecycle_label": None, "dup_of": [],
        }
        cur = status_map.get(key)
        if isinstance(cur, (list, tuple)) and cur and isinstance(cur[0], int):
            row["current_status"] = cur[0]
            row["current_changed"] = cur[0] != status
            if row["current_changed"]:
                # "당시"와 "현재"를 반드시 분리 표기 (누적 최신 status는 재시도
                # 복구를 숨기므로) — 계약 §2.5.
                row["current_note"] = ("복구 관측" if 200 <= cur[0] < 300
                                        else "값 변동 관측")

        cat_svc_op = _split_catalog_key(key)
        if cat_svc_op:
            cat, svc, op = cat_svc_op
            row.update(kind="catalog", cat=cat, svc=svc, op=op,
                       link=f"/v2/services/{slug(cat, svc)}", service_label=svc)
        elif ":" in key:
            lifecycle, _, step = key.partition(":")
            row.update(kind="synthetic", lifecycle=lifecycle, step=step,
                       lifecycle_label=(f"라이프사이클 단계 — {lifecycle} / {step}"
                                        if step else f"라이프사이클 단계 — {lifecycle}"))
        rows.append(row)

    # 이중 기록 힌트: 같은 status의 (합성 키, 카탈로그 키) 쌍 중 카탈로그
    # 키의 "cat-svc"가 합성 키의 라이프사이클 이름에 부분 문자열로 나타나면
    # 같은 호출의 이중 기록 가능성으로 본다 — 병합은 하지 않고 정직하게
    # 둘 다 표시 + 안내 배지만 붙인다 (계약 §2.5).
    catalog_rows = [r for r in rows if r["kind"] == "catalog"]
    synth_rows = [r for r in rows if r["kind"] == "synthetic"]
    for s in synth_rows:
        for c in catalog_rows:
            if c["status"] != s["status"]:
                continue
            probe = f'{c["cat"]}-{c["svc"]}'
            if probe in s["lifecycle"]:
                s["dup_of"].append(c["key"])
                c["dup_of"].append(s["key"])
    return rows


def get_new_regressions() -> list[dict] | None:
    """새 회귀 상세 목록(보강 완료). 파싱 실패/블록 없음 -> None (empty-state)."""
    try:
        parsed = _parse_new_regressions_raw()
        if not parsed:
            return None
        return _enrich_new_regressions(parsed)
    except Exception:
        return None


def get_known_issues() -> list[dict] | None:
    """data/baselines/known_issues.json의 issues 목록 -> 화면용 dict 목록."""
    try:
        if not KNOWN_ISSUES_PATH.exists():
            return None
        data = json.loads(KNOWN_ISSUES_PATH.read_text())
        out = []
        for it in data.get("issues") or []:
            key = it.get("key") or ""
            if not key:
                continue
            out.append({
                "key": key,
                "status": it.get("status"),
                "type": it.get("type") or it.get("classification") or "",
                "since": it.get("since") or it.get("added") or "",
                "note": it.get("note") or "",
            })
        return out
    except Exception:
        return None


def get_conformance_changes() -> dict | None:
    """conformance_new.json의 new/regressed/fixed — 파일 접근 실패 시 None.

    (실제로 존재하나 전부 빈 리스트인 경우와 접근 실패를 구분한다: 접근 실패는
    ``dashdata.file``이 None을 돌려주므로 이 함수도 None을 반환하고, 존재하되
    빈 문서는 ``{"new": [], ...}``로 참값(dict에 키가 있음)이라 정상 empty로
    렌더된다.)"""
    try:
        from controlplane import dashdata
        got = dashdata.file("conformance_new.json")
        if not got:
            return None
        doc = json.loads(got[0].decode(errors="replace"))

        def _enrich(entry: dict) -> dict:
            out = dict(entry)
            cat_svc_op = _split_catalog_key(entry.get("endpoint") or "")
            if cat_svc_op:
                cat, svc, _op = cat_svc_op
                out["link"] = f"/v2/services/{slug(cat, svc)}"
            else:
                out["link"] = None
            return out

        return {
            "new": [_enrich(e) for e in (doc.get("new") or [])],
            "regressed": [_enrich(e) for e in (doc.get("regressed") or [])],
            "fixed": [_enrich(e) for e in (doc.get("fixed") or [])],
        }
    except Exception:
        return None


def get_conformance_summary() -> dict | None:
    """conformance.json summary(green/yellow/red) + top systemic findings."""
    try:
        from controlplane import dashdata
        return dashdata.conformance_summary()
    except Exception:
        return None


def get_results_data() -> dict:
    """/v2/results 화면 컨텍스트 전부 — 필드별로 독립 실패해도 페이지는 성립."""
    from controlplane.v2 import published  # 지연 import: 순환 임포트 회피

    try:
        head = published.headline()  # 회귀 카운트 = 현황 헤드라인과 동일 원천 (S1)
    except Exception:
        head = None

    return {
        "head": head,
        "new_regressions": get_new_regressions(),
        "known_issues": get_known_issues(),
        "conf_changes": get_conformance_changes(),
        "conf_summary": get_conformance_summary(),
    }
