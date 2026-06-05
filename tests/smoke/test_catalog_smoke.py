"""Catalog-driven smoke / regression tests — THIN pytest entrypoint.

For every endpoint discovered in the API Reference we generate a test case; the
actual probe + categorize + record logic lives in the :mod:`regression.smoke`
engine. This module is only the pytest glue: parametrization (one case per
catalog endpoint, ids = endpoint key), the ``smoke`` marker, the session reset
of the legacy status logs, and the ok/soft/fail + known-issues-xfail assertions.

  * GET (read-only) endpoints WITHOUT path params -> called for real; a 'fail'
    category fails the test (5xx / HMAC-401), everything else passes.
  * Endpoints WITH path params / mutating endpoints -> skipped (covered by the
    read-chain and CRUD lifecycle suites).
  * A 'fail' on an endpoint baselined in data/baselines/known_issues.json is
    xfailed (still recorded), so the gate stays green unless a NEW endpoint breaks.

Run a subset, e.g.:  pytest tests/smoke -m smoke --category compute
"""
from __future__ import annotations

import pytest

from core.catalog import Endpoint
from core import results
from regression import smoke

pytestmark = pytest.mark.smoke

# Known-issues baseline loaded once via the engine (same data file/behaviour).
_KNOWN_ISSUES = smoke.load_known_issues()


def pytest_generate_tests(metafunc):
    if "endpoint" in metafunc.fixturenames:
        cat = metafunc.config.getoption("--category")
        svc = metafunc.config.getoption("--service")
        eps = smoke.select_endpoints(category=cat, service=svc)
        metafunc.parametrize("endpoint", eps, ids=[e.key for e in eps])


@pytest.fixture(scope="session", autouse=True)
def _reset_smoke_status():
    """Start the smoke session with fresh status logs. Scoped to the smoke
    suite (not the root conftest) so a later CRUD pytest session does NOT wipe
    these rows — CRUD's probe-reads append to them for the dashboard's coverage."""
    smoke.reset_status_files()
    yield


def test_endpoint_reachable(endpoint: Endpoint, client):
    res = smoke.smoke_endpoint(endpoint, client, known_issues=_KNOWN_ISSUES)

    if res.get("skipped"):
        pytest.skip(res.get("reason", "skipped"))

    # Transient/host failure surfaced as status 0 (already recorded by the engine).
    if res["status"] == 0 and res["category"] == results.FAIL:
        pytest.fail(f"{endpoint.method} {endpoint.http_path} -> {res['reason']}")

    # A failure on a baselined endpoint is an already-tracked backend bug, not a
    # regression in our suite — xfail (it's still recorded as known-red).
    if res["category"] == results.FAIL and res.get("known_issue"):
        ki = _KNOWN_ISSUES[endpoint.key]
        pytest.xfail(
            f"known issue ({ki.get('type', '?')}, since {ki.get('since', '?')}): "
            f"{endpoint.http_path} -> {res['status']}; {ki.get('note', '')}")

    assert res["category"] != results.FAIL, (
        f"{endpoint.method} {endpoint.http_path} -> {res['status']} ({res['reason']})")
