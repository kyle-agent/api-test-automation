"""/runtime 스코프 — loggingaudit 스팬 × oplog 이벤트 origin join + scope/deleted
필터의 오프라인 단위 테스트 (합성 데이터만 — 네트워크·자격증명 없음).

audit.live_view:
  * build_spans            — loggingaudit 이벤트 → 스팬 (res_id 캡처 포함)
  * origin_of_run_id       — run_id → local:<id> | ci:<id> | unknown 분류
  * annotate_origins       — res_id 우선, 이름 fallback 매칭; oplog=None 이면 무표기
  * annotate_local_origins — 콘솔 in-process 기록 overlay (버킷 join 보다 우선)
  * filter_spans           — scope=mine|all · deleted=hide|show

console2_server (2026-07-04 결함 후속 — 버킷 없이도 mine 귀속, active-run 폴백):
  * _local_res_index — rec 이벤트 파일 + core.registry 샤드 → mine-set
  * _runtime_view    — 로컬 귀속 성공 / 실행 중 귀속 실패 배너 / 유휴 폴백 노트
"""
import importlib.util
import json
import time
from datetime import datetime, timezone
from pathlib import Path

from audit import live_view as lv

ROOT = Path(__file__).resolve().parents[2]
NOW = datetime(2026, 7, 3, 12, 0, 0, tzinfo=timezone.utc)


def _ev(name, event_name, rtype="vpc", ts="2026-07-03T11:00:00Z", rid=None):
    e = {"resource_name": name, "resource_type": rtype,
         "event_name": event_name, "timestamp": ts}
    if rid:
        e["resource_id"] = rid
    return e


AUDIT_EVENTS = [
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


def _spans():
    return lv.build_spans(list(AUDIT_EVENTS), NOW, ours_only=True)


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


def test_annotate_local_origins_res_id_then_name_and_wins_over_bucket():
    spans = _spans()
    lv.annotate_origins(spans, OPLOG, local_run_ids=LOCAL_IDS)
    lv.annotate_local_origins(spans, {
        "rec-1": {"ids": {"VPC-LOCAL"}, "names": {"regrsrv33333333",
                                                  "regrsub22222222"}}})
    d = _by_name(spans)
    assert d["regrvpc11111111"]["origin"] == "local:rec-1"   # res_id 매칭
    assert d["regrsrv33333333"]["origin"] == "local:rec-1"   # 이름 매칭 (was unknown)
    # in-process 기록은 버킷 join 보다 우선 — 내 실행 기록이 있으면 ci 표기도 뒤집는다
    assert d["regrsub22222222"]["origin"] == "local:rec-1"
    assert d["regrvpc44444444"]["origin"].startswith("local:")  # 무관 스팬은 그대로


def test_annotate_local_origins_empty_index_noop():
    spans = _spans()
    lv.annotate_local_origins(spans, None)
    assert all("origin" not in d for d in spans.values())
    lv.annotate_local_origins(spans, {"rec": {"ids": set(), "names": set()}})
    assert all("origin" not in d for d in spans.values())


def test_local_attribution_without_bucket_enables_mine_scope():
    """버킷 불가(oplog=None)여도 로컬 기록만으로 scope=mine 이 동작해야 한다 —
    P1-1 후속 결함(2026-07-04)의 핵심 요구."""
    spans = _spans()
    lv.annotate_origins(spans, None, local_run_ids=LOCAL_IDS)   # 버킷 다운
    lv.annotate_local_origins(spans, {"rec-9": {"ids": {"VPC-LOCAL"}, "names": ()}})
    mine = _by_name(lv.filter_spans(spans, scope="mine", deleted="hide"))
    assert set(mine) == {"regrvpc11111111"}


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


# --------------------------------------------------------------------------- #
# console2_server 수준 — 로컬 매니페스트 귀속 + active-run 폴백 배너 (2026-07-04)
# --------------------------------------------------------------------------- #
def _load_server():
    """tools/console2_server.py 를 매 테스트 새 모듈로 로드 (tools 는 패키지가
    아니고, _RUNS/_RUNTIME_CACHE 모듈 전역을 테스트 간 격리하기 위해)."""
    spec = importlib.util.spec_from_file_location(
        "console2_server_rt_test", ROOT / "tools" / "console2_server.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _prime(C2, tmp_path, monkeypatch, *, run_status, tracked=(), oplog=None,
           run_age_s=0.0):
    """합성 로컬 run rec(+events 파일) 1건과 fresh 런타임 캐시를 심는다."""
    monkeypatch.setattr(C2, "ROOT", tmp_path)   # registry 샤드 glob → tmp (비어있음)
    evfile = tmp_path / "rec.events.jsonl"
    with open(evfile, "w", encoding="utf-8") as fh:
        for t in tracked:
            fh.write(json.dumps({"ts": 1.0, "kind": "resource-tracked", **t},
                                ensure_ascii=False) + "\n")
    C2._RUNS["t-rec-1"] = {"id": "t-rec-1", "kind": "run", "status": run_status,
                           "events": str(evfile),
                           "started": time.time() - run_age_s}
    C2._LOCAL_RES_CACHE.update(ts=0.0, val=None)   # memo 무효화 (테스트 간 격리)
    C2._RUNTIME_CACHE.update(events=list(AUDIT_EVENTS), oplog=oplog, error=None,
                             meta={"start": "s", "end": "e"},
                             ts=time.monotonic(), hours=1.0, generating=False)


def test_runtime_view_mine_from_local_records_without_bucket(tmp_path, monkeypatch):
    """ACTIVE 로컬 실행 + 버킷 join 불가(oplog=None) — in-process 기록(res_id)만으로
    scope=mine 이 내 스팬을 렌더해야 한다 (결함 전: 빈 페이지)."""
    C2 = _load_server()
    _prime(C2, tmp_path, monkeypatch, run_status="running", oplog=None,
           tracked=[{"lifecycle": "compute-virtualserver-full",
                     "resource_id": "VPC-LOCAL", "resource_type": "vpc",
                     "service": "vpc", "path": "/v1/vpcs",
                     "name": "regrvpc11111111"}])
    html, ready = C2._runtime_view(1.0, scope="mine", deleted="hide")
    assert ready
    assert "regrvpc11111111" in html          # 내 스팬 — 로컬 귀속 성공
    assert "regrsub22222222" not in html      # 남의 스팬은 mine 에서 제외
    assert "귀속 실패" not in html            # 성공 경로에 실패 배너 없음


def test_runtime_view_active_run_attribution_failure_banner(tmp_path, monkeypatch):
    """로컬 실행이 ACTIVE + grace 초과인데 mine 귀속이 0건 — 절대 빈 페이지가 아니라
    계정 전체 + 진단 배너로 폴백해야 한다 (오너가 맞은 최악 케이스)."""
    C2 = _load_server()
    _prime(C2, tmp_path, monkeypatch, run_status="running", oplog=None, tracked=[],
           run_age_s=C2._ATTRIB_GRACE_S + 60)
    html, _ready = C2._runtime_view(1.0, scope="mine", deleted="hide")
    assert "내 실행 귀속 실패 — 계정 전체 표시 중, 귀속 로직 점검 필요" in html
    assert "regrsub22222222" in html          # 계정 스팬이 실제로 렌더됨 (blank 아님)
    assert "regrvpc11111111" in html


def test_runtime_view_startup_grace_banner_not_failure(tmp_path, monkeypatch):
    """방금 시작한 로컬 실행(grace 이내)은 자원 이벤트가 아직 없는 게 정상 —
    '준비 중' 안내를 내고 '귀속 실패' 진단 배너를 내지 않는다 (리뷰 지적 2026-07-04)."""
    C2 = _load_server()
    _prime(C2, tmp_path, monkeypatch, run_status="running", oplog=None, tracked=[],
           run_age_s=0.0)
    html, _ready = C2._runtime_view(1.0, scope="mine", deleted="hide")
    assert "내 실행 준비 중 — 자원 이벤트 대기" in html
    assert "귀속 실패" not in html
    assert "regrsub22222222" in html          # 계정 전체 뷰로는 렌더됨 (blank 아님)


def test_runtime_view_idle_fallback_note_preserved(tmp_path, monkeypatch):
    """실행이 없고 mine 0건 → 기존의 안내 노트 + 계정 전체 배너 (기존 동작 유지)."""
    C2 = _load_server()
    _prime(C2, tmp_path, monkeypatch, run_status="done", oplog=None, tracked=[])
    html, _ready = C2._runtime_view(1.0, scope="mine", deleted="hide")
    assert "계정 전체 뷰로 전환했습니다" in html
    assert "계정 전체 뷰 — 다른 run·CI 자원 포함" in html
    assert "귀속 실패" not in html


def test_local_res_index_reads_events_and_registry_shards(tmp_path, monkeypatch):
    """mine-set 소스 = rec 이벤트 파일(resource-tracked/-deleted) + core.registry
    per-run 샤드(reports/registry/<rec id>*.jsonl)."""
    C2 = _load_server()
    monkeypatch.setattr(C2, "ROOT", tmp_path)
    evfile = tmp_path / "r.events.jsonl"
    with open(evfile, "w", encoding="utf-8") as fh:
        fh.write(json.dumps({"ts": 1, "kind": "resource-tracked",
                             "resource_id": "SRV-1", "name": "regrsrv73172abf"}) + "\n")
        fh.write(json.dumps({"ts": 2, "kind": "resource-deleted",
                             "resource_id": "VOL-1"}) + "\n")
        fh.write(json.dumps({"ts": 3, "kind": "step-end", "status": 200}) + "\n")
    regdir = tmp_path / "reports" / "registry"
    regdir.mkdir(parents=True)
    (regdir / "t-rec-2-gw0.jsonl").write_text(json.dumps(
        {"service": "vpc", "delete_path": "/v1/vpcs/VPC-9",
         "resource_id": "VPC-9", "kind": "vpc"}) + "\n", encoding="utf-8")
    C2._RUNS["t-rec-2"] = {"id": "t-rec-2", "kind": "run", "status": "running",
                           "events": str(evfile), "started": time.time()}
    idx = C2._local_res_index()
    assert idx["t-rec-2"]["ids"] == {"SRV-1", "VOL-1", "VPC-9"}
    assert idx["t-rec-2"]["names"] == {"regrsrv73172abf"}
