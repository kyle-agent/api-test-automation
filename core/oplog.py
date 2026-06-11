"""Operations log — persistent, cross-run workflow progress on Object Storage.

A single NEVER-DELETED S3-compatible bucket (default ``apitest-oplog``) on the
test account accumulates one small JSON object per workflow milestone, so a
static viewer (``dashboard/ops.html``) can show the LIVE progress of the
current run and the history of every past run — independent of GitHub.

Layout (all keys under the bucket):
  runs/<run_id>/run.json                  run manifest (sha, branch, options)
  runs/<run_id>/events/<ms>-<stage>.json  one object per milestone (unique key
                                          per event -> no write races between
                                          the parallel A/B jobs)
  runs/<run_id>/summary.json              final summary (history row)
  index.json                              [{run summary}, ...] newest-first —
                                          read-modify-written ONLY by the
                                          dashboard job (single writer at the
                                          end of a run, so no race)

SCP Object Storage is Amazon-S3 compatible (userguide "Amazon S3 활용 가이드")
and accepts the SAME access/secret key pair as the Open APIs (owner-confirmed
2026-06-11). SDK region for kr-west1 is "kr-west"; the real region is resolved
from the endpoint URL.

Everything here is BEST-EFFORT and self-disabling: missing boto3, missing
credentials, or an unreachable endpoint prints one notice and no-ops — a
broken oplog must never fail a test run.

CLI:
  python -m core.oplog ensure                      # create bucket + CORS + ACL
  python -m core.oplog emit --stage smoke --status done [--detail '...']
  python -m core.oplog finalize --history dashboard/history.jsonl
"""
from __future__ import annotations

import json
import os
import sys
import time

_NOTICE_SHOWN = False


def _cfg():
    """Resolve endpoint/bucket/credentials from env (None = disabled)."""
    bucket = os.getenv("SCP_OPLOG_BUCKET", "apitest-oplog").strip()
    access = (os.getenv("SCP_OPLOG_ACCESS_KEY") or os.getenv("SCP_ACCESS_KEY") or "").strip()
    secret = (os.getenv("SCP_OPLOG_SECRET_KEY") or os.getenv("SCP_SECRET_KEY") or "").strip()
    endpoint = os.getenv("SCP_OPLOG_S3_ENDPOINT", "").strip()
    if not endpoint:
        # per-service host convention; override via SCP_OPLOG_S3_ENDPOINT with
        # the Public URL from the Object Storage detail page if this guess is
        # wrong for the account.
        region = os.getenv("SCP_REGION", "kr-west1").strip()
        env = os.getenv("SCP_ENV", "e").strip()
        endpoint = f"https://objectstorage.{region}.{env}.samsungsdscloud.com"
    # SDK region: kr-west1 -> kr-west, kr-south1/2/3 -> kr-south (userguide)
    region = os.getenv("SCP_REGION", "kr-west1").strip()
    sdk_region = "kr-south" if region.startswith("kr-south") else "kr-west"
    if not (bucket and access and secret):
        return None
    return {"bucket": bucket, "endpoint": endpoint, "region": sdk_region,
            "access": access, "secret": secret}


def _client():
    global _NOTICE_SHOWN
    cfg = _cfg()
    if not cfg:
        if not _NOTICE_SHOWN:
            print("[oplog] disabled (no credentials/bucket configured)")
            _NOTICE_SHOWN = True
        return None, None
    try:
        import boto3
        from botocore.config import Config
    except ImportError:
        if not _NOTICE_SHOWN:
            print("[oplog] disabled (boto3 not installed)")
            _NOTICE_SHOWN = True
        return None, None
    c = boto3.client(
        "s3", endpoint_url=cfg["endpoint"], region_name=cfg["region"],
        aws_access_key_id=cfg["access"], aws_secret_access_key=cfg["secret"],
        config=Config(connect_timeout=10, read_timeout=20,
                      retries={"max_attempts": 2}))
    return c, cfg


def _run_id() -> str:
    return os.getenv("APITEST_RUN_ID") or os.getenv("GITHUB_RUN_ID") or "local"


def _put(c, cfg, key, payload: dict) -> bool:
    try:
        c.put_object(Bucket=cfg["bucket"], Key=key,
                     Body=json.dumps(payload, ensure_ascii=False).encode(),
                     ContentType="application/json")
        return True
    except Exception as exc:
        print(f"[oplog] put {key} failed: {exc}")
        return False


def ensure_bucket() -> bool:
    """Create the bucket if missing; apply CORS + public-read so the static
    ops viewer (GitHub Pages) can fetch/list it from the browser. Each step is
    independent best-effort (SCP may reject some ACL/CORS shapes)."""
    c, cfg = _client()
    if not c:
        return False
    try:
        c.head_bucket(Bucket=cfg["bucket"])
        print(f"[oplog] bucket {cfg['bucket']} exists")
    except Exception:
        try:
            c.create_bucket(Bucket=cfg["bucket"])
            print(f"[oplog] bucket {cfg['bucket']} created (PERSISTENT — never swept)")
        except Exception as exc:
            print(f"[oplog] create_bucket failed: {exc}")
            return False
    try:
        c.put_bucket_cors(Bucket=cfg["bucket"], CORSConfiguration={
            "CORSRules": [{"AllowedMethods": ["GET", "HEAD"],
                           "AllowedOrigins": ["*"],
                           "AllowedHeaders": ["*"], "MaxAgeSeconds": 300}]})
    except Exception as exc:
        print(f"[oplog] put_bucket_cors failed (viewer may need a proxy): {exc}")
    try:
        c.put_bucket_acl(Bucket=cfg["bucket"], ACL="public-read")
    except Exception as exc:
        print(f"[oplog] put_bucket_acl public-read failed (viewer reads may 403): {exc}")
    return True


def emit(stage: str, status: str, detail: str = "", job: str = "") -> bool:
    """Write one milestone event (unique key — race-free across jobs)."""
    c, cfg = _client()
    if not c:
        return False
    rid = _run_id()
    now_ms = int(time.time() * 1000)
    job = job or os.getenv("GITHUB_JOB", "")
    ev = {"ts": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
          "run_id": rid, "job": job, "stage": stage, "status": status,
          "detail": detail[:2000]}
    ok = _put(c, cfg, f"runs/{rid}/events/{now_ms}-{stage}.json", ev)
    # first emit of a run also drops the manifest (idempotent overwrite)
    manifest = {"run_id": rid,
                "sha": os.getenv("GITHUB_SHA", "")[:7],
                "branch": os.getenv("GITHUB_REF_NAME", ""),
                "event": os.getenv("GITHUB_EVENT_NAME", ""),
                "url": (f"{os.getenv('GITHUB_SERVER_URL', 'https://github.com')}/"
                        f"{os.getenv('GITHUB_REPOSITORY', '')}/actions/runs/{rid}"),
                "started": ev["ts"]}
    if stage == "run-start":
        _put(c, cfg, f"runs/{rid}/run.json", manifest)
    return ok


def finalize(history_path: str = "dashboard/history.jsonl") -> bool:
    """Called once by the dashboard job (single writer): write summary.json for
    this run and fold it into the newest-first index.json (kept ≤ 200 rows)."""
    c, cfg = _client()
    if not c:
        return False
    rid = _run_id()
    row = {}
    try:
        with open(history_path) as fh:
            lines = [l for l in fh if l.strip()]
        if lines:
            row = json.loads(lines[-1])
    except (OSError, ValueError):
        pass
    summary = {"run_id": rid, "sha": os.getenv("GITHUB_SHA", "")[:7],
               "finished": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
               "history": row}
    _put(c, cfg, f"runs/{rid}/summary.json", summary)
    index = []
    try:
        obj = c.get_object(Bucket=cfg["bucket"], Key="index.json")
        index = json.loads(obj["Body"].read())
        if not isinstance(index, list):
            index = []
    except Exception:
        pass
    index = [summary] + [r for r in index if r.get("run_id") != rid]
    return _put(c, cfg, "index.json", index[:200])


def main(argv=None) -> int:
    import argparse
    ap = argparse.ArgumentParser(description="workflow oplog -> object storage")
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("ensure")
    em = sub.add_parser("emit")
    em.add_argument("--stage", required=True)
    em.add_argument("--status", required=True)
    em.add_argument("--detail", default="")
    em.add_argument("--job", default="")
    fin = sub.add_parser("finalize")
    fin.add_argument("--history", default="dashboard/history.jsonl")
    a = ap.parse_args(argv)
    if a.cmd == "ensure":
        ensure_bucket()
    elif a.cmd == "emit":
        emit(a.stage, a.status, a.detail, a.job)
    elif a.cmd == "finalize":
        finalize(a.history)
    return 0  # never fail the calling step


if __name__ == "__main__":
    sys.exit(main())
