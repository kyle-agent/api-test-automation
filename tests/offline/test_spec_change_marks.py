"""스펙 변경 마커 + 변경분 검증 리포트 (오너 2026-07-14 "변경분 반영/표시").

체인: ``spec.diff --mark`` 가 diff를 ``data/spec_diff_latest.json`` 로 영속화
(marks = key -> added|changed) → 대시보드가 NEW/UPD 배지로 표시, ``spec.change_report``
가 런 관측을 '변경 버킷 vs 기존 버킷'으로 분리해 (1) 변경분이 검증됐는지
(2) 실패가 어디에 몰리는지 판정. 전부 hermetic — 파일은 tmp_path, 네트워크 0.
"""
from __future__ import annotations

import json

from spec.diff import diff_catalog, mark_payload, write_marks
from spec.change_report import build_report


def _cat_old():
    return [
        {"key": "a/b/keep", "category": "a", "service": "b", "name": "keep",
         "method": "GET", "http_path": "/v1/keep", "title": "keep",
         "version": "1.0", "doc_url": "u"},
        {"key": "a/b/chg", "category": "a", "service": "b", "name": "chg",
         "method": "GET", "http_path": "/v1/chg", "title": "chg",
         "version": "1.0", "doc_url": "u"},
        {"key": "a/b/gone", "category": "a", "service": "b", "name": "gone",
         "method": "DELETE", "http_path": "/v1/gone", "title": "gone",
         "version": "1.0", "doc_url": "u"},
    ]


def _cat_new():
    return [
        _cat_old()[0],
        {"key": "a/b/chg", "category": "a", "service": "b", "name": "chg",
         "method": "POST", "http_path": "/v1/chg", "title": "chg",
         "version": "1.1", "doc_url": "u"},
        {"key": "a/b/new", "category": "a", "service": "b", "name": "new",
         "method": "GET", "http_path": "/v1/new/{id}", "title": "new",
         "version": "1.0", "doc_url": "u"},
    ]


def _report(tmp_path):
    old, new = tmp_path / "old.json", tmp_path / "new.json"
    old.write_text(json.dumps(_cat_old()))
    new.write_text(json.dumps(_cat_new()))
    return diff_catalog(old, new)


def test_mark_payload_marks_added_and_changed_only(tmp_path):
    rep = _report(tmp_path)
    m = mark_payload(rep, old_label="old", new_label="new")
    assert m["marks"] == {"a/b/new": "added", "a/b/chg": "changed"}
    assert [e["key"] for e in m["removed"]] == ["a/b/gone"]
    assert m["summary"]["unchanged"] == 1
    # changed 항목은 어떤 필드가 바뀌었는지 담는다 (대시보드 툴팁/리포트용)
    assert "method" in m["changed"][0]["fields"]


def test_write_marks_persists_json(tmp_path):
    rep = _report(tmp_path)
    out = write_marks(rep, old_label="o", new_label="n",
                      path=tmp_path / "marks.json")
    saved = json.loads(out.read_text())
    assert saved["marks"]["a/b/chg"] == "changed"
    assert saved["generated_at"]


def test_change_report_buckets_touched_and_failures(tmp_path):
    """변경 버킷 실패(변경 자체 문제)와 기존 버킷 실패(호환성 회귀 신호)를 분리.
    path 템플릿 {id}는 실측 경로에 매칭되고, since_ts로 직전 런만 대상."""
    marks = mark_payload(_report(tmp_path), old_label="o", new_label="n")
    obs = [
        {"method": "GET", "path": "/v1/new/123", "status": 200,
         "category": "ok", "ts": 9e9},                       # added 정상 관측
        {"method": "POST", "path": "/v1/chg", "status": 500,
         "category": "fail", "ts": 9e9},                     # changed 실패
        {"method": "GET", "path": "/v1/keep", "status": 500,
         "category": "fail", "ts": 9e9},                     # 기존 버킷 실패
        {"method": "GET", "path": "/v1/new/999", "status": 200,
         "category": "ok", "ts": 0.0},                       # since 이전 — 제외
    ]
    rep = build_report(marks, _cat_new(), obs, since_ts=1.0)
    s = rep["summary"]
    assert rep["observations_considered"] == 3
    assert s["touched"] == 2 and s["touched_ok"] == 1 and s["touched_failing"] == 1
    assert s["failures_in_changed_bucket"] == 1
    assert s["failures_in_existing_bucket"] == 1
    assert rep["failures_changed_bucket"][0]["changed_key"] == "a/b/chg"


def test_change_report_untouched_marks_are_reported(tmp_path):
    """이번 런이 관측하지 못한 변경분은 '미검증'으로 떠야 한다 (커버리지 갭)."""
    marks = mark_payload(_report(tmp_path), old_label="o", new_label="n")
    rep = build_report(marks, _cat_new(), [], since_ts=0.0)
    assert rep["summary"]["untouched"] == 2
    assert {u["key"] for u in rep["untouched"]} == {"a/b/new", "a/b/chg"}


def test_dashboard_rows_carry_chg_badge(tmp_path):
    """render_service_page 행에 marks가 chg로 실리고, 마커 없으면 무영향."""
    from dashboard.build import render_service_page
    s = {"category": "a", "service": "b", "slug": "a__b",
         "covered": 0, "total": 2, "gcov": 0, "gtot": 1, "wcov": 0, "wtot": 1,
         "reached": 0, "untestable": "",
         "rows": [("GET", "/v1/new/{id}", "new", False, None, None, "", "", None),
                  ("GET", "/v1/keep", "keep", False, None, None, "", "", None)]}
    meta = {"when": "now", "branch": "t", "conf": {},
            "spec_diff": {"marks": {"a/b/new": "added"},
                          "generated_at": "2026-07-14"}}
    out = render_service_page(s, meta)
    assert '"chg": "added"' in out, "마크된 행에 chg가 실려야"
    assert out.count('"chg"') >= 1
    meta_nomark = {"when": "now", "branch": "t", "conf": {}}
    out2 = render_service_page(s, meta_nomark)
    assert '"chg"' not in out2, "마커 없으면 행 데이터 무변화"
