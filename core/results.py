"""Unified results store — one schema both axes write and the dashboard reads.

Today the signals are scattered: regression -> reports/smoke_status.tsv,
conformance -> data/conformance.json, runtime -> reports/runtime_*.json.
This module is the single home so the dashboard reads ONE place and both axes are
first-class:

  * ``Observation`` — an endpoint was *called* (regression smoke / read-chain /
    CRUD probe). Carries status + category + **elapsed_ms** (response time).
  * ``Finding`` — a design/behavior *defect* (conformance static or runtime).

Both are appended as JSONL under ``reports/results/`` (one line per record).
Writers are append-only and failure-tolerant so recording never breaks a run.
"""
from __future__ import annotations

import json
import os
import time
from dataclasses import dataclass, asdict, field
from pathlib import Path

RESULTS_DIR = Path(os.environ.get("APITEST_RESULTS_DIR", "reports/results"))


def _worker_suffix() -> str:
    """Per-xdist-worker suffix (e.g. '-gw0') so parallel workers append to their
    OWN shard instead of racing on one file. Empty when not under xdist."""
    w = os.environ.get("PYTEST_XDIST_WORKER", "")
    return f"-{w}" if w else ""


# Canonical files the dashboard reads; under xdist each worker writes its own
# shard (observations-gw0.jsonl) which `merge_worker_shards()` concatenates into
# the canonical file (and removes) so the dashboard never double-counts.
OBSERVATIONS = RESULTS_DIR / "observations.jsonl"
FINDINGS = RESULTS_DIR / "findings.jsonl"


def _observations_path() -> Path:
    return RESULTS_DIR / f"observations{_worker_suffix()}.jsonl"


def _findings_path() -> Path:
    return RESULTS_DIR / f"findings{_worker_suffix()}.jsonl"

# axis / kind tags
REGRESSION = "regression"     # smoke + read-chain + CRUD probe reads
CONFORMANCE = "conformance"   # design/behavior defects

# observation categories (the ok/soft/fail split)
OK, SOFT, FAIL = "ok", "soft", "fail"


@dataclass
class Observation:
    endpoint_key: str
    method: str
    path: str
    status: int
    category: str               # ok | soft | fail
    elapsed_ms: float | None = None
    source: str = "smoke"       # smoke | read_chain | crud_probe
    note: str = ""
    run: str = field(default_factory=lambda: os.environ.get("GITHUB_RUN_ID", ""))
    ts: float = field(default_factory=time.time)


@dataclass
class Finding:
    endpoint_key: str
    rule_id: str                # e.g. "naming.snake_case", "status.wrong_code"
    severity: str               # red | yellow | green (info)
    detail: str
    source: str = "static"      # static | runtime
    issue: str = ""             # external tracker id if any
    run: str = field(default_factory=lambda: os.environ.get("GITHUB_RUN_ID", ""))
    ts: float = field(default_factory=time.time)


def _append(path: Path, rec) -> None:
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "a") as fh:
            fh.write(json.dumps(asdict(rec)) + "\n")
    except OSError:
        pass


def record(obs: Observation) -> None:
    _append(_observations_path(), obs)


def record_finding(f: Finding) -> None:
    _append(_findings_path(), f)


def _shards(canonical: Path) -> list[Path]:
    """Per-worker shards for a canonical file: ``foo.jsonl`` -> ``foo-*.jsonl``."""
    return sorted(canonical.parent.glob(f"{canonical.stem}-*{canonical.suffix}"))


def load_observations(path: str | os.PathLike | None = None) -> list[dict]:
    return _read_with_shards(Path(path or OBSERVATIONS))


def load_findings(path: str | os.PathLike | None = None) -> list[dict]:
    return _read_with_shards(Path(path or FINDINGS))


def _read_with_shards(canonical: Path) -> list[dict]:
    """Read the canonical file PLUS any per-worker shards (so a loader sees a
    complete picture even before an explicit merge)."""
    out = _read(canonical)
    for shard in _shards(canonical):
        out.extend(_read(shard))
    return out


def merge_worker_shards(canonical: Path | None = None) -> int:
    """Concatenate per-worker shards (observations-gw0.jsonl, ...) into the
    canonical file and DELETE the shards, so the dashboard reads one file and
    never double-counts. Idempotent; safe when there are no shards. Returns the
    number of shard files merged. Merges BOTH observations + findings when called
    with no argument."""
    if canonical is None:
        return sum(merge_worker_shards(p) for p in (OBSERVATIONS, FINDINGS))
    merged = 0
    shards = _shards(canonical)
    if not shards:
        return 0
    try:
        canonical.parent.mkdir(parents=True, exist_ok=True)
        with open(canonical, "a") as out:
            for shard in shards:
                try:
                    out.write(shard.read_text())
                except OSError:
                    continue
                merged += 1
        for shard in shards:
            try:
                shard.unlink()
            except OSError:
                pass
    except OSError:
        pass
    return merged


def _read(path: Path) -> list[dict]:
    if not path.exists():
        return []
    out = []
    for ln in path.read_text().splitlines():
        ln = ln.strip()
        if ln:
            try:
                out.append(json.loads(ln))
            except ValueError:
                pass
    return out
