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


def _record(endpoint: Endpoint, status: int, category: str) -> None:
    """Append status + category so the CI summary can group results."""
    try:
        with open(_STATUS_FILE, "a") as fh:
            fh.write(f"{status}\t{category}\t{endpoint.key}\t{endpoint.method}\t{endpoint.http_path}\n")
    except OSError:
        pass


def _categorize(status: int, text: str) -> tuple[str, str]:
    """Classify a read-only response into ok / soft / fail with a reason.

    Hard-fail only on genuine problems:
      * 401 HmacValidFail   -> our signing is wrong.
      * 5xx                 -> server error.
    Everything else is the API responding correctly to a bare list call given
    this account's params/permissions/entitlements, and is reported, not failed:
      * 401 "rejected by gateway"/"catalog has not target" -> service not
        entitled for this account/region (env), not an auth bug.
      * 403 no permission, 404 not provisioned, 400/409/422 needs params/data.
    """
    t = (text or "").lower()
    if 200 <= status < 300:
        return "ok", ""
    if status == 401:
        if "rejected by gateway" in t or "catalog has not target" in t:
            return "soft", "service not entitled for this account/region"
        return "fail", "HmacValidFail — signing/credentials wrong"
    if status >= 500:
        return "fail", "server error"
    if status == 403:
        return "soft", "no permission for this key"
    if status == 404:
        return "soft", "not provisioned / not found"
    return "soft", "needs required query params / data"  # 400/409/422/etc.


def test_endpoint_reachable(endpoint: Endpoint, client):
    if endpoint.is_mutating:
        pytest.skip("mutating endpoint — covered by CRUD lifecycle suites")
    if endpoint.has_path_params:
        pytest.skip(f"needs a real resource id: {endpoint.http_path}")

    try:
        resp = client.get(endpoint.http_path, service=endpoint.service)
    except Exception as exc:
        # Transient/host failure (timeout, connection reset). Record it as a
        # fail (status 0) instead of letting it vanish from the tsv, so the
        # count stays honest and the dashboard surfaces unreachable services.
        _record(endpoint, 0, "fail")
        pytest.fail(f"{endpoint.method} {endpoint.http_path} -> unreachable: {exc}")

    category, reason = _categorize(resp.status, resp.raw_text)
    _record(endpoint, resp.status, category)

    assert category != "fail", (
        f"{endpoint.method} {endpoint.http_path} -> {resp.status} ({reason})\n"
        f"{resp.raw_text[:300]}")
