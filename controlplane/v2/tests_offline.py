"""v2 offline smoke tests — no network, no bucket, throwaway DB.

    PYTHONPATH=. python3 controlplane/v2/tests_offline.py
"""
from __future__ import annotations

import os
import sys
import tempfile

# fresh throwaway DB + clean env BEFORE the app import (기존 tests_offline.py 관례)
os.environ["PLATFORM_DB"] = os.path.join(
    tempfile.mkdtemp(prefix="platform-v2-test-"), "platform.db")
os.environ["SCP_ALLOW_DESTRUCTIVE"] = "false"

from fastapi.testclient import TestClient  # noqa: E402

from controlplane import db  # noqa: E402
from controlplane.app import app  # noqa: E402
from controlplane.v2 import published  # noqa: E402

client = TestClient(app)
FAILED: list[str] = []


def check(name: str, fn):
    try:
        fn()
        print(f"  ok  {name}")
    except Exception as e:  # noqa: BLE001
        FAILED.append(name)
        print(f"FAIL  {name}: {e}")


def test_situation_renders():
    r = client.get("/v2")
    assert r.status_code == 200, r.status_code
    body = r.text
    assert "플랫폼 현황" in body  # 격식 명사형 — 구어체·질문형 금지 (D7 추가, 2026-07-10)
    assert "SCP API Regression" in body
    # 판정 헤드라인 또는 empty-state 중 하나는 반드시 존재 (L1 §2.1/§3)
    assert ("새 회귀" in body) or ("발행된 공식 수치가 아직 없습니다" in body)
    # 로컬 관측 empty-state는 0이 아니라 안내문 (원칙 1-3)
    assert ("이 서버에서 실행된 런이 없습니다" in body) or ("이 서버의 런" in body)


def test_axes_render():
    for path in ("/v2/model", "/v2/run", "/v2/results", "/v2/tools"):
        r = client.get(path)
        assert r.status_code == 200, f"{path} -> {r.status_code}"
        assert "기존 화면" in r.text or "이용하세요" in r.text


def test_services_list_renders():
    r = client.get("/v2/services")
    assert r.status_code == 200, r.status_code
    body = r.text
    assert "서비스별 테스트 현황" in body
    # 서비스 1개 이상 렌더되거나 empty-state 중 하나는 반드시 존재 (L1 §2.2/§3)
    assert ("상세 보기" in body) or ("발행된 공식 수치를 가져올 수 없습니다" in body) \
        or ("표시할 서비스가 없습니다" in body)


def test_service_detail_renders_or_skips_offline():
    from controlplane.v2 import services_data
    data = services_data.get_services_data()
    if not data or not data.get("services"):
        print("  (skip: 발행본 접근 불가 - empty-state는 위 테스트에서 검증됨)")
        return
    svc_slug = data["services"][0]["slug"]
    r = client.get(f"/v2/services/{svc_slug}")
    assert r.status_code == 200, r.status_code
    body = r.text
    assert "엔드포인트 목록" in body
    assert "이 서비스 실행" in body
    assert "/testing?service=" in body  # 기존 prefill 계약 딥링크


def test_service_detail_unknown_slug_is_404():
    r = client.get("/v2/services/__no-such-service__")
    assert r.status_code == 404, r.status_code
    assert "찾을 수 없" in r.text
    assert "/v2/services" in r.text


def test_local_run_shows_in_isolated_section():
    db.record_local_run("local-test-0001", suite="console2", status="done")
    r = client.get("/v2")
    assert r.status_code == 200
    assert "local-test-0001" in r.text


def test_static_css_served_and_traversal_blocked():
    r = client.get("/v2/static/v2.css")
    assert r.status_code == 200 and "badge-published" in r.text
    r2 = client.get("/v2/static/../routes.py")
    assert r2.status_code == 404, r2.status_code


def test_published_meta_shape():
    m = published.meta()
    for k in ("ok", "updated", "updated_label", "sha", "stale", "age_hours"):
        assert k in m, k
    # ok=False여도 화면은 성립해야 함 — situation이 200을 주는지는 위에서 검증됨


def test_existing_home_untouched():
    # 스트랭글러 불변식: v2 마운트가 기존 화면을 깨지 않는다
    r = client.get("/")
    assert r.status_code == 200, r.status_code


if __name__ == "__main__":
    for name, fn in sorted(
            {k: v for k, v in globals().items() if k.startswith("test_")}.items()):
        check(name, fn)
    if FAILED:
        print(f"\n{len(FAILED)} failed: {FAILED}")
        sys.exit(1)
    print("\nall v2 offline tests passed")
