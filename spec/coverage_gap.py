"""Static coverage-gap analysis (no live calls).

Mirrors the dashboard's coverage model so we can see, *before* spending CI hours,
exactly which endpoints the current scenario surface can reach and which are still
uncovered — grouped by service so each service agent gets a concrete target list.

Coverage model (must match dashboard/build.py):
  * GET, no path params      -> reachable by the read-only smoke floor (always).
  * GET, with path params    -> reachable iff an ENABLED lifecycle has a GET step
                                whose normalised path matches (a read-chain / probe).
  * non-GET (write)          -> reachable iff an ENABLED lifecycle has a step with
                                the same (method, normalised path).

This is the *static ceiling* of the current scenarios assuming a perfect run; the
live dashboard number can only be <= this. Closing the gap = authoring scenarios.

Usage:
  python -m spec.coverage_gap                      # summary + per-service gap table
  python -m spec.coverage_gap --service vpc        # drill into one service
  python -m spec.coverage_gap --category database  # drill into one category
  python -m spec.coverage_gap --json gap.json      # machine-readable dump
  python -m spec.coverage_gap --include-heavy      # count heavy lifecycles as enabled
"""
from __future__ import annotations

import argparse
import json
import os
from collections import defaultdict

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CATALOG = os.path.join(ROOT, "data", "api_catalog.json")
SCENARIOS = os.path.join(ROOT, "regression", "scenarios", "scenarios.json")


def norm_path(p: str) -> str:
    """Drop query; collapse templated/concrete id segments to '*'. (== dashboard)"""
    p = p.split("?")[0].strip("/")
    return "/".join("*" if "{" in s else s for s in p.split("/"))


def load_catalog(path: str = CATALOG):
    cat = json.load(open(path))
    for e in cat:
        e["_norm"] = norm_path(e["http_path"])
    return cat


def lifecycle_steps(path: str = SCENARIOS, include_heavy: bool = True):
    """Return ((method, norm_path) -> set(lifecycle_id)) for ENABLED lifecycles.

    include_heavy=True treats SCP_RUN_HEAVY lifecycles as enabled (they ARE part of
    the path to 100%); pass False to see the light-only ceiling.

    Merges base scenarios.json + per-service fragments via the shared loader.
    """
    try:
        from regression.scenarios.loader import load_lifecycles
        lifecycles = load_lifecycles()
    except Exception:
        lifecycles = json.load(open(path))["lifecycles"]
    hit: dict[tuple[str, str], set[str]] = defaultdict(set)
    for lc in lifecycles:
        if not lc.get("enabled"):
            continue
        if lc.get("heavy") and not include_heavy:
            continue
        for s in lc.get("steps", []):
            if not s.get("method") or not s.get("path"):
                continue
            hit[(s["method"].upper(), norm_path(s["path"]))].add(lc["id"])
    return hit


def classify(cat, hit):
    """Annotate each endpoint with reachability under the current scenarios."""
    for e in cat:
        m = e["method"]
        np = e["_norm"]
        if m == "GET" and "{" not in e["http_path"]:
            e["_reach"] = "smoke"            # floor — always reachable
            e["_by"] = ["<smoke>"]
        elif (m, np) in hit:
            e["_reach"] = "scenario"
            e["_by"] = sorted(hit[(m, np)])
        else:
            e["_reach"] = "GAP-get-id" if m == "GET" else "GAP-write"
            e["_by"] = []
    return cat


def summarize(cat):
    covered = [e for e in cat if e["_reach"] in ("smoke", "scenario")]
    gaps = [e for e in cat if e["_reach"].startswith("GAP")]
    return covered, gaps


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--service")
    ap.add_argument("--category")
    ap.add_argument("--json")
    ap.add_argument("--no-heavy", action="store_true",
                    help="count only light lifecycles as enabled")
    args = ap.parse_args()

    cat = load_catalog()
    hit = lifecycle_steps(include_heavy=not args.no_heavy)
    classify(cat, hit)

    if args.service:
        cat = [e for e in cat if e["service"] == args.service]
    if args.category:
        cat = [e for e in cat if e["category"] == args.category]

    covered, gaps = summarize(cat)
    total = len(cat)
    print(f"Static coverage ceiling (heavy={'on' if not args.no_heavy else 'off'}):")
    print(f"  endpoints           : {total}")
    print(f"  reachable now       : {len(covered)} ({len(covered)/total*100:.1f}%)")
    print(f"    - smoke GET floor  : {sum(1 for e in covered if e['_reach']=='smoke')}")
    print(f"    - via scenarios    : {sum(1 for e in covered if e['_reach']=='scenario')}")
    print(f"  GAP (need scenarios): {len(gaps)}")
    print(f"    - id-bound GETs    : {sum(1 for e in gaps if e['_reach']=='GAP-get-id')}")
    print(f"    - write ops        : {sum(1 for e in gaps if e['_reach']=='GAP-write')}")

    # per-service gap table, biggest gap first
    by_svc = defaultdict(lambda: {"total": 0, "gap": 0, "getid": 0, "write": 0})
    for e in cat:
        s = by_svc[(e["category"], e["service"])]
        s["total"] += 1
        if e["_reach"] == "GAP-get-id":
            s["gap"] += 1; s["getid"] += 1
        elif e["_reach"] == "GAP-write":
            s["gap"] += 1; s["write"] += 1
    rows = sorted(by_svc.items(), key=lambda kv: -kv[1]["gap"])

    print(f"\n{'category/service':45} {'gap':>5} {'getid':>6} {'write':>6} {'total':>6}")
    for (c, s), v in rows:
        if v["gap"] == 0:
            continue
        print(f"{c+'/'+s:45} {v['gap']:>5} {v['getid']:>6} {v['write']:>6} {v['total']:>6}")

    if args.service or args.category:
        print("\nUncovered endpoints:")
        for e in sorted(gaps, key=lambda x: (x["method"], x["http_path"])):
            print(f"  {e['method']:6} {e['http_path']:60} [{e['_reach']}]")

    if args.json:
        out = {
            "total": total, "covered": len(covered), "gap": len(gaps),
            "gaps": [{"key": e["key"], "method": e["method"], "path": e["http_path"],
                      "category": e["category"], "service": e["service"],
                      "reach": e["_reach"]} for e in gaps],
            "by_service": [{"category": c, "service": s, **v}
                           for (c, s), v in rows],
        }
        json.dump(out, open(args.json, "w"), indent=2)
        print(f"\nwrote {args.json}")


if __name__ == "__main__":
    main()
