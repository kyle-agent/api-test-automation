"""서버타입 세대 핀 — {vs_server_type_prefix}/{db_server_type_filter} 토큰.

2026-07-29 타 오퍼링(west1) run 3b65 실측이 계기: 기저 VM 풀 자원부족으로
전 DB 클러스터가 create 202 → ~2분 내 FAILED (오너 확인 "신규 VM은 s2
타입으로 생성해야"). find-server-type 캡처의 where_prefix가 세대를
리터럴(id "s" / type "Standard-1")로 고정하고 있어 env로 핀할 수 없었다.
engine._capture가 ctx를 받아 필터 값의 {token}을 치환한다.
"""
from __future__ import annotations

from regression.scenarios import engine


def test_defaults_preserve_current_behavior(monkeypatch):
    monkeypatch.delenv("SCP_VS_SERVER_TYPE_PREFIX", raising=False)
    monkeypatch.delenv("SCP_DB_SERVER_TYPE", raising=False)
    assert engine._vs_server_type_prefix() == "s"
    assert engine._db_server_type_filter() == "Standard-1"


def test_env_pins_generation(monkeypatch):
    monkeypatch.setenv("SCP_VS_SERVER_TYPE_PREFIX", "s2")
    monkeypatch.setenv("SCP_DB_SERVER_TYPE", "Standard-2")
    assert engine._vs_server_type_prefix() == "s2"
    assert engine._db_server_type_filter() == "Standard-2"


def test_capture_fills_filter_tokens():
    body = {"contents": [
        {"name": "std1.small", "type": "Standard-1", "purpose": "general",
         "cpu_core": 2, "memory_gb": 4},
        {"name": "std2.small", "type": "Standard-2", "purpose": "general",
         "cpu_core": 2, "memory_gb": 4},
    ]}
    expr = {"list": "$.contents",
            "where_prefix": {"purpose": "general",
                             "type": "{db_server_type_filter}"},
            "min_by": ["cpu_core", "memory_gb"], "get": "name"}
    assert engine._capture(body, expr,
                           {"db_server_type_filter": "Standard-2"}) == "std2.small"
    assert engine._capture(body, expr,
                           {"db_server_type_filter": "Standard-1"}) == "std1.small"


def test_capture_fills_vs_prefix_and_not_prefix():
    body = {"server_types": [
        {"id": "g1.large"}, {"id": "s1.small"}, {"id": "s2.small"},
    ]}
    expr = {"list": "$.server_types",
            "where_prefix": {"id": "{vs_server_type_prefix}"},
            "where_not_prefix": {"id": "g"}, "get": "id"}
    assert engine._capture(body, expr, {"vs_server_type_prefix": "s2"}) == "s2.small"
    assert engine._capture(body, expr, {"vs_server_type_prefix": "s"}) == "s1.small"


def test_capture_without_ctx_unchanged():
    body = {"server_types": [{"id": "s1.small"}]}
    expr = {"list": "$.server_types", "where_prefix": {"id": "s"}, "get": "id"}
    assert engine._capture(body, expr) == "s1.small"
