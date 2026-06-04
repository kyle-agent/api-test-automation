#!/usr/bin/env python3
"""Extract real example request bodies from the SCP API Reference pages.

Each API doc page server-side-renders its "Example HTTP request" body as a
syntax-highlighted code block (every token wrapped in <span>). The catalog
builder (build_catalog.py) only reads the first 12 KB (method+path from the
<head> meta); this tool fetches the FULL page and recovers the example request
body — the only machine-readable source of required fields / enum values, since
the rendered docs expose no OpenAPI schema and the gateway returns unnamed
"Field required" errors.

Output: framework/api_bodies.json  ->  { "<catalog key>": {<example body>}, ... }
Resumable: keys already present are skipped. Filter with --key-substr / --method.

Usage:
  python tools/fetch_request_bodies.py --key-substr security/kms
  python tools/fetch_request_bodies.py            # all POST/PUT/PATCH endpoints
"""
from __future__ import annotations
import argparse, html, json, re, time, urllib.request
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
    """Recover the example request JSON: the balanced {...} object immediately
    preceding the 'Example HTTP response' marker, after scripts/styles (which
    embed their own braces) are removed."""
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


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--catalog", default=str(CATALOG))
    ap.add_argument("--out", default=str(OUT))
    ap.add_argument("--key-substr", default="", help="only endpoints whose key contains this")
    ap.add_argument("--method", default="", help="restrict to one method (POST/PUT/PATCH)")
    ap.add_argument("--limit", type=int, default=0, help="stop after N fetches (0 = all)")
    args = ap.parse_args()

    cat = json.load(open(args.catalog))
    out = json.load(open(args.out)) if Path(args.out).exists() else {}
    todo = [e for e in cat
            if e["method"].upper() in (
                {args.method.upper()} if args.method else WRITE_METHODS)
            and args.key_substr in e["key"]
            and e["key"] not in out]
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
            json.dump(out, open(args.out, "w"), indent=2, ensure_ascii=False, sort_keys=True)
        if args.limit and done >= args.limit:
            break

    json.dump(out, open(args.out, "w"), indent=2, ensure_ascii=False, sort_keys=True)
    print(f"wrote {len(out)} bodies -> {args.out}")


if __name__ == "__main__":
    main()
