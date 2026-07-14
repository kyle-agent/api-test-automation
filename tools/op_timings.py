"""Per-operation timing from a run's oplog events.

Every mutating API call (POST/PUT/DELETE) in a run has two costs:
  * api_ms   — the call's own round-trip latency (step-end elapsed_ms)
  * settle_s — the ASYNC completion wait: many SCP writes return 202 and the
               resource then transitions state; the scenario polls a
               `wait-after-<op>` / settle GET until it re-settles. That poll's
               wall span IS the operation's real completion time.
  * total_s  — api_ms/1000 + settle_s

This module derives one record per op from events.jsonl WITHOUT any engine
change (works on every existing run), associating an op with its settle wait by
adjacency: the first `wait`/`settle` GET that follows the op in the same
lifecycle before the next mutation. Synchronous ops (no following wait) get
settle_s = None. It prints a per-run service×op table and can accumulate a
cross-run p50/p90/max store (data/optimizer/op_timings.json) for the dashboard.

CLI::
    python -m tools.op_timings                 # latest run: service×op timing table
    python -m tools.op_timings <run-id>
    python -m tools.op_timings --list
    python -m tools.op_timings <run-id> --json  # raw per-op records
    python -m tools.op_timings <run-id> --accumulate --out data/optimizer/op_timings.json
    python -m tools.op_timings --html dashboard/op_timings.html --store data/optimizer/op_timings.json

Read-only over the bucket; never calls the live API or touches safety gates.
"""
from __future__ import annotations

import argparse
import json
import statistics
import sys
from collections import defaultdict
from pathlib import Path

from tools.triage_run import _client, _load_events, _runs_newest_first, _step_catalog_key

_MUT = {"POST", "PUT", "DELETE", "PATCH"}
# op-name prefixes that are NOT operations (reads/settles/captures) even if some
# are POST-as-query — they never own a settle wait of interest here.
_NOT_OP_PREFIX = ("wait", "settle", "probe", "get", "list", "show", "verify",
                  "find", "capture", "seed")


def _classify(name: str, method: str, path: str) -> str:
    n = (name or "").lower()
    for kw, kind in (
        ("gone", "delete"), ("delete", "delete"), ("remove", "delete"),
        ("resize", "resize"),
        ("upgrade", "upgrade"), ("kernel", "upgrade"), ("patch", "upgrade"),
        ("restart", "restart"), ("start", "start"), ("stop", "stop"),
        ("backup", "backup"), ("sync", "sync"), ("purge", "purge"),
        ("create", "create"), ("add", "create"), ("register", "create"),
        ("set", "update"), ("update", "update"), ("modify", "update"),
        ("approve", "update"), ("connect", "update"),
    ):
        if kw in n:
            return kind
    if method == "DELETE":
        return "delete"
    if method == "POST":
        return "create"
    if method in ("PUT", "PATCH"):
        return "update"
    return "other"


def _lifecycle_steps(evts):
    """Per lifecycle, an ordered list of completed steps with start/end ts.
    Pairs each step-start with the next matching step-end (FIFO by name)."""
    starts = defaultdict(list)  # (lc, step) -> [start_ts,...]
    order = defaultdict(list)   # lc -> [record,...] in step-end order
    for e in evts:
        k = e.get("kind")
        if k == "step-start":
            starts[(e["lifecycle"], e.get("step"))].append(e["ts"])
        elif k == "step-end":
            q = starts.get((e["lifecycle"], e.get("step")))
            t0 = q.pop(0) if q else e["ts"]
            order[e["lifecycle"]].append({
                "name": e.get("step"), "method": (e.get("method") or "").upper(),
                "path": e.get("path"), "service": e.get("service"),
                "status": e.get("status"), "elapsed_ms": e.get("elapsed_ms"),
                "ts_start": t0, "ts_end": e["ts"],
            })
    return order


def derive(evts) -> list[dict]:
    """One record per mutating op with api_ms / settle_s / total_s."""
    out = []
    for lc, steps in _lifecycle_steps(evts).items():
        for i, s in enumerate(steps):
            name = (s["name"] or "")
            if s["method"] not in _MUT:
                continue
            if name.lower().startswith(_NOT_OP_PREFIX):
                continue
            # settle = first GET 'wait'/'settle' step after this op, before the
            # next mutation (the op's async completion poll).
            settle_s = None
            for j in range(i + 1, len(steps)):
                nx = steps[j]
                nn = (nx["name"] or "").lower()
                if nx["method"] in _MUT and not nn.startswith(("wait", "settle")):
                    break  # next op reached; no settle for this one
                if nx["method"] == "GET" and (nn.startswith("wait") or "settle" in nn):
                    settle_s = round(nx["ts_end"] - nx["ts_start"], 1)
                    break
            api_ms = s["elapsed_ms"] if isinstance(s["elapsed_ms"], (int, float)) else None
            total_s = round((api_ms or 0) / 1000.0 + (settle_s or 0), 1)
            out.append({
                "lifecycle": lc, "step": name, "service": s["service"],
                "catalog_key": _step_catalog_key(s), "method": s["method"],
                "kind": _classify(name, s["method"], s["path"]),
                "status": s["status"], "api_ms": api_ms, "settle_s": settle_s,
                "total_s": total_s,
            })
    return out


def _mmss(s) -> str:
    if s is None:
        return "-"
    s = int(s)
    return f"{s // 60}:{s % 60:02d}" if s >= 60 else f"{s}s"


def print_table(records) -> None:
    """service × op matrix of total_s (this run), settle-dominant ops first."""
    by_svc = defaultdict(list)
    for r in records:
        by_svc[(r["service"] or "?")].append(r)
    print(f"# op timings — {len(records)} ops across {len(by_svc)} services "
          f"(total_s = api + async-settle)\n")
    rows = sorted(records, key=lambda r: -(r["total_s"] or 0))
    shown = [r for r in rows if (r["total_s"] or 0) >= 1]
    print(f"{'total':>7} {'api':>7} {'settle':>7}  kind        service · op")
    for r in shown[:40]:
        api = f"{r['api_ms']:.0f}ms" if r["api_ms"] is not None else "-"
        st = "2xx" if isinstance(r["status"], int) and r["status"] < 300 else str(r["status"])
        print(f"{_mmss(r['total_s']):>7} {api:>7} {_mmss(r['settle_s']):>7}  "
              f"{r['kind']:<10}  {r['service']} · {r['step']}  [{st}]")


def accumulate(records, store_path: Path, run_id: str) -> dict:
    """Fold this run's op timings into a cross-run p50/p90/max store keyed by
    catalog_key (dashboard source). Union-merge; keeps a rolling sample."""
    store = {}
    if store_path.exists():
        try:
            store = json.loads(store_path.read_text())
        except (ValueError, OSError):
            store = {}
    for r in records:
        key = r["catalog_key"] or f"{r['service']}:{r['step']}"
        if r["total_s"] is None:
            continue
        rec = store.setdefault(key, {"kind": r["kind"], "service": r["service"],
                                     "samples": [], "n": 0})
        rec["samples"] = (rec.get("samples", []) + [r["total_s"]])[-50:]  # rolling 50
        rec["n"] = rec.get("n", 0) + 1
        rec["last_s"] = r["total_s"]
        rec["last_run"] = run_id
        s = sorted(rec["samples"])
        rec["p50"] = round(statistics.median(s), 1)
        rec["p90"] = round(s[min(len(s) - 1, int(len(s) * 0.9))], 1)
        rec["max"] = max(s)
    store_path.parent.mkdir(parents=True, exist_ok=True)
    store_path.write_text(json.dumps(store, indent=2, ensure_ascii=False) + "\n")
    return store


def render_html(store: dict, out_path: Path) -> None:
    """Standalone op-timings page for the dashboard (cross-run p50/p90/max)."""
    rows = sorted(store.items(), key=lambda kv: -(kv[1].get("p90") or 0))
    trs = []
    for key, v in rows:
        trs.append(
            f"<tr><td>{key}</td><td>{v.get('service','')}</td><td>{v.get('kind','')}</td>"
            f"<td class='n'>{_mmss(v.get('p50'))}</td><td class='n'>{_mmss(v.get('p90'))}</td>"
            f"<td class='n'>{_mmss(v.get('max'))}</td><td class='n'>{_mmss(v.get('last_s'))}</td>"
            f"<td class='n'>{v.get('n',0)}</td></tr>")
    html = (
        "<!doctype html><meta charset=utf-8><title>Operation Timings</title>"
        "<style>body{font:14px system-ui;margin:2rem;color:#1a1a2e}"
        "h1{font-size:1.3rem}table{border-collapse:collapse;width:100%}"
        "th,td{padding:6px 10px;border-bottom:1px solid #eee;text-align:left}"
        ".n{text-align:right;font-variant-numeric:tabular-nums}"
        "th{background:#f6f6fb;position:sticky;top:0}</style>"
        "<h1>Operation Timings <small>— per-API create/delete/update 실제 완료시간 (cross-run)</small></h1>"
        "<p>total_s = API 호출 + async 정착(settle) 대기. p50/p90/max는 누적 샘플.</p>"
        "<table><thead><tr><th>catalog key</th><th>service</th><th>kind</th>"
        "<th>p50</th><th>p90</th><th>max</th><th>last</th><th>n</th></tr></thead>"
        f"<tbody>{''.join(trs)}</tbody></table>")
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(html)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Per-operation timing from oplog events.")
    ap.add_argument("run_id", nargs="?", help="run-id (default: newest)")
    ap.add_argument("--list", action="store_true", help="list recent runs and exit")
    ap.add_argument("--json", action="store_true", help="print raw per-op records")
    ap.add_argument("--accumulate", action="store_true", help="fold into --store")
    ap.add_argument("--store", default="data/optimizer/op_timings.json",
                    help="cross-run store path")
    ap.add_argument("--html", help="render the cross-run store to a standalone HTML page")
    args = ap.parse_args(argv)

    store_path = Path(args.store)
    if args.html and not args.run_id and not args.accumulate:
        # render existing store only
        store = json.loads(store_path.read_text()) if store_path.exists() else {}
        render_html(store, Path(args.html))
        print(f"rendered {len(store)} keys -> {args.html}")
        return 0

    s3, bucket = _client()
    runs = _runs_newest_first(s3, bucket)
    if args.list:
        for lm, sz, rid in runs[:20]:
            print(f"{lm.isoformat()}  {rid}")
        return 0
    if not runs:
        sys.exit("no runs with an events.jsonl artifact found")
    run_id = args.run_id or runs[0][2]
    evts = _load_events(s3, bucket, run_id)
    records = derive(evts)

    if args.json:
        for r in records:
            print(json.dumps(r, ensure_ascii=False))
    else:
        print(f"# run {run_id}")
        print_table(records)

    if args.accumulate:
        store = accumulate(records, store_path, run_id)
        print(f"\naccumulated {len(records)} ops -> {store_path} ({len(store)} keys)")
        if args.html:
            render_html(store, Path(args.html))
            print(f"rendered -> {args.html}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
