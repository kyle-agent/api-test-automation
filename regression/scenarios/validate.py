"""Static validator for CRUD lifecycle fragments (no live calls).

Every service-agent runs this on its fragment before handing back; the
coordinator runs it on the whole merged set before integrating. It is the
machine-checkable half of the "definition of done" in docs/agent-team.md.

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
                  "needs_cert_material", "requires_env",
                  "_note", "_comment", "_disabled_reason", "_replaced_by",
                  "_status",
                  # owner-유예 (C-6 2026-07-08): scope 확장 제외 + 명시 선택 허용
                  # (console2_server._resolve_lifecycle_ids가 소비; 은퇴 아님).
                  "_scope_exclude",
                  # loader가 로드 시점에 주입하는 파생 필드 (파일에는 없음) —
                  # HEAVY-PREMISE-CONTRACT §1 role(verify/probe).
                  "role"}
# Machine-readable disposition for enabled:false lifecycles (IB-030). Derived
# from docs/working/trackers/LIVE-READINESS-GATES.md. Advisory only — a missing/invalid _status
# is a WARNING, never an error (must not break the offline gate for others).
STATUS_ENUM = {"done-modeled", "timing-gated", "blocked-engine",
               "blocked-owner", "stale"}
STEP_KEYS = {"name", "method", "path", "service", "json", "params", "headers",
             "expect_status", "capture", "capture_soft", "cleanup", "poll",
             "wait", "retries", "retry_interval", "retry_on_status",
             "retry_on_error_code",
             "group", "optional", "destructive", "adopt", "probe_reads",
             "action", "input", "output", "values", "json_b64_fields", "skip",
             "_note", "_comment"}
METHODS = {"GET", "POST", "PUT", "PATCH", "DELETE"}
BUILTINS = {"unique", "ualpha", "region", "zone",
            "vs_server_type_prefix", "vs_server_type", "db_server_type_filter",
            "db_server_type_name_prefix",
            "today", "today_plus_5y", "this_month", "zone_alt",
            "scp_access_key", "scp_secret_key",
            "iso_today", "iso_29d_ago", "epoch_now", "epoch_1h_ago",
            "iso_dt_29d_ago", "iso_dt_1h_ago",
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
    status_counts = {}  # _status value -> count (IB-030 readiness summary)
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

        # IB-030: every enabled:false lifecycle SHOULD carry a machine-readable
        # _status from STATUS_ENUM (advisory — warn only, never error).
        if lc.get("enabled") is False:
            st = lc.get("_status")
            if st is None:
                warnings.append(f"{where}: disabled lifecycle is missing '_status' "
                                f"(expected one of {sorted(STATUS_ENUM)}) — IB-030")
            elif st not in STATUS_ENUM:
                warnings.append(f"{where}: disabled lifecycle has invalid _status "
                                f"'{st}' (expected one of {sorted(STATUS_ENUM)}) — IB-030")
            else:
                status_counts[st] = status_counts.get(st, 0) + 1

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
            # HTTP 스텝인데 method/path가 없으면 엔진이 step['path'] KeyError로
            # 크래시한다 (2026-07-16 run 7a26 실측 — method 없는 단독 wait 스텝).
            # 특수 스텝(probe_reads/action)만 예외.
            if ("probe_reads" not in step and "action" not in step
                    and (not step.get("method") or not step.get("path"))):
                errors.append(f"{sw}: HTTP step needs method+path (엔진 크래시 클래스"
                              " — wait만 필요하면 다음 HTTP 스텝에 wait를 접을 것)")
            if step.get("probe_reads"):
                # probe_reads is a read-only map of {key: templated_path}; no method
                used = _placeholders_in(step["probe_reads"])
                missing = used - available
                if missing:
                    warnings.append(f"{sw}: probe_reads uses undefined {sorted(missing)}")
                continue

            if step.get("action"):
                # Pure ctx-transform step (e.g. b64_encode): consumes {input} and
                # publishes its 'output' as a placeholder for later steps. No HTTP,
                # so it's checked here (input capture-before-use) and skipped from
                # the path/method checks below.
                # `set_const` (2026-07-16) has no {input} — it seeds ctx directly
                # from its literal 'values' dict (fixed enum path tokens with no
                # live discovery source), so each key becomes available right away.
                if step["action"] == "set_const":
                    for _k in (step.get("values") or {}):
                        available.add(_k)
                    continue
                used = _placeholders_in(step.get("input"))
                missing = used - available
                if missing:
                    errors.append(f"{sw}: action input references undefined "
                                  f"placeholders {sorted(missing)} (capture them earlier?)")
                if step.get("output"):
                    available.add(step["output"])
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
                # Region-routing service aliases (e.g. 'filestorage-dr' -> kr-east1
                # host) map to the base service's catalog endpoint — mirror the
                # engine's _canon_service so DR-side write steps aren't false-flagged.
                base_svc = step_svc[:-3] if step_svc.endswith("-dr") else step_svc
                if method != "GET" and not step.get("adopt"):
                    key = ((method, _norm_path(step["path"]), step_svc))
                    if key not in catalog and (method, _norm_path(step["path"]), base_svc) not in catalog:
                        warnings.append(
                            f"{sw}: {method} {step['path']} does not resolve to a "
                            f"catalog endpoint for service '{step_svc}' — won't count "
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

        # Success-path teardown guard (2026-07-13): the engine fires a step's
        # `cleanup` ONLY on the FAILURE path (engine._teardown runs inside the
        # `except`); a *successful* run tears down via explicit DELETE steps.
        # So a create carrying a `cleanup` but with NO matching explicit DELETE
        # step LEAKS its resource on every green run (live-observed: the FIFO
        # queue in application-queueservice-queue + the NFS volume in
        # gen-heavy-vs-netops, both stranded in the console). Warn so the author
        # adds an explicit delete step (query string ignored when matching).
        def _norm_del(p):
            return (p or "").split("?")[0]
        _explicit_deletes = {_norm_del(s.get("path")) for s in steps
                             if s.get("method", "").upper() == "DELETE" and s.get("path")}
        for s in steps:
            cu = s.get("cleanup")
            if (s.get("method", "").upper() == "POST" and isinstance(cu, dict)
                    and cu.get("method", "").upper() == "DELETE"
                    and _norm_del(cu.get("path")) not in _explicit_deletes):
                warnings.append(
                    f"{where} step '{s.get('name')}': create has a cleanup DELETE "
                    f"{cu.get('path')} but NO matching explicit DELETE step — the "
                    f"resource LEAKS on a successful run (cleanup fires only on "
                    f"failure). Add an explicit delete step.")

    for w in warnings:
        print(f"WARN  {w}")
    for e in errors:
        print(f"ERROR {e}")

    # IB-030: machine-readable readiness summary of disabled lifecycles by _status.
    n_disabled_tagged = sum(status_counts.values())
    if status_counts:
        print(f"\nDisabled-lifecycle _status summary ({n_disabled_tagged} tagged):")
        for st in sorted(STATUS_ENUM):
            print(f"  {st:<14} {status_counts.get(st, 0)}")
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
