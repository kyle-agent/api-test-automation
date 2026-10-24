"""spec.refresh_versions — Scp-Api-Version 사이드파일 재생성기 (2026-09-18).

881개 엔드포인트 버전업 + 신규 상품 4개를 손으로 따라갈 수 없어 생성기로 고정:
endpoint 버전은 카탈로그의 엔드포인트 CURRENT 버전, 제품 버전은 docs 버전 페이지
(표시명→코드 매핑) 폴백 카탈로그 최대 버전.
"""
from __future__ import annotations

from spec import refresh_versions as rv

CAT = [
    {"service": "vs", "method": "GET", "http_path": "/v1/servers", "version": "1.5"},
    {"service": "vs", "method": "POST", "http_path": "/v1/servers/{server_id}/password", "version": "1.3"},
    {"service": "newsvc", "method": "GET", "http_path": "/v1/things/{id}", "version": "1.10"},
    {"service": "newsvc", "method": "POST", "http_path": "/v1/things", "version": "1.9"},
    {"service": "broken", "method": None, "http_path": None, "version": "1.0"},
]


def test_endpoint_versions_collapse_path_params_and_keep_per_endpoint_version():
    ep = rv.endpoint_versions(CAT)["services"]
    assert ep["vs"] == {"GET /v1/servers": "1.5", "POST /v1/servers/{}/password": "1.3"}
    assert ep["newsvc"]["GET /v1/things/{}"] == "1.10"
    assert "broken" not in ep


def test_products_prefer_version_page_then_catalog_max():
    existing = {"display_names": {"vs": "Virtual Server"}, "supported": {"vs": ["1.4"]},
                "products": {"vs": "1.4"}}
    page = {"Virtual Server": "1.5", "Message Hub": "1.0", "Unknown Thing": "2.0"}
    out = rv.products(CAT, page, existing)
    assert out["products"]["vs"] == "1.5" and out["product_version_source"]["vs"] == "version-page"
    # newsvc not on the page and not in the display map -> catalog max (1.10 > 1.9 numerically)
    assert out["products"]["newsvc"] == "1.10" and out["product_version_source"]["newsvc"] == "catalog-max"
    assert out["supported"] == {"vs": ["1.4"]}                 # carried over, never derived
    assert {"display_name": "Unknown Thing", "version": "2.0"} in out["unmapped_display_names"]
    assert out["display_names"]["messagehub"] == "Message Hub"  # EXTRA_DISPLAY_NAMES merged


def test_parse_version_page_extracts_name_version_pairs():
    html = "<html><body><ul><li>Virtual Server v1.5</li><li>Message Hub v1.0</li></ul></body></html>"
    got = rv.parse_version_page(html)
    assert got["Virtual Server"] == "1.5" and got["Message Hub"] == "1.0"
