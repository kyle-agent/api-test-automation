"""Offline tests for the ADR 1.0-b closure + topological-wave planner.

Hermetic: synthetic deps/lifecycles for the unit cases (closure correctness,
self-create wave sizing, adopter placement), plus ONE integration case driven by
the real dependencies.json + engine.LIFECYCLES. No client, no network.
"""
from __future__ import annotations


from regression.scenarios import dag_planner, validate_dag


# --------------------------------------------------------------------------- #
# synthetic fixtures (no composition / no network)
# --------------------------------------------------------------------------- #
SYNTH_DEPS = {
    "budget_paths": {"/v1/vpcs": "vpc", "/v1/private-dns": "private-dns"},
    "shared_roots": {
        "vpc": {"parent": None},
        "subnet": {"parent": "vpc"},
        "subnet#db": {"parent": "vpc"},
    },
    "vpc_schedule": {"vpc_limit": 5},
}


def _lc(lid, *, adopt=None, post_paths=None):
    """Build a minimal composed-lifecycle dict for derive()."""
    steps = []
    for a in (adopt or []):
        steps.append({"adopt": a, "method": "POST", "path": "/x"})
    for path in (post_paths or []):
        steps.append({"method": "POST", "path": path})
    return {"id": lid, "enabled": True, "steps": steps}


# an adopter (reuses shared roots) vs a self-creator (POSTs a budget path with
# no same-kind adopt on that step).
ADOPTER_VS = _lc("adopter-vs", adopt=["vpc", "subnet"])
ADOPTER_DB = _lc("adopter-db", adopt=["vpc", "subnet#db"])
SELF_VPC_A = _lc("self-vpc-a", post_paths=["/v1/vpcs"])
SELF_VPC_B = _lc("self-vpc-b", post_paths=["/v1/vpcs"])
SELF_VPC_C = _lc("self-vpc-c", post_paths=["/v1/vpcs"])
SELF_VPC_D = _lc("self-vpc-d", post_paths=["/v1/vpcs"])
SELF_VPC_E = _lc("self-vpc-e", post_paths=["/v1/vpcs"])
SELF_DNS = _lc("self-dns", post_paths=["/v1/private-dns"])  # 0 vpc slots


def _plan(lifecycles, leaf_set=None, vpc_cap=None):
    return dag_planner.plan(leaf_set=leaf_set, deps=SYNTH_DEPS,
                            lifecycles=lifecycles, vpc_cap=vpc_cap)


# --------------------------------------------------------------------------- #
# 1. closure correctness
# --------------------------------------------------------------------------- #
def test_db_only_leafset_needs_vpc_and_db_subnet_not_subnet():
    p = _plan([ADOPTER_DB])
    assert p.shared_roots == ["vpc", "subnet#db"]
    assert "subnet" not in p.shared_roots


def test_vpc_adopter_leafset_needs_vpc_and_subnet():
    p = _plan([ADOPTER_VS])
    assert p.shared_roots == ["vpc", "subnet"]
    assert "subnet#db" not in p.shared_roots


def test_closure_is_union_over_leaf_set():
    p = _plan([ADOPTER_VS, ADOPTER_DB])
    assert set(p.shared_roots) == {"vpc", "subnet", "subnet#db"}


def test_shared_roots_parent_ordered_vpc_first():
    p = _plan([ADOPTER_VS, ADOPTER_DB])
    assert p.shared_roots[0] == "vpc"  # parent before any subnet child
    assert p.shared_roots.index("vpc") < p.shared_roots.index("subnet")
    assert p.shared_roots.index("vpc") < p.shared_roots.index("subnet#db")


def test_ancestor_pulled_in_even_if_only_child_adopted():
    # a leaf adopting only 'subnet#db' still forces 'vpc' into the closure.
    lc = _lc("only-db-subnet", adopt=["subnet#db"])
    p = _plan([lc])
    assert "vpc" in p.shared_roots


# --------------------------------------------------------------------------- #
# 2. adopters land in the adopt wave (parallel)
# --------------------------------------------------------------------------- #
def test_adopters_in_single_adopt_wave():
    p = _plan([ADOPTER_VS, ADOPTER_DB])
    adopt_waves = [w for w in p.waves if w.kind == "adopt"]
    assert len(adopt_waves) == 1
    assert set(adopt_waves[0].lifecycles) == {"adopter-vs", "adopter-db"}
    assert set(p.adopters) == {"adopter-vs", "adopter-db"}


def test_provision_wave_is_first():
    p = _plan([ADOPTER_VS])
    assert p.waves[0].kind == "provision"
    assert p.waves[0].lifecycles == ["vpc", "subnet"]
    assert p.waves[0].vpc_slots == 1


# --------------------------------------------------------------------------- #
# 3. self-creator wave sizing respects the cap
# --------------------------------------------------------------------------- #
def test_self_creators_grouped_into_waves_under_budget():
    # cap 5, shared vpc 1 -> budget 4. Five self-created VPCs -> 4 then 1.
    lcs = [ADOPTER_VS, SELF_VPC_A, SELF_VPC_B, SELF_VPC_C, SELF_VPC_D, SELF_VPC_E]
    p = _plan(lcs, vpc_cap=5)
    assert p.self_create_budget == 4
    sc_waves = [w for w in p.waves if w.kind == "self-create"]
    assert len(sc_waves) == 2
    assert len(sc_waves[0].lifecycles) == 4
    assert len(sc_waves[1].lifecycles) == 1
    for w in sc_waves:
        assert w.vpc_slots <= p.self_create_budget


def test_zero_vpc_self_creator_does_not_consume_a_slot():
    # self-dns (private-dns only) + 4 vpc self-creators must still fit one wave:
    # 4 vpc slots == budget, and the dns one adds 0 slots.
    lcs = [SELF_VPC_A, SELF_VPC_B, SELF_VPC_C, SELF_VPC_D, SELF_DNS]
    p = _plan(lcs, vpc_cap=5)
    sc_waves = [w for w in p.waves if w.kind == "self-create"]
    assert len(sc_waves) == 1
    assert sc_waves[0].vpc_slots == 4
    assert "self-dns" in sc_waves[0].lifecycles


def test_smaller_cap_makes_more_waves():
    # include an adopter so 'vpc' enters the closure and the shared VPC reserves
    # a slot (shared_vpc_count=1); cap 3 -> budget 2.
    lcs = [ADOPTER_VS, SELF_VPC_A, SELF_VPC_B, SELF_VPC_C, SELF_VPC_D]
    p = _plan(lcs, vpc_cap=3)
    assert p.shared_vpc_count == 1
    assert p.self_create_budget == 2
    sc_waves = [w for w in p.waves if w.kind == "self-create"]
    assert len(sc_waves) == 2  # 4 vpc self-creators / 2 per wave
    for w in sc_waves:
        assert w.vpc_slots <= 2


def test_no_shared_vpc_means_full_cap_for_self_creators():
    # a pure self-creator leaf set provisions NO shared VPC, so the whole cap is
    # available (shared_vpc_count=0): budget == cap.
    lcs = [SELF_VPC_A, SELF_VPC_B, SELF_VPC_C, SELF_VPC_D]
    p = _plan(lcs, vpc_cap=3)
    assert p.shared_vpc_count == 0
    assert p.self_create_budget == 3
    sc_waves = [w for w in p.waves if w.kind == "self-create"]
    assert len(sc_waves) == 2  # 4 self-creators / 3 per wave -> 3 + 1
    assert sorted(len(w.lifecycles) for w in sc_waves) == [1, 3]


def test_self_creator_not_in_adopt_wave():
    p = _plan([ADOPTER_VS, SELF_VPC_A])
    adopt_lids = {lid for w in p.waves if w.kind == "adopt" for lid in w.lifecycles}
    assert "self-vpc-a" not in adopt_lids
    assert "self-vpc-a" in p.self_creators


# --------------------------------------------------------------------------- #
# leaf-set defaulting / determinism
# --------------------------------------------------------------------------- #
def test_default_leaf_set_is_all_enabled():
    lcs = [ADOPTER_VS, ADOPTER_DB, SELF_VPC_A]
    p = _plan(lcs, leaf_set=None)
    assert set(p.leaf_set) == {"adopter-vs", "adopter-db", "self-vpc-a"}


def test_leaf_set_restriction_drops_unselected():
    lcs = [ADOPTER_VS, ADOPTER_DB, SELF_VPC_A]
    p = _plan(lcs, leaf_set=["adopter-db"])
    assert p.leaf_set == ["adopter-db"]
    assert p.shared_roots == ["vpc", "subnet#db"]


def test_plan_is_json_serializable():
    p = _plan([ADOPTER_VS, SELF_VPC_A])
    d = p.to_dict()
    import json
    json.loads(json.dumps(d))  # round-trips


def test_format_plan_renders_all_waves():
    p = _plan([ADOPTER_VS, ADOPTER_DB, SELF_VPC_A])
    out = dag_planner.format_plan(p)
    assert "wave 0 (provision)" in out
    assert "adopt" in out
    assert "self-create" in out


# --------------------------------------------------------------------------- #
# integration: real dependencies.json + engine.LIFECYCLES
# --------------------------------------------------------------------------- #
def test_integration_full_enabled_plan():
    deps = validate_dag._load_deps()
    lcs = validate_dag._load_lifecycles()
    p = dag_planner.plan(deps=deps, lifecycles=lcs)

    # closure over ALL enabled covers the six real shared roots (vpc#a/vpc#b =
    # 네트워킹 공유 VPC A/B, tgw = 공유 TGW, 오너 설계 2026-07-13). 불변식은
    # parent-before-child: vpc가 subnet/subnet#db보다 앞. tgw는 자식이 없어 순서
    # 무관(먼저 정렬돼도 무해 — 실 provision은 provision_shared_vpc 자체 순서).
    assert p.shared_roots.index("vpc") < p.shared_roots.index("subnet")
    assert p.shared_roots.index("vpc") < p.shared_roots.index("subnet#db")
    assert set(p.shared_roots) == {"vpc", "subnet", "subnet#db", "vpc#a", "vpc#b", "tgw"}

    # real cap from dependencies.json — 상주 공유 VPC 3개(메인+A+B)가 슬롯에서
    # 상시 차감된다.
    assert p.vpc_cap == deps["vpc_schedule"]["vpc_limit"]
    assert p.shared_vpc_count == 3
    assert p.self_create_budget == p.vpc_cap - 3

    # exactly one provision wave (first) and one adopt wave
    assert p.waves[0].kind == "provision"
    assert sum(1 for w in p.waves if w.kind == "adopt") == 1

    # every self-create wave stays within the vpc-slot budget
    for w in p.waves:
        if w.kind == "self-create":
            assert w.vpc_slots <= p.self_create_budget

    # no lifecycle appears in both adopt and self-create roles
    assert set(p.adopters).isdisjoint(p.self_creators)


def test_integration_db_service_leaf_set():
    deps = validate_dag._load_deps()
    lcs = validate_dag._load_lifecycles()
    leaf = dag_planner._service_leaf_set("mysql", lcs)
    assert "database-mysql-cluster" in leaf
    p = dag_planner.plan(leaf_set=leaf, deps=deps, lifecycles=lcs)
    # DB lifecycles adopt the db subnet, never the plain subnet.
    assert "subnet#db" in p.shared_roots
    assert "subnet" not in p.shared_roots


def test_integration_matches_validate_dag_self_creators():
    # the planner's self_creators must equal validate_dag's derived set.
    deps = validate_dag._load_deps()
    lcs = validate_dag._load_lifecycles()
    derived = validate_dag.derive_all(lcs, deps["budget_paths"])
    expected = {lid for lid, d in derived.items() if d["self_creates"]}
    p = dag_planner.plan(deps=deps, lifecycles=lcs)
    assert set(p.self_creators) == expected
