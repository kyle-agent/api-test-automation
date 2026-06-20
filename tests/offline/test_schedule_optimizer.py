"""Offline (hermetic) tests for regression/scenarios/schedule_optimizer.

No network, no markers, no real duration-store touches: every test that writes a
duration store uses a tmp_path so data/optimizer/durations.json is never touched.
"""
from __future__ import annotations

import math

import pytest

from regression.scenarios import schedule_optimizer as so


# --------------------------------------------------------------------------- #
# helpers
# --------------------------------------------------------------------------- #
def make_requires(deps: dict):
    """Build a requires(node) callable from a {node: [deps...]} mapping."""
    return lambda n: deps.get(n, [])


def make_holds(slot_nodes):
    s = set(slot_nodes)
    return lambda n: n in s


# --------------------------------------------------------------------------- #
# 1. duration store: rolling average
# --------------------------------------------------------------------------- #
def test_update_durations_rolling_average(tmp_path):
    p = tmp_path / "durations.json"
    store = so.update_durations({"a": 10}, path=p)
    assert store["a"] == {"avg_s": 10.0, "n": 1, "last_s": 10.0}

    store = so.update_durations({"a": 20}, path=p)
    assert store["a"]["avg_s"] == 15.0
    assert store["a"]["n"] == 2
    assert store["a"]["last_s"] == 20.0


def test_update_durations_persists_and_reloads(tmp_path):
    p = tmp_path / "durations.json"
    so.update_durations({"a": 10}, path=p)
    so.update_durations({"a": 20}, path=p)
    assert p.exists()
    reloaded = so.load_durations(p)
    assert reloaded["a"]["avg_s"] == 15.0
    assert reloaded["a"]["n"] == 2


def test_update_durations_skips_none(tmp_path):
    p = tmp_path / "durations.json"
    store = so.update_durations({"a": 10, "b": None}, path=p)
    assert "a" in store
    assert "b" not in store


def test_update_durations_multiple_nodes_independent(tmp_path):
    p = tmp_path / "durations.json"
    so.update_durations({"a": 10, "b": 4}, path=p)
    store = so.update_durations({"a": 30}, path=p)
    assert store["a"]["avg_s"] == 20.0  # (10 + 30) / 2
    assert store["a"]["n"] == 2
    assert store["b"]["avg_s"] == 4.0   # untouched
    assert store["b"]["n"] == 1


def test_load_durations_missing_path_is_empty(tmp_path):
    p = tmp_path / "does_not_exist.json"
    assert so.load_durations(p) == {}


def test_duration_of_default_for_unknown_node():
    assert so.duration_of("nope", {}, default=30.0) == 30.0
    # custom default honoured
    assert so.duration_of("nope", {}, default=7.5) == 7.5


def test_duration_of_returns_stored_avg():
    durations = {"a": {"avg_s": 12.5, "n": 3, "last_s": 9.0}}
    assert so.duration_of("a", durations, default=30.0) == 12.5


def test_duration_of_zero_avg_falls_back_to_default():
    # NOTE: a stored avg_s of 0.0 is falsy and is treated as "unmeasured".
    durations = {"a": {"avg_s": 0.0, "n": 1, "last_s": 0.0}}
    assert so.duration_of("a", durations, default=30.0) == 30.0


# --------------------------------------------------------------------------- #
# 2. tail_lengths
# --------------------------------------------------------------------------- #
def test_tail_lengths_chain():
    # chain a <- b <- c : requires(c)=[b], requires(b)=[a], requires(a)=[]
    nodes = {"a", "b", "c"}
    requires = make_requires({"c": ["b"], "b": ["a"]})
    durations = {
        "a": {"avg_s": 1.0, "n": 1, "last_s": 1.0},
        "b": {"avg_s": 2.0, "n": 1, "last_s": 2.0},
        "c": {"avg_s": 3.0, "n": 1, "last_s": 3.0},
    }
    tail = so.tail_lengths(nodes, requires, durations, default=30.0)
    # longest remaining path FROM a (incl a) is a->b->c = 1+2+3
    assert tail["a"] == 6.0
    assert tail["b"] == 5.0
    # c is a leaf (nothing requires c), tail is just its own duration
    assert tail["c"] == 3.0


def test_tail_lengths_diamond():
    # a is root; b and c require a; d requires both b and c
    #   a -> b -> d
    #   a -> c -> d
    nodes = {"a", "b", "c", "d"}
    requires = make_requires({"b": ["a"], "c": ["a"], "d": ["b", "c"]})
    durations = {
        "a": {"avg_s": 1.0, "n": 1, "last_s": 1.0},
        "b": {"avg_s": 5.0, "n": 1, "last_s": 5.0},   # longer branch
        "c": {"avg_s": 2.0, "n": 1, "last_s": 2.0},
        "d": {"avg_s": 4.0, "n": 1, "last_s": 4.0},
    }
    tail = so.tail_lengths(nodes, requires, durations, default=30.0)
    assert tail["d"] == 4.0           # leaf
    assert tail["b"] == 9.0           # b + d
    assert tail["c"] == 6.0           # c + d
    # a takes the longer of its children's tails: 1 + max(9, 6) = 10
    assert tail["a"] == 10.0


def test_tail_lengths_uses_default_for_unmeasured():
    nodes = {"x", "y"}
    requires = make_requires({"y": ["x"]})
    tail = so.tail_lengths(nodes, requires, {}, default=10.0)
    assert tail["y"] == 10.0
    assert tail["x"] == 20.0


# --------------------------------------------------------------------------- #
# 3. critical_path
# --------------------------------------------------------------------------- #
def test_critical_path_chain():
    nodes = {"a", "b", "c"}
    requires = make_requires({"c": ["b"], "b": ["a"]})
    durations = {
        "a": {"avg_s": 1.0, "n": 1, "last_s": 1.0},
        "b": {"avg_s": 2.0, "n": 1, "last_s": 2.0},
        "c": {"avg_s": 3.0, "n": 1, "last_s": 3.0},
    }
    path, total = so.critical_path(nodes, requires, durations, default=30.0)
    assert path == ["a", "b", "c"]
    assert total == 6.0


def test_critical_path_picks_longest_branch():
    # root a; two branches: a->b->bb (long) and a->c (short)
    nodes = {"a", "b", "bb", "c"}
    requires = make_requires({"b": ["a"], "bb": ["b"], "c": ["a"]})
    durations = {
        "a": {"avg_s": 1.0, "n": 1, "last_s": 1.0},
        "b": {"avg_s": 5.0, "n": 1, "last_s": 5.0},
        "bb": {"avg_s": 5.0, "n": 1, "last_s": 5.0},
        "c": {"avg_s": 2.0, "n": 1, "last_s": 2.0},
    }
    path, total = so.critical_path(nodes, requires, durations, default=30.0)
    assert path == ["a", "b", "bb"]
    assert total == 11.0  # 1 + 5 + 5


def test_critical_path_empty():
    requires = make_requires({})
    assert so.critical_path(set(), requires, {}, default=30.0) == ([], 0.0)


# --------------------------------------------------------------------------- #
# 4. schedule cap-feasibility
# --------------------------------------------------------------------------- #
def test_schedule_cap_limits_concurrency():
    # N independent slot-holding nodes, equal durations, budget = cap - shared = 4
    n = 9
    dur = 10.0
    nodes = {f"v{i}" for i in range(n)}
    requires = make_requires({})
    durations = {name: {"avg_s": dur, "n": 1, "last_s": dur} for name in nodes}
    holds = make_holds(nodes)

    sched = so.schedule(
        nodes, requires, durations,
        vpc_cap=5, shared_vpc=1, holds_slot=holds, default=30.0,
    )
    # at most 4 run concurrently -> ceil(9/4) waves * 10s = 3 * 10 = 30
    expected = math.ceil(n / 4) * dur
    assert sched.makespan_s == expected
    assert sorted(sched.slot_consumers) == sorted(nodes)
    assert len(sched.order) == n


def test_schedule_no_slots_means_full_parallel():
    n = 9
    dur = 10.0
    nodes = {f"v{i}" for i in range(n)}
    requires = make_requires({})
    durations = {name: {"avg_s": dur, "n": 1, "last_s": dur} for name in nodes}

    sched = so.schedule(
        nodes, requires, durations,
        vpc_cap=5, shared_vpc=1, holds_slot=make_holds(set()), default=30.0,
    )
    # nothing holds a slot -> all parallel -> one duration
    assert sched.makespan_s == dur
    assert sched.slot_consumers == []


def test_schedule_budget_one_serialises():
    # budget = max(1, 2 - 1) = 1 -> slot holders run one at a time
    n = 3
    dur = 5.0
    nodes = {f"v{i}" for i in range(n)}
    requires = make_requires({})
    durations = {name: {"avg_s": dur, "n": 1, "last_s": dur} for name in nodes}

    sched = so.schedule(
        nodes, requires, durations,
        vpc_cap=2, shared_vpc=1, holds_slot=make_holds(nodes), default=30.0,
    )
    assert sched.makespan_s == n * dur  # fully serial


def test_schedule_cap_le_shared_clamps_budget_to_one():
    # NOTE: vpc_cap <= shared_vpc would give a non-positive budget, but the code
    # clamps with max(1, ...) so at least one slot holder still runs.  This keeps
    # the schedule feasible rather than deadlocking.
    n = 3
    dur = 5.0
    nodes = {f"v{i}" for i in range(n)}
    requires = make_requires({})
    durations = {name: {"avg_s": dur, "n": 1, "last_s": dur} for name in nodes}

    sched = so.schedule(
        nodes, requires, durations,
        vpc_cap=1, shared_vpc=5, holds_slot=make_holds(nodes), default=30.0,
    )
    # budget clamped to 1 -> serialised, no deadlock
    assert sched.makespan_s == n * dur
    assert len(sched.order) == n


# --------------------------------------------------------------------------- #
# 5. schedule priority (longest-tail first)
# --------------------------------------------------------------------------- #
def test_schedule_dispatches_longest_tail_first():
    # three independent ready nodes with distinct durations; LPT => longest first
    nodes = {"short", "mid", "long"}
    requires = make_requires({})
    durations = {
        "short": {"avg_s": 1.0, "n": 1, "last_s": 1.0},
        "mid": {"avg_s": 5.0, "n": 1, "last_s": 5.0},
        "long": {"avg_s": 9.0, "n": 1, "last_s": 9.0},
    }
    sched = so.schedule(
        nodes, requires, durations,
        vpc_cap=5, shared_vpc=1, holds_slot=make_holds(set()), default=30.0,
    )
    assert sched.order[0] == "long"


def test_schedule_long_nonslot_and_short_slot_nodes():
    # one long non-slot node + several short slot-bounded nodes.  The long node
    # has the biggest tail so it dispatches first; everything still completes and
    # makespan is at least the longest single node.
    nodes = {"big"} | {f"s{i}" for i in range(6)}
    requires = make_requires({})
    durations = {"big": {"avg_s": 100.0, "n": 1, "last_s": 100.0}}
    for i in range(6):
        durations[f"s{i}"] = {"avg_s": 5.0, "n": 1, "last_s": 5.0}
    holds = make_holds({f"s{i}" for i in range(6)})

    sched = so.schedule(
        nodes, requires, durations,
        vpc_cap=5, shared_vpc=1, holds_slot=holds, default=30.0,
    )
    assert sched.order[0] == "big"
    # 6 slot nodes at budget 4 => 2 waves * 5 = 10s; big runs in parallel = 100s
    assert sched.makespan_s == 100.0
    assert sorted(sched.slot_consumers) == [f"s{i}" for i in range(6)]


# --------------------------------------------------------------------------- #
# 6. schedule respects dependencies
# --------------------------------------------------------------------------- #
def test_schedule_respects_dependency_order():
    nodes = {"a", "b", "c"}
    requires = make_requires({"c": ["b"], "b": ["a"]})
    durations = {
        "a": {"avg_s": 2.0, "n": 1, "last_s": 2.0},
        "b": {"avg_s": 3.0, "n": 1, "last_s": 3.0},
        "c": {"avg_s": 4.0, "n": 1, "last_s": 4.0},
    }
    sched = so.schedule(
        nodes, requires, durations,
        vpc_cap=5, shared_vpc=1, holds_slot=make_holds(set()), default=30.0,
    )
    # chain must be a topological order: a before b before c
    assert sched.order.index("a") < sched.order.index("b")
    assert sched.order.index("b") < sched.order.index("c")
    # serial chain -> makespan is the sum
    assert sched.makespan_s == 9.0
    assert sched.critical_path == ["a", "b", "c"]
    assert sched.floor_s == 9.0


def test_schedule_diamond_topological():
    nodes = {"a", "b", "c", "d"}
    requires = make_requires({"b": ["a"], "c": ["a"], "d": ["b", "c"]})
    durations = {name: {"avg_s": 3.0, "n": 1, "last_s": 3.0} for name in nodes}
    sched = so.schedule(
        nodes, requires, durations,
        vpc_cap=5, shared_vpc=1, holds_slot=make_holds(set()), default=30.0,
    )
    idx = {name: i for i, name in enumerate(sched.order)}
    assert idx["a"] < idx["b"]
    assert idx["a"] < idx["c"]
    assert idx["b"] < idx["d"]
    assert idx["c"] < idx["d"]
    # a (3) then b||c (3) then d (3) = 9
    assert sched.makespan_s == 9.0


def test_schedule_empty_graph():
    sched = so.schedule(
        set(), make_requires({}), {},
        vpc_cap=5, shared_vpc=1, holds_slot=make_holds(set()), default=30.0,
    )
    assert sched.order == []
    assert sched.makespan_s == 0.0
    assert sched.critical_path == []
    assert sched.floor_s == 0.0


def test_schedule_to_dict_shape():
    nodes = {"a", "b"}
    requires = make_requires({"b": ["a"]})
    durations = {
        "a": {"avg_s": 2.0, "n": 1, "last_s": 2.0},
        "b": {"avg_s": 3.0, "n": 1, "last_s": 3.0},
    }
    sched = so.schedule(nodes, requires, durations, holds_slot=make_holds(set()))
    d = sched.to_dict()
    assert set(d) == {"order", "makespan_s", "floor_s", "critical_path", "slot_consumers"}
    assert d["makespan_s"] == 5.0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(pytest.main([__file__, "-q"]))
