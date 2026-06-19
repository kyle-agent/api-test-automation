"""Live watcher — anomaly detection over the live run state, for the watcher agent.

The watcher's *senses*: one deterministic pass that compares what SHOULD be
happening (a heavy batch is running → billable creates should appear; an account
post-teardown → 0 survivors) against what loggingaudit + the results store + the
live API actually show, and emits an anomaly line per problem. A Monitor runs this
on a cadence and streams anomalies to the orchestrator, who confirms and — if
needed — contacts a dev/coverage/heavy agent to fix it.

Emits **deltas only** (a small state file dedupes), so a standing anomaly isn't
re-reported every cycle; it prints `ANOMALY <key>: ...` when one appears and
`RESOLVED <key>` when it clears. Read-only: loggingaudit spans + observations +
one VPC list. Never mutates anything.

Usage::
    python -m tools.live_watch                      # one pass, print deltas
    python -m tools.live_watch --events reports/audit/_live_view.jsonl
"""
from __future__ import annotations

import argparse
import json
import time
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
STATE = ROOT / "reports" / "audit" / ".watch_state.json"
HEAVY_START = ROOT / "reports" / "audit" / "heavy_batch_start.txt"
EVENTS = ROOT / "reports" / "audit" / "_live_view.jsonl"   # kept fresh by the live loop
OBS = ROOT / "reports" / "results" / "observations.jsonl"

_BILLABLE_KINDS = {"cluster", "virtual-server", "loadbalancer", "baremetal",
                   "nodepool", "postgresql", "mysql", "mariadb", "epas",
                   "cachestore", "sqlserver", "vertica", "searchengine", "eventstreams"}
HEAVY_STALL_MIN = 12     # heavy batch running this long with 0 billable creates == stalled
QUIET_MIN = 10           # owned infra up but no create/delete activity this long == orphan-ish


def _load_events(path: Path) -> list[dict]:
    out = []
    try:
        for line in open(path):
            line = line.strip()
            if line:
                out.append(json.loads(line))
    except Exception:
        pass
    return out


def _heavy_active() -> tuple[bool, float, float]:
    """(is a heavy batch in progress?, minutes since it started, start epoch)."""
    try:
        t = datetime.strptime(HEAVY_START.read_text().strip(), "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
        mins = (datetime.now(timezone.utc) - t).total_seconds() / 60
        return (mins < 120, mins, t.timestamp())   # a heavy batch is "active" for up to 2h
    except Exception:
        return (False, 1e9, 0.0)


def _recent_db_activity(start_ts: float) -> bool:
    """Reliable 'the heavy lifecycles ARE running' signal from the results store
    (not the volatile harvest file): any database/* observation since the batch
    started means clusters were created+exercised — so a momentarily-empty harvest
    or a post-teardown 0-clusters reading must NOT be called a stall."""
    import glob
    for f in glob.glob(str(ROOT / "reports" / "results" / "observations*.jsonl")):
        try:
            for line in open(f):
                if '"database/' not in line:
                    continue
                try:
                    o = json.loads(line)
                except Exception:
                    continue
                if (o.get("endpoint_key") or "").startswith("database/") and (o.get("ts") or 0) >= start_ts:
                    return True
        except FileNotFoundError:
            continue
    return False


def detect(events: list[dict]) -> dict:
    """Return {anomaly_key: human message} for everything currently wrong."""
    now = datetime.now(timezone.utc)
    found: dict[str, str] = {}
    heavy, since_min, start_ts = _heavy_active()

    # activity from loggingaudit: billable Create-Starts + last event time
    billable_creates = 0
    last_evt = None
    for e in events:
        nm = e.get("event_name") or ""
        rt = e.get("resource_type") or ""
        ts = e.get("timestamp")
        if ts:
            last_evt = ts if last_evt is None else max(last_evt, ts)
        if "Create" in nm and rt in _BILLABLE_KINDS:
            billable_creates += 1
    quiet_min = ((now - datetime.strptime(last_evt, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
                  ).total_seconds() / 60) if last_evt else 1e9

    # cross-check LIVE state (ground truth) — loggingaudit lags + the shared
    # _live_view.jsonl can be transiently partial, which would false-fire stalls.
    owned_vpc, owned_clusters = [], 0
    try:
        from core.config import settings
        from core.http_client import ApiClient
        import re
        c = ApiClient(settings)
        j = json.loads((c.get("/v1/vpcs", service="vpc").raw_text) or "{}")
        owned_vpc = [v for v in j.get("vpcs", []) if re.search(r"regr|zznet", str(v.get("name", "")))]
        for eng in ("mysql", "postgresql", "mariadb", "epas", "cachestore"):
            try:
                cj = json.loads((c.get("/v1/clusters", service=eng).raw_text) or "{}")
                lst = next((v for v in cj.values() if isinstance(v, list)), [])
                owned_clusters += sum(1 for x in lst if re.search(r"regr", str(x.get("name", ""))))
            except Exception:
                continue
    except Exception as exc:
        found["WATCH_DEGRADED"] = f"watcher could not reach the API: {exc}"

    # A1 — heavy batch running but NO billable activity. Only a real stall if
    # loggingaudit shows 0 creates AND 0 owned clusters live AND the results store
    # has NO database/* observation since the batch began (the reliable signal —
    # clusters created+torn-down still leave their sub-op observations behind, so
    # this won't false-fire after a successful teardown or on an empty harvest).
    if (heavy and since_min > HEAVY_STALL_MIN and billable_creates == 0
            and owned_clusters == 0 and not _recent_db_activity(start_ts)):
        found["HEAVY_STALL"] = (f"heavy batch started {since_min:.0f}m ago but 0 billable creates in "
                                f"loggingaudit, 0 owned DB clusters live, AND no database/* "
                                f"observations — lifecycles aren't running (check shared-VPC env, "
                                f"host DNS, pytest selection).")

    # A2 — owned billable survivors when NO heavy batch is active == leak
    try:
        if owned_vpc and not heavy:
            found["BILLABLE_SURVIVOR"] = (f"{len(owned_vpc)} owned VPC(s) still ACTIVE with no heavy "
                                          f"batch running ({[v.get('name') for v in owned_vpc]}) — "
                                          f"possible leak / orphaned shared infra.")
        # A3 — owned infra up but the run went quiet (no events) for a while.
        # Require real event data (last_evt set) so an empty/mid-write events file
        # doesn't masquerade as "quiet for 1e9 minutes".
        if owned_vpc and last_evt and quiet_min > QUIET_MIN and heavy:
            found["INFRA_QUIET"] = (f"owned infra up but no loggingaudit activity for {quiet_min:.0f}m "
                                    f"during an active heavy batch — run may be stuck/dead.")
    except Exception as exc:
        found["WATCH_DEGRADED"] = f"watcher could not reach the API to check survivors: {exc}"

    return found


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Live anomaly watcher (delta output).")
    ap.add_argument("--events", default=str(EVENTS))
    ap.add_argument("--reset", action="store_true", help="clear the dedup state")
    a = ap.parse_args(argv)
    if a.reset and STATE.exists():
        STATE.unlink()

    cur = detect(_load_events(Path(a.events)))
    prev = {}
    if STATE.exists():
        try:
            prev = json.loads(STATE.read_text())
        except Exception:
            prev = {}
    stamp = datetime.now(timezone.utc).strftime("%H:%M:%SZ")
    prev_keys = set(prev)
    # emit an anomaly ONCE when it first appears (not every cycle — the message
    # carries a changing elapsed-minutes count, so compare on the stable KEY).
    for k, msg in cur.items():
        if k not in prev_keys:
            print(f"{stamp} ANOMALY {k}: {msg}", flush=True)
    # emit RESOLVED once when it clears
    for k in prev_keys:
        if k not in cur:
            print(f"{stamp} RESOLVED {k}", flush=True)
    STATE.parent.mkdir(parents=True, exist_ok=True)
    STATE.write_text(json.dumps(cur))
    return 0


if __name__ == "__main__":
    import sys
    sys.exit(main())
