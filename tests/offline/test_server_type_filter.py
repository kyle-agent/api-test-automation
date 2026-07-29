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


def test_db_name_prefix_pin_auto_disables_type_filter(monkeypatch):
    """이름 핀(db2v2m4 꼴 오퍼링) 사용 시 type 필터는 자동 no-op — 둘이
    교집합을 비워 캡처가 빈손이 되는 사고 방지."""
    monkeypatch.delenv("SCP_DB_SERVER_TYPE", raising=False)
    monkeypatch.setenv("SCP_DB_SERVER_TYPE_NAME_PREFIX", "db2")
    assert engine._db_server_type_name_prefix() == "db2"
    assert engine._db_server_type_filter() == ""
    body = {"contents": [
        {"name": "db1v2m4", "type": "Standard-1", "purpose": "general",
         "cpu_core": 2, "memory_gb": 4},
        {"name": "db2v2m8", "type": "Standard(db2)", "purpose": "general",
         "cpu_core": 2, "memory_gb": 8},
        {"name": "db2v2m4", "type": "Standard(db2)", "purpose": "general",
         "cpu_core": 2, "memory_gb": 4},
    ]}
    expr = {"list": "$.contents",
            "where_prefix": {"purpose": "general",
                             "name": "{db_server_type_name_prefix}",
                             "type": "{db_server_type_filter}"},
            "min_by": ["cpu_core", "memory_gb"], "get": "name"}
    ctx = {"db_server_type_name_prefix": engine._db_server_type_name_prefix(),
           "db_server_type_filter": engine._db_server_type_filter()}
    assert engine._capture(body, expr, ctx) == "db2v2m4"   # min_by가 최소 선택


def test_db_type_star_disables_filter(monkeypatch):
    monkeypatch.setenv("SCP_DB_SERVER_TYPE", "*")
    assert engine._db_server_type_filter() == ""


def test_db_name_prefix_service_map(monkeypatch):
    """패밀리별 접두 맵 (2026-07-30 실측: DB=db2*, eventstreams=ess2* — 접두가
    패밀리마다 다름). 맵 사용 시에도 type 필터는 자동 해제(env 원문 판정)."""
    monkeypatch.delenv("SCP_DB_SERVER_TYPE", raising=False)
    monkeypatch.setenv("SCP_DB_SERVER_TYPE_NAME_PREFIX",
                       "mysql=db2,eventstreams=ess2,*=db2")
    assert engine._db_server_type_name_prefix("database/mysql") == "db2"
    assert engine._db_server_type_name_prefix("data-analytics/eventstreams") == "ess2"
    assert engine._db_server_type_name_prefix("database/cachestore") == "db2"  # '*'
    assert engine._db_server_type_filter() == ""  # 맵이어도 자동 해제


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
