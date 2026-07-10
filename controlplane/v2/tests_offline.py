"""v2 offline smoke tests — no network, no bucket, throwaway DB.

    PYTHONPATH=. python3 controlplane/v2/tests_offline.py
"""
from __future__ import annotations

import json
import os
import re
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
    assert "Overview" in body  # 페이지 h1 — D7 개정(2026-07-10): 이름은 영어
    assert "SCP API Regression" in body
    # 판정 헤드라인 또는 empty-state 중 하나는 반드시 존재 (L1 §2.1/§3)
    assert ("New regressions" in body) or ("발행된 공식 수치가 아직 없습니다" in body)
    # 로컬 관측 empty-state는 0이 아니라 안내문 (원칙 1-3)
    assert ("이 서버에서 실행된 런이 없습니다" in body) or ("Runs on this server" in body)


def test_axes_render():
    # /v2/run·/v2/model은 실화면으로 전환됨(§2.6·§2.7) — 전용 테스트로 분리
    for path in ("/v2/tools",):
        r = client.get(path)
        assert r.status_code == 200, f"{path} -> {r.status_code}"
        assert "기존 화면" in r.text or "이용하세요" in r.text


def test_services_list_renders():
    r = client.get("/v2/services")
    assert r.status_code == 200, r.status_code
    body = r.text
    assert "Services" in body
    # 서비스 1개 이상 렌더되거나 empty-state 중 하나는 반드시 존재 (L1 §2.2/§3)
    assert ("Details" in body) or ("발행된 공식 수치를 가져올 수 없습니다" in body) \
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
    assert "Endpoints" in body
    assert "Run this service" in body
    assert "/testing?service=" in body  # 기존 prefill 계약 딥링크


def test_service_detail_unknown_slug_is_404():
    r = client.get("/v2/services/__no-such-service__")
    assert r.status_code == 404, r.status_code
    assert "찾을 수 없" in r.text
    assert "/v2/services" in r.text


def test_service_detail_filter_bar_and_resource_groups():
    """발행 페이지 패리티 리디자인(2026-07-10) — 필터 바(Resource groups/Defects
    only/Slow/HTTP status) + 히어로 + 액션 배너 + JS가 소비하는 행 JSON(그룹·
    slow·근거 run id 필드)이 실제로 응답에 실려 있는지 확인한다."""
    from controlplane.v2 import services_data
    data = services_data.get_services_data()
    if not data or not data.get("services"):
        print("  (skip: 발행본 접근 불가 - empty-state는 위 테스트에서 검증됨)")
        return
    svc = next((s for s in data["services"] if s.get("endpoint_rows")), None)
    if not svc:
        print("  (skip: rows가 있는 서비스 없음)")
        return
    r = client.get(f"/v2/services/{svc['slug']}")
    assert r.status_code == 200, r.status_code
    body = r.text
    for label in ("Resource groups", "Defects only", "Slow (", "All HTTP status", "Reset"):
        assert label in body, label
    assert "Verified (2xx) coverage" in body
    assert "action-banner" in body

    m = re.search(r'<script type="application/json" id="ep-rows-data">(.*?)</script>', body, re.S)
    assert m, "행 JSON 스크립트가 없음"
    rows = json.loads(m.group(1))
    assert rows, "행이 비어 있음"
    for row in rows:
        for key in ("group", "slow", "ev", "cov", "status", "elapsed_s"):
            assert key in row, key
        parts = row["path"].split("/")
        expected_group = parts[2] if len(parts) > 2 and parts[2] else "other"
        assert row["group"] == expected_group, (row["path"], row["group"])


def test_service_detail_group_counts_match_endpoints():
    """리소스 그룹으로 나눠도 엔드포인트 총합이 그대로여야 한다(행 누락 없음)."""
    from controlplane.v2 import services_data
    data = services_data.get_services_data()
    if not data or not data.get("services"):
        print("  (skip: 발행본 접근 불가 - empty-state는 위 테스트에서 검증됨)")
        return
    svc = next((s for s in data["services"] if len(s.get("endpoint_rows") or []) >= 2), None)
    if not svc:
        print("  (skip: rows가 2개 이상인 서비스 없음)")
        return
    rows = svc["endpoint_rows"]
    groups: dict[str, list] = {}
    for row in rows:
        groups.setdefault(row["group"], []).append(row)
    assert sum(len(v) for v in groups.values()) == len(rows)
    assert len(groups) >= 1


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


def test_results_axis_renders():
    r = client.get("/v2/results")
    assert r.status_code == 200, r.status_code
    body = r.text
    assert "Results" in body
    # 회귀 섹션은 실제 목록 또는 empty-state 중 하나로 반드시 성립 (L1 §2.5/§3)
    assert ("Status then" in body) or ("없음 — 배포 안전" in body) \
        or ("현재 발행본에 포함되어 있지 않습니다" in body) \
        or ("발행된 공식 수치를 가져올 수 없습니다" in body)
    assert "Conformance changes" in body
    assert ("Known issues" in body)


def test_results_new_regressions_detail_when_available():
    # 실측(계약 §2.5 작성 시점)에는 발행 배너에 이 두 항목이 있었다. 발행본이
    # 그 사이 바뀌었을 수 있어(오늘 재확인: 최신 발행은 healthy — Hard Rule 5,
    # 기억보다 현재 관측이 우선) 존재를 강제하지 않고 있으면 렌더까지 검증한다.
    from controlplane.v2 import results_data
    rows = results_data.get_new_regressions()
    if not rows:
        print("  (skip: 발행 배너에 새 회귀 상세 없음 - empty-state는 위 테스트에서 검증됨)")
        return
    r = client.get("/v2/results")
    body = r.text
    for row in rows:
        assert row["key"] in body
    keys = {row["key"] for row in rows}
    if ("networking-loadbalancer-members-nat:static-nat-create" in keys
            and "networking/loadbalancer/createloadbalancerpublicnatip" in keys):
        assert "이중 기록 추정" in body


def test_new_regressions_enrichment_is_deterministic():
    # 라이브 발행본 상태와 무관하게(임시 우회 파서의 견고성) 이중 기록 감지 +
    # 카탈로그/합성 키 분기 + 딥링크를 직접 검증한다.
    from controlplane.v2 import results_data
    parsed = [
        ("networking-loadbalancer-members-nat:static-nat-create", 500),
        ("networking/loadbalancer/createloadbalancerpublicnatip", 500),
    ]
    rows = results_data._enrich_new_regressions(parsed)
    by_key = {row["key"]: row for row in rows}
    synth = by_key["networking-loadbalancer-members-nat:static-nat-create"]
    cat = by_key["networking/loadbalancer/createloadbalancerpublicnatip"]
    assert synth["kind"] == "synthetic"
    assert cat["kind"] == "catalog"
    assert cat["link"] == "/v2/services/networking__loadbalancer"
    assert synth["dup_of"] and cat["dup_of"]


def test_banner_regex_parses_synthetic_html_fixture():
    # dashboard/build.py:1150-1154이 실제로 내는 마크업 구조를 흉내낸 고정
    # fixture로 정규식 파서 자체를 라이브 상태와 독립적으로 검증한다.
    from controlplane.v2 import results_data
    snippet = (
        '<div class="action bad"><div><b>새 회귀 2건 — 조치 필요.</b>'
        '<div><code>networking-loadbalancer-members-nat:static-nat-create</code> → 500</div>'
        '<div><code>networking/loadbalancer/createloadbalancerpublicnatip</code> → 500</div>'
        '</div></div>')
    m = results_data._BANNER_RE.search(snippet)
    assert m
    items = results_data._ITEM_RE.findall(m.group(1))
    assert len(items) == 2


def test_known_issues_shape_or_none():
    from controlplane.v2 import results_data
    issues = results_data.get_known_issues()
    assert issues is None or isinstance(issues, list)
    if issues:
        assert all("key" in it and "status" in it for it in issues)


def test_conformance_changes_shape_or_none():
    from controlplane.v2 import results_data
    changes = results_data.get_conformance_changes()
    assert changes is None or all(k in changes for k in ("new", "regressed", "fixed"))


def test_run_axis_renders_with_gate_panel():
    r = client.get("/v2/run")
    assert r.status_code == 200, r.status_code
    body = r.text
    assert "Gate status" in body
    assert "Plan" in body
    assert "Live runs" in body
    assert "History" in body
    # 계획 경험(선택 트리→DAG→견적→pre-flight) 골격 + 렌더러/JS 로드 (계약 §2.6 개정)
    assert 'id="rp-root"' in body
    assert "resource_graph.js" in body and "runs_plan.js" in body
    # v2 자체 발사는 여전히 없음 — [Run live]는 disabled (검수 후 활성화)
    assert "POST /api/run" not in body or "발사(POST /api/run)는 하지 않는다" in body


def test_run_axis_gate_values_match_settings_no_hardcoding():
    # 실효 게이트는 core.config.settings에서 읽어야 한다(하드코딩 금지, §2.6).
    # 테스트 env는 이 파일 상단에서 SCP_ALLOW_DESTRUCTIVE=false로 고정했다 —
    # 화면 표기가 그 실측과 일치하는지 확인한다.
    import core.config as _core_cfg
    settings = _core_cfg.settings
    assert settings.allow_destructive is False  # 이 테스트 스위트의 전제
    r = client.get("/v2/run")
    body = r.text
    assert "Destructive OFF" in body
    assert ("Mutations ON" in body) == bool(settings.allow_mutations)
    assert ("Heavy ON" in body) == bool(settings.run_heavy)
    if settings.allow_mutations:
        assert "실제 클라우드에 작용합니다" in body
    else:
        assert "열람용(read-only)으로 기동되어 실행이 비활성화" in body


def test_run_axis_readonly_mode_banner_and_disabled_cta():
    # SCP_ALLOW_MUTATIONS=false 기동 = 열람 모드 (D2/pre-flight 결정) —
    # 싱글턴을 임시로 monkeypatch해 그 분기를 직접 확인하고 원복한다.
    import core.config as _core_cfg
    settings = _core_cfg.settings
    original = settings.allow_mutations
    object.__setattr__(settings, "allow_mutations", False)
    try:
        r = client.get("/v2/run")
        assert r.status_code == 200
        body = r.text
        assert "Mutations OFF" in body
        assert "이 서버는 열람용(read-only)으로 기동되어 실행이 비활성화되어 있습니다" in body
        # 게이트 데이터 아일랜드가 mutations=false를 실어 JS가 Review 버튼을 잠근다
        assert '"mutations": false' in body or '"mutations":false' in body
    finally:
        object.__setattr__(settings, "allow_mutations", original)


def test_run_axis_prefill_query_reflected():
    r = client.get("/v2/run?service=networking%2Floadbalancer")
    assert r.status_code == 200
    body = r.text
    # 프리필은 선택 트리 초기화용 data 속성으로 승계 (runs_plan.js가 소비)
    assert 'data-prefill-service="networking/loadbalancer"' in body


def test_run_axis_no_post_route():
    # v1은 발사 없음 — /v2/run은 조회 전용, POST 라우트가 존재하지 않는다.
    r = client.post("/v2/run")
    assert r.status_code in (404, 405), r.status_code


def test_model_renders():
    r = client.get("/v2/model")
    assert r.status_code == 200, r.status_code
    body = r.text
    assert "Model table" in body or "정의된 리소스 모델 노드가 없습니다" in body
    assert "Node editor (legacy)" in body


def test_model_data_shape():
    from controlplane.v2 import model_data
    d = model_data.get_model_data()
    assert d is None or isinstance(d.get("groups"), list)
    if d and d.get("groups"):
        g = d["groups"][0]
        assert {"gid", "node_count", "validated", "docs", "incomplete", "nodes"} <= g.keys()


def test_run_detail_local_renders():
    db.record_local_run("local-detail-test-0001", suite="console2", status="done")
    r = client.get("/v2/runs/local-detail-test-0001")
    assert r.status_code == 200, r.status_code
    assert "Official aggregation" in r.text  # 로컬 런 전용 fold 섹션 (§2.4)


def test_run_detail_unknown_is_404():
    r = client.get("/v2/runs/local-no-such-run")
    assert r.status_code == 404, r.status_code
    assert "찾을 수 없" in r.text


def test_run_detail_ci_hides_fold_section():
    db.create_run("smoke", "default", trigger="external", gh_run_id="999001")
    r = client.get("/v2/runs/999001")
    assert r.status_code == 200, r.status_code
    assert "Official aggregation" not in r.text  # fold 동선은 로컬 런 전용


def test_search_axis_empty_state_for_short_query():
    r = client.get("/v2/search?q=a")
    assert r.status_code == 200, r.status_code
    assert "검색어를 2자 이상 입력해 주세요" in r.text


def test_search_axis_finds_service_and_endpoint():
    r = client.get("/v2/search?q=load")
    assert r.status_code == 200, r.status_code
    body = r.text
    assert "Services" in body and "Endpoints" in body and "Runs" in body
    from controlplane.v2 import search_data
    data = search_data.search("load")
    if data["services"]["items"]:
        assert data["services"]["items"][0]["slug"] in body


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
