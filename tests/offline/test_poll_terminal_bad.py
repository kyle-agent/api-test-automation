"""Engine terminal-bad poll convention (owner 2026-07-09 — VM ERROR field case).

A settle-poll whose ``until`` never matches because the resource pinned at
ERROR/FAILED used to burn its FULL timeout and then silently PASS (HTTP 200 in
``expect_status`` — the masked-defect class). The engine now (1) ends the poll
on the first ERROR/FAILED sighting, (2) classifies the step as FAILED with an
explicit reason, and (3) leaves deliberate waits alone: values named in
``until`` and refire polls. A ``terminal_bad: []`` override disables only the
*early* exit (the poll keeps waiting to timeout); a poll that never reaches its
``until`` still fails via the not-ready gate — abandoning the wait doesn't make
the resource ready (masked-defect fix, owner 2026-07-13).
"""
from __future__ import annotations

import pytest

from regression.scenarios import engine
from tests.offline.test_command_channel import FakeClient, _cfg, _r


def _lc(**step_extra):
    step = {
        "name": "wait-server-active", "method": "GET",
        "path": "/v1/servers/abc123", "expect_status": [200],
        "poll": {"field": "$.status", "until": ["ACTIVE"],
                 "timeout": 1200, "interval": 30},
    }
    step.update(step_extra)
    return {"id": "terminal-bad-test", "service": "virtualserver",
            "enabled": True, "steps": [step]}


def test_error_state_ends_poll_and_fails_step(monkeypatch):
    monkeypatch.setattr(engine, "_commands", None)
    monkeypatch.setattr(engine.time, "sleep",
                        lambda s: pytest.fail(f"poll slept ({s}s) past a terminal-bad state"))
    client = FakeClient({("GET", "/v1/servers/"): _r(200, {"status": "ERROR"})})
    # run_lifecycle re-raises genuine failures for pytest entrypoints (_finish)
    with pytest.raises(AssertionError, match="TERMINAL-BAD"):
        engine.run_lifecycle(_lc(), client, _cfg())


def test_optional_group_step_skips_group_instead(monkeypatch):
    monkeypatch.setattr(engine, "_commands", None)
    monkeypatch.setattr(engine.time, "sleep", lambda s: None)
    client = FakeClient({("GET", "/v1/servers/"): _r(200, {"status": "ERROR"})})
    res = engine.run_lifecycle(_lc(optional=True, group="vm"), client, _cfg())
    assert res["status"] == "passed", res
    assert "vm" in (res.get("failed_groups") or [])


def test_until_naming_the_state_is_a_deliberate_wait(monkeypatch):
    """ERROR listed in `until` = the lifecycle WANTS to observe it — no marker."""
    monkeypatch.setattr(engine, "_commands", None)
    monkeypatch.setattr(engine.time, "sleep", lambda s: None)
    client = FakeClient({("GET", "/v1/servers/"): _r(200, {"status": "ERROR"})})
    lc = _lc(poll={"field": "$.status", "until": ["ACTIVE", "ERROR"],
                   "timeout": 60, "interval": 5})
    res = engine.run_lifecycle(lc, client, _cfg())
    assert res["status"] == "passed", res


def test_terminal_bad_override_empty_disables_early_exit_but_still_fails(monkeypatch):
    """``terminal_bad: []`` disables the EARLY terminal-bad exit — the poll keeps
    waiting to timeout instead of ending on the first ERROR sighting. But the
    not-ready gate still fails the step: ``until`` (ACTIVE) was never reached, so
    the resource never converged. The override silences the early-exit reason,
    not the fact that the wait failed (masked-defect fix, owner 2026-07-13)."""
    monkeypatch.setattr(engine, "_commands", None)
    slept = []
    monkeypatch.setattr(engine.time, "sleep", lambda s: slept.append(s))
    fake_now = [0.0]
    monkeypatch.setattr(
        engine.time, "monotonic",
        lambda: fake_now.__setitem__(0, fake_now[0] + 5) or fake_now[0])
    client = FakeClient({("GET", "/v1/servers/"): _r(200, {"status": "ERROR"})})
    lc = _lc(poll={"field": "$.status", "until": ["ACTIVE"],
                   "timeout": 30, "interval": 1, "terminal_bad": []})
    with pytest.raises(AssertionError, match="poll timed out"):
        engine.run_lifecycle(lc, client, _cfg())
    assert slept, "opt-out poll should keep waiting to timeout (no early exit)"
