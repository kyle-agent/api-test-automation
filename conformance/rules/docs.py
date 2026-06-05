"""Per-endpoint STATIC documentation / API-design rules (AXIS 2 lens).

Ported from ``tools/analyze_docs.py``: the checks that look at *one* endpoint at a
time become individual, registered :class:`conformance.rules.Rule` objects so the
design lens is extensible by simply adding rules. Each rule consumes an endpoint
doc dict (``SCOPE_ENDPOINT``) — the same shape stored under
``data/api_docs.json["endpoints"][key]`` — and returns a single
:class:`core.results.Finding` or ``None``.

The cross-endpoint / whole-spec aggregates from ``analyze_docs.py`` that cannot be
expressed as a one-endpoint-in / one-finding-out check (path collisions,
duplicated operation sets, inconsistent-update-verb spread, the model-level
checks) live in :mod:`conformance.static` instead, where they also dual-write the
legacy ``data/findings.json`` group structure.

These rules are pure/read-only: no I/O. Registration happens via
:func:`load_docs_rules`, invoked on import.
"""
from __future__ import annotations

import re
from typing import Optional

from core.results import Finding
from conformance.rules import (
    FunctionRule,
    SCOPE_ENDPOINT,
    YELLOW,
    register,
)

READ_VERBS = ("list", "show", "get", "detail", "describe")
WRITE_CREATE = ("create",)


def _key(ep: dict) -> str:
    """Endpoint key matching the catalog/api_docs key convention."""
    return f"{ep.get('category')}/{ep.get('service')}/{ep.get('name')}"


def _path(ep: dict) -> str:
    # api_docs endpoints carry ``path``; catalog rows carry ``http_path``.
    return ep.get("http_path") or ep.get("path") or ""


# --- A1: method vs verb mismatch -------------------------------------------
def _method_verb_mismatch(ep: dict) -> Optional[Finding]:
    name = (ep.get("name") or "").lower()
    meth = (ep.get("method") or "").upper()
    if not name or not meth:
        return None
    reason = None
    if name.startswith(READ_VERBS) and meth != "GET":
        reason = "read-verb name but not GET"
    elif name.startswith(WRITE_CREATE) and meth != "POST":
        reason = "create-verb name but not POST"
    elif name.startswith("delete") and meth != "DELETE":
        reason = "delete-verb name but not DELETE"
    if reason is None:
        return None
    return Finding(
        endpoint_key=_key(ep),
        rule_id="method-verb-mismatch",
        severity=YELLOW,
        detail=f"{reason} ({meth} {_path(ep)})",
        source="static",
        issue="11",
    )


# --- A5: inconsistent path-param naming ------------------------------------
def _inconsistent_param_naming(ep: dict) -> Optional[Finding]:
    path = _path(ep)
    params = re.findall(r"\{([^}]+)\}", path)
    bare = [p for p in params if p == "id"]
    uuid = [p for p in params if p.endswith("_uuid")]
    if not bare and not uuid:
        return None
    bits = []
    if bare:
        bits.append(f"bare {{id}} in {path}")
    if uuid:
        bits.append(f"{{{uuid[0]}}} vs {{*_id}} in {path}")
    return Finding(
        endpoint_key=_key(ep),
        rule_id="param-naming",
        severity=YELLOW,
        detail="; ".join(bits),
        source="static",
        issue="14",
    )


# --- B7: empty endpoint description ----------------------------------------
def _missing_endpoint_description(ep: dict) -> Optional[Finding]:
    if (ep.get("description") or "").strip():
        return None
    return Finding(
        endpoint_key=_key(ep),
        rule_id="missing-endpoint-description",
        severity=YELLOW,
        detail="endpoint has an empty Description in the spec",
        source="static",
        issue="",
    )


# --- B8: path/query parameters with no description -------------------------
def _missing_param_description(ep: dict) -> Optional[Finding]:
    missing = [p["name"] for p in ep.get("parameters", [])
               if p.get("in") in ("path", "query")
               and not (p.get("description") or "").strip()]
    if not missing:
        return None
    return Finding(
        endpoint_key=_key(ep),
        rule_id="missing-param-description",
        severity=YELLOW,
        detail="path/query params with no description: " + ", ".join(missing[:8]),
        source="static",
        issue="",
    )


# --- B11: 4xx/5xx responses never document a schema ------------------------
def _no_error_response_schema(ep: dict) -> Optional[Finding]:
    errs = [r for r in ep.get("responses", [])
            if re.match(r"[45]", str(r.get("code", "")))]
    if not errs:
        return None
    if all(not r.get("schema_ref")
           and (str(r.get("schema", "")).lower() in ("", "none"))
           for r in errs):
        return Finding(
            endpoint_key=_key(ep),
            rule_id="no-error-response-schema",
            severity=YELLOW,
            detail="4xx/5xx responses document no body schema",
            source="static",
            issue="15",
        )
    return None


# --- B12: 2xx response documents no schema (skip DELETE) -------------------
def _no_success_response_schema(ep: dict) -> Optional[Finding]:
    meth = (ep.get("method") or "").upper()
    if meth == "DELETE":
        return None
    ok = [r for r in ep.get("responses", [])
          if str(r.get("code", "")).startswith("2")]
    if not ok:
        return None
    if all(not r.get("schema_ref")
           and str(r.get("schema", "")).lower() in ("", "none")
           for r in ok):
        return Finding(
            endpoint_key=_key(ep),
            rule_id="no-success-schema",
            severity=YELLOW,
            detail=f"{meth} 2xx documents no schema",
            source="static",
            issue="16",
        )
    return None


# --- B13: required body but no model linked --------------------------------
def _required_body_no_model(ep: dict) -> Optional[Finding]:
    blind = [p for p in ep.get("parameters", [])
             if p.get("in") == "body" and p.get("required") and not p.get("schema_ref")]
    if not blind:
        return None
    return Finding(
        endpoint_key=_key(ep),
        rule_id="required-body-no-model",
        severity=YELLOW,
        detail="required request body has no model/schema linked",
        source="static",
        issue="",
    )


# --- C15: deprecated endpoints ---------------------------------------------
def _deprecated_endpoint(ep: dict) -> Optional[Finding]:
    if not ep.get("deprecated"):
        return None
    return Finding(
        endpoint_key=_key(ep),
        rule_id="deprecated",
        severity=YELLOW,
        detail="DEPRECATED endpoint",
        source="static",
        issue="18",
    )


_DOCS_RULES = [
    FunctionRule("method-verb-mismatch", YELLOW, SCOPE_ENDPOINT, _method_verb_mismatch),
    FunctionRule("param-naming", YELLOW, SCOPE_ENDPOINT, _inconsistent_param_naming),
    FunctionRule("missing-endpoint-description", YELLOW, SCOPE_ENDPOINT, _missing_endpoint_description),
    FunctionRule("missing-param-description", YELLOW, SCOPE_ENDPOINT, _missing_param_description),
    FunctionRule("no-error-response-schema", YELLOW, SCOPE_ENDPOINT, _no_error_response_schema),
    FunctionRule("no-success-schema", YELLOW, SCOPE_ENDPOINT, _no_success_response_schema),
    FunctionRule("required-body-no-model", YELLOW, SCOPE_ENDPOINT, _required_body_no_model),
    FunctionRule("deprecated", YELLOW, SCOPE_ENDPOINT, _deprecated_endpoint),
]


def load_docs_rules() -> None:
    """Register all per-endpoint docs/design rules (idempotent by rule id)."""
    for r in _DOCS_RULES:
        register(r)


load_docs_rules()
