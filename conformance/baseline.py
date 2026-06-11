"""AXIS 2 — conformance baseline so only NEW design/behavior defects alarm.

Ports ``tools/conformance_baseline.py``: compares the current per-endpoint
conformance colours (``data/conformance.json``, produced by
:mod:`conformance.static`) against a stored baseline and reports NEW / regressed /
fixed endpoints. This is the conformance-axis mirror of the regression
``known_issues`` baseline.

baseline format: ``{"<endpoint key>": "green|yellow|red", ...}``

Outputs ``reports/conformance_new.json`` (legacy dual-write). Reporting only —
:func:`main` never raises on diff; CI decides what to do with the counts.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CONF = ROOT / "data" / "conformance.json"
OUT = ROOT / "reports" / "conformance_new.json"
DEFAULT_BASELINE = "data/baselines/conformance_baseline.json"
RANK = {"green": 0, "yellow": 1, "red": 2}


def current_status() -> dict:
    """Per-endpoint status map from the current conformance.json."""
    conf = json.loads(CONF.read_text())
    return {k: v["status"] for k, v in conf["by_endpoint"].items()}


def load_baseline(path: str | Path) -> dict | None:
    """Load a baseline map, or None if absent/empty/corrupt (seed-fresh signal)."""
    bpath = Path(path)
    if bpath.exists() and bpath.read_text().strip():
        try:
            return json.loads(bpath.read_text())
        except (ValueError, json.JSONDecodeError):
            return None
    return None


def diff(current: dict, baseline: dict, conf: dict) -> dict:
    """Compute new / regressed / fixed against a baseline."""
    new, regressed, fixed = [], [], []
    for k, st in current.items():
        base = baseline.get(k, "green")
        if RANK[st] > RANK[base]:
            (new if base == "green" else regressed).append(
                {"endpoint": k, "from": base, "to": st,
                 "items": [i["type"] for i in conf["by_endpoint"][k]["items"]]})
        elif RANK[st] < RANK[base]:
            fixed.append({"endpoint": k, "from": base, "to": st})
    return {"new": new, "regressed": regressed, "fixed": fixed}


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--baseline", default=DEFAULT_BASELINE)
    ap.add_argument("--update", action="store_true",
                    help="rewrite the baseline to the current state")
    ap.add_argument("--init-if-missing", action="store_true",
                    help="if baseline absent, seed it from current (no NEW)")
    args = ap.parse_args()

    conf = json.loads(CONF.read_text())
    cur = {k: v["status"] for k, v in conf["by_endpoint"].items()}
    # per-environment baseline: profile-suffixed sibling wins (core/baselines.py)
    from core import baselines as _baselines
    bpath = Path(_baselines.resolve(args.baseline))
    baseline = load_baseline(bpath)

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

    result = diff(cur, baseline, conf)
    new, regressed, fixed = result["new"], result["regressed"], result["fixed"]

    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(result, indent=2, ensure_ascii=False))
    print(f"## conformance vs baseline\n- NEW defects (green->y/r): {len(new)}"
          f"\n- regressed (y->r): {len(regressed)}\n- fixed: {len(fixed)}")
    for r in (new + regressed)[:30]:
        print(f"  {r['from']}->{r['to']}  {r['endpoint']}  ({', '.join(r['items'][:4])})")

    if args.update:
        bpath.write_text(json.dumps(cur, indent=2, ensure_ascii=False, sort_keys=True))
        print(f"baseline updated -> {bpath}")


if __name__ == "__main__":
    main()
