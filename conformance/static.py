"""AXIS 2 — STATIC spec analysis (design findings).

Ports ``tools/build_conformance.py``: aggregates the pre-computed static analysis
(``data/findings.json`` + ``data/validation_findings.json``) and the
runtime probe outputs into per-endpoint conformance colours, and additionally
runs the pluggable :mod:`conformance.rules` lens directly over the spec
(``data/api_docs.json``) so the design checks are extensible.

Findings are emitted to the unified results store via
:func:`core.results.record_finding` (``source="static"``), while the legacy
``data/conformance.json`` is *still written* (dual-write) so the existing
dashboard and baseline keep working unchanged.

Nothing here performs network I/O; everything is local file analysis.
"""
from __future__ import annotations

import json
import re
from collections import Counter, defaultdict
from pathlib import Path

from core.results import Finding, record_finding
from conformance import rules as rules_mod
from conformance.rules import validation as validation_rules

ROOT = Path(__file__).resolve().parent.parent
F = ROOT / "data"
R = ROOT / "reports"
OUT = F / "conformance.json"          # legacy dual-write target
DOCS = F / "api_docs.json"
CATALOG = F / "api_catalog.json"
FINDINGS_OUT = F / "findings.json"               # legacy analyze_docs dual-write
VALIDATION_OUT = F / "validation_findings.json"  # legacy analyze_validation dual-write

READ_VERBS = ("list", "show", "get", "detail", "describe")
WRITE_CREATE = ("create",)


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


# --- aggregate STATIC analysis (ported from tools/analyze_docs.py) ----------
# These checks span the whole catalog/spec (path collisions, duplicated
# operation sets, the model-level checks) or build counts that don't reduce to a
# single endpoint's Finding, so they live here rather than under rules/. They
# DUAL-WRITE the legacy ``data/findings.json`` group structure AND emit
# per-endpoint ``core.results.Finding(source="static")`` where it makes sense.

def _doc_url(e: dict) -> str:
    return e.get("doc_url", "")


def analyze_docs(*, emit_findings: bool = True) -> dict:
    """Port of ``tools/analyze_docs.py``.

    Reads ``data/api_catalog.json`` + ``data/api_docs.json``, builds the grouped
    findings structure, dual-writes ``data/findings.json``, and (when
    ``emit_findings``) records per-endpoint unified Findings for the checks that
    are endpoint-scoped. Returns the grouped findings dict.
    """
    cat = _load(CATALOG, [])
    docs = _load(DOCS, {"endpoints": {}, "models": {}})
    eps = docs.get("endpoints", {})
    models = docs.get("models", {})
    findings: dict = {}

    def _emit(key, rule_id, sev, detail, issue):
        if emit_findings:
            record_finding(Finding(endpoint_key=key, rule_id=rule_id, severity=sev,
                                   detail=detail, source="static", issue=str(issue)))

    # ---- A1: method vs verb mismatch -------------------------------------
    mism = []
    for x in cat:
        nm, meth = x["name"], x["method"]
        reason = None
        if nm.startswith(READ_VERBS) and meth != "GET":
            reason = "read-verb name but not GET"
        elif nm.startswith(WRITE_CREATE) and meth not in ("POST",):
            reason = "create-verb name but not POST"
        elif nm.startswith("delete") and meth != "DELETE":
            reason = "delete-verb name but not DELETE"
        if reason:
            mism.append((x["key"], meth, x["http_path"], reason))
            _emit(x["key"], "method-verb-mismatch", rules_mod.YELLOW,
                  f"{reason} ({meth} {x['http_path']})", 11)
    findings["method-verb-mismatch"] = {
        "title": "API design: HTTP method does not match the operation verb (reads via POST, create via GET)",
        "items": mism,
    }

    # ---- A2: inconsistent update verb + method ---------------------------
    upd = defaultdict(lambda: defaultdict(list))
    for x in cat:
        m = re.match(r"^(update|set|modify|change|edit|put|patch)", x["name"])
        if m:
            upd[m.group(1)][x["method"]].append(x["key"])
    upd_summary = {v: {meth: len(ks) for meth, ks in d.items()} for v, d in upd.items()}
    findings["inconsistent-update-verb"] = {
        "title": "API design: 'update' semantics are split across set/update/modify and PUT/PATCH/POST inconsistently",
        "items": upd_summary,
        "examples": {v: {meth: ks[:3] for meth, ks in d.items()} for v, d in upd.items()},
    }

    # ---- A3: path collisions across services -----------------------------
    by_mp = defaultdict(list)
    for x in cat:
        by_mp[(x["method"], x["http_path"])].append(x["key"])
    collisions = {f"{m} {p}": ks for (m, p), ks in by_mp.items()
                  if len({k.split('/')[1] for k in ks}) > 1}
    findings["path-collisions"] = {
        "title": "API design: identical method+path reused across unrelated services (no service namespacing)",
        "items": collisions,
    }

    # ---- A4: duplicated operation sets across services -------------------
    dup_groups = defaultdict(set)
    for (m, p), ks in by_mp.items():
        svcs = {k.split("/")[1] for k in ks}
        if len(svcs) > 1:
            dup_groups[frozenset(svcs)].add(f"{m} {p}")
    dups = {", ".join(sorted(g)): sorted(paths)
            for g, paths in dup_groups.items() if len(paths) >= 3}
    findings["duplicated-operations"] = {
        "title": "API design: whole operation sets are duplicated verbatim across two services",
        "items": dups,
    }

    # ---- A5: inconsistent path-param naming ------------------------------
    bare_id, uuid_named = [], []
    for x in cat:
        for prm in re.findall(r"\{([^}]+)\}", x["http_path"]):
            if prm == "id":
                bare_id.append((x["key"], x["http_path"]))
                _emit(x["key"], "param-naming", rules_mod.YELLOW,
                      f"bare {{id}} in {x['http_path']}", 14)
            if prm.endswith("_uuid"):
                uuid_named.append((x["key"], x["http_path"], prm))
                _emit(x["key"], "param-naming", rules_mod.YELLOW,
                      f"{{{prm}}} vs {{*_id}} in {x['http_path']}", 14)
    findings["inconsistent-param-naming"] = {
        "title": "API design: path id params are named inconsistently (bare {id} and {x_uuid} vs the usual {resource_id})",
        "items": {"bare_id": bare_id, "uuid_named": uuid_named},
    }

    # ===== documentation-completeness checks (need scraped docs) ==========
    if eps:
        no_desc = []
        for k, e in eps.items():
            if not (e.get("description") or "").strip():
                no_desc.append((k, _doc_url(e)))
                _emit(k, "missing-endpoint-description", rules_mod.YELLOW,
                      "endpoint has an empty Description", "")
        findings["missing-endpoint-description"] = {
            "title": "Docs: endpoints with an empty Description", "items": no_desc}

        param_nodesc = []
        for k, e in eps.items():
            for p in e.get("parameters", []):
                if p["in"] in ("path", "query") and not (p.get("description") or "").strip():
                    param_nodesc.append((k, p["in"], p["name"]))
        findings["missing-param-description"] = {
            "title": "Docs: path/query parameters with no description", "items": param_nodesc}

        no_err_schema, any_err_schema = [], 0
        for k, e in eps.items():
            errs = [r for r in e.get("responses", []) if re.match(r"[45]", str(r.get("code", "")))]
            if errs and all(not r.get("schema_ref")
                            and (str(r.get("schema", "")).lower() in ("", "none")) for r in errs):
                no_err_schema.append(k)
                _emit(k, "no-error-response-schema", rules_mod.YELLOW,
                      "4xx/5xx responses document no body schema", 15)
            if any(r.get("schema_ref") for r in errs):
                any_err_schema += 1
        findings["no-error-response-schema"] = {
            "title": "Docs: error responses (4xx/5xx) never document a response body/schema",
            "items": {"endpoints_without_any_error_schema": len(no_err_schema),
                      "endpoints_with_some_error_schema": any_err_schema,
                      "sample": no_err_schema[:30], "full": no_err_schema}}

        no_2xx_schema, by_method = [], Counter()
        for k, e in eps.items():
            ok = [r for r in e.get("responses", []) if str(r.get("code", "")).startswith("2")]
            if ok and all(not r.get("schema_ref")
                          and str(r.get("schema", "")).lower() in ("", "none") for r in ok):
                no_2xx_schema.append((k, e.get("method"), _doc_url(e)))
                by_method[e.get("method")] += 1
                if e.get("method") != "DELETE":
                    _emit(k, "no-success-schema", rules_mod.YELLOW,
                          f"{e.get('method')} 2xx documents no schema", 16)
        findings["no-success-response-schema"] = {
            "title": "Docs: success (2xx) response documents no schema",
            "by_method": dict(by_method), "items": no_2xx_schema}

        body_no_model = []
        for k, e in eps.items():
            for p in e.get("parameters", []):
                if p["in"] == "body" and p.get("required") and not p.get("schema_ref"):
                    body_no_model.append((k, p.get("schema", "")))
        findings["required-body-no-model"] = {
            "title": "Docs: a required request body with no model/schema linked", "items": body_no_model}

    if models:
        empty_models = [(k, m["doc_url"]) for k, m in models.items() if not m.get("fields")]
        findings["empty-models"] = {
            "title": "Docs: model pages that define no fields", "items": empty_models}

        field_nodesc, field_nodesc_models = 0, []
        for k, m in models.items():
            miss = [f["name"] for f in m.get("fields", []) if not (f.get("description") or "").strip()]
            if miss:
                field_nodesc += len(miss)
                field_nodesc_models.append((k, miss[:8]))
        findings["model-fields-no-description"] = {
            "title": "Docs: model fields with no description",
            "items": {"total_fields_missing_desc": field_nodesc,
                      "models_affected": len(field_nodesc_models),
                      "sample": field_nodesc_models[:25], "full": field_nodesc_models}}

        TYPOS = ["reuqest", "requst", "reqeust", "reqest", "resopnse", "reponse",
                 "respnse", "resonse", "udpate", "upadte", "delte", "craete",
                 "infomation", "informaion", "lenght", "recieve", "adress",
                 "paramter", "paramater", "attibute", "atribute", "defalut",
                 "seperate", "occured", "priviledge", "certifcate", "descrption",
                 "retrive", "retreive", "enviroment", "existance", "succesful",
                 "successfull", "threshhold", "bandwith", "namme", "nmae"]
        typo_models = []
        for k, m in models.items():
            n = m["name"].lower()
            for t in TYPOS:
                if t in n:
                    typo_models.append((k, m["name"], t))
        findings["model-name-typos"] = {
            "title": "Docs: likely typos in model names", "items": typo_models}

    if eps:
        deprecated = [(k, e.get("path"), list(e.get("support", [])))
                      for k, e in eps.items() if e.get("deprecated")]
        for k, _p, _s in deprecated:
            _emit(k, "deprecated", rules_mod.YELLOW, "DEPRECATED endpoint", 18)
        findings["deprecated-endpoints"] = {
            "title": "Docs: endpoints marked DEPRECATED in their description", "items": deprecated}

    FINDINGS_OUT.write_text(json.dumps(findings, indent=2, ensure_ascii=False))
    return findings


def analyze_validation(*, emit_findings: bool = True) -> dict:
    """Port of ``tools/analyze_validation.py`` (OPAQUE validation discoverability).

    Delegates the spec walk to :func:`conformance.rules.validation.scan`,
    dual-writes ``data/validation_findings.json``, and (when ``emit_findings``)
    records per-operation unified Findings. Returns the report dict.
    """
    docs = _load(DOCS, {"endpoints": {}, "models": {}})
    report = validation_rules.scan(docs)
    VALIDATION_OUT.write_text(json.dumps(report, indent=2, ensure_ascii=False))
    if emit_findings:
        for f in validation_rules.to_findings(report):
            record_finding(f)
    return report


# --- legacy aggregation (ported from tools/build_conformance.py) -------------
def build(*, emit_findings: bool = True) -> dict:
    """Aggregate static + runtime signals into per-endpoint conformance.

    Dual-writes ``data/conformance.json`` (legacy) and, when
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
    # Refresh the legacy STATIC inputs (dual-write data/findings.json +
    # data/validation_findings.json) before aggregating them into conformance.
    docs_groups = analyze_docs()
    val_report = analyze_validation()
    print(f"analyze_docs: {len(docs_groups)} groups -> {FINDINGS_OUT}")
    print(f"analyze_validation: {val_report['summary']} -> {VALIDATION_OUT}")

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
