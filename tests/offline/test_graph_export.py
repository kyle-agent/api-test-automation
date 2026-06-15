"""Offline tests for controlplane/graph_export.py (R-platform P1/P2/P4).

graph_export is composer-pure (no fastapi/network), so the static Catalog /
Plan / Report export is unit-testable. Focus here: the per-node timing/result
mapping (build_report) and that build_catalog precomputes focus graphs.
"""
from __future__ import annotations

import pytest

from regression.scenarios import composer

graph_export = pytest.importorskip("controlplane.graph_export")


@pytest.fixture(scope="module")
def model():
    return composer.load_model()


def test_build_report_maps_endpoints_to_nodes(model):
    obs = [
        {"method": "POST", "path": "/v1/vpcs", "status": 201, "elapsed_ms": 120.0},
        {"method": "POST", "path": "/v1/vpcs", "status": 201, "elapsed_ms": 140.0},
        {"method": "POST", "path": "/v1/subnets", "status": 202, "elapsed_ms": 90.0},
        {"method": "POST", "path": "/v1/private-nats", "status": 400, "elapsed_ms": 55.0},
    ]
    r = graph_export.build_report(model, obs)
    vpc = r["nodes"]["vpc"]
    assert vpc["status"] == "pass" and vpc["calls"] == 2
    assert abs(vpc["elapsed_ms"] - 130.0) < 0.51          # mean of the two calls
    assert r["nodes"]["private-nat"]["status"] == "fail"  # 4xx
    assert r["nodes"]["keypair"]["status"] == "untested"  # no observation
    assert r["observed"] >= 3 and r["passed"] >= 2 and r["failed"] >= 1


def test_build_report_ignores_query_strings(model):
    obs = [{"method": "POST", "path": "/v1/vpcs?foo=bar", "status": 201,
            "elapsed_ms": 100.0}]
    r = graph_export.build_report(model, obs)
    assert r["nodes"]["vpc"]["status"] == "pass"


def test_build_report_empty_is_all_untested(model):
    r = graph_export.build_report(model, [])
    assert r["observed"] == 0
    assert all(n["status"] == "untested" for n in r["nodes"].values())


def test_build_catalog_precomputes_focus(model):
    cat = graph_export.build_catalog(model)
    assert cat["node_count"] == len(model)
    assert "vpc" in cat["nodes"] and "vpc" in cat["focus"]
    # focus graph carries upstream + dependents; dependents flagged
    fv = cat["focus"]["vpc"]
    assert any(n.get("is_dependent") for n in fv["nodes"])
    # catalog node meta exposes requires + dependents for client closure
    assert "requires" in cat["nodes"]["vpc"] and "dependents" in cat["nodes"]["vpc"]
