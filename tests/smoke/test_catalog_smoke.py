"""Catalog-driven smoke / regression tests.

For every endpoint discovered in the API Reference we generate a test case:

  * GET (read-only) endpoints WITHOUT path params -> called for real; we assert
    the gateway answers with a non-server-error, authenticated status. 2xx is a
    pass; 401/403 is reported as an auth/permission problem; 5xx is a failure.
  * Endpoints WITH path params (need a real resource id) -> skipped unless a
    value is supplied; they are exercised by the CRUD lifecycle suites instead.
  * Mutating endpoints (POST/PUT/PATCH/DELETE) -> skipped by default. They are
    only meaningful inside an ordered CRUD lifecycle (see tests/crud/), and the
    client itself blocks them unless SCP_ALLOW_MUTATIONS is set.

Run a subset, e.g.:  pytest tests/smoke -m smoke --category compute
"""
from __future__ import annotations

import pytest

from framework.catalog import Endpoint, endpoints

pytestmark = pytest.mark.smoke


def _selected(config) -> list[Endpoint]:
    cat = config.getoption("--category")
    svc = config.getoption("--service")
    return endpoints(category=cat, service=svc, resolved_only=True)


def pytest_generate_tests(metafunc):
    if "endpoint" in metafunc.fixturenames:
        eps = _selected(metafunc.config)
        metafunc.parametrize("endpoint", eps, ids=[e.key for e in eps])


def test_endpoint_reachable(endpoint: Endpoint, client):
    if endpoint.is_mutating:
        pytest.skip("mutating endpoint — covered by CRUD lifecycle suites")
    if endpoint.has_path_params:
        pytest.skip(f"needs a real resource id: {endpoint.http_path}")

    resp = client.get(endpoint.http_path, service=endpoint.service)

    if resp.status in (401, 403):
        pytest.fail(
            f"{endpoint.http_path} -> {resp.status}: authentication/authorization "
            f"rejected. Verify SCP_ACCESS_KEY/SCP_SECRET_KEY and the HMAC signing "
            f"scheme (framework/auth.py).")
    # A list GET without path params should return 2xx. Anything else (404 "not
    # found" route, 5xx, etc.) is a real finding, not tolerated.
    assert resp.ok, (
        f"{endpoint.method} {endpoint.http_path} -> {resp.status} (expected 2xx)\n"
        f"{resp.raw_text[:300]}")
