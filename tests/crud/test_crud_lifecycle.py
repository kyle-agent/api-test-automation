"""Data-driven CRUD lifecycle regression tests — THIN pytest entrypoint.

Each lifecycle in scenarios.json runs its steps in order against the live
gateway: create -> read -> [update] -> delete. The ordered runner (capture /
poll / retry / group teardown / quota budgets / owner-tagging / recording) lives
in the :mod:`regression.scenarios.engine` engine; this module is only the pytest
glue: one parametrized case per enabled lifecycle (ids = lifecycle id), the
``crud`` marker, and surfacing the engine's result as skip / xfail / pass / fail.

Safety (enforced by the engine via ``cfg``):
  * the whole suite is skipped unless SCP_ALLOW_MUTATIONS=true;
  * any step marked "destructive" additionally needs SCP_ALLOW_DESTRUCTIVE=true
    (otherwise the resource is left for manual cleanup and the test xfails);
  * heavy lifecycles need SCP_RUN_HEAVY=true;
  * lifecycles are opt-in per entry via "enabled": true.
"""
from __future__ import annotations

import pytest

from regression.scenarios import engine

_active = engine.active_lifecycles()


@pytest.mark.crud
@pytest.mark.parametrize("lifecycle", _active or [pytest.param(None, marks=pytest.mark.skip(
    reason="no enabled lifecycles in scenarios.json"))],
    ids=[lc["id"] for lc in _active] or ["none"])
def test_crud_lifecycle(lifecycle, client, cfg):
    # The engine runs the lifecycle and NEVER raises on an environmental skip:
    # it returns status='skipped'. A genuine failure (wrong status / capture
    # miss) is re-raised after best-effort teardown, failing this test.
    result = engine.run_lifecycle(lifecycle, client, cfg)

    if result["status"] == "skipped":
        reason = result.get("reason") or "skipped"
        # Preserve the original test's xfail (not skip) for a destructive step
        # left un-run because SCP_ALLOW_DESTRUCTIVE is unset.
        if reason.startswith("destructive step "):
            pytest.xfail(reason)
        pytest.skip(reason)

    # status == 'passed' (a 'failed' status only occurs when the engine could not
    # re-raise; run_lifecycle re-raises genuine failures, so they fail the test
    # directly). Group-level optional failures were already warned by the engine.
    assert result["status"] == "passed", (
        f"[{result['id']}] lifecycle {result['status']}: {result.get('reason')}")
