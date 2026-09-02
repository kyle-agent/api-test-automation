"""conformance.quality_report — 개발팀 전달용 품질점검 리포트 (오너 2026-09-02 기능화).

hermetic: 리포지토리에 커밋된 data/conformance.json · api_catalog.json ·
reports/runtime_*.json 만 읽고 tmp_path 에 쓴다. 검사 3가지 —
(1) 요약 수치가 conformance.json 과 일치, (2) CSV 행수 = 항목 수, 본문+부록 = 전체,
(3) 조사자 어투 금지어(결함/확정/소행/오탐/재실시) 0건 + 내부 용어(run-id, oplog) 0건.
"""
from __future__ import annotations

import csv
import json
import re

import pytest

from conformance import quality_report as qr


@pytest.fixture(scope="module")
def out(tmp_path_factory):
    d = tmp_path_factory.mktemp("qr")
    return qr.generate(qr.ROOT, d, env_label="검증계", date="2026-08-20")


def test_summary_matches_conformance_json(out):
    conf = json.loads((qr.ROOT / "data" / "conformance.json").read_text(encoding="utf-8"))
    assert out["summary"] == conf["summary"]
    md = out["md"].read_text(encoding="utf-8")
    assert md.startswith("# SCP API 품질점검 결과 — 2026-08-20")
    assert f"| 이상 없음 | {conf['summary']['green']} |" in md
    assert f"| 우선 개선 (RED) | {conf['summary']['red']} |" in md
    assert "검증계 · 2026-08-20 실측 기준" in md          # --env-label 이 헤더에 반영
    n_items = sum(len(v["items"]) for v in conf["by_endpoint"].values() if v["status"] != "green")
    assert out["rows"] == n_items == out["main"] + out["appendix"]


def test_csv_rows_and_columns(out):
    with open(out["csv"], encoding="utf-8-sig", newline="") as fp:
        rows = list(csv.DictReader(fp))
    assert len(rows) == out["rows"]
    assert list(rows[0].keys()) == qr.CSV_FIELDS
    assert {r["cls"] for r in rows} <= {"규격", "문서", "기능"}
    assert {r["tier"] for r in rows} <= {"본문", "부록"}
    assert rows[0]["severity"] == "red"                    # RED 먼저 정렬
    # 부록 판정: 부재-id 403/401 은 본문에 없어야 한다
    nf = qr.notfound_status(qr.ROOT)
    for r in rows:
        if r["rule"] == "notfound-inconsistent":
            key = f"{r['category']}/{r['service']}/{r['endpoint']}"
            assert (r["tier"] == "부록") == (nf.get(key) in (403, 401)), key
    # 파일명에 라벨 반영
    assert out["csv"].name == "SCP-API-품질점검-2026-08-20-검증계-항목목록.csv"


def test_tone_and_no_internal_terms(out):
    for p in (out["md"], out["html"]):
        t = p.read_text(encoding="utf-8")
        for w in qr.BANNED_WORDS:
            assert w not in t, f"{p.name}: 금지어 '{w}'"
        assert not re.search(r"\brun [0-9a-f]{4}\b", t), f"{p.name}: run-id 노출"
        assert not re.search(r"\boplog\b", t, re.I), f"{p.name}: oplog 노출"
        assert "regr" not in t.lower().replace("regression", ""), f"{p.name}: 시나리오 자원명 노출"


def test_sanitize():
    s = qr.sanitize("seen in run 3e67 (2026-08-20) — see artifact/events.jsonl for regrvpc01 lifecycles")
    assert "run 3e67" not in s and "events.jsonl" not in s and "regrvpc01" not in s
    assert "실측 2026-08-20" in s and "호출 기록" in s and "<자원명>" in s


def test_every_rule_in_data_has_korean_wording():
    """새 conformance 규칙이 RULE_KR 없이 리포트에 영문 rule 명으로 새지 않도록."""
    conf = json.loads((qr.ROOT / "data" / "conformance.json").read_text(encoding="utf-8"))
    types = {f["type"] for v in conf["by_endpoint"].values() for f in v["items"]}
    missing = types - set(qr.RULE_KR)
    assert not missing, f"RULE_KR 에 없는 유형: {sorted(missing)}"
    sys_types = {s["type"] for s in conf.get("systemic", [])}
    assert not sys_types - set(qr.SYS_KR), f"SYS_KR 에 없는 공통 항목: {sorted(sys_types - set(qr.SYS_KR))}"
