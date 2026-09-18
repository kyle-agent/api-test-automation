"""extract_catalog — search-index 기반 discovery 규약 (2026-09-17 사이트 리디자인).

구 규약(index HTML href + --fresh 재수집)은 사이트 리디자인으로 죽었고, --fresh가
카탈로그를 1개로 덮어쓴 사고가 있었다. 새 규약:
  * discovery = search-index.json storedFields (ko · /apis/<name>/<version>/ 리프)
  * method/path = body 첫 줄 → 재실행만으로 변경 감지 (페이지 재수집 불필요)
  * 최고 버전만 CURRENT (숫자 튜플 비교: 1.10 > 1.9)
  * body가 파싱 안 되는 행만 라이브 페이지 head로 enrich
  * 바닥 게이트: 기존 대비 90% 미만이면 쓰지 않는다 (--force 예외)
hermetic: fetch/인덱스를 스텁, 파일은 tmp_path.
"""
from __future__ import annotations

import json

import spec.extract_catalog as m


def _row(ref, body, lang="ko", title=None):
    return {"ref": ref, "body": body, "lang": lang, "title": title or ref.rstrip("/").rsplit("/", 1)[-1]}


def _index(rows):
    return {"documentCount": len(rows), "storedFields": {str(i): r for i, r in enumerate(rows)}}


def _setup(tmp_path, monkeypatch, index, existing=None):
    monkeypatch.setattr(m, "CATALOG", tmp_path / "cat.json")
    monkeypatch.setattr(m, "SEARCH_CACHE", tmp_path / "idx.json")
    if existing is not None:
        m.CATALOG.write_text(json.dumps(existing))
    monkeypatch.setattr(m, "get_search_index", lambda fresh=False: index)
    calls = []

    def fake_fetch(url, byte_range=None, **kw):
        calls.append(url)
        return (b"<title>t2</title>"
                b'<meta name=description content="delete /v1/x/{id} Description">')
    monkeypatch.setattr(m, "fetch", fake_fetch)
    return calls


EXISTING = [{"key": "a/b/x", "category": "a", "service": "b", "name": "x", "version": "1.0",
             "doc_path": "/apireference/a/b/apis/x/1.0", "doc_url": "u", "method": "GET",
             "http_path": "/v1/x", "title": "1.0"}]


def test_discovery_takes_max_version_and_parses_method_path():
    idx = _index([
        _row("/apireference/a/b/apis/x/1.9/", "get /v1/x Description old"),
        _row("/apireference/a/b/apis/x/1.10/", "post /v1/x Description new"),
        _row("/apireference/a/b/apis/x/", ""),                       # list page: empty body
        _row("/apireference/a/b/apis/", ""),
        _row("/en/apireference/a/b/apis/x/1.10/", "post /v1/x", lang="en"),   # en mirror
        _row("/apireference/a/b/models/XReq/", "field table"),
    ])
    found = m.discover_endpoints_from_index(idx)
    assert [e["key"] for e in found] == ["a/b/x"]
    e = found[0]
    assert e["version"] == "1.10" and e["method"] == "POST" and e["http_path"] == "/v1/x"
    assert e["doc_path"] == "/apireference/a/b/apis/x/1.10"
    assert m.discover_models_from_index(idx) == [{
        "key": "a/b/XReq", "category": "a", "service": "b", "name": "XReq",
        "doc_url": f"{m.BASE}/apireference/a/b/models/XReq/"}]
    assert m.index_body_texts(idx)["a/b/x"].startswith("post /v1/x")


def test_rerun_detects_change_without_page_fetch(tmp_path, monkeypatch):
    idx = _index([_row("/apireference/a/b/apis/x/1.1/", "post /v1/x Description")])
    calls = _setup(tmp_path, monkeypatch, idx, EXISTING)
    assert m.build_catalog() == 0
    cat = json.loads(m.CATALOG.read_text())
    assert cat[0]["method"] == "POST" and cat[0]["version"] == "1.1"
    assert calls == []                      # method/path came from the index


def test_unparsable_body_falls_back_to_live_head(tmp_path, monkeypatch):
    idx = _index([_row("/apireference/a/b/apis/x/1.1/", "")])
    calls = _setup(tmp_path, monkeypatch, idx, EXISTING)
    assert m.build_catalog() == 0
    cat = json.loads(m.CATALOG.read_text())
    assert cat[0]["method"] == "DELETE" and cat[0]["http_path"] == "/v1/x/{id}"
    assert len(calls) == 1


def test_floor_gate_refuses_a_shrunken_index(tmp_path, monkeypatch):
    big = [dict(EXISTING[0], key=f"a/b/x{i}", name=f"x{i}") for i in range(10)]
    idx = _index([_row("/apireference/a/b/apis/x0/1.0/", "get /v1/x0 Description")])
    _setup(tmp_path, monkeypatch, idx, big)
    assert m.build_catalog() == 3
    assert len(json.loads(m.CATALOG.read_text())) == 10   # untouched
    assert m.build_catalog(force=True) == 0
    assert len(json.loads(m.CATALOG.read_text())) == 1
