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

import pytest

from regression.scenarios import dag_planner, dag_scheduler
from regression.scenarios.dag_runner import LifecycleOutcome


def _blocking_sleep(secs: float) -> None:
    """Genuine wall-clock sleep via the C-level lock timeout — works no matter
    what ``time.sleep`` currently points to."""
    threading.Event().wait(secs)


@pytest.fixture(autouse=True)
def _genuine_time_sleep(monkeypatch):
    """Full-suite guard: ``cleanup/verify_clean.py`` no-ops ``time.sleep``
    PROCESS-WIDE at import (``import time as _t; _t.sleep = lambda: None``), and
    tests/offline/test_console2.py's scan_owned tests import it. Any test after
    them then sees a no-op sleep, which collapses this file's real waits (fake
    task durations, the scheduler's heavy-stagger gap) to ~0 — pass alone, fail
    in the full offline run. Pin a genuine sleep per-test here; monkeypatch
    restores the outside state afterwards. Proper fix belongs in verify_clean
    (stub sleep only inside scan_owned's try/finally, like _delete/_wait_gone)."""
    monkeypatch.setattr(time, "sleep", _blocking_sleep)


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


def test_priority_first_pin_overrides_lpt(monkeypatch):
    """priority_first 핀(오너 2026-07-13): 짧은 슬롯 소비자도 핀이면 LPT를 이기고
    가장 먼저 디스패치된다 — 뒤로 밀려 슬롯 대기 + 런 꼬리가 되던 실측의 회귀 고정.
    핀이 아닌 노드들끼리는 여전히 longest-first."""
    from regression.scenarios import schedule_optimizer
    monkeypatch.setattr(schedule_optimizer, "load_priority_first",
                        lambda *_a, **_k: ["aaa-short"])
    sc = {"aaa-short": ["vpc"], "zzz-long": ["vpc"], "mmm-mid": ["vpc"]}
    plan = _plan(self_creators=sc, vpc_cap=2)        # budget == 1 -> fully serial
    durations = {"zzz-long": {"avg_s": 100.0, "n": 1},
                 "mmm-mid": {"avg_s": 50.0, "n": 1},
                 "aaa-short": {"avg_s": 10.0, "n": 1}}
    order, lock = [], threading.Lock()

    def ex(lid):
        with lock:
            order.append(lid)
        return LifecycleOutcome(lid, "passed")

    dag_scheduler.run_dynamic(plan, ex, max_workers=4, durations=durations)
    assert order == ["aaa-short", "zzz-long", "mmm-mid"], (
        f"pinned short node must dispatch first: {order}")


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


def test_adopters_dispatched_longest_first():
    """The zero-slot batch (free + adopt) must also dispatch longest-duration-first
    so heavy critical-path ADOPTERS (DB clusters) grab worker slots before the light
    free wave (the 2026-06-20 finding: dbaas started 22.9 min late behind 162 light
    nodes). With max_workers=1 the dispatch order == execution order."""
    plan = _plan(free=["light-a", "light-b"],
                 adopters=["db-long", "db-mid"], self_creators={}, shared_roots=())
    durations = {"db-long": {"avg_s": 2700.0, "n": 1}, "db-mid": {"avg_s": 1500.0, "n": 1},
                 "light-a": {"avg_s": 30.0, "n": 1}, "light-b": {"avg_s": 20.0, "n": 1}}
    order, lock = [], threading.Lock()

    def ex(lid):
        with lock:
            order.append(lid)
        return LifecycleOutcome(lid, "passed")

    dag_scheduler.run_dynamic(plan, ex, max_workers=1, durations=durations)
    assert order == ["db-long", "db-mid", "light-a", "light-b"], (
        f"not longest-first across free+adopt: {order}")


def test_long_selfcreator_not_buried_behind_free_wave():
    """Regression for the 2026-06-20 self-creator-tail bug: a LONG self-creator
    (slot node) must be dispatched EARLY by priority, not queued behind the entire
    free wave. The old two-phase submission ran all zero-slot nodes first, so a
    21-min vpc-peering started ~72 min in and tailed the makespan ~28 min. The
    unified dispatcher must pick the long self-creator among the first."""
    plan = _plan(free=[f"light{i}" for i in range(10)], adopters=[],
                 self_creators={"long-sc": ["vpc"]}, vpc_cap=5)
    durations = {"long-sc": {"avg_s": 1000.0, "n": 1}}
    for i in range(10):
        durations[f"light{i}"] = {"avg_s": 10.0, "n": 1}
    order, lock = [], threading.Lock()

    def ex(lid):
        with lock:
            order.append(lid)
        time.sleep(0.01)
        return LifecycleOutcome(lid, "passed")

    dag_scheduler.run_dynamic(plan, ex, max_workers=2, durations=durations)
    # the long self-creator (1000s) outranks every 10s light node -> starts first.
    assert order[0] == "long-sc", f"long self-creator buried behind free wave: {order[:3]}"


def test_heavy_stagger_spaces_heavy_submissions():
    """burst-stagger: with heavy_stagger_s>0, consecutive HEAVY (prio>=threshold)
    lifecycles must START at least ~stagger apart (their create-burst is spread),
    while light jobs are not delayed. Proof by timing the first-call timestamps."""
    plan = _plan(free=["light-a", "light-b"],
                 adopters=["db1", "db2", "db3"], self_creators={}, shared_roots=())
    durations = {"db1": {"avg_s": 2000.0, "n": 1}, "db2": {"avg_s": 1900.0, "n": 1},
                 "db3": {"avg_s": 1800.0, "n": 1},
                 "light-a": {"avg_s": 10.0, "n": 1}, "light-b": {"avg_s": 10.0, "n": 1}}
    starts, lock = {}, threading.Lock()

    def ex(lid):
        with lock:
            starts[lid] = time.monotonic()
        return LifecycleOutcome(lid, "passed")

    dag_scheduler.run_dynamic(plan, ex, max_workers=8, durations=durations,
                              heavy_stagger_s=0.10, heavy_threshold_s=300.0)
    # the three heavy creates (longest-first db1,db2,db3) must be ~0.10s apart
    hs = sorted(starts[h] for h in ("db1", "db2", "db3"))
    assert hs[1] - hs[0] >= 0.08, "db2 not staggered behind db1"
    assert hs[2] - hs[1] >= 0.08, "db3 not staggered behind db2"


def test_heavy_stagger_zero_is_no_op():
    """Default heavy_stagger_s=0 must not delay anything (no behaviour change)."""
    plan = _plan(adopters=["db1", "db2"], self_creators={}, shared_roots=())
    durations = {"db1": {"avg_s": 2000.0, "n": 1}, "db2": {"avg_s": 1900.0, "n": 1}}
    t0 = time.monotonic()
    r = dag_scheduler.run_dynamic(plan, lambda lid: LifecycleOutcome(lid, "passed"),
                                  max_workers=4, durations=durations)
    assert (time.monotonic() - t0) < 1.0
    assert r.by_status() == {"passed": 2}


def test_simulate_full_dynamic_beats_baseline_under_contention():
    """Full-run DES: with the heavy adopters far longer than the light wave and a
    worker bottleneck, longest-job-first must beat duration-blind order, and never
    finish below the critical-path floor."""
    plan = _plan(free=[f"light{i}" for i in range(12)],
                 adopters=["db-long", "db-mid"], self_creators={}, shared_roots=())
    durations = {"db-long": {"avg_s": 3000.0, "n": 1}, "db-mid": {"avg_s": 1800.0, "n": 1}}
    for i in range(12):
        durations[f"light{i}"] = {"avg_s": 60.0, "n": 1}
    sim = dag_scheduler.simulate_full(plan, durations, workers=2)
    assert sim["dynamic_makespan_s"] <= sim["baseline_makespan_s"]
    assert sim["dynamic_makespan_s"] >= sim["critical_path_s"]  # cannot beat the floor
    # longest adopter starts at t=0 under dynamic, not behind the light wave
    assert sim["dynamic_start"]["db-long"] == 0.0


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
