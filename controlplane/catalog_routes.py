"""Catalog menu (① 재료, READ-ONLY) — /catalog 하위 (IA contract §4).

resource_routes.py 선례 그대로: 자체 APIRouter + 자체 Jinja2Templates(catalog.html)
로 app.py를 건드리지 않고 착륙한다 — 통합 소유자(lead)가 머지 시
``app.include_router(catalog_routes.router)`` + nav 슬롯을 배선한다.

  GET  /catalog       전체 API 인벤토리(data/api_catalog.json) 검색 + 카테고리▸서비스
                      그룹 표 (각 행 = ``METHOD path``).

순수 RO — 저장/편집 엔드포인트 없음(그래프 *이전* 목록). 각 서비스 헤더에
**``✏️ 레시피 편집 →``** 링크 = Modeling 의 노드 에디터로 deep-link:
service(category/service)가 resource_model 노드로 매핑되면
``/planning/resources/{node_id}``, 아니면 ``/planning/resources?service=...``
(둘 다 Modeling(다른 에이전트) 소유 라우트 — 여기서는 링크만 건다).
"""
from __future__ import annotations

import json
from collections import defaultdict
from functools import lru_cache
from pathlib import Path
from urllib.parse import quote

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates

from controlplane import common, resource_model

ROOT = Path(__file__).resolve().parent.parent
HERE = Path(__file__).resolve().parent
CATALOG_PATH = ROOT / "data" / "api_catalog.json"

templates = Jinja2Templates(directory=str(HERE / "templates"))

router = APIRouter(prefix="/catalog")


# --- 데이터 적재 / 변환 ----------------------------------------------------------------

def _load_catalog() -> list[dict]:
    """data/api_catalog.json -> 엔드포인트 dict 목록 (부재/깨짐 -> 빈 목록).

    페이지는 항상 떠야 하므로(자원 모델 로더와 동일 철학) best-effort.
    """
    try:
        data = json.loads(CATALOG_PATH.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []
    return [e for e in data if isinstance(e, dict)] if isinstance(data, list) else []


def _service_node_index() -> dict[str, str]:
    """catalog 의 ``category/service`` -> Modeling 노드 id (deep-link 대상).

    resource_model 노드의 ``service`` 필드가 'category/service' 형식이므로
    그대로 키로 쓴다. 한 서비스에 노드가 여럿이면 정렬상 첫 노드를 대표로
    링크한다(편집기에서 그 서비스의 다른 노드로 이동 가능). 모델 디렉토리
    부재(R1 머지 전)면 빈 인덱스 -> 모든 행이 service= 쿼리 링크로 degrade.
    """
    index: dict[str, str] = {}
    try:
        model = resource_model.load_model()
    except Exception:
        return index
    for nid in sorted(model):
        svc = str((model.get(nid) or {}).get("service") or "").strip()
        if svc and svc not in index:
            index[svc] = nid
    return index


def _recipe_link(service_key: str, node_id: str | None) -> str:
    """Modeling deep-link — 노드 매핑되면 노드 에디터, 아니면 service= 쿼리."""
    if node_id:
        return f"/planning/resources/{quote(node_id)}"
    return f"/planning/resources?service={quote(service_key)}"


def _endpoint_row(e: dict) -> dict:
    return {
        "method": str(e.get("method") or "").upper(),
        "path": str(e.get("http_path") or e.get("path") or ""),
        "name": str(e.get("name") or ""),
        "version": str(e.get("version") or ""),
        "doc_url": str(e.get("doc_url") or ""),
    }


@lru_cache(maxsize=1)
def _build_view() -> dict:
    """카탈로그 -> {categories:[{category, services:[{service, service_key,
    node_id, recipe, endpoints:[...]}], ...}], total, n_categories, n_services}.

    캐시는 프로세스 수명 동안 1회(카탈로그는 정적 인벤토리). 정렬: 카테고리/
    서비스 알파벳, 엔드포인트는 path 그다음 method.
    """
    node_index = _service_node_index()
    # category -> service -> [rows]
    grouped: dict[str, dict[str, list[dict]]] = defaultdict(lambda: defaultdict(list))
    for e in _load_catalog():
        category = str(e.get("category") or "(uncategorized)")
        service = str(e.get("service") or "(unknown)")
        grouped[category][service].append(_endpoint_row(e))

    categories: list[dict] = []
    total = 0
    n_services = 0
    for category in sorted(grouped):
        services: list[dict] = []
        for service in sorted(grouped[category]):
            rows = sorted(grouped[category][service],
                          key=lambda r: (r["path"], r["method"]))
            service_key = f"{category}/{service}"
            node_id = node_index.get(service_key)
            services.append({
                "service": service,
                "service_key": service_key,
                "node_id": node_id,
                "modeled": node_id is not None,
                "recipe": _recipe_link(service_key, node_id),
                "endpoints": rows,
                "count": len(rows),
            })
            total += len(rows)
            n_services += 1
        categories.append({
            "category": category,
            "services": services,
            "count": sum(s["count"] for s in services),
        })
    return {
        "categories": categories,
        "total": total,
        "n_categories": len(categories),
        "n_services": n_services,
        "n_modeled": sum(1 for c in categories for s in c["services"] if s["modeled"]),
    }


# --- 라우트 (READ-ONLY) ---------------------------------------------------------------

@router.get("", response_class=HTMLResponse)
@router.get("/", response_class=HTMLResponse)
def catalog_index(request: Request):
    view = _build_view()
    return templates.TemplateResponse(
        request,
        "catalog.html",
        {**common.base_ctx("catalog"), **view},
    )
