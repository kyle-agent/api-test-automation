"""AXIS 2 dashboard — per-endpoint 결함 column must fall back to data/conformance.json.

Owner 2026-10-24 "api 품질결과가 내용이 없네": the unified findings store is
machine-local (gitignored), so a publish from a machine that never ran
`python -m conformance.static` rendered every API as 미평가 although the
committed data/conformance.json carried the full per-endpoint verdicts.
"""
import json
import os

from dashboard import build as B


def test_conf_from_conformance_file_adapts_items_and_recomputes_status():
    conf_file = {
        "summary": {"red": 99},            # stale on purpose — must be recomputed
        "systemic": [{"type": "sys", "detail": "x"}],
        "by_endpoint": {
            "a/b/create": {"status": "green", "items": [
                {"sev": "red", "type": "runtime.500", "src": "runtime", "detail": "500", "issue": 7}]},
            "a/b/list": {"status": "green", "items": []},
        },
    }
    conf = B.conf_from_conformance_file(conf_file)
    assert conf["by_endpoint"]["a/b/create"]["status"] == "red"
    assert conf["by_endpoint"]["a/b/create"]["items"][0] == {
        "type": "runtime.500", "src": "runtime", "issue": 7, "detail": "500"}
    assert conf["by_endpoint"]["a/b/list"]["status"] == "green"
    assert conf["summary"] == {"red": 1, "yellow": 0, "green": 1, "total": 2}
    assert conf["systemic"] == conf_file["systemic"]


def test_build_uses_conformance_file_when_findings_store_is_empty(tmp_path):
    empty = tmp_path / "findings.jsonl"
    empty.write_text("")
    B.build(obs_path=str(tmp_path / "obs.jsonl"), findings_path=str(empty),
            out=str(tmp_path / "index.html"), history=str(tmp_path / "h.jsonl"),
            run_type="test")
    pages = [p for p in os.listdir(tmp_path / "services") if p.endswith(".html")]
    assert pages
    html = (tmp_path / "services" / pages[0]).read_text(encoding="utf-8")
    assert "CONF_EVAL=true" in html          # 근거 있음 — 미평가로 렌더하지 않는다
    with open("data/conformance.json", encoding="utf-8") as fh:
        file_conf = json.load(fh)
    assert file_conf.get("by_endpoint")       # precondition: the committed file carries verdicts
