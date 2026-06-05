#!/usr/bin/env python3
"""RUNTIME probe (needs SCP credentials — runs in CI): for each *create* (POST)
operation whose request body has at least one REQUIRED field, send an empty
`{}` body and record whether the resulting 400 ValidationError *names the
offending field*.

This is the runtime counterpart to tools/analyze_validation.py (the STATIC,
docs-only view). Together they answer: "for which parameters can a caller NOT
tell what to send — neither from the docs nor from the API's own error?" (cf. #5).

Safety:
  * POST-only, and only endpoints whose body model has >=1 required field, so an
    empty body fails validation *before* anything is created (no billable
    resource is made, every call is expected to be a 400).
  * collection endpoints only (no path params) — nothing is addressed/mutated.
  * still a mutating verb, so it is double-gated: SCP_PROBE_VALIDATION=true AND
    the client's own SCP_ALLOW_MUTATIONS=true gate.

Classification per response:
  names_field : the 400 body identifies which field is wrong (good)
  field_less  : 400 but does NOT say which field (the problem this catches)
  other       : non-400 (auth error, 5xx, accidental 2xx, ...)

Output: reports/validation_probe.json + a Markdown summary on stdout.

Usage (CI, with creds):
  SCP_PROBE_VALIDATION=true SCP_ALLOW_MUTATIONS=true python tools/probe_validation.py
  ... --category database --limit 50
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
DOCS = ROOT / "framework" / "api_docs.json"
OUT = ROOT / "reports" / "validation_probe.json"

# Does the error name a field? a field token, a quoted field, or a JSON path.
NAMES_FIELD = re.compile(
    r"\bfield\b\s*[\"']?[a-z_][a-z0-9_]*"
    r"|[\"'][a-z_][a-z0-9_.]*[\"']\s*(is|must|required|invalid|cannot|should)"
    r"|\$\.[a-z_]"
    r"|\"field\"\s*:\s*\"[a-z_]",
    re.IGNORECASE,
)


def classify(status: int, body: str) -> str:
    if status != 400:
        return "other"
    return "names_field" if NAMES_FIELD.search(body or "") else "field_less"


def targets(docs, category):
    models = docs["models"]
    out = []
    for k, e in docs["endpoints"].items():
        if e.get("method") != "POST":
            continue
        path = e.get("path") or "{"
        if "{" in path:                      # skip path-param endpoints
            continue
        if category and category not in e["category"]:
            continue
        ref = next((p.get("schema_ref") for p in e.get("parameters", [])
                    if p["in"] == "body" and p.get("schema_ref")), None)
        if not ref:
            continue
        m = models.get(f"{e['category']}/{e['service']}/{ref}")
        if not m or not any(f.get("required") for f in m.get("fields", [])):
            continue                          # empty body must be guaranteed-invalid
        out.append((k, e))
    return out


def main() -> int:
    if os.environ.get("SCP_PROBE_VALIDATION") != "true":
        print("Refusing to run: set SCP_PROBE_VALIDATION=true (RUNTIME probe; needs "
              "SCP creds + SCP_ALLOW_MUTATIONS=true). Run in CI.")
        return 2
    from framework.config import Settings
    from framework.client import ApiClient, MutationBlocked

    ap = argparse.ArgumentParser()
    ap.add_argument("--category", default="")
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--sleep", type=float, default=0.2)
    args = ap.parse_args()

    cfg = Settings()
    cfg.require_credentials()
    client = ApiClient(cfg)

    docs = json.loads(DOCS.read_text())
    tgts = targets(docs, args.category)
    if args.limit:
        tgts = tgts[: args.limit]

    results, tally = [], {"names_field": 0, "field_less": 0, "other": 0}
    for k, e in tgts:
        try:
            resp = client.request("POST", e["path"], json={}, service=e["service"])
            verdict = classify(resp.status, resp.raw_text)
            results.append({"endpoint": k, "path": e["path"], "status": resp.status,
                            "verdict": verdict, "body": (resp.raw_text or "")[:500]})
        except MutationBlocked as exc:
            print(f"::error::{exc}")
            return 3
        except Exception as exc:
            results.append({"endpoint": k, "path": e["path"], "verdict": "other",
                            "error": str(exc)})
            verdict = "other"
        tally[verdict] += 1
        time.sleep(args.sleep)

    OUT.parent.mkdir(exist_ok=True)
    OUT.write_text(json.dumps({"summary": tally, "count": len(tgts), "results": results},
                              indent=2, ensure_ascii=False))

    # --- Markdown summary (stdout; CI appends to the job summary) -------------
    fl = [r for r in results if r["verdict"] == "field_less"]
    nf = [r for r in results if r["verdict"] == "names_field"]
    print("## RUNTIME validation probe (empty body → 400)\n")
    print(f"- probed **{len(tgts)}** create (POST) operations with required fields")
    print(f"- ✅ names the field: **{tally['names_field']}**")
    print(f"- ❌ field-less 400 (caller can't tell which field): **{tally['field_less']}**")
    print(f"- ⚠️ other (non-400): **{tally['other']}**\n")
    if fl:
        print("### Field-less 400s (the problem)\n")
        print("| Endpoint | sample error body |\n|---|---|")
        for r in fl[:60]:
            body = (r.get("body") or "").replace("\n", " ").replace("|", "\\|")[:160]
            print(f"| `{r['endpoint']}` | {body} |")
    if nf:
        print(f"\n<details><summary>{len(nf)} that DO name the field</summary>\n")
        for r in nf:
            print(f"- `{r['endpoint']}`")
        print("\n</details>")
    print(f"\n_wrote {OUT}_")
    return 0


if __name__ == "__main__":
    sys.exit(main())
