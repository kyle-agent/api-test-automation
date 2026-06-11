"""Per-run result snapshot — the archive run-history Reporting reads.

Today the dashboard publish OVERWRITES the latest state (dashboard-data
branch); this module additionally archives each run's canonical outputs —
results JSONL, built dashboard HTML, and a meta.json recording the suite /
profile / catalog version the run used — under ``runs/<run_id>/snapshot/`` in
the same never-deleted oplog bucket (core/oplog.py). That makes "run list →
click → that run's full dashboard incl. coverage" possible later
(docs/PLATFORM-PLAN.md §2.6): today via the bucket directly, in M1 served by
the platform server.

Best-effort like the oplog: missing boto3 / credentials / bucket prints one
notice and no-ops — a broken snapshot must never fail a run.

CLI:
  python -m core.snapshot upload [--suite S] [--profile P]
"""
from __future__ import annotations

import hashlib
import json
import os
import sys
import time
from pathlib import Path

from core import oplog

ROOT = Path(__file__).resolve().parent.parent

# (glob relative to repo root, content type). Existing files only — a smoke-only
# run simply archives fewer files.
SNAPSHOT_GLOBS = (
    ("reports/results/observations.jsonl", "application/x-ndjson"),
    ("reports/results/findings.jsonl", "application/x-ndjson"),
    ("reports/smoke_status.tsv", "text/tab-separated-values"),
    ("dashboard/index.html", "text/html"),
    ("dashboard/services/*.html", "text/html"),
    ("dashboard/history.jsonl", "application/x-ndjson"),
    ("data/conformance.json", "application/json"),
    ("data/conformance_baseline.json", "application/json"),
    ("reports/conformance_new.json", "application/json"),
)


def _put_bytes(c, cfg, key: str, body: bytes, content_type: str) -> bool:
    """put_object with the oplog's public-read-then-private fallback."""
    try:
        c.put_object(Bucket=cfg["bucket"], Key=key, Body=body,
                     ContentType=content_type, ACL="public-read")
        return True
    except Exception:
        pass
    try:
        c.put_object(Bucket=cfg["bucket"], Key=key, Body=body,
                     ContentType=content_type)
        return True
    except Exception as exc:
        print(f"[snapshot] put {key} failed: {exc}")
        return False


def _catalog_version() -> dict:
    """Which spec the run tested against — sha256 + endpoint count."""
    path = ROOT / "data" / "api_catalog.json"
    try:
        raw = path.read_bytes()
        info = {"sha256": hashlib.sha256(raw).hexdigest(), "bytes": len(raw)}
        try:
            catalog = json.loads(raw)
            if isinstance(catalog, list):
                info["endpoints"] = len(catalog)
        except ValueError:
            pass
        return info
    except OSError:
        return {}


def build_meta(suite: str = "", profile: str = "") -> dict:
    return {
        "run_id": oplog._run_id(),
        "ts": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "sha": os.getenv("GITHUB_SHA", "")[:7],
        "branch": os.getenv("GITHUB_REF_NAME", ""),
        "event": os.getenv("GITHUB_EVENT_NAME", ""),
        "suite": suite,
        "profile": profile or os.getenv("SCP_PROFILE_ID", ""),
        "region": os.getenv("SCP_REGION", ""),
        "env": os.getenv("SCP_ENV", ""),
        "catalog": _catalog_version(),
    }


def upload(suite: str = "", profile: str = "") -> int:
    """Archive this run's outputs under runs/<run_id>/snapshot/. Returns the
    number of files uploaded (0 also when the oplog/bucket is disabled)."""
    c, cfg = oplog._client()
    if not c:
        print("[snapshot] disabled (no oplog client) — skipping archive")
        return 0
    rid = oplog._run_id()
    prefix = f"runs/{rid}/snapshot/"
    meta = build_meta(suite, profile)
    uploaded = []
    for pattern, ctype in SNAPSHOT_GLOBS:
        for path in sorted(ROOT.glob(pattern)):
            if not path.is_file() or path.stat().st_size == 0:
                continue
            rel = path.relative_to(ROOT).as_posix()
            if _put_bytes(c, cfg, prefix + rel, path.read_bytes(), ctype):
                uploaded.append(rel)
    meta["files"] = uploaded
    _put_bytes(c, cfg, prefix + "meta.json",
               json.dumps(meta, ensure_ascii=False, indent=1).encode(),
               "application/json")
    print(f"[snapshot] archived {len(uploaded)} file(s) -> "
          f"s3://{cfg['bucket']}/{prefix} (suite={suite or '-'} "
          f"profile={meta['profile'] or '-'})")
    return len(uploaded)


def main(argv=None) -> int:
    import argparse
    ap = argparse.ArgumentParser(description="per-run result snapshot -> object storage")
    sub = ap.add_subparsers(dest="cmd", required=True)
    up = sub.add_parser("upload")
    up.add_argument("--suite", default="")
    up.add_argument("--profile", default="")
    a = ap.parse_args(argv)
    if a.cmd == "upload":
        upload(a.suite, a.profile)
    return 0  # never fail the calling step


if __name__ == "__main__":
    sys.exit(main())
