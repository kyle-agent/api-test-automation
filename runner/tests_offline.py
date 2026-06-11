"""Offline tests for the M4 same-host worker (runner/worker.py) + executor
switch (controlplane/dispatch.py).

No network, no subprocesses (worker._run is stubbed to RECORD the commands
that would run), throwaway temp DB. Rerunnable from the repo root:

    PYTHONPATH=. python3 runner/tests_offline.py
"""
from __future__ import annotations

import os
import sys
import tempfile
import traceback
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

# fresh throwaway DB + clean gate env BEFORE importing controlplane.db
os.environ["PLATFORM_DB"] = os.path.join(
    tempfile.mkdtemp(prefix="worker-test-"), "platform.db")
for var in ("PLATFORM_EXECUTOR", "PLATFORM_GITHUB_TOKEN", "PLATFORM_GITHUB_REPO",
            "GITHUB_TOKEN", "SCP_RUN_CRUD", "SCP_RUN_HEAVY", "SCP_RUN_CONFORMANCE",
            "PLATFORM_AUTO_TRIAGE", "APITEST_PLATFORM_URL", "SCP_PROFILE_FORBID",
            "SCP_SHARED_VPC_ID", "SCP_SHARED_SUBNET_ID", "SCP_SHARED_VPC_DISABLE",
            "SCP_ALLOW_MUTATIONS", "SCP_ALLOW_DESTRUCTIVE", "SCP_SWEEP_IGNORE_TTL"):
    os.environ.pop(var, None)

from controlplane import db, dispatch  # noqa: E402
from runner import worker  # noqa: E402

worker.PROVISION_RETRY_SLEEP = 0.0  # tests never sleep

PRINT_FILTERS_OUT = ("ADOPT_K=adopt-a or adopt-b\n"
                     "VPC_CRUD_K=vpc-a or vpc-b\n"
                     "PARALLEL_K=not (vpc-a or vpc-b)\n")
PROVISION_OUT = ("SCP_SHARED_VPC_ID=vpc-shared-1\n"
                 "SCP_SHARED_SUBNET_ID=subnet-shared-1\n")


class StubRunner:
    """Records every command worker._run would execute; scripted failures by
    label, scripted stdout for the two parse-its-output commands."""

    def __init__(self, fail_labels=(), empty_provision=False):
        self.calls: list[tuple[list[str], dict, str]] = []
        self.fail_labels = set(fail_labels)
        self.empty_provision = empty_provision

    def __call__(self, args, env, timeout=None, label=""):
        self.calls.append((list(args), dict(env), label))
        if "--print-filters" in args:
            return 0, PRINT_FILTERS_OUT
        if "--provision" in args:
            return (0, "") if self.empty_provision else (0, PROVISION_OUT)
        return (1, "") if label in self.fail_labels else (0, "")

    # ---- helpers over the recorded stage commands (oplog parity noise dropped)
    def stage_calls(self):
        return [(a, e, l) for a, e, l in self.calls if not l.startswith("oplog-")]

    def labels(self):
        return [l for _, _, l in self.stage_calls()]

    def find(self, label):
        return [(a, e) for a, e, l in self.stage_calls() if l == label]


def _process(suite, profile="", fail_labels=(), **kw):
    rid = db.create_run(suite, profile, trigger="manual")
    run = worker.claim_next()
    assert run and run["id"] == rid, (rid, run)
    stub = StubRunner(fail_labels=fail_labels, **kw)
    orig = worker._run
    worker._run = stub
    try:
        status = worker.process_run(run)
    finally:
        worker._run = orig
    return run, stub, status


def _milestones(gh):
    return [(e["stage"], e["status"], e["job"])
            for e in db.list_events(gh, kind="milestone")]


def _drain_queue():
    while worker.claim_next() is not None:
        pass


# --- 1. executor switch -------------------------------------------------------------

def test_dispatch_executor_switch():
    # default = actions, unchanged: unconfigured → records-only message
    assert dispatch.executor() == "actions"
    ok, msg = dispatch.dispatch_run("smoke")
    assert not ok and "not configured" in msg, (ok, msg)
    assert not dispatch.configured()
    # worker mode: no GitHub call, queued message, always "configured"
    os.environ["PLATFORM_EXECUTOR"] = "worker"
    try:
        assert dispatch.executor() == "worker"
        assert dispatch.configured()
        assert dispatch.dispatch_run("full", "stage-kr-west1") == (
            True, "queued for local worker")
    finally:
        del os.environ["PLATFORM_EXECUTOR"]
    # typos fall back to actions (runs must not silently vanish)
    os.environ["PLATFORM_EXECUTOR"] = "wroker"
    try:
        assert dispatch.executor() == "actions"
    finally:
        del os.environ["PLATFORM_EXECUTOR"]


# --- 2. claim semantics ---------------------------------------------------------------

def test_claim_oldest_first_and_id_shape():
    _drain_queue()
    first = db.create_run("smoke", "", trigger="manual")
    second = db.create_run("full", "", trigger="manual")
    got = worker.claim_next()
    assert got["id"] == first and got["suite"] == "smoke", got
    assert got["gh_run_id"].startswith("local-"), got
    row = db.get_run(got["gh_run_id"])
    assert row["status"] == "running" and row["started_at"], dict(row)
    got2 = worker.claim_next()
    assert got2["id"] == second
    assert got2["gh_run_id"] != got["gh_run_id"]  # UNIQUE even in the same second
    assert worker.claim_next() is None  # queue drained


def test_two_workers_cannot_claim_same_run():
    _drain_queue()
    rid = db.create_run("smoke", "", trigger="manual")
    # worker 1 wins the row
    got = worker.claim_next()
    assert got["id"] == rid
    # worker 2's guarded UPDATE (the exact claim statement) matches 0 rows
    with db.connect() as con:
        cur = con.execute(
            "UPDATE runs SET gh_run_id = ?, status = 'running', started_at = ?"
            " WHERE id = ? AND gh_run_id IS NULL AND status = 'dispatched'",
            ("local-9999999999", db.now(), rid))
        assert cur.rowcount == 0, "second claimant must lose the race"
    assert worker.claim_next() is None


def test_already_bound_actions_runs_are_never_claimed():
    _drain_queue()
    db.create_run("smoke", "", gh_run_id="27314355676")  # dispatched but bound
    assert worker.claim_next() is None


def test_ingest_never_fifo_steals_worker_queue():
    """In worker mode an out-of-band Actions run's first ingest event must get
    an 'external' record, NOT bind to a queued (unclaimed) worker run."""
    _drain_queue()
    queued = db.create_run("full", "", trigger="manual")
    os.environ["PLATFORM_EXECUTOR"] = "worker"
    try:
        rid = db.attach_run("27399999999")
    finally:
        del os.environ["PLATFORM_EXECUTOR"]
    assert rid != queued, "ingest stole the worker queue entry"
    with db.connect() as con:
        row = con.execute("SELECT gh_run_id, status FROM runs WHERE id = ?",
                          (queued,)).fetchone()
    assert row["gh_run_id"] is None and row["status"] == "dispatched", dict(row)
    # actions mode keeps the FIFO bind (dev-period behaviour unchanged)
    rid2 = db.attach_run("27388888888")
    assert rid2 == queued
    _drain_queue()


# --- 3. suite expansion → gate mapping --------------------------------------------------

def test_suite_gate_mapping():
    empty = {}
    cases = {
        "smoke": dict(mutations=False, destructive=False, heavy=False,
                      conformance=False, sweep=False),
        "full": dict(mutations=True, destructive=True, heavy=False,
                     conformance=False, sweep=True),
        "full-heavy": dict(mutations=True, destructive=True, heavy=True,
                           conformance=True, sweep=True),
        "conformance": dict(mutations=False, destructive=False, heavy=False,
                            conformance=True, sweep=False),
    }
    for suite_id, want in cases.items():
        g = worker.gates(worker.expand_suite(suite_id), environ=empty)
        got = {k: g[k] for k in want}
        assert got == want, f"{suite_id}: {got} != {want}"
    # no suite = read-only file-trigger defaults
    g = worker.gates(worker.expand_suite(""), environ=empty)
    assert not (g["mutations"] or g["sweep"] or g["conformance"] or g["heavy"])
    # repo-Variable analogs on the worker host OR into the gates
    g = worker.gates({}, environ={"SCP_RUN_CRUD": "true"})
    assert g["mutations"] and g["destructive"] and g["sweep"]
    g = worker.gates({}, environ={"SCP_RUN_CONFORMANCE": "true"})
    assert g["conformance"] and not g["mutations"]


def test_kv_parse_and_k_filter():
    opts = worker.parse_kv_lines(
        "# comment\nsuite=full\ncrud_filter= a or b \nmutations=true\nmutations=false\n")
    assert opts["crud_filter"] == "a or b"  # inner spaces survive, edges trimmed
    assert opts["mutations"] == "false"     # last wins
    assert worker.k_filter("base") == "base"
    assert worker.k_filter("base", "", "x and y") == "(base) and (x and y)"
    assert worker.k_filter("", "only") == "only"


def test_unknown_suite_fails_before_any_stage():
    _drain_queue()
    run, stub, status = _process("no-such-suite")
    assert status == "failed"
    assert stub.stage_calls() == [], stub.stage_calls()  # nothing touched the API
    assert db.get_run(run["gh_run_id"])["status"] == "failed"


# --- 4. stage sequencing per suite ------------------------------------------------------

def _joined(stub):
    return [" ".join(a) for a, _, _ in stub.stage_calls()]

def test_smoke_runs_no_crud_no_sweep():
    _drain_queue()
    run, stub, status = _process("smoke")
    assert status == "done"
    cmds = _joined(stub)
    assert any("pytest tests/smoke -m smoke" in c for c in cmds), cmds
    assert not any("tests/crud" in c for c in cmds), cmds
    assert not any("cleanup.reconciler" in c for c in cmds), cmds
    assert not any("conformance" in c for c in cmds), cmds
    assert not any("shared_infra" in c for c in cmds), cmds
    # validation first, then smoke; dashboard + snapshot close the run
    assert any("regression.scenarios.validate" in c for c in cmds), cmds
    assert cmds.index([c for c in cmds if "scenarios.validate" in c][0]) \
        < cmds.index([c for c in cmds if "tests/smoke" in c][0])
    assert any("dashboard.build" in c for c in cmds), cmds
    assert any("core.snapshot upload" in c for c in cmds), cmds
    assert db.get_run(run["gh_run_id"])["status"] == "done"


def test_full_runs_crud_lanes_and_sweep():
    _drain_queue()
    run, stub, status = _process("full", profile="stage-kr-west1")
    assert status == "done"
    cmds = _joined(stub)
    # lane filters derived, shared infra provisioned + torn down
    assert any("--print-filters" in c for c in cmds), cmds
    assert any("--provision" in c for c in cmds), cmds
    assert any("--teardown" in c for c in cmds), cmds
    # A: adopt pass — parallel, complement filter, shared ids in env
    adopt = [(a, e) for a, e in stub.find("adopt-crud")]
    assert len(adopt) == 1
    a_args, a_env = adopt[0]
    assert "-n" in a_args and a_args[a_args.index("-n") + 1] == "6"
    assert a_args[a_args.index("-k") + 1] == "not (vpc-a or vpc-b)"
    assert a_env["SCP_SHARED_VPC_ID"] == "vpc-shared-1"
    assert a_env["SCP_ALLOW_MUTATIONS"] == "true"
    assert a_env["SCP_ALLOW_DESTRUCTIVE"] == "true"
    assert a_env["SCP_RUN_HEAVY"] == "false"
    assert a_env["APITEST_RUN_ID"] == run["gh_run_id"]
    assert a_env["SCP_PROFILE_ID"] == "stage-kr-west1"  # profile exported
    # B: vpc-crud — serial, two passes, heavy-shared-networking LAST, no shared adopt
    vpc = stub.find("vpc-crud")
    assert len(vpc) == 2, [a for a, _ in vpc]
    ks = [a[a.index("-k") + 1] for a, _ in vpc]
    assert ks == ["(vpc-a or vpc-b) and (not heavy-shared-networking)",
                  "(vpc-a or vpc-b) and (heavy-shared-networking)"], ks
    assert all(a[a.index("-n") + 1] == "0" for a, _ in vpc)
    assert all(e["SCP_SHARED_VPC_DISABLE"] == "true" for _, e in vpc)
    # teardown frees the shared VPC BEFORE the serial vpc-crud lane
    labels = stub.labels()
    assert labels.index("teardown") < labels.index("vpc-crud"), labels
    # sweep armed (mutations+destructive)
    sweep = stub.find("sweep")
    assert len(sweep) == 1 and "cleanup.reconciler" in " ".join(sweep[0][0])
    assert sweep[0][1]["SCP_ALLOW_DESTRUCTIVE"] == "true"
    assert "SCP_SWEEP_IGNORE_TTL" not in sweep[0][1]
    # full ≠ conformance
    assert not any("conformance" in c for c in cmds), cmds


def test_full_heavy_adds_heavy_gate_and_conformance():
    _drain_queue()
    run, stub, status = _process("full-heavy")
    assert status == "done"
    adopt_env = stub.find("adopt-crud")[0][1]
    assert adopt_env["SCP_RUN_HEAVY"] == "true"
    cmds = _joined(stub)
    assert any("conformance.static" in c for c in cmds), cmds
    assert any("conformance.runtime --probe all" in c for c in cmds), cmds
    assert any("conformance.baseline" in c and "--init-if-missing" in c
               for c in cmds), cmds
    conf_env = stub.find("conformance-runtime")[0][1]
    # conformance job's own gates: empty-body probes allowed, schema-live OFF
    assert conf_env["SCP_PROBE_RUNTIME"] == "true"
    assert conf_env["SCP_ALLOW_MUTATIONS"] == "true"
    assert conf_env["SCP_ALLOW_DESTRUCTIVE"] == "false"
    assert conf_env["SCP_RUN_SCHEMA_LIVE"] == "false"


def test_conformance_only_skips_regression_mutations():
    _drain_queue()
    run, stub, status = _process("conformance")
    assert status == "done"
    cmds = _joined(stub)
    assert any("tests/smoke" in c for c in cmds), cmds          # smoke still runs
    assert not any("tests/crud" in c for c in cmds), cmds       # no mutations
    assert not any("shared_infra" in c for c in cmds), cmds
    assert not any("cleanup.reconciler" in c for c in cmds), cmds  # no sweep
    assert any("conformance.runtime" in c for c in cmds), cmds


# --- 5. failure semantics ----------------------------------------------------------------

def test_failing_stage_still_sweeps_and_dashboards():
    _drain_queue()
    run, stub, status = _process("full", fail_labels={"smoke", "adopt-crud"})
    assert status == "failed"
    labels = stub.labels()
    # everything after the failures still ran (always() parity)
    for must in ("vpc-crud", "sweep", "dashboard", "snapshot", "finalize"):
        assert must in labels, (must, labels)
    assert db.get_run(run["gh_run_id"])["status"] == "failed"
    # the final dashboard milestone names the failed stages
    ms = _milestones(run["gh_run_id"])
    dash = [m for m in ms if m[0] == "dashboard"][-1]
    assert dash[1] == "failure", ms
    ev = [e for e in db.list_events(run["gh_run_id"], kind="milestone")
          if e["stage"] == "dashboard"][-1]
    assert "smoke" in ev["detail"] and "adopt-crud" in ev["detail"], ev["detail"]


def test_validate_failure_skips_tests_but_still_finishes():
    _drain_queue()
    run, stub, status = _process("full", fail_labels={"validate"})
    assert status == "failed"
    cmds = _joined(stub)
    assert not any("pytest" in c for c in cmds), cmds        # no smoke, no crud
    assert not any("conformance." in c for c in cmds), cmds  # conformance needs spec
    assert any("cleanup.reconciler" in c for c in cmds), cmds  # sweep still armed
    assert any("dashboard.build" in c for c in cmds), cmds
    assert db.get_run(run["gh_run_id"])["status"] == "failed"


def test_provision_failure_retries_then_proceeds():
    _drain_queue()
    run, stub, status = _process("full", empty_provision=True)
    assert len(stub.find("provision")) == worker.PROVISION_ATTEMPTS
    # adopt pass still ran (lifecycles fall back to self-create, like the workflow)
    adopt_env = stub.find("adopt-crud")[0][1]
    assert "SCP_SHARED_VPC_ID" not in adopt_env


# --- 6. milestone events (same shape as ingest) --------------------------------------------

def test_milestones_written_to_db():
    _drain_queue()
    run, stub, status = _process("full")
    gh = run["gh_run_id"]
    ms = _milestones(gh)
    stages = [m[0] for m in ms]
    assert stages == ["run-start", "validate", "smoke", "adopt-crud", "vpc-crud",
                      "sweep", "dashboard"], stages
    jobs = dict((m[0], m[2]) for m in ms)
    assert jobs["smoke"] == "regression-A" and jobs["vpc-crud"] == "regression-B"
    assert jobs["sweep"] == "sweep" and jobs["run-start"] == "spec"
    # run-start detail carries the resolved options (apply_milestone stores it)
    row = db.get_run(gh)
    assert "mutations=true" in (row["detail"] or ""), dict(row)
    assert row["status"] == "done" and row["finished_at"]
    # S3 archive parity emits exist but NEVER carry the platform mirror URL
    os.environ["APITEST_PLATFORM_URL"] = "http://server:8800"
    try:
        run2, stub2, _ = _process("smoke")
    finally:
        del os.environ["APITEST_PLATFORM_URL"]
    emits = [(a, e) for a, e, _ in stub2.calls if "core.oplog" in " ".join(a)]
    assert emits, "oplog parity emits missing"
    assert all(not e.get("APITEST_PLATFORM_URL") for _, e in emits)
    # …while engine children DO inherit it (mirror keeps working)
    smoke_env = stub2.find("smoke")[0][1]
    assert smoke_env["APITEST_PLATFORM_URL"] == "http://server:8800"


# --- runner -----------------------------------------------------------------------

TESTS = [
    test_dispatch_executor_switch,
    test_claim_oldest_first_and_id_shape,
    test_two_workers_cannot_claim_same_run,
    test_already_bound_actions_runs_are_never_claimed,
    test_ingest_never_fifo_steals_worker_queue,
    test_suite_gate_mapping,
    test_kv_parse_and_k_filter,
    test_unknown_suite_fails_before_any_stage,
    test_smoke_runs_no_crud_no_sweep,
    test_full_runs_crud_lanes_and_sweep,
    test_full_heavy_adds_heavy_gate_and_conformance,
    test_conformance_only_skips_regression_mutations,
    test_failing_stage_still_sweeps_and_dashboards,
    test_validate_failure_skips_tests_but_still_finishes,
    test_provision_failure_retries_then_proceeds,
    test_milestones_written_to_db,
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
