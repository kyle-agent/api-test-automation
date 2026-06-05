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

# Does the error name the offending field? SCP wraps the field name in *escaped*
# quotes inside the JSON detail string (e.g.  Field \"name\" is invalid), so allow
# optional backslashes around the quotes.
NAMES_FIELD = re.compile(r'[Ff]ield\s*\\*"?[a-z_][a-z0-9_]*'      # Field \"name\"
                         r'|\\*"[a-z_][a-z0-9_.]*\\*"\s*(is|must|required|invalid|should)'
                         r'|"field"\s*:\s*\\*"[a-z_]', re.IGNORECASE)
# Does it at least state the constraint rule (length / charset / regex / type)?
RULE = re.compile(r"\d+\s*(to|~|-)\s*\d+|characters|digits|should have|should be|"
                  r"must be|\^\[|hyphens|lowercase|uppercase|required|valid Type", re.IGNORECASE)
# Useless: a bare placeholder that names neither the field nor the rule.
OPAQUE = re.compile(r"value_error|InvalidInputValue|Invalid input data", re.IGNORECASE)


def classify(status: int, body: str) -> str:
    if status != 400:
        return "other"
    body = body or ""
    if OPAQUE.search(body) and not NAMES_FIELD.search(body):
        return "opaque"                      # neither field nor rule (worst)
    if NAMES_FIELD.search(body):
        return "names_field"                 # structured field (often + rule)
    if RULE.search(body):
        return "rule_in_prose"               # rule stated, but no structured field
    return "opaque"


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
    from core.config import Settings
    from core.http_client import ApiClient, MutationBlocked

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

    results, tally = [], {"names_field": 0, "rule_in_prose": 0, "opaque": 0, "other": 0}
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
    op = [r for r in results if r["verdict"] == "opaque"]
    print("## RUNTIME validation probe (empty body → 400)\n")
    print(f"- probed **{len(tgts)}** create (POST) operations with required fields")
    print(f"- ✅ names the offending field (structured): **{tally['names_field']}**")
    print(f"- 🟡 states the rule in prose only: **{tally['rule_in_prose']}**")
    print(f"- ❌ OPAQUE — neither field nor rule: **{tally['opaque']}**")
    print(f"- ⚠️ other (non-400: auth/routing): **{tally['other']}**\n")
    if op:
        print("### OPAQUE 400s — caller can't tell field OR rule (the real problem)\n")
        print("| Endpoint | error body |\n|---|---|")
        for r in op:
            body = (r.get("body") or r.get("error") or "").replace("\n", " ").replace("|", "\\|")[:160]
            print(f"| `{r['endpoint']}` | {body} |")
    print(f"\n_wrote {OUT}_")
    return 0


if __name__ == "__main__":
    sys.exit(main())
