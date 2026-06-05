#!/usr/bin/env python3
"""Compare current conformance vs a baseline so only NEW design/behavior defects
alarm (mirror of data/baselines/known_issues.json for the coverage axis).

baseline format: {"<endpoint key>": "green|yellow|red", ...}

Usage:
  python tools/conformance_baseline.py --baseline data/baselines/conformance_baseline.json
      [--update]            # rewrite the baseline to the current state
      [--init-if-missing]   # if baseline absent, seed it from current (no NEW)
Outputs: reports/conformance_new.json + console summary.
Exit code 0 always (reporting tool; CI decides what to do with the count).
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CONF = ROOT / "data" / "conformance.json"
OUT = ROOT / "reports" / "conformance_new.json"
RANK = {"green": 0, "yellow": 1, "red": 2}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--baseline", default="data/baselines/conformance_baseline.json")
    ap.add_argument("--update", action="store_true")
    ap.add_argument("--init-if-missing", action="store_true")
    args = ap.parse_args()

    conf = json.loads(CONF.read_text())
    cur = {k: v["status"] for k, v in conf["by_endpoint"].items()}
    bpath = Path(args.baseline)
    baseline = None
    if bpath.exists() and bpath.read_text().strip():
        try:
            baseline = json.loads(bpath.read_text())
        except (ValueError, json.JSONDecodeError):
            baseline = None   # empty/corrupt -> treat as absent (seed fresh)

    if baseline is None:
        if args.init_if_missing or args.update:
            bpath.parent.mkdir(parents=True, exist_ok=True)
            bpath.write_text(json.dumps(cur, indent=2, ensure_ascii=False, sort_keys=True))
            print(f"baseline seeded ({len(cur)} endpoints) -> {bpath}; no NEW on first run")
        else:
            print("no baseline; run with --init-if-missing to seed")
        OUT.parent.mkdir(parents=True, exist_ok=True)
        OUT.write_text(json.dumps({"new": [], "regressed": [], "fixed": []}, indent=2))
        return

    new, regressed, fixed = [], [], []
    for k, st in cur.items():
        base = baseline.get(k, "green")
        if RANK[st] > RANK[base]:
            (new if base == "green" else regressed).append(
                {"endpoint": k, "from": base, "to": st,
                 "items": [i["type"] for i in conf["by_endpoint"][k]["items"]]})
        elif RANK[st] < RANK[base]:
            fixed.append({"endpoint": k, "from": base, "to": st})

    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps({"new": new, "regressed": regressed, "fixed": fixed},
                              indent=2, ensure_ascii=False))
    print(f"## conformance vs baseline\n- NEW defects (green→y/r): {len(new)}"
          f"\n- regressed (y→r): {len(regressed)}\n- fixed: {len(fixed)}")
    for r in (new + regressed)[:30]:
        print(f"  {r['from']}→{r['to']}  {r['endpoint']}  ({', '.join(r['items'][:4])})")

    if args.update:
        bpath.write_text(json.dumps(cur, indent=2, ensure_ascii=False, sort_keys=True))
        print(f"baseline updated -> {bpath}")


if __name__ == "__main__":
    main()
