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


def test_gone_poll_timeout_does_not_fail_step(monkeypatch):
    """gone-poll(until_status 404 = 자원 소멸 대기)은 teardown 정리라, 캡 안에 안
    사라져도(느린 삭제) not-ready로 실패시키지 않는다 — sweep/cleanup 백스톱
    (오너 2026-07-14: mariadb ~90분 drain > 900s 캡, wait-cluster-gone 오해). masked-
    defect 게이트는 create-side wait에만."""
    monkeypatch.setattr(engine, "_commands", None)
    monkeypatch.setattr(engine.time, "sleep", lambda s: None)
    fake_now = [0.0]
    monkeypatch.setattr(engine.time, "monotonic",
                        lambda: fake_now.__setitem__(0, fake_now[0] + 5) or fake_now[0])
    # 자원이 안 사라짐(200 계속) — gone-poll은 404를 기다리다 타임아웃
    client = FakeClient({("GET", "/v1/servers/"): _r(200, {"status": "DELETING"})})
    lc = _lc(name="wait-server-gone", expect_status=[200, 404],
             poll={"until_status": [404], "timeout": 30, "interval": 1})
    res = engine.run_lifecycle(lc, client, _cfg())
    assert res["status"] == "passed", res       # 타임아웃해도 실패 아님(200 in expect)


def test_transient_429_at_timeout_not_classified_not_ready(monkeypatch):
    """폴 타임아웃 시점 마지막 응답이 429면 not-ready로 확정하지 않는다(상태를 못
    읽은 unknown) — 종전엔 field 미충족으로 실패 사유가 'resource never converged'로
    오분류(오너 2026-07-14 heavy-net wait-subnet 429)."""
    monkeypatch.setattr(engine, "_commands", None)
    monkeypatch.setattr(engine.time, "sleep", lambda s: None)
    fake_now = [0.0]
    monkeypatch.setattr(engine.time, "monotonic",
                        lambda: fake_now.__setitem__(0, fake_now[0] + 5) or fake_now[0])
    client = FakeClient({("GET", "/v1/servers/"): _r(429, {})})   # 지속 rate-limit
    lc = _lc(expect_status=[200], poll={"field": "$.status", "until": ["ACTIVE"],
                                        "timeout": 30, "interval": 1})
    with pytest.raises(AssertionError) as ei:
        engine.run_lifecycle(lc, client, _cfg())
    # 실패는 하되(429는 expect[200] 밖) 사유가 not-ready(poll timed out)가 아님
    assert "poll timed out" not in str(ei.value) and "never converged" not in str(ei.value)


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


def test_adaptive_poll_interval_ladder(monkeypatch):
    """적응형 폴 간격 (2026-07-14): 첫 재시도 3s에서 2배씩 interval로 수렴 —
    빠른 정착이 interval 격자(예: DBaaS 20s → 22s 양자화)에 갇히지 않는다.
    interval_start=interval이면 종전 고정 간격과 동일(opt-out)."""
    monkeypatch.setattr(engine, "_commands", None)
    slept = []
    monkeypatch.setattr(engine.time, "sleep", lambda s: slept.append(s))
    fake_now = [0.0]
    monkeypatch.setattr(engine.time, "monotonic",
                        lambda: fake_now.__setitem__(0, fake_now[0] + 1) or fake_now[0])
    client = FakeClient({("GET", "/v1/servers/"): _r(200, {"status": "CREATING"})})
    lc = _lc(optional=True, group="g",
             poll={"field": "$.status", "until": ["ACTIVE"],
                   "timeout": 60, "interval": 20})
    engine.run_lifecycle(lc, client, _cfg())
    assert slept[:4] == [3.0, 6.0, 12.0, 20.0], f"ladder 3→6→12→20: {slept[:6]}"
    assert all(s == 20.0 for s in slept[4:]), "수렴 후엔 interval 고정"

    slept.clear()
    fake_now[0] = 0.0
    lc2 = _lc(optional=True, group="g",
              poll={"field": "$.status", "until": ["ACTIVE"],
                    "timeout": 60, "interval": 20, "interval_start": 20})
    engine.run_lifecycle(lc2, client, _cfg())
    assert all(s == 20.0 for s in slept), f"opt-out은 종전 고정 20s: {slept[:4]}"
