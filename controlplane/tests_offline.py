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

from fastapi.testclient import TestClient  # noqa: E402

from controlplane import authoring, compare, db, resources, snapshots  # noqa: E402
from controlplane.app import app  # noqa: E402

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
    test_compare_diff_buckets,
    test_compare_view_with_stubbed_snapshots,
    test_editor_pages_render,
    test_dependencies_view_renders,
    test_propose_edit_rejects_and_restores,
    test_validate_only_passes_real_validators_and_restores,
    test_good_edit_applies_and_git_commits,
    test_quota_simulation_warns_on_peak_over_limit,
    test_quota_simulation_runs_on_dependencies_save,
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
