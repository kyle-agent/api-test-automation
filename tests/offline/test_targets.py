"""Offline tests for regression/scenarios/targets.py (M6b/M6c).

``expand_targets`` is a thin selector layer over the real resource model
(``composer.load_model()``); these tests assert the selector -> node-id
contract directly against that model (no network, no composition).
"""
from __future__ import annotations

import pytest

from regression.scenarios import composer
from regression.scenarios.targets import expand_targets


@pytest.fixture(scope="module")
def model():
    return composer.load_model()


# --- helpers mirroring the selector predicates (independent of targets.py) --

def _group_of(task):
    g = task.get("group")
    if g:
        return g
    code = task.get("code") or ""
    parts = code.split("-")
    return "-".join(parts[:2]) if len(parts) >= 2 else None


def _has_delete(task):
    return bool((task.get("delete") or {}).get("endpoint"))


# --- service: -------------------------------------------------------------

def test_service_returns_exactly_that_services_nodes(model):
    got = expand_targets("service:networking/vpc", model=model)
    expected = sorted(n for n, t in model.items()
                      if t.get("service") == "networking/vpc")
    assert got == expected
    assert "vpc" in got and "subnet" in got
    # nothing from another service leaked in
    assert all(model[n].get("service") == "networking/vpc" for n in got)


def test_service_unknown_raises(model):
    with pytest.raises(ValueError) as exc:
        expand_targets("service:networking/does-not-exist", model=model)
    assert "unknown service" in str(exc.value)
    # error lists valid values
    assert "networking/vpc" in str(exc.value)


# --- group: ---------------------------------------------------------------

def test_group_matches(model):
    got = expand_targets("group:ct-ske", model=model)
    expected = sorted(n for n, t in model.items() if _group_of(t) == "ct-ske")
    assert got == expected
    assert "ske-cluster" in got
    assert all(_group_of(model[n]) == "ct-ske" for n in got)


def test_group_unknown_raises(model):
    with pytest.raises(ValueError) as exc:
        expand_targets("group:zz-nope", model=model)
    assert "unknown group" in str(exc.value)


# --- theme: ---------------------------------------------------------------

def test_theme_read_only_excludes_delete_bearing_and_includes_lookups(model):
    got = expand_targets("theme:read-only", model=model)
    # no node in the read-only set has a delete endpoint
    assert all(not _has_delete(model[n]) for n in got)
    # a delete-bearing node is excluded
    assert "vpc" not in got  # vpc has a delete (crud)
    # known lookup nodes are included
    assert "kubernetes-version" in got
    assert "image" in got


def test_theme_crud_includes_delete_bearing(model):
    got = expand_targets("theme:crud", model=model)
    assert all(_has_delete(model[n]) for n in got)
    assert "vpc" in got
    # read-only and crud partition the model
    ro = set(expand_targets("theme:read-only", model=model))
    assert ro.isdisjoint(got)
    assert ro | set(got) == set(model)


def test_theme_heavy_includes_server_and_ske_cluster(model):
    got = expand_targets("theme:heavy", model=model)
    assert "server" in got
    assert "ske-cluster" in got
    assert all(model[n].get("heavy") for n in got)


def test_theme_vary(model):
    got = expand_targets("theme:vary", model=model)

    def is_vary(t):
        opts = ((t.get("create") or {}).get("options")) or {}
        return any(isinstance(s, dict) and s.get("vary") for s in opts.values())

    assert got == sorted(n for n, t in model.items() if is_vary(t))
    assert got, "expected at least one vary node in the model"


def test_theme_unknown_raises(model):
    with pytest.raises(ValueError) as exc:
        expand_targets("theme:bogus", model=model)
    assert "unknown theme" in str(exc.value)


# --- all ------------------------------------------------------------------

def test_all_returns_full_model_node_count(model):
    got = expand_targets("all", model=model)
    assert len(got) == len(model)
    assert set(got) == set(model)


# --- bare node id + composition of clauses --------------------------------

def test_bare_node_id(model):
    assert expand_targets("vpc", model=model) == ["vpc"]


def test_unknown_bare_node_id_raises(model):
    with pytest.raises(ValueError):
        expand_targets("not-a-real-node", model=model)


def test_multi_clause_union_sorted_deduped(model):
    got = expand_targets("vpc, subnet vpc", model=model)
    assert got == ["subnet", "vpc"]  # sorted, deduped (vpc given twice)


def test_result_is_sorted_and_deduped(model):
    got = expand_targets("group:nw-vpc, service:networking/vpc", model=model)
    assert got == sorted(set(got))
