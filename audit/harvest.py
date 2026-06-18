"""Audit-log HARVESTER — pull the SCP loggingaudit event log for a run window.

Read-only. Calls `GET /v1/logs` on the `loggingaudit` service (page 0,1,...
size 100) over a [start_at, end_at] window and writes every event as JSONL.

Each log event carries (among others):
    event_name, event_type (e.g. "mysql.create.end" / "mariadb.delete.start"),
    resource_type, resource_id, resource_name, product_name, status,
    timestamp (ISO8601 Z), region, request_user_name.

Usage:
    python -m audit.harvest --start <ISO> --end <ISO> \
        [--out reports/audit/<id>.jsonl] [--service loggingaudit] [--max-pages 300]

Window defaults: if --start/--end are omitted, derive [start, end] from the env
var APITEST_RUN_STARTED_AT (start) and now (end), both UTC; if that is not set,
error asking for an explicit window. Never mutates — GET only.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

from core.http_client import ApiClient

PAGE_SIZE = 100


def _now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _resolve_window(start: str | None, end: str | None) -> tuple[str, str]:
    """Resolve the [start, end] window, falling back to env/now defaults."""
    if not end:
        end = _now_iso()
    if not start:
        start = os.environ.get("APITEST_RUN_STARTED_AT", "").strip()
    if not start:
        raise SystemExit(
            "No window: pass --start <ISO> --end <ISO> (e.g. 2026-06-18T06:30:00Z), "
            "or set APITEST_RUN_STARTED_AT in the environment.")
    return start, end


def harvest(start: str, end: str, out: Path, service: str = "loggingaudit",
            max_pages: int = 300) -> int:
    """Harvest all audit events in [start, end] to `out` as JSONL. Returns count."""
    client = ApiClient()
    out.parent.mkdir(parents=True, exist_ok=True)

    events: list[dict] = []
    pages = 0
    for page in range(max_pages):
        path = (f"/v1/logs?start_at={start}&end_at={end}"
                f"&size={PAGE_SIZE}&page={page}")
        try:
            resp = client.get(path, service=service, timeout=20, retry=False)
        except Exception as exc:  # network/timeout/etc — be defensive, stop
            print(f"[harvest] request error on page {page}: {exc!r} — stopping",
                  file=sys.stderr)
            break
        pages = page + 1
        if resp.status != 200:
            snippet = (resp.raw_text or "")[:300].replace("\n", " ")
            print(f"[harvest] non-200 on page {page}: status={resp.status} "
                  f"body={snippet!r} — stopping", file=sys.stderr)
            break
        body = resp.body if isinstance(resp.body, dict) else {}
        logs = body.get("logs") or []
        events.extend(logs)
        if len(logs) < PAGE_SIZE:
            break  # last page

    with out.open("w", encoding="utf-8") as fh:
        for ev in events:
            fh.write(json.dumps(ev, ensure_ascii=False) + "\n")

    print(f"[harvest] {len(events)} events over {pages} page(s) "
          f"[{start} .. {end}] -> {out}")
    return len(events)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Harvest SCP loggingaudit events to JSONL.")
    ap.add_argument("--start", default=None, help="window start (ISO8601 Z)")
    ap.add_argument("--end", default=None, help="window end (ISO8601 Z)")
    ap.add_argument("--out", default=None, help="output JSONL path")
    ap.add_argument("--service", default="loggingaudit", help="SCP service name")
    ap.add_argument("--max-pages", type=int, default=300, help="page cap")
    args = ap.parse_args(argv)

    start, end = _resolve_window(args.start, args.end)
    if args.out:
        out = Path(args.out)
    else:
        safe = start.replace(":", "").replace("-", "")
        out = Path("reports/audit") / f"harvest-{safe}.jsonl"

    harvest(start, end, out, service=args.service, max_pages=args.max_pages)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
