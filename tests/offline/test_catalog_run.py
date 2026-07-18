"""Offline tests for catalog_run — the topology-selection → execution bridge.

Hermetic: catalog_planner.lifecycles_for maps resource nodes (via source.lifecycle)
to the runnable leaf set; plan_for hands that to dag_planner. Unit tests use a
synthetic model; one integration test uses the real catalog.
"""
from __future__ import annotations

from regression.scenarios import catalog_planner, catalog_run, dag_planner


def _model():
    # a<-b<-c chain (c requires b requires a), each tested by its own lifecycle;
    # 'a' tested by a SHARED lifecycle that also tests 'x'.
    return {
        "a": {"requires": [], "source": {"lifecycle": "lc-root"}},
        "b": {"requires": ["a"], "source": {"lifecycle": "lc-b"}},
        "c": {"requires": ["b"], "source": {"lifecycle": "lc-c"}},
        "x": {"requires": [], "source": {"lifecycle": "lc-root"}},  # shares lc with 'a'
        "orphan": {"requires": [], "source": {}},                   # no lifecycle
    }


def test_lifecycles_for_maps_closure_to_source_lifecycles():
    m = _model()
    g = catalog_planner.load_graph(m)
    # target c, full closure {a,b,c} -> lc-root, lc-b, lc-c (deduped, sorted)
    lcs = catalog_planner.lifecycles_for(["c"], graph=g, model=m, enabled_only=False)
    assert lcs == ["lc-b", "lc-c", "lc-root"]


def test_lifecycles_for_no_closure_uses_only_targets():
    m = _model()
    g = catalog_planner.load_graph(m)
    lcs = catalog_planner.lifecycles_for(["c"], graph=g, model=m,
                                         include_closure=False, enabled_only=False)
    assert lcs == ["lc-c"]


def test_lifecycles_for_dedups_shared_lifecycle():
    m = _model()
    g = catalog_planner.load_graph(m)
    # a and x share lc-root -> appears once
    lcs = catalog_planner.lifecycles_for(["a", "x"], graph=g, model=m, enabled_only=False)
    assert lcs == ["lc-root"]


def test_lifecycles_for_skips_nodes_without_lifecycle():
    m = _model()
    g = catalog_planner.load_graph(m)
    lcs = catalog_planner.lifecycles_for(["orphan"], graph=g, model=m, enabled_only=False)
    assert lcs == []


def test_lifecycles_for_enabled_only_filters(monkeypatch):
    m = _model()
    g = catalog_planner.load_graph(m)
    # only lc-c is "enabled" -> the rest are filtered out
    import types
    fake_engine = types.SimpleNamespace(LIFECYCLES=[{"id": "lc-c", "enabled": True},
                                                    {"id": "lc-b", "enabled": False}])
    import regression.scenarios.engine as real_engine  # noqa: F401 (ensure importable)
    monkeypatch.setattr("regression.scenarios.engine.LIFECYCLES", fake_engine.LIFECYCLES, raising=False)
    lcs = catalog_planner.lifecycles_for(["c"], graph=g, model=m, enabled_only=True)
    assert lcs == ["lc-c"]


# ------------------------------------------------------------------ integration
def test_plan_for_ske_cluster_real_catalog():
    """The real chain: ske-cluster → its lifecycles → a dag_planner Plan whose
    waves cover exactly those lifecycles, cap-safe."""
    leaf, plan = catalog_run.plan_for(["ske-cluster"])
    assert "container-ske-cluster-nodepool" in leaf
    # vpc/subnet are shared roots; networking-vpc-subnet self-creates the VPC
    assert "networking-vpc-subnet" in leaf
    assert isinstance(plan, dag_planner.Plan)
    placed = {lid for w in plan.waves for lid in w.lifecycles}
    # every runnable leaf lands in some wave (roots aside)
    assert set(leaf) <= placed
    # ske self-creates its own VPC since the 2026-07-17 conversion (owner:
    # "아예 vpc 생성하고 하는 걸로" — shared-subnet adoption kept deleting the
    # shared subnet) -> lands in the self-create wave, NOT adopt.
    selfc = [lid for w in plan.waves if w.kind == "self-create"
             for lid in w.lifecycles]
    assert "container-ske-cluster-nodepool" in selfc
    adopt = [lid for w in plan.waves if w.kind == "adopt" for lid in w.lifecycles]
    assert "container-ske-cluster-nodepool" not in adopt


def test_leaf_set_for_no_deps_is_subset():
    full = set(catalog_run.leaf_set_for(["ske-cluster"], include_closure=True))
    own = set(catalog_run.leaf_set_for(["ske-cluster"], include_closure=False))
    assert own <= full
