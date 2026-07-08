"""tools.promote_validated — service-scoped docs→VALIDATED promotion.

The load-bearing property: the join between a node's create endpoint and
verified_endpoints.json MUST be service-scoped (node service tail == verified
key middle segment). ``/v1/clusters`` collides across the DB engines — an
unscoped (method, norm_path) match would promote sqlserver off cachestore
evidence, which is exactly the masked promotion this tool must never do.
"""
import json

import pytest
import yaml

from tools import promote_validated as pv


MODEL_CACHESTORE = """\
# hand-written comment that MUST survive --apply
version: 1
resources:
  cachestore-cluster:
    code: "db-cachestore-cluster"
    service: database/cachestore
    requires: []
    create:
      endpoint: "POST /v1/clusters"
      body: {name: "regr{unique}"}
    capture: {cluster_id: "$.resource_id"}
    delete: {endpoint: "DELETE /v1/clusters/{cluster_id}", destructive: true}
    provenance: docs
    notes: "trailing node comment"

  cachestore-stop:
    code: "db-cachestore-stop"
    service: database/cachestore
    requires: [cachestore-cluster]
    create:
      endpoint: "POST /v1/clusters/{cachestore-cluster.cluster_id}/stop"
    provenance: docs  # existing inline comment
"""

MODEL_SQLSERVER = """\
version: 1
resources:
  sqlserver-cluster:
    code: "db-sqlserver-cluster"
    service: database/sqlserver
    requires: []
    create:
      endpoint: "POST /v1/clusters"
      body: {name: "regr{unique}"}
    capture: {cluster_id: "$.resource_id"}
    delete: {endpoint: "DELETE /v1/clusters/{cluster_id}", destructive: true}
    provenance: docs

  sqlserver-lookup:
    code: "db-sqlserver-lookup"
    service: database/sqlserver
    lookup: true
    requires: []
    create: {endpoint: "GET /v1/engine-versions"}
    capture: {engine_id: "$.contents[0].id"}
    provenance: docs
"""

# cachestore create + stop have 2xx evidence; sqlserver has NONE for
# /v1/clusters (only the GET lookup) — the collision the scoping must survive.
VERIFIED = {
    "database/cachestore/createcluster": {
        "method": "POST", "path": "/v1/clusters", "norm_path": "v1/clusters",
        "first_run": "r1", "last_run": "r9", "count": 3},
    "database/cachestore/stopcluster": {
        "method": "POST", "path": "/v1/clusters/abc123/stop",
        "norm_path": "v1/clusters/*/stop",
        "first_run": "r2", "last_run": "r9", "count": 1},
    "database/sqlserver/listengineversions": {
        "method": "GET", "path": "/v1/engine-versions?size=20",
        "norm_path": "v1/engine-versions",
        "first_run": "r3", "last_run": "r8", "count": 2},
}


@pytest.fixture
def fixture(tmp_path):
    mdir = tmp_path / "resources"
    mdir.mkdir()
    (mdir / "database__cachestore.yaml").write_text(MODEL_CACHESTORE)
    (mdir / "database__sqlserver.yaml").write_text(MODEL_SQLSERVER)
    vpath = tmp_path / "verified_endpoints.json"
    vpath.write_text(json.dumps(VERIFIED))
    return mdir, vpath


def test_service_scoped_never_cross_service(fixture):
    mdir, vpath = fixture
    model, _ = pv.load_model(mdir)
    rows = pv.promotable(model, json.loads(vpath.read_text()))
    got = {r["node"]: r["evidence_key"] for r in rows}
    # cachestore promotes off its OWN evidence; the templated-id stop path
    # collapses to */stop and matches; the GET-create lookup counts too.
    assert got == {
        "cachestore-cluster": "database/cachestore/createcluster",
        "cachestore-stop": "database/cachestore/stopcluster",
        "sqlserver-lookup": "database/sqlserver/listengineversions",
    }
    # the collision case: sqlserver-cluster shares (POST, v1/clusters) with
    # cachestore's evidence but has none of its own — NEVER promotable.
    assert "sqlserver-cluster" not in got


def test_apply_targeted_edit_preserves_comments(fixture):
    mdir, vpath = fixture
    rows, errors = pv.run(mdir, vpath, apply=True)
    assert errors == []
    assert len(rows) == 3

    cache_text = (mdir / "database__cachestore.yaml").read_text()
    sql_text = (mdir / "database__sqlserver.yaml").read_text()

    # hand-written comments survive
    assert "# hand-written comment that MUST survive --apply" in cache_text
    # evidence note appended, existing inline comment preserved ahead of it
    assert ("provenance: VALIDATED  # evidence: "
            "database/cachestore/createcluster (run r9)") in cache_text
    assert ("provenance: VALIDATED  # existing inline comment · evidence: "
            "database/cachestore/stopcluster (run r9)") in cache_text
    # the cross-service collision node stays docs
    doc = yaml.safe_load(sql_text)
    assert doc["resources"]["sqlserver-cluster"]["provenance"] == "docs"
    assert doc["resources"]["sqlserver-lookup"]["provenance"] == "VALIDATED"

    # only provenance changed — full parse matches the original modulo that
    orig = yaml.safe_load(MODEL_CACHESTORE)
    orig["resources"]["cachestore-cluster"]["provenance"] = "VALIDATED"
    orig["resources"]["cachestore-stop"]["provenance"] = "VALIDATED"
    assert yaml.safe_load(cache_text) == orig


def test_apply_is_idempotent(fixture):
    mdir, vpath = fixture
    pv.run(mdir, vpath, apply=True)
    rows, errors = pv.run(mdir, vpath, apply=True)
    assert rows == [] and errors == []


def test_node_filter(fixture):
    mdir, vpath = fixture
    model, _ = pv.load_model(mdir)
    rows = pv.promotable(model, json.loads(vpath.read_text()),
                         only={"cachestore-stop"})
    assert [r["node"] for r in rows] == ["cachestore-stop"]


def test_no_api_nodes_never_promotable(fixture, tmp_path):
    mdir, vpath = fixture
    (mdir / "container__scr.yaml").write_text(
        "version: 1\n"
        "resources:\n"
        "  scr-image-x:\n"
        "    code: \"ct-scr-image-x\"\n"
        "    service: container/scr\n"
        "    no_api: true\n"
        "    requires: []\n"
        "    provenance: docs\n")
    model, _ = pv.load_model(mdir)
    rows = pv.promotable(model, json.loads(vpath.read_text()))
    assert "scr-image-x" not in {r["node"] for r in rows}
