"""AXIS 2 dashboard rendering — per-endpoint conformance items must be deduped.

Regression for the field report "각 API의 conformance 개선사항이 동일한 결과가
반복되어 보인다": the findings store is append-only (re-records the same defect on
every conformance run) and the static + runtime lenses independently report the
same defect, so without dedup the dashboard printed the identical improvement
line 2-3× per API. `findings_to_conf` is the single chokepoint every consumer
(conf_cell / render_service_page) reads, so dedup belongs there.
"""
from dashboard.build import findings_to_conf


def test_identical_findings_collapse_to_one_item():
    # Same static finding recorded 3× (append-only store across runs).
    findings = [
        {"endpoint_key": "compute/vs/delete", "rule_id": "deprecated",
         "severity": "yellow", "source": "static", "issue": 18,
         "detail": "DEPRECATED endpoint"}
    ] * 3
    conf = findings_to_conf(findings)
    items = conf["by_endpoint"]["compute/vs/delete"]["items"]
    assert len(items) == 1
    assert items[0]["issue"] == 18


def test_static_and_runtime_dup_collapse_keeping_issue_ref():
    # Static (#35) and runtime ('') report the same defect — collapse to one,
    # keeping the more informative issue reference.
    findings = [
        {"endpoint_key": "db/mysql/getx", "rule_id": "notfound-inconsistent",
         "severity": "yellow", "source": "runtime", "issue": "",
         "detail": "non-existent id -> 400 (not 404)"},
        {"endpoint_key": "db/mysql/getx", "rule_id": "notfound-inconsistent",
         "severity": "yellow", "source": "static", "issue": 35,
         "detail": "non-existent id -> 400 (not 404)"},
    ]
    items = findings_to_conf(findings)["by_endpoint"]["db/mysql/getx"]["items"]
    assert len(items) == 1
    assert items[0]["issue"] == 35


def test_distinct_details_for_same_rule_are_preserved():
    # Same rule, genuinely different details (different fields) must NOT collapse.
    findings = [
        {"endpoint_key": "db/mysql/create", "rule_id": "undiscoverable-params",
         "severity": "red", "source": "static", "issue": 19, "detail": "required: name"},
        {"endpoint_key": "db/mysql/create", "rule_id": "undiscoverable-params",
         "severity": "red", "source": "static", "issue": 19, "detail": "required: subnet_id"},
    ]
    rec = findings_to_conf(findings)["by_endpoint"]["db/mysql/create"]
    assert len(rec["items"]) == 2
    assert rec["status"] == "red"  # severity still aggregates correctly
