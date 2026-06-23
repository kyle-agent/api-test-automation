#!/usr/bin/env python3
"""List endpoints that were CALLED but FAILED (status >= N) — the coverage
agent's "what to fix next" view over the results store.

Reads ``reports/results/observations*.jsonl`` (``core.results``), groups the
non-2xx observations by method+path, and prints the status + the recorded note
(the API's own error hint) + which lifecycle exercised it. The note is exactly
the signal a coverage agent needs — e.g. a queue step recorded
``"queue name must end with '.fifo'"`` tells you what to change to make it 2xx.

    python -m spec.failures                  # all failing endpoints
    python -m spec.failures --service queue  # filter by service/lifecycle/path
    python -m spec.failures --min-status 500 # only 5xx (likely product bugs)
"""
from __future__ import annotations

import argparse
import sys
from collections import defaultdict

from core import results


def _lifecycle_of(endpoint_key: str) -> str:
    """endpoint_key is ``<lifecycle-id>:<step>``; the lifecycle id is the prefix."""
    return (endpoint_key or "").split(":", 1)[0]


def collect(observations: list[dict] | None = None, *, service: str | None = None,
            min_status: int = 400) -> dict:
    """Group failing (status >= min_status) observations by (method, path)."""
    obs = observations if observations is not None else results.load_observations()
    groups: dict = defaultdict(lambda: {"count": 0, "statuses": set(),
                                        "notes": set(), "lifecycles": set()})
    for o in obs:
        st = o.get("status")
        if not isinstance(st, int) or st < min_status:
            continue
        hay = f"{o.get('endpoint_key', '')} {o.get('path', '')} {o.get('method', '')}"
        if service and service.lower() not in hay.lower():
            continue
        g = groups[(o.get("method", ""), o.get("path", ""))]
        g["count"] += 1
        g["statuses"].add(st)
        if o.get("note"):
            g["notes"].add(str(o["note"])[:200])
        lc = _lifecycle_of(o.get("endpoint_key", ""))
        if lc:
            g["lifecycles"].add(lc)
    return groups


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(
        description="failing endpoints (status>=N) from the results store")
    ap.add_argument("--service", help="filter by service / lifecycle / path substring")
    ap.add_argument("--min-status", type=int, default=400,
                    help="minimum status to report (default 400)")
    a = ap.parse_args(argv)
    groups = collect(service=a.service, min_status=a.min_status)
    if not groups:
        scope = f" matching {a.service!r}" if a.service else ""
        print(f"no failing endpoints (status>={a.min_status}){scope} in the results store.")
        return 0
    rows = sorted(groups.items(), key=lambda kv: kv[1]["count"], reverse=True)
    print(f"{'#':>4}  {'status':<10} {'method':<6} path   (lifecycle)")
    for (method, path), g in rows:
        sts = ",".join(str(s) for s in sorted(g["statuses"]))
        lcs = ",".join(sorted(g["lifecycles"]))
        print(f"{g['count']:>4}  {sts:<10} {method:<6} {path}   ({lcs})")
        for note in sorted(g["notes"])[:2]:
            print(f"        ↳ {note}")
    print(f"\n{len(rows)} failing endpoint(s). Fix the body/params/sequencing per the "
          f"note, then re-run to turn them 2xx (raise coverage).")
    return 0


if __name__ == "__main__":
    sys.exit(main())
