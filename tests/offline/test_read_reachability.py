"""Offline proof for spec.read_reachability — the verdict now comes from the
enrichment sidecar's AUTHORITATIVE per-path-param producer (`produced_by` +
`producer_kind`), not the retired capture-var-name heuristic.

These run without network/engine/live model. They pin the verdict precedence
(model-gap > waiver > query-param > cat2-needs-child > cat1-auto) and assert the
real sidecar produces sane, documented counts.
"""
import json
from pathlib import Path

from spec import read_reachability as rr


class _Ep:
    """Minimal catalog Endpoint stand-in (classify reads .http_path + .key)."""
    def __init__(self, http_path, key="cat/svc/op"):
        self.http_path = http_path
        self.key = key


def _classify(http_path, params, rq=None, key="cat/svc/op"):
    sidecar = {key: {p["name"]: p for p in params}}
    required_query = {key: rq} if rq is not None else {}
    return rr.classify(_Ep(http_path, key), sidecar, required_query)["verdict"]


def test_create_producer_is_cat1_auto():
    v = _classify(
        "/v1/things/{thing_id}",
        [{"name": "thing_id", "produced_by": "cat/svc/creatething",
          "producer_kind": "create"}],
    )
    assert v == "cat1-auto"


def test_xsvc_and_lookup_and_detail_are_cat2():
    for kind in ("create-xsvc", "lookup", "detail-read", "async-op"):
        v = _classify(
            "/v1/things/{thing_id}",
            [{"name": "thing_id", "produced_by": "other/svc/x",
              "producer_kind": kind}],
        )
        assert v == "cat2-needs-child", kind


def test_null_no_waiver_is_model_gap():
    v = _classify(
        "/v1/a/{a}/b/{b}",
        [{"name": "a", "produced_by": None, "producer_kind": None},
         {"name": "b", "produced_by": "cat/svc/createb", "producer_kind": "create"}],
    )
    assert v == "model-gap"


def test_null_waiver_is_waiver():
    v = _classify(
        "/v1/tagses/{tags_id}",
        [{"name": "tags_id", "produced_by": None, "producer_kind": "waiver"}],
    )
    assert v == "waiver"


def test_model_gap_dominates_waiver():
    """A genuine gap on one param outranks a waiver on another."""
    v = _classify(
        "/v1/a/{a}/b/{b}",
        [{"name": "a", "produced_by": None, "producer_kind": "waiver"},
         {"name": "b", "produced_by": None, "producer_kind": None}],
    )
    assert v == "model-gap"


def test_required_query_blocks_otherwise_reachable():
    v = _classify(
        "/v1/things/{thing_id}",
        [{"name": "thing_id", "produced_by": "cat/svc/creatething",
          "producer_kind": "create"}],
        rq=["version"],
    )
    assert v == "query-param"


def test_required_query_does_not_rescue_a_gap():
    """model-gap precedes query-param: an unproducible param is the real blocker."""
    v = _classify(
        "/v1/things/{thing_id}",
        [{"name": "thing_id", "produced_by": None, "producer_kind": None}],
        rq=["version"],
    )
    assert v == "model-gap"


def test_real_run_has_sane_documented_distribution():
    """End-to-end against the committed sidecar — counts must sum and the four
    self-param totals (960 produced / 19 waivers / 0 null) must hold."""
    rows_by_service, _skipped = rr.analyze()
    rows = [r for rows in rows_by_service.values() for r in rows]
    assert len(rows) == 302, "id-bound GET universe changed unexpectedly"
    totals = {v: sum(1 for r in rows if r["verdict"] == v) for v in rr._VERDICTS}
    assert sum(totals.values()) == len(rows)
    # every verdict is a recognised bucket
    assert all(r["verdict"] in rr._VERDICTS for r in rows)
    # the heuristic is retired — these symbols must be gone
    assert not hasattr(rr, "near_misses")
    assert not hasattr(rr, "_TRIVIAL")


def test_sidecar_self_params_match_design_invariants():
    """The numbers the change is calibrated against (defends future enrich runs)."""
    data = json.loads(
        (Path(rr.ROOT) / "data" / "api_catalog_params.json").read_text())
    self_total = produced = waiver = null_no_waiver = 0
    for ep in data.values():
        for pp in ep.get("path_params") or []:
            if pp.get("role") != "self":
                continue
            self_total += 1
            if pp.get("produced_by"):
                produced += 1
            elif pp.get("producer_kind") == "waiver":
                waiver += 1
            else:
                null_no_waiver += 1
    assert (self_total, produced, waiver, null_no_waiver) == (979, 960, 19, 0)
