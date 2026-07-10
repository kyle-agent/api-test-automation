"""tools.auto_repair 규칙 엔진 회귀 (run-end 자동수리 v1, 2026-07-10)."""
from __future__ import annotations

import json

from tools import auto_repair as ar


def _ev(**kw):
    return kw


LCS = {
    "lc-a": {"id": "lc-a", "steps": [
        {"name": "set-member", "method": "PUT", "path": "/v1/x/{id}",
         "expect_status": [200, 202]},
        {"name": "wait-x", "method": "GET", "path": "/v1/x/{id}",
         "poll": {"field": "$.state", "until": ["ACTIVE"],
                  "timeout": 300, "interval": 15}},
    ]},
    "lc-b": {"id": "lc-b", "steps": [
        {"name": "del-x", "method": "DELETE", "path": "/v1/x/{id}",
         "retry_on_status": [400, 401, 409]},
    ]},
}
SRC = {"lc-a": "test__a.json", "lc-b": "test__b.json"}


def test_r1_settle_409_detected_and_idempotent():
    events = [
        _ev(kind="step-end", lifecycle="lc-a", step="set-member", status=409,
            resp_snippet='{"code":"x.NotUpdatableState","detail":"Not Active"}'),
        _ev(kind="step-end", lifecycle="lc-a", step="set-member", status=409,
            resp_snippet="NotUpdatableState"),        # 중복 — 1건만
    ]
    fs = ar.classify(events, LCS, SRC)
    r1 = [f for f in fs if f["rule"] == "R1-settle-409"]
    assert len(r1) == 1 and r1[0]["patch"]["retry_on_status"] == [409]
    # 이미 사다리가 있으면(멱등) 검출 안 함
    lcs2 = json.loads(json.dumps(LCS))
    lcs2["lc-a"]["steps"][0]["retry_on_status"] = [409]
    assert not [f for f in ar.classify(events, lcs2, SRC)
                if f["rule"] == "R1-settle-409"]


def test_r2_401_static_detection():
    fs = ar.classify([], LCS, SRC)
    r2 = [f for f in fs if f["rule"] == "R2-401-retry"]
    assert len(r2) == 1 and r2[0]["lifecycle"] == "lc-b"
    assert r2[0]["patch"]["retry_on_status"] == [400, 409]


def test_r3_timeout_boundary_needs_current_match():
    events = [_ev(kind="poll-progress", lifecycle="lc-a", step="wait-x",
                  elapsed_s=295.0, timeout_s=300.0)]
    fs = [f for f in ar.classify(events, LCS, SRC) if f["rule"].startswith("R3")]
    assert len(fs) == 1 and fs[0]["patch"]["poll.timeout"] == 420  # 300*1.25→60s 반올림
    # 관측 timeout과 현재 값이 다르면(이미 상향) 재검출 안 함 — 중복 상향 방지
    lcs2 = json.loads(json.dumps(LCS))
    lcs2["lc-a"]["steps"][1]["poll"]["timeout"] = 420
    assert not [f for f in ar.classify(events, lcs2, SRC)
                if f["rule"].startswith("R3")]


def test_apply_patches_file_and_rolls_back_on_validate_fail(tmp_path, monkeypatch):
    fp = tmp_path / "test__a.json"
    fp.write_text(json.dumps({"lifecycles": [json.loads(json.dumps(LCS["lc-a"]))]},
                             ensure_ascii=False, indent=2))
    monkeypatch.setattr(ar, "_LC_DIR", tmp_path)
    finding = {"rule": "R1-settle-409", "lifecycle": "lc-a", "step": "set-member",
               "file": "test__a.json", "detail": "테스트",
               "patch": {"retry_on_status": [409], "retries": 8, "retry_interval": 15}}

    class _OK:
        stdout, stderr = "251 lifecycle(s) checked · 0 error(s)", ""
    monkeypatch.setattr(ar.subprocess, "run", lambda *a, **k: _OK())
    applied, skipped = ar.apply_findings([finding])
    assert applied and not skipped
    doc = json.loads(fp.read_text())
    step = doc["lifecycles"][0]["steps"][0]
    assert step["retry_on_status"] == [409] and "auto-repair" in step["_note"]

    # validate 실패 → 전량 롤백
    fp.write_text(json.dumps({"lifecycles": [json.loads(json.dumps(LCS["lc-a"]))]},
                             ensure_ascii=False, indent=2))
    before = fp.read_text()

    class _BAD:
        stdout, stderr = "3 error(s)", ""
    monkeypatch.setattr(ar.subprocess, "run", lambda *a, **k: _BAD())
    applied, skipped = ar.apply_findings([finding])
    assert not applied and skipped and fp.read_text() == before


def test_scenarios_json_is_report_only():
    src = {"lc-a": "scenarios.json", "lc-b": "scenarios.json"}
    fs = ar.classify([], LCS, src)
    applied, skipped = ar.apply_findings(fs)
    assert not applied and all("report-only" in s["why"] for s in skipped)
