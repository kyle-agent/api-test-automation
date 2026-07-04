"""Offline tests — console2 Run 관측성 개편 (2026-07-04 batch).

Covers the server halves of the owner-approved items:
  * D/신규2  run-history rehydration from reports/console2-runs/*.log/.events
             + controlplane runs-DB mirroring (record_local_run upsert).
  * B/F3    _events_summary closes still-open steps of a FAILED lifecycle as
             fail (the JS mirror lives in console2.js groupEventsByLifecycle —
             manual check: run 흐름 API tab, a timed-out step must show
             "fail (timeout/중단)" and bump the fail KPI).
  * C/신규1  delayed post-run owned re-scans (+0/+5m/+15m) with a FAKE clock;
             a later scan finding MORE than +0 raises late_alert.
  * A/F1    /api/runs/<id>/graph — the run's OWN lifecycle-closure graph
             (same composer.graph_view contract as /api/graph).
  * F/신규8  known-stuck annotation on the owned inventory (folded, not red).
  * F/신규10 capacity '내 실행' attribution keyed on run-known VPC ids.
  * E/신규5  /runtime freshness chip + auto-refresh when the window is stale.

Hermetic: no network, no credentials — module state is monkeypatched and all
file IO goes to tmp_path.
"""
from __future__ import annotations

import importlib.util
import json
import os
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def _load_server():
    """Load tools/console2_server.py fresh WITHOUT the import-time rehydration
    (tests drive _rehydrate_runs with explicit tmp dirs instead)."""
    os.environ["SCP_CONSOLE_REHYDRATE"] = "false"
    try:
        spec = importlib.util.spec_from_file_location(
            "console2_server_obs", ROOT / "tools" / "console2_server.py")
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
    finally:
        os.environ.pop("SCP_CONSOLE_REHYDRATE", None)
    return mod


C2 = _load_server()


def _ev(kind, **f):
    return {"ts": f.pop("ts", 1000.0), "kind": kind, **f}


# --------------------------------------------------------------------------- #
# _events_summary — the run verdict fold (+ fail-row closure, F3)
# --------------------------------------------------------------------------- #
def test_events_summary_counts_and_closes_open_steps_as_fail():
    events = [
        _ev("lifecycle-start", lifecycle="a", service="vpc"),
        _ev("step-start", lifecycle="a", step="s1", method="POST", path="/v1/x"),
        _ev("step-end", lifecycle="a", step="s1", status=201, category="ok"),
        _ev("step-start", lifecycle="a", step="s2", method="GET", path="/v1/x/1"),
        # s2 NEVER ends (timeout) — lifecycle-end(failed) must close it as fail
        _ev("lifecycle-end", lifecycle="a", status="failed"),
        _ev("lifecycle-start", lifecycle="b", service="vpc"),
        _ev("step-start", lifecycle="b", step="t1", method="GET", path="/v1/y"),
        _ev("step-end", lifecycle="b", step="t1", status=404, category="soft"),
        _ev("lifecycle-end", lifecycle="b", status="passed"),
    ]
    s = C2._events_summary(events)
    assert s["lifecycles"]["total"] == 2
    assert s["lifecycles"]["passed"] == 1
    assert s["lifecycles"]["failed"] == 1
    assert s["lifecycles"]["failed_ids"] == ["a"]
    # the phantom ⏳ step counts as a FAIL, immediately
    assert s["api"] == {"ok": 1, "soft": 1, "fail": 1}


def test_events_summary_leaves_unfinished_lifecycles_marked():
    events = [
        _ev("lifecycle-start", lifecycle="a"),
        _ev("step-start", lifecycle="a", step="s1"),
    ]
    s = C2._events_summary(events)
    assert s["lifecycles"]["unfinished"] == 1
    assert s["api"]["fail"] == 0        # no lifecycle-end yet → not closed


# --------------------------------------------------------------------------- #
# rehydration (신규2) — _RUNS rebuilt from the on-disk remains
# --------------------------------------------------------------------------- #
def _write_run_files(rd: Path, rid: str, *, failed=True):
    log = rd / f"{rid}.log"
    log.write_text(
        f"# console2 run {rid}  lifecycle_ids=['virtualserver-keypair', 'vs-x']\n"
        "# gates: mutations=True destructive=True heavy=True  parallel=2\n"
        "\n=== pytest ===\n"
        + ("1 failed, 6 passed, 7 warnings in 1116.95s (0:18:36)\n" if failed
           else "7 passed in 100.00s\n"),
        encoding="utf-8")
    ev = rd / f"{rid}.events.jsonl"
    lines = [
        _ev("lifecycle-start", lifecycle="virtualserver-keypair", ts=100.0),
        _ev("step-start", lifecycle="virtualserver-keypair", step="create", ts=101.0),
        _ev("step-end", lifecycle="virtualserver-keypair", step="create",
            status=201, category="ok", ts=102.0),
        _ev("lifecycle-end", lifecycle="virtualserver-keypair",
            status="passed", ts=103.0),
    ]
    ev.write_text("\n".join(json.dumps(x) for x in lines) + "\n", encoding="utf-8")


def test_rehydrate_runs_rebuilds_records_from_disk(tmp_path):
    rd = tmp_path / "runs"
    rd.mkdir()
    _write_run_files(rd, "20260704-000000-t3st")
    # 0-byte remains (aborted starts) must be SKIPPED — pure noise in the list
    (rd / "20260704-000001-zero.events.jsonl").write_text("")
    n = C2._rehydrate_runs(run_dir=rd)
    try:
        assert n == 1
        rec = C2._RUNS["20260704-000000-t3st"]
        assert rec["rehydrated"] is True
        assert rec["kind"] == "lifecycle" and rec["mode"] == "live"
        assert rec["status"] == "done" and rec["rc"] == 1     # pytest '1 failed'
        assert rec["lifecycle_ids"] == ["virtualserver-keypair", "vs-x"]
        assert rec["heavy"] and rec["mutations"] and rec["destructive"]
        assert rec["started"] == 100.0 and rec["ended"] == 103.0
        assert rec["events_summary"]["lifecycles"]["passed"] == 1
        v = C2._rec_view(rec)
        assert v["rehydrated"] is True
        assert "passed" in v["summary"]                        # pytest tail line
        assert "20260704-000001-zero" not in C2._RUNS
    finally:
        C2._RUNS.pop("20260704-000000-t3st", None)


def test_rehydrate_is_idempotent_and_never_overwrites_live_recs(tmp_path):
    rd = tmp_path / "runs"
    rd.mkdir()
    _write_run_files(rd, "20260704-000002-live")
    sentinel = {"id": "20260704-000002-live", "kind": "lifecycle",
                "status": "running", "log": "x", "events": "y",
                "lifecycle_ids": [], "started": 1.0}
    C2._RUNS["20260704-000002-live"] = sentinel
    try:
        assert C2._rehydrate_runs(run_dir=rd) == 0
        assert C2._RUNS["20260704-000002-live"] is sentinel
    finally:
        C2._RUNS.pop("20260704-000002-live", None)


def test_local_run_summary_reads_the_events_file(tmp_path, monkeypatch):
    rd = tmp_path / "runs"
    rd.mkdir()
    _write_run_files(rd, "20260704-000003-summ")
    monkeypatch.setattr(C2, "RUN_DIR", rd)
    s = C2._local_run_summary("local-20260704-000003-summ")
    assert s and s["lifecycles"]["passed"] == 1
    assert C2._local_run_summary("local-20260704-nope") is None


# --------------------------------------------------------------------------- #
# controlplane runs-DB mirroring (P2-9 완결)
# --------------------------------------------------------------------------- #
def test_record_local_run_upserts_and_run_detail_summary(tmp_path, monkeypatch):
    from controlplane import db as cdb
    monkeypatch.setattr(cdb, "DB_PATH", tmp_path / "platform.db")
    rid = cdb.record_local_run("local-t-1", status="failed",
                               requested_at="2026-07-04T00:00:00Z",
                               finished_at="2026-07-04T00:20:00Z",
                               detail='{"summary": 1}')
    # idempotent: same gh_run_id → same row, status converges
    rid2 = cdb.record_local_run("local-t-1", status="done")
    assert rid == rid2
    row = cdb.get_run("local-t-1")
    assert row["status"] == "done"
    assert row["suite"] == "console2" and row["trigger"] == "local"
    assert row["detail"] == '{"summary": 1}'       # first detail wins
    assert row["finished_at"] == "2026-07-04T00:20:00Z"


def test_record_run_to_db_mirrors_a_finished_rec(tmp_path, monkeypatch):
    from controlplane import db as cdb
    monkeypatch.setattr(cdb, "DB_PATH", tmp_path / "platform.db")
    rd = tmp_path / "runs"
    rd.mkdir()
    _write_run_files(rd, "20260704-000004-db")
    C2._rehydrate_runs(run_dir=rd)
    try:
        C2._record_run_to_db(C2._RUNS["20260704-000004-db"])
        row = cdb.get_run("local-20260704-000004-db")
        assert row is not None and row["status"] == "failed"   # rc=1
        detail = json.loads(row["detail"])
        assert detail["summary"]["lifecycles"]["passed"] == 1
        assert detail["lifecycle_ids"] == ["virtualserver-keypair", "vs-x"]
    finally:
        C2._RUNS.pop("20260704-000004-db", None)


# --------------------------------------------------------------------------- #
# delayed post-run re-scans (신규1) — fake clock, injectable scan
# --------------------------------------------------------------------------- #
def test_post_run_rescans_late_resources_raise_alert(tmp_path):
    rec = {"id": "t", "log": str(tmp_path / "t.log")}
    Path(rec["log"]).write_text("", encoding="utf-8")
    waits = []
    scans = iter([
        [],                                                     # +0  → 0건
        [],                                                     # +5m → 0건
        [{"service": "virtualserver", "path": "/v1/images/i1"},
         {"service": "virtualserver", "path": "/v1/snapshots/s1"}],  # +15m → 2건!
    ])
    C2._post_run_rescans(rec, offsets=(0, 300, 900),
                         scan=lambda: next(scans), sleep=waits.append)
    assert waits == [300, 600]                # +0 immediate, then 5m, then +10m
    assert [e["total"] for e in rec["rescans"]] == [0, 0, 2]
    assert rec["late_alert"]["delta"] == 2 and rec["late_alert"]["base"] == 0
    assert "늦출현" in rec["late_alert"]["msg"]
    log = Path(rec["log"]).read_text(encoding="utf-8")
    assert "종료 후 재스캔 +0s: 0건" in log or "종료 후 재스캔 +0m: 0건" in log
    assert "늦출현 2건" in log
    # the alert (and rescans) ride the public record view → the UI can render it
    view_keys = C2._REC_VIEW_KEYS
    assert "late_alert" in view_keys and "rescans" in view_keys


def test_post_run_rescans_no_alert_when_stable_and_errors_tolerated(tmp_path):
    rec = {"id": "t2", "log": str(tmp_path / "t2.log")}
    Path(rec["log"]).write_text("", encoding="utf-8")

    calls = {"n": 0}

    def scan():
        calls["n"] += 1
        if calls["n"] == 2:
            raise RuntimeError("creds gone")
        return [{"service": "vpc", "path": "/v1/vpcs/v"}]

    C2._post_run_rescans(rec, offsets=(0, 1, 2), scan=scan, sleep=lambda s: None)
    assert "late_alert" not in rec
    assert [e.get("total") for e in rec["rescans"]] == [1, None, 1]
    assert rec["rescans"][1]["error"].startswith("creds")


def test_run_worker_log_line_softened_no_premature_deleted_claim():
    """The run-end log must not claim everything was deleted — teardown was
    ATTEMPTED and the measured re-scans are the verdict (신규1)."""
    import inspect
    src = inspect.getsource(C2._run_worker)
    assert "teardown 시도 완료" in src
    assert "실측 재스캔 예약" in src
    assert "이미 삭제됨" not in src


# --------------------------------------------------------------------------- #
# run graph (F1) — the run's own lifecycle closure via composer.graph_view
# --------------------------------------------------------------------------- #
def test_run_graph_projects_the_runs_lifecycle_closure():
    m = C2._model()
    nid, node = next((i, n) for i, n in m["nodes"].items() if n.get("lifecycle"))
    g = C2._run_graph({"lifecycle_ids": [node["lifecycle"]]})
    ids = {n["id"] for n in g["nodes"]}
    assert nid in ids and g["nodes"], "run graph must contain the lifecycle's node"
    # frozen graph_view shape (IA-BUILD-CONTRACT) — same keys as /api/graph
    for key in ("nodes", "edges", "levels", "shared", "peak_quota", "order", "teardown"):
        assert key in g
    assert C2._run_graph({"lifecycle_ids": []})["nodes"] == []
    assert C2._run_graph({"lifecycle_ids": ["no-such-lifecycle"]})["nodes"] == []


# --------------------------------------------------------------------------- #
# known-stuck folding on the owned inventory (신규8)
# --------------------------------------------------------------------------- #
def test_annotate_known_stuck_marks_documented_residues(monkeypatch):
    monkeypatch.setattr(C2, "_known_stuck_entries", lambda: [
        {"id": "47fabeca13f24958a0344a00011a274d", "name": "/scp/ske/regrske",
         "reason": "IAM-gated"},
        {"id": "", "name": "regrw5trg57f68be7", "reason": "PLS deadlock"},
    ])
    owned = [
        {"service": "servicewatch", "path": "/v1/log-groups",
         "json": {"ids": ["47fabeca13f24958a0344a00011a274d"]}},
        {"service": "scf", "path": "/v1/cloud-functions/regrw5trg57f68be7"},
        {"service": "vpc", "path": "/v1/vpcs/deadbeef"},
    ]
    n = C2._annotate_known_stuck(owned)
    assert n == 2
    assert owned[0]["known_stuck"]["reason"] == "IAM-gated"
    assert owned[1]["known_stuck"]["reason"] == "PLS deadlock"
    assert "known_stuck" not in owned[2]
    # summary: the red count EXCLUDES the folded 기지 항목
    rec = {"id": "x", "kind": "owned", "status": "done",
           "owned_total": 3, "owned_known_stuck": 2, "log": "/nonexistent"}
    assert "1건" in C2._summarize(rec, "") and "기지 2건 제외" in C2._summarize(rec, "")
    rec["owned_total"] = 2
    assert "없음 ✅" in C2._summarize(rec, "")


def test_cleanup_summary_prefers_genuine_removed_lines():
    rec = {"id": "c", "kind": "cleanup", "status": "done", "log": "/nonexistent"}
    log = ("--- sweep round 1 ---\nsweep done: 5 resource(s) deleted\n"
           "sweep round 1 genuine-removed: 3\n"
           "--- sweep round 2 ---\nsweep done: 2 resource(s) deleted\n"
           "sweep round 2 genuine-removed: 0\n")
    assert "3 resource(s) deleted" in C2._summarize(rec, log)
    # legacy logs (no genuine lines) keep the old tally
    legacy = "sweep done: 4 resource(s) deleted\n"
    assert "4 resource(s) deleted" in C2._summarize(rec, legacy)


# --------------------------------------------------------------------------- #
# capacity '내 실행' attribution (신규10)
# --------------------------------------------------------------------------- #
def test_capacity_view_keeps_my_vpc_out_of_the_baseline(monkeypatch):
    monkeypatch.setattr(C2, "_vpc_cap", lambda: 5)
    monkeypatch.setattr(C2, "_account_vpc_count", lambda ttl=12.0: 2)
    C2._VPCCNT["rows"] = [{"id": "vpc-mine", "name": "regrvpcsh6a"},
                          {"id": "vpc-other", "name": "someone-else"}]
    monkeypatch.setattr(C2, "_local_res_index",
                        lambda: {"r1": {"ids": {"vpc-mine"}, "names": set()}})
    with C2._ADMIT:
        saved_res = dict(C2._RESERVED)
        C2._RESERVED.clear()
    try:
        c = C2._capacity_view()
        assert c["mine_live"] == 1
        assert c["baseline"] == 1          # my shared VPC never drifts to '기존'
        assert c["account_live"] == 2
        # my live VPC still occupies a real slot → headroom 5-1-max(0,1)=3
        assert c["headroom"] == 3
    finally:
        with C2._ADMIT:
            C2._RESERVED.update(saved_res)
        C2._VPCCNT["rows"] = []


def test_shared_vpc_id_counts_as_mine(monkeypatch):
    monkeypatch.setattr(C2, "_local_res_index", lambda: {})
    C2._RUNS["shared-test-rec"] = {"id": "shared-test-rec",
                                   "shared_vpc_id": "vpc-shared-1"}
    C2._VPCCNT["rows"] = [{"id": "vpc-shared-1", "name": "regrvpcsh"}]
    try:
        assert C2._mine_live_vpcs() == 1
    finally:
        C2._RUNS.pop("shared-test-rec", None)
        C2._VPCCNT["rows"] = []


# --------------------------------------------------------------------------- #
# runtime freshness (신규5)
# --------------------------------------------------------------------------- #
def test_runtime_view_stale_window_gets_age_chip_and_auto_refresh():
    with C2._RUNTIME_LOCK:
        saved = dict(C2._RUNTIME_CACHE)
        C2._RUNTIME_CACHE.update(
            events=[], oplog=[], error=None,
            meta={"start": "2026-07-04T00:00:00Z", "end": "2026-07-04T01:00:00Z"},
            ts=time.monotonic(),                 # monotonic-fresh → no bg regen
            wall=time.time() - 25 * 60,          # …but the WINDOW is 25min old
            hours=1.0, generating=True)          # block regen thread spawn
    try:
        html, _ = C2._runtime_view(1.0, scope="all", deleted="hide")
        assert html and "데이터 기준: 25분 전 윈도우" in html
        assert 'http-equiv="refresh"' in html    # stale → auto-refresh to converge
    finally:
        with C2._RUNTIME_LOCK:
            C2._RUNTIME_CACHE.clear()
            C2._RUNTIME_CACHE.update(saved)


def test_runtime_view_fresh_window_has_no_stale_chip():
    with C2._RUNTIME_LOCK:
        saved = dict(C2._RUNTIME_CACHE)
        C2._RUNTIME_CACHE.update(
            events=[], oplog=[], error=None,
            meta={"start": "s", "end": "e"}, ts=time.monotonic(),
            wall=time.time(), hours=1.0, generating=True)
    try:
        html, _ = C2._runtime_view(1.0, scope="all", deleted="hide")
        assert html and "분 전 윈도우" not in html
        assert 'http-equiv="refresh"' not in html
    finally:
        with C2._RUNTIME_LOCK:
            C2._RUNTIME_CACHE.clear()
            C2._RUNTIME_CACHE.update(saved)
