"""Offline tests for the Modeling worklist completeness judgment
(controlplane/resource_model.py: node_meta / worklist).

These are fastapi-free (the logic was moved out of resource_routes precisely so
it is unit-testable). Focus: a node with `no_api: true` is *complete-by-design*
— it has no REST create endpoint on purpose (docker-push-born resource), so it
must NOT be reported as "incomplete" (an authoring target) nor as "docs-only"
(an API-validation target); it belongs in its own `no_api` group.
"""
from __future__ import annotations

from controlplane import resource_model


def test_node_meta_marks_no_api_complete_by_design():
    model = {
        "img": {"service": "container/scr", "no_api": True,
                "provenance": "docs", "requires": ["repo"]},
        "repo": {"service": "container/scr", "provenance": "VALIDATED",
                 "create": {"endpoint": "POST /v1/repositories"}},
    }
    _, meta = resource_model.node_meta(model)
    # no_api node: endpoint-less yet complete (not an authoring defect).
    assert meta["img"]["no_api"] is True
    assert meta["img"]["has_endpoint"] is False
    assert meta["img"]["complete"] is True
    # ordinary anchor with an endpoint stays complete too.
    assert meta["repo"]["complete"] is True


def test_node_meta_flags_genuine_incomplete():
    model = {
        "x": {"service": "s", "provenance": "docs"},  # no endpoint, not no_api
        "y": {"service": "s", "provenance": "docs",
              "create": {"endpoint": "POST /v1/y"}, "requires": ["ghost"]},
    }
    _, meta = resource_model.node_meta(model)
    assert meta["x"]["complete"] is False          # missing create.endpoint
    assert meta["y"]["complete"] is False           # unresolved require
    assert "ghost" in meta["y"]["missing"]


def test_worklist_buckets_no_api_separately():
    model = {
        "img": {"service": "container/scr", "no_api": True, "provenance": "docs"},
        "needs_author": {"service": "s", "provenance": "docs"},  # no endpoint
        "needs_valid": {"service": "s", "provenance": "docs",
                        "create": {"endpoint": "POST /v1/v"}},
        "done": {"service": "s", "provenance": "VALIDATED",
                 "create": {"endpoint": "POST /v1/d"}},
    }
    wl = resource_model.worklist(model)
    ids = lambda bucket: {r["id"] for r in wl[bucket]}
    assert ids("incomplete") == {"needs_author"}
    assert ids("docs_only") == {"needs_valid"}
    assert ids("no_api") == {"img"}
    # a no_api node never leaks into the authoring or validation buckets.
    assert "img" not in ids("incomplete") and "img" not in ids("docs_only")


def test_real_model_no_api_nodes_not_incomplete():
    """Regression: scr-image / scr-tag (the repo's only no_api nodes) must not
    appear as 'incomplete' — they have no REST create endpoint by design."""
    model = resource_model.load_model()
    no_api_ids = {nid for nid, n in model.items() if n.get("no_api")}
    if not no_api_ids:
        return  # model changed; nothing to assert
    wl = resource_model.worklist(model)
    inc_ids = {r["id"] for r in wl["incomplete"]}
    assert no_api_ids.isdisjoint(inc_ids), (
        f"no_api nodes mis-flagged as incomplete: {no_api_ids & inc_ids}")
    assert no_api_ids <= {r["id"] for r in wl["no_api"]}
