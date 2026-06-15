"""Offline regression test for the scalar-status TypeError self-repair.

Locks the run-27540589368 fix: a malformed lifecycle/composed fragment that
carries a SCALAR status-set (e.g. ``poll.until_status: 404`` instead of
``[404]`` — as gen-wave5-scf-triggers' wait-function-gone did) used to crash
the whole lifecycle with ``TypeError: argument of type 'int' is not iterable``
on ``resp.status in until_status``. The engine now coerces every status-set
config value to a list before any membership test (``engine._as_status_list``).
"""
from __future__ import annotations

from regression.scenarios import engine


def test_as_status_list_coerces_scalar():
    # the exact malformed shape that crashed gen-wave5-scf-triggers
    assert engine._as_status_list(404) == [404]
    # a membership test against the coerced value must NOT raise
    assert 404 in engine._as_status_list(404)
    assert 200 not in engine._as_status_list(404)


def test_as_status_list_passthrough_and_none():
    assert engine._as_status_list([200, 202]) == [200, 202]
    assert engine._as_status_list((200, 404)) == [200, 404]
    assert engine._as_status_list(None) == []


def test_scalar_status_does_not_raise_typeerror():
    # before the fix: `status in 404` -> TypeError; after: a clean bool
    for v in (404, [404], None):
        coerced = engine._as_status_list(v)
        # never raises, always iterable
        _ = 404 in coerced
