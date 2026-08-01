"""서버타입 세대 핀 — {vs_server_type_prefix}/{db_server_type_filter} 토큰.

2026-07-29 타 오퍼링(west1) run 3b65 실측이 계기: 기저 VM 풀 자원부족으로
전 DB 클러스터가 create 202 → ~2분 내 FAILED (오너 확인 "신규 VM은 s2
타입으로 생성해야"). find-server-type 캡처의 where_prefix가 세대를
리터럴(id "s" / type "Standard-1")로 고정하고 있어 env로 핀할 수 없었다.
engine._capture가 ctx를 받아 필터 값의 {token}을 치환한다.
"""
from __future__ import annotations

import pathlib

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


def test_vs_server_type_default(monkeypatch):
    """{vs_server_type} — 하드코딩 create 바디의 타입 리터럴 토큰 (2026-08-01,
    s2 오퍼링: s1 풀 고갈로 리터럴 s1v1m2 create 클래스 6곳 전멸이 계기)."""
    monkeypatch.delenv("SCP_VS_SERVER_TYPE", raising=False)
    monkeypatch.delenv("SCP_VS_SERVER_TYPE_PREFIX", raising=False)
    assert engine._vs_server_type() == "s1v1m2"


def test_vs_server_type_explicit_env_wins(monkeypatch):
    monkeypatch.setenv("SCP_VS_SERVER_TYPE", "x9v9m99")
    monkeypatch.setenv("SCP_VS_SERVER_TYPE_PREFIX", "s2")
    assert engine._vs_server_type() == "x9v9m99"


def test_vs_server_type_derives_from_prefix(monkeypatch):
    """PREFIX 하나로 캡처 스텝(min_by 최소)과 리터럴 create 스텝이 같은 세대를
    보게 한다 — 세대 접두면 이름 문법 바닥 v1m2를 붙이고(s2→s2v1m2, 오너 픽커
    실측 존재), 풀네임이면 그대로, 문법 밖이면 기본으로 안전 후퇴."""
    monkeypatch.delenv("SCP_VS_SERVER_TYPE", raising=False)
    monkeypatch.setenv("SCP_VS_SERVER_TYPE_PREFIX", "s2")
    assert engine._vs_server_type() == "s2v1m2"
    monkeypatch.setenv("SCP_VS_SERVER_TYPE_PREFIX", "s2v4m8")
    assert engine._vs_server_type() == "s2v4m8"
    monkeypatch.setenv("SCP_VS_SERVER_TYPE_PREFIX", "std")
    assert engine._vs_server_type() == "s1v1m2"


def test_capture_fills_token_inside_where_not_list():
    """virtualserver-actions resize 캡처: 제외 목록의 create 타입이 토큰이라
    PREFIX 핀을 따라간다 — s2 핀이면 s2v1m2(=create)를 빼고 다음 최소를 집는다."""
    body = {"server_types": [
        {"id": "g1v4m32", "vcpus": 4, "ram": 32},
        {"id": "s2v1m2", "vcpus": 1, "ram": 2},
        {"id": "s2v2m4", "vcpus": 2, "ram": 4},
        {"id": "s2v4m8", "vcpus": 4, "ram": 8},
    ]}
    expr = {"list": "$.server_types",
            "where_prefix": {"id": "{vs_server_type_prefix}"},
            "where_not_prefix": {"id": ["g", "{vs_server_type}"]},
            "get": "id", "min_by": ["vcpus", "ram"]}
    ctx = {"vs_server_type_prefix": "s2", "vs_server_type": "s2v1m2"}
    assert engine._capture(body, expr, ctx) == "s2v2m4"


def test_no_literal_vs_server_type_hardcodes_left():
    """값 위치의 s{n}v{c}m{m} 타입 리터럴 금지 (_note 프로즈만 허용) — 리터럴은
    SCP_VS_SERVER_TYPE(_PREFIX) 핀을 우회해 세대 교체 오퍼링에서 재발한다."""
    root = pathlib.Path(engine.__file__).resolve().parent
    files = list((root / "lifecycles").glob("*.json")) + [root / "scenarios.json"]
    bad = []
    for p in files:
        for i, line in enumerate(p.read_text(encoding="utf-8").splitlines(), 1):
            if '"_note"' in line or '"_comment"' in line:
                continue
            if '"s1v1m2"' in line:
                bad.append(f"{p.name}:{i}")
    assert not bad, f"VS 서버타입 리터럴 잔존 (값 위치): {bad}"
