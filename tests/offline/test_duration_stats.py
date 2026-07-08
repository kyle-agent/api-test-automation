"""Offline tests for tools.duration_stats (HEAVY-PREMISE contract §3, WP2).

Hermetic: every test synthesizes its own ``*.events.jsonl`` under tmp_path with
known timestamps — no dependency on the real ``reports/console2-runs`` content
and no network. One optional integration test at the bottom runs against the
real corpus when live event files are present (skipped otherwise).

Pinned behaviors:
  * fold = per-lifecycle first-event → lifecycle-end **timestamp gap** — never
    the step-end ``elapsed_ms`` call latency (contract: a wait step records
    ~1.2s elapsed_ms while really occupying ~40min of wall time);
  * simulate runs (run-meta mode=="simulate") are excluded from the fold;
  * passed-status samples preferred, failed used only when no passed exist;
  * contract §3 class defaults (read 30s / small-create 120s / cluster 2400s);
  * ``estimate()`` fixed return keys + basis mixing + the makespan bound
    ``max(longest, total/parallel)``.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from tools import duration_stats as ds


# --------------------------------------------------------------------------- #
# synthesis helpers
# --------------------------------------------------------------------------- #
def _write_events(path: Path, events: list[dict]) -> None:
    path.write_text("".join(json.dumps(e) + "\n" for e in events), encoding="utf-8")


def _lifecycle_events(lc: str, t0: float, t1: float, *, status: str = "passed",
                      elapsed_ms: float = 1.2) -> list[dict]:
    """A minimal realistic lifecycle: start → step-start/step-end → end.

    The step-end deliberately carries a tiny ``elapsed_ms`` while the ts gap is
    large — folding must trust the ts gap, never elapsed_ms.
    """
    return [
        {"ts": t0, "kind": "lifecycle-start", "lifecycle": lc, "service": "svc",
         "heavy": False, "n_steps": 2},
        {"ts": t0, "kind": "step-start", "lifecycle": lc, "step": "create",
         "method": "POST", "path": "/v1/things"},
        {"ts": t1 - 1.0, "kind": "step-end", "lifecycle": lc, "step": "create",
         "method": "POST", "path": "/v1/things", "status": 200,
         "category": "ok", "elapsed_ms": elapsed_ms},
        {"ts": t1, "kind": "lifecycle-end", "lifecycle": lc, "status": status,
         "failed_groups": [], "reason": None},
    ]


def _run_file(tmp: Path, name: str, events: list[dict]) -> Path:
    p = tmp / f"{name}.events.jsonl"
    _write_events(p, events)
    return p


# --------------------------------------------------------------------------- #
# fold_events
# --------------------------------------------------------------------------- #
def test_fold_wall_time_is_ts_gap_not_elapsed_ms(tmp_path):
    # 40 minutes of wall between first event and lifecycle-end, but the only
    # step-end says elapsed_ms=1.2 — the contract's poster case.
    _run_file(tmp_path, "r1",
              _lifecycle_events("waity", 1000.0, 1000.0 + 2400.0, elapsed_ms=1.2))
    stats = ds.fold_events(tmp_path)
    assert stats["waity"]["p50_s"] == pytest.approx(2400.0)
    assert stats["waity"]["p90_s"] == pytest.approx(2400.0)
    assert stats["waity"]["n_runs"] == 1


def test_fold_multiple_lifecycles_and_runs_percentiles(tmp_path):
    # lifecycle "a": walls 10, 20, 30 across three runs -> p50=20, p90=28
    # (linear interpolation at 0.9*(3-1)=1.8 -> 20 + 0.8*10).
    _run_file(tmp_path, "r1", _lifecycle_events("a", 100.0, 110.0)
              + _lifecycle_events("b", 100.0, 105.0))
    _run_file(tmp_path, "r2", _lifecycle_events("a", 200.0, 220.0))
    _run_file(tmp_path, "r3", _lifecycle_events("a", 300.0, 330.0))
    stats = ds.fold_events(tmp_path)
    assert stats["a"]["n_runs"] == 3
    assert stats["a"]["p50_s"] == pytest.approx(20.0)
    assert stats["a"]["p90_s"] == pytest.approx(28.0)
    assert stats["b"] == {"p50_s": pytest.approx(5.0), "p90_s": pytest.approx(5.0),
                          "n_runs": 1}


def test_fold_excludes_simulate_runs(tmp_path):
    sim = [{"ts": 50.0, "kind": "run-meta", "mode": "simulate", "waves": 1,
            "runnable": ["a"]}] + _lifecycle_events("a", 50.0, 50.0)
    _run_file(tmp_path, "sim", sim)
    _run_file(tmp_path, "live", _lifecycle_events("a", 100.0, 160.0))
    stats = ds.fold_events(tmp_path)
    assert stats["a"]["n_runs"] == 1          # only the live run counted
    assert stats["a"]["p50_s"] == pytest.approx(60.0)


def test_fold_prefers_passed_samples_falls_back_to_failed(tmp_path):
    # "mostly": one passed (10s) + one early-abort failure (2s) -> passed only.
    _run_file(tmp_path, "r1", _lifecycle_events("mostly", 0.0, 10.0)
              + _lifecycle_events("failonly", 0.0, 3961.0, status="failed"))
    _run_file(tmp_path, "r2",
              _lifecycle_events("mostly", 0.0, 2.0, status="failed"))
    stats = ds.fold_events(tmp_path)
    assert stats["mostly"] == {"p50_s": pytest.approx(10.0),
                               "p90_s": pytest.approx(10.0), "n_runs": 1}
    # "failonly" has no passed run -> its failed wall is used (beats a default).
    assert stats["failonly"]["p50_s"] == pytest.approx(3961.0)


def test_fold_ignores_unfinished_empty_and_malformed(tmp_path):
    # no lifecycle-end (crashed run) -> no sample; empty + junk files skipped.
    _run_file(tmp_path, "crash", _lifecycle_events("x", 0.0, 100.0)[:-1])
    (tmp_path / "empty.events.jsonl").write_text("", encoding="utf-8")
    (tmp_path / "junk.events.jsonl").write_text("not json\n{}\n", encoding="utf-8")
    assert ds.fold_events(tmp_path) == {}


def test_fold_memo_invalidates_on_new_file(tmp_path):
    _run_file(tmp_path, "r1", _lifecycle_events("a", 0.0, 10.0))
    assert ds.fold_events(tmp_path)["a"]["n_runs"] == 1
    _run_file(tmp_path, "r2", _lifecycle_events("a", 0.0, 20.0))
    assert ds.fold_events(tmp_path)["a"]["n_runs"] == 2


# --------------------------------------------------------------------------- #
# class inference + defaults
# --------------------------------------------------------------------------- #
def _lc(lid, steps, **extra):
    return {"id": lid, "service": extra.pop("service", "svc"),
            "steps": steps, **extra}


READ_LC = _lc("gen-x-reads", [
    {"name": "l1", "method": "GET", "path": "/v1/a", "expect_status": [200]},
    {"name": "l2", "method": "GET", "path": "/v1/b",
     "expect_status": [200, 400, 403, 404], "optional": True},
])
SMALL_LC = _lc("iam-policy", [
    {"name": "create", "method": "POST", "path": "/v1/p",
     "expect_status": [200, 201, 202]},
    {"name": "delete", "method": "DELETE", "path": "/v1/p/{id}",
     "expect_status": [200, 204]},
])
HEAVY_LC = _lc("compute-x-full", [
    {"name": "create", "method": "POST", "path": "/v1/servers",
     "expect_status": [200, 201, 202]},
], heavy=True, service="virtualserver")
KW_LC = _lc("database-x-cluster", [
    {"name": "create", "method": "POST", "path": "/v1/clusters",
     "expect_status": [201]},
])  # no heavy flag — "cluster" keyword carries it
PROBE_LC = _lc("x-subops-guarded", [
    {"name": "poke", "method": "POST", "path": "/v1/x",
     "expect_status": [200, 400, 403, 404, 422]},
])  # tolerant-only writes: probe-like -> small-create, never cluster-grade


def test_classify_lifecycle_classes():
    assert ds.classify_lifecycle(READ_LC) == "read"
    assert ds.classify_lifecycle(SMALL_LC) == "small-create"
    assert ds.classify_lifecycle(HEAVY_LC) == "cluster-grade"
    assert ds.classify_lifecycle(KW_LC) == "cluster-grade"
    assert ds.classify_lifecycle(PROBE_LC) == "small-create"
    assert ds.classify_lifecycle(None) == "small-create"      # unknown id


def test_class_defaults_match_contract():
    assert ds.CLASS_DEFAULT_S == {"read": 30.0, "small-create": 120.0,
                                  "cluster-grade": 2400.0}


# --------------------------------------------------------------------------- #
# estimate() — contract keys, basis mixing, makespan bound
# --------------------------------------------------------------------------- #
def _model(tmp_path, lifecycles, **extra):
    return {"lifecycles": lifecycles, "events_dir": str(tmp_path), **extra}


def test_estimate_contract_keys_and_defaults(tmp_path):
    model = _model(tmp_path, [READ_LC, SMALL_LC, HEAVY_LC])
    est = ds.estimate([READ_LC["id"], SMALL_LC["id"], HEAVY_LC["id"]], model)
    assert set(est) == {"p50_s", "p90_s", "basis", "per_lifecycle"}
    assert est["basis"] == "default"
    per = est["per_lifecycle"]
    assert per[READ_LC["id"]]["p50_s"] == 30
    assert per[SMALL_LC["id"]]["p50_s"] == 120
    assert per[HEAVY_LC["id"]]["p50_s"] == 2400
    for entry in per.values():
        assert {"p50_s", "basis"} <= set(entry)          # contract per-lc keys
        assert isinstance(entry["p50_s"], int)
        assert entry["basis"] == "default"
    # makespan: sum/4=637.5 < longest 2400 -> 2400; p90 = 2400*1.5 = 3600
    assert est["p50_s"] == 2400 and isinstance(est["p50_s"], int)
    assert est["p90_s"] == 3600


def test_estimate_measured_basis_and_values(tmp_path):
    _run_file(tmp_path, "r1", _lifecycle_events("a", 0.0, 100.0))
    est = ds.estimate(["a"], _model(tmp_path, [_lc("a", [])]))
    assert est["basis"] == "measured"
    assert est["per_lifecycle"]["a"] == {"p50_s": 100, "p90_s": 100,
                                         "basis": "measured"}
    assert est["p50_s"] == 100 and est["p90_s"] == 100


def test_estimate_mixed_basis(tmp_path):
    _run_file(tmp_path, "r1", _lifecycle_events("a", 0.0, 50.0))
    est = ds.estimate(["a", READ_LC["id"]], _model(tmp_path, [READ_LC]))
    assert est["basis"] == "mixed"
    assert est["per_lifecycle"]["a"]["basis"] == "measured"
    assert est["per_lifecycle"][READ_LC["id"]]["basis"] == "default"


def test_estimate_makespan_parallel_bound(tmp_path):
    # 5 measured lifecycles of 100s each: longest=100, sum/parallel=500/4=125
    # -> makespan 125 (the sum/parallel term dominates).
    evs = []
    for i in range(5):
        evs += _lifecycle_events(f"lc{i}", 1000.0 * i, 1000.0 * i + 100.0)
    _run_file(tmp_path, "r1", evs)
    ids = [f"lc{i}" for i in range(5)]
    est = ds.estimate(ids, _model(tmp_path, []))
    assert est["p50_s"] == 125
    # admission override: parallel=5 -> max(100, 500/5)=100
    est5 = ds.estimate(ids, _model(tmp_path, [], parallel=5))
    assert est5["p50_s"] == 100
    # parallel capped by len(ids): 2 ids, parallel=4 -> max(100, 200/2)=100
    est2 = ds.estimate(ids[:2], _model(tmp_path, []))
    assert est2["p50_s"] == 100


def test_estimate_unknown_id_and_dict_model_shape(tmp_path):
    # console2 _model() carries lifecycles as {id: dict}; unknown ids fall back
    # to the small-create default.
    model = {"lifecycles": {READ_LC["id"]: READ_LC},
             "events_dir": str(tmp_path)}
    est = ds.estimate([READ_LC["id"], "never-seen"], model)
    assert est["per_lifecycle"][READ_LC["id"]]["p50_s"] == 30
    assert est["per_lifecycle"]["never-seen"] == {"p50_s": 120, "p90_s": 180,
                                                  "basis": "default"}


def test_estimate_empty_selection(tmp_path):
    est = ds.estimate([], _model(tmp_path, []))
    assert est == {"p50_s": 0, "p90_s": 0, "basis": "default",
                   "per_lifecycle": {}}


# --------------------------------------------------------------------------- #
# cache (reports/ only)
# --------------------------------------------------------------------------- #
def test_refresh_cache_writes_under_reports_only(tmp_path):
    _run_file(tmp_path, "r1", _lifecycle_events("a", 0.0, 10.0))
    target = ds.ROOT / "reports" / "duration_stats_cache.test.json"
    try:
        payload = ds.refresh_cache(tmp_path, target)
        on_disk = json.loads(target.read_text(encoding="utf-8"))
        assert on_disk["stats"]["a"]["p50_s"] == 10.0
        assert payload["n_lifecycles_measured"] == 1
    finally:
        target.unlink(missing_ok=True)
    with pytest.raises(ValueError):
        ds.refresh_cache(tmp_path, tmp_path / "outside.json")
    with pytest.raises(ValueError):
        ds.refresh_cache(tmp_path, ds.ROOT / "data" / "nope.json")


# --------------------------------------------------------------------------- #
# optional integration against the real corpus (skipped when absent)
# --------------------------------------------------------------------------- #
def test_real_corpus_integration_if_present():
    if not ds.EVENTS_DIR.is_dir():
        pytest.skip("no reports/console2-runs directory")
    stats = ds.fold_events(ds.EVENTS_DIR)
    if "compute-virtualserver-full" not in stats:
        pytest.skip("no live compute-virtualserver-full run in local history")
    st = stats["compute-virtualserver-full"]
    # sanity: a real VS full lifecycle is tens of minutes, not seconds
    assert 600 <= st["p50_s"] <= 6000
    est = ds.estimate(["compute-virtualserver-full"])
    assert est["basis"] == "measured"
    assert est["p50_s"] == int(round(st["p50_s"]))
