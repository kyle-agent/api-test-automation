"""Offline tests for regression/scenarios/optimizer_report.

Hermetic (no network, no markers — offline tier). ``schedule_optimizer``'s
duration store is monkeypatched to a SYNTHETIC dict so the report never depends
on the real ``data/optimizer/durations.json`` (and the numbers are
deterministic). The catalog graph and the enabled-lifecycle set are the REAL
ones, so the tests stay resilient to the catalog growing — they assert
structure (path non-empty, makespan > 0, ⊆ enabled, words present) rather than
exact counts.
"""
from __future__ import annotations

import pytest

from regression.scenarios import optimizer_report as orep
from regression.scenarios import schedule_optimizer as sopt


@pytest.fixture
def synthetic_durations(monkeypatch):
    """Patch load_durations to a synthetic store keyed by every enabled
    lifecycle id, so the report is fully driven by controlled numbers."""
    lifecycles = orep._enabled_lifecycles()
    ids = [lc["id"] for lc in lifecycles if lc.get("enabled")]

    # spread durations so the priority order and critical path are non-trivial.
    store = {}
    for i, lid in enumerate(ids):
        secs = 10.0 + (i % 7) * 20.0       # 10..130s, varied
        store[lid] = {"avg_s": secs, "n": 3, "last_s": secs}

    monkeypatch.setattr(sopt, "load_durations", lambda path=None: dict(store))
    return store, set(ids)


def test_resource_critical_path_has_positive_floor(synthetic_durations):
    path, seconds = orep.resource_critical_path()
    assert isinstance(path, list)
    assert path, "real resource graph should yield a non-empty critical path"
    assert seconds > 0, "critical-path floor must be positive"
    # the chain is a real dependency chain of resource node ids.
    assert all(isinstance(n, str) for n in path)


def test_lifecycle_schedule_is_cap_feasible(synthetic_durations):
    _store, enabled = synthetic_durations
    sched = orep.lifecycle_schedule(vpc_cap=5)
    assert sched.makespan_s > 0
    # every scheduled node is an enabled lifecycle.
    assert set(sched.order) <= enabled
    # slot consumers (VPC self-creators) are a subset of the enabled leaves.
    assert set(sched.slot_consumers) <= enabled
    # the schedule dispatches every enabled leaf exactly once.
    assert set(sched.order) == enabled


def test_lifecycle_schedule_cap_changes_makespan(synthetic_durations):
    """A tighter cap can only make the schedule no faster (more serialization)."""
    loose = orep.lifecycle_schedule(vpc_cap=5).makespan_s
    tight = orep.lifecycle_schedule(vpc_cap=2).makespan_s
    assert tight >= loose


def test_render_report_contains_key_sections(synthetic_durations):
    _store, enabled = synthetic_durations
    text = orep.render_report(vpc_cap=5)
    assert isinstance(text, str) and text.strip()
    low = text.lower()
    assert "critical path" in low
    assert "makespan" in low
    # learned-average labelling is present (the report must say it's approximate).
    assert "learned" in low or "approximate" in low
    # at least one real lifecycle id appears in the rendered report.
    assert any(lid in text for lid in enabled)


def test_build_report_numbers_are_consistent(synthetic_durations):
    r = orep.build_report(vpc_cap=5)
    assert r["optimal_makespan_s"] > 0
    assert r["current_makespan_s"] > 0
    # time_saved is current - optimal by definition.
    assert r["time_saved_s"] == pytest.approx(
        r["current_makespan_s"] - r["optimal_makespan_s"])
    assert set(r["vpc_self_creators"]) <= set(r["priority"])
