"""Offline tests for the cross-process quota semaphore (scheduler v0.5).

The semaphore is the enabling primitive from
docs/decisions/2026-06-19-dependency-dag-test-scheduler.md §0.5: it lets the
VPC-self-creating lifecycles run throttled inside ONE parallel pool (≤ cap)
instead of as a separate serial job. The contract it MUST honour:

  * single-process counting logic respects ``limit`` (the easy case);
  * a slot reserved in ONE process is visible to ANOTHER process through the
    file — this is the whole point, the thing the in-process :class:`Budget`
    cannot do and why the serial job exists today;
  * a blocking ``acquire`` parked at the cap wakes and succeeds once a holder
    in a different process releases (the throttle, not a skip);
  * a holder whose process has died is reclaimed, so a crashed xdist worker
    can never wedge the pool.

No network, no engine — pure filesystem + processes, runs in the offline tier.
"""
from __future__ import annotations

import multiprocessing as mp
import time

import pytest

from core.budgets import CrossProcessSemaphore


# fork: children re-read the on-disk state (proves CROSS-process, not shared
# memory) while inheriting the tmp dir path. Skip where fork is unavailable.
_ctx = mp.get_context("fork") if "fork" in mp.get_all_start_methods() else None
pytestmark = pytest.mark.skipif(_ctx is None, reason="fork start-method required")


def _sem(tmp_path, kind="vpc"):
    return CrossProcessSemaphore(kind, dir=tmp_path)


def test_single_process_counting_respects_limit(tmp_path):
    sem = _sem(tmp_path)
    t1 = sem.try_acquire(limit=2)
    t2 = sem.try_acquire(limit=2)
    assert t1 and t2 and t1 != t2
    assert sem.used() == 2
    assert sem.try_acquire(limit=2) is None      # cap full -> no token

    sem.release(t1)
    assert sem.used() == 1
    t3 = sem.try_acquire(limit=2)                 # a slot freed
    assert t3 is not None


def test_release_is_idempotent(tmp_path):
    sem = _sem(tmp_path)
    tok = sem.try_acquire(limit=1)
    sem.release(tok)
    sem.release(tok)                              # double release: no-op
    sem.release("never-issued")                   # unknown token: no-op
    assert sem.used() == 0
    assert sem.try_acquire(limit=1) is not None


def test_n_greater_than_one_reserves_block(tmp_path):
    sem = _sem(tmp_path)
    tok = sem.try_acquire(limit=3, n=2)           # e.g. a peering lifecycle
    assert tok is not None
    assert sem.used() == 2
    assert sem.try_acquire(limit=3, n=2) is None  # only 1 slot left
    assert sem.try_acquire(limit=3, n=1) is not None


# -- cross-process visibility (the reason this primitive exists) ------------

def _child_try_acquire(sem_dir, kind, limit, out):
    sem = CrossProcessSemaphore(kind, dir=sem_dir)
    out.put(sem.try_acquire(limit=limit))


def test_reservation_is_visible_in_another_process(tmp_path):
    sem = _sem(tmp_path)
    parent_tokens = [sem.try_acquire(limit=2), sem.try_acquire(limit=2)]
    assert all(parent_tokens)

    # A DIFFERENT process sees the cap is full through the file alone.
    out = _ctx.Queue()
    p = _ctx.Process(target=_child_try_acquire,
                     args=(tmp_path, "vpc", 2, out))
    p.start(); p.join(timeout=10)
    assert out.get(timeout=5) is None             # child blocked by parent's slots

    sem.release(parent_tokens[0])                 # free one in the parent
    out2 = _ctx.Queue()
    p2 = _ctx.Process(target=_child_try_acquire,
                      args=(tmp_path, "vpc", 2, out2))
    p2.start(); p2.join(timeout=10)
    assert out2.get(timeout=5) is not None        # child now gets the freed slot


def _child_blocking_acquire(sem_dir, kind, limit, out):
    sem = CrossProcessSemaphore(kind, dir=sem_dir)
    t0 = time.monotonic()
    tok = sem.acquire(limit=limit, timeout=10.0, poll=0.05)
    out.put((tok is not None, time.monotonic() - t0))


def test_blocking_acquire_wakes_when_holder_releases(tmp_path):
    sem = _sem(tmp_path)
    held = sem.try_acquire(limit=1)               # cap fully held by the parent
    assert held is not None

    out = _ctx.Queue()
    p = _ctx.Process(target=_child_blocking_acquire,
                     args=(tmp_path, "vpc", 1, out))
    p.start()
    time.sleep(0.5)                               # child is parked at the cap
    sem.release(held)                             # ...now free it
    p.join(timeout=10)

    got_slot, waited = out.get(timeout=5)
    assert got_slot is True                       # it woke and acquired
    assert waited >= 0.4                          # it genuinely waited for us


def test_blocking_acquire_times_out_when_never_freed(tmp_path):
    sem = _sem(tmp_path)
    assert sem.try_acquire(limit=1) is not None   # held for the whole test
    t0 = time.monotonic()
    assert sem.acquire(limit=1, timeout=0.4, poll=0.05) is None
    assert time.monotonic() - t0 >= 0.4           # waited the full timeout


# -- crash recovery ----------------------------------------------------------

def _child_acquire_then_exit_hard(sem_dir, kind):
    # Reserve a slot, then die WITHOUT releasing (os._exit skips finalizers) —
    # exactly an xdist worker crashing mid-lifecycle.
    import os
    sem = CrossProcessSemaphore(kind, dir=sem_dir)
    sem.try_acquire(limit=1)
    os._exit(0)


def test_dead_holder_is_reclaimed(tmp_path):
    p = _ctx.Process(target=_child_acquire_then_exit_hard,
                     args=(tmp_path, "vpc"))
    p.start(); p.join(timeout=10)
    assert p.exitcode == 0                         # it grabbed the only slot, then died

    sem = _sem(tmp_path)
    # PID is gone -> the holder is pruned on next access, slot reclaimed.
    assert sem.used() == 0
    assert sem.try_acquire(limit=1) is not None
