#!/usr/bin/env python3
"""Export the full-analysis results as CSVs for hand-off to the dev teams.

Outputs (reports/csv/):
  1. validation_required_fields.csv  - every REQUIRED field of create/update
     bodies, with its documented-constraint status (the #19 data: which params
     have no pattern/length/enum in the docs).
  2. error_response_coverage.csv     - every endpoint (all 1372) with whether
     its success (2xx) and error (4xx/5xx) responses document a schema (#15/#16).
  3. runtime_validation_probe.csv    - the live probe verdict per create op
     (names_field / rule_in_prose / opaque / other) + error excerpt.

Data input relocated framework/ -> data/ to match the new layout; report paths
are preserved. The runtime-probe classifier is sourced from conformance.runtime
(the kernel's runtime axis) instead of the original standalone tools module.
"""
from __future__ import annotations

import csv
import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DOCS = ROOT / "data" / "api_docs.json"
PROBE = ROOT / "reports" / "validation_probe.json"
OUTDIR = ROOT / "reports" / "csv"

HAS_PATTERN = re.compile(r"Pattern\s*:", re.I)
HAS_LENGTH = re.compile(r"(Minimum|Maximum)\s+length\s*:", re.I)
HAS_ENUM = re.compile(r"enum\s*\(", re.I)
STRINGISH = re.compile(r"^\s*(any of \[)?\s*string", re.I)


def strip_example(desc: str) -> str:
    return re.split(r"\bExample\b\s*:?", desc or "", 1, flags=re.I)[0].strip()


def constraint_status(schema: str, desc: str) -> str:
    schema = schema or ""
    if HAS_ENUM.search(schema):
        return "enum"
    if HAS_PATTERN.search(desc or ""):
        return "pattern"
    if HAS_LENGTH.search(desc or ""):
        return "length_only"
    if schema.strip() and not STRINGISH.search(schema):
        return "typed_non_string"
    return "NONE"          # free-form string, no documented rule


def export_fields(docs):
    rows = []
    for k, e in docs["endpoints"].items():
        if e.get("method") not in ("POST", "PUT", "PATCH"):
            continue
        ref = next((p.get("schema_ref") for p in e.get("parameters", [])
                    if p["in"] == "body" and p.get("schema_ref")), None)
        if not ref:
            continue
        m = docs["models"].get(f"{e['category']}/{e['service']}/{ref}")
        if not m:
            continue
        for f in m.get("fields", []):
            if not f.get("required"):
                continue
            desc = strip_example(f.get("description", ""))
            rows.append({
                "category": e["category"], "service": e["service"],
                "operation": e["name"], "method": e["method"], "http_path": e.get("path"),
                "body_model": ref, "field": f["name"],
                "is_string": bool(STRINGISH.search(f.get("schema", "") or "")),
                "schema_type": f.get("schema", ""),
                "constraint_status": constraint_status(f.get("schema", ""), f.get("description", "")),
                "description": desc,
                "doc_url": e.get("doc_url"),
            })
    return rows


def export_responses(docs):
    rows = []
    for k, e in docs["endpoints"].items():
        resp = e.get("responses", [])
        codes = [str(r.get("code", "")) for r in resp]
        succ = [r for r in resp if str(r.get("code", "")).startswith("2")]
        errs = [r for r in resp if re.match(r"[45]", str(r.get("code", "")))]
        rows.append({
            "category": e["category"], "service": e["service"],
            "operation": e["name"], "method": e.get("method"), "http_path": e.get("path"),
            "response_codes": "|".join(codes),
            "has_success_schema": any(r.get("schema_ref") for r in succ),
            "has_error_schema": any(r.get("schema_ref") for r in errs),
            "doc_url": e.get("doc_url"),
        })
    return rows


def _load_classifier():
    """Return the runtime validation-probe classifier ``classify(status, body)``.

    The original tool loaded this from a standalone tools/probe_validation.py;
    in the new layout the classifier is the one in the conformance runtime axis.
    Falls back to a no-op verdict if conformance is unavailable so the other
    CSV exports still run.
    """
    try:
        from conformance.runtime import classify
        return classify
    except Exception:
        return lambda status, body: "unknown"


def export_probe(docs):
    if not PROBE.exists():
        return []
    classify = _load_classifier()
    data = json.loads(PROBE.read_text())
    rows = []
    for r in data.get("results", []):
        e = docs["endpoints"].get(r["endpoint"], {})
        body = (r.get("body") or r.get("error") or "").replace("\n", " ")
        st = r.get("status") if isinstance(r.get("status"), int) else 0
        verdict = classify(st, r.get("body", ""))   # recompute with fixed classifier
        rows.append({
            "endpoint": r["endpoint"], "method": e.get("method", "POST"),
            "http_path": r.get("path", e.get("path", "")),
            "status": r.get("status", ""), "verdict": verdict,
            "error_excerpt": body[:300],
        })
    return rows


def write_csv(name, rows):
    OUTDIR.mkdir(parents=True, exist_ok=True)
    if not rows:
        return
    p = OUTDIR / name
    with p.open("w", newline="", encoding="utf-8-sig") as fh:   # BOM => Excel-friendly
        w = csv.DictWriter(fh, fieldnames=list(rows[0].keys()))
        w.writeheader()
        w.writerows(rows)
    print(f"  {name}: {len(rows)} rows")


def main() -> int:
    docs = json.loads(DOCS.read_text())
    print("writing CSVs ->", OUTDIR)
    fields = export_fields(docs)
    write_csv("validation_required_fields.csv", fields)
    none = [r for r in fields if r["constraint_status"] == "NONE"]
    print(f"    (required fields total={len(fields)}, no-constraint={len(none)})")
    write_csv("error_response_coverage.csv", export_responses(docs))
    write_csv("runtime_validation_probe.csv", export_probe(docs))
    return 0


if __name__ == "__main__":
    import sys
    sys.exit(main())
