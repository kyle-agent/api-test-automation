"""Offline tests for the control-plane M2+M3 features (명령 채널 · 인벤토리 ·
비교 뷰 · 저작 편집기/authoring 파이프라인 · 의존 그래프 · 할당량 시뮬레이션).

No network, no bucket, no credentials — the snapshot reader is stubbed and the
DB is a throwaway temp file. Rerunnable any time from the repo root:

    PYTHONPATH=. python3 controlplane/tests_offline.py
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import traceback
from pathlib import Path

# fresh throwaway DB + a clean engine-API env, BEFORE the app import
os.environ["PLATFORM_DB"] = os.path.join(
    tempfile.mkdtemp(prefix="platform-test-"), "platform.db")
for var in ("PLATFORM_INGEST_TOKEN", "SCP_ALLOW_DESTRUCTIVE",
            "PLATFORM_AUTO_TRIAGE", "SCP_ACCESS_KEY", "SCP_SECRET_KEY",
            "SCP_OPLOG_ACCESS_KEY", "SCP_OPLOG_SECRET_KEY",
            "PLATFORM_GIT_PUSH", "SCP_BUDGET_LIMITS"):
    os.environ.pop(var, None)
# pop alone is NOT hermetic: core.config._load_dotenv() (triggered by the app
# import below) setdefault()s values from a host .env back INTO os.environ, and
# _bool("SCP_ALLOW_DESTRUCTIVE") defaults True — so on a host whose .env arms
# the gate, test_delete_gated_without_destructive_env broke. Pin the gate OFF
# explicitly (existing env vars always win over .env). 2026-07-02.
os.environ["SCP_ALLOW_DESTRUCTIVE"] = "false"

from fastapi.testclient import TestClient  # noqa: E402

from controlplane import authoring, compare, db, resources, snapshots  # noqa: E402
from controlplane.app import app  # noqa: E402

# 2026-07-09: 위 env 핀만으론 부족 — pytest 는 root conftest.py 를 테스트 모듈보다
# 먼저 import 하고, conftest 가 `from core.config import settings` 로 frozen 싱글턴을
# 그 시점 env(.env 포함, _bool 기본 True)로 구워 버린다. .env 가 게이트를 arm 한
# 호스트에선 destructive_enabled() 가 True 로 남아 test_delete_gated_* 가 실제
# 라이브 DELETE 를 시도했다 (실측: VPC-G 404). 싱글턴 필드를 직접 끈다.
import core.config as _core_cfg  # noqa: E402
object.__setattr__(_core_cfg.settings, "allow_destructive", False)

client = TestClient(app)


def _resource_event(gh_run_id: str, action: str, *, res_id: str, kind: str,
                    service: str = "vpc", name: str = "", lifecycle: str = "",
                    ts: str = "2026-06-11T00:00:00Z") -> None:
    """Insert one resource event the way /api/ingest/events does."""
    ev = {"ts": ts, "t": 0, "action": action, "kind": kind, "service": service,
          "name": name, "res_id": res_id, "lifecycle": lifecycle,
          "status": "", "parent": ""}
    db.insert_event(gh_run_id, "resource", ts, stage=action,
                    detail=json.dumps(ev, ensure_ascii=False))


# --- 1. multi-tenancy groundwork -------------------------------------------------

def test_tenant_columns_exist():
    with db.connect() as con:
        run_cols = {r["name"] for r in con.execute("PRAGMA table_info(runs)")}
        sched_cols = {r["name"] for r in con.execute("PRAGMA table_info(schedules)")}
    assert "tenant" in run_cols, f"runs.tenant missing: {run_cols}"
    assert "tenant" in sched_cols, f"schedules.tenant missing: {sched_cols}"
    db.create_run("smoke", "stage", gh_run_id="t-run-1", tenant="acme")
    sid = db.add_schedule("0 2 * * *", "smoke", tenant="acme")
    with db.connect() as con:
        assert con.execute("SELECT tenant FROM runs WHERE gh_run_id='t-run-1'"
                           ).fetchone()["tenant"] == "acme"
        assert con.execute("SELECT tenant FROM schedules WHERE id=?",
                           (sid,)).fetchone()["tenant"] == "acme"
    db.delete_schedule(sid)


# --- 2. command channel (API contract) -------------------------------------------

def test_command_crud_and_ack_idempotency():
    # UI inserts a pending command
    r = client.post("/runs/9001/commands",
                    data={"action": "skip_scenario", "target": "network/vpc"})
    assert r.status_code == 200, r.status_code  # 303 followed to the run page
    r = client.post("/runs/9001/commands", data={"action": "abort_run"})
    assert r.status_code == 200

    # engine polls: exact contract shape
    r = client.get("/api/runs/9001/commands")
    assert r.status_code == 200
    cmds = r.json()["commands"]
    assert [c["action"] for c in cmds] == ["skip_scenario", "abort_run"], cmds
    assert cmds[0]["target"] == "network/vpc"
    assert all(set(c) == {"id", "action", "target"} for c in cmds), cmds

    # ack: idempotent, removes from pending
    cid = cmds[0]["id"]
    assert client.post(f"/api/commands/{cid}/ack").json() == {"ok": True}
    assert client.post(f"/api/commands/{cid}/ack").json() == {"ok": True}  # re-ack ok
    left = client.get("/api/runs/9001/commands").json()["commands"]
    assert [c["action"] for c in left] == ["abort_run"], left
    assert client.post("/api/commands/999999/ack").status_code == 404
    # acked_at is set exactly once
    row = [c for c in db.list_commands("9001") if c["id"] == cid][0]
    assert row["status"] == "acked" and row["acked_at"]


def test_command_validation():
    assert client.post("/runs/9001/commands",
                       data={"action": "rm_rf"}).status_code == 400
    assert client.post("/runs/9001/commands",
                       data={"action": "skip_scenario", "target": "  "}
                       ).status_code == 400


def test_command_api_token_gate():
    os.environ["PLATFORM_INGEST_TOKEN"] = "sekrit"
    try:
        assert client.get("/api/runs/9001/commands").status_code == 401
        assert client.post("/api/commands/1/ack").status_code == 401
        ok = client.get("/api/runs/9001/commands",
                        headers={"Authorization": "Bearer sekrit"})
        assert ok.status_code == 200
        assert client.post("/api/commands/1/ack",
                           headers={"Authorization": "Bearer sekrit"}
                           ).status_code in (200, 404)
    finally:
        del os.environ["PLATFORM_INGEST_TOKEN"]


def test_intervention_ui_only_when_running():
    db.create_run("smoke", "stage", gh_run_id="9100")
    db.apply_milestone("9100", "run-start", "running")
    body = client.get("/runs/9100").text
    assert "run 전체 중단" in body and "시나리오 skip" in body
    db.apply_milestone("9100", "dashboard", "done")
    body = client.get("/runs/9100").text
    assert "run 전체 중단" not in body


# --- 3. resource inventory --------------------------------------------------------

def test_inventory_folding_created_then_deleted_is_gone():
    # full ingest path for the created events (the real wire format)
    payload = {"kind": "resources", "run_id": "8001", "events": [
        {"ts": "2026-06-11T01:00:00Z", "t": 1, "action": "created",
         "kind": "vpcs", "service": "vpc", "name": "regrvpc1",
         "res_id": "VPC-1", "lifecycle": "network/vpc", "status": "", "parent": ""},
        {"ts": "2026-06-11T01:01:00Z", "t": 2, "action": "created",
         "kind": "subnets", "service": "vpc", "name": "regrsub1",
         "res_id": "SUB-1", "lifecycle": "network/vpc", "status": "",
         "parent": "VPC-1"},
    ]}
    assert client.post("/api/ingest/events", json=payload).json() == {"ok": True}
    _resource_event("8001", "deleted", res_id="VPC-1", kind="vpcs",
                    name="regrvpc1", ts="2026-06-11T01:30:00Z")

    rows = {r["res_id"]: r for r in resources.inventory("8001")}
    assert rows["VPC-1"]["live"] is False, rows["VPC-1"]
    assert rows["SUB-1"]["live"] is True
    assert rows["SUB-1"]["kind"] == "subnets" and rows["SUB-1"]["name"] == "regrsub1"
    assert rows["SUB-1"]["age"]  # created long ago -> non-empty age

    page = client.get("/testing/resources?gh_run_id=8001").text
    assert "SUB-1" in page and "regrsub1" in page and "live" in page
    # the run filter excludes other runs' resources
    assert "VPC-OTHER" not in page
    _resource_event("8002", "created", res_id="VPC-OTHER", kind="vpcs")
    assert "VPC-OTHER" not in client.get("/testing/resources?gh_run_id=8001").text
    assert "VPC-OTHER" in client.get("/testing/resources").text


def test_inventory_platform_delete_marks_gone_only_on_ok():
    _resource_event("8003", "created", res_id="Q-1", kind="queues",
                    service="queueservice")
    resources.record_attempt("8003", service="queueservice", kind="queues",
                             res_id="Q-1", ok=False, message="HTTP 409")
    assert resources.inventory("8003")[0]["live"] is True  # failed attempt → 그대로 live
    resources.record_attempt("8003", service="queueservice", kind="queues",
                             res_id="Q-1", ok=True, message="HTTP 204")
    assert resources.inventory("8003")[0]["live"] is False


def test_delete_gated_without_destructive_env():
    _resource_event("8004", "created", res_id="VPC-G", kind="vpcs")
    before = len(db.list_resource_events("8004"))
    r = client.post("/testing/resources/delete", data={
        "gh_run_id": "8004", "service": "vpc", "kind": "vpcs",
        "res_id": "VPC-G", "name": "regrvpcG", "lifecycle": "", "filter_run": ""})
    assert r.status_code == 200  # 303 followed to the inventory page
    assert "SCP_ALLOW_DESTRUCTIVE" in r.text and "차단" in r.text
    # blocked BEFORE any attempt — no platform-delete event recorded
    assert len(db.list_resource_events("8004")) == before
    assert resources.inventory("8004")[0]["live"] is True


def test_empty_inventory_explains_ingest_only():
    page = client.get("/testing/resources?gh_run_id=no-such-run").text
    assert "ingest된 이벤트만" in page


# --- 3b. 에러/빈 상태 폴리시 (UIUX-AUDIT P2-12) -------------------------------------

def test_error_empty_states():
    # /planning/edit·view without ?path= -> friendly HTML picker, not raw 422
    for mode in ("edit", "view"):
        r = client.get(f"/planning/{mode}")
        assert r.status_code == 200, (mode, r.status_code)
        assert "파일을 선택하세요" in r.text, mode
        assert "suites/smoke.yaml" in r.text, mode
    # unknown run id -> 404 page with the "전체 목록" link (was a 200 empty page)
    r = client.get("/runs/no-such-run-424242")
    assert r.status_code == 404, r.status_code
    assert "기록 없음" in r.text and "/reporting?tab=runs" in r.text
    # a run the DB knows still renders 200 (archive-only runs ride snapshots/index)
    db.create_run("smoke", "stage", gh_run_id="9200")
    assert client.get("/runs/9200").status_code == 200


def test_reporting_subtabs_single_include():
    # 서브탭 단일 정의 (P2-8) — 2026-07-11 색칠지도 탭 제거(오너 결정): 5탭.
    # /reporting/coverage 는 요약으로 리다이렉트되므로 같은 탭 세트가 나온다.
    for path in ("/reporting?tab=summary", "/reporting/coverage",
                 "/reporting/compare"):
        page = client.get(path).text
        for label in ("요약", "대시보드", "실행 기록", "비교", "트리아지"):
            assert label in page, (path, label)
        assert "색칠지도" not in page, path


def test_ia_catalog_absorbed_into_modeling():
    """2026-07-07 IA 개정 — Catalog는 네비 단계에서 우측 유틸 링크(📖 카탈로그)로,
    Modeling이 서비스별 카탈로그 엔드포인트를 인라인(집계 + lazy 드로어)으로 품는다.

    개정 (오너 확정 2026-07-15, 블록 ③): 홈 파이프라인 스트립 자체가 제거됨 —
    세 카드 전부 중복(진행 중=최근 RUN, %=사다리, 점프=네비)이거나 관객 부재
    (Modeling 저작 지표는 에이전트용). 스트립 부재를 계약으로 고정."""
    home = client.get("/").text
    assert "📖 카탈로그" in home                       # 유틸 링크
    assert '<a href="/catalog" class=' in home         # 딥링크 유지
    assert "3단계 현황" not in home and "pstage" not in home   # 스트립 제거 확정
    assert 'class="pipe"' not in home

    # /catalog 는 남고(참조용) 머리에 통합 안내 1줄
    cat = client.get("/catalog")
    assert cat.status_code == 200 and "Modeling으로 통합됨" in cat.text

    # Modeling 표: 서비스 행 집계 + 표 CX (code/opt 숨김 · 검증상태 헤더 · 범례)
    r = client.get("/planning/resources/map")
    assert r.status_code == 200
    page = r.text
    assert "모델됨" in page and "미모델" in page and "epdrawer" in page
    assert ">검증상태</th>" in page and ">code</th>" not in page \
        and ">opt</th>" not in page
    # 2026-07-11 모델링 개선 ①(P2C-25 결정 이행 + v2 D6): 전역 '의존 그래프
    # (영향 파악)' 탭은 제거 — 의존은 서비스 행의 🕸 미니그래프 인스펙터로.
    assert "범례:" in page and "의존 그래프 (영향 파악)" not in page

    # 엔드포인트 드로어 파셜 — 상태 칩 3종 분류 (규칙: resource_routes.py 주석)
    part = client.get("/planning/resources/map/endpoints",
                      params={"service": "networking/vpc"})
    assert part.status_code == 200
    assert "epchip ok" in part.text          # 모델됨 → 노드 편집 딥링크
    assert "/planning/resources/" in part.text
    missing = client.get("/planning/resources/map/endpoints",
                         params={"service": "no/such"})
    assert missing.status_code == 200 and "없습니다" in missing.text


def test_modeling_group_header_rows_not_hit_by_global_cat_badge():
    """2026-07-09 오너 실측 — Modeling 표의 카테고리 그룹 헤더 행이 내용-폭 둥근
    칩으로 떠 보인 진짜 뿌리: base.html 의 bare `.cat` 배지 규칙(display:
    inline-block · border-radius)이 `<tr class="cat">` 에도 걸렸다 (P2C-15 는 셀
    내부만 고쳐 재발). 수리 = 배지 규칙을 `span.cat` 으로 스코프. 이 테스트는
    (a) 셸 CSS 에 bare `.cat{` 선택자가 다시 생기지 않는 것과 (b) 그룹 헤더가
    전폭 colspan 행 문법을 유지하는 것을 고정한다."""
    import re
    page = client.get("/planning/resources/map").text
    # (a) 배지는 span 스코프로만 — bare `.cat{` 는 tr.cat 행을 인라인 칩으로 만든다
    assert "span.cat{" in page
    assert not re.search(r"(?<![\w.-])\.cat\s*\{", page), \
        "bare `.cat{` 전역 규칙 재등장 — <tr class=\"cat\"> 그룹 헤더 행이 다시 칩이 된다"
    # (b) 그룹 헤더 = 표 전체 폭을 스팬하는 행 (카테고리·서비스 모두)
    assert '<tr class="cat"' in page and '<tr class="svc"' in page
    assert page.count('colspan="6"') >= page.count('<tr class="cat"')


def test_ctxbar_env_strip_live_vs_snapshot_badge():
    """2026-07-09 owner GO (b) — legacy /platform 콘솔의 환경정보 스트립 이식.
    ctxbar(전 페이지, base_ctx P1-3)는 env·suite 세그먼트 형식이 되고, 표면별
    데이터 성격을 배지로 선언한다: Testing 계열 = LIVE (실행·자원 수치는 라이브
    실측 — 종전 '모든 수치는 이 스냅샷 기준' 오표기 교정), 그 외 = SNAPSHOT.
    노후 칩(P2-10)은 유지."""
    home = client.get("/").text
    testing = client.get("/testing").text
    modeling = client.get("/planning/resources/map").text
    # 표면별 배지 — testing 만 LIVE, 나머지는 SNAPSHOT (ctxbar 배지 클래스로 판별)
    assert 'class="mode snap"' in home and 'class="mode live"' not in home
    assert 'class="mode snap"' in modeling and 'class="mode live"' not in modeling
    assert 'class="mode live"' in testing and 'class="mode snap"' not in testing
    # env 세그먼트 — settings.env_code 가 있으면 모든 페이지에 동일하게
    from core.config import settings as _settings
    if _settings.env_code:
        assert f"env <b>{_settings.env_code}</b>" in home
        assert f"env <b>{_settings.env_code}</b>" in testing
    # 스냅샷이 읽힐 때만: suite 라벨 + LIVE 화면의 분기 문구 (오프라인 환경에선
    # dashboard-data 가 없어 폴백 문구가 나온다 — 그 경우도 배지는 위에서 보장)
    from controlplane import dashdata
    snap = dashdata.latest_coverage()
    if snap:
        if snap.get("run_type"):
            assert f"suite <b>{snap['run_type']}</b>" in home
        assert "모든 수치는 이 스냅샷 기준" in home
        assert "커버리지 수치만 이 스냅샷 기준" in testing
    else:
        assert "발행 스냅샷 정보 없음" in home


# --- 4. run comparison ------------------------------------------------------------

_OBS = {
    "100": [
        {"endpoint_key": "vpc:create", "method": "POST", "category": "ok", "status": 201},
        {"endpoint_key": "vpc:list", "method": "GET", "category": "fail", "status": 500},
        {"endpoint_key": "subnet:get", "method": "GET", "category": "fail", "status": 404},
        # duplicate observation: worst category must win the fold
        {"endpoint_key": "srv:get", "method": "GET", "category": "ok", "status": 200},
        {"endpoint_key": "srv:get", "method": "GET", "category": "fail", "status": 500},
    ],
    "200": [
        {"endpoint_key": "vpc:create", "method": "POST", "category": "fail", "status": 500},
        {"endpoint_key": "vpc:list", "method": "GET", "category": "ok", "status": 200},
        {"endpoint_key": "subnet:get", "method": "GET", "category": "fail", "status": 404},
        {"endpoint_key": "srv:get", "method": "GET", "category": "soft", "status": 200},
        {"endpoint_key": "kms:get", "method": "GET", "category": "fail", "status": 500},
    ],
}


def test_compare_diff_buckets():
    d = compare.diff(_OBS["100"], _OBS["200"])
    assert [r["key"] for r in d["new_fails"]] == ["GET kms:get", "POST vpc:create"]
    assert [r["key"] for r in d["fixed"]] == ["GET srv:get", "GET vpc:list"]
    assert [r["key"] for r in d["still"]] == ["GET subnet:get"]
    assert d["changed"] == []
    assert d["a_total"] == 4 and d["b_total"] == 5
    # endpoint absent in A shows '—' and never counts as fixed
    kms = [r for r in d["new_fails"] if r["key"] == "GET kms:get"][0]
    assert kms["a"] == "—"


def test_compare_view_with_stubbed_snapshots():
    real = snapshots.observations
    snapshots.observations = lambda rid: _OBS.get(rid, [])
    try:
        page = client.get("/reporting/compare?a=100&b=200").text
        assert "POST vpc:create" in page and "GET vpc:list" in page
        assert "새로 깨짐 (2)" in page and "고쳐짐 (2)" in page
        assert "계속 실패 (1)" in page
        # missing snapshot degrades to a warning, not an error
        page = client.get("/reporting/compare?a=100&b=999").text
        assert "스냅샷 observations를 읽을" in page
        assert client.get("/reporting/compare").status_code == 200  # picker only
    finally:
        snapshots.observations = real


# --- 5. authoring — 편집기 + validate→write→commit 파이프라인 (M3) ------------------

def test_editor_pages_render():
    page = client.get("/planning/edit?path=suites/smoke.yaml").text
    assert "<textarea" in page and "id: smoke" in page
    assert "검증만" in page and "검증 + 저장" in page
    # out-of-scope / traversal paths are 404, not served
    assert client.get("/planning/edit?path=core/budgets.py").status_code == 404
    assert client.get("/planning/edit?path=../etc/passwd").status_code == 404
    assert client.get("/planning/edit?path=docs/PLATFORM-PLAN.md").status_code == 404
    # scenario rows expose 보기/편집 against the CONTAINING file (loader merge)
    page = client.get("/planning/scenarios?service=networking").text
    assert "/planning/edit?path=regression/scenarios/scenarios.json" in page
    assert "&find=networking-vpc-subnet" in page
    assert "/planning/edit?path=regression/scenarios/lifecycles/networking__vpc.json" in page
    # knowledge browser links the same editor
    page = client.get("/planning/knowledge").text
    assert "/planning/edit?path=knowledge/formal/cross-service.yaml" in page


def test_dependencies_view_renders():
    page = client.get("/planning/dependencies").text
    # vpc_schedule: adopt vs vpc-crud classes, lanes, quota cards
    assert "ADOPT — 병렬" in page and "VPC-CRUD — 직렬" in page
    assert "heavy-shared-networking" in page and "vpc-peering" in page
    assert "L3-networking" in page                       # lanes table
    assert "<svg" in page and "ske-cluster" in page      # cross-service graph
    assert "filestorage-volume" in page
    # read-only this round — editing goes through the file editor
    assert "/planning/edit?path=regression/scenarios/dependencies.json" in page
    assert "/planning/edit?path=knowledge/formal/cross-service.yaml" in page


def test_propose_edit_rejects_and_restores():
    path = Path("suites/smoke.yaml")
    orig = path.read_bytes()
    # bad YAML never reaches the validators
    r = authoring.propose_edit("suites/smoke.yaml", "id: [unclosed")
    assert not r["ok"] and any("YAML" in e for e in r["errors"]), r
    # out-of-scope paths (engine code, traversal) are refused outright
    for bad in ("core/budgets.py", "../outside.yaml",
                "regression/scenarios/engine.py", ".github/workflows/x.yml"):
        r = authoring.propose_edit(bad, "x: 1")
        assert not r["ok"] and "편집 가능 범위 밖" in r["errors"][0], (bad, r)
    # parses fine but the suite validator rejects (id != filename stem) →
    # temp-applied state is rolled back byte-identical
    r = authoring.propose_edit("suites/smoke.yaml",
                               "id: not-smoke\nlabel: x\nrequest: {}\n")
    assert not r["ok"] and any("must match" in e for e in r["errors"]), r
    assert path.read_bytes() == orig
    # htmx validate endpoint shows the errors inline (fragment, no save)
    body = client.post("/planning/edit/validate",
                       data={"path": "suites/smoke.yaml",
                             "content": "id: [broken"}).text
    assert "검증 실패" in body and "원본" in body
    assert path.read_bytes() == orig


def test_validate_only_passes_real_validators_and_restores():
    path = Path("environments/stage-kr-west1.yaml")
    orig = path.read_bytes()
    content = orig.decode() + "# edited-by-offline-test\n"
    r = authoring.propose_edit("environments/stage-kr-west1.yaml", content,
                               validate_only=True)
    assert r["ok"] and not r["errors"] and r["commit"] == "", r
    assert path.read_bytes() == orig  # validate-only always restores
    body = client.post("/planning/edit/validate",
                       data={"path": "environments/stage-kr-west1.yaml",
                             "content": content}).text
    assert "검증 통과" in body
    assert path.read_bytes() == orig


def test_good_edit_applies_and_git_commits():
    # a throwaway git repo as the working copy (PLAN constraint: never push)
    root = Path(tempfile.mkdtemp(prefix="platform-authoring-"))
    sub = subprocess.run
    sub(["git", "init", "-q", str(root)], check=True)
    for k, v in (("user.name", "Platform UI"), ("user.email", "platform@local"),
                 ("commit.gpgsign", "false")):
        sub(["git", "-C", str(root), "config", k, v], check=True)
    (root / "suites").mkdir()
    (root / "suites" / "smoke.yaml").write_text("id: smoke\n")
    sub(["git", "-C", str(root), "add", "-A"], check=True)
    sub(["git", "-C", str(root), "commit", "-qm", "init"], check=True)
    new = "id: smoke\nlabel: 편집 테스트\nrequest:\n  smoke: true\n"
    r = authoring.propose_edit("suites/smoke.yaml", new, root=root)
    assert r["ok"], r
    assert (root / "suites" / "smoke.yaml").read_text() == new
    assert r["commit"], r            # local commit made (identity fallback ok)
    assert r["pushed"] is False      # PLATFORM_GIT_PUSH unset → never pushes
    log = sub(["git", "-C", str(root), "log", "-1", "--pretty=%s"],
              capture_output=True, text=True).stdout.strip()
    assert log == "authoring: suites/smoke.yaml via platform UI", log
    porcelain = sub(["git", "-C", str(root), "status", "--porcelain"],
                    capture_output=True, text=True).stdout.strip()
    assert porcelain == "", porcelain  # nothing left uncommitted


def test_quota_simulation_warns_on_peak_over_limit():
    deps = {"budget_paths": {"/v1/vpcs": "vpc"},
            "vpc_schedule": {"adopt_lifecycles": ["adopter"],
                             "vpc_crud_lifecycles": ["greedy", "mild"],
                             "per_run_vpc_cap": 4}}
    lifecycles = [
        {"id": "adopter", "steps": [
            {"method": "POST", "path": "/v1/vpcs", "adopt": "vpc"}]},
        {"id": "greedy", "steps": [
            {"method": "POST", "path": "/v1/vpcs"}] * 5},   # 5 self-created
        {"id": "mild", "steps": [{"method": "POST", "path": "/v1/vpcs"}]},
    ]
    sim = authoring.vpc_peak(deps, lifecycles)
    assert sim["peak"] == 6 and sim["worst_id"] == "greedy", sim  # 1 shared + 5
    ws = authoring.vpc_quota_warnings(deps, lifecycles)
    assert any("peak 동시 VPC 6개" in w and "한도" in w for w in ws), ws
    # a sane schedule (the real repo data shape) warns nothing
    lifecycles[1]["steps"] = [{"method": "POST", "path": "/v1/vpcs"}]
    assert authoring.vpc_quota_warnings(deps, lifecycles) == []
    # unknown lifecycle ids in the schedule are flagged (authoring aid)
    deps["vpc_schedule"]["adopt_lifecycles"] = ["no-such-lifecycle"]
    ws = authoring.vpc_quota_warnings(deps, lifecycles)
    assert any("존재하지 않는 lifecycle" in w for w in ws), ws


def test_quota_simulation_runs_on_dependencies_save():
    # full pipeline: temp-apply dependencies.json + real scenario validator,
    # with a 1-VPC env override so the REAL schedule's peak (2) now warns
    path = Path("regression/scenarios/dependencies.json")
    orig = path.read_bytes()
    deps = json.loads(orig)
    deps["notes"] = (deps.get("notes") or "") + " [offline-test]"
    os.environ["SCP_BUDGET_LIMITS"] = '{"vpc": 1}'
    try:
        r = authoring.propose_edit("regression/scenarios/dependencies.json",
                                   json.dumps(deps, indent=2, ensure_ascii=False),
                                   validate_only=True)
    finally:
        del os.environ["SCP_BUDGET_LIMITS"]
    assert r["ok"], r                                  # warn, never block
    assert any("할당량 시뮬레이션" in w for w in r["warnings"]), r
    assert path.read_bytes() == orig


def test_source_badges_and_empty_states():
    """v2 접목 1 (2026-07-11, V2-L1-DATA-CONTRACT §1·§3·§4) — 출처 배지 3종.

    v1의 최대 약점이던 "발행본 fail N vs 이 서버 run 없음" 모순을 출처 배지
    (Published/This server/This run)와 empty-state 문구("관측 없음 ≠ 0")로
    해소한다. 배지 마크업은 _badges.html 매크로 단일 구현.

    개정 (오너 2026-07-14 재검수 "이런 표현이 필요한가"): **예외만 배지** —
    페이지 기본 출처(발행 스냅샷)는 헤더 Published 배지 + ctxbar 가 1곳에서
    선언하고, 타일·배너에서의 반복은 제거. 배지는 기본과 다른 출처(잔존 타일의
    라이브 This server, 런 목록의 CI/로컬 행 구분, 런 상세 This run)에만."""
    from controlplane import common, dashdata

    # 매크로 시각 라벨 — history ts(UTC) → KST 짧은 라벨, 파싱 실패는 빈 문자열
    assert common.snap_ts_short({"ts": "2026-07-09T10:27:00Z"}) == "07-09 19:27"
    assert common.snap_ts_short({"ts": "not-a-ts"}) == ""
    assert common.snap_ts_short(None) == ""

    home = client.get("/").text
    # 셸 CSS: 배지 4종(발행/이 서버/이 런 + 노후)이 항상 정의돼 있다
    for cls in (".badge-published", ".badge-local", ".badge-run", ".badge-stale"):
        assert cls in home, cls

    snap = dashdata.latest_coverage()
    if snap:
        # 발행 출처 선언은 헤더 배지 1곳 (타일 반복 없음 — 예외만 배지 원칙)
        assert 'badge badge-published' in home
        assert ">Published" in home
        # class 가 아니라 라벨로 센다 — 'ci' 배지가 파랑 스타일(badge-published)을
        # 재사용해 런 표의 CI 행이 class 카운트에 잡힌다 (라벨은 '>CI').
        assert home.count(">Published") == 1, \
            "기본 출처(발행) 배지는 헤더 1곳만 — 타일·배너 반복 금지 (2026-07-14)"
    else:
        # 발행본 없음 = "관측 없음"으로 렌더 (0으로 위장 금지 — 계약 §3)
        assert "발행된 공식 수치를 가져올 수 없습니다" in home
        assert "관측 없음" in home

    # 병합 런 타임라인 — 행별 출처 배지: local- 접두 = This server, 숫자 = CI
    db.create_run("smoke", "stage", gh_run_id="local-badge-test")
    runs_page = client.get("/reporting?tab=runs").text
    assert ">source</th>" in runs_page
    assert "badge badge-local" in runs_page and ">This server" in runs_page
    assert ">CI" in runs_page          # 9200 등 CI 런 행 (파랑 재사용)

    # 런 상세 = 이 런(S3) 배지 — 과거형 고정 값임을 선언
    detail = client.get("/runs/9200").text
    assert "badge badge-run" in detail and ">This run" in detail


def test_verdict_vs_publish_time_split():
    """v2 접목 5 — 판정 런 시각 ≠ 발행 갱신 시각 분리 병기.

    배지의 ``@ts`` 는 판정 런 시각(history ts)인데, 발행본(dashboard-data)은 그
    뒤 결과 없이 재발행돼 갱신 시각이 더 늦을 수 있다. pub_ts(발행 갱신 시각)가
    주어지고 판정 시각과 다르면 '판정 @… · 발행 @…' 로 나눠 보여준다."""
    from controlplane import common
    from controlplane.app import templates

    mod = templates.env.get_template("_badges.html").module

    # 1) 두 시각이 다르면 분리 병기 ('@ts' 단일 표기는 쓰지 않는다)
    split = mod.badge("published", ts="07-09 19:27", pub_ts="07-11 03:14",
                      ident="dd:abc123")
    assert "판정 07-09 19:27" in split
    assert "발행 07-11 03:14" in split
    assert "@07-09 19:27" not in split          # 단일 @ts 표기는 억제
    assert "badge-ts2" in split                  # 발행 시각은 보조 스타일
    assert "dd:abc123" in split                  # ident 는 그대로

    # 2) 두 시각이 같으면 기존처럼 '@ts' 하나만 (노이즈 억제) — 분리 병기 마크업
    #    (badge-ts2·'판정 ') 부재로 검사 (라벨 '공식 발행'에 '발행'이 들어가므로
    #    문자열 '발행 ' 부정 검사는 쓰지 않는다).
    same = mod.badge("published", ts="07-09 19:27", pub_ts="07-09 19:27")
    assert "@07-09 19:27" in same
    assert "badge-ts2" not in same and "판정 " not in same

    # 3) 발행 시각을 못 구하면(pub_ts 없음) 기존 표기 유지 — 절대 깨지지 않음
    none_pub = mod.badge("published", ts="07-09 19:27")
    assert "@07-09 19:27" in none_pub
    assert "badge-ts2" not in none_pub

    # 4) dd_ts_short 는 best-effort — git 접근 실패/부재에도 문자열만 반환
    assert isinstance(common.dd_ts_short(), str)
    # _dd_head 파서 재사용: committed 가 None 이면 빈 라벨
    assert common.snap_ts_short({"ts": None}) == ""


def test_residual_resources_home_kpi_d8():
    """D8 — 잔존 자원 홈 승격 + 단일 표면.

    비용·안전 사안인 잔존 자원이 실행 화면 깊숙이 숨어 있던 것을 홈 KPI 로
    끌어올린다. 라이브 '이 서버' owned 스캔이라 출처 배지는 local(발행 아님),
    스캔 안 한 상태는 '미확인'(0 으로 위장 금지, empty-state 규율). 정본 표면 =
    /testing/resources (홈은 마지막 캐시만 읽고 자동 스캔하지 않는다)."""
    from controlplane import dashdata, resources

    # owned_summary(): 스캔 전이면 scanned=False, actionable/total=None (0 아님)
    s = resources.owned_summary()
    assert set(s) >= {"scanned", "scanning", "actionable", "stuck",
                      "total", "age", "when", "error"}
    if not s["scanned"]:
        assert s["actionable"] is None and s["total"] is None

    # /runtime·/local-run 은 잔존 단일 표면으로 이어짐 (별도 표면 발명 금지)
    assert client.get("/local-run",
                      follow_redirects=False).headers["location"] == "/testing/resources"

    # 홈 KPI: 발행 스냅샷이 있으면 잔존 타일이 KPI 행에 뜬다 (local 배지 + 정본 링크)
    if dashdata.latest_coverage():
        home = client.get("/").text
        assert "잔존 자원" in home
        assert 'href="/testing/resources"' in home
        assert "badge badge-local" in home       # 발행 아님 — 이 서버 관측
        # 스캔 전이면 '미확인' + 지금 확인 유도 (0 으로 위장하지 않음)
        if not resources.owned_summary()["scanned"]:
            assert "미확인" in home and "지금 확인" in home


def test_run_op_timings_api():
    """op-timings 핸드오프 — GET /api/runs/<id>/op-timings 백엔드 API.

    tools.op_timings.derive 를 이 런 events.jsonl 에 적용: api_ms(호출 왕복) +
    settle_s(async 정착 GET poll) + total_s (내림차순). 렌더(콘솔 탭)는 console2
    소관 — 여긴 백엔드 API 만."""
    from tools import console2_server as c2
    evp = tempfile.NamedTemporaryFile("w", suffix=".jsonl", delete=False,
                                      encoding="utf-8")
    for ln in [
        {"kind": "step-start", "lifecycle": "lc1", "step": "create-cluster", "ts": 1000.0},
        {"kind": "step-end", "lifecycle": "lc1", "step": "create-cluster", "method": "POST",
         "path": "/v1/clusters", "service": "mysql", "status": 202, "elapsed_ms": 3500, "ts": 1003.5},
        {"kind": "step-start", "lifecycle": "lc1", "step": "wait-cluster-active", "ts": 1003.5},
        {"kind": "step-end", "lifecycle": "lc1", "step": "wait-cluster-active", "method": "GET",
         "path": "/v1/clusters/x", "service": "mysql", "status": 200, "elapsed_ms": 800, "ts": 1063.5},
        {"kind": "step-start", "lifecycle": "lc1", "step": "delete-cluster", "ts": 1064.0},
        {"kind": "step-end", "lifecycle": "lc1", "step": "delete-cluster", "method": "DELETE",
         "path": "/v1/clusters/x", "service": "mysql", "status": 204, "elapsed_ms": 500, "ts": 1064.5},
    ]:
        evp.write(json.dumps(ln) + "\n")
    evp.close()
    with c2._LOCK:
        c2._RUNS["op-timings-test"] = {"id": "op-timings-test", "status": "done",
                                       "events": evp.name, "lifecycle_ids": ["lc1"]}
    try:
        r = client.get("/api/runs/op-timings-test/op-timings")
        assert r.status_code == 200, r.status_code
        j = r.json()
        assert j["id"] == "op-timings-test"
        assert j["count"] == 2                         # wait/settle GET 은 op 아님
        totals = [x["total_s"] for x in j["records"]]
        assert totals == sorted(totals, reverse=True)  # total_s 내림차순
        recs = {x["step"]: x for x in j["records"]}
        cc = recs["create-cluster"]                    # api 3.5s + async settle 60s
        assert cc["api_ms"] == 3500 and cc["settle_s"] == 60.0 and cc["total_s"] == 63.5
        assert cc["kind"] == "create" and cc["status"] == 202 and cc["service"] == "mysql"
        assert "catalog_key" in cc
        dd = recs["delete-cluster"]                     # 동기 — settle 없음
        assert dd["settle_s"] is None and dd["kind"] == "delete"
        assert client.get("/api/runs/nope-xyz/op-timings").status_code == 404
    finally:
        with c2._LOCK:
            c2._RUNS.pop("op-timings-test", None)
        os.unlink(evp.name)


def test_regression_verdict_banner(monkeypatch):
    """회귀 판정 배너 — 오너 공동 설계 (2026-07-15): 통과/실패 · 몇 건 · 언제
    (절대+상대·suite) · 실패 시 서비스별 내역 미리보기(1-a) + Triage 링크 ·
    48h+ 중립 배너 + [지금 실행](3) · 기준 툴팁(known_issues 베이스라인).

    + 발행 index 의 관측 0 가드 (오너 발견): 0-call 재발행이 HEALTHY '배포 안전'
    을 자동 선언하지 않는다 — build.py 소스 계약 + fail_new.json skip-write."""
    import time as _time

    from controlplane import dashdata, results_data

    def cov(ts, fail_new):
        return {"ts": ts, "fail_new": fail_new, "fail_known": 2,
                "run_type": "full", "cov_op": 60, "cov_get": 70,
                "cov_c3": 80, "reachable_pct": 99, "total": 1372}

    fresh = _time.strftime("%Y-%m-%dT%H:%M:%SZ", _time.gmtime(_time.time() - 3600))
    stale = "2026-01-01T00:00:00Z"
    regs = {"items": [{"service_label": "database/mysql"},
                      {"service_label": "database/mysql"},
                      {"service_label": None, "lifecycle": "gen-x"}],
            "capped": False, "source": "file"}
    monkeypatch.setattr(results_data, "get_new_regressions", lambda: regs)

    # 1) 실패 (신선): 판정+건수+시각+suite+내역(서비스별 집계)+Triage 링크+기준 툴팁
    monkeypatch.setattr(dashdata, "latest_coverage", lambda: cov(fresh, 3))
    home = client.get("/").text
    assert "회귀 테스트 실패 — 신규 실패 3건" in home
    assert "full suite" in home and "시간 전" in home       # 절대 시각은 KST 라벨
    assert "내역:" in home
    assert "<b>database/mysql</b> 2" in home and "<b>gen-x</b> 1" in home
    assert 'href="/reporting?tab=triage"' in home
    assert "known_issues.json" in home                       # 판정 기준 툴팁
    assert "알려진 실패 2건은 추적 중" in home

    # 2) 통과 (신선): 배너 유지(결정 2) — 통과 문구 + 시각
    monkeypatch.setattr(dashdata, "latest_coverage", lambda: cov(fresh, 0))
    home = client.get("/").text
    assert "회귀 테스트 통과 — 신규 실패 0건" in home

    # 3) 오래됨 (48h+): 시각을 앞세운 중립 배너 + 당시 판정 + [지금 실행](결정 3)
    monkeypatch.setattr(dashdata, "latest_coverage", lambda: cov(stale, 3))
    home = client.get("/").text
    assert "마지막 회귀 테스트가" in home and "전입니다" in home
    assert "당시 판정" in home and "신규 3건" in home
    assert "지금 실행" in home and 'href="/testing/embed"' in home

    # 4) 발행 index 관측 0 가드 — build.py 소스 계약 (실빌드 검증은 수동 수행)
    src = (Path(__file__).resolve().parent.parent / "dashboard" / "build.py"
           ).read_text(encoding="utf-8")
    assert 'no_obs = not d.get("has_results")' in src
    assert "판정 불가" in src and "NO NEW OBSERVATIONS" in src
    assert 'healthy = d["fail_new"] == 0 and not no_obs' in src
    # fail_new.json 은 관측 있을 때만 발행 (skip-write = 직전 발행본 보존)
    i_guard = src.index('if d.get("has_results"):')
    assert i_guard < src.index('os.path.join(outdir, "fail_new.json")')


def test_home_mini_ladder():
    """홈 커버리지 = 미니 사다리 1장 (오너 공동 설계 2026-07-15): 커버리지 타일
    4장(write-op 오표기 포함)+신규 fail 타일을 대시보드/Reporting 과 같은 그림의
    C3→C2→C1 사다리로 대체. '남은 실질 gap'(gap_write·gap_getid)이 다음 걸음,
    상세는 발행 대시보드 링크. 잔존 자원 타일은 유지, 파이프라인·최근 RUN 은
    재논의 보류(불변). 배너 시각은 '공식 런' 라벨(로컬 런과의 관계 명시)."""
    from controlplane import dashdata
    if not dashdata.latest_coverage():
        return                                   # 발행본 없으면 empty-state 경로
    home = client.get("/").text
    assert home.count('class="lad mini"') == 3   # C3 · C2 · C1
    assert home.index(">C3<") < home.index(">C2<") < home.index(">C1<")
    assert "남은 실질 gap" in home and "사다리·서비스별 상세" in home
    assert 'href="/dashboard/index.html"' in home
    # 구 타일 5장 제거 (오표기 라벨 포함) — 사다리·배너가 대체
    for gone in ("write-op 커버리지", "GET 커버리지 (cov_get)",
                 "C1 static reachable", ">신규 fail<"):
        assert gone not in home, gone
    assert "잔존 자원" in home                    # 유지 (D8)
    assert "공식 런" in home                      # 판정 시각 = CI 발행 런 명시


def test_runs_table_view(monkeypatch):
    """최근 RUN 표 개편 (오너 '전부 반영' 2026-07-15) — _runs_view 계약:
    ① status 요약(lifecycle n/m 실패 — _local_run_summary 재사용·캐시)
    ② KST '시작·소요' 1열 (UTC ISO 2열 대체) ③ profile 열 제거(템플릿)
    ④ 연속 실패 스트릭(진행 중 건너뜀, done 에서 종료+마지막 성공 시각)."""
    from controlplane import app as cp_app
    from tools import console2_server as c2

    monkeypatch.setattr(c2, "_local_run_summary", lambda gid: {
        "lifecycles": {"total": 118, "passed": 115, "failed": 3,
                       "skipped": 0, "unfinished": 0, "failed_ids": []}})
    cp_app._RUN_SUM_CACHE.clear()
    rows = [  # 최신순: failed·failed·(running 건너뜀)·failed·done
        {"gh_run_id": "local-a", "status": "failed", "suite": "console2",
         "trigger": "local", "requested_at": "2026-07-14T05:25:01Z",
         "finished_at": "2026-07-14T06:43:19Z"},
        {"gh_run_id": "local-b", "status": "failed", "suite": "console2",
         "trigger": "local", "requested_at": "2026-07-14T02:21:13Z",
         "finished_at": "2026-07-14T03:49:03Z"},
        {"gh_run_id": "local-r", "status": "running", "suite": "console2",
         "trigger": "local", "requested_at": "2026-07-14T01:00:00Z",
         "finished_at": None},
        {"gh_run_id": "local-c", "status": "failed", "suite": "console2",
         "trigger": "local", "requested_at": "2026-07-13T23:50:03Z",
         "finished_at": "2026-07-14T01:17:53Z"},
        {"gh_run_id": "local-ok", "status": "done", "suite": "console2",
         "trigger": "local", "requested_at": "2026-07-13T11:40:14Z",
         "finished_at": "2026-07-13T13:01:38Z"},
    ]
    v = cp_app._runs_view(rows, display=2)
    assert len(v["runs"]) == 2                       # 표시분만 보강
    r0 = v["runs"][0]
    assert r0["when_label"] == "07-14 14:25"         # UTC 05:25 → KST
    assert r0["dur_label"] == "1시간 18분"
    assert r0["summary"] == "lifecycle 3/118 실패"
    # 스트릭: failed 3 (running 은 건너뜀), done 에서 멈추고 마지막 성공 시각
    assert v["fail_streak"] == 3
    assert v["last_ok_when"] == "07-13 20:40"        # UTC 11:40 → KST
    # 진행 중 런의 소요 라벨
    assert cp_app._run_dur_label("2026-07-14T01:00:00Z", "", "running") == "진행 중"
    # 통과 런 요약 문구
    monkeypatch.setattr(c2, "_local_run_summary", lambda gid: {
        "lifecycles": {"total": 10, "passed": 10, "failed": 0}})
    cp_app._RUN_SUM_CACHE.clear()
    ok = cp_app._runs_view([{"gh_run_id": "local-z", "status": "done",
                             "suite": "s", "trigger": "local",
                             "requested_at": "2026-07-13T11:40:14Z",
                             "finished_at": "2026-07-13T11:41:00Z"}])
    assert ok["runs"][0]["summary"] == "lifecycle 10/10 통과"
    # 템플릿 계약: profile 열 없음 · KST 1열 헤더 · 스트릭 경고 마크업 존재
    part = client.get("/partials/runs").text
    assert ">profile</th>" not in part and "시작 · 소요" in part
    tpl = (Path(__file__).resolve().parent / "templates" / "_runs_table.html"
           ).read_text(encoding="utf-8")
    assert "연속 실패" in tpl and "fail_streak" in tpl


def test_v2_shell_header_and_global_search():
    """v2 접목 6a (오너 지시 2026-07-11) — v2 셸의 상단 디자인 이식.

    1. 네비: Overview 첫 메뉴 신설(홈에서 active) + 다크 pill active 스타일.
       메뉴명은 v1 유지 (Modeling→Testing→Reporting — 오너 지시).
    2. 헤더 우측: 전역 검색폼(GET /search) + Published 배지(발행 시각·dd sha·
       노후) — ctxbar 의 발행 시각·노후 칩은 헤더 배지로 흡수(중복 제거),
       ctxbar 에는 sha·suite·env·표면 모드 유지.
    3. GET /search — 서비스/엔드포인트(카탈로그)·런(이 서버) 3섹션,
       2자 미만 안내. 카탈로그 ?q= 딥링크."""
    home = client.get("/").text
    # 1) Overview 메뉴 + 다크 pill
    assert '>Overview</a>' in home
    assert ".nav a.active{color:#fff;background:var(--text)}" in home
    # 홈에서 Overview 가 active (active == 'home')
    import re
    m = re.search(r'<a href="/" class="active">Overview</a>', home)
    assert m, "홈에서 Overview 메뉴가 active 여야 한다"
    # 메뉴명 유지
    for label in (">Modeling</a>", ">Testing</a>", ">Reporting</a>"):
        assert label in home, label
    # 2) 헤더 검색폼 + Published 배지(또는 접근불가 empty-state)
    assert 'action="/search"' in home and 'class="hdr-search"' in home
    from controlplane import dashdata
    if dashdata.latest_coverage():
        assert home.index('class="hdrctx"') < home.index("badge badge-published")
    else:
        assert "발행본 접근 불가" in home
    # 3) 전역 검색 화면
    short = client.get("/search", params={"q": "v"})
    assert short.status_code == 200 and "2자 이상" in short.text
    r = client.get("/search", params={"q": "vpc"})
    assert r.status_code == 200
    page = r.text
    for sec in ("서비스", "엔드포인트", ">RUN"):
        assert sec in page, sec
    assert "/catalog?q=" in page                     # 카탈로그 딥링크
    assert "/testing?service=" in page               # 실행 prefill 딥링크
    assert "저장소(카탈로그) 기준" in page            # 출처 정직 표기
    # 런 검색 — 이 서버 기록에서 부분일치 (이전 테스트가 만든 local- 런)
    rr = client.get("/search", params={"q": "local-badge"}).text
    assert "local-badge-test" in rr and "badge badge-local" in rr
    # 카탈로그 ?q= 딥링크 스크립트
    cat = client.get("/catalog").text
    assert "URLSearchParams(location.search).get('q')" in cat


def test_modeling_improvements_batch():
    """모델링 화면 개선 ①~⑤ (2026-07-11 오너 승인 — 검토 보고의 5개 항목).

    ① 전역 의존 그래프 탭 제거(P2C-25 + v2 D6) — 서비스 행 🕸 의존 미니그래프
       인스펙터로 대체 (donor: v2 svc_graph.js · /api/graph + resource_graph.js
       scene() 재사용). map.json 라우트는 보존(console2 공유).
    ② 출처·단위 정직화 — '저장소(main) 기준' + VALIDATED는 노드 단위 註.
    ③ ?q= 딥링크 (전역 검색 → Modeling 필터 프리필).
    ④ 서비스 행 ▶ 실행 prefill (/testing?service= — 자동 발사 아님).
    ⑤ 카테고리 행 검증 진척 미니바."""
    page = client.get("/planning/resources/map").text
    # ① 탭 제거 + 미니그래프 인스펙터 골격
    assert 'data-view="graph"' not in page and 'id="pane-graph"' not in page
    assert 'class="epbtn depbtn"' in page and "🕸 의존" in page
    assert '<tr class="depdrawer"' in page
    assert '"/api/graph"' in page                       # console2 계약 재사용
    assert "window.ResourceGraph.scene" in page         # 렌더러 원본 재사용
    assert "resource_graph.js" in page
    assert "노드 편집 →" in page                        # 인스펙터의 다음 행동
    # 드로어는 검색/필터 중에도 보인다 (오너 실측 2026-07-11: ?q= 진입 상태에서
    # 🕸 클릭 시 그림이 안 떠 보였음 — 명시적으로 연 드로어를 필터가 숨기지 않게)
    assert "(filtering || !collapsedSvcs[key])" in page
    # 라우트 보존 — console2 run 뷰가 공유
    assert client.get("/planning/resources/map.json").status_code == 200
    # ② 출처·단위 註
    assert "저장소(main) 기준" in page and "모델 노드 단위" in page
    # ③ ?q= 프리필
    assert "URLSearchParams(location.search)" in page
    # ④ ▶ 실행 prefill
    assert 'class="epbtn runbtn"' in page and "/testing?service=" in page
    # ⑤ 진척 미니바 (카테고리 행마다 1개) — 주의: bare '<tr class="cat"' 는
    # base.html 의 P2C-23 CSS 주석 본문에도 등장하므로 data-cat 행만 센다
    assert page.count('class="pbar"') >= page.count('<tr class="cat" data-cat=')
    assert page.count('<tr class="cat" data-cat=') >= 10
    # /search 서비스 결과의 Modeling 링크가 ?q= 를 나른다
    sr = client.get("/search", params={"q": "vpc"}).text
    assert "/planning/resources/map?q=" in sr



def test_triage_new_fail_detail_and_known_list():
    """Reporting 개선 A (계약 §2.5, donor: v2 results_data) — 트리아지 탭이
    '신규 fail N건'의 정체를 직접 답한다.

    1. 발행 fail_new.json(정공법 — 이번에 build.py 가 신설 발행) 우선 소비.
    2. '당시 500 → 현재 201'을 분리 표기 (복구 관측 — 누적 최신값이 재시도
       복구를 숨기는 문제 방지).
    3. 같은 호출의 이중 기록(카탈로그/라이프사이클 키) 힌트 — 병합하지 않고
       정직하게 둘 다 + 배지.
    4. known 목록 = 저장소 baseline (알림 대상 아님 섹션)."""
    from controlplane import dashdata

    orig = dashdata.file

    def fake(name):
        if name == "fail_new.json":
            return (json.dumps({"new": [
                {"key": "networking/vpc/createvpc", "status": 500, "path": "/v1/vpcs"},
                {"key": "lifecycle-networking-vpc:create-vpc", "status": 500, "path": ""},
            ], "known": [], "updated": "2026-07-11 12:00 KST"}).encode(), "application/json")
        if name == "endpoint_status.json":
            return (json.dumps({"status": {"networking/vpc/createvpc": [201, 320]}}).encode(),
                    "application/json")
        return None

    dashdata.file = fake
    try:
        page = client.get("/reporting?tab=triage").text
    finally:
        dashdata.file = orig
    assert "신규 fail 상세" in page
    assert "networking/vpc/createvpc" in page
    assert "복구 관측" in page                       # 당시 500 → 현재 201 분리 표기
    assert "이중 기록?" in page                      # 합성/카탈로그 쌍 힌트
    assert "/dashboard/services/networking__vpc.html" in page   # 발행 상세 딥링크
    assert "라이프사이클 단계 —" in page              # 합성 키 라벨 (링크 없음)
    assert "최대 6건" not in page                    # 파일 경로는 상한 없음
    # known 섹션 — 저장소 baseline 실제 렌더
    assert "이미 알던 실패" in page
    assert "listplannedcomputeservertypes" in page
    # 발행물 접근 불가 → 섹션 empty-state (0 위장 금지)
    dashdata.file = lambda name: None
    try:
        page2 = client.get("/reporting?tab=triage").text
    finally:
        dashdata.file = orig
    assert "상세 목록을 가져올 수 없습니다" in page2


def test_coverage_map_removed_and_runs_timeline_merged():
    """색칠지도 제거(오너 결정 2026-07-11) + Reporting 개선 C (병합 타임라인).

    1. /reporting/coverage → 요약 탭 302 (딥링크 호환), map.json 데이터 API 보존.
    2. 상단 네비 Reporting 랜딩 = /reporting (요약).
    3. 실행 기록 탭: RUN 히스토리 ∪ 아카이브 병합 단일 타임라인 —
       gh_run_id dedupe, 아카이브 전용 런은 '아카이브' 태그, 행별 출처 배지."""
    # 1) 리다이렉트 + 데이터 API 보존
    r = client.get("/reporting/coverage", follow_redirects=False)
    assert r.status_code == 302 and "/reporting?tab=summary" in r.headers["location"]
    assert client.get("/reporting/coverage/map.json").status_code == 200
    # 2) 네비 랜딩
    home = client.get("/").text
    assert '<a href="/reporting" class=' in home
    assert 'href="/reporting/coverage"' not in home
    # 3) 병합 타임라인 — db 런(9200, 앞 테스트가 생성) + 아카이브 스텁 2건
    #    (하나는 db 와 중복 → dedupe, 하나는 아카이브 전용 → 태그)
    real = snapshots.archive_index
    snapshots.archive_index = lambda limit=100: [
        {"run_id": "9200", "sha": "dupsha", "finished": "2026-07-10T00:00:00Z"},
        {"run_id": "7777", "sha": "arcsha", "finished": "2026-07-09T00:00:00Z"},
    ]
    try:
        page = client.get("/reporting?tab=runs").text
    finally:
        snapshots.archive_index = real
    assert "RUN 타임라인" in page and "아카이브(oplog" in page
    assert "RUN 히스토리" not in page          # 두 표 체제 종료
    assert page.count("/runs/9200") == 1       # dedupe — 한 행만
    assert "/runs/7777" in page and ">아카이브</span>" in page and "arcsha" in page
    assert page.count("badge badge-") >= 2     # 행별 출처 배지


def test_scheduler_fires_local_suite_run_and_heavy_schedule_blocked():
    """스케줄 → 이 서버 LIVE 런 배선 (D5 후속 결정, 오너 승인 2026-07-11).

    1. tick(): due 스케줄이 GHA 디스패치가 아니라 launch_suite_run(로컬 LIVE)을
       부른다 — trigger 에 schedule:id 를 싣는다. 성공 시 run 행은 엔진 DB
       미러 몫이라 여기서 이중 기록하지 않는다.
    2. 발화 실패(heavy 거부·LIVE 중복 등)는 failed 런 행으로 기록 — 화면에서
       보인다.
    3. POST /schedules 는 heavy(과금) suite 등록을 400 으로 차단 (Hard Rule 1
       — heavy 는 pre-flight 수동 opt-in)."""
    from controlplane import scheduler
    from tools import console2_server as c2

    calls = []
    real = c2.launch_suite_run
    c2.launch_suite_run = (lambda suite, trigger="":
                           (calls.append((suite, trigger)), (True, "launched"))[1])
    sid = db.add_schedule("* * * * *", "full", note="wire-test")
    try:
        with db.connect() as con:
            con.execute("UPDATE schedules SET last_fired='2026-01-01T00:00:00Z'"
                        " WHERE id=?", (sid,))
        fired = scheduler.tick()
        assert sid in fired and calls, (fired, calls)
        assert calls[0] == ("full", f"schedule:{sid}")
        # 성공 발화는 여기서 run 행을 만들지 않는다 (엔진 미러 몫)
        assert not [r for r in db.list_runs(20)
                    if (r["trigger"] or "") == f"schedule:{sid}"]
        # 실패 발화 → failed 런 행 (사유 포함)
        c2.launch_suite_run = lambda suite, trigger="": (False, "heavy 거부(테스트)")
        with db.connect() as con:
            con.execute("UPDATE schedules SET last_fired='2026-01-01T00:00:00Z'"
                        " WHERE id=?", (sid,))
        scheduler.tick()
        rows = [dict(r) for r in db.list_runs(20)
                if (r["trigger"] or "") == f"schedule:{sid}"]
        assert rows and rows[0]["status"] == "failed", rows
        assert "heavy" in (rows[0]["detail"] or "")
    finally:
        c2.launch_suite_run = real
        db.delete_schedule(sid)
    # 3) heavy suite 등록 차단 + 정상 suite 는 통과
    r = client.post("/schedules", data={"cron": "0 2 * * *", "suite": "full-heavy"})
    assert r.status_code == 400
    r2 = client.post("/schedules", data={"cron": "0 2 * * *", "suite": "full"})
    assert r2.status_code == 200
    for srow in db.list_schedules():
        if srow["suite"] == "full" and srow["cron"] == "0 2 * * *":
            db.delete_schedule(srow["id"])
    # 화면 문구 — 실행 경로 정직화
    page = client.get("/testing").text
    assert "이" in page and "서버(콘솔 엔진)에서 LIVE 실행" in page


# --- runner -----------------------------------------------------------------------

TESTS = [
    test_tenant_columns_exist,
    test_command_crud_and_ack_idempotency,
    test_command_validation,
    test_command_api_token_gate,
    test_intervention_ui_only_when_running,
    test_inventory_folding_created_then_deleted_is_gone,
    test_inventory_platform_delete_marks_gone_only_on_ok,
    test_delete_gated_without_destructive_env,
    test_empty_inventory_explains_ingest_only,
    test_error_empty_states,
    test_reporting_subtabs_single_include,
    test_ia_catalog_absorbed_into_modeling,
    test_compare_diff_buckets,
    test_compare_view_with_stubbed_snapshots,
    test_editor_pages_render,
    test_dependencies_view_renders,
    test_propose_edit_rejects_and_restores,
    test_validate_only_passes_real_validators_and_restores,
    test_good_edit_applies_and_git_commits,
    test_quota_simulation_warns_on_peak_over_limit,
    test_quota_simulation_runs_on_dependencies_save,
    test_source_badges_and_empty_states,
    test_v2_shell_header_and_global_search,
    test_modeling_improvements_batch,
    test_triage_new_fail_detail_and_known_list,
    test_coverage_map_removed_and_runs_timeline_merged,
    test_scheduler_fires_local_suite_run_and_heavy_schedule_blocked,
]


def main() -> int:
    failed = 0
    for fn in TESTS:
        try:
            fn()
            print(f"PASS  {fn.__name__}")
        except Exception:
            failed += 1
            print(f"FAIL  {fn.__name__}")
            traceback.print_exc()
    print(f"\n{len(TESTS) - failed}/{len(TESTS)} passed (db: {os.environ['PLATFORM_DB']})")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
