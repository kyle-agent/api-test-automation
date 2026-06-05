#!/usr/bin/env python3
"""Extract example request bodies from SCP API Reference pages.

Each API doc page server-side-renders its "Example HTTP request" body as a
syntax-highlighted code block (every token wrapped in <span>). The catalog
builder (extract_catalog.py) only reads the first 12 KB (method+path from the
<head> meta); this module fetches the FULL page and recovers the example request
body — the only machine-readable source of required fields / enum values, since
the rendered docs expose no OpenAPI schema and the gateway returns unnamed
"Field required" errors.

Output: framework/api_bodies.json  ->  { "<catalog key>": {<example body>}, ... }
Resumable: keys already present are skipped. Filter with --key-substr / --method.

Ported from tools/fetch_request_bodies.py; logic is unchanged.

Usage:
  python -m spec.extract_bodies --key-substr security/kms
  python -m spec.extract_bodies            # all POST/PUT/PATCH endpoints
"""
from __future__ import annotations

import argparse
import html
import json
import re
import sys
import time
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CATALOG = ROOT / "framework" / "api_catalog.json"
OUT = ROOT / "framework" / "api_bodies.json"
WRITE_METHODS = {"POST", "PUT", "PATCH"}


def fetch(url: str, tries: int = 8, timeout: int = 60) -> str:
    """GET the full doc page, retrying the gateway's intermittent 503s."""
    backoff = 3
    for attempt in range(tries):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "scp-bodies/1.0"})
            return urllib.request.urlopen(req, timeout=timeout).read().decode("utf-8", "replace")
        except Exception:
            if attempt == tries - 1:
                raise
            time.sleep(backoff)
            backoff = min(backoff * 2, 25)


def extract_request_body(page: str) -> str | None:
    """Recover the example request JSON.

    Finds the balanced ``{...}`` object immediately preceding the
    'Example HTTP response' marker, after scripts/styles (which embed their
    own braces) are stripped.
    """
    page = re.sub(r"<script\b.*?</script>", "", page, flags=re.S | re.I)
    page = re.sub(r"<style\b.*?</style>", "", page, flags=re.S | re.I)
    plain = html.unescape(re.sub(r"<[^>]+>", "", page))
    marker = plain.find("Example HTTP response")
    if marker < 0:
        return None
    end = plain.rfind("}", 0, marker)
    if end < 0:
        return None
    depth = 0
    for i in range(end, -1, -1):
        if plain[i] == "}":
            depth += 1
        elif plain[i] == "{":
            depth -= 1
            if depth == 0:
                return " ".join(plain[i:end + 1].split())
    return None


def fetch_bodies(
    catalog_path: str | Path = CATALOG,
    out_path: str | Path = OUT,
    key_substr: str = "",
    method_filter: str = "",
    limit: int = 0,
) -> int:
    """Fetch request bodies for matching endpoints and write to *out_path*.

    Returns the number of newly fetched bodies.
    """
    catalog_path = Path(catalog_path)
    out_path = Path(out_path)

    cat = json.loads(catalog_path.read_text())
    out: dict = json.loads(out_path.read_text()) if out_path.exists() else {}

    target_methods = {method_filter.upper()} if method_filter else WRITE_METHODS
    todo = [
        e for e in cat
        if e["method"].upper() in target_methods
        and key_substr in e["key"]
        and e["key"] not in out
    ]
    print(f"{len(todo)} endpoint(s) to fetch ({len(out)} already cached)")

    done = 0
    for e in todo:
        try:
            body = extract_request_body(fetch(e["doc_url"]))
        except Exception as exc:
            print(f"  ERR  {e['key']}: {exc}")
            continue
        if not body:
            print(f"  --   {e['key']}: no body example")
            continue
        try:
            out[e["key"]] = json.loads(body)
        except json.JSONDecodeError:
            out[e["key"]] = {"_raw": body}  # keep the text even if not strict JSON
        print(f"  ok   {e['key']}")
        done += 1
        if done % 10 == 0:  # checkpoint (resumable)
            out_path.write_text(json.dumps(out, indent=2, ensure_ascii=False, sort_keys=True))
        if limit and done >= limit:
            break

    out_path.write_text(json.dumps(out, indent=2, ensure_ascii=False, sort_keys=True))
    print(f"wrote {len(out)} bodies -> {out_path}")
    return done


def main() -> int:
    ap = argparse.ArgumentParser(
        description="Extract example request bodies from the SCP API Reference docs.")
    ap.add_argument("--catalog", default=str(CATALOG))
    ap.add_argument("--out", default=str(OUT))
    ap.add_argument("--key-substr", default="", help="only endpoints whose key contains this")
    ap.add_argument("--method", default="", help="restrict to one method (POST/PUT/PATCH)")
    ap.add_argument("--limit", type=int, default=0, help="stop after N fetches (0 = all)")
    args = ap.parse_args()

    fetch_bodies(
        catalog_path=args.catalog,
        out_path=args.out,
        key_substr=args.key_substr,
        method_filter=args.method,
        limit=args.limit,
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
