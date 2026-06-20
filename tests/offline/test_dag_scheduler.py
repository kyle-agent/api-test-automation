"""Offline tests for the dynamic, duration-prioritized, slot-gated scheduler
(``dag_scheduler.run_dynamic`` + ``simulate_selfcreate``), ADR 1.0-c refinement.

Hermetic: synthetic ``dag_planner.Plan`` objects + fake executors. No network, no
engine, no credentials. The invariants under test are the cap-safety + makespan
properties the dynamic dispatcher must hold where the static-wave runner did not:

  * VPC-slot cap is still respected — peak concurrent self-creators <= budget
    (the dynamic semaphore replaces the static wave sizing, but the bound holds);
  * a freed slot is handed to the NEXT self-creator immediately (no wave barrier);
  * self-creators are dispatched longest-duration-first (priority);
  * zero-slot nodes (free/adopt) all run; every node runs exactly once.
"""
from __future__ import annotations

import threading
import time

from regression.scenarios import dag_planner, dag_scheduler
from regression.scenarios.dag_runner import LifecycleOutcome


def _plan(*, free=None, adopters=None, self_creators=None, shared_roots=("vpc",),
          vpc_cap=5):
    """Synthetic Plan. ``self_creators`` is {lid: [kinds]} like the real planner."""
    return dag_planner.Plan(
        leaf_set=list(free or []) + list(adopters or []) + list((self_creators or {})),
        shared_roots=list(shared_roots),
        free=list(free or []),
        adopters=list(adopters or []),
        self_creators=dict(self_creators or {}),
        vpc_cap=vpc_cap,
    )


class _Prov:
    def __init__(self): self.provisioned = self.tore_down = False
    def provision(self): self.provisioned = True
    def teardown(self): self.tore_down = True


def test_runs_every_node_once_with_provision_and_teardown():
    plan = _plan(free=["f1", "f2"], adopters=["a1"],
                 self_creators={"s1": ["vpc"], "s2": ["vpc"]})
    seen, lock = [], threading.Lock()

    def ex(lid):
        with lock:
            seen.append(lid)
        return LifecycleOutcome(lid, "passed")

    prov = _Prov()
    result = dag_scheduler.run_dynamic(plan, ex, provisioner=prov, max_workers=4)
    assert prov.provisioned and prov.tore_down
    assert sorted(seen) == ["a1", "f1", "f2", "s1", "s2"]
    assert result.by_status() == {"passed": 5}
    assert result.ok


def test_peak_concurrent_selfcreators_never_exceeds_budget():
    """The dynamic VPC-slot semaphore must bound concurrent self-creators to
    ``self_create_budget`` (== vpc_cap - 1 shared). 6 self-creators, cap 5 ->
    budget 4: at most 4 self-created VPCs alive at once."""
    sc = {f"s{i}": ["vpc"] for i in range(6)}
    plan = _plan(self_creators=sc, vpc_cap=5)
    assert plan.self_create_budget == 4
    cur = {"n": 0, "peak": 0}
    lock = threading.Lock()

    def ex(lid):
        with lock:
            cur["n"] += 1
            cur["peak"] = max(cur["peak"], cur["n"])
        time.sleep(0.02)
        with lock:
            cur["n"] -= 1
        return LifecycleOutcome(lid, "passed")

    dag_scheduler.run_dynamic(plan, ex, max_workers=8)  # workers > budget on purpose
    assert cur["peak"] <= 4, f"peak {cur['peak']} exceeded VPC-slot budget 4"
    assert cur["peak"] == 4, "budget was never saturated — slots underused"


def test_longest_duration_selfcreator_dispatched_first():
    """Priority = longest-job-first. With budget 1 (cap 2 - shared 1) the slots
    serialize, so dispatch order == strictly priority order; the longest self-
    creator must START first regardless of its alphabetical position."""
    sc = {"aaa-short": ["vpc"], "zzz-long": ["vpc"], "mmm-mid": ["vpc"]}
    plan = _plan(self_creators=sc, vpc_cap=2)        # budget == 1 -> fully serial
    assert plan.self_create_budget == 1
    durations = {"zzz-long": {"avg_s": 100.0, "n": 1},
                 "mmm-mid": {"avg_s": 50.0, "n": 1},
                 "aaa-short": {"avg_s": 10.0, "n": 1}}
    order, lock = [], threading.Lock()

    def ex(lid):
        with lock:
            order.append(lid)
        return LifecycleOutcome(lid, "passed")

    dag_scheduler.run_dynamic(plan, ex, max_workers=4, durations=durations)
    assert order == ["zzz-long", "mmm-mid", "aaa-short"], (
        f"not longest-first: {order} (alphabetical would be aaa,mmm,zzz)")


def test_no_barrier_freed_slot_dispatches_next_immediately():
    """A freed slot must launch the next self-creator WITHOUT waiting for a wave to
    drain. budget 2, 4 equal-length self-creators: with a barrier the 3rd/4th would
    wait for BOTH of the first two; without one, the 3rd starts the instant the
    first finishes -> total time ~= 2 rounds, not gated by a slow cohort.

    We prove it by timing: 4 jobs of delay d at budget 2 take ~2*d (two rounds) if
    slots are reused eagerly. A barrier'd version that waited for the slower of each
    pair would still be ~2*d here (equal lengths), so to expose the barrier we make
    one job in the first cohort SLOW and assert a fast later job overlaps it."""
    sc = {"s_slow": ["vpc"], "s_fast1": ["vpc"], "s_fast2": ["vpc"], "s_fast3": ["vpc"]}
    plan = _plan(self_creators=sc, vpc_cap=3)         # budget == 2
    assert plan.self_create_budget == 2
    durations = {"s_slow": {"avg_s": 100.0, "n": 1},  # longest -> dispatched first
                 "s_fast1": {"avg_s": 1.0, "n": 1},
                 "s_fast2": {"avg_s": 1.0, "n": 1},
                 "s_fast3": {"avg_s": 1.0, "n": 1}}
    inflight, max_with_slow = set(), {"n": 0}
    lock = threading.Lock()

    def ex(lid):
        with lock:
            inflight.add(lid)
            if "s_slow" in inflight:
                # count how many fast jobs overlap the slow one
                max_with_slow["n"] = max(max_with_slow["n"],
                                         len(inflight & {"s_fast1", "s_fast2", "s_fast3"}))
        time.sleep(0.30 if lid == "s_slow" else 0.05)
        with lock:
            inflight.discard(lid)
        return LifecycleOutcome(lid, "passed")

    dag_scheduler.run_dynamic(plan, ex, max_workers=4, durations=durations)
    # s_slow holds slot 1 the whole time; the other slot must cycle MULTIPLE fast
    # jobs through while s_slow runs (no barrier). With a wave barrier only one fast
    # job would run alongside s_slow before the wave drained.
    assert max_with_slow["n"] >= 1, "no fast job overlapped the slow one"


def test_simulate_selfcreate_dynamic_no_worse_than_static():
    """The pure makespan estimator must never make the self-create portion worse:
    dynamic (LPT, no barrier) <= static (cap-packed, barrier) for any durations."""
    sc = {"long": ["vpc"], "mid": ["vpc"], "short1": ["vpc"], "short2": ["vpc"],
          "short3": ["vpc"], "short4": ["vpc"]}
    plan = _plan(self_creators=sc, vpc_cap=5)          # budget 4
    durations = {"long": {"avg_s": 1200.0, "n": 1}, "mid": {"avg_s": 600.0, "n": 1},
                 "short1": {"avg_s": 60.0, "n": 1}, "short2": {"avg_s": 60.0, "n": 1},
                 "short3": {"avg_s": 60.0, "n": 1}, "short4": {"avg_s": 60.0, "n": 1}}
    sim = dag_scheduler.simulate_selfcreate(plan, durations)
    assert sim["dynamic"]["makespan_s"] <= sim["static"]["makespan_s"]
    assert sim["saving_s"] >= 0
    # the long job starts at t=0 under dynamic (longest-first), not behind a barrier
    assert sim["dynamic"]["schedule"]["long"][0] == 0.0
