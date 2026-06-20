"""dag_diff — ADR 1.0-d: validate the DAG runner against legacy pytest-xdist.

Before retiring xdist (1.0-d) we run BOTH for the same leaf set and prove they
agree on pass/skip/fail per lifecycle. The xdist side emits a JUnit XML
(``pytest --junitxml=``) with one ``<testcase name="test_crud_lifecycle[<id>]">``
per lifecycle; the DAG side produces a ``dag_runner.RunResult``. This module maps
both to ``lifecycle_id -> status`` ('passed'|'skipped'|'failed') and diffs them.

A disagreement (e.g. xdist 'passed' but dagrun 'failed') is the signal that the
runner is NOT yet a faithful replacement — that is the set to investigate. An id
present on only one side means the two runs did not attempt the same leaf set.

Offline + stdlib only (xml.etree, json, dataclasses) — no client, no network,
no credentials. CLI::

    python -m regression.scenarios.dag_diff --junit run.xml --runresult run.json

exits 0 iff the runs agree on every attempted lifecycle (``Diff.ok``), else 1.
"""
from __future__ import annotations

import json
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field

# Statuses we normalise both sides to. 'planned' (dry_run) is NOT a real outcome
# and is intentionally excluded from runresult_status — a diff only compares runs
# that actually executed.
_VALID = {"passed", "skipped", "failed"}
_TESTNAME = "test_crud_lifecycle"


def _lifecycle_id(name: str) -> str | None:
    """Extract the id from ``test_crud_lifecycle[<id>]``; None if not parametrised
    or not the crud-lifecycle test."""
    if "[" not in name or not name.endswith("]"):
        return None
    base, _, rest = name.partition("[")
    # base may be 'test_crud_lifecycle' or 'TestClass::test_crud_lifecycle';
    # match the function name on the tail of the (possibly dotted) base.
    func = base.rsplit("::", 1)[-1].rsplit(".", 1)[-1]
    if func != _TESTNAME:
        return None
    return rest[:-1]  # strip trailing ']'


def parse_junit(path: str) -> dict[str, str]:
    """Map lifecycle_id -> status from a pytest JUnit XML.

    A ``<testcase>`` carrying ``<failure>`` or ``<error>`` is 'failed', one with
    ``<skipped>`` is 'skipped', otherwise 'passed'. Only ``test_crud_lifecycle``
    cases are considered; the id is the part inside the ``[...]`` of the name.
    """
    tree = ET.parse(path)
    root = tree.getroot()
    out: dict[str, str] = {}
    for tc in root.iter("testcase"):
        lid = _lifecycle_id(tc.get("name", ""))
        if lid is None:
            continue
        if tc.find("failure") is not None or tc.find("error") is not None:
            status = "failed"
        elif tc.find("skipped") is not None:
            status = "skipped"
        else:
            status = "passed"
        out[lid] = status
    return out


def runresult_status(result) -> dict[str, str]:
    """Map lifecycle_id -> status from a dag_runner ``RunResult`` (via ``.outcomes``).

    Only real outcomes ('passed'|'skipped'|'failed') are included; a 'planned'
    outcome (from ``dry_run``) is skipped since it represents an un-executed run.
    """
    out: dict[str, str] = {}
    for o in result.outcomes:
        if o.status in _VALID:
            out[o.lifecycle_id] = o.status
    return out


@dataclass
class Diff:
    """Per-lifecycle comparison of the two runs.

    - ``agree``: ids both sides report with the SAME status (incl. skipped==skipped).
    - ``disagree``: id -> (xdist_status, dagrun_status) where they differ — the set
      that matters (e.g. xdist 'passed' vs dagrun 'failed' = a regression; vs
      'skipped' = a new skip).
    - ``only_xdist`` / ``only_dagrun``: ids missing from the other side (the runs
      did not attempt the same leaf set).
    """
    agree: dict[str, str] = field(default_factory=dict)
    disagree: dict[str, tuple[str, str]] = field(default_factory=dict)
    only_xdist: dict[str, str] = field(default_factory=dict)
    only_dagrun: dict[str, str] = field(default_factory=dict)

    @property
    def ok(self) -> bool:
        """True iff no disagreements and no id missing on either side — i.e. the
        runs attempted the same leaf set and agreed on every outcome."""
        return not self.disagree and not self.only_xdist and not self.only_dagrun


def diff(xdist: dict[str, str], dagrun: dict[str, str]) -> Diff:
    """Compare the two ``lifecycle_id -> status`` maps."""
    d = Diff()
    for lid in xdist.keys() - dagrun.keys():
        d.only_xdist[lid] = xdist[lid]
    for lid in dagrun.keys() - xdist.keys():
        d.only_dagrun[lid] = dagrun[lid]
    for lid in xdist.keys() & dagrun.keys():
        xs, ds = xdist[lid], dagrun[lid]
        if xs == ds:
            d.agree[lid] = xs
        else:
            d.disagree[lid] = (xs, ds)
    return d


def format_diff(d: Diff) -> str:
    L: list[str] = []
    L.append(f"agree:       {len(d.agree)}")
    L.append(f"disagree:    {len(d.disagree)}")
    L.append(f"only xdist:  {len(d.only_xdist)}")
    L.append(f"only dagrun: {len(d.only_dagrun)}")
    if d.disagree:
        L.append("")
        L.append("DISAGREEMENTS (xdist -> dagrun):")
        for lid, (xs, ds) in sorted(d.disagree.items()):
            L.append(f"  ✗ {lid}: {xs} -> {ds}")
    if d.only_xdist:
        L.append("")
        L.append("ONLY in xdist (dagrun did not attempt):")
        for lid, s in sorted(d.only_xdist.items()):
            L.append(f"  ← {lid} [{s}]")
    if d.only_dagrun:
        L.append("")
        L.append("ONLY in dagrun (xdist did not attempt):")
        for lid, s in sorted(d.only_dagrun.items()):
            L.append(f"  → {lid} [{s}]")
    L.append("")
    L.append(f"result: {'OK — runs agree' if d.ok else 'MISMATCH — investigate above'}")
    return "\n".join(L)


# --------------------------------------------------------------------------- #
# RunResult JSON: a minimal, stable mirror of RunResult for the CLI. Schema:
#   {"waves": [{"kind": <str>, "outcomes": [{"lifecycle_id": <str>,
#                                            "status": <str>}, ...]}, ...]}
# Only the fields runresult_status reads (per-outcome id + status) are required;
# extra keys are ignored. Produce one with dump_runresult(result).
# --------------------------------------------------------------------------- #
def load_runresult_json(path: str):
    """Load a JSON-dumped RunResult into a real ``dag_runner.RunResult`` so it can
    be fed to ``runresult_status`` (or any RunResult consumer)."""
    from regression.scenarios.dag_runner import (
        LifecycleOutcome,
        RunResult,
        WaveResult,
    )

    with open(path, encoding="utf-8") as fh:
        data = json.load(fh)
    waves = []
    for w in data.get("waves", []):
        outs = [
            LifecycleOutcome(o["lifecycle_id"], o["status"])
            for o in w.get("outcomes", [])
        ]
        waves.append(WaveResult(kind=w.get("kind", ""), outcomes=outs))
    return RunResult(waves=waves, shared_roots=list(data.get("shared_roots", [])))


def dump_runresult(result) -> dict:
    """Serialise a RunResult to the JSON schema above (round-trips load_*)."""
    return {
        "shared_roots": list(result.shared_roots),
        "waves": [
            {
                "kind": w.kind,
                "outcomes": [
                    {"lifecycle_id": o.lifecycle_id, "status": o.status}
                    for o in w.outcomes
                ],
            }
            for w in result.waves
        ],
    }


def main(argv: list[str] | None = None) -> int:
    import argparse

    ap = argparse.ArgumentParser(
        description="Diff a pytest-xdist JUnit XML against a dag_runner RunResult "
                    "(JSON) for the same leaf set — ADR 1.0-d cutover gate.")
    ap.add_argument("--junit", required=True, help="path to the xdist pytest --junitxml output")
    ap.add_argument("--runresult", required=True, help="path to a JSON-dumped dag_runner RunResult")
    args = ap.parse_args(argv)

    xdist = parse_junit(args.junit)
    dagrun = runresult_status(load_runresult_json(args.runresult))
    d = diff(xdist, dagrun)
    print(format_diff(d))
    return 0 if d.ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
