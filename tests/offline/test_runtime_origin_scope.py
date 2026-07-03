"""/runtime 스코프 — loggingaudit 스팬 × oplog 이벤트 origin join + scope/deleted
필터의 오프라인 단위 테스트 (합성 데이터만 — 네트워크·자격증명 없음).

audit.live_view:
  * build_spans      — loggingaudit 이벤트 → 스팬 (res_id 캡처 포함)
  * origin_of_run_id — run_id → local:<id> | ci:<id> | unknown 분류
  * annotate_origins — res_id 우선, 이름 fallback 매칭; oplog=None 이면 무표기
  * filter_spans     — scope=mine|all · deleted=hide|show
"""
from datetime import datetime, timezone

from audit import live_view as lv

NOW = datetime(2026, 7, 3, 12, 0, 0, tzinfo=timezone.utc)


def _ev(name, event_name, rtype="vpc", ts="2026-07-03T11:00:00Z", rid=None):
    e = {"resource_name": name, "resource_type": rtype,
         "event_name": event_name, "timestamp": ts}
    if rid:
        e["resource_id"] = rid
    return e


def _spans():
    events = [
        # 내 로컬 실행이 만든 VPC (res_id 매칭)
        _ev("regrvpc11111111", "Create VPC Start", rid="VPC-LOCAL"),
        _ev("regrvpc11111111", "Create VPC End", ts="2026-07-03T11:01:00Z", rid="VPC-LOCAL"),
        # CI 실행이 만든 서브넷 (이름 fallback 매칭 — resource_id 없음)
        _ev("regrsub22222222", "Create Subnet Start", rtype="subnet"),
        _ev("regrsub22222222", "Create Subnet End", rtype="subnet",
            ts="2026-07-03T11:02:00Z"),
        # 출처 불명 (oplog에 없음)
        _ev("regrsrv33333333", "Create Server Start", rtype="virtual-server"),
        # 내 로컬 실행이 만들고 이미 삭제한 리소스
        _ev("regrvpc44444444", "Create VPC Start", rid="VPC-DEL"),
        _ev("regrvpc44444444", "Create VPC End", ts="2026-07-03T11:03:00Z", rid="VPC-DEL"),
        _ev("regrvpc44444444", "Delete VPC End", ts="2026-07-03T11:10:00Z", rid="VPC-DEL"),
    ]
    return lv.build_spans(events, NOW, ours_only=True)


OPLOG = [
    {"_run_id": "20260703-120000-ab12", "action": "created",
     "res_id": "VPC-LOCAL", "name": "regrvpc11111111"},
    {"_run_id": "gha-chatheavy-999", "action": "created",
     "res_id": "", "name": "regrsub22222222"},
    {"_run_id": "20260703-120000-ab12", "action": "created",
     "res_id": "VPC-DEL", "name": "regrvpc44444444"},
]
LOCAL_IDS = ["20260703-120000-ab12"]


def _by_name(spans):
    return {d["name"]: d for d in spans.values()}


def test_build_spans_captures_res_id():
    d = _by_name(_spans())
    assert d["regrvpc11111111"]["res_id"] == "VPC-LOCAL"
    assert "res_id" not in d["regrsub22222222"]  # loggingaudit had no resource_id


def test_origin_of_run_id_classification():
    assert lv.origin_of_run_id("20260703-120000-ab12", LOCAL_IDS) == \
        "local:20260703-120000-ab12"
    assert lv.origin_of_run_id("local", []) == "local:local"          # off-CI fallback
    assert lv.origin_of_run_id("gha-chatheavy-999", []) == "ci:gha-chatheavy-999"
    assert lv.origin_of_run_id("27346642059", []) == "ci:27346642059"  # bare GITHUB_RUN_ID
    assert lv.origin_of_run_id("oplog-test-1", []) == "unknown"
    assert lv.origin_of_run_id("", []) == "unknown"


def test_annotate_origins_join_res_id_then_name():
    spans = _spans()
    lv.annotate_origins(spans, OPLOG, local_run_ids=LOCAL_IDS)
    d = _by_name(spans)
    assert d["regrvpc11111111"]["origin"] == "local:20260703-120000-ab12"  # res_id 매칭
    assert d["regrsub22222222"]["origin"] == "ci:gha-chatheavy-999"        # 이름 fallback
    assert d["regrsrv33333333"]["origin"] == "unknown"                     # 미매칭
    assert d["regrvpc44444444"]["origin"].startswith("local:")


def test_annotate_origins_none_degrades_to_today():
    spans = _spans()
    lv.annotate_origins(spans, None, local_run_ids=LOCAL_IDS)
    assert all("origin" not in d for d in spans.values())   # 버킷 불가 → 무표기


def test_filter_spans_scope_and_deleted():
    spans = _spans()
    lv.annotate_origins(spans, OPLOG, local_run_ids=LOCAL_IDS)
    mine = _by_name(lv.filter_spans(spans, scope="mine", deleted="hide"))
    # mine: 내 로컬 실행 것만, 삭제됨은 기본 숨김
    assert set(mine) == {"regrvpc11111111"}
    mine_del = _by_name(lv.filter_spans(spans, scope="mine", deleted="show"))
    assert set(mine_del) == {"regrvpc11111111", "regrvpc44444444"}
    alls = _by_name(lv.filter_spans(spans, scope="all", deleted="hide"))
    assert set(alls) == {"regrvpc11111111", "regrsub22222222", "regrsrv33333333"}
    all_del = _by_name(lv.filter_spans(spans, scope="all", deleted="show"))
    assert len(all_del) == 4


def test_render_flow_chrome_banner_and_origin_badge():
    spans = _spans()
    lv.annotate_origins(spans, OPLOG, local_run_ids=LOCAL_IDS)
    shown = lv.filter_spans(spans, scope="all", deleted="show")
    html = lv.render_flow(shown, NOW, {"start": "s", "end": "e"},
                          chrome={"scope": "all", "hours": 1, "deleted": "show",
                                  "banner": "계정 전체 뷰 — 다른 run·CI 자원 포함"})
    assert "계정 전체 뷰" in html                        # all-모드 배너
    assert "내 실행" in html and ">CI<" in html          # origin 배지
    assert "/testing/resources" in html                  # 위생 화면 교차 링크
    assert "← Test Execution" in html                    # Testing 셸 헤더
    assert "scope=mine" in html and "deleted=" in html   # 컨트롤 바
