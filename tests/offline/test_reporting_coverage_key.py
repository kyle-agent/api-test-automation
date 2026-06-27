"""Regression test for the Reporting coverage surface's service-attribution.

The convergence bug this guards: the Testing engine records observations under a
``lifecycle:step`` endpoint_key (e.g. ``gen-cost-reads:create-cost-reads``), but
the read-only smoke/crud sweep uses ``category/service/op``. An early version of
``_service_of_key`` only split on ``/``, so EVERY live lifecycle run was invisible
to coverage — a run could turn three services green and Reporting would show +0.

These tests pin both key shapes to the right ``category/service`` unit, and prove
a single ``lifecycle:step`` 2xx flips its service to TESTED. Hermetic: pure
functions, no network / creds / results store.
"""
from controlplane import reporting_routes as rr


# lifecycle_id -> declared service, as load_lifecycles() would yield it.
LC = {
    "gen-cost-reads": "financial-management/costexplorer",
    "security-kms-managed-readonly": "security/kms",
}


def test_service_of_key_lifecycle_step_shape():
    # lifecycle:step -> the lifecycle's declared service (the formerly-blind path)
    assert rr._service_of_key("gen-cost-reads:create-cost-reads", LC) == \
        "financial-management/costexplorer"
    assert rr._service_of_key("security-kms-managed-readonly:verify-x", LC) == \
        "security/kms"


def test_service_of_key_sweep_slash_shape():
    # category/service/op -> first two segments (unchanged behaviour)
    assert rr._service_of_key(
        "networking/security-group/deletesecuritygroup", LC) == \
        "networking/security-group"


def test_service_of_key_unresolvable():
    # unknown lifecycle id, or a too-short key, resolves to empty (not credited)
    assert rr._service_of_key("unknown-lifecycle:step", LC) == ""
    assert rr._service_of_key("bare", LC) == ""
    assert rr._service_of_key("", LC) == ""


def test_tested_services_credits_lifecycle_runs():
    obs = [
        {"endpoint_key": "gen-cost-reads:create-cost-reads", "status": 200},
        {"endpoint_key": "gen-cost-reads:verify-usages", "status": 200},
        # a non-2xx on a lifecycle step must NOT credit the service
        {"endpoint_key": "security-kms-managed-readonly:create", "status": 404},
        # the slash-shape sweep still counts
        {"endpoint_key": "networking/vpc/listvpcs", "status": 200},
    ]
    tested = rr._tested_services(obs, LC)
    assert "financial-management/costexplorer" in tested  # via lifecycle:step
    assert "networking/vpc" in tested                     # via sweep slash key
    assert "security/kms" not in tested                   # 404 => not tested


def test_coverage_by_service_marks_lifecycle_run_tested():
    # model: two services, both modeled (have provenance); only one gets a 2xx.
    model = {
        "n1": {"service": "financial-management/costexplorer", "provenance": "docs"},
        "n2": {"service": "security/kms", "provenance": "docs"},
    }
    obs = [{"endpoint_key": "gen-cost-reads:create-cost-reads", "status": 200}]
    cov = rr._coverage_by_service(model, obs, LC)
    assert cov["financial-management/costexplorer"] == rr.TESTED
    assert cov["security/kms"] == rr.MODELED
