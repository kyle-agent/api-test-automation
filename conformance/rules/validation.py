"""STATIC validation-discoverability check (OPAQUE-validation, AXIS 2).

Ported from ``tools/analyze_validation.py``. The question: for every
create/update operation, can a caller learn what makes a required string field
*valid* from the docs alone? A required free-form string with no enum and no
constraint hint in its Description is **undiscoverable** — you can only learn the
rule by trial-and-error against the live API (runtime issue #5).

This check is inherently *cross-context*: classifying one endpoint's required
fields needs the whole-spec ``models`` map (to resolve the body ``schema_ref``).
A single :class:`conformance.rules.Rule` returns at most one Finding, so the
multi-finding aggregation + the legacy ``data/validation_findings.json``
dual-write live in :mod:`conformance.static`. This module hosts the *pure,
reusable predicate* (:func:`field_is_undiscoverable`) and a spec-walking helper
(:func:`scan`) that both the static aggregator and tests can call. Everything
here is read-only — no I/O.
"""
from __future__ import annotations

import re
from collections import Counter
from typing import Optional

from core.results import Finding
from conformance.rules import SCOPE_SPEC, YELLOW

# Korean + English hints that a Description actually states a constraint.
CONSTRAINT_HINTS = re.compile(
    r"(\d+\s*[~\-–]\s*\d+"                       # 3~20  / 1-64
    r"|\d+\s*(자|글자|바이트|byte|chars?|characters?|digits?)"  # 20자, 64 bytes
    r"|이상|이하|최대|최소|초과|미만|범위"
    r"|영문|소문자|대문자|숫자|특수\s*문자|공백|한글"
    r"|시작|구성|포함하|허용|불가|제외"
    r"|패턴|정규\s*식|regex|pattern|format|형식|규칙"
    r"|min(imum)?|max(imum)?|length|길이"
    r"|^\^|\$$|\[a-z|\[A-Z|\\d)",               # an inline regex
    re.IGNORECASE,
)
ENUM_RE = re.compile(r"enum\s*\(", re.IGNORECASE)
# string-ish (free-form) schema cells
STRINGISH = re.compile(r"^\s*(any of \[)?\s*string", re.IGNORECASE)


def strip_example(desc: str) -> str:
    """Remove the trailing 'Example : ...' so it doesn't count as guidance."""
    return re.split(r"\bExample\b\s*:?", desc, 1, flags=re.IGNORECASE)[0].strip()


def field_is_undiscoverable(f: dict) -> bool:
    """True when a required field's validation rule cannot be learned from docs."""
    schema = f.get("schema", "") or ""
    if ENUM_RE.search(schema):
        return False                       # allowed values are listed
    if not STRINGISH.search(schema) and schema.strip():
        # typed (integer/boolean/object/array/ref) — shape is at least known
        if "$ref" not in schema and not schema.lower().startswith("any of [string"):
            return False
    body = strip_example(f.get("description", "") or "")
    if not body:
        return True                        # no description at all
    return not CONSTRAINT_HINTS.search(body)


def scan(docs: dict) -> dict:
    """Walk the spec and produce the validation-discoverability report.

    Returns the same structure as the legacy ``validation_findings.json``::

        {"summary": {...}, "operations": [ {endpoint, method, path, model,
                                            doc_url, undiscoverable_required_fields[]} ]}
    """
    eps = docs.get("endpoints", {})
    models = docs.get("models", {})

    per_ep = []
    counter: Counter = Counter()
    for k, e in eps.items():
        if e.get("method") not in ("POST", "PUT", "PATCH"):
            continue
        body_ref = next((p.get("schema_ref") for p in e.get("parameters", [])
                         if p.get("in") == "body" and p.get("schema_ref")), None)
        if not body_ref:
            continue
        mkey = f"{e.get('category')}/{e.get('service')}/{body_ref}"
        model = models.get(mkey)
        if not model:
            continue
        flagged = []
        for f in model.get("fields", []):
            if not f.get("required"):
                continue
            if field_is_undiscoverable(f):
                flagged.append({"field": f["name"], "schema": f.get("schema", ""),
                                "description": strip_example(f.get("description", ""))})
        if flagged:
            per_ep.append({"endpoint": k, "method": e["method"], "path": e.get("path"),
                           "model": body_ref, "doc_url": e.get("doc_url"),
                           "undiscoverable_required_fields": flagged})
            counter["endpoints"] += 1
            counter["fields"] += len(flagged)

    create_blind = [r for r in per_ep
                    if "create" in r["endpoint"].rsplit("/", 1)[-1].lower()]
    return {
        "summary": {
            "operations_with_undiscoverable_required_fields": counter["endpoints"],
            "total_undiscoverable_required_fields": counter["fields"],
            "create_operations_affected": len(create_blind),
        },
        "operations": sorted(per_ep,
                             key=lambda r: -len(r["undiscoverable_required_fields"])),
    }


def to_findings(report: dict) -> list[Finding]:
    """Turn a :func:`scan` report into per-operation unified Findings."""
    out: list[Finding] = []
    for op in report.get("operations", []):
        flds = [f["field"] for f in op.get("undiscoverable_required_fields", [])]
        if not flds:
            continue
        out.append(Finding(
            endpoint_key=op["endpoint"],
            rule_id="undiscoverable-params",
            severity=YELLOW,
            detail="required fields with no documented constraint: "
                   + ", ".join(flds[:8]),
            source="static",
            issue="19",
        ))
    return out


# scope advertised for callers that want to know this is a whole-spec analysis.
SCOPE = SCOPE_SPEC
