"""extract_catalog --fresh 규약 (오너 2026-07-14 저녁 API 변경 대비).

기본(resumable) 모드는 method+http_path가 이미 있는 항목을 재수집하지 않는다 —
재개용으로는 옳지만 **기존 엔드포인트의 '변경'을 감지하지 못한다** (실측:
1372개 전부 cache-hit로 no-op). 스펙 변경 diff 전 수집은 --fresh 필수.
hermetic: fetch를 스텁, 파일은 tmp_path.
"""
from __future__ import annotations

import json


def _setup(m, tmp_path, monkeypatch):
    monkeypatch.setattr(m, "CATALOG", tmp_path / "cat.json")
    monkeypatch.setattr(m, "INDEX_CACHE", tmp_path / "idx.html")
    m.CATALOG.write_text(json.dumps([{
        "key": "a/b/x", "category": "a", "service": "b", "name": "x",
        "version": "1.0", "doc_path": "/apireference/a/b/apis/x/1.0",
        "doc_url": "u", "method": "GET", "http_path": "/v1/x", "title": "t"}]))
    m.INDEX_CACHE.write_text("x" * 200_000)   # 캐시 존재( >100KB 게이트 통과)
    calls = []

    def fake_fetch(url, byte_range=None, **kw):
        calls.append(url)
        if url == m.INDEX:
            return b'href="/apireference/a/b/apis/x/1.0" '
        return (b"<title>t2</title>"
                b'<meta name=description content="post /v1/x Description">')

    monkeypatch.setattr(m, "fetch", fake_fetch)
    return calls


def test_resumable_reuses_cache_and_misses_changes(tmp_path, monkeypatch):
    import spec.extract_catalog as m
    calls = _setup(m, tmp_path, monkeypatch)
    m.build_catalog()
    assert not any("apis/x" in u for u in calls), \
        "resumable은 method+path 보유 항목의 상세 페이지를 재수집하지 않는다"
    assert json.loads(m.CATALOG.read_text())[0]["method"] == "GET", \
        "resumable은 변경(GET->POST)을 감지하지 못한다 — 이것이 --fresh의 존재 이유"


def test_fresh_bypasses_cache_and_detects_change(tmp_path, monkeypatch):
    import spec.extract_catalog as m
    calls = _setup(m, tmp_path, monkeypatch)
    m.build_catalog(fresh=True)
    assert any("apis/x" in u for u in calls), "--fresh는 상세 페이지를 재수집한다"
    assert not m.INDEX_CACHE.exists() or "apireference" in m.INDEX_CACHE.read_text(), \
        "--fresh는 stale 인덱스 캐시를 버리고 재수집한다"
    assert json.loads(m.CATALOG.read_text())[0]["method"] == "POST", \
        "--fresh는 기존 엔드포인트의 변경(GET->POST)을 감지한다"
