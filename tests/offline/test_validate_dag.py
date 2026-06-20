"""Offline tests for ``regression.scenarios.validate_dag`` (scheduler ADR 1.0-a).

validate_dag derives the scheduler DAG (which lifecycles adopt which shared
upstream roots, and which self-create a capped resource) from the composed
lifecycle dicts, and proves ``dependencies.json`` declares those edges exactly.

These tests run in the OFFLINE tier: no network, no markers, no lifecycle
execution. They exercise three layers:

  1. ``derive()`` — the per-step classification of adopt edges vs self-create
     slots, including the subtle ADOPT-FALLBACK case (a ``POST /v1/vpcs`` that
     also carries ``{"adopt":"vpc"}`` is an EDGE, not a slot).
  2. ``build_report()`` — gap detection against a synthetic ``deps`` dict.
  3. The REAL graph regression guard: the live lifecycles + the committed
     ``dependencies.json`` must form a COMPLETE DAG (.ok / gap_count==0), so
     adding an adopt step without updating dependencies.json fails CI here.
"""
import json
from pathlib import Path

from regression.scenarios import validate_dag as vd


# A minimal budget_paths mirroring the real dependencies.json: a POST to one of
# these paths "spends" a cap slot of the mapped kind (unless that same step also
# adopts the kind, which makes it the engine's adopt-fallback create — an edge).
BUDGET_PATHS = {"/v1/vpcs": "vpc", "/v1/private-dns": "private-dns"}


def _lc(lid, steps, *, enabled=True):
    """Build a fake composed-lifecycle dict with just the fields derive() reads."""
    return {"id": lid, "enabled": enabled, "steps": steps}


def _post(path):
    return {"method": "POST", "path": path}


def _post_adopt(path, kind):
    """A POST that ALSO adopts a kind on the same step (the adopt-fallback shape)."""
    return {"method": "POST", "path": path, "adopt": kind}


def _adopt(kind):
    """A non-budget step that adopts a shared root (e.g. a create-subnet step)."""
    return {"adopt": kind, "method": "POST", "path": "/v1/something/else"}


# ---------------------------------------------------------------------------
# 1. derive() classification
# ---------------------------------------------------------------------------

def test_derive_pure_self_creator():
    """A plain POST /v1/vpcs with no adopt -> consumes a vpc slot, adopts nothing.

    This is a vpc_crud lifecycle: it genuinely provisions the VPC, so the
    cap-aware scheduler must serialize it against the vpc budget."""
    sc, ad = vd.derive(_lc("pure-create", [_post("/v1/vpcs")]), BUDGET_PATHS)
    assert sc == {"vpc"}
    assert ad == set()


def test_derive_pure_adopter():
    """An adopt:vpc step with NO budget POST -> an edge, no slot consumed.

    This is an adopt-class lifecycle reusing the shared VPC; it never creates
    one, so it is safe to run in parallel."""
    sc, ad = vd.derive(_lc("pure-adopt", [_adopt("vpc")]), BUDGET_PATHS)
    assert ad == {"vpc"}
    assert sc == set()


def test_derive_adopt_fallback_is_edge_not_slot():
    """THE KEY NUANCE: a POST /v1/vpcs that ALSO carries {"adopt":"vpc"} on the
    SAME step is the engine's adopt-fallback (IB-049 skips it under xdist), so it
    is an EDGE (adopts vpc) and NOT a self-create slot.

    If derive() got this wrong it would mark every adopter as a slot-consumer and
    the scheduler would needlessly serialize the whole parallel adopt class."""
    sc, ad = vd.derive(_lc("adopt-fallback", [_post_adopt("/v1/vpcs", "vpc")]),
                       BUDGET_PATHS)
    assert ad == {"vpc"}
    assert sc == set()


def test_derive_both_adopt_and_separate_self_create():
    """A lifecycle with an adopt:vpc step AND a SEPARATE non-adopt POST /v1/vpcs
    (the vpc-peering shape: it reuses the shared VPC but also stands up a 2nd VPC
    to peer with) -> it BOTH adopts vpc AND self-creates a vpc slot.

    The adopt-fallback suppression is per-step, so a distinct plain POST still
    counts as a real create."""
    lc = _lc("vpc-peering-like", [_adopt("vpc"), _post("/v1/vpcs")])
    sc, ad = vd.derive(lc, BUDGET_PATHS)
    assert ad == {"vpc"}
    assert sc == {"vpc"}


def test_derive_subnet_db_base_kind_recorded_verbatim():
    """An adopt of 'subnet#db' is recorded VERBATIM in adopts (the edge keeps the
    qualified token), while _base_kind() only strips the suffix for the slot
    comparison. DB lifecycles adopt the dedicated DB subnet, a distinct root."""
    sc, ad = vd.derive(_lc("db-like", [_adopt("subnet#db")]), BUDGET_PATHS)
    assert ad == {"subnet#db"}
    assert sc == set()
    # sanity: the helper strips the qualifier for budget-kind matching only.
    assert vd._base_kind("subnet#db") == "subnet"


def test_derive_non_budget_post_ignored():
    """A POST to a path NOT in budget_paths consumes no slot (only capped
    resources matter to the scheduler)."""
    sc, ad = vd.derive(_lc("misc", [_post("/v1/keypairs")]), BUDGET_PATHS)
    assert sc == set()
    assert ad == set()


def test_derive_all_skips_disabled_and_sorts():
    """derive_all only includes ENABLED lifecycles and returns sorted lists."""
    lcs = [
        _lc("on", [_adopt("vpc"), _adopt("subnet")]),
        _lc("off", [_post("/v1/vpcs")], enabled=False),
    ]
    out = vd.derive_all(lcs, BUDGET_PATHS)
    assert set(out) == {"on"}                       # disabled dropped
    assert out["on"]["adopts"] == ["subnet", "vpc"]  # sorted
    assert out["on"]["self_creates"] == []


# ---------------------------------------------------------------------------
# 2. build_report() gap detection on a synthetic deps dict
# ---------------------------------------------------------------------------

def _consistent_deps():
    """A tiny deps dict that exactly matches the lifecycles below -> a complete DAG."""
    return {
        "budget_paths": dict(BUDGET_PATHS),
        "adopt_edges": {"adopter": ["vpc"]},
        "shared_roots": {"vpc": {"parent": None}},
    }


def _consistent_lifecycles():
    return [_lc("adopter", [_adopt("vpc")])]


def test_build_report_consistent_is_ok():
    """deps that declares exactly the derived edges + roots -> .ok True, 0 gaps."""
    r = vd.build_report(_consistent_lifecycles(), _consistent_deps())
    assert r.ok is True
    assert r.gap_count == 0
    assert r.adopt_missing == []
    assert r.adopt_extra == []
    assert r.root_undefined == []


def test_build_report_adopt_missing_when_edge_undeclared():
    """A lifecycle adopts vpc but adopt_edges omits it -> adopt_missing, not ok.

    This is the core regression guard: forgetting to record a new adopt edge."""
    deps = _consistent_deps()
    deps["adopt_edges"] = {}  # forgot to declare adopter's edge
    r = vd.build_report(_consistent_lifecycles(), deps)
    assert r.ok is False
    assert r.gap_count == 1
    # tuple shape is (lid, derived_sorted, declared_sorted)
    assert r.adopt_missing == [("adopter", ["vpc"], [])]


def test_build_report_adopt_extra_for_non_enabled_lifecycle():
    """adopt_edges declaring an id that isn't an enabled lifecycle -> adopt_extra.

    Catches STALE edges left behind after a lifecycle is removed/disabled."""
    deps = _consistent_deps()
    deps["adopt_edges"]["ghost"] = ["vpc"]  # no such enabled lifecycle
    r = vd.build_report(_consistent_lifecycles(), deps)
    assert r.ok is False
    assert ("ghost", ["vpc"]) in r.adopt_extra
    # 'adopter' itself is still consistent, so the only gap is the stale extra.
    assert r.adopt_missing == []
    assert r.gap_count == 1


def test_build_report_root_undefined_when_root_absent():
    """An adopted root missing from shared_roots -> root_undefined, not ok."""
    deps = _consistent_deps()
    deps["shared_roots"] = {}  # vpc root not defined
    r = vd.build_report(_consistent_lifecycles(), deps)
    assert r.ok is False
    assert ("adopter", "vpc") in r.root_undefined
    assert r.gap_count == 1


def test_build_report_self_creators_reported_but_not_a_gap():
    """A self-creator is scheduler INPUT (slot-consumer), not a DAG gap: it shows
    up in r.self_creators yet keeps .ok True when edges/roots are consistent AND
    the VPC self-creator is declared in the serial-lane partition list."""
    deps = _consistent_deps()
    deps["adopt_edges"] = {}                     # the creator adopts nothing
    # a VPC self-creator must be in vpc_crud_lifecycles or it's a partition gap
    # (would race the cap in the parallel lane) — declare it so this stays clean.
    deps["vpc_schedule"] = {"vpc_crud_lifecycles": ["creator"]}
    lcs = [_lc("creator", [_post("/v1/vpcs")])]  # pure self-create, no adopt
    r = vd.build_report(lcs, deps)
    assert r.ok is True                          # no adopt edges to mismatch
    assert r.gap_count == 0
    assert r.self_creators == {"creator": ["vpc"]}


def test_build_report_vpc_self_creator_missing_from_vpc_crud_is_a_gap():
    """A VPC self-creator absent from vpc_schedule.vpc_crud_lifecycles is a
    partition gap: in the pre-cutover workflow it would land in the parallel lane
    and race the account VPC cap. validate_dag must catch that drift."""
    deps = _consistent_deps()
    deps["adopt_edges"] = {}
    deps["vpc_schedule"] = {"vpc_crud_lifecycles": []}   # creator NOT listed
    lcs = [_lc("creator", [_post("/v1/vpcs")])]
    r = vd.build_report(lcs, deps)
    assert r.ok is False
    assert ("creator",) in r.vpc_crud_missing
    assert r.gap_count == 1


def test_build_report_shared_roots_dependents_aggregated():
    """The informational shared_roots map lists every dependent of a root."""
    deps = {
        "budget_paths": dict(BUDGET_PATHS),
        "adopt_edges": {"a": ["vpc"], "b": ["vpc"]},
        "shared_roots": {"vpc": {"parent": None}},
    }
    lcs = [_lc("a", [_adopt("vpc")]), _lc("b", [_adopt("vpc")])]
    r = vd.build_report(lcs, deps)
    assert r.ok is True
    assert sorted(r.shared_roots["vpc"]) == ["a", "b"]


# ---------------------------------------------------------------------------
# 3. REAL graph regression guard
# ---------------------------------------------------------------------------

def _real_deps():
    deps_path = Path(vd.__file__).resolve().parent / "dependencies.json"
    return json.loads(deps_path.read_text())


def test_real_graph_is_a_complete_dag():
    """The committed dependencies.json must declare EXACTLY the edges/roots the
    live lifecycles derive -> .ok True, 0 gaps.

    THIS is the guard that fails if someone adds an {"adopt":...} step (or a new
    adopt lifecycle) without running `validate_dag --check` and updating
    dependencies.json. If it fails, regenerate per the _dag_comment in the json."""
    from regression.scenarios import engine
    r = vd.build_report(list(engine.LIFECYCLES), _real_deps())
    # surface the offending entries in the failure message for fast triage.
    assert r.ok, (
        f"dependencies.json is not a complete DAG: {r.gap_count} gap(s)\n"
        f"{vd.format_report(r)}"
    )
    assert r.gap_count == 0


# ---------------------------------------------------------------------------
# 4. main(--check) on the real (complete) graph
# ---------------------------------------------------------------------------

def test_main_check_returns_zero_on_complete_graph(capsys):
    """`main(['--check'])` exits 0 when the real graph is complete (CI gate green)."""
    rc = vd.main(["--check"])
    assert rc == 0
    out = capsys.readouterr().out
    assert "COMPLETE DAG" in out  # the success banner format_report emits


def test_main_json_mode_emits_derived_edges(capsys):
    """`main(['--json'])` prints the derived edges as JSON and returns 0."""
    rc = vd.main(["--json"])
    assert rc == 0
    payload = json.loads(capsys.readouterr().out)
    # every enabled lifecycle entry carries the two derived signals.
    assert payload
    sample = next(iter(payload.values()))
    assert set(sample) == {"self_creates", "adopts"}
