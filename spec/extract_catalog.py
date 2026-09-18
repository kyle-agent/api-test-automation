#!/usr/bin/env python3
"""Extract all SCP API Reference endpoints into data/api_catalog.json.

Discovery source (2026-09-17 redesign): the docs site's **search index**
``https://docs.e.samsungsdscloud.com/search-index.json`` (minisearch v2,
~86 MB). The old source — per-endpoint hrefs embedded in the ``/apireference/``
index HTML — died with the 2026-09 site redesign (the index page shrank from a
5 MB nav embed to a 31 KB category list; the old ``--fresh`` run discovered
**1** endpoint and truncated the catalog — SPEC-DIFF-20260917 §1). The search
index's ``storedFields`` still carry every page: ``ref`` (URL path), ``title``,
``lang`` and ``body`` (the rendered page text). For an endpoint page the body's
first line is ``"<method> <path> Description ..."`` — validated 0 mismatches
against the 1,390 catalog rows it overlaps (§7).

Rules:
  * ko pages only (en mirrors 1:1); leaf pages
    ``/apireference/<cat>/<svc>/apis/<name>/<version>/`` only — the ``/apis/``
    and ``/apis/<name>/`` list pages have EMPTY bodies (client-rendered) even
    in the index.
  * the highest version per ``<cat>/<svc>/<name>`` is CURRENT (numeric tuple
    compare, so 1.10 > 1.9). Historical-version retention in the index is
    inconsistent per service, so "missing from the index" is never read as
    "page is dead" — only the max version matters.
  * rows whose body lacks a parsable method/path fall back to fetching the
    live page head (``enrich``), exactly as before.
  * **floor gate**: the new catalog must be ≥ ``FLOOR`` (90%) of the committed
    one, else nothing is written (``--force`` overrides) — the truncation-to-1
    incident must never repeat.

The 86 MB index is cached at ``data/.search-index.json`` (git-ignored);
``--fresh`` re-downloads it. Because method/path come from the index itself,
a re-run with a fresh index DOES detect changed endpoints (the old resumable
mode could not).

Output: data/api_catalog.json (schema unchanged: key, category, service, name,
version, doc_path, doc_url, method, http_path, title).

Also exported for sibling modules: ``discover_models_from_index`` (model
pages, used by ``spec.scrape_docs``) and ``index_body_texts`` (endpoint page
text by catalog key, used by ``spec.extract_bodies --from-index`` — the text
carries the "Request body {…} Example HTTP response" example verbatim, so
request bodies need no page fetch at all).
"""
from __future__ import annotations

import html
import json
import re
import ssl
import sys
import time
import urllib.request
from pathlib import Path

BASE = "https://docs.e.samsungsdscloud.com"
SEARCH_INDEX_URL = f"{BASE}/search-index.json"
ROOT = Path(__file__).resolve().parent.parent
CATALOG = ROOT / "data" / "api_catalog.json"
SEARCH_CACHE = ROOT / "data" / ".search-index.json"   # 86 MB, git-ignored
# legacy HTML index cache — kept only so old call sites don't break; unused.
INDEX_CACHE = ROOT / "data" / ".apiref_index.html"
FLOOR = 0.90            # new catalog must keep >= 90% of the committed size
CA_BUNDLE = Path("/root/.ccr/ca-bundle.crt")

ENDPOINT_REF_RE = re.compile(
    r"^/apireference/([a-z0-9-]+)/([a-z0-9-]+)/apis/([a-z0-9]+)/([0-9.]+)/?$")
MODEL_REF_RE = re.compile(
    r"^/apireference/([a-z0-9-]+)/([a-z0-9-]+)/models/([a-zA-Z0-9_.-]+?)/?$")
META_RE = re.compile(r'<meta name=description content="(.*?)"', re.DOTALL)
# meta / body starts with: "<method> <path> Description ..." e.g. "get /v1/aimlops-platform ..."
METHOD_PATH_RE = re.compile(r"^\s*(get|post|put|delete|patch)\s+(/\S+)", re.IGNORECASE)
TITLE_RE = re.compile(r"<title>(.*?)</title>", re.DOTALL)


def _ssl_ctx():
    if CA_BUNDLE.exists():
        return ssl.create_default_context(cafile=str(CA_BUNDLE))
    return ssl.create_default_context()


def fetch(url: str, byte_range: str | None = None, tries: int = 6, timeout: int = 60) -> bytes:
    """GET a URL with optional Range header, retrying on 5xx / network errors."""
    backoff = 2
    last = None
    for attempt in range(1, tries + 1):
        req = urllib.request.Request(url, headers={"User-Agent": "scp-api-catalog/2.0"})
        if byte_range:
            req.add_header("Range", f"bytes={byte_range}")
        try:
            with urllib.request.urlopen(req, timeout=timeout, context=_ssl_ctx()) as resp:
                if resp.status in (200, 206):
                    return resp.read()
                last = f"HTTP {resp.status}"
        except Exception as exc:  # urllib raises HTTPError (5xx) and URLError
            last = repr(exc)
        if attempt < tries:
            time.sleep(backoff)
            backoff = min(backoff * 2, 16)
    raise RuntimeError(f"failed to fetch {url}: {last}")


# ---------------------------------------------------------------- search index
def get_search_index(*, fresh: bool = False) -> dict:
    """Return the parsed minisearch index, downloading it when ``fresh`` or when
    no cache exists. Raises when the payload is not a minisearch document."""
    if fresh or not SEARCH_CACHE.exists() or SEARCH_CACHE.stat().st_size < 1_000_000:
        print(f"downloading {SEARCH_INDEX_URL} ...", flush=True)
        data = fetch(SEARCH_INDEX_URL, timeout=180)
        SEARCH_CACHE.parent.mkdir(parents=True, exist_ok=True)
        SEARCH_CACHE.write_bytes(data)
    idx = json.loads(SEARCH_CACHE.read_text(encoding="utf-8"))
    if not isinstance(idx, dict) or "storedFields" not in idx:
        raise RuntimeError("search-index.json is not a minisearch document (no storedFields)")
    return idx


def _version_key(v: str) -> tuple:
    try:
        return tuple(int(p) for p in v.split("."))
    except ValueError:
        return (0,)


def _ko_rows(index: dict):
    for v in index.get("storedFields", {}).values():
        if not isinstance(v, dict) or v.get("lang") != "ko":
            continue
        ref = v.get("ref") or ""
        if ref.startswith("/en/"):
            continue
        yield ref, v


def discover_endpoints_from_index(index: dict) -> list[dict]:
    """Every ko endpoint leaf page, reduced to the highest version per key.
    Method/path come from the body's first line (``None`` when unparsable —
    the caller enriches those from the live page head)."""
    seen: dict[str, dict] = {}
    for ref, v in _ko_rows(index):
        m = ENDPOINT_REF_RE.match(ref)
        if not m:
            continue
        category, service, name, version = m.groups()
        key = f"{category}/{service}/{name}"
        prev = seen.get(key)
        if prev is not None and _version_key(version) <= _version_key(prev["version"]):
            continue
        mp = METHOD_PATH_RE.match((v.get("body") or "").strip())
        path = f"/apireference/{category}/{service}/apis/{name}/{version}"
        seen[key] = {
            "key": key, "category": category, "service": service, "name": name,
            "version": version, "doc_path": path, "doc_url": f"{BASE}{path}/",
            "method": mp.group(1).upper() if mp else None,
            "http_path": mp.group(2) if mp else None,
            "title": (v.get("title") or version).strip() or version,
        }
    return sorted(seen.values(), key=lambda e: e["key"])


def discover_models_from_index(index: dict) -> list[dict]:
    """Model pages (``/models/<name>/``) — the shape ``spec.scrape_docs`` expects."""
    seen: dict[str, dict] = {}
    for ref, _v in _ko_rows(index):
        m = MODEL_REF_RE.match(ref)
        if not m:
            continue
        c, s, name = m.groups()
        key = f"{c}/{s}/{name}"
        seen[key] = {"key": key, "category": c, "service": s, "name": name,
                     "doc_url": f"{BASE}/apireference/{c}/{s}/models/{name}/"}
    return sorted(seen.values(), key=lambda e: e["key"])


def index_body_texts(index: dict, catalog: list[dict] | None = None) -> dict[str, str]:
    """``{catalog key: rendered page text}`` for the CURRENT version of every
    endpoint (or only the given catalog's keys/versions when ``catalog`` is
    passed). The text carries the request-body example verbatim."""
    want = {e["key"]: e["version"] for e in (catalog or [])}
    best: dict[str, tuple[tuple, str]] = {}
    for ref, v in _ko_rows(index):
        m = ENDPOINT_REF_RE.match(ref)
        if not m:
            continue
        category, service, name, version = m.groups()
        key = f"{category}/{service}/{name}"
        if want:
            if key not in want or want[key] != version:
                continue
            best[key] = (_version_key(version), v.get("body") or "")
            continue
        vk = _version_key(version)
        if key not in best or vk > best[key][0]:
            best[key] = (vk, v.get("body") or "")
    return {k: t for k, (_vk, t) in best.items()}


# ---------------------------------------------------------------- live fallback
def enrich(entry: dict) -> dict:
    """Fetch the head of the detail page and extract method/path/title."""
    head = fetch(entry["doc_url"], byte_range="0-12287").decode("utf-8", errors="replace")
    meta_m = META_RE.search(head)
    title_m = TITLE_RE.search(head)
    method = http_path = None
    if meta_m:
        desc = html.unescape(meta_m.group(1)).strip()
        mp = METHOD_PATH_RE.match(desc)
        if mp:
            method = mp.group(1).upper()
            http_path = mp.group(2)
    entry["method"] = method
    entry["http_path"] = http_path
    entry["title"] = html.unescape(title_m.group(1)).split("|")[0].strip() if title_m else None
    return entry


# ---------------------------------------------------------------- build
def build_catalog(*, fresh: bool = False, force: bool = False) -> int:
    """Discover from the search index, enrich unparsable rows from the live
    page head, gate on the size floor, write the catalog.

    ``fresh`` re-downloads the search index (needed to see NEW site content;
    the cached index is otherwise reused). ``force`` bypasses the floor gate.
    Returns the process exit code (0 = written, 3 = floor gate refused)."""
    CATALOG.parent.mkdir(parents=True, exist_ok=True)
    existing: dict[str, dict] = {}
    if CATALOG.exists():
        for e in json.loads(CATALOG.read_text(encoding="utf-8")):
            existing[e["key"]] = e

    index = get_search_index(fresh=fresh)
    found = discover_endpoints_from_index(index)
    print(f"discovered {len(found)} endpoints in the search index "
          f"({index.get('documentCount')} documents); catalog has {len(existing)}", flush=True)

    floor = int(len(existing) * FLOOR)
    if existing and len(found) < floor and not force:
        print(f"REFUSED: discovered {len(found)} < floor {floor} ({FLOOR:.0%} of "
              f"{len(existing)}) — the index looks incomplete; catalog left untouched "
              f"(--force to override)", flush=True)
        return 3

    # unparsable bodies -> live page head (same as the pre-redesign path)
    todo = [e for e in found if not (e.get("method") and e.get("http_path"))]
    for i, entry in enumerate(todo, 1):
        cached = existing.get(entry["key"])
        if cached and cached.get("method") and cached.get("http_path") \
                and cached.get("version") == entry["version"]:
            entry.update({k: cached[k] for k in ("method", "http_path", "title")})
            continue
        try:
            enrich(entry)
            print(f"  [{i}/{len(todo)}] enriched {entry['key']} -> {entry['method']} {entry['http_path']}",
                  flush=True)
        except Exception as exc:  # noqa: BLE001 — keep the row, mark the failure
            entry["error"] = str(exc)
            print(f"  [{i}/{len(todo)}] FAIL {entry['key']}: {exc}", flush=True)

    new_keys = {e["key"] for e in found}
    added = sorted(new_keys - set(existing))
    removed = sorted(set(existing) - new_keys)
    bumped = sorted(k for k in new_keys & set(existing)
                    if existing[k].get("version") != next(e["version"] for e in found if e["key"] == k))
    print(f"added {len(added)} · removed {len(removed)} · version-bumped {len(bumped)}", flush=True)
    for k in removed:
        print(f"  removed: {k} (was {existing[k].get('version')})", flush=True)

    catalog = sorted(found, key=lambda e: e["key"])
    CATALOG.write_text(json.dumps(catalog, indent=2, ensure_ascii=False), encoding="utf-8")
    ok = sum(1 for e in catalog if e.get("http_path"))
    print(f"done: {len(catalog)} endpoints, {ok} with method/path -> {CATALOG}", flush=True)
    return 0


def main(argv: list[str] | None = None) -> int:
    import argparse
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--fresh", action="store_true",
                    help="re-download search-index.json (see NEW site content); "
                         "otherwise the cached copy is reused")
    ap.add_argument("--force", action="store_true",
                    help="bypass the size floor gate (never on a hunch — the floor "
                         "exists because a broken discovery once wrote a 1-entry catalog)")
    args = ap.parse_args(argv)
    return build_catalog(fresh=args.fresh, force=args.force)


if __name__ == "__main__":
    sys.exit(main())
