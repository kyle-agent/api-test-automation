"""Static validator for CRUD lifecycle fragments (no live calls).

Every service-agent runs this on its fragment before handing back; the
coordinator runs it on the whole merged set before integrating. It is the
machine-checkable half of the "definition of done" in agents/CAMPAIGN.md.

Checks (errors fail with exit 1; warnings are advisory):
  * the loader merges cleanly — no duplicate lifecycle ids across base+fragments;
  * each lifecycle has id + non-empty steps; flags unknown lifecycle/step keys;
  * each step is well-formed (method/path/name, expect_status ints, capture dicts,
    cleanup has method+path, poll has field/until or until_status);
  * every non-GET step path resolves to a real catalog endpoint (else it will NOT
    count toward write coverage — usually a path typo);  [warning]
  * every {placeholder} used in a path/body is produced by an earlier capture,
    a builtin ctx var, or an adopt/cert var (catches capture-name typos). [error]

Usage:
  python -m regression.scenarios.validate            # validate the full merged set
  python -m regression.scenarios.validate --service vpc   # focus one service + coverage delta
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

from regression.scenarios.loader import load_lifecycles, FRAGMENTS_DIR

_HERE = Path(__file__).parent
_PLACEHOLDER = re.compile(r"\{([a-zA-Z0-9_]+)\}")

LIFECYCLE_KEYS = {"id", "service", "enabled", "heavy", "steps", "credentials",
                  "needs_cert_material", "_note", "_comment", "_disabled_reason"}
STEP_KEYS = {"name", "method", "path", "service", "json", "params", "headers",
             "expect_status", "capture", "capture_soft", "cleanup", "poll",
             "wait", "retries", "retry_interval", "retry_on_status",
             "group", "optional", "destructive", "adopt", "probe_reads",
             "_note", "_comment"}
METHODS = {"GET", "POST", "PUT", "PATCH", "DELETE"}
BUILTINS = {"unique", "ualpha", "region", "today", "today_plus_5y",
            "shared_vpc_id", "shared_subnet_id",
            "cert_body", "private_key", "cert_chain"}


def _norm_path(p: str) -> str:
    p = (p or "").split("?")[0].strip("/")
    return "/".join("*" if "{" in s else s for s in p.split("/"))


def _catalog_index():
    cat = json.load(open(_HERE.parent.parent / "data" / "api_catalog.json"))
    idx = {}
    for e in cat:
        idx.setdefault(((e.get("method") or "").upper(),
                        _norm_path(e["http_path"]), e["service"]), e["key"])
    return idx


def _placeholders_in(obj):
    """All {var} names referenced anywhere in a string / nested dict / list."""
    out = set()
    if isinstance(obj, str):
        out |= set(_PLACEHOLDER.findall(obj))
    elif isinstance(obj, dict):
        for v in obj.values():
            out |= _placeholders_in(v)
    elif isinstance(obj, list):
        for v in obj:
            out |= _placeholders_in(v)
    return out


def validate(service_filter=None):
    errors, warnings = [], []
    try:
        lifecycles, source = load_lifecycles(with_sources=True)
    except ValueError as exc:
        print(f"ERROR (loader): {exc}")
        return 1
    catalog = _catalog_index()

    for lc in lifecycles:
        lid = lc.get("id", "<no-id>")
        src = source.get(lid, "?")
        svc = (lc.get("service") or "").split("/")[-1]
        if service_filter and svc != service_filter:
            continue
        where = f"{src}:{lid}"

        for k in lc:
            if k not in LIFECYCLE_KEYS:
                warnings.append(f"{where}: unknown lifecycle key '{k}'")
        steps = lc.get("steps")
        if not isinstance(steps, list) or not steps:
            errors.append(f"{where}: 'steps' must be a non-empty list")
            continue

        # placeholders available at a given point = builtins + captures seen so far
        available = set(BUILTINS)
        for i, step in enumerate(steps):
            sname = step.get("name", f"step[{i}]")
            sw = f"{where} step '{sname}'"
            for k in step:
                if k not in STEP_KEYS:
                    warnings.append(f"{sw}: unknown step key '{k}'")
            if "name" not in step:
                errors.append(f"{sw}: missing 'name'")
            if step.get("probe_reads"):
                # probe_reads is a read-only map of {key: templated_path}; no method
                used = _placeholders_in(step["probe_reads"])
                missing = used - available
                if missing:
                    warnings.append(f"{sw}: probe_reads uses undefined {sorted(missing)}")
                continue

            method = (step.get("method") or "").upper()
            if "path" in step:
                if method not in METHODS:
                    errors.append(f"{sw}: method '{method}' not in {sorted(METHODS)}")
                # placeholder check (path + body + params), before adding this
                # step's own captures
                used = _placeholders_in(step.get("path")) \
                    | _placeholders_in(step.get("json")) \
                    | _placeholders_in(step.get("params"))
                missing = used - available
                if missing:
                    errors.append(f"{sw}: references undefined placeholders "
                                  f"{sorted(missing)} (capture them earlier?)")
                # write step should resolve to a catalog endpoint (else 0 coverage).
                # Use the step's service override when present (engine: step_service
                # = step.service or lifecycle service) so cross-service steps in a
                # shared lifecycle resolve correctly.
                step_svc = (step.get("service") or svc)
                if method != "GET" and not step.get("adopt"):
                    key = ((method, _norm_path(step["path"]), step_svc))
                    if key not in catalog:
                        warnings.append(
                            f"{sw}: {method} {step['path']} does not resolve to a "
                            f"catalog endpoint for service '{svc}' — won't count "
                            f"toward write coverage (path typo?)")
            es = step.get("expect_status")
            if es is not None and not (isinstance(es, list)
                                       and all(isinstance(x, int) for x in es)):
                errors.append(f"{sw}: expect_status must be a list of ints")
            for cap_key in ("capture", "capture_soft"):
                cap = step.get(cap_key)
                if cap is not None and not isinstance(cap, dict):
                    errors.append(f"{sw}: {cap_key} must be a dict")
            cu = step.get("cleanup")
            if cu is not None:
                if not isinstance(cu, dict) or "method" not in cu or "path" not in cu:
                    errors.append(f"{sw}: cleanup needs 'method' and 'path'")
            poll = step.get("poll")
            if poll is not None:
                if not isinstance(poll, dict) or not (
                        poll.get("until_status") or (poll.get("field") and poll.get("until"))):
                    errors.append(f"{sw}: poll needs until_status OR field+until")

            # this step's captures become available to later steps
            for cap_key in ("capture", "capture_soft"):
                if isinstance(step.get(cap_key), dict):
                    available |= set(step[cap_key])

    for w in warnings:
        print(f"WARN  {w}")
    for e in errors:
        print(f"ERROR {e}")
    n_lc = len([lc for lc in lifecycles
                if not service_filter
                or (lc.get("service") or "").split("/")[-1] == service_filter])
    print(f"\n{n_lc} lifecycle(s) checked · {len(errors)} error(s) · "
          f"{len(warnings)} warning(s)")
    return 1 if errors else 0


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--service")
    args = ap.parse_args()
    rc = validate(args.service)
    if args.service:
        print(f"\nCoverage for '{args.service}' "
              f"(run `python -m spec.coverage_gap --service {args.service}`):")
    sys.exit(rc)


if __name__ == "__main__":
    main()
