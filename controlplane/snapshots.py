"""Read per-run snapshots (core/snapshot.py) back from the oplog bucket.

The dashboard job archives each run's results + built dashboard under
runs/<run_id>/snapshot/ — this module serves them to the run-history UI so a
past run's full dashboard (coverage included) can be reopened.
"""
from __future__ import annotations

import json

from core import oplog


def fetch(gh_run_id: str, rel_path: str) -> tuple[bytes, str] | None:
    """One snapshot file -> (body, content_type), or None when absent/disabled."""
    if ".." in rel_path or rel_path.startswith("/"):
        return None
    c, cfg = oplog._client()
    if not c:
        return None
    try:
        obj = c.get_object(Bucket=cfg["bucket"],
                           Key=f"runs/{gh_run_id}/snapshot/{rel_path}")
        return obj["Body"].read(), obj.get("ContentType") or "application/octet-stream"
    except Exception:
        return None


def meta(gh_run_id: str) -> dict | None:
    got = fetch(gh_run_id, "meta.json")
    if not got:
        return None
    try:
        return json.loads(got[0])
    except ValueError:
        return None


def observations(gh_run_id: str) -> list[dict]:
    """The run's AXIS-1 observations (one dict per endpoint call)."""
    got = fetch(gh_run_id, "reports/results/observations.jsonl")
    if not got:
        return []
    rows = []
    for line in got[0].decode(errors="replace").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            rows.append(json.loads(line))
        except ValueError:
            continue
    return rows


def archive_index(limit: int = 100) -> list[dict]:
    """The oplog's newest-first run index (covers runs older than the DB)."""
    c, cfg = oplog._client()
    if not c:
        return []
    try:
        obj = c.get_object(Bucket=cfg["bucket"], Key="index.json")
        index = json.loads(obj["Body"].read())
        return index[:limit] if isinstance(index, list) else []
    except Exception:
        return []
