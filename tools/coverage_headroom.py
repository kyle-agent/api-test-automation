"""Per-service coverage headroom + ledger — the targeting input for the
standing per-service coverage agents.

Every service owns an agent whose single job is to raise THAT service's coverage
(see `.claude/agents/coverage-service.md` / `docs/agent-team.md`). This
module gives the fan-out two things:

  * `headroom()` — per-service (covered / total / gap) from the catalog + the
    unified results store, so the orchestrator dispatches agents worst-gap-first;
  * a persistent **ledger** at `data/coverage_ledger.json` — one row per service
    carrying the live numbers PLUS the agent-maintained `blockers`
    (entitlement / product-bug / heavy-prereq) and `next_levers`, so the service's
    agent resumes where it left off across runs instead of re-discovering.

Read-only over results; `--write` updates the numeric fields of the ledger while
preserving each service's agent-authored `blockers`/`next_levers`/`notes`.

Usage::
    python -m tools.coverage_headroom                 # print headroom table
    python -m tools.coverage_headroom --write         # refresh data/coverage_ledger.json
    python -m tools.coverage_headroom --top 8 --exclude queueservice,apigateway
"""
from __future__ import annotations

import argparse
import json
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OBS = ROOT / "reports" / "results" / "observations.jsonl"
LEDGER = ROOT / "data" / "coverage_ledger.json"

# services that need billable/heavy resources to cover most of their surface —
# the fan-out runs these in a separate gated heavy batch, not the cheap batch.
HEAVY_SVCS = {"virtualserver", "baremetal", "baremetal-blockstorage", "loadbalancer",
              "postgresql", "mysql", "mariadb", "epas", "sqlserver", "vertica",
              "cachestore", "searchengine", "eventstreams", "ske", "multinodegpucluster",
              "vpc", "filestorage", "archivestorage", "backup"}


def _service_of(endpoint_key: str) -> str | None:
    # catalog keys are "category/service/op"; scenario keys ("svc-x:step") don't map.
    parts = endpoint_key.split("/")
    return parts[1] if len(parts) >= 3 else None


def headroom() -> list[dict]:
    from core.catalog import endpoints
    total: dict[str, set] = defaultdict(set)
    for ep in endpoints():
        total[ep.service].add(ep.key)
    ok: dict[str, set] = defaultdict(set)
    try:
        for line in open(OBS):
            line = line.strip()
            if not line:
                continue
            try:
                o = json.loads(line)
            except Exception:
                continue
            if o.get("category") != "ok":
                continue
            svc = _service_of(o.get("endpoint_key") or "")
            if svc:
                ok[svc].add(o["endpoint_key"])
    except FileNotFoundError:
        pass
    rows = []
    for svc, keys in total.items():
        cov = len(keys & ok.get(svc, set()))
        rows.append({"service": svc, "covered": cov, "total": len(keys),
                     "gap": len(keys) - cov, "heavy": svc in HEAVY_SVCS})
    rows.sort(key=lambda r: (-r["gap"], r["service"]))
    return rows


def write_ledger(rows: list[dict]) -> None:
    """Refresh numeric fields; preserve agent-authored blockers/next_levers/notes."""
    prev = {}
    if LEDGER.exists():
        try:
            prev = {r["service"]: r for r in json.loads(LEDGER.read_text()).get("services", [])}
        except Exception:
            prev = {}
    out = []
    for r in rows:
        old = prev.get(r["service"], {})
        out.append({**r,
                    "blockers": old.get("blockers", []),        # entitlement / product-bug / heavy-prereq
                    "next_levers": old.get("next_levers", []),  # what its agent should try next
                    "notes": old.get("notes", ""),
                    "updated": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")})
    LEDGER.parent.mkdir(parents=True, exist_ok=True)
    LEDGER.write_text(json.dumps({"_comment": "Per-service coverage ledger. Numeric "
                                  "fields refreshed by tools.coverage_headroom --write; "
                                  "blockers/next_levers/notes are maintained by each "
                                  "service's coverage agent.", "services": out}, indent=2))


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Per-service coverage headroom + ledger.")
    ap.add_argument("--write", action="store_true", help="refresh data/coverage_ledger.json")
    ap.add_argument("--top", type=int, default=40)
    ap.add_argument("--exclude", default="", help="comma-sep services to skip (already in flight)")
    ap.add_argument("--cheap-only", action="store_true", help="hide heavy/billable services")
    a = ap.parse_args(argv)
    rows = headroom()
    if a.write:
        write_ledger(rows)
        print(f"wrote {LEDGER} ({len(rows)} services)")
    skip = {s for s in a.exclude.split(",") if s}
    shown = [r for r in rows if r["service"] not in skip and not (a.cheap_only and r["heavy"])]
    print(f'{"GAP":>4} {"cov":>4} {"tot":>4} {"heavy":>5}  service')
    for r in shown[:a.top]:
        print(f'{r["gap"]:4} {r["covered"]:4} {r["total"]:4} {"H" if r["heavy"] else "·":>5}  {r["service"]}')
    return 0


if __name__ == "__main__":
    import sys
    sys.exit(main())
