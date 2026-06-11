"""Offline tests for the M2 platform command channel (no network, no gateway).

Two layers, mirroring how the pieces meet in production:

  * :mod:`core.commands` unit tests — the HTTP layer is stubbed at
    ``_http_json`` so we can prove throttling, ack-on-consume, the sticky
    abort flag, target matching, disabled-when-unset, and that a dead
    platform yields "no commands" instead of an exception.
  * engine-level tests — a stubbed commands module is injected as
    ``engine._commands`` and a fake lifecycle runs through the REAL
    ``run_lifecycle`` step executor, proving that a platform skip/abort still
    tears down already-created resources (the non-negotiable invariant) and
    that stop_polling exits a poll wait exactly like a timeout would.
"""
from __future__ import annotations

import time
import types

import pytest

from core import commands
from core.http_client import Response
from regression.scenarios import engine


# --------------------------------------------------------------------------- #
# core.commands — HTTP layer stubbed at _http_json
# --------------------------------------------------------------------------- #
@pytest.fixture
def server(monkeypatch):
    """Enable the channel against an in-memory 'server' honouring the real
    contract: GET returns un-acked commands; POST .../ack removes one."""
    state = {"commands": [], "gets": 0, "acks": []}

    def fake_http(method, url):
        if method == "GET":
            state["gets"] += 1
            return {"commands": [dict(c) for c in state["commands"]]}
        state["acks"].append(url)
        cid = int(url.rstrip("/").split("/")[-2])
        state["commands"] = [c for c in state["commands"] if c["id"] != cid]
        return {"ok": True}

    monkeypatch.setattr(commands, "_BASE_URL", "http://platform.test")
    monkeypatch.setattr(commands, "_ENABLED", True)
    monkeypatch.setattr(commands, "_http_json", fake_http)
    monkeypatch.setattr(commands, "_last_poll", None)
    monkeypatch.setattr(commands, "_pending", [])
    monkeypatch.setattr(commands, "_consumed", set())
    monkeypatch.setattr(commands, "_abort", False)
    return state


def _force_repoll():
    commands._last_poll = time.monotonic() - commands._POLL_INTERVAL - 1


def test_disabled_when_env_unset(monkeypatch):
    monkeypatch.setattr(commands, "_ENABLED", False)
    monkeypatch.setattr(commands, "_http_json",
                        lambda *a: pytest.fail("HTTP must not be touched when disabled"))
    assert commands.check() == []
    assert commands.should_abort_run() is False
    assert commands.should_skip("any") is False
    assert commands.should_stop_polling("any") is False
    commands.ack(1)  # must be a silent no-op


def test_check_throttles_to_one_poll_per_interval(server):
    server["commands"] = [{"id": 1, "action": "skip_scenario", "target": "lc-a"}]
    first = commands.check()
    assert [c["id"] for c in first] == [1]
    assert server["gets"] == 1
    # immediate re-check serves the cache — no second HTTP poll
    assert [c["id"] for c in commands.check()] == [1]
    assert server["gets"] == 1
    # once the interval has elapsed, the next check polls again
    _force_repoll()
    commands.check()
    assert server["gets"] == 2


def test_should_skip_acks_on_consume_and_never_reapplies(server):
    server["commands"] = [{"id": 7, "action": "skip_scenario", "target": "lc-a"}]
    assert commands.should_skip("lc-b") is False     # wrong target: not consumed
    assert server["acks"] == []
    assert commands.should_skip("lc-a") is True
    assert server["acks"] == ["http://platform.test/api/commands/7/ack"]
    # even if the server re-sends it (ack lost), a consumed id is never re-applied
    server["commands"] = [{"id": 7, "action": "skip_scenario", "target": "lc-a"}]
    _force_repoll()
    assert commands.should_skip("lc-a") is False


def test_abort_is_sticky_after_ack(server):
    server["commands"] = [{"id": 3, "action": "abort_run"}]
    assert commands.should_abort_run() is True
    assert server["acks"] == ["http://platform.test/api/commands/3/ack"]
    # the acked command is gone from the server, but the flag holds per process
    _force_repoll()
    assert commands.check() == []
    assert commands.should_abort_run() is True


def test_stop_polling_target_matching(server):
    server["commands"] = [{"id": 2, "action": "stop_polling", "target": "lc-x"}]
    assert commands.should_stop_polling("lc-y") is False
    assert commands.should_stop_polling("lc-x") is True
    # an empty target means "whatever is polling right now"
    server["commands"] = [{"id": 4, "action": "stop_polling", "target": ""}]
    _force_repoll()
    assert commands.should_stop_polling("anything") is True
    assert "http://platform.test/api/commands/4/ack" in server["acks"]


def test_dead_platform_means_no_commands(server, monkeypatch):
    # _http_json returns None on any failure — predicates must stay quiet
    monkeypatch.setattr(commands, "_http_json", lambda *a: None)
    assert commands.check() == []
    assert commands.should_abort_run() is False
    assert commands.should_skip("lc-a") is False


# --------------------------------------------------------------------------- #
# engine integration — real run_lifecycle, stubbed commands module
# --------------------------------------------------------------------------- #
class FakeClient:
    """Records calls; returns canned responses by (METHOD, path-startswith)."""

    def __init__(self, routes: dict):
        self.routes = routes
        self.calls: list[tuple[str, str]] = []

    def request(self, method, path, *, json=None, service=None, params=None):
        self.calls.append((method.upper(), path))
        for (m, pfx), resp in self.routes.items():
            if method.upper() == m and path.startswith(pfx):
                return resp
        return Response(200, 1.0, {}, {}, "{}")


def _cfg(**over):
    base = dict(allow_mutations=True, allow_destructive=True, run_heavy=True,
                region="kr-west1")
    base.update(over)
    return types.SimpleNamespace(**base)


def _r(status, body):
    return Response(status, 1.0, {}, body, "{}")


@pytest.fixture(autouse=True)
def _no_disk(monkeypatch):
    # hermetic: don't write the results store / smoke TSV
    monkeypatch.setattr(engine, "_record_smoke", lambda *a, **k: None)


def _two_create_lc():
    return {
        "id": "cmd-test", "service": "vpc", "enabled": True,
        "steps": [
            {"name": "create-vpc", "method": "POST", "path": "/v1/vpcs",
             "json": {"name": "x", "cidr": "10.99.0.0/20", "tags": []},
             "capture": {"vpc_id": "$.vpc.id"}, "expect_status": [200, 201, 202],
             "cleanup": {"method": "DELETE", "path": "/v1/vpcs/{vpc_id}", "service": "vpc"}},
            {"name": "create-subnet", "method": "POST", "path": "/v1/subnets",
             "json": {"cidr": "10.99.0.0/24", "vpc_id": "{vpc_id}", "tags": []},
             "capture": {"subnet_id": "$.subnet.id"}, "expect_status": [200, 201, 202],
             "cleanup": {"method": "DELETE", "path": "/v1/subnets/{subnet_id}", "service": "vpc"}},
            {"name": "delete-subnet", "method": "DELETE", "path": "/v1/subnets/{subnet_id}",
             "expect_status": [200, 202, 204]},
            {"name": "delete-vpc", "method": "DELETE", "path": "/v1/vpcs/{vpc_id}",
             "expect_status": [200, 202, 204]},
        ],
    }


class StubCommands:
    """Drop-in for engine._commands with scripted answers + call recording."""

    def __init__(self, *, abort=False, skip_target=None, skip_at_boundary=1,
                 stop_polling=False):
        self.abort = abort
        self.skip_target = skip_target
        self.skip_at_boundary = skip_at_boundary
        self.stop = stop_polling
        self.skip_calls = 0
        self.stop_calls: list[str] = []

    def should_abort_run(self):
        return self.abort

    def should_skip(self, lifecycle_id):
        self.skip_calls += 1
        return (lifecycle_id == self.skip_target
                and self.skip_calls >= self.skip_at_boundary)

    def should_stop_polling(self, lifecycle_id=""):
        self.stop_calls.append(lifecycle_id)
        return self.stop


def test_platform_skip_mid_lifecycle_still_cleans_up(monkeypatch):
    # skip arrives at the SECOND step boundary — the vpc created by step 1
    # must be torn down, and step 2 must never execute
    stub = StubCommands(skip_target="cmd-test", skip_at_boundary=2)
    monkeypatch.setattr(engine, "_commands", stub)
    client = FakeClient({("POST", "/v1/vpcs"): _r(201, {"vpc": {"id": "own-1"}})})
    res = engine.run_lifecycle(_two_create_lc(), client, _cfg())
    assert res["status"] == "skipped", res
    assert "platform command" in (res["reason"] or "")
    assert ("DELETE", "/v1/vpcs/own-1") in client.calls
    assert ("POST", "/v1/subnets") not in client.calls


def test_platform_abort_skips_before_any_call(monkeypatch):
    stub = StubCommands(abort=True)
    monkeypatch.setattr(engine, "_commands", stub)
    client = FakeClient({})
    res = engine.run_lifecycle(_two_create_lc(), client, _cfg())
    assert res["status"] == "skipped", res
    assert "run abort" in (res["reason"] or "")
    assert client.calls == []
    # abort is sticky in core.commands — every later lifecycle skips the same way
    res2 = engine.run_lifecycle(_two_create_lc(), client, _cfg())
    assert res2["status"] == "skipped" and client.calls == []


def test_stop_polling_exits_wait_like_a_timeout(monkeypatch):
    stub = StubCommands(stop_polling=True)
    monkeypatch.setattr(engine, "_commands", stub)
    # the poll loop must break BEFORE its first sleep — sleeping at all here
    # would hang the offline suite for poll.interval seconds
    monkeypatch.setattr(engine.time, "sleep",
                        lambda s: pytest.fail(f"poll loop slept ({s}s) despite stop_polling"))
    lc = {
        "id": "cmd-poll-test", "service": "vpc", "enabled": True,
        "steps": [
            {"name": "create-vpc", "method": "POST", "path": "/v1/vpcs",
             "json": {"name": "x", "cidr": "10.99.0.0/20", "tags": []},
             "capture": {"vpc_id": "$.vpc.id"}, "expect_status": [200, 201, 202],
             "poll": {"field": "$.vpc.state", "until": ["ACTIVE"],
                      "timeout": 300, "interval": 10},
             "cleanup": {"method": "DELETE", "path": "/v1/vpcs/{vpc_id}", "service": "vpc"}},
            {"name": "delete-vpc", "method": "DELETE", "path": "/v1/vpcs/{vpc_id}",
             "expect_status": [200, 202, 204]},
        ],
    }
    client = FakeClient({
        # state never reaches ACTIVE — only the stop command can end the wait
        ("POST", "/v1/vpcs"): _r(201, {"vpc": {"id": "own-2", "state": "CREATING"}}),
        ("GET", "/v1/vpcs/"): _r(200, {"vpc": {"id": "own-2", "state": "CREATING"}}),
    })
    res = engine.run_lifecycle(lc, client, _cfg())
    # the wait ended early with the create's 201 (an expected status), so the
    # lifecycle proceeded and finished — exactly the engine's timeout behaviour
    assert res["status"] == "passed", res
    assert stub.stop_calls == ["cmd-poll-test"]
    assert ("DELETE", "/v1/vpcs/own-2") in client.calls
