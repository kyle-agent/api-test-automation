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


def _fill(template: str, ctx: dict) -> str:
    return _PLACEHOLDER.sub(lambda m: str(ctx.get(m.group(1), m.group(0))), template)


_active = [lc for lc in LIFECYCLES if lc.get("enabled")]


@pytest.mark.crud
@pytest.mark.parametrize("lifecycle", _active or [pytest.param(None, marks=pytest.mark.skip(
    reason="no enabled lifecycles in lifecycles.json"))],
    ids=[lc["id"] for lc in _active] or ["none"])
def test_crud_lifecycle(lifecycle, client, cfg):
    if not cfg.allow_mutations:
        pytest.skip("set SCP_ALLOW_MUTATIONS=true to run CRUD lifecycle tests")

    # lifecycle "service" is "<category>/<service>"; the host uses the service part.
    service = lifecycle.get("service", "").split("/")[-1] or None

    ctx: dict[str, str] = {}
    created_undeleted: list[str] = []
    try:
        for step in lifecycle["steps"]:
            path = _fill(step["path"], ctx)
            body = step.get("json")
            destructive = step.get("destructive", False)

            if destructive and not cfg.allow_destructive:
                created_undeleted.append(path)
                pytest.xfail(
                    f"destructive step '{step['name']}' skipped (set "
                    f"SCP_ALLOW_DESTRUCTIVE=true). Manual cleanup needed: {path}")

            try:
                resp = client.request(step["method"], path, json=body, service=service)
            except MutationBlocked as exc:
                pytest.skip(str(exc))

            expected = step.get("expect_status", [200])
            assert resp.status in expected, (
                f"[{lifecycle['id']}] step '{step['name']}' "
                f"{step['method']} {path} -> {resp.status}, expected {expected}\n"
                f"{resp.raw_text[:500]}")

            for var, expr in step.get("capture", {}).items():
                val = _jsonpath_get(resp.body, expr) if resp.body else None
                assert val is not None, (
                    f"could not capture '{var}' via '{expr}' from {step['name']} response")
                ctx[var] = val
    finally:
        if created_undeleted:
            print(f"\nWARNING: resources may need manual cleanup: {created_undeleted}")
