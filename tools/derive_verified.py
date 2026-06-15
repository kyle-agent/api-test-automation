"""IB-041 — derive per-endpoint **2xx evidence** so docs→VALIDATED promotion is
masked-defect-safe.

A live run today cannot promote nodes: heavy/ADOPT lifecycle create steps are
soft/optional (tolerant expect, 4xx allowed), so a lifecycle "pass" does NOT
prove the create endpoint actually returned a real 2xx. The promotion rule must
therefore consult **endpoint-level evidence**, not lifecycle pass/fail.

This tool reads the unified observation store (``reports/results/observations.jsonl``,
schema in ``core/results.py``) and emits/merges
``data/baselines/verified_endpoints.json`` — the set of ``endpoint_key``s that
have **≥1 observation with a real 2xx status (200–299)**, REGARDLESS of whether
the step was soft/optional. Non-2xx observations (4xx soft "reached", 5xx fail)
are excluded — a 404'd POST created nothing, so it is never evidence.

The output is **accumulated** (union-merged) across runs: an endpoint verified
by any past run stays verified; we keep its first-seen run id and update the
last-seen run id + observation count.

Output schema (``data/baselines/verified_endpoints.json``)::

    {
      "<endpoint_key>": {
        "method":    "POST",
        "path":      "/v1/vpcs",        # raw observed path (last-seen)
        "norm_path": "v1/vpcs",         # query-stripped, id-collapsed
        "first_run": "27258520218",     # GITHUB_RUN_ID first time we saw a 2xx
        "last_run":  "27492496266",     # most recent run that saw a 2xx
        "count":     7                  # cumulative number of 2xx observations
      },
      ...
    }

The promotion consumer rule (docs→VALIDATED): promote node N → VALIDATED only if
N's CREATE endpoint_key is a key in this file. (The create endpoint is the
load-bearing step; a node whose create never returned a real 2xx has not been
proven, even if its lifecycle "passed" with soft steps.)

CLI::

    python -m tools.derive_verified \
        --observations reports/results/observations.jsonl \
        --out data/baselines/verified_endpoints.json
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

_ROOT = Path(__file__).parent.parent
DEFAULT_OBS = _ROOT / "reports" / "results" / "observations.jsonl"
DEFAULT_OUT = _ROOT / "data" / "baselines" / "verified_endpoints.json"


def norm_path(p: str) -> str:
    """Query-stripped, id-collapsed path (mirrors dashboard.build.norm_path):
    drop the query string and collapse templated/concrete id segments to '*'."""
    p = (p or "").split("?")[0].strip("/")
    return "/".join("*" if "{" in s else s for s in p.split("/"))


def _is_2xx(status) -> bool:
    try:
        return 200 <= int(status) <= 299
    except (TypeError, ValueError):
        return False


def load_observations(path: Path) -> list[dict]:
    """Read a JSONL observation file. Tolerant: missing file -> [], bad lines
    skipped. (We deliberately do NOT import core.results so the tool stays usable
    standalone, e.g. on a synthetic /tmp file in CI.)"""
    if not path.exists():
        return []
    out: list[dict] = []
    for ln in path.read_text().splitlines():
        ln = ln.strip()
        if not ln:
            continue
        try:
            out.append(json.loads(ln))
        except ValueError:
            pass
    return out


def load_existing(path: Path) -> dict:
    """Read the existing verified_endpoints.json for accumulation. Tolerant of
    absent / malformed files (returns {})."""
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text())
    except (ValueError, OSError):
        return {}
    return data if isinstance(data, dict) else {}


def derive(observations: list[dict], existing: dict | None = None) -> dict:
    """Union-merge: fold every 2xx observation into the verified set.

    For each observation with a real 2xx status:
      * new key  -> first_run = last_run = run id, count = 1
      * existing -> last_run updated, count += 1, first_run preserved
    method/path/norm_path reflect the most-recently-seen 2xx observation.
    Idempotent on the *aggregate* level only by design — re-running on the SAME
    observations re-counts them (count tracks total 2xx observations seen across
    all merged runs); the KEY SET is idempotent (the contract the promoter uses).
    """
    out: dict = {k: dict(v) for k, v in (existing or {}).items()}

    for o in observations:
        if not _is_2xx(o.get("status")):
            continue
        key = o.get("endpoint_key", "")
        if not key:
            continue
        method = (o.get("method") or "").upper()
        path = o.get("path") or ""
        run = str(o.get("run") or "")

        rec = out.get(key)
        if rec is None:
            out[key] = {
                "method": method,
                "path": path,
                "norm_path": norm_path(path),
                "first_run": run,
                "last_run": run,
                "count": 1,
            }
        else:
            rec["count"] = int(rec.get("count", 0)) + 1
            # method/path reflect the latest 2xx observation
            if method:
                rec["method"] = method
            if path:
                rec["path"] = path
                rec["norm_path"] = norm_path(path)
            if not rec.get("first_run"):
                rec["first_run"] = run
            rec["last_run"] = run
    return out


def write_out(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    # sorted keys -> stable diffs when committed / published
    path.write_text(json.dumps(data, indent=2, sort_keys=True, ensure_ascii=False) + "\n")


def run(observations_path: Path, out_path: Path) -> dict:
    """Read observations, merge into the existing out file, write it back, return
    the merged dict."""
    obs = load_observations(observations_path)
    existing = load_existing(out_path)
    before = len(existing)
    merged = derive(obs, existing)
    write_out(out_path, merged)
    added = len(merged) - before
    print(
        f"derive_verified: {len(obs)} observations from {observations_path} -> "
        f"{len(merged)} verified endpoints in {out_path} "
        f"(+{added} new; {before} carried over)"
    )
    return merged


def main(argv: list[str] | None = None) -> None:
    ap = argparse.ArgumentParser(
        description="Derive per-endpoint 2xx evidence (verified_endpoints.json) "
                    "from the unified observation store — for masked-defect-safe "
                    "docs->VALIDATED promotion (IB-041).")
    ap.add_argument("--observations", default=str(DEFAULT_OBS),
                    help="Path to observations.jsonl (unified store). "
                         f"Default: {DEFAULT_OBS}")
    ap.add_argument("--out", default=str(DEFAULT_OUT),
                    help="Path to verified_endpoints.json to emit/merge. "
                         f"Default: {DEFAULT_OUT}")
    args = ap.parse_args(argv)
    run(Path(args.observations), Path(args.out))


if __name__ == "__main__":
    main()
