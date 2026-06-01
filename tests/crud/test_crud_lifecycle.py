"""Data-driven CRUD lifecycle regression tests.

Each lifecycle in lifecycles.json runs its steps in order against the live
gateway: create -> read -> [update] -> delete. Values produced by one step
(e.g. a new resource id) are captured and substituted into later steps.

Safety:
  * the whole suite is skipped unless SCP_ALLOW_MUTATIONS=true;
  * any step marked "destructive" additionally needs SCP_ALLOW_DESTRUCTIVE=true
    (otherwise the resource is left for manual cleanup and the test xfails with
    a clear message);
  * lifecycles are opt-in per entry via "enabled": true.

This keeps a default regression run from ever creating or deleting real cloud
resources, while giving a single place to declare full CRUD coverage per
service. Add a new service by adding an entry to lifecycles.json — no new code.
"""
from __future__ import annotations

import json
import re
import time
from pathlib import Path

import pytest

from framework.client import MutationBlocked

LIFECYCLES = json.loads((Path(__file__).parent / "lifecycles.json").read_text())["lifecycles"]
_PLACEHOLDER = re.compile(r"\{([a-zA-Z0-9_]+)\}")


def _jsonpath_get(obj, expr: str):
    """Tiny `$.a.b` / `$.a[0].b` resolver — enough for capturing ids."""
    cur = obj
    for token in expr.lstrip("$").lstrip(".").split("."):
        m = re.match(r"([a-zA-Z0-9_]+)(?:\[(\d+)\])?", token)
        if not m:
            return None
        key, idx = m.group(1), m.group(2)
        cur = cur.get(key) if isinstance(cur, dict) else None
        if idx is not None and isinstance(cur, list):
            cur = cur[int(idx)] if int(idx) < len(cur) else None
        if cur is None:
            return None
    return cur


def _capture(body, expr):
    """Capture a value from a response. `expr` is either a JSONPath string
    ("$.a.b", "$.items[0].id") or a filter object that selects the first list
    element matching field prefixes:
        {"list": "$.server_types", "where_prefix": {"id": "s"},
         "where_not_prefix": {"id": "g"}, "get": "id"}
    `where_prefix` keeps only items whose field startswith the value; the value
    of `where_not_prefix` may be a string or list of prefixes to EXCLUDE (e.g.
    exclude GPU 'g*' server types)."""
    if body is None:
        return None
    if isinstance(expr, str):
        return _jsonpath_get(body, expr)
    items = _jsonpath_get(body, expr["list"]) or []
    where = expr.get("where_prefix", {})
    wnot = expr.get("where_not_prefix", {})
    for item in items:
        if not isinstance(item, dict):
            continue
        if not all(str(item.get(k, "")).startswith(v) for k, v in where.items()):
            continue
        excluded = False
        for k, pfx in wnot.items():
            prefixes = [pfx] if isinstance(pfx, str) else pfx
            if any(str(item.get(k, "")).startswith(p) for p in prefixes):
                excluded = True
                break
        if not excluded:
            return item.get(expr["get"])
    return None


def _fill(template: str, ctx: dict) -> str:
    return _PLACEHOLDER.sub(lambda m: str(ctx.get(m.group(1), m.group(0))), template)


def _fill_obj(obj, ctx: dict):
    """Recursively substitute {placeholders} inside a request body (dict/list/str)."""
    if isinstance(obj, str):
        return _fill(obj, ctx)
    if isinstance(obj, dict):
        return {k: _fill_obj(v, ctx) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_fill_obj(v, ctx) for v in obj]
    return obj


def _run_step(client, step, path, body, service, ctx):
    """Execute a step; if it declares "poll", repeat until a condition holds
    (for async provisioning/teardown) or the timeout elapses. The condition is
    either a body field reaching a value ("field"/"until") or the response
    status reaching one of "until_status" (e.g. [404] = resource gone)."""
    params = step.get("params")
    resp = client.request(step["method"], path, json=body, service=service, params=params)
    # Optional: retry while the status is in retry_on_status (e.g. flaky 500 on
    # an async delete). Distinct from poll, which waits for a target state.
    ros = step.get("retry_on_status")
    if ros:
        attempts = int(step.get("retries", 4))
        interval = float(step.get("retry_interval", 15))
        while resp.status in ros and attempts > 0:
            time.sleep(interval)
            resp = client.request(step["method"], path, json=body, service=service, params=params)
            attempts -= 1
    poll = step.get("poll")
    if not poll:
        return resp
    until_status = poll.get("until_status")
    field, until = poll.get("field"), poll.get("until", [])
    timeout, interval = float(poll.get("timeout", 300)), float(poll.get("interval", 10))
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if until_status is not None:
            if resp.status in until_status:
                return resp
        elif field:
            val = _jsonpath_get(resp.body, field) if resp.body else None
            if val in until:
                return resp
        time.sleep(interval)
        resp = client.request(step["method"], path, json=body, service=service, params=params)
    return resp


_active = [lc for lc in LIFECYCLES if lc.get("enabled")]


@pytest.mark.crud
@pytest.mark.parametrize("lifecycle", _active or [pytest.param(None, marks=pytest.mark.skip(
    reason="no enabled lifecycles in lifecycles.json"))],
    ids=[lc["id"] for lc in _active] or ["none"])
def test_crud_lifecycle(lifecycle, client, cfg):
    if not cfg.allow_mutations:
        pytest.skip("set SCP_ALLOW_MUTATIONS=true to run CRUD lifecycle tests")

    # lifecycle "service" is "<category>/<service>"; the host uses the service
    # part. Individual steps may override it (chains span several services).
    service = lifecycle.get("service", "").split("/")[-1] or None

    # Seed context: {unique} = short lowercase alnum token (safe for name
    # patterns / length limits), {region} for bodies that need it.
    ctx: dict[str, str] = {"unique": format(int(time.time()), "x"), "region": cfg.region}
    # Teardown stack of (label, method, path, service) for resources created so
    # far, used to best-effort clean up if the lifecycle fails partway — so a
    # failed run never leaks a billable resource (e.g. an orphaned VM).
    cleanups: list[tuple] = []

    def _teardown():
        for label, method, path, svc in reversed(cleanups):
            try:
                if cfg.allow_destructive:
                    client.request(method, path, service=svc)
                    print(f"  cleanup: {method} {path}")
            except Exception as exc:  # best-effort; report and continue
                print(f"  cleanup FAILED for {label} ({path}): {exc}")

    try:
        for step in lifecycle["steps"]:
            step_service = step.get("service") or service
            if step.get("wait"):  # let async provisioning settle before this step
                time.sleep(float(step["wait"]))
            path = _fill(step["path"], ctx)
            body = _fill_obj(step.get("json"), ctx)

            if step.get("destructive") and not cfg.allow_destructive:
                pytest.xfail(
                    f"destructive step '{step['name']}' skipped (set "
                    f"SCP_ALLOW_DESTRUCTIVE=true). Manual cleanup needed: {path}")

            try:
                resp = _run_step(client, step, path, body, step_service, ctx)
            except MutationBlocked as exc:
                pytest.skip(str(exc))

            expected = step.get("expect_status", [200])
            assert resp.status in expected, (
                f"[{lifecycle['id']}] step '{step['name']}' "
                f"{step['method']} {path} -> {resp.status}, expected {expected}\n"
                f"{resp.raw_text[:500]}")

            for var, expr in step.get("capture", {}).items():
                val = _capture(resp.body, expr)
                assert val is not None, (
                    f"could not capture '{var}' via {expr!r} from {step['name']} response")
                ctx[var] = str(val)

            # Register teardown for a freshly-created resource (runs only on a
            # later failure; the happy path deletes via its own steps).
            cu = step.get("cleanup")
            if cu:
                cleanups.append((step["name"], cu["method"], _fill(cu["path"], ctx),
                                 cu.get("service") or step_service))
    except Exception:
        print(f"\n[{lifecycle['id']}] failed — attempting teardown of created resources:")
        _teardown()
        raise
