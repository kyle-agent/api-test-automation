"""Scenario OPTIMIZER / deep-analysis tool (AXIS post-run analysis).

Reads a JSONL of harvested SCP audit events (see ``audit.harvest``) and derives
time/cost optimization signals for the regression scenario suite:

  1. Operation durations  - pair ``.start``/``.end`` per (resource_id, action);
                            aggregate per action (n, median, p95, max minutes).
  2. Redundancy           - distinct resource_names CREATED per resource_type;
                            >1 distinct create => consolidation candidate.
  3. Cost proxy           - per billable resource_type, sum LIFETIME minutes
                            (create -> delete) = "billable resource-minutes";
                            still-LIVE resources (no delete) are the leak risk.
  4. Run wall-time        - overall window + per-resource_type timeline.
  5. Recommendations      - auto-generated optimization bullets.

Read-only. Emits a markdown report and a machine JSON.

    python -m audit.optimizer <events.jsonl> \
        [--out reports/audit/<id>-report.md] \
        [--json reports/audit/<id>-analysis.json]

Robust to: events missing a start/end pair (counted as unpaired), bad/missing
timestamps (skipped, counted), and the ``type:ROOT`` envelope field.
"""

from __future__ import annotations

import argparse
import json
import statistics
import sys
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Iterable

TS_FMT = "%Y-%m-%dT%H:%M:%SZ"

# resource_types that incur (DBaaS / compute / SKE) running cost while alive.
BILLABLE_TYPES = {
    "mysql",
    "mariadb",
    "epas",
    "postgresql",
    "sqlserver",
    "cachestore",
    "searchengine",
    "eventstreams",
    "vertica",
    "cluster",
    "nodepool",
    "virtual-server",
    "baremetal",
    "gpu",
}


# --------------------------------------------------------------------------- #
# Parsing
# --------------------------------------------------------------------------- #
@dataclass
class Event:
    event_type: str
    resource_type: str
    resource_id: str
    resource_name: str
    product_name: str
    status: str
    timestamp: str
    ts: datetime | None  # parsed; None if missing/unparseable

    @property
    def action(self) -> str | None:
        """event_type with trailing ``.start``/``.end`` stripped, else None."""
        if self.event_type.endswith(".start"):
            return self.event_type[: -len(".start")]
        if self.event_type.endswith(".end"):
            return self.event_type[: -len(".end")]
        return None

    @property
    def phase(self) -> str | None:
        if self.event_type.endswith(".start"):
            return "start"
        if self.event_type.endswith(".end"):
            return "end"
        return None

    @property
    def is_create(self) -> bool:
        return ".create" in self.event_type

    @property
    def is_delete(self) -> bool:
        return ".delete" in self.event_type


def _parse_ts(raw: str | None) -> datetime | None:
    if not raw:
        return None
    try:
        return datetime.strptime(raw, TS_FMT)
    except (ValueError, TypeError):
        return None


@dataclass
class ParseResult:
    events: list[Event] = field(default_factory=list)
    total_lines: int = 0
    bad_json: int = 0
    bad_timestamp: int = 0


def load_events(path: Path) -> ParseResult:
    res = ParseResult()
    with path.open("r", encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            res.total_lines += 1
            try:
                raw = json.loads(line)
            except json.JSONDecodeError:
                res.bad_json += 1
                continue
            if not isinstance(raw, dict):
                res.bad_json += 1
                continue
            ts_raw = raw.get("timestamp")
            ts = _parse_ts(ts_raw)
            if ts is None:
                res.bad_timestamp += 1
            ev = Event(
                event_type=str(raw.get("event_type", "")),
                resource_type=str(raw.get("resource_type", "") or "-"),
                resource_id=str(raw.get("resource_id", "") or ""),
                resource_name=str(raw.get("resource_name", "") or ""),
                product_name=str(raw.get("product_name", "") or "-"),
                status=str(raw.get("status", "") or "-"),
                timestamp=str(ts_raw or ""),
                ts=ts,
            )
            res.events.append(ev)
    return res


# --------------------------------------------------------------------------- #
# Analyses
# --------------------------------------------------------------------------- #
def _pctl(values: list[float], q: float) -> float:
    """Nearest-rank-ish percentile, linear interpolation; q in [0,1]."""
    if not values:
        return 0.0
    s = sorted(values)
    if len(s) == 1:
        return s[0]
    pos = q * (len(s) - 1)
    lo = int(pos)
    hi = min(lo + 1, len(s) - 1)
    frac = pos - lo
    return s[lo] + (s[hi] - s[lo]) * frac


def analyze_durations(events: list[Event]) -> dict[str, Any]:
    """Pair .start/.end by (resource_id, action); aggregate per action."""
    # process in timestamp order so an earlier start binds to the next end
    timed = [e for e in events if e.ts is not None and e.phase]
    timed.sort(key=lambda e: e.ts)  # type: ignore[arg-type,return-value]

    open_starts: dict[tuple[str, str], datetime] = {}
    durations: dict[str, list[float]] = defaultdict(list)
    unpaired_start = 0
    unpaired_end = 0
    end_keys_seen: set[tuple[str, str]] = set()

    for e in timed:
        action = e.action
        assert action is not None
        key = (e.resource_id, action)
        if e.phase == "start":
            open_starts[key] = e.ts  # type: ignore[assignment]
        else:  # end
            end_keys_seen.add(key)
            start_ts = open_starts.pop(key, None)
            if start_ts is None:
                unpaired_end += 1
                continue
            minutes = (e.ts - start_ts).total_seconds() / 60.0  # type: ignore[operator]
            if minutes < 0:
                # clock skew / out-of-order; ignore the negative pair
                unpaired_end += 1
                continue
            durations[action].append(minutes)

    unpaired_start = len(open_starts)

    rows = []
    for action, vals in durations.items():
        rows.append(
            {
                "action": action,
                "n": len(vals),
                "median_min": round(statistics.median(vals), 2),
                "p95_min": round(_pctl(vals, 0.95), 2),
                "max_min": round(max(vals), 2),
                "total_min": round(sum(vals), 2),
            }
        )
    rows.sort(key=lambda r: r["median_min"], reverse=True)
    return {
        "actions": rows,
        "paired_count": sum(len(v) for v in durations.values()),
        "unpaired_start": unpaired_start,
        "unpaired_end": unpaired_end,
    }


def analyze_redundancy(events: list[Event]) -> dict[str, Any]:
    """Distinct resource_names created per resource_type."""
    created: dict[str, set[str]] = defaultdict(set)
    for e in events:
        if e.is_create:
            # ignore blank names (e.g. ports/volumes auto-created w/o name)
            name = e.resource_name.strip()
            if name:
                created[e.resource_type].add(name)
    rows = []
    for rtype, names in created.items():
        rows.append(
            {
                "resource_type": rtype,
                "distinct_creates": len(names),
                "names": sorted(names),
                "consolidation_candidate": len(names) > 1,
            }
        )
    rows.sort(key=lambda r: r["distinct_creates"], reverse=True)
    return {"resource_types": rows}


def analyze_cost(events: list[Event]) -> dict[str, Any]:
    """Per billable resource lifetime (create -> delete) in minutes."""
    # Group billable events by resource (prefer resource_id, fall back to name).
    by_resource: dict[tuple[str, str], list[Event]] = defaultdict(list)
    for e in events:
        if e.resource_type not in BILLABLE_TYPES:
            continue
        if e.ts is None:
            continue
        rid = e.resource_id or f"name:{e.resource_name}"
        by_resource[(e.resource_type, rid)].append(e)

    per_type_minutes: dict[str, float] = defaultdict(float)
    per_type_count: dict[str, int] = defaultdict(int)
    live_by_type: dict[str, list[str]] = defaultdict(list)
    resources = []

    for (rtype, rid), evs in by_resource.items():
        evs.sort(key=lambda e: e.ts)  # type: ignore[arg-type,return-value]
        # birth = first create.* event, else first event
        creates = [e for e in evs if e.is_create]
        birth = (creates[0].ts if creates else evs[0].ts)
        # death = matching delete.end, else last delete.*, else None (still live)
        delete_ends = [e for e in evs if e.is_delete and e.phase == "end"]
        deletes = [e for e in evs if e.is_delete]
        if delete_ends:
            death = delete_ends[-1].ts
            live = False
        elif deletes:
            death = deletes[-1].ts
            live = False
        else:
            death = None
            live = True
        name = next((e.resource_name for e in evs if e.resource_name), rid)
        if live:
            # cost proxy for a live resource = up to last observed event
            end_ts = evs[-1].ts
            live_by_type[rtype].append(name)
        else:
            end_ts = death
        lifetime = max(0.0, (end_ts - birth).total_seconds() / 60.0)  # type: ignore[operator]
        per_type_minutes[rtype] += lifetime
        per_type_count[rtype] += 1
        resources.append(
            {
                "resource_type": rtype,
                "resource_id": rid,
                "resource_name": name,
                "lifetime_min": round(lifetime, 2),
                "live": live,
            }
        )

    type_rows = []
    for rtype, minutes in per_type_minutes.items():
        type_rows.append(
            {
                "resource_type": rtype,
                "resource_minutes": round(minutes, 2),
                "n_resources": per_type_count[rtype],
                "live_names": sorted(live_by_type.get(rtype, [])),
            }
        )
    type_rows.sort(key=lambda r: r["resource_minutes"], reverse=True)

    live_total = sorted(
        {(r["resource_type"], n) for r in type_rows for n in r["live_names"]}
    )
    return {
        "per_type": type_rows,
        "resources": sorted(resources, key=lambda r: r["lifetime_min"], reverse=True),
        "total_resource_minutes": round(sum(per_type_minutes.values()), 2),
        "live_resources": [{"resource_type": t, "resource_name": n} for t, n in live_total],
    }


def analyze_walltime(events: list[Event]) -> dict[str, Any]:
    timed = [e for e in events if e.ts is not None]
    if not timed:
        return {"start": None, "end": None, "minutes": 0.0, "per_type": []}
    tmin = min(e.ts for e in timed)  # type: ignore[type-var]
    tmax = max(e.ts for e in timed)  # type: ignore[type-var]
    per_type: dict[str, list[datetime]] = defaultdict(list)
    for e in timed:
        per_type[e.resource_type].append(e.ts)  # type: ignore[arg-type]
    rows = []
    for rtype, tss in per_type.items():
        f, l = min(tss), max(tss)
        rows.append(
            {
                "resource_type": rtype,
                "first": f.strftime(TS_FMT),
                "last": l.strftime(TS_FMT),
                "span_min": round((l - f).total_seconds() / 60.0, 2),
                "n_events": len(tss),
            }
        )
    rows.sort(key=lambda r: r["span_min"], reverse=True)
    return {
        "start": tmin.strftime(TS_FMT),
        "end": tmax.strftime(TS_FMT),
        "minutes": round((tmax - tmin).total_seconds() / 60.0, 2),
        "per_type": rows,
    }


def build_recommendations(
    durations: dict[str, Any],
    redundancy: dict[str, Any],
    cost: dict[str, Any],
    walltime: dict[str, Any],
) -> list[str]:
    recs: list[str] = []

    # median minutes per create action, keyed by resource_type (action prefix)
    create_median: dict[str, float] = {}
    for row in durations["actions"]:
        action = row["action"]
        if action.endswith(".create"):
            rtype = action.split(".")[0]
            # keep the simple "<type>.create" if present, else first seen
            if action == f"{rtype}.create" or rtype not in create_median:
                create_median[rtype] = row["median_min"]

    # consolidation candidates with a known per-create cost
    cand = [
        r
        for r in redundancy["resource_types"]
        if r["consolidation_candidate"] and r["resource_type"] in BILLABLE_TYPES
    ]
    cand.sort(
        key=lambda r: (r["distinct_creates"] - 1)
        * create_median.get(r["resource_type"], 0.0),
        reverse=True,
    )
    for r in cand:
        rtype = r["resource_type"]
        n = r["distinct_creates"]
        each = create_median.get(rtype)
        if each:
            save = round((n - 1) * each, 1)
            recs.append(
                f"`{rtype}` created {n}x (~{each:.1f} min each) -> consolidate to 1 "
                f"(save ~{save} min/run)."
            )
        else:
            recs.append(
                f"`{rtype}` created {n}x -> review whether all are needed "
                f"(consolidate to reduce setup time)."
            )

    # single biggest time sink (slowest action by median * n total)
    if durations["actions"]:
        sink = max(durations["actions"], key=lambda r: r["total_min"])
        recs.append(
            f"Biggest time sink: `{sink['action']}` "
            f"(n={sink['n']}, median {sink['median_min']:.1f} min, "
            f"total {sink['total_min']:.1f} min across the run)."
        )

    # still-live / leak risk
    live = cost["live_resources"]
    if live:
        names = ", ".join(f"{r['resource_type']}/{r['resource_name']}" for r in live)
        recs.append(
            f"Still-LIVE (no delete observed) -> COST/LEAK RISK: {names}. "
            f"Confirm cleanup ran."
        )
    else:
        recs.append("No still-live billable resources detected (clean teardown).")

    # cost-heaviest type
    if cost["per_type"]:
        top = cost["per_type"][0]
        recs.append(
            f"Cost-heaviest type: `{top['resource_type']}` "
            f"({top['resource_minutes']:.1f} billable resource-minutes "
            f"across {top['n_resources']} resources)."
        )

    return recs


# --------------------------------------------------------------------------- #
# Rendering
# --------------------------------------------------------------------------- #
def _md_table(headers: list[str], rows: Iterable[list[Any]]) -> str:
    out = ["| " + " | ".join(headers) + " |"]
    out.append("| " + " | ".join("---" for _ in headers) + " |")
    for r in rows:
        out.append("| " + " | ".join(str(c) for c in r) + " |")
    return "\n".join(out)


def render_markdown(analysis: dict[str, Any]) -> str:
    meta = analysis["meta"]
    durations = analysis["durations"]
    redundancy = analysis["redundancy"]
    cost = analysis["cost"]
    walltime = analysis["walltime"]
    recs = analysis["recommendations"]

    L: list[str] = []
    L.append(f"# Scenario Optimizer Report — `{meta['source']}`")
    L.append("")
    L.append(
        f"_{meta['event_count']} events parsed "
        f"({meta['bad_json']} bad-json, {meta['bad_timestamp']} bad-timestamp)._"
    )
    L.append("")

    # 4. Run wall-time (lead with the headline window)
    L.append("## Run wall-time")
    L.append("")
    L.append(
        f"- Window: `{walltime['start']}` → `{walltime['end']}` "
        f"(**{walltime['minutes']:.1f} min** wall-clock)."
    )
    L.append("")
    L.append(
        _md_table(
            ["resource_type", "first", "last", "span (min)", "events"],
            [
                [r["resource_type"], r["first"], r["last"], r["span_min"], r["n_events"]]
                for r in walltime["per_type"]
            ],
        )
    )
    L.append("")

    # 1. Operation durations
    L.append("## 1. Operation durations (slowest first)")
    L.append("")
    L.append(
        f"_Paired {durations['paired_count']} ops; "
        f"unpaired: {durations['unpaired_start']} start-only, "
        f"{durations['unpaired_end']} end-only._"
    )
    L.append("")
    L.append(
        _md_table(
            ["action", "n", "median (min)", "p95 (min)", "max (min)", "total (min)"],
            [
                [
                    r["action"],
                    r["n"],
                    r["median_min"],
                    r["p95_min"],
                    r["max_min"],
                    r["total_min"],
                ]
                for r in durations["actions"]
            ],
        )
    )
    L.append("")

    # 2. Redundancy
    L.append("## 2. Redundancy (distinct creates per resource_type)")
    L.append("")
    cands = [r for r in redundancy["resource_types"] if r["consolidation_candidate"]]
    L.append(
        f"_{len(cands)} resource_type(s) created >1 distinct resource — "
        f"consolidation candidates flagged below._"
    )
    L.append("")
    L.append(
        _md_table(
            ["resource_type", "distinct creates", "candidate?", "names"],
            [
                [
                    r["resource_type"],
                    r["distinct_creates"],
                    "⚠️ yes" if r["consolidation_candidate"] else "no",
                    ", ".join(r["names"]) if r["names"] else "—",
                ]
                for r in redundancy["resource_types"]
            ],
        )
    )
    L.append("")

    # 3. Cost proxy
    L.append("## 3. Cost proxy (billable resource-minutes)")
    L.append("")
    L.append(
        f"- Total billable lifetime: **{cost['total_resource_minutes']:.1f} "
        f"resource-minutes**."
    )
    if cost["live_resources"]:
        names = ", ".join(
            f"`{r['resource_type']}/{r['resource_name']}`" for r in cost["live_resources"]
        )
        L.append(f"- ⚠️ **Still-LIVE (leak/cost risk):** {names}")
    else:
        L.append("- ✅ No still-live billable resources (clean teardown).")
    L.append("")
    L.append(
        _md_table(
            ["resource_type", "resource-minutes", "# resources", "live names"],
            [
                [
                    r["resource_type"],
                    r["resource_minutes"],
                    r["n_resources"],
                    ", ".join(r["live_names"]) if r["live_names"] else "—",
                ]
                for r in cost["per_type"]
            ],
        )
    )
    L.append("")

    # 5. Recommendations
    L.append("## Top optimization recommendations")
    L.append("")
    for r in recs:
        L.append(f"- {r}")
    L.append("")

    return "\n".join(L)


# --------------------------------------------------------------------------- #
# Orchestration
# --------------------------------------------------------------------------- #
def analyze(path: Path) -> dict[str, Any]:
    parsed = load_events(path)
    events = parsed.events
    durations = analyze_durations(events)
    redundancy = analyze_redundancy(events)
    cost = analyze_cost(events)
    walltime = analyze_walltime(events)
    recs = build_recommendations(durations, redundancy, cost, walltime)
    return {
        "meta": {
            "source": str(path),
            "event_count": len(events),
            "total_lines": parsed.total_lines,
            "bad_json": parsed.bad_json,
            "bad_timestamp": parsed.bad_timestamp,
        },
        "durations": durations,
        "redundancy": redundancy,
        "cost": cost,
        "walltime": walltime,
        "recommendations": recs,
    }


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        prog="python -m audit.optimizer",
        description="Deep time/cost analysis of harvested SCP audit events.",
    )
    ap.add_argument("events", type=Path, help="path to harvested events JSONL")
    ap.add_argument("--out", type=Path, default=None, help="markdown report output path")
    ap.add_argument("--json", type=Path, default=None, help="machine JSON output path")
    args = ap.parse_args(argv)

    if not args.events.exists():
        print(f"error: events file not found: {args.events}", file=sys.stderr)
        return 2

    analysis = analyze(args.events)
    markdown = render_markdown(analysis)

    if args.out:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(markdown, encoding="utf-8")
        print(f"wrote markdown report -> {args.out}", file=sys.stderr)
    if args.json:
        args.json.parent.mkdir(parents=True, exist_ok=True)
        args.json.write_text(json.dumps(analysis, indent=2), encoding="utf-8")
        print(f"wrote analysis json  -> {args.json}", file=sys.stderr)

    # Always print the markdown to stdout.
    print(markdown)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
