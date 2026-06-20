"""Offline tests for dag_diff — ADR 1.0-d xdist-vs-runner validation harness.

Hermetic: a tiny junit XML written to tmp_path + a real dag_runner.RunResult
built from LifecycleOutcome/WaveResult. Stdlib only, no client, no network.
"""
from __future__ import annotations

from regression.scenarios import dag_diff
from regression.scenarios.dag_runner import (
    LifecycleOutcome,
    RunResult,
    WaveResult,
)

# A junit doc with passed + skipped + failed crud-lifecycle cases, plus an
# <error> case and a non-crud case that must be ignored. Id 'foo-bar' exercises
# the [...] extraction (hyphenated id).
_JUNIT = """<?xml version="1.0" encoding="utf-8"?>
<testsuites>
  <testsuite name="pytest" tests="5">
    <testcase classname="tests.regression.test_crud" name="test_crud_lifecycle[foo-bar]" time="1.0"/>
    <testcase classname="tests.regression.test_crud" name="test_crud_lifecycle[skip-one]" time="0.0">
      <skipped message="quota exhausted"/>
    </testcase>
    <testcase classname="tests.regression.test_crud" name="test_crud_lifecycle[fail-one]" time="2.0">
      <failure message="assert 500 == 200">boom</failure>
    </testcase>
    <testcase classname="tests.regression.test_crud" name="test_crud_lifecycle[err-one]" time="0.5">
      <error message="setup error">teardown blew up</error>
    </testcase>
    <testcase classname="tests.regression.test_other" name="test_unrelated[xyz]" time="0.1"/>
  </testsuite>
</testsuites>
"""


def _write_junit(tmp_path):
    p = tmp_path / "run.xml"
    p.write_text(_JUNIT, encoding="utf-8")
    return str(p)


def test_parse_junit_maps_statuses_and_extracts_ids(tmp_path):
    parsed = dag_diff.parse_junit(_write_junit(tmp_path))
    assert parsed == {
        "foo-bar": "passed",
        "skip-one": "skipped",
        "fail-one": "failed",
        "err-one": "failed",  # <error> counts as failed
    }
    # non-crud testcase is ignored entirely
    assert "xyz" not in parsed


def test_parse_junit_id_extraction_hyphenated(tmp_path):
    parsed = dag_diff.parse_junit(_write_junit(tmp_path))
    assert "foo-bar" in parsed  # hyphen survives [...] extraction


def _runresult(statuses: dict[str, str]) -> RunResult:
    outs = [LifecycleOutcome(lid, st) for lid, st in statuses.items()]
    return RunResult(waves=[WaveResult(kind="self-create", outcomes=outs)])


def test_runresult_status_maps_outcomes():
    rr = _runresult({"foo-bar": "passed", "skip-one": "skipped", "fail-one": "failed"})
    assert dag_diff.runresult_status(rr) == {
        "foo-bar": "passed",
        "skip-one": "skipped",
        "fail-one": "failed",
    }


def test_runresult_status_ignores_planned():
    # 'planned' (dry_run) is not a real outcome and must be dropped
    rr = _runresult({"a": "planned", "b": "passed"})
    assert dag_diff.runresult_status(rr) == {"b": "passed"}


def test_diff_full_agreement_is_ok():
    side = {"foo-bar": "passed", "skip-one": "skipped", "fail-one": "failed"}
    d = dag_diff.diff(side, dict(side))
    assert d.ok is True
    assert d.agree == side
    assert d.disagree == {}
    assert d.only_xdist == {} and d.only_dagrun == {}


def test_diff_passed_vs_failed_is_disagreement_not_ok():
    xdist = {"foo-bar": "passed"}
    dagrun = {"foo-bar": "failed"}
    d = dag_diff.diff(xdist, dagrun)
    assert d.ok is False
    assert d.disagree == {"foo-bar": ("passed", "failed")}
    assert d.agree == {}


def test_diff_passed_vs_skipped_is_disagreement():
    d = dag_diff.diff({"x": "passed"}, {"x": "skipped"})
    assert d.disagree == {"x": ("passed", "skipped")}
    assert d.ok is False


def test_diff_skipped_vs_skipped_is_agreement():
    d = dag_diff.diff({"x": "skipped"}, {"x": "skipped"})
    assert d.agree == {"x": "skipped"}
    assert d.ok is True


def test_diff_only_one_side_flags_missing_and_not_ok():
    d = dag_diff.diff({"a": "passed", "b": "passed"}, {"a": "passed", "c": "passed"})
    assert d.agree == {"a": "passed"}
    assert d.only_xdist == {"b": "passed"}
    assert d.only_dagrun == {"c": "passed"}
    assert d.ok is False


def test_format_diff_mentions_disagreement():
    d = dag_diff.diff({"x": "passed"}, {"x": "failed"})
    text = dag_diff.format_diff(d)
    assert "MISMATCH" in text
    assert "x: passed -> failed" in text


def test_runresult_json_round_trips(tmp_path):
    rr = _runresult({"foo-bar": "passed", "skip-one": "skipped"})
    payload = dag_diff.dump_runresult(rr)
    p = tmp_path / "run.json"
    import json

    p.write_text(json.dumps(payload), encoding="utf-8")
    loaded = dag_diff.load_runresult_json(str(p))
    assert dag_diff.runresult_status(loaded) == {
        "foo-bar": "passed",
        "skip-one": "skipped",
    }
