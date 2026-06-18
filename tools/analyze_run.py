"""Run-log analyzer — the deterministic data layer for the optimizer agent.

After every test run, result logs pile up (``reports/results/observations*.jsonl``,
per-xdist-worker ``observations-gw*.jsonl``, loggingaudit spans, sweep logs). This
module mines them WITHOUT calling the API and emits:

  * ``reports/optimizer/report-<ts>.md`` — a human report (metrics + mechanical
    improvement leads) the optimizer agent reasons on top of;
  * one appended row in ``reports/optimizer/history.jsonl`` — the per-run metric
    vector that makes **cross-day trend analysis** possible (is fail-rate
    dropping? is wall-time shrinking? is parallelism rising?).

It is read-only over the result stores and never touches live resources or the
safety gates. The agent's job is to turn these numbers into concrete changes
(scenario concurrency, deletion order, scope) — this tool just measures, so the
measurement is identical every run and the trend is honest.

Usage::

    python -m tools.analyze_run                       # analyze current result store
    python -m tools.analyze_run --audit reports/audit/ci_live.jsonl   # + topology timing
    python -m tools.analyze_run --label heavy-n6      # tag the history row
"""
from __future__ import annotations

import argparse
import glob
import json
import statistics
import time
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
RESULTS = ROOT / "reports" / "results"
OUT_DIR = ROOT / "reports" / "optimizer"        # per-run reports (ephemeral, gitignored)
# Trend history lives under data/ (TRACKED) so multi-day/cross-session analysis
# survives container resets — reports/ is gitignored. The optimizer agent commits
# new rows as part of its normal run.
HIST = ROOT / "data" / "optimizer" / "history.jsonl"


def _load_observations() -> tuple[list[dict], int]:
    """Union of the merged store + per-worker files, deduped. Returns
    (observations, n_workers) — worker count drives the parallelism analysis."""
    seen, rows = set(), []
    worker_files = sorted(glob.glob(str(RESULTS / "observations-gw*.jsonl")))
    files = worker_files + [str(RESULTS / "observations.jsonl")]
    for f in files:
        try:
            for line in open(f):
                line = line.strip()
                if not line:
                    continue
                try:
                    o = json.loads(line)
                except Exception:
                    continue
                key = (o.get("endpoint_key"), o.get("source"), o.get("status"),
                       round(o.get("ts") or 0, 3))
                if key in seen:
                    continue
                seen.add(key)
                rows.append(o)
        except FileNotFoundError:
            continue
    return rows, len(worker_files)


def _percentile(values: list[float], p: float) -> float:
    if not values:
        return 0.0
    s = sorted(values)
    k = max(0, min(len(s) - 1, int(round((p / 100) * (len(s) - 1)))))
    return s[k]


def analyze(obs: list[dict], n_workers: int) -> dict:
    ts = [o["ts"] for o in obs if o.get("ts")]
    wall = (max(ts) - min(ts)) if len(ts) >= 2 else 0.0
    elapsed = [o["elapsed_ms"] for o in obs if isinstance(o.get("elapsed_ms"), (int, float))]
    cats = Counter(o.get("category") for o in obs)
    sources = Counter(o.get("source") for o in obs)
    total = len(obs)
    fails = [o for o in obs if o.get("category") == "fail"]
    softs = [o for o in obs if o.get("category") == "soft"]

    # recurring problems: same endpoint failing/softing repeatedly == a fixable
    # pattern (missing param default, wrong body) rather than a one-off.
    fail_by_ep = Counter(o.get("endpoint_key") for o in fails)
    soft_by_note = Counter((o.get("note") or "").strip() for o in softs)
    fail_by_note = Counter((o.get("note") or "").strip() for o in fails)

    # slowest endpoints (mean over occurrences) — the test-time long tail.
    by_ep_ms = defaultdict(list)
    for o in obs:
        if isinstance(o.get("elapsed_ms"), (int, float)):
            by_ep_ms[o.get("endpoint_key")].append(o["elapsed_ms"])
    slowest = sorted(((statistics.mean(v), len(v), k) for k, v in by_ep_ms.items()),
                     reverse=True)[:15]

    # parallelism: busy-seconds vs wall-seconds. Observed concurrency = how many
    # calls were effectively in flight on average; compare to worker count to see
    # if we're leaving parallelism on the table (efficiency < 1) — the headline
    # "최대 병렬 수행" metric.
    busy_s = sum(elapsed) / 1000.0
    concurrency = (busy_s / wall) if wall > 0 else 0.0
    efficiency = (concurrency / n_workers) if n_workers else 0.0

    return {
        "ts": time.time(),
        "date": datetime.now(timezone.utc).strftime("%Y-%m-%d"),
        "generated": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "total": total,
        "ok": cats.get("ok", 0), "soft": cats.get("soft", 0), "fail": cats.get("fail", 0),
        "fail_rate": round(len(fails) / total, 4) if total else 0.0,
        "mean_ms": round(statistics.mean(elapsed), 1) if elapsed else 0.0,
        "p95_ms": round(_percentile(elapsed, 95), 1),
        "max_ms": round(max(elapsed), 1) if elapsed else 0.0,
        "wall_s": round(wall, 1),
        "busy_s": round(busy_s, 1),
        "workers": n_workers,
        "concurrency": round(concurrency, 2),
        "efficiency": round(efficiency, 3),
        "sources": dict(sources),
        "fail_by_ep": fail_by_ep.most_common(20),
        "fail_by_note": fail_by_note.most_common(10),
        "soft_by_note": soft_by_note.most_common(12),
        "slowest": [(round(m, 0), n, k) for m, n, k in slowest],
        "distinct_2xx": len({o["endpoint_key"] for o in obs if o.get("category") == "ok"}),
    }


def _audit_timing(audit_path: str) -> dict | None:
    """Optional: per-kind create/delete durations + delete-wait from a loggingaudit
    span file — feeds deletion-order / teardown-time suggestions."""
    try:
        from audit.live_view import build_spans, _t
    except Exception:
        return None
    events = []
    try:
        for line in open(audit_path):
            line = line.strip()
            if line:
                events.append(json.loads(line))
    except FileNotFoundError:
        return None
    if not events:
        return None
    now = datetime.now(timezone.utc)
    spans = build_spans(events, now)
    by_kind = defaultdict(lambda: {"n": 0, "life_s": []})
    for d in spans.values():
        if not d["start"]:
            continue
        rt = d["rtype"]
        by_kind[rt]["n"] += 1
        if d["end"]:
            by_kind[rt]["life_s"].append((_t(d["end"]) - _t(d["start"])).total_seconds())
    rows = []
    for rt, v in by_kind.items():
        mean_life = statistics.mean(v["life_s"]) if v["life_s"] else 0
        rows.append((round(mean_life, 0), v["n"], rt))
    rows.sort(reverse=True)
    return {"slow_teardown_kinds": rows[:12]}


def _trend(cur: dict) -> list[str]:
    """Compare this run to history: is it improving across days?"""
    if not HIST.exists():
        return ["(no history yet — this is the baseline row)"]
    prev = []
    for line in HIST.read_text().splitlines():
        line = line.strip()
        if line:
            try:
                prev.append(json.loads(line))
            except Exception:
                pass
    if not prev:
        return ["(no prior runs)"]
    last = prev[-1]
    out = []

    def cmp(field, lower_better, unit=""):
        a, b = last.get(field), cur.get(field)
        if a is None or b is None:
            return
        d = b - a
        arrow = "▼" if d < 0 else ("▲" if d > 0 else "=")
        good = (d < 0) if lower_better else (d > 0)
        tag = "✅" if (good and d != 0) else ("⚠️" if (d != 0 and not good) else "·")
        out.append(f"  {tag} {field}: {a}{unit} → {b}{unit} ({arrow}{abs(round(d,3))})")

    cmp("fail_rate", True)
    cmp("fail", True)
    cmp("wall_s", True, "s")
    cmp("mean_ms", True, "ms")
    cmp("p95_ms", True, "ms")
    cmp("concurrency", False)
    cmp("efficiency", False)
    cmp("distinct_2xx", False)

    # 7-run window direction
    window = prev[-7:]
    if len(window) >= 3:
        frs = [p.get("fail_rate", 0) for p in window] + [cur["fail_rate"]]
        eff = [p.get("efficiency", 0) for p in window] + [cur["efficiency"]]
        out.append(f"  trend(≤8 runs): fail_rate {frs[0]}→{frs[-1]}, "
                   f"efficiency {eff[0]}→{eff[-1]}")
    return out


def _leads(a: dict, audit: dict | None) -> list[str]:
    """Mechanical improvement leads (the agent expands these into changes)."""
    leads = []
    if a["efficiency"] and a["efficiency"] < 0.6 and a["workers"]:
        leads.append(f"PARALLELISM: efficiency {a['efficiency']} (concurrency "
                     f"{a['concurrency']} of {a['workers']} workers) — calls are "
                     f"serializing. Raise -n, or split long scenarios so workers don't idle.")
    if a["fail_by_ep"]:
        top = ", ".join(f"{k}×{n}" for k, n in a["fail_by_ep"][:5])
        leads.append(f"RECURRING FAILS: {top} — fix at the source (param default, "
                     f"body shape, or baseline if a real backend bug).")
    if a["p95_ms"] and a["mean_ms"] and a["p95_ms"] > 4 * a["mean_ms"]:
        leads.append(f"LONG TAIL: p95 {a['p95_ms']}ms ≫ mean {a['mean_ms']}ms — a "
                     f"few slow endpoints dominate wall-time; see slowest[] (retry/timeout tune).")
    if a["slowest"] and a["slowest"][0][0] > 8000:
        ms, n, k = a["slowest"][0]
        leads.append(f"SLOWEST: {k} ~{ms:.0f}ms ×{n} — candidate for a tighter timeout "
                     f"or removal from the hot path.")
    if audit and audit.get("slow_teardown_kinds"):
        s, n, k = audit["slow_teardown_kinds"][0]
        leads.append(f"TEARDOWN: {k} lived ~{s:.0f}s ×{n} — review delete ordering / "
                     f"NOWAIT sweep so teardown isn't on the critical path.")
    if not leads:
        leads.append("No mechanical lead crossed threshold — agent should still scan "
                     "soft_by_note + multi-day history for slower-moving patterns.")
    return leads


def render_md(a: dict, audit: dict | None) -> str:
    L = [f"# Run optimization report — {a['generated']}", ""]
    L.append(f"**{a['total']}** obs · ok {a['ok']} / soft {a['soft']} / **fail {a['fail']}** "
             f"(fail-rate {a['fail_rate']}) · distinct 2xx {a['distinct_2xx']}")
    L.append(f"**Time**: wall {a['wall_s']}s · busy {a['busy_s']}s · mean {a['mean_ms']}ms "
             f"· p95 {a['p95_ms']}ms · max {a['max_ms']}ms")
    L.append(f"**Parallelism**: {a['workers']} workers · observed concurrency "
             f"{a['concurrency']} · efficiency {a['efficiency']}")
    L.append(f"**Sources**: {a['sources']}")
    L.append("")
    L.append("## Improvement leads")
    for x in _leads(a, audit):
        L.append(f"- {x}")
    L.append("")
    L.append("## Cross-run trend (vs last + window)")
    L += _trend(a)
    L.append("")
    if a["fail_by_ep"]:
        L.append("## Recurring fails (endpoint × count)")
        for k, n in a["fail_by_ep"]:
            L.append(f"- {k} × {n}")
        L.append("")
    if a["fail_by_note"]:
        L.append("## Fail notes (reason × count)")
        for note, n in a["fail_by_note"]:
            L.append(f"- {n}× — {note or '(none)'}")
        L.append("")
    if a["soft_by_note"]:
        L.append("## Soft reasons (recoverable-with-data)")
        for note, n in a["soft_by_note"]:
            L.append(f"- {n}× — {note or '(none)'}")
        L.append("")
    L.append("## Slowest endpoints (mean ms × occurrences)")
    for ms, n, k in a["slowest"]:
        L.append(f"- {ms:.0f}ms ×{n} — {k}")
    if audit and audit.get("slow_teardown_kinds"):
        L.append("")
        L.append("## Longest-lived kinds (teardown critical path)")
        for s, n, k in audit["slow_teardown_kinds"]:
            L.append(f"- {s:.0f}s ×{n} — {k}")
    L.append("")
    return "\n".join(L)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Analyze run logs for the optimizer agent.")
    ap.add_argument("--audit", help="loggingaudit span jsonl for teardown timing")
    ap.add_argument("--label", default="", help="tag the history row (e.g. heavy-n6)")
    ap.add_argument("--out", default="", help="report path (default reports/optimizer/report-<ts>.md)")
    a = ap.parse_args(argv)

    obs, n_workers = _load_observations()
    if not obs:
        print("analyze_run: no observations found in reports/results/ — nothing to analyze")
        return 1
    metrics = analyze(obs, n_workers)
    metrics["label"] = a.label
    audit = _audit_timing(a.audit) if a.audit else None

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    HIST.parent.mkdir(parents=True, exist_ok=True)
    md = render_md(metrics, audit)
    out = a.out or str(OUT_DIR / f"report-{int(metrics['ts'])}.md")
    Path(out).write_text(md)
    # append the slim metric vector (no big lists) for trend analysis
    slim = {k: v for k, v in metrics.items()
            if k not in ("fail_by_ep", "fail_by_note", "soft_by_note", "slowest")}
    with open(HIST, "a") as fh:
        fh.write(json.dumps(slim) + "\n")

    print(f"analyze_run: {metrics['total']} obs · fail {metrics['fail']} "
          f"(rate {metrics['fail_rate']}) · wall {metrics['wall_s']}s · "
          f"efficiency {metrics['efficiency']} -> {out}")
    return 0


if __name__ == "__main__":
    import sys
    sys.exit(main())
