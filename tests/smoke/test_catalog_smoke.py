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

import json
from pathlib import Path

import pytest

from framework.catalog import Endpoint, endpoints

pytestmark = pytest.mark.smoke


def _load_known_issues() -> dict:
    """Baseline of already-tracked backend failures (known_issues.json). A smoke
    'fail' whose key is listed here is xfailed (expected) rather than failing the
    suite, so the regression check stays green unless a NEW endpoint breaks. The
    row is still recorded, so the dashboard keeps showing it as known-red."""
    p = Path("known_issues.json")
    if not p.exists():
        return {}
    try:
        return {i["key"]: i for i in json.loads(p.read_text()).get("issues", [])}
    except Exception:
        return {}


_KNOWN_ISSUES = _load_known_issues()


def _selected(config) -> list[Endpoint]:
    cat = config.getoption("--category")
    svc = config.getoption("--service")
    return endpoints(category=cat, service=svc, resolved_only=True)


def pytest_generate_tests(metafunc):
    if "endpoint" in metafunc.fixturenames:
        eps = _selected(metafunc.config)
        metafunc.parametrize("endpoint", eps, ids=[e.key for e in eps])


_STATUS_FILE = "reports/smoke_status.tsv"
# Parameter-coverage probe: re-issue each OK GET once with a universally-ignorable
# read-only pagination set, recording to a SEPARATE file so it never inflates the
# smoke count/coverage. Makes the dashboard's "parameter" axis measurable.
_PARAM_FILE = "reports/param_status.tsv"
_PARAM_SET = {"page": 0, "size": 1, "limit": 1}
_PARAM_REPR = "page=0&size=1&limit=1"


@pytest.fixture(scope="session", autouse=True)
def _reset_smoke_status():
    """Start the smoke session with fresh status logs. Scoped to the smoke
    suite (not the root conftest) so a later CRUD pytest session does NOT wipe
    these rows — CRUD's probe-reads append to them for the dashboard's coverage."""
    from pathlib import Path
    for f in (_STATUS_FILE, _PARAM_FILE):
        p = Path(f)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text("")
    yield


def _record(endpoint: Endpoint, status: int, category: str) -> None:
    """Append status + category so the CI summary can group results."""
    try:
        with open(_STATUS_FILE, "a") as fh:
            fh.write(f"{status}\t{category}\t{endpoint.key}\t{endpoint.method}\t{endpoint.http_path}\n")
    except OSError:
        pass


def _record_param(endpoint: Endpoint, status: int, category: str) -> None:
    """Append a parameter-coverage result (6th column = the param set used)."""
    try:
        with open(_PARAM_FILE, "a") as fh:
            fh.write(f"{status}\t{category}\t{endpoint.key}\t{endpoint.method}\t{endpoint.http_path}\t{_PARAM_REPR}\n")
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


# A handful of read GETs answer 4xx on a bare call because a query param is
# mandatory (not a permission/entitlement limit). We feed a synthetic value so
# they actually exercise — non-destructive (a duplication check with an unused
# name creates nothing). Param names aren't published (docs are JS-rendered), so
# we try an ordered candidate list and keep the first that returns 2xx; if none
# do, the endpoint simply stays soft exactly as before (no regression).
_DUP_NAME = "regrprobesmoke"


def _required_param_candidates(endpoint: Endpoint) -> list[dict]:
    p = endpoint.http_path
    if p.endswith("/check-duplication") or p.endswith("/check-duplication/name"):
        return [{"name": _DUP_NAME}, {"productName": _DUP_NAME}, {"resourceName": _DUP_NAME}]
    if p.endswith("/parameters"):
        svc = endpoint.service  # e.g. mysql / postgresql / eventstreams
        return [{"dbType": svc}, {"engine": svc}, {"engineName": svc},
                {"dbEngine": svc}, {"engineVersion": "1"}, {"version": "1"}]
    return []


def test_endpoint_reachable(endpoint: Endpoint, client):
    if endpoint.is_mutating:
        pytest.skip("mutating endpoint — covered by CRUD lifecycle suites")
    if endpoint.has_path_params:
        pytest.skip(f"needs a real resource id: {endpoint.http_path}")

    # Endpoints needing a mandatory query param: try candidates (first 2xx wins);
    # everything else makes a single bare call.
    candidates = _required_param_candidates(endpoint)
    resp = category = reason = None
    for params in (candidates or [None]):
        try:
            resp = client.get(endpoint.http_path, service=endpoint.service, params=params)
        except Exception as exc:
            # Transient/host failure (timeout, connection reset). Record it as a
            # fail (status 0) instead of letting it vanish from the tsv, so the
            # count stays honest and the dashboard surfaces unreachable services.
            _record(endpoint, 0, "fail")
            pytest.fail(f"{endpoint.method} {endpoint.http_path} -> unreachable: {exc}")
        category, reason = _categorize(resp.status, resp.raw_text)
        if category == "ok":
            break

    _record(endpoint, resp.status, category)

    # Parameter-coverage probe (read-only, record-only): re-issue the same GET
    # once with pagination params. Only for plain OK list endpoints (skip the
    # ones we already fed required params, to keep that axis clean); a 400 on
    # params is informative (recorded soft), not a failure.
    if resp.ok and not candidates:
        try:
            presp = client.get(endpoint.http_path, service=endpoint.service, params=_PARAM_SET)
            pcat, _ = _categorize(presp.status, presp.raw_text)
            _record_param(endpoint, presp.status, pcat)
        except Exception:
            _record_param(endpoint, 0, "fail")

    # A failure on an endpoint already baselined in known_issues.json is an
    # already-tracked backend bug, not a regression in our suite — xfail it so the
    # check stays green for the known set (it's still recorded above as known-red).
    if category == "fail" and endpoint.key in _KNOWN_ISSUES:
        ki = _KNOWN_ISSUES[endpoint.key]
        pytest.xfail(
            f"known issue ({ki.get('type', '?')}, since {ki.get('since', '?')}): "
            f"{endpoint.http_path} -> {resp.status}; {ki.get('note', '')}")

    assert category != "fail", (
        f"{endpoint.method} {endpoint.http_path} -> {resp.status} ({reason})\n"
        f"{resp.raw_text[:300]}")
