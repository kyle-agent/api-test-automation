"""Per-lifecycle stop (owner 2026-07-09 '중간에 특정 라이프사이클을 멈출 수는
없네') — the console2_server side of the engine command channel: enqueue
stop_polling+skip_scenario for ONE lifecycle of a running local run, serve them
on /api/runs/{rid}/commands, consume acks. The engine half already exists
(core.commands + engine step-boundary/poll checks)."""
from __future__ import annotations

import time

from tools import console2_server as c2


def _fake_run(rid="run-test-1", status="running", ids=("lc-a", "lc-b")):
    rec = {"id": rid, "status": status, "lifecycle_ids": list(ids),
           "started": time.time()}
    with c2._LOCK:
        c2._RUNS[rid] = rec
    return rec


def _cleanup(rid):
    with c2._LOCK:
        c2._RUNS.pop(rid, None)
        c2._COMMANDS.pop(rid, None)


def test_skip_enqueues_stop_polling_then_skip_scenario():
    _fake_run()
    try:
        code, payload = c2.skip_lifecycle("run-test-1", "lc-b")
        assert code == 202 and payload["ok"]
        cmds = c2.local_pending_commands("run-test-1")
        assert [c["action"] for c in cmds] == ["stop_polling", "skip_scenario"]
        assert {c["target"] for c in cmds} == {"lc-b"}
        assert all(c["id"] >= c2._CMD_BASE for c in cmds)  # never collides with DB ids
    finally:
        _cleanup("run-test-1")


def test_ack_consumes_a_command():
    _fake_run()
    try:
        c2.skip_lifecycle("run-test-1", "lc-a")
        first = c2.local_pending_commands("run-test-1")[0]
        assert c2.local_ack_command(first["id"]) is True
        left = c2.local_pending_commands("run-test-1")
        assert first["id"] not in {c["id"] for c in left} and len(left) == 1
        assert c2.local_ack_command(123) is False   # unknown id
    finally:
        _cleanup("run-test-1")


def test_skip_guards_run_state_and_membership():
    _fake_run(rid="run-test-2", status="done")
    _fake_run(rid="run-test-3", status="running")
    try:
        assert c2.skip_lifecycle("no-such-run", "lc-a")[0] == 404
        assert c2.skip_lifecycle("run-test-2", "lc-a")[0] == 409   # not running
        assert c2.skip_lifecycle("run-test-3", "lc-zz")[0] == 404  # not in run
        assert c2.local_pending_commands("run-test-3") == []
    finally:
        _cleanup("run-test-2")
        _cleanup("run-test-3")


def test_run_env_carries_platform_url_and_run_id():
    """The engine subprocess must be able to poll the channel: the run env
    exports APITEST_PLATFORM_URL (and APITEST_RUN_ID already existed)."""
    import inspect
    src = inspect.getsource(c2)
    assert '"APITEST_PLATFORM_URL"' in src and '"APITEST_RUN_ID"' in src
