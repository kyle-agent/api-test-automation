"""Offline tests for the live_watch BILLABLE_SURVIVOR grace filter (P2C-13).

The watcher's HEAVY_START marker is local-orchestrator-only, so during a
GitHub-runner-driven heavy run heavy=False and the run's own shared VPC
(regrvpcsh<epoch-hex>) used to be false-flagged as a survivor. The fix decodes
the creation epoch from the NAME and withholds the verdict while the VPC is
younger than SHARED_VPC_GRACE_MIN — while anything old, non-shared, or with an
undecodable name stays flagged. No network, no state files — pure functions.
"""
from __future__ import annotations

import time

from tools.live_watch import SHARED_VPC_GRACE_MIN, _survivor_vpcs, _vpc_age_min


def _shared_name(age_min: float, now: float) -> str:
    return "regrvpcsh" + format(int(now - age_min * 60), "x")


def test_fresh_shared_vpc_is_in_grace():
    """A shared VPC created 30m ago (runner run in flight) is NOT a survivor."""
    now = time.time()
    vpcs = [{"id": "v1", "name": _shared_name(30, now)}]
    assert _survivor_vpcs(vpcs, now) == []


def test_old_shared_vpc_is_still_flagged():
    """A shared VPC past the grace window (true orphan) IS flagged."""
    now = time.time()
    old = {"id": "v1", "name": _shared_name(SHARED_VPC_GRACE_MIN + 60, now)}
    assert _survivor_vpcs([old], now) == [old]


def test_boundary_is_conservative():
    """Exactly at the grace threshold counts as old (>= flags)."""
    now = time.time()
    at = {"id": "v1", "name": _shared_name(SHARED_VPC_GRACE_MIN, now)}
    assert _survivor_vpcs([at], now) == [at]


def test_non_shared_owned_vpcs_keep_flagging():
    """Names without a decodable epoch (per-lifecycle regr*/zznet* VPCs,
    missing name) are treated as OLD — the grace never widens the blind spot."""
    now = time.time()
    vpcs = [{"id": "v1", "name": "regrvpc12345678"},   # not the shared stem
            {"id": "v2", "name": "zznetvpc"},
            {"id": "v3"}]                              # no name at all
    assert _survivor_vpcs(vpcs, now) == vpcs


def test_garbage_or_future_epoch_treated_as_old():
    """A hex suffix outside the sane epoch range must not grant grace."""
    now = time.time()
    assert _vpc_age_min("regrvpcsh0000abc", now) >= SHARED_VPC_GRACE_MIN
    future = "regrvpcsh" + format(int(now) + 7200, "x")   # 2h in the future
    assert _vpc_age_min(future, now) >= SHARED_VPC_GRACE_MIN


def test_mixed_list_only_flags_the_aged():
    now = time.time()
    fresh = {"id": "f", "name": _shared_name(10, now)}
    aged = {"id": "a", "name": _shared_name(600, now)}
    assert _survivor_vpcs([fresh, aged], now) == [aged]
