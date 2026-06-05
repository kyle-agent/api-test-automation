"""Unified results store — one schema both axes write and the dashboard reads.

Today the signals are scattered: regression -> reports/smoke_status.tsv,
conformance -> framework/conformance.json, runtime -> reports/runtime_*.json.
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
OBSERVATIONS = RESULTS_DIR / "observations.jsonl"
FINDINGS = RESULTS_DIR / "findings.jsonl"

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
    _append(OBSERVATIONS, obs)


def record_finding(f: Finding) -> None:
    _append(FINDINGS, f)


def load_observations(path: str | os.PathLike | None = None) -> list[dict]:
    return _read(Path(path or OBSERVATIONS))


def load_findings(path: str | os.PathLike | None = None) -> list[dict]:
    return _read(Path(path or FINDINGS))


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
