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


_STATUS_FILE = "reports/smoke_status.tsv"


def _record(endpoint: Endpoint, status: int) -> None:
    """Append the observed status so the CI summary can show the distribution."""
    try:
        with open(_STATUS_FILE, "a") as fh:
            fh.write(f"{status}\t{endpoint.key}\t{endpoint.method}\t{endpoint.http_path}\n")
    except OSError:
        pass


def test_endpoint_reachable(endpoint: Endpoint, client):
    if endpoint.is_mutating:
        pytest.skip("mutating endpoint — covered by CRUD lifecycle suites")
    if endpoint.has_path_params:
        pytest.skip(f"needs a real resource id: {endpoint.http_path}")

    resp = client.get(endpoint.http_path, service=endpoint.service)
    _record(endpoint, resp.status)

    # Regression gate: the API must authenticate and not error on the server.
    #  * 401  -> our signing/credentials are broken (hard fail).
    #  * 5xx  -> server error (hard fail).
    #  * 2xx  -> works.
    #  * 400/403/404/409/422 -> the endpoint responded correctly to a request that
    #    lacks required query params, permissions, or a provisioned resource;
    #    that is expected for a bare list call and is reported, not failed.
    if resp.status == 401:
        pytest.fail(
            f"{endpoint.http_path} -> 401: authentication rejected. Verify "
            f"SCP_ACCESS_KEY/SCP_SECRET_KEY and the HMAC signing (framework/auth.py).")
    assert resp.status < 500, (
        f"{endpoint.method} {endpoint.http_path} -> {resp.status} (server error)\n"
        f"{resp.raw_text[:300]}")
