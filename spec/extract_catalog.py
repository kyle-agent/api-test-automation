#!/usr/bin/env python3
"""Extract all SCP API Reference endpoints into data/api_catalog.json.

The API Reference site (https://docs.e.samsungsdscloud.com/apireference/) renders
each API on its own page. Every page embeds the HTTP method + path in the
<head> meta description, e.g.:

    <meta name=description content="get /v1/aimlops-platform Description ...">

Each detail page is ~4.3 MB (it ships the full nav tree), so we use HTTP Range
requests to fetch only the first few KB — enough to read <title> + meta.

The gateway intermittently returns 503 ("upstream connect ... connection
timeout"), so every request is retried with exponential backoff. The run is
resumable: already-collected entries are skipped on a re-run.

Output: data/api_catalog.json  (same path as the original build_catalog.py)

Ported from tools/build_catalog.py; logic is unchanged.
"""
from __future__ import annotations

import html
import json
import re
import sys
import time
import urllib.request
from pathlib import Path

BASE = "https://docs.e.samsungsdscloud.com"
INDEX = f"{BASE}/apireference/"
ROOT = Path(__file__).resolve().parent.parent
CATALOG = ROOT / "data" / "api_catalog.json"
INDEX_CACHE = ROOT / "data" / ".apiref_index.html"

# href like: /apireference/<category>/<service>/apis/<apiname>/<version>
HREF_RE = re.compile(
    r"href=[\"']?(/apireference/([a-z0-9-]+)/([a-z0-9-]+)/apis/([a-z0-9]+)/([0-9.]+))/?[\"' >]"
)
META_RE = re.compile(r'<meta name=description content="(.*?)"', re.DOTALL)
# meta starts with: "<method> <path> Description ..." e.g. "get /v1/aimlops-platform Description ..."
METHOD_PATH_RE = re.compile(r"^\s*(get|post|put|delete|patch)\s+(/\S+)", re.IGNORECASE)
TITLE_RE = re.compile(r"<title>(.*?)</title>", re.DOTALL)


def fetch(url: str, byte_range: str | None = None, tries: int = 6, timeout: int = 60) -> bytes:
    """GET a URL with optional Range header, retrying on 5xx / network errors."""
    backoff = 2
    last = None
    for attempt in range(1, tries + 1):
        req = urllib.request.Request(url, headers={"User-Agent": "scp-api-catalog/1.0"})
        if byte_range:
            req.add_header("Range", f"bytes={byte_range}")
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                if resp.status in (200, 206):
                    return resp.read()
                last = f"HTTP {resp.status}"
        except Exception as exc:  # urllib raises HTTPError (5xx) and URLError
            last = repr(exc)
        if attempt < tries:
            time.sleep(backoff)
            backoff = min(backoff * 2, 16)
    raise RuntimeError(f"failed to fetch {url}: {last}")


def get_index_html() -> str:
    """Return the index page HTML, using a local cache when available."""
    if INDEX_CACHE.exists() and INDEX_CACHE.stat().st_size > 100_000:
        return INDEX_CACHE.read_text(encoding="utf-8", errors="replace")
    data = fetch(INDEX)
    INDEX_CACHE.write_bytes(data)
    return data.decode("utf-8", errors="replace")


def discover_endpoints(index_html: str) -> list[dict]:
    """Parse all unique endpoint hrefs from the index page."""
    seen: dict[str, dict] = {}
    for m in HREF_RE.finditer(index_html):
        path, category, service, name, version = m.groups()
        # keep the highest version per (category/service/name)
        key = f"{category}/{service}/{name}"
        prev = seen.get(key)
        if prev is None or version > prev["version"]:
            seen[key] = {
                "key": key,
                "category": category,
                "service": service,
                "name": name,
                "version": version,
                "doc_path": path,
                "doc_url": f"{BASE}{path}/",
            }
    return sorted(seen.values(), key=lambda e: e["key"])


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


def build_catalog() -> int:
    """Main entry point: discover + enrich all endpoints, write catalog JSON.

    Returns the process exit code (0 = success).
    """
    CATALOG.parent.mkdir(parents=True, exist_ok=True)
    existing: dict[str, dict] = {}
    if CATALOG.exists():
        for e in json.loads(CATALOG.read_text()):
            existing[e["key"]] = e

    print("fetching index ...", flush=True)
    found = discover_endpoints(get_index_html())
    print(f"discovered {len(found)} endpoints; {len(existing)} already in catalog", flush=True)

    done = 0
    for i, entry in enumerate(found, 1):
        cached = existing.get(entry["key"])
        if cached and cached.get("method") and cached.get("http_path"):
            entry.update({k: cached[k] for k in ("method", "http_path", "title")})
        else:
            try:
                enrich(entry)
            except Exception as exc:
                entry["method"] = entry.get("method")
                entry["http_path"] = entry.get("http_path")
                entry["error"] = str(exc)
                print(f"  [{i}/{len(found)}] FAIL {entry['key']}: {exc}", flush=True)
        existing[entry["key"]] = entry
        done += 1
        if done % 25 == 0:
            CATALOG.write_text(json.dumps(sorted(existing.values(), key=lambda e: e["key"]),
                                          indent=2, ensure_ascii=False))
            ok = sum(1 for e in existing.values() if e.get("http_path"))
            print(f"  progress {i}/{len(found)} (resolved={ok})", flush=True)

    catalog = sorted(existing.values(), key=lambda e: e["key"])
    CATALOG.write_text(json.dumps(catalog, indent=2, ensure_ascii=False))
    ok = sum(1 for e in catalog if e.get("http_path"))
    print(f"done: {len(catalog)} endpoints, {ok} with method/path -> {CATALOG}", flush=True)
    return 0


def main() -> int:
    return build_catalog()


if __name__ == "__main__":
    sys.exit(main())
