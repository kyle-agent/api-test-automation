"""Offline tests for regression/scenarios/catalog_planner.

Hermetic (no network, no markers — offline tier). The unit tests drive
``load_graph``/``closure``/``topo_layers``/``plan`` with hand-built SYNTHETIC
model dicts mirroring composer's per-node shape::

    {node_id: {"requires": [...], "service": ..., "quota": ...,
               "adopt": ..., "heavy": ..., "provenance": ...}}

so the graph is fully controlled. ONE integration block exercises the REAL
composed model via ``load_graph()`` and stays resilient to the model growing
(subset / ⊆ assertions, never exact full counts).
"""
from __future__ import annotations

import pytest

from regression.scenarios.catalog_planner import (
    CatalogPlan,
    _requires_ids,
    closure,
    format_plan,
    load_graph,
    plan,
    topo_layers,
)


# ---------------------------------------------------------------------------
# synthetic model builders
# ---------------------------------------------------------------------------

def _node(requires=None, **kw):
    t = {"requires": list(requires or [])}
    t.update(kw)
    return t


def _chain_model():
    """a <- b <- c : c requires b, b requires a, a requires nothing."""
    return {
        "a": _node(),
        "b": _node(["a"]),
        "c": _node(["b"]),
    }


def _diamond_model():
    """d requires b and c; both b and c require a."""
    return {
        "a": _node(),
        "b": _node(["a"]),
        "c": _node(["a"]),
        "d": _node(["b", "c"]),
    }


# ---------------------------------------------------------------------------
# 1. load_graph
# ---------------------------------------------------------------------------

def test_load_graph_drops_dangling_requires():
    model = {
        "a": _node(),
        "b": _node(["a", "ghost"]),  # 'ghost' is not a node
    }
    g = load_graph(model)
    assert set(g) == {"a", "b"}
    # the dangling ref is dropped; only the real dep survives.
    assert g["b"].requires == ["a"]


def test_load_graph_required_by_is_reverse_of_requires():
    g = load_graph(_diamond_model())
    # a is required by b and c (sorted)
    assert g["a"].requires == []
    assert g["a"].required_by == ["b", "c"]
    # b/c require a and are required by d
    assert g["b"].requires == ["a"]
    assert g["b"].required_by == ["d"]
    assert g["c"].requires == ["a"]
    assert g["c"].required_by == ["d"]
    # d is a leaf in the reverse direction
    assert g["d"].requires == ["b", "c"]
    assert g["d"].required_by == []


def test_load_graph_carries_node_fields():
    model = {
        "vpc": _node(quota="vpc", adopt="vpc", service="networking/vpc",
                     provenance="VALIDATED"),
        "big": _node(["vpc"], heavy=True, service="ske/cluster"),
        "plain": _node(),
    }
    g = load_graph(model)
    assert g["vpc"].quota == "vpc"
    assert g["vpc"].adopt == "vpc"
    assert g["vpc"].service == "networking/vpc"
    assert g["vpc"].provenance == "VALIDATED"
    assert g["vpc"].heavy is False
    assert g["big"].heavy is True
    # uncapped / unshared defaults
    assert g["plain"].quota is None
    assert g["plain"].adopt is None
    assert g["plain"].service == ""
    assert g["plain"].provenance == ""


def test_load_graph_requires_dedupes_and_sorts():
    # duplicate + unordered refs are collapsed to a sorted unique list.
    model = {"a": _node(), "b": _node(), "c": _node(["b", "a", "b"])}
    g = load_graph(model)
    assert g["c"].requires == ["a", "b"]


def test_load_graph_skips_non_dict_tasks():
    model = {"a": _node(), "junk": "not-a-dict", "b": _node(["a"])}
    g = load_graph(model)
    assert set(g) == {"a", "b"}


@pytest.mark.parametrize(
    "requires, expected",
    [
        # bare id string
        (["x"], ["x"]),
        # dict {id: ...}
        ([{"id": "x"}], ["x"]),
        # one_of list of bare strings -> contributes every id
        ([{"one_of": ["a", "b"]}], ["a", "b"]),
        # one_of list of {id:...} dicts
        ([{"one_of": [{"id": "p"}, {"id": "q"}]}], ["p", "q"]),
        # 'and'/'any'/'all' keys collect ids too
        ([{"and": ["m", "n"]}], ["m", "n"]),
        # a dict carrying both id and one_of contributes all
        ([{"id": "k", "one_of": ["a", "b"]}], ["k", "a", "b"]),
        # mixed bare + dict
        (["z", {"one_of": ["a", "b"]}], ["z", "a", "b"]),
        # empty / None
        ([], []),
    ],
)
def test_requires_ids_normalization(requires, expected):
    """`_requires_ids` exact behavior: bare ids, {id}, and one_of/and/any/all
    members all contribute their ids (closure stays a safe superset)."""
    assert _requires_ids({"requires": requires}) == expected


def test_requires_ids_none_requires():
    assert _requires_ids({}) == []
    assert _requires_ids({"requires": None}) == []


def test_load_graph_normalizes_dict_requires_into_edges():
    # a one_of entry where both branches exist becomes two real edges.
    model = {
        "a": _node(),
        "b": _node(),
        "c": _node([{"one_of": ["a", "b"]}]),
    }
    g = load_graph(model)
    assert g["c"].requires == ["a", "b"]
    assert g["a"].required_by == ["c"]
    assert g["b"].required_by == ["c"]

    # a one_of branch that doesn't exist is dropped as a dangling edge.
    model2 = {"a": _node(), "c": _node([{"one_of": ["a", "ghost"]}])}
    g2 = load_graph(model2)
    assert g2["c"].requires == ["a"]


# ---------------------------------------------------------------------------
# 2. closure
# ---------------------------------------------------------------------------

def test_closure_chain_includes_all_ancestors():
    g = load_graph(_chain_model())
    assert closure({"c"}, g) == {"a", "b", "c"}


def test_closure_leaf_is_just_itself():
    g = load_graph(_chain_model())
    assert closure({"a"}, g) == {"a"}


def test_closure_ignores_targets_not_in_graph():
    g = load_graph(_chain_model())
    assert closure({"c", "nonexistent"}, g) == {"a", "b", "c"}
    # a target set with ONLY unknown ids yields the empty closure.
    assert closure({"nope"}, g) == set()


def test_closure_diamond():
    g = load_graph(_diamond_model())
    assert closure({"d"}, g) == {"a", "b", "c", "d"}
    # closing on an interior node only pulls its own ancestors.
    assert closure({"b"}, g) == {"a", "b"}


def test_closure_accepts_list_targets():
    g = load_graph(_diamond_model())
    assert closure(["b", "c"], g) == {"a", "b", "c"}


# ---------------------------------------------------------------------------
# 3. topo_layers
# ---------------------------------------------------------------------------

def test_topo_layers_chain():
    g = load_graph(_chain_model())
    assert topo_layers(g) == [["a"], ["b"], ["c"]]


def test_topo_layers_diamond_sorted_band():
    g = load_graph(_diamond_model())
    layers = topo_layers(g)
    assert layers == [["a"], ["b", "c"], ["d"]]
    # the middle band is sorted.
    assert layers[1] == sorted(layers[1])


def test_topo_layers_higher_than_every_requirement():
    """Invariant: a node always lands in a strictly higher layer than anything
    it requires. Asserted over a hand-built irregular DAG."""
    model = {
        "a": _node(),
        "b": _node(),
        "c": _node(["a"]),
        "d": _node(["a", "b"]),
        "e": _node(["c", "d"]),
        "f": _node(["a"]),          # shallow node sharing root a
        "g": _node(["e", "f"]),
    }
    g = load_graph(model)
    layers = topo_layers(g)
    level = {n: i for i, band in enumerate(layers) for n in band}
    for n, node in g.items():
        for r in node.requires:
            assert level[n] > level[r], (
                f"{n} (L{level[n]}) must be strictly above its requirement "
                f"{r} (L{level[r]})")
    # every node placed exactly once
    placed = [n for band in layers for n in band]
    assert sorted(placed) == sorted(g)


def test_topo_layers_cycle_raises():
    model = {"x": _node(["y"]), "y": _node(["x"])}
    g = load_graph(model)
    with pytest.raises(ValueError):
        topo_layers(g)


def test_topo_layers_subset_restricts_layering():
    g = load_graph(_diamond_model())
    # only b and d in subset: b has no in-subset requirement (a excluded), so
    # it is a root; d still requires b (c excluded).
    layers = topo_layers(g, subset={"b", "d"})
    assert layers == [["b"], ["d"]]
    placed = [n for band in layers for n in band]
    assert set(placed) == {"b", "d"}


def test_topo_layers_subset_filters_unknown_ids():
    g = load_graph(_chain_model())
    layers = topo_layers(g, subset={"a", "b", "ghost"})
    assert layers == [["a"], ["b"]]


def test_topo_layers_empty_graph():
    assert topo_layers({}) == []
    assert topo_layers(load_graph(_chain_model()), subset=set()) == []


# ---------------------------------------------------------------------------
# 4. plan
# ---------------------------------------------------------------------------

def _annotated_model():
    """vpc: capped+shared root; subnet: plain; cluster: heavy target."""
    return {
        "vpc": _node(quota="vpc", adopt="vpc", service="networking/vpc"),
        "subnet": _node(["vpc"], service="networking/subnet"),
        "cluster": _node(["subnet"], heavy=True, service="ske/cluster"),
    }


def test_plan_annotations_and_create_order():
    g = load_graph(_annotated_model())
    p = plan(targets=["cluster"], graph=g)
    assert isinstance(p, CatalogPlan)
    # whole chain pulled into closure
    assert p.closure == ["cluster", "subnet", "vpc"]
    # capped {node: quota_kind}
    assert p.capped == {"vpc": "vpc"}
    # shared {node: adopt_token}
    assert p.shared == {"vpc": "vpc"}
    # heavy list
    assert p.heavy == ["cluster"]
    # create_order flattens layers in order: vpc -> subnet -> cluster
    assert p.create_order == ["vpc", "subnet", "cluster"]
    assert p.create_order == [n for band in p.layers for n in band]
    # targets stored sorted
    assert p.targets == ["cluster"]


def test_plan_caps_default_from_budgets():
    g = load_graph(_annotated_model())
    p = plan(targets=["cluster"], graph=g)
    # defaults come from core.budgets.DEFAULT_LIMITS
    from core import budgets
    assert p.caps == dict(budgets.DEFAULT_LIMITS)
    assert p.caps.get("vpc") == 5


def test_plan_caps_passed_override():
    g = load_graph(_annotated_model())
    custom = {"vpc": 99, "private-dns": 7}
    p = plan(targets=["cluster"], graph=g, caps=custom)
    assert p.caps == custom
    # caps is copied, not aliased
    assert p.caps is not custom


def test_plan_default_targets_whole_graph():
    g = load_graph(_annotated_model())
    p = plan(graph=g)  # no targets -> whole catalog
    assert p.targets == ["cluster", "subnet", "vpc"]
    assert set(p.closure) == {"cluster", "subnet", "vpc"}


def test_plan_filters_unknown_targets():
    g = load_graph(_annotated_model())
    p = plan(targets=["cluster", "bogus"], graph=g)
    assert p.targets == ["cluster"]
    assert "bogus" not in p.closure


def test_plan_to_dict_roundtrip():
    g = load_graph(_annotated_model())
    p = plan(targets=["cluster"], graph=g)
    d = p.to_dict()
    assert d["targets"] == p.targets
    assert d["closure"] == p.closure
    assert d["layers"] == p.layers
    assert d["capped"] == p.capped
    assert d["shared"] == p.shared
    assert d["heavy"] == p.heavy
    assert d["caps"] == p.caps


def test_plan_no_annotations_when_plain():
    g = load_graph(_chain_model())
    p = plan(targets=["c"], graph=g)
    assert p.capped == {}
    assert p.shared == {}
    assert p.heavy == []


def test_format_plan_smoke():
    g = load_graph(_annotated_model())
    p = plan(targets=["cluster"], graph=g)
    text = format_plan(p, graph=g)
    assert "catalog plan:" in text
    assert "account caps:" in text
    # capped/shared/heavy markers surface in the rendered bands
    assert "vpc" in text
    assert "L0" in text
    # heavy marker (△) appears for the heavy node when graph is supplied
    assert "cluster" in text


# ---------------------------------------------------------------------------
# 5. integration — REAL composed model (resilient to growth)
# ---------------------------------------------------------------------------

SKE_CLOSURE = {
    "ske-cluster", "vpc", "subnet", "security-group", "keypair",
    "filestorage-volume", "kubernetes-version",
}


def test_integration_ske_cluster_closure_and_layering():
    g = load_graph()  # real model
    assert "ske-cluster" in g, "real model missing ske-cluster"
    p = plan(targets=["ske-cluster"], graph=g)
    # exact closure for this target today
    assert set(p.closure) == SKE_CLOSURE
    # vpc is a root (layer 0); ske-cluster is in the final layer
    assert "vpc" in p.layers[0]
    assert "ske-cluster" in p.layers[-1]
    # vpc is flagged capped
    assert p.capped.get("vpc") == "vpc"
    # invariant holds on the real subgraph too
    level = {n: i for i, band in enumerate(p.layers) for n in band}
    for n in p.closure:
        for r in g[n].requires:
            if r in level:
                assert level[n] > level[r]


def test_integration_full_plan_capped_and_acyclic():
    g = load_graph()
    # full plan must not raise (model is a DAG)
    p = plan(graph=g)
    # vpc and private-dns are capped in the full closure (⊆, resilient to growth)
    assert {"vpc", "private-dns"} <= set(p.capped)
    # caps carry the account limits
    assert p.caps.get("vpc") == 5
    # create_order is a permutation of the closure
    assert sorted(p.create_order) == sorted(p.closure)
    # every requirement edge respects layer order
    level = {n: i for i, band in enumerate(p.layers) for n in band}
    for n in p.closure:
        for r in g[n].requires:
            assert level[n] > level[r]


def test_integration_load_graph_default_is_nonempty():
    g = load_graph()
    assert len(g) > 50  # generously below the ~275 real node count
    # required_by reverse edges are consistent with requires on the real graph
    for nid, node in g.items():
        for r in node.requires:
            assert nid in g[r].required_by
