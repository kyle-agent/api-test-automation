"""Offline regression test for the engine-level literal-token poll guard.

Class root-caused in run-2 (73243c6e, gen-heavy-aimlops): an exploratory
create that TOLERATES 4xx leaves its ``capture_soft`` token unresolved, and
the following state-poll then re-GETs the literal ``{release_id}`` path for
its FULL timeout (observed: 10 attempts / ~30min against a 400). A literal
token can never converge, so ``_run_step`` now skips the poll loop entirely
(first response returned as-is) whenever the polled path or query params
still carry an unresolved ``{token}`` — the class-wide fix the per-lifecycle
``give_up_status`` ladders only patched point-wise.
"""
from __future__ import annotations

import pytest

from regression.scenarios import engine
from tests.offline.test_command_channel import FakeClient, _cfg, _r


def _lc(step_extra: dict) -> dict:
    step = {
        "name": "wait-on-literal", "method": "GET",
        "expect_status": [200, 400, 404], "optional": True,
        "poll": {"field": "$.state", "until": ["ACTIVE"],
                 "timeout": 1800, "interval": 30},
    }
    step.update(step_extra)
    return {"id": "literal-poll-guard-test", "service": "vpc",
            "enabled": True, "steps": [step]}


def test_unresolved_path_token_skips_poll(monkeypatch):
    monkeypatch.setattr(engine, "_commands", None)
    monkeypatch.setattr(engine.time, "sleep",
                        lambda s: pytest.fail(f"poll slept ({s}s) on a literal-token path"))
    lc = _lc({"path": "/v1/aimlops-platform/{release_id}"})  # capture miss upstream
    client = FakeClient({("GET", "/v1/aimlops-platform/"): _r(400, {"errors": []})})
    res = engine.run_lifecycle(lc, client, _cfg())
    assert res["status"] == "passed", res


def test_unresolved_param_token_skips_poll(monkeypatch):
    monkeypatch.setattr(engine, "_commands", None)
    monkeypatch.setattr(engine.time, "sleep",
                        lambda s: pytest.fail(f"poll slept ({s}s) on literal-token params"))
    lc = _lc({"path": "/v1/snapshots/abc123",
              "params": {"volume_id": "{volume_id}"}})  # unresolved query token
    client = FakeClient({("GET", "/v1/snapshots/"): _r(404, {"errors": []})})
    res = engine.run_lifecycle(lc, client, _cfg())
    assert res["status"] == "passed", res


def test_resolved_path_still_polls(monkeypatch):
    """The guard must not touch healthy polls — resolved paths keep looping."""
    monkeypatch.setattr(engine, "_commands", None)
    slept = []
    monkeypatch.setattr(engine.time, "sleep", lambda s: slept.append(s))
    fake_now = [0.0]
    monkeypatch.setattr(
        engine.time, "monotonic",
        lambda: fake_now.__setitem__(0, fake_now[0] + 5) or fake_now[0])
    lc = _lc({"path": "/v1/clusters/abc123",
              "poll": {"field": "$.state", "until": ["ACTIVE"],
                       "timeout": 30, "interval": 1}})
    client = FakeClient({("GET", "/v1/clusters/"): _r(404, {"errors": []})})
    res = engine.run_lifecycle(lc, client, _cfg())
    assert res["status"] == "passed", res
    assert slept, "a resolved-path poll should still loop until timeout"
