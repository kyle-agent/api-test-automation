#!/usr/bin/env python3
"""Scrape full per-page documentation from the SCP API Reference into a local,
machine-readable store (data/api_docs.json).

Why this exists: every doc page is ~4.3 MB because it server-side-renders the
entire navigation tree before the actual content. The real content (Description,
Parameters table, Responses table, request/response examples, model field table)
sits at the END of the HTML. So we use a *suffix* HTTP Range request
(`Range: bytes=-N`) to download only the tail of each page — skipping the nav and
cutting bandwidth ~14x. The gateway intermittently 503s, so every request is
retried with backoff. The run is resumable: keys already present are skipped.

Output: data/api_docs.json
  {
    "endpoints": { "<cat>/<svc>/<name>": {method, path, description, deprecated,
                    support, parameters[], responses[], request_example,
                    response_example, ...}, ... },
    "models":    { "<cat>/<svc>/<model>": {name, fields[], ...}, ... }
  }

Then build_openapi.py assembles this into an OpenAPI 3.0 document.

"""
from __future__ import annotations

import argparse
import json
import re
import sys
import time
import urllib.request
from html.parser import HTMLParser
from pathlib import Path

BASE = "https://docs.e.samsungsdscloud.com"
ROOT = Path(__file__).resolve().parent.parent
CATALOG = ROOT / "data" / "api_catalog.json"
INDEX_CACHE = ROOT / "data" / ".apiref_index.html"
OUT = ROOT / "data" / "api_docs.json"

ENDPOINT_RANGE = 320_000  # tail bytes for an API page
MODEL_RANGE = 240_000     # tail bytes for a model page


def fetch_tail(url: str, nbytes: int, tries: int = 8, timeout: int = 60) -> str:
    backoff = 3
    last = None
    for attempt in range(tries):
        try:
            req = urllib.request.Request(
                url, headers={"User-Agent": "scp-docs/1.0", "Range": f"bytes=-{nbytes}"}
            )
            with urllib.request.urlopen(req, timeout=timeout) as r:
                if r.status in (200, 206):
                    return r.read().decode("utf-8", "replace")
                last = f"HTTP {r.status}"
        except Exception as exc:
            last = repr(exc)
        if attempt < tries - 1:
            time.sleep(backoff)
            backoff = min(backoff * 2, 25)
    raise RuntimeError(f"fetch failed {url}: {last}")


# ---------------------------------------------------------------- HTML parsing
class TableExtractor(HTMLParser):
    """Collect every <table> as a list of rows; each cell keeps its plain text
    plus any hyperlink targets (model refs) and whether it contains <em> markers
    like 'required'/'optional'."""

    def __init__(self):
        super().__init__()
        self.tables = []
        self._cur = None      # current table (list of rows)
        self._row = None
        self._cell = None
        self._in_cell = False
        self._depth_table = 0

    def handle_starttag(self, tag, attrs):
        a = dict(attrs)
        if tag == "table":
            self._depth_table += 1
            if self._depth_table == 1:
                self._cur = []
        elif tag == "tr" and self._cur is not None:
            self._row = []
        elif tag in ("td", "th") and self._row is not None:
            self._cell = {"text": [], "hrefs": [], "ems": []}
            self._in_cell = True
        elif tag == "br" and self._in_cell:
            self._cell["text"].append(" ")
        elif tag == "a" and self._in_cell and a.get("href"):
            self._cell["hrefs"].append(a["href"])

    def handle_endtag(self, tag):
        if tag == "table":
            if self._depth_table == 1 and self._cur is not None:
                self.tables.append(self._cur)
                self._cur = None
            self._depth_table = max(0, self._depth_table - 1)
        elif tag == "tr" and self._row is not None:
            self._cur.append(self._row)
            self._row = None
        elif tag in ("td", "th") and self._cell is not None:
            txt = " ".join("".join(self._cell["text"]).split())
            self._row.append(
                {"text": txt, "hrefs": self._cell["hrefs"], "ems": self._cell["ems"]}
            )
            self._cell = None
            self._in_cell = False
        elif tag == "em" and self._in_cell:
            pass

    def handle_data(self, data):
        if self._in_cell:
            self._cell["text"].append(data)
            # crude: capture required/optional words for the Name column
            for w in ("required", "optional"):
                if w in data:
                    self._cell["ems"].append(w)


def strip_tags(s: str) -> str:
    import html as H
    s = re.sub(r"<script\b.*?</script>", "", s, flags=re.S | re.I)
    s = re.sub(r"<style\b.*?</style>", "", s, flags=re.S | re.I)
    s = re.sub(r"<br\s*/?>", " ", s, flags=re.I)
    s = re.sub(r"<[^>]+>", " ", s)
    return H.unescape(re.sub(r"\s+", " ", s)).strip()


def content_region(tail: str) -> str:
    """Return HTML from the start of the page body content (the breadcrumb just
    before the <h2 id=description>) to the footer. The tail already excludes the
    nav; we cut from the last <h1 ...> which heads the content title."""
    # The content title is an <h1>; description/parameters follow. Cut from the
    # last <h1 to </main> (or end) to drop any trailing nav fragments.
    h1 = tail.rfind("<h1")
    seg = tail[h1:] if h1 >= 0 else tail
    end = seg.find("</main>")
    return seg[: end] if end >= 0 else seg


def parse_endpoint(tail: str) -> dict:
    seg = content_region(tail)
    out = {}
    # method + path (rendered as e.g. "post /v1/servers/{server_id}/password")
    m = re.search(r">\s*(get|post|put|delete|patch)\s+(/v[0-9][^<\s]*)", seg, re.I)
    if m:
        out["method"] = m.group(1).upper()
        out["path"] = m.group(2)
    # description: text between <h2 id=description> and the next <h2
    dm = re.search(r"<h2 id=description>(.*?)(?=<h2 |\Z)", seg, re.S)
    desc_html = dm.group(1) if dm else ""
    out["description"] = strip_tags(re.sub(r"^Description.*?self-link\"></a>", "", desc_html, flags=re.S))
    out["deprecated"] = bool(re.search(r"DEPRECATED|Deprecated", desc_html))

    te = TableExtractor()
    te.feed(seg)
    params, responses, support = [], [], []
    for tbl in te.tables:
        if not tbl:
            continue
        header = [c["text"].lower() for c in tbl[0]]
        rows = tbl[1:]
        if header[:3] == ["type", "name", "description"]:
            for r in rows:
                if len(r) < 4:
                    continue
                name_cell = r[1]
                name = re.sub(r"\b(required|optional)\b", "", name_cell["text"]).strip()
                if not _is_ident(name):
                    continue  # example text bled into a phantom row
                params.append({
                    "in": r[0]["text"],
                    "name": name,
                    "required": "required" in name_cell["ems"] or "required" in name_cell["text"],
                    "description": r[2]["text"],
                    "schema": r[3]["text"],
                    "schema_ref": _ref(r[3]["hrefs"]),
                    "default": r[4]["text"] if len(r) > 4 else "",
                })
        elif header and header[0].startswith("http"):
            for r in rows:
                if len(r) < 2:
                    continue
                responses.append({
                    "code": r[0]["text"],
                    "description": r[1]["text"],
                    "schema": r[2]["text"] if len(r) > 2 else "",
                    "schema_ref": _ref(r[2]["hrefs"]) if len(r) > 2 else None,
                })
        elif "버전" in header and "최소 지원 보장일" in " ".join(header):
            for r in rows:
                if len(r) >= 2:
                    support.append({"version": r[0]["text"], "min_support": r[1]["text"]})
    out["parameters"] = params
    out["responses"] = responses
    out["support"] = support
    # examples
    out["request_example"] = _example(seg, "example-http-request")
    out["response_example"] = _example(seg, "example-http-response")
    return out


def parse_model(tail: str) -> dict:
    seg = content_region(tail)
    te = TableExtractor()
    te.feed(seg)
    fields = []
    for tbl in te.tables:
        if not tbl:
            continue
        header = [c["text"].lower() for c in tbl[0]]
        if header[:2] == ["name", "description"]:
            for r in tbl[1:]:
                if len(r) < 2:
                    continue
                name_cell = r[0]
                name = re.sub(r"\b(required|optional)\b", "", name_cell["text"]).strip()
                if not _is_ident(name):
                    continue  # example text bled into a phantom row
                fields.append({
                    "name": name,
                    "required": "required" in name_cell["ems"] or "required" in name_cell["text"],
                    "description": r[1]["text"],
                    "schema": r[2]["text"] if len(r) > 2 else "",
                    "schema_ref": _ref(r[2]["hrefs"]) if len(r) > 2 else None,
                    "default": r[3]["text"] if len(r) > 3 else "",
                })
            break
    return {"fields": fields}


def _is_ident(name: str) -> bool:
    """API field/param names are snake_case/camelCase identifiers (optionally
    dotted for nested keys). Anything with spaces, backticks or dashes is example
    text that bled into the table from a malformed cell."""
    return bool(name) and bool(re.fullmatch(r"[A-Za-z_][A-Za-z0-9_.]*", name))


def _ref(hrefs):
    for h in hrefs:
        m = re.search(r"/models/([a-zA-Z0-9_.-]+)/?$", h)
        if m:
            return m.group(1)
    return None


def _example(seg, anchor):
    m = re.search(rf"<h2 id={anchor}>(.*?)(?=<h2 |\Z)", seg, re.S)
    if not m:
        return ""
    blocks = re.findall(r"<pre[^>]*><code[^>]*>(.*?)</code></pre>", m.group(1), re.S)
    return strip_tags(" \n".join(blocks)) if blocks else ""


# ---------------------------------------------------------------- discovery
def discover_models() -> list[dict]:
    h = INDEX_CACHE.read_text(errors="replace")
    seen = {}
    for c, s, name in re.findall(
        r"/apireference/([a-z0-9-]+)/([a-z0-9-]+)/models/([a-zA-Z0-9_.-]+?)/", h
    ):
        key = f"{c}/{s}/{name}"
        seen[key] = {
            "key": key, "category": c, "service": s, "name": name,
            "doc_url": f"{BASE}/apireference/{c}/{s}/models/{name}/",
        }
    return sorted(seen.values(), key=lambda e: e["key"])


def main() -> int:
    global OUT
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--only", choices=["endpoints", "models"], default=None)
    ap.add_argument("--key-substr", default="")
    ap.add_argument("--out", default=str(OUT), help="output JSON path (for sharded runs)")
    args = ap.parse_args()

    OUT = Path(args.out)
    store = json.loads(OUT.read_text()) if OUT.exists() else {}
    store.setdefault("endpoints", {})
    store.setdefault("models", {})

    cat = json.loads(CATALOG.read_text())
    endpoints = [e for e in cat if e.get("doc_url") and args.key_substr in e["key"]]
    models = [m for m in discover_models() if args.key_substr in m["key"]]

    jobs = []
    if args.only in (None, "endpoints"):
        jobs += [("endpoints", e, ENDPOINT_RANGE, parse_endpoint) for e in endpoints]
    if args.only in (None, "models"):
        jobs += [("models", m, MODEL_RANGE, parse_model) for m in models]

    todo = [j for j in jobs if j[1]["key"] not in store[j[0]]]
    print(f"{len(todo)} pages to scrape "
          f"({len(store['endpoints'])} endpoints + {len(store['models'])} models cached)",
          flush=True)

    done = 0
    for bucket, item, rng, parser in todo:
        try:
            tail = fetch_tail(item["doc_url"], rng)
            rec = parser(tail)
        except Exception as exc:
            print(f"  ERR {item['key']}: {exc}", flush=True)
            continue
        rec.update({k: item[k] for k in ("category", "service", "name")})
        rec["doc_url"] = item["doc_url"]
        store[bucket][item["key"]] = rec
        done += 1
        if done % 25 == 0:
            OUT.write_text(json.dumps(store, indent=2, ensure_ascii=False))
            print(f"  ... {done}/{len(todo)} "
                  f"(ep={len(store['endpoints'])} md={len(store['models'])})", flush=True)
        if args.limit and done >= args.limit:
            break

    OUT.write_text(json.dumps(store, indent=2, ensure_ascii=False))
    print(f"done: {len(store['endpoints'])} endpoints, {len(store['models'])} models -> {OUT}",
          flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
