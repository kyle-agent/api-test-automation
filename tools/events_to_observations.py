"""Rebuild a unified ``observations.jsonl`` from a run's oplog artifact.

Why: the dashboard (``dashboard.build``) reads the unified results store
(``reports/results/observations.jsonl``), which lives on the machine that ran the
console — a remote session only has the run's ``artifact/events.jsonl`` in the
oplog bucket. This tool replays the artifact's ``step-end`` events into
Observation rows using the same keying the engine uses when it records live:

* every step under ``<lifecycle>:<step>``;
* write steps (non-GET) and GETs that carried explicit query ``params`` ALSO under
  their real catalog key (``engine._catalog_key_for``), which is what the
  per-endpoint dashboard cells key on.

Known fidelity gap: the engine's bare id-bound read probes (``_probe_reads``) are
recorded only in the local store, not as artifact events, so their GET coverage is
missing here. The console now also mirrors the local store to the artifact
(``artifact/observations.jsonl``); when that file exists prefer it over this
reconstruction — ``tools.publish_dashboard.sh``-style publishes should pass
``--obs`` accordingly.

    python -m tools.events_to_observations <run-id> [--events PATH] [--out PATH]
"""
from __future__ import annotations

import argparse
import io
import json
import sys
from pathlib import Path

from regression.scenarios import engine
from core import results


def convert(events: list[dict], run_id: str) -> list[dict]:
    rows: list[dict] = []
    for e in events:
        if e.get("kind") != "step-end" or not e.get("method") or not e.get("path"):
            continue
        try:
            status = int(e.get("status") or 0)
        except (TypeError, ValueError):
            continue
        cat = e.get("category") or ("ok" if status < 400 else "fail")
        method = str(e["method"]).upper()
        path = e["path"]
        ems = e.get("elapsed_ms")
        note = ""
        if cat == "fail" or (cat == "soft" and method != "GET" and status >= 400):
            note = (e.get("resp_snippet") or "")[:400]
        ts = e.get("ts") or 0.0
        base = dict(method=method, path=path, status=status, category=cat,
                    elapsed_ms=ems, source="crud_probe", note=note, run=run_id, ts=ts)
        rows.append(dict(endpoint_key=f"{e.get('lifecycle','')}:{e.get('step','')}", **base))
        if method != "GET" or e.get("params"):
            ck = engine._catalog_key_for(method, path, e.get("service"))
            if ck:
                rows.append(dict(endpoint_key=ck, **base))
    return rows


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("run_id")
    ap.add_argument("--events", help="local events.jsonl (default: download runs/<run-id>/artifact/events.jsonl)")
    ap.add_argument("--out", default=str(results.OBSERVATIONS))
    a = ap.parse_args(argv)
    if a.events:
        text = Path(a.events).read_text(encoding="utf-8")
    else:
        from core.oplog import _client
        c, cfg = _client()
        if not c:
            print("oplog client unavailable", file=sys.stderr)
            return 2
        buf = io.BytesIO()
        c.download_fileobj(cfg["bucket"], f"runs/{a.run_id}/artifact/events.jsonl", buf)
        text = buf.getvalue().decode("utf-8")
    events = [json.loads(l) for l in text.splitlines() if l.strip()]
    rows = convert(events, a.run_id)
    out = Path(a.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text("".join(json.dumps(r) + "\n" for r in rows), encoding="utf-8")
    cat = {}
    for r in rows:
        cat[r["category"]] = cat.get(r["category"], 0) + 1
    print(f"{len(rows)} observation(s) from {sum(1 for e in events if e.get('kind')=='step-end')} step-end event(s) "
          f"-> {out}  categories={cat}  catalog-keyed={sum(1 for r in rows if ':' not in r['endpoint_key'])}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
