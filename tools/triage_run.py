"""Run triage — pull a run's mirrored event log from the oplog bucket and surface
every 4xx/5xx step (status · error code · request body) plus a pass/fail lifecycle
summary, in one command. This is the loop a coverage session runs every round;
before this it was a hand-typed boto3 + json filter each time.

The oplog bucket (``core.oplog``) mirrors every step of a console/CI run to
``runs/<run-id>/artifact/events.jsonl`` (step-end carries status/req_body/
resp_snippet — see the artifact schema). A headless session can't fetch GitHub
artifacts, so this bucket is the source of truth.

Usage::

    python -m tools.triage_run                      # latest run: ACTIONABLE 4xx + summary
    python -m tools.triage_run 20260713-145320-c373 # a specific run-id
    python -m tools.triage_run --list               # list recent runs (newest first)
    python -m tools.triage_run --show-waived         # also show waived-key 4xx (hidden by
                                                     #   default — they are accepted C2-only
                                                     #   non-goals, e.g. baremetal/archivestorage)
    python -m tools.triage_run --keys KEYFILE        # also report status for catalog
                                                     #   keys listed in KEYFILE (or a
                                                     #   comma list via --keys=a,b)
    python -m tools.triage_run --diff 20260713-145320-c373   # before/after vs a baseline
                                                     #   run: which endpoints flipped
                                                     #   4xx->2xx (improved) or 2xx->4xx
                                                     #   (regressed)

By default a 4xx step whose catalog key is in data/baselines/coverage_waivers.json
is SUPPRESSED from the actionable list (shown only as a count) — otherwise the ~60
waived billing-prohibitive/reachability keys (baremetal-blockstorage, archivestorage,
…) drown the handful of real, fixable defects. Read-only over the bucket; never
calls the live API or touches safety gates.
"""
from __future__ import annotations

import argparse
import io
import json
import re
import sys
from collections import defaultdict
from pathlib import Path

try:
    import boto3  # noqa: F401
except ImportError:  # pragma: no cover
    boto3 = None

from core.oplog import _cfg

try:
    from regression.scenarios.engine import _catalog_key_for
except Exception:  # pragma: no cover — keep triage usable even if engine import fails
    _catalog_key_for = lambda *a, **k: None  # noqa: E731


def _waived_keys() -> set:
    p = Path(__file__).resolve().parents[1] / "data" / "baselines" / "coverage_waivers.json"
    try:
        return {w["key"] for w in json.load(open(p)).get("waivers", [])}
    except Exception:
        return set()


def _step_catalog_key(e: dict):
    return _catalog_key_for(e.get("method"), e.get("path") or "", e.get("service"))


def _client():
    if boto3 is None:
        sys.exit("boto3 not installed — pip install boto3")
    cfg = _cfg()
    if not cfg:
        sys.exit("oplog disabled: no SCP_ACCESS_KEY/SCP_SECRET_KEY/bucket in env")
    import boto3 as _b
    return _b.client("s3", endpoint_url=cfg["endpoint"], region_name=cfg["region"],
                     aws_access_key_id=cfg["access"],
                     aws_secret_access_key=cfg["secret"]), cfg["bucket"]


def _runs_newest_first(s3, bucket):
    """List run-ids that have an events.jsonl artifact, newest first (by mtime)."""
    paginator = s3.get_paginator("list_objects_v2")
    out = []
    for page in paginator.paginate(Bucket=bucket, Prefix="runs/"):
        for o in page.get("Contents", []):
            if o["Key"].endswith("/artifact/events.jsonl"):
                run_id = o["Key"][len("runs/"):-len("/artifact/events.jsonl")]
                out.append((o["LastModified"], o["Size"], run_id))
    out.sort(reverse=True)
    return out


def _load_events(s3, bucket, run_id):
    key = f"runs/{run_id}/artifact/events.jsonl"
    buf = io.BytesIO()
    s3.download_fileobj(bucket, key, buf)
    return [json.loads(l) for l in buf.getvalue().decode().splitlines() if l.strip()]


def _err_code(snippet: str) -> str:
    """Pull the SCP error code out of a resp_snippet, else a short prefix."""
    if not snippet:
        return ""
    m = re.search(r'"code"\s*:\s*"([^"]+)"', snippet)
    if m:
        return m.group(1)
    m = re.search(r'"(?:message|detail|title)"\s*:\s*"([^"]{0,60})', snippet)
    return m.group(1) if m else snippet[:60]


def _step_ends(evts):
    return [e for e in evts if e.get("kind") == "step-end"]


def _failures(evts):
    return [e for e in _step_ends(evts)
            if isinstance(e.get("status"), int) and e["status"] >= 400]


def triage(evts, show_waived=False) -> None:
    le = [e for e in evts if e.get("kind") == "lifecycle-end"]
    passed = [e for e in le if e.get("status") == "passed"]
    failed = [e for e in le if e.get("status") != "passed"]
    fails = _failures(evts)
    waived = _waived_keys()
    waived_fails = [e for e in fails if _step_catalog_key(e) in waived]
    actionable = fails if show_waived else [e for e in fails if e not in waived_fails]
    print(f"lifecycles: {len(le)}  ({len(passed)} passed / {len(failed)} not-passed)")
    print(f"step 4xx/5xx: {len(fails)} total · {len(waived_fails)} waived (hidden) · "
          f"{len(actionable)} actionable across {len({e['lifecycle'] for e in actionable})} lifecycles\n")

    if failed:
        print("== NOT-PASSED lifecycles ==")
        for e in sorted(failed, key=lambda x: x["lifecycle"]):
            print(f"  {e.get('status'):8} {e['lifecycle']}  failed_groups={e.get('failed_groups')}"
                  f"  {(e.get('reason') or '')[:60]}")
        print()

    by_lc = defaultdict(list)
    for e in actionable:
        by_lc[e["lifecycle"]].append(e)
    label = "ALL" if show_waived else "ACTIONABLE (waived keys hidden — see --show-waived)"
    print(f"== {label} 4xx/5xx steps by lifecycle ==")
    for lc in sorted(by_lc):
        print(f"  [{lc}]")
        for e in by_lc[lc]:
            code = _err_code(e.get("resp_snippet") or "")
            opt = "" if e.get("optional") else "  !REQUIRED"
            wv = "  [WAIVED]" if _step_catalog_key(e) in waived else ""
            print(f"    {e['status']} {e['method']:6} {e.get('path'):50} {e['step']:28} {code}{opt}{wv}")


def report_keys(evts, keys) -> None:
    """For each requested catalog key or (svc,path,method) probe, show observed statuses."""
    se = _step_ends(evts)
    print("\n== requested keys ==")
    for k in keys:
        hits = [e for e in se if k in (e.get("path") or "") or k in (e.get("step") or "")
                or k in (e.get("lifecycle") or "")]
        if not hits:
            print(f"  {k}: (not seen)")
            continue
        best = min(h["status"] for h in hits)
        tag = "2xx" if best < 300 else ("4xx" if best < 500 else "5xx")
        print(f"  {k}: best={best} [{tag}]")
        for h in hits:
            print(f"      {h['status']} {h.get('service')} [{h['lifecycle']}:{h['step']}]")


def _endpoint_status(evts):
    """(method, norm_path, service) -> best (lowest) status seen this run."""
    def norm(p):
        p = re.sub(r"\{[^}]+\}", "/*", p or "")
        return re.sub(r"/[0-9a-fA-F][0-9a-fA-F-]{7,}", "/*", p)
    best = {}
    for e in _step_ends(evts):
        if not isinstance(e.get("status"), int):
            continue
        # canonicalize the DR region alias the way the engine credits coverage
        svc = e.get("service") or ""
        svc = svc[:-3] if svc.endswith("-dr") else svc
        k = (e.get("method"), norm(e.get("path")), svc)
        best[k] = min(best.get(k, 999), e["status"])
    return best


def diff(evts_new, evts_base) -> None:
    new, base = _endpoint_status(evts_new), _endpoint_status(evts_base)
    improved, regressed = [], []
    for k in sorted(set(new) & set(base)):
        b, n = base[k], new[k]
        if b >= 400 and n < 300:
            improved.append((k, b, n))
        elif b < 300 and n >= 400:
            regressed.append((k, b, n))
    print(f"\n== DIFF vs baseline ==  improved={len(improved)}  regressed={len(regressed)}")
    if improved:
        print("  IMPROVED (4xx->2xx):")
        for (m, p, s), b, n in improved:
            print(f"    {b}->{n}  {m:6} {p:48} {s}")
    if regressed:
        print("  REGRESSED (2xx->4xx):")
        for (m, p, s), b, n in regressed:
            print(f"    {b}->{n}  {m:6} {p:48} {s}")
    only_new = sorted(set(new) - set(base))
    if only_new:
        print(f"  (+{len(only_new)} endpoint(s) exercised only in the new run)")


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Triage a run's oplog event log.")
    ap.add_argument("run_id", nargs="?", help="run-id (default: newest)")
    ap.add_argument("--list", action="store_true", help="list recent runs and exit")
    ap.add_argument("--show-waived", action="store_true",
                    help="include waived-key 4xx (hidden by default)")
    ap.add_argument("--keys", help="comma list, or a file of catalog keys / path substrings")
    ap.add_argument("--diff", metavar="BASELINE_RUN_ID",
                    help="compare against a baseline run: which endpoints flipped")
    args = ap.parse_args(argv)

    s3, bucket = _client()
    runs = _runs_newest_first(s3, bucket)
    if args.list:
        for lm, sz, rid in runs[:20]:
            print(f"{lm.isoformat()}  {sz:>10}  {rid}")
        return 0
    if not runs:
        sys.exit("no runs with an events.jsonl artifact found in the bucket")

    run_id = args.run_id or runs[0][2]
    print(f"# triage {run_id}\n")
    evts = _load_events(s3, bucket, run_id)
    triage(evts, show_waived=args.show_waived)

    if args.keys:
        import os
        if os.path.exists(args.keys):
            keys = [l.strip() for l in open(args.keys) if l.strip()]
        else:
            keys = [k.strip() for k in args.keys.split(",") if k.strip()]
        report_keys(evts, keys)

    if args.diff:
        base_evts = _load_events(s3, bucket, args.diff)
        diff(evts, base_evts)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
