"""Offline unit tests for the DAG runner (scheduler ADR 1.0-c orchestration core).

Hermetic by construction: every test here drives ``dag_runner.run_plan`` /
``dry_run`` / ``format_run`` with FAKE executors and provisioners and SYNTHETIC
``dag_planner.Plan`` / ``Wave`` objects. No network, no engine, no credentials,
no pytest markers -> this is the offline tier (``tests/offline``), runnable with
``python -m pytest tests/offline/test_dag_runner.py -q -o addopts=""``.

WHY these invariants matter (the load-bearing safety properties):

  * Strict wave ordering is the STRUCTURAL cap-guard. The planner pre-sizes each
    self-create wave to ``vpc_cap - shared`` VPC slots; that bound is only real
    if the runner finishes wave N before starting wave N+1. If the runner ever
    overlapped waves, two cap-sized waves could run together and blow the account
    VPC cap. So "all of wave N dispatched before any of wave N+1" is not a nicety
    — it is the whole point of the wave structure.

  * Teardown-always (try/finally) protects shared infra from leaking. The shared
    VPC/subnets are stood up ONCE for the whole run; if a lifecycle fails and we
    skipped teardown, that billable shared infra would leak across runs. So
    teardown must run after a failed wave just as it does after a clean one.

  * provision-before-execute / abort-on-provision-error keeps us from running
    lifecycles against infra that was never stood up.
"""
from __future__ import annotations

import threading
import time

import pytest

from regression.scenarios import dag_planner
from regression.scenarios import dag_runner
from regression.scenarios.dag_runner import LifecycleOutcome, RunResult


# --------------------------------------------------------------------------- #
# synthetic plan + fake executor/provisioner helpers
# --------------------------------------------------------------------------- #
def make_plan(*waves: dag_planner.Wave, shared_roots=None) -> dag_planner.Plan:
    """Build a synthetic Plan straight from Wave objects.

    The runner only reads ``plan.waves`` (each ``Wave.kind`` / ``Wave.lifecycles``)
    and ``plan.shared_roots`` — it never re-derives anything — so a hand-built Plan
    exercises the real code path exactly like a planner-produced one.
    """
    return dag_planner.Plan(
        shared_roots=list(shared_roots or []),
        waves=list(waves),
    )


def passing_executor(lid: str) -> LifecycleOutcome:
    """A trivial fake executor: every lifecycle 'passes'. Executors must NOT raise
    — they convert engine errors into status='failed' — so all our fakes return an
    outcome rather than throwing."""
    return LifecycleOutcome(lid, "passed")


class RecordingExecutor:
    """Fake executor that records (id, dispatch-timestamp) for every call, thread-
    safely, so tests can assert dispatch ORDER across waves. Returns a configurable
    status per id (default 'passed')."""

    def __init__(self, statuses: dict[str, str] | None = None):
        self._statuses = statuses or {}
        self._lock = threading.Lock()
        self.calls: list[tuple[str, float]] = []  # (lid, monotonic ts) in call order

    def __call__(self, lid: str) -> LifecycleOutcome:
        with self._lock:
            self.calls.append((lid, time.monotonic()))
        return LifecycleOutcome(lid, self._statuses.get(lid, "passed"))

    @property
    def ids(self) -> list[str]:
        return [c[0] for c in self.calls]


class RecordingProvisioner:
    """Fake provisioner recording the relative ORDER of provision()/teardown()
    against executor calls via a shared event log."""

    def __init__(self, log: list[str], provision_raises: Exception | None = None):
        self._log = log
        self._provision_raises = provision_raises
        self.provision_calls = 0
        self.teardown_calls = 0

    def provision(self) -> None:
        self.provision_calls += 1
        self._log.append("provision")
        if self._provision_raises is not None:
            raise self._provision_raises

    def teardown(self) -> None:
        self.teardown_calls += 1
        self._log.append("teardown")


class LoggingExecutor:
    """Executor that appends 'exec:<id>' to a shared log (to interleave with the
    provisioner's provision/teardown markers) and returns a configurable status."""

    def __init__(self, log: list[str], statuses: dict[str, str] | None = None):
        self._log = log
        self._statuses = statuses or {}
        self._lock = threading.Lock()

    def __call__(self, lid: str) -> LifecycleOutcome:
        with self._lock:
            self._log.append(f"exec:{lid}")
        return LifecycleOutcome(lid, self._statuses.get(lid, "passed"))


# --------------------------------------------------------------------------- #
# 1. wave ordering — waves strictly sequential, provision wave runs no lifecycles
# --------------------------------------------------------------------------- #
def test_waves_dispatched_strictly_in_order():
    """All of wave N's ids must be dispatched before ANY of wave N+1's.

    This is the cap-safety invariant: each self-create wave is pre-sized to the
    VPC budget, and that bound only holds if waves never overlap. We build a
    provision wave + an adopt wave + two self-create waves and assert that the
    flattened dispatch order respects wave boundaries.
    """
    plan = make_plan(
        dag_planner.Wave(kind="provision", lifecycles=["vpc", "subnet"]),
        dag_planner.Wave(kind="adopt", lifecycles=["a1", "a2", "a3"]),
        dag_planner.Wave(kind="self-create", lifecycles=["s1", "s2"]),
        dag_planner.Wave(kind="self-create", lifecycles=["s3", "s4"]),
        shared_roots=["vpc", "subnet"],
    )
    ex = RecordingExecutor()
    result = dag_runner.run_plan(plan, ex)

    # The provision wave executes NO lifecycles — its roots are stood up by the
    # provisioner, not run as lifecycles. So 'vpc'/'subnet' must never reach the
    # executor.
    assert "vpc" not in ex.ids and "subnet" not in ex.ids

    # Group the dispatched ids by which wave they belong to and assert the LAST
    # dispatch of wave N happened before the FIRST dispatch of wave N+1.
    wave_groups = [["a1", "a2", "a3"], ["s1", "s2"], ["s3", "s4"]]
    # map id -> dispatch timestamp
    ts = {lid: t for lid, t in ex.calls}
    for n in range(len(wave_groups) - 1):
        last_of_n = max(ts[i] for i in wave_groups[n])
        first_of_next = min(ts[i] for i in wave_groups[n + 1])
        assert last_of_n <= first_of_next, (
            f"wave {n} overlapped wave {n + 1}: cap-safety broken")

    # And the provision wave is recorded as an empty-outcome WaveResult.
    prov_wave = result.waves[0]
    assert prov_wave.kind == "provision"
    assert prov_wave.outcomes == []

    # Every non-provision lifecycle appears exactly once, in wave order.
    assert ex.ids == ["a1", "a2", "a3", "s1", "s2", "s3", "s4"]


# --------------------------------------------------------------------------- #
# 2. within-wave concurrency — barrier proves parallelism; max_workers=1 serializes
# --------------------------------------------------------------------------- #
def test_within_wave_runs_concurrently():
    """Lifecycles within ONE wave must run concurrently when max_workers allows.

    Proof technique: a Barrier sized to the wave. Each executor call waits on the
    barrier; the barrier only releases once ALL parties have arrived. If the runner
    serialized the wave, the first call would block forever (the others never start
    to release it) and we'd hit the barrier timeout -> test fails. Releasing
    cleanly proves true concurrency.
    """
    ids = ["c1", "c2", "c3", "c4"]
    barrier = threading.Barrier(len(ids), timeout=5)
    seen = []
    seen_lock = threading.Lock()

    def barrier_executor(lid: str) -> LifecycleOutcome:
        barrier.wait()  # raises BrokenBarrierError on timeout -> surfaced as fail
        with seen_lock:
            seen.append(lid)
        return LifecycleOutcome(lid, "passed")

    plan = make_plan(dag_planner.Wave(kind="adopt", lifecycles=ids))
    # max_workers defaults to len(ids), so the whole wave can run at once.
    result = dag_runner.run_plan(plan, barrier_executor)

    assert sorted(seen) == sorted(ids)
    assert result.by_status() == {"passed": len(ids)}
    assert result.ok


def test_max_workers_one_forces_sequential():
    """max_workers=1 must serialize a wave. We assert this by timing: a sleeping
    executor of N lifecycles at width 1 takes >= N*delay; if it parallelized it
    would take ~1*delay. (No barrier here — a barrier would deadlock a correctly
    serialized runner, which is itself the proof that width-1 is sequential.)"""
    ids = ["c1", "c2", "c3"]
    delay = 0.05
    order = []

    def sleeping_executor(lid: str) -> LifecycleOutcome:
        order.append(lid)
        time.sleep(delay)
        return LifecycleOutcome(lid, "passed")

    plan = make_plan(dag_planner.Wave(kind="adopt", lifecycles=ids))
    t0 = time.monotonic()
    result = dag_runner.run_plan(plan, sleeping_executor, max_workers=1)
    elapsed = time.monotonic() - t0

    # Serial execution: ids run one after another, in input order.
    assert order == ids
    # Total wall time is at least the sum of the sleeps (allow scheduler slack).
    assert elapsed >= delay * len(ids) * 0.9
    assert result.ok


# --------------------------------------------------------------------------- #
# 3. provisioner lifecycle — provision-before, teardown-after, teardown-on-fail,
#    abort-on-provision-error
# --------------------------------------------------------------------------- #
def test_provision_before_and_teardown_after():
    """provision() runs ONCE before any executor call; teardown() runs ONCE after
    all waves. Verified via a single shared event log."""
    log: list[str] = []
    prov = RecordingProvisioner(log)
    ex = LoggingExecutor(log)
    plan = make_plan(
        dag_planner.Wave(kind="provision", lifecycles=["vpc"]),
        dag_planner.Wave(kind="adopt", lifecycles=["a1"]),
        dag_planner.Wave(kind="self-create", lifecycles=["s1"]),
        shared_roots=["vpc"],
    )
    dag_runner.run_plan(plan, ex, provisioner=prov)

    assert prov.provision_calls == 1
    assert prov.teardown_calls == 1
    # provision must precede every exec, and teardown must follow every exec.
    assert log[0] == "provision"
    assert log[-1] == "teardown"
    assert log.index("provision") < min(i for i, e in enumerate(log)
                                        if e.startswith("exec:"))
    assert log.index("teardown") > max(i for i, e in enumerate(log)
                                       if e.startswith("exec:"))


def test_teardown_runs_even_when_a_lifecycle_fails():
    """teardown() must STILL run after a 'failed' outcome — this is the leaked-
    shared-infra guard. A failed lifecycle returns status='failed' (it does NOT
    raise), and the runner's try/finally must still tear the shared roots down."""
    log: list[str] = []
    prov = RecordingProvisioner(log)
    ex = LoggingExecutor(log, statuses={"a1": "failed"})
    plan = make_plan(
        dag_planner.Wave(kind="provision", lifecycles=["vpc"]),
        dag_planner.Wave(kind="adopt", lifecycles=["a1", "a2"]),
        shared_roots=["vpc"],
    )
    result = dag_runner.run_plan(plan, ex, provisioner=prov)

    assert prov.teardown_calls == 1
    assert log[-1] == "teardown"
    assert result.by_status().get("failed") == 1
    assert result.ok is False  # a failure makes the run not-ok


def test_provision_error_aborts_run_no_executor_no_teardown():
    """If provision() raises, the run aborts: provision_error is set, NO executor
    runs, NO wave is recorded — and (per the ACTUAL code) teardown() is NOT called,
    because provision returns early BEFORE entering the try/finally block.

    NOTE: this asserts against the real behavior of dag_runner.run_plan (read at
    lines 100-106): the provisioner try/except returns the RunResult immediately on
    a provision failure, so the finally-teardown is never reached. teardown is the
    provisioner's responsibility to make idempotent if provision partially stood
    infra up; the runner deliberately does not call it here.
    """
    boom = RuntimeError("no quota for shared VPC")
    log: list[str] = []
    prov = RecordingProvisioner(log, provision_raises=boom)
    ex = RecordingExecutor()
    plan = make_plan(
        dag_planner.Wave(kind="provision", lifecycles=["vpc"]),
        dag_planner.Wave(kind="adopt", lifecycles=["a1", "a2"]),
        shared_roots=["vpc"],
    )
    result = dag_runner.run_plan(plan, ex, provisioner=prov)

    # provision_error carries "TypeName: message".
    assert result.provision_error == "RuntimeError: no quota for shared VPC"
    assert prov.provision_calls == 1
    assert prov.teardown_calls == 0          # teardown NOT reached on early-return
    assert ex.calls == []                    # no lifecycle ever dispatched
    assert result.waves == []                # no wave recorded
    assert result.ok is False                # provision_error -> not ok
    # shared_roots are still carried through from the plan even on abort.
    assert result.shared_roots == ["vpc"]


# --------------------------------------------------------------------------- #
# 4. result aggregation — by_status / .ok / .outcomes flattening
# --------------------------------------------------------------------------- #
def test_result_aggregation_mixed_statuses():
    """Mixed passed/skipped/failed across waves: by_status counts each, .outcomes
    flattens all waves in order, and .ok is False iff anything failed."""
    statuses = {
        "a1": "passed", "a2": "skipped", "a3": "failed",
        "s1": "passed", "s2": "skipped",
    }
    ex = RecordingExecutor(statuses)
    plan = make_plan(
        dag_planner.Wave(kind="provision", lifecycles=["vpc"]),
        dag_planner.Wave(kind="adopt", lifecycles=["a1", "a2", "a3"]),
        dag_planner.Wave(kind="self-create", lifecycles=["s1", "s2"]),
        shared_roots=["vpc"],
    )
    result = dag_runner.run_plan(plan, ex)

    assert result.by_status() == {"passed": 2, "skipped": 2, "failed": 1}
    # .outcomes flattens ALL non-provision waves (provision contributes nothing).
    assert [o.lifecycle_id for o in result.outcomes] == ["a1", "a2", "a3", "s1", "s2"]
    assert result.ok is False  # one failed


def test_result_ok_true_when_no_failures():
    """.ok is True when nothing failed and provisioning succeeded."""
    ex = RecordingExecutor({"a1": "passed", "a2": "skipped"})
    plan = make_plan(dag_planner.Wave(kind="adopt", lifecycles=["a1", "a2"]))
    result = dag_runner.run_plan(plan, ex)
    assert result.by_status() == {"passed": 1, "skipped": 1}
    assert result.ok is True


def test_ok_false_iff_failed_or_provision_error():
    """.ok is False if ANY outcome failed OR provision_error is set; True otherwise.
    Direct property check on hand-built RunResults (no runner needed)."""
    clean = RunResult(waves=[dag_runner.WaveResult(
        kind="adopt", outcomes=[LifecycleOutcome("a1", "passed")])])
    assert clean.ok is True

    failed = RunResult(waves=[dag_runner.WaveResult(
        kind="adopt", outcomes=[LifecycleOutcome("a1", "failed")])])
    assert failed.ok is False

    prov_err = RunResult(provision_error="RuntimeError: boom")
    assert prov_err.ok is False
    assert prov_err.by_status() == {}  # no outcomes recorded


# --------------------------------------------------------------------------- #
# 5. dry_run — every lifecycle 'planned', nothing invoked, shared_roots carried
# --------------------------------------------------------------------------- #
def test_dry_run_marks_all_planned_and_invokes_nothing():
    """dry_run mirrors what WOULD execute (status='planned') without provisioning
    or running anything. The provision wave executes NO lifecycles (its roots are
    provisioned, not run), so dry_run empties it — matching run_plan, so dry_run's
    planned count == what a live run would execute.
    """
    plan = make_plan(
        dag_planner.Wave(kind="provision", lifecycles=["vpc", "subnet"]),
        dag_planner.Wave(kind="adopt", lifecycles=["a1", "a2"]),
        dag_planner.Wave(kind="self-create", lifecycles=["s1"]),
        shared_roots=["vpc", "subnet"],
    )
    result = dag_runner.dry_run(plan)

    # Every outcome across every wave is 'planned'.
    assert all(o.status == "planned" for o in result.outcomes)
    # by_status counts the EXECUTABLE lifecycles only (2 adopt + 1 self-create);
    # the provision wave's roots are not lifecycles and are excluded.
    assert result.by_status() == {"planned": 3}
    # shared_roots carried through.
    assert result.shared_roots == ["vpc", "subnet"]
    # nothing failed and no provision error -> a planned run is "ok".
    assert result.ok is True


def test_dry_run_against_real_plan_is_offline():
    """Integration smoke: the REAL planner output (from dependencies.json +
    composed lifecycles) dry-runs offline with no executor/provisioner. Every leaf
    in every wave is 'planned'; shared_roots match the plan's."""
    plan = dag_planner.plan()  # pure offline computation
    result = dag_runner.dry_run(plan)
    assert all(o.status == "planned" for o in result.outcomes)
    assert result.shared_roots == plan.shared_roots
    # the flattened planned-outcome count equals the total EXECUTABLE lifecycles
    # (every non-provision wave; provision roots are stood up, not executed).
    total = sum(len(w.lifecycles) for w in plan.waves if w.kind != "provision")
    assert result.by_status().get("planned", 0) == total


# --------------------------------------------------------------------------- #
# 6. format_run — renders without error for dry_run + mixed live-ish results
# --------------------------------------------------------------------------- #
def test_format_run_renders_dry_run():
    """format_run must render a dry_run result without error and mark the planned
    lifecycles with the '→' planned marker and the provision wave label."""
    plan = make_plan(
        dag_planner.Wave(kind="provision", lifecycles=["vpc"]),
        dag_planner.Wave(kind="adopt", lifecycles=["a1"]),
        shared_roots=["vpc"],
    )
    text = dag_runner.format_run(dag_runner.dry_run(plan))
    assert "shared roots: vpc" in text
    assert "[provision]" in text
    assert "→ a1 [planned]" in text
    assert "summary: planned=" in text


def test_format_run_renders_mixed_result():
    """format_run smoke for a mixed live-ish result: the status markers for
    passed/skipped/failed appear, the failed reason is surfaced, and the summary
    line tallies the statuses."""
    statuses = {"a1": "passed", "a2": "skipped", "a3": "failed"}
    ex = RecordingExecutor(statuses)
    plan = make_plan(
        dag_planner.Wave(kind="provision", lifecycles=["vpc"]),
        dag_planner.Wave(kind="adopt", lifecycles=["a1", "a2", "a3"]),
        shared_roots=["vpc"],
    )
    result = dag_runner.run_plan(plan, ex)
    text = dag_runner.format_run(result)

    assert "shared roots: vpc" in text
    assert "[provision]" in text
    assert "[adopt]" in text
    # status markers from the format map.
    assert "· a1 [passed]" in text
    assert "○ a2 [skipped]" in text
    assert "✗ a3 [failed]" in text
    assert "summary:" in text and "failed=1" in text


def test_format_run_renders_provision_error():
    """format_run shows the provisioning-failure banner and stops (no wave lines)
    when provision_error is set."""
    result = RunResult(shared_roots=["vpc"],
                       provision_error="RuntimeError: no quota")
    text = dag_runner.format_run(result)
    assert "provisioning failed: RuntimeError: no quota" in text
    assert "summary:" not in text  # early return before the summary line


# --------------------------------------------------------------------------- #
# 7. on_event — progress callback for live observability
# --------------------------------------------------------------------------- #
def test_on_event_fires_progress_in_order():
    """run_plan streams provision/wave/lifecycle/teardown events so a live
    dashboard can render progress. Events must arrive in a sane order and the
    lifecycle_done events must cover every executed lifecycle exactly once."""
    plan = make_plan(
        dag_planner.Wave(kind="provision", lifecycles=["vpc"]),
        dag_planner.Wave(kind="adopt", lifecycles=["a1", "a2"]),
        shared_roots=["vpc"],
    )

    class _Prov:
        def provision(self): pass
        def teardown(self): pass

    events = []
    lock = threading.Lock()

    def on_event(kind, payload):
        with lock:  # lifecycle_done can fire from worker threads
            events.append((kind, payload.get("lifecycle_id") or payload.get("index")))

    dag_runner.run_plan(plan, passing_executor, provisioner=_Prov(),
                        on_event=on_event)

    kinds = [e[0] for e in events]
    assert kinds[0] == "provision_start"
    assert "provision_done" in kinds[:2]
    assert kinds[-1] == "teardown_done"
    assert kinds.index("wave_start") < kinds.index("wave_done")
    # every executed lifecycle reported exactly once
    done = sorted(e[1] for e in events if e[0] == "lifecycle_done")
    assert done == ["a1", "a2"]


def test_free_wave_runs_concurrently_with_pipeline():
    """A 'free' wave (VPC-independent leaves) must NOT block the provision→adopt→
    self-create pipeline — it runs in the background. Proof: with a free wave and a
    later adopt wave, the adopt lifecycle is dispatched BEFORE the free lifecycles
    finish (they overlap), and every lifecycle still runs exactly once."""
    plan = make_plan(
        dag_planner.Wave(kind="provision", lifecycles=["vpc"]),
        dag_planner.Wave(kind="free", lifecycles=["f1", "f2"]),
        dag_planner.Wave(kind="adopt", lifecycles=["a1"]),
        shared_roots=["vpc"],
    )

    class _Prov:
        def provision(self): pass
        def teardown(self): pass

    events = []
    lock = threading.Lock()

    def ex(lid):
        with lock:
            events.append(("start", lid))
        time.sleep(0.25)
        with lock:
            events.append(("end", lid))
        return LifecycleOutcome(lid, "passed")

    result = dag_runner.run_plan(plan, ex, provisioner=_Prov(), max_workers=4)

    # adopt 'a1' starts while free f1/f2 are still in flight (not strictly after them)
    f_ends = [i for i, (k, lid) in enumerate(events) if k == "end" and lid in ("f1", "f2")]
    a1_start_evt = [i for i, (k, lid) in enumerate(events) if k == "start" and lid == "a1"][0]
    assert a1_start_evt < max(f_ends), "adopt waited for the free wave — not concurrent"
    assert result.ok is True
    assert sorted(o.lifecycle_id for o in result.outcomes) == ["a1", "f1", "f2"]


def test_on_event_failure_does_not_break_run():
    """A throwing on_event callback must never sink the run (observability is
    best-effort)."""
    plan = make_plan(dag_planner.Wave(kind="adopt", lifecycles=["a1"]))

    def boom(kind, payload):
        raise ValueError("dashboard exploded")

    result = dag_runner.run_plan(plan, passing_executor, on_event=boom)
    assert result.ok is True
    assert [o.status for o in result.outcomes] == ["passed"]


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(pytest.main([__file__, "-q", "-o", "addopts="]))
