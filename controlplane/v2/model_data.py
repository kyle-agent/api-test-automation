"""Model 축(② 테스트 정의) 데이터 계층 — v2 /model.

``controlplane.resource_model``의 ``load_model()``/``load_groups()`` (§1 스키마
로더, C1 계약: 디렉토리 전체 merge, 부재 시 빈 모델)를 읽기 전용으로 재사용해
그룹별 노드 집계(개수·provenance 분포·불완전 수)를 만든다.

D6(2026-07-10 확정): 독립 의존그래프 탭 없음 — 의존 정보는 노드 편집기
(``/planning/resources/<id>``)와 서비스 상세 인스펙터(후속)로만 제공한다.
이 화면은 "무엇이 모델링되어 있고 무엇이 검증됐는가"만 보여준다.

출처 주의: 이 데이터는 **저장소(main) HEAD** 파일(``knowledge/formal/resources/
*.yaml``)을 그대로 읽는다 — L1 데이터 계약의 출처 3종(published/local/run)
어디에도 해당하지 않는다(발행본도, 이 서버의 관측도, 특정 런의 스냅샷도
아니다). 그래서 배지를 씌우지 않는다 — 화면(model.html)은 배지 대신
panel-note로 "저장소(main) 기준"임을 밝힌다. 새 배지 종류는 발명하지 않는다
(L1 계약 §4).

실패 시 None 반환(empty-state 원칙, §3). ``resource_model.load_model``/
``load_groups``는 디렉토리 부재·파싱 실패를 이미 빈 값으로 삼키므로(그 자체가
정상적인 "아직 모델 없음" 상태), None은 예기치 못한 예외 상황 전용이다 — 모델이
그냥 비어 있는 경우는 ``groups: []``인 유효한 dict로 반환하고, 화면이 그 경우의
안내문을 따로 갖는다(services_data의 data-is-None vs data.services-empty와
같은 2단 empty-state 관례).
"""
from __future__ import annotations

from pathlib import Path

from controlplane import resource_model

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
CATALOG_PATH = REPO_ROOT / "data" / "api_catalog.json"

# app.py 실측 (2026-07-10): resource_routes.router = APIRouter(prefix="/planning/resources").
# "" 도 "/" 도 resource_list.html(그룹/노드 표)를 렌더하고, "/map"이 (주석상) 정본
# 진입("Modeling의 정본 진입은 /planning/resources/map" — app.py:207-208의 /planning
# 리다이렉트 목적지)이라 그룹 핸드오프는 map으로, 개별 노드는 /{node_id}로 보낸다.
NODE_EDITOR_BASE = "/planning/resources"
MAP_HANDOFF = "/planning/resources/map"


def _missing_requires(node: dict, known: set) -> list[str]:
    """requires 대상 중 모델에 없는 노드 — 완성도 판정의 결손.

    resource_routes.py의 동명 헬퍼와 같은 판정(§1 requires 3형: str/one_of/ref)
    이지만 그 파일은 비공개(밑줄) 헬퍼이자 FastAPI 라우터 모듈이라 이 화면(새
    파일만 생성 가능한 병렬 배치 제약)이 import하지 않고 자체 보유한다."""
    miss: list[str] = []
    for r in node.get("requires") or []:
        if isinstance(r, str):
            if r not in known:
                miss.append(r)
        elif isinstance(r, dict) and "one_of" in r:
            alts = [(a.get("ref") if isinstance(a, dict) else a)
                    for a in (r.get("one_of") or [])]
            if alts and not any(a in known for a in alts):
                miss.append("one_of:" + ",".join(str(a) for a in alts))
        elif isinstance(r, dict) and "ref" in r:
            if str(r["ref"]) not in known:
                miss.append(str(r["ref"]))
    return miss


def _is_incomplete(node: dict, known: set) -> bool:
    """불완전 = create.endpoint가 없음(단, no_api 노드는 예외) OR 미해결 requires
    존재. legacy map_page(_map_meta)와 같은 정의를 이 화면 전용으로 재현."""
    has_ep = bool((node.get("create") or {}).get("endpoint"))
    no_api = bool(node.get("no_api"))
    return not ((has_ep or no_api) and not _missing_requires(node, known))


def _pct(n: int, d: int) -> float:
    return round(n / d * 100, 1) if d else 0.0


def _catalog_total() -> int | None:
    """카탈로그 인벤토리 한 줄(스펙 2d)용 총 엔드포인트 수 — 저장소 HEAD
    data/api_catalog.json 그대로(발행본 아님, 이 화면과 같은 출처 축).
    전체 인벤토리 표는 중복 금지(Services 축으로 링크) — 숫자 하나만 쓴다."""
    try:
        import json
        if not CATALOG_PATH.exists():
            return None
        cat = json.loads(CATALOG_PATH.read_text(encoding="utf-8"))
        return len(cat) if isinstance(cat, list) else None
    except Exception:
        return None


def _build() -> dict | None:
    model = resource_model.load_model()
    groups_meta = resource_model.load_groups()
    if not isinstance(model, dict) or not isinstance(groups_meta, dict):
        return None

    known = set(model)
    by_gid: dict[str, list[str]] = {}
    for nid in sorted(model):
        gid = resource_model.group_of(nid, model[nid])
        by_gid.setdefault(gid, []).append(nid)

    groups_out: list[dict] = []
    total_nodes = validated_total = docs_total = other_total = incomplete_total = 0
    for gid in sorted(by_gid):
        node_ids = by_gid[gid]  # 이미 sorted(model) 순서로 append됨
        meta = groups_meta.get(gid) or {}
        v = d = o = inc = 0
        nodes_out = []
        for nid in node_ids:
            node = model[nid]
            prov = str(node.get("provenance") or "")
            if prov == "VALIDATED":
                v += 1
            elif prov == "docs":
                d += 1
            else:
                o += 1
            incomplete = _is_incomplete(node, known)
            if incomplete:
                inc += 1
            nodes_out.append({
                "id": nid,
                "provenance": prov or "(미지정)",
                "is_validated": prov == "VALIDATED",
                "is_docs": prov == "docs",
                "create_endpoint": (node.get("create") or {}).get("endpoint") or "",
                "service": str(node.get("service") or ""),
                "incomplete": incomplete,
                "editor_href": f"{NODE_EDITOR_BASE}/{nid}",
            })
        n = len(node_ids)
        groups_out.append({
            "gid": gid,
            "label": str(meta.get("label") or gid),
            "category": str(meta.get("category") or ""),
            "node_count": n,
            "validated": v, "docs": d, "other": o, "incomplete": inc,
            "validated_pct": _pct(v, n), "docs_pct": _pct(d, n), "other_pct": _pct(o, n),
            "nodes": nodes_out,
        })
        total_nodes += n
        validated_total += v
        docs_total += d
        other_total += o
        incomplete_total += inc

    groups_out.sort(key=lambda g: (-g["node_count"], g["gid"]))

    return {
        "total_nodes": total_nodes,
        "validated": validated_total,
        "validated_pct": _pct(validated_total, total_nodes),
        "docs_only": docs_total,
        "other": other_total,
        "incomplete": incomplete_total,
        "group_count": len(groups_out),
        "groups": groups_out,
        "map_href": MAP_HANDOFF,
        "catalog_total": _catalog_total(),
    }


def get_model_data() -> dict | None:
    """실패 시 None(empty-state, §3). 캐시 없음 — 로컬 yaml 파싱뿐(git fetch
    없음)이라 services_data 수준의 캐시가 굳이 필요하지 않다(저장소 HEAD 파일
    읽기는 요청마다 재계산해도 충분히 저렴한 오더: 노드 수백 개)."""
    try:
        return _build()
    except Exception:
        return None
