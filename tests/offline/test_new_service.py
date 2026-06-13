"""Offline tests for tools/new_service.py — the M6a onboarding scaffolder.

No network, no credentials. Proves that scaffolding a known service yields a
node whose create endpoint resolves in the catalog, and that the emitted YAML
parses and carries the expected resource-task keys.
"""
from __future__ import annotations

import json
from pathlib import Path

import yaml

from tools import new_service

_ROOT = Path(new_service.__file__).parent.parent


def _catalog_norm_index():
    """(method, normalized-path, short-service) set, mirroring the validator."""
    from regression.scenarios.validate import _norm_path

    cat = json.loads((_ROOT / "data" / "api_catalog.json").read_text("utf-8"))
    eps = cat["endpoints"] if isinstance(cat, dict) and "endpoints" in cat \
        else cat
    return {((e.get("method") or "").upper(), _norm_path(e["http_path"]),
             e["service"]) for e in eps}


def test_scaffold_queueservice_has_resolvable_create():
    nodes = new_service.scaffold("application-service/queueservice")
    # POST /v1/queues + DELETE /v1/queues/{id} => one creatable node 'queue'
    assert "queue" in nodes
    queue = nodes["queue"]
    assert queue["service"] == "application-service/queueservice"
    assert queue["code"] == "ap-queueservice-queue"

    # create endpoint resolves against the catalog (method, norm-path, svc)
    method, _, path = queue["create"]["endpoint"].partition(" ")
    from regression.scenarios.validate import _norm_path
    key = (method.upper(), _norm_path(path), "queueservice")
    assert key in _catalog_norm_index(), \
        f"create endpoint {queue['create']['endpoint']!r} not in catalog"

    # the matching DELETE was discovered (creatable family has teardown)
    assert queue.get("delete", {}).get("endpoint", "").startswith("DELETE ")

    # capture guess present
    assert queue["capture"] == {"queue_id": "$.id"}


def test_scaffold_body_from_required_dto_fields():
    nodes = new_service.scaffold("application-service/queueservice")
    body = nodes["queue"]["create"].get("body") or {}
    # queuecreaterequestv1_2 has required 'name' among others
    assert "name" in body
    # name placeholders use the {unique} builtin (no dangling placeholder)
    assert "{unique}" in str(body["name"])


def test_emitted_yaml_parses_and_has_expected_keys():
    nodes = new_service.scaffold("application-service/queueservice")
    text = new_service.emit_yaml("application-service/queueservice", nodes)
    doc = yaml.safe_load(text)
    assert doc["version"] == 1
    assert "resources" in doc and "queue" in doc["resources"]
    queue = doc["resources"]["queue"]
    for k in ("code", "service", "requires", "create", "capture",
              "provenance", "notes"):
        assert k in queue, f"emitted node missing key {k!r}"
    assert queue["provenance"] == "docs"
    assert queue["create"]["endpoint"] == "POST /v1/queues"
    assert "auto-scaffolded" in queue["notes"]


def test_lookup_family_is_get_only_with_no_delete():
    # cachestore has GET-only families (engine-versions, parameters, ...)
    nodes = new_service.scaffold("database/cachestore")
    # creatable cluster (POST + DELETE)
    assert nodes["cluster"]["create"]["endpoint"] == "POST /v1/clusters"
    assert "delete" in nodes["cluster"]
    # a lookup node: GET create, capture_soft, no delete
    lookup = nodes["engine-version"]
    assert lookup["create"]["endpoint"].startswith("GET ")
    assert lookup.get("capture_soft") is True
    assert "delete" not in lookup


def test_unknown_service_raises():
    import pytest
    with pytest.raises(SystemExit):
        new_service.scaffold("no-such/service")
