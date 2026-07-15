"""_capture min_by/nth — smallest-server-type selection (2026-07-15).

DBaaS server-type lists open with 10vCPU/120G entries, so the legacy
first-match capture provisioned 10vCPU clusters every run. min_by picks the
actual smallest by numeric field(s) regardless of list order; nth=1 gives the
second-smallest (resize target must differ from the base type).
"""
from regression.scenarios.engine import _capture

BODY = {"contents": [
    {"name": "db1v10m120", "cpu_core": 10, "memory_gb": 120},
    {"name": "db1v10m160", "cpu_core": 10, "memory_gb": 160},
    {"name": "db1v2m4",    "cpu_core": 2,  "memory_gb": 4},
    {"name": "db1v4m8",    "cpu_core": 4,  "memory_gb": 8},
    {"name": "gpu1v2m4",   "cpu_core": 2,  "memory_gb": 4},
]}


def test_min_by_picks_smallest_regardless_of_order():
    assert _capture(BODY, {"list": "$.contents",
                           "min_by": ["cpu_core", "memory_gb"],
                           "get": "name"}) == "db1v2m4"


def test_nth_1_picks_second_smallest_for_resize_targets():
    assert _capture(BODY, {"list": "$.contents",
                           "min_by": ["cpu_core", "memory_gb"],
                           "nth": 1, "get": "name"}) == "gpu1v2m4"


def test_min_by_composes_with_prefix_filters():
    assert _capture(BODY, {"list": "$.contents",
                           "where_prefix": {"name": "db"},
                           "min_by": ["cpu_core", "memory_gb"],
                           "nth": 1, "get": "name"}) == "db1v4m8"


def test_min_by_string_field_form_and_nth_clamp():
    assert _capture(BODY, {"list": "$.contents", "min_by": "cpu_core",
                           "nth": 99, "get": "name"}) == "db1v10m160"


def test_non_numeric_values_sort_last():
    body = {"contents": [{"name": "bad", "cpu_core": "N/A"},
                         {"name": "ok", "cpu_core": 8}]}
    assert _capture(body, {"list": "$.contents", "min_by": "cpu_core",
                           "get": "name"}) == "ok"


def test_legacy_first_match_untouched_without_min_by():
    assert _capture(BODY, {"list": "$.contents", "get": "name"}) == "db1v10m120"
