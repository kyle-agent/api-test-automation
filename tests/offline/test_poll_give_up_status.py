"""Offline regression test for ``poll.give_up_status`` (run-28602725440 fix).

The subops-full settle-polls (``wait-after-<mutation>`` GETs on
``/v1/clusters/{cluster_id}``) poll ``$.service_state`` until it settles.
When the cluster CREATE was rejected, the id never resolves and the GET 404s
forever — before this option, each such poll burned its FULL timeout (epas in
run 28602725440: ~900s x ~15 waits against a nonexistent cluster).
``give_up_status`` ends the poll immediately on the listed statuses.
"""
from __future__ import annotations

import pytest

from regression.scenarios import engine
from tests.offline.test_command_channel import FakeClient, _cfg, _r


def test_give_up_status_ends_poll_without_sleeping(monkeypatch):
    monkeypatch.setattr(engine, "_commands", None)
    # a compliant poll must bail BEFORE its first sleep when the GET 404s
    monkeypatch.setattr(engine.time, "sleep",
                        lambda s: pytest.fail(f"poll slept ({s}s) despite give_up_status"))
    lc = {
        "id": "giveup-poll-test", "service": "epas", "enabled": True,
        "steps": [
            {"name": "wait-after-set-archive", "method": "GET",
             "path": "/v1/clusters/{cluster_id}",  # unresolved -> 404 forever
             "expect_status": [200, 400, 404], "optional": True,
             "poll": {"field": "$.service_state", "until": ["RUNNING"],
                      "timeout": 900, "interval": 20,
                      "give_up_status": [400, 404]}},
        ],
    }
    client = FakeClient({("GET", "/v1/clusters/"): _r(404, {"errors": []})})
    res = engine.run_lifecycle(lc, client, _cfg())
    assert res["status"] == "passed", res


def test_poll_without_give_up_still_waits(monkeypatch):
    """Existing polls (no give_up_status) keep their wait-until-timeout shape."""
    monkeypatch.setattr(engine, "_commands", None)
    slept = []
    monkeypatch.setattr(engine.time, "sleep", lambda s: slept.append(s))
    fake_now = [0.0]
    monkeypatch.setattr(engine.time, "monotonic", lambda: fake_now.__setitem__(0, fake_now[0] + 5) or fake_now[0])
    lc = {
        "id": "no-giveup-poll-test", "service": "epas", "enabled": True,
        "steps": [
            {"name": "wait-x", "method": "GET", "path": "/v1/clusters/abc",
             "expect_status": [200, 404], "optional": True,
             "poll": {"field": "$.service_state", "until": ["RUNNING"],
                      "timeout": 30, "interval": 1}},
        ],
    }
    client = FakeClient({("GET", "/v1/clusters/"): _r(404, {"errors": []})})
    res = engine.run_lifecycle(lc, client, _cfg())
    assert res["status"] == "passed", res
    assert slept, "poll should have looped (slept) at least once without give_up_status"
