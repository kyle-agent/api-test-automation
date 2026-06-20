"""Offline tests for the AIMD AdaptiveLimiter + the http_client 503 counter."""
from __future__ import annotations

import threading

from regression.scenarios.dag_runner_live import AdaptiveLimiter


def _lim(start=8, lo=4, hi=24, errs=None):
    errs = errs if errs is not None else [0]
    return AdaptiveLimiter(start=start, lo=lo, hi=hi,
                           err_source=lambda: errs[0], interval=0), errs


def _tick(lim):
    """One acquire/release cycle — triggers the limiter's periodic _adjust."""
    lim.acquire()
    lim.release()


def test_probes_up_when_healthy():
    lim, _ = _lim(start=8)
    for _ in range(5):
        _tick(lim)
    assert lim.limit > 8           # additive increase while no new errors
    assert lim.limit <= 24         # never above the ceiling


def test_ceiling_is_respected():
    lim, _ = _lim(start=23, hi=24)
    for _ in range(10):
        _tick(lim)
    assert lim.limit == 24


def test_halves_on_errors():
    lim, errs = _lim(start=16)
    errs[0] = 5                     # a 503 burst since last check
    _tick(lim)
    assert lim.limit == 8.0        # multiplicative decrease


def test_floor_is_respected():
    lim, errs = _lim(start=8, lo=4)
    for i in range(1, 6):
        errs[0] = i * 10           # keep erroring
        _tick(lim)
    assert lim.limit == 4          # never below the floor


def test_recovers_after_errors_stop():
    lim, errs = _lim(start=16)
    errs[0] = 5
    _tick(lim)                     # -> 8
    dropped = lim.limit
    for _ in range(4):             # errors stop -> probe back up
        _tick(lim)
    assert lim.limit > dropped


def test_gates_concurrency_to_limit():
    # with limit pinned low, only `limit` acquirers proceed; the rest block
    lim, _ = _lim(start=2, lo=2, hi=2)
    lim.acquire()
    lim.acquire()                  # 2 slots taken (== limit)
    got_third = threading.Event()

    def third():
        lim.acquire()
        got_third.set()

    t = threading.Thread(target=third, daemon=True)
    t.start()
    assert not got_third.wait(0.3)  # blocked: limit reached
    lim.release()                   # free a slot
    assert got_third.wait(1.0)      # now it proceeds
    lim.release()
    lim.release()


def test_http_client_503_counter():
    from core import http_client
    before = http_client.retry_status_count()
    http_client._bump_retry_status()
    assert http_client.retry_status_count() == before + 1
