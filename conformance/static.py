"""AXIS 2 — STATIC spec analysis (design findings).

Ports ``tools/build_conformance.py``: aggregates the pre-computed static analysis
(``framework/findings.json`` + ``framework/validation_findings.json``) and the
runtime probe outputs into per-endpoint conformance colours, and additionally
runs the pluggable :mod:`conformance.rules` lens directly over the spec
(``framework/api_docs.json``) so the design checks are extensible.

Findings are emitted to the unified results store via
:func:`core.results.record_finding` (``source="static"``), while the legacy
``framework/conformance.json`` is *still written* (dual-write) so the existing
dashboard and baseline keep working unchanged.

Nothing here performs network I/O; everything is local file analysis.
"""
from __future__ import annotations

import json
from collections import defaultdict
from pathlib import Path

from core.results import Finding, record_finding
from conformance import rules as rules_mod

ROOT = Path(__file__).resolve().parent.parent
F = ROOT / "framework"
R = ROOT / "reports"
OUT = F / "conformance.json"          # legacy dual-write target
DOCS = F / "api_docs.json"


def _load(p, default=None):
    p = Path(p)
    return json.loads(p.read_text()) if p.exists() else (default if default is not None else {})


# --- pluggable rule lens over the spec --------------------------------------
def run_spec_rules(docs: dict | None = None, *, emit: bool = True) -> list[Finding]:
    """Run every endpoint/spec-scoped rule over the spec and return Findings.

    When ``emit`` is True each Finding is also written to the unified results
    store. This is the *extensible* half of static analysis — adding a rule
    module is all that's needed to grow it.
    """
    docs = docs if docs is not None else _load(DOCS, {"endpoints": {}, "models": {}})
    endpoints = docs.get("endpoints", {})
    found: list[Finding] = []

    endpoint_rules = rules_mod.rules(scope=rules_mod.SCOPE_ENDPOINT)
    for _key, ep in endpoints.items():
        for rule in endpoint_rules:
            try:
                f = rule.check(ep)
            except Exception:
                f = None  # a misbehaving rule must never break analysis
            if f is not None:
                found.append(f)

    spec_rules = rules_mod.rules(scope=rules_mod.SCOPE_SPEC)
    for rule in spec_rules:
        try:
            f = rule.check(docs)
        except Exception:
            f = None
        if f is not None:
            found.append(f)

    if emit:
        for f in found:
            record_finding(f)
    return found


# --- legacy aggregation (ported from tools/build_conformance.py) -------------
def build(*, emit_findings: bool = True) -> dict:
    """Aggregate static + runtime signals into per-endpoint conformance.

    Dual-writes ``framework/conformance.json`` (legacy) and, when
    ``emit_findings`` is True, records each per-endpoint item as a unified
    :class:`core.results.Finding` (``source`` carried through from the item).
    Returns the assembled conformance dict.
    """
    cat = _load(F / "api_catalog.json", [])
    keys = {e["key"] for e in cat}
    findings = _load(F / "findings.json", {})
    val = _load(F / "validation_findings.json", {})

    items = defaultdict(list)   # endpoint key -> [item]
    emitted: list[Finding] = []

    def add(key, sev, typ, src, detail, issue):
        if key in keys:
            items[key].append({"sev": sev, "type": typ, "src": src,
                               "detail": detail, "issue": issue})
            if emit_findings:
                f = Finding(endpoint_key=key, rule_id=typ, severity=sev,
                            detail=detail, source=src, issue=str(issue))
                record_finding(f)
                emitted.append(f)

    # ---- STATIC (findings.json) -----------------------------------------
    for k, m, p, reason in findings.get("method-verb-mismatch", {}).get("items", []):
        add(k, "yellow", "method-verb", "static", f"{reason} ({m} {p})", 11)
    pn = findings.get("inconsistent-param-naming", {}).get("items", {})
    for k, p in pn.get("bare_id", []):
        add(k, "yellow", "param-naming", "static", f"bare {{id}} in {p}", 14)
    for k, p, prm in pn.get("uuid_named", []):
        add(k, "yellow", "param-naming", "static", f"{{{prm}}} vs {{*_id}} in {p}", 14)
    for row in findings.get("no-success-response-schema", {}).get("items", []):
        k, meth = row[0], row[1]
        if meth != "DELETE":
            add(k, "yellow", "no-success-schema", "static", f"{meth} 2xx documents no schema", 16)
    for row in findings.get("deprecated-endpoints", {}).get("items", []):
        add(row[0], "yellow", "deprecated", "static", "DEPRECATED endpoint", 18)

    # ---- STATIC (validation discoverability) ----------------------------
    for op in val.get("operations", []):
        flds = [f["field"] for f in op.get("undiscoverable_required_fields", [])]
        if flds:
            add(op["endpoint"], "yellow", "undiscoverable-params", "static",
                "required fields with no documented constraint: " + ", ".join(flds[:8]), 19)

    # ---- RUNTIME --------------------------------------------------------
    for r in _load(R / "runtime_status.json", {}).get("results", []):
        if r.get("klass") == "server_5xx_BUG":
            add(r["endpoint"], "red", "5xx-on-bad-input", "runtime",
                f"empty body -> {r.get('status')} (should be 400)", 33)
    for r in _load(R / "runtime_notfound.json", {}).get("results", []):
        s = r.get("status_nonexistent_id")
        ep, path = r["endpoint"], r.get("path", "")
        name = ep.rsplit("/", 1)[-1]
        # name-availability checks legitimately return 200 for any value
        if "checkduplication" in name or "check-duplication" in path or "duplication" in name:
            continue
        if s == 200:
            if name.startswith("list"):   # empty list for a non-existent parent (debatable)
                add(ep, "yellow", "notfound-200-list", "runtime",
                    "sub-resource list of a non-existent parent -> 200 (empty), not 404", 35)
            else:
                add(ep, "red", "notfound-200", "runtime",
                    "non-existent id -> 200 (should be 404)", 34)
        elif s in (400, 403):
            add(ep, "yellow", "notfound-inconsistent", "runtime",
                f"non-existent id -> {s} (not 404)", 35)
    for src in ("runtime_schema.json", "runtime_schema_live.json"):
        for r in _load(R / src, {}).get("results", []):
            extra = r.get("undocumented_fields") or r.get("item_undocumented_fields")
            miss = r.get("missing_required_fields") or r.get("item_missing_required")
            if miss:
                add(r["endpoint"], "red", "schema-missing-field", "runtime",
                    f"response omits documented required field(s): {miss}", 37)
            elif extra:
                add(r["endpoint"], "yellow", "schema-undocumented-field", "runtime",
                    f"response has undocumented field(s): {extra}", 37)
    for r in _load(R / "runtime_pagination.json", {}).get("results", []):
        if r.get("status") == 200 and r.get("respects_size") is False:
            add(r["endpoint"], "yellow", "pagination", "runtime",
                f"ignores size=1 (returned {r.get('returned_items_at_size1')})", 38)
    for r in _load(R / "validation_probe.json", {}).get("results", []):
        body = r.get("body", "") or ""
        if r.get("status") == 400 and ("value_error" in body or "InvalidInputValue" in body):
            add(r["endpoint"], "red", "opaque-validation", "runtime",
                "400 names neither field nor rule", 5)

    # ---- assemble -------------------------------------------------------
    by_endpoint = {}
    for k in keys:
        its = items.get(k, [])
        status = "red" if any(i["sev"] == "red" for i in its) else \
                 "yellow" if its else "green"
        by_endpoint[k] = {"status": status, "items": its}

    counts = {"green": 0, "yellow": 0, "red": 0}
    for v in by_endpoint.values():
        counts[v["status"]] += 1

    no_err = findings.get("no-error-response-schema", {}).get("items", {})
    systemic = [
        {"type": "error-schema-undocumented", "issue": 15, "scope": "all endpoints",
         "detail": "4xx/5xx responses document no schema; ≥3 different error envelopes",
         "count": no_err.get("endpoints_without_any_error_schema", 0)},
        {"type": "unauth-404", "issue": 36, "scope": "all services",
         "detail": "unauthenticated request -> 404 + Spring envelope (not 401)", "count": 58},
        {"type": "no-cors", "issue": 39, "scope": "all services",
         "detail": "OPTIONS -> 403; no Allow/CORS headers", "count": 58},
        {"type": "accept-language-ignored", "issue": 40, "scope": "most endpoints",
         "detail": "error messages English-only regardless of Accept-Language", "count": 124},
        {"type": "path-collisions", "issue": 13, "scope": "75 path groups",
         "detail": "same method+path reused across services (no namespacing)",
         "count": len(findings.get("path-collisions", {}).get("items", {}))},
        {"type": "model-fields-no-description", "issue": 17, "scope": "432 models",
         "detail": "model fields with empty description",
         "count": findings.get("model-fields-no-description", {}).get("items", {}).get("models_affected", 0)},
    ]

    conformance = {
        "summary": {**counts, "total": len(keys)},
        "systemic": systemic,
        "by_endpoint": by_endpoint,
    }
    # legacy dual-write
    OUT.write_text(json.dumps(conformance, indent=2, ensure_ascii=False))
    return conformance


def main() -> None:
    conformance = build()
    counts = conformance["summary"]
    by_endpoint = conformance["by_endpoint"]
    total = counts["total"]
    print(f"conformance: green={counts['green']} yellow={counts['yellow']} "
          f"red={counts['red']} / {total} -> {OUT}")
    reds = [k for k, v in by_endpoint.items() if v["status"] == "red"]
    print("red endpoints:", len(reds))
    for k in reds[:20]:
        print("  ", k, "::", "; ".join(i["type"] for i in by_endpoint[k]["items"]))

    # also run the pluggable spec rules (emits unified findings)
    spec_findings = run_spec_rules()
    print(f"spec-rule findings: {len(spec_findings)} "
          f"(rules: {', '.join(r.id for r in rules_mod.rules(scope=rules_mod.SCOPE_ENDPOINT))})")


if __name__ == "__main__":
    main()
