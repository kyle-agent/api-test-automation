#!/usr/bin/env python3
"""Diff two catalog JSON snapshots to surface spec changes.

A spec change is an input to both regression and conformance axes; this module
lets a CI step detect which endpoints changed so only affected tests are
(re-)triggered rather than the full suite.

Each catalog JSON is a list of endpoint dicts as produced by
``spec.extract_catalog`` (same schema as ``data/api_catalog.json``).

Public API
----------
``diff_catalog(old_path, new_path) -> dict``
    Load two catalog files and return a structured change report.

``__main__``
    Run as ``python -m spec.diff <old.json> <new.json>`` for a human-readable
    summary.

Dependencies: stdlib ``json`` only (no third-party packages).
"""
from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _load(path: str | Path) -> dict[str, dict]:
    """Load a catalog JSON file and index entries by their ``key`` field."""
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    return {e["key"]: e for e in data}


def _endpoint_sig(entry: dict) -> tuple[str | None, str | None]:
    """Return the (method, http_path) signature used to detect changes."""
    return (entry.get("method"), entry.get("http_path"))


def _changed_fields(old: dict, new: dict) -> dict[str, dict[str, Any]]:
    """Return a mapping of field -> {old, new} for fields that differ.

    Only semantic fields are compared: ``method``, ``http_path``, ``title``,
    ``version``, ``doc_url``.  Transient fields (``error``, ``doc_path``) are
    intentionally excluded.
    """
    TRACKED = ("method", "http_path", "title", "version", "doc_url")
    diff: dict[str, dict[str, Any]] = {}
    for field in TRACKED:
        o, n = old.get(field), new.get(field)
        if o != n:
            diff[field] = {"old": o, "new": n}
    return diff


# ---------------------------------------------------------------------------
# Public interface
# ---------------------------------------------------------------------------

def diff_catalog(old_path: str | Path, new_path: str | Path) -> dict:
    """Compare two catalog JSON files and return a structured change report.

    Parameters
    ----------
    old_path:
        Path to the *previous* catalog JSON (baseline).
    new_path:
        Path to the *current* catalog JSON (new snapshot).

    Returns
    -------
    A dict with the following keys:

    ``added`` : list[dict]
        Endpoints present in *new* but not in *old*.  Each item contains the
        full new entry plus a convenience ``"sig"`` key
        ``"<METHOD> <http_path>"`` (or ``"UNRESOLVED"`` if method/path are
        absent).

    ``removed`` : list[dict]
        Endpoints present in *old* but not in *new*.  Same shape as *added*.

    ``changed`` : list[dict]
        Endpoints present in both but with at least one tracked field
        different.  Each item has ``"key"``, ``"sig_old"``, ``"sig_new"``, and
        ``"fields"`` (the per-field ``{old, new}`` mapping).

    ``unchanged_count`` : int
        Number of endpoints that exist in both files with no tracked changes.

    ``summary`` : dict
        Convenience counts: ``added``, ``removed``, ``changed``,
        ``unchanged``, ``total_old``, ``total_new``.
    """
    old = _load(old_path)
    new = _load(new_path)

    old_keys = set(old)
    new_keys = set(new)

    def _sig_str(entry: dict) -> str:
        m, p = _endpoint_sig(entry)
        if m and p:
            return f"{m.upper()} {p}"
        return "UNRESOLVED"

    added = []
    for k in sorted(new_keys - old_keys):
        e = dict(new[k])
        e["sig"] = _sig_str(new[k])
        added.append(e)

    removed = []
    for k in sorted(old_keys - new_keys):
        e = dict(old[k])
        e["sig"] = _sig_str(old[k])
        removed.append(e)

    changed = []
    unchanged_count = 0
    for k in sorted(old_keys & new_keys):
        fields = _changed_fields(old[k], new[k])
        if fields:
            changed.append({
                "key": k,
                "sig_old": _sig_str(old[k]),
                "sig_new": _sig_str(new[k]),
                "fields": fields,
            })
        else:
            unchanged_count += 1

    return {
        "added": added,
        "removed": removed,
        "changed": changed,
        "unchanged_count": unchanged_count,
        "summary": {
            "added": len(added),
            "removed": len(removed),
            "changed": len(changed),
            "unchanged": unchanged_count,
            "total_old": len(old),
            "total_new": len(new),
        },
    }


# ---------------------------------------------------------------------------
# Human-readable printer
# ---------------------------------------------------------------------------

def print_diff(report: dict, *, verbose: bool = False) -> None:
    """Print a human-readable summary of a ``diff_catalog`` report."""
    s = report["summary"]
    print(
        f"Catalog diff: "
        f"+{s['added']} added, "
        f"-{s['removed']} removed, "
        f"~{s['changed']} changed, "
        f"{s['unchanged']} unchanged  "
        f"(old={s['total_old']}, new={s['total_new']})"
    )

    if report["added"]:
        print(f"\nAdded ({s['added']}):")
        for e in report["added"]:
            print(f"  + {e['key']}  [{e['sig']}]")

    if report["removed"]:
        print(f"\nRemoved ({s['removed']}):")
        for e in report["removed"]:
            print(f"  - {e['key']}  [{e['sig']}]")

    if report["changed"]:
        print(f"\nChanged ({s['changed']}):")
        for c in report["changed"]:
            sig_line = (
                f"  ~ {c['key']}"
                if c["sig_old"] == c["sig_new"]
                else f"  ~ {c['key']}  [{c['sig_old']} -> {c['sig_new']}]"
            )
            print(sig_line)
            if verbose:
                for field, diff in c["fields"].items():
                    print(f"      {field}: {diff['old']!r} -> {diff['new']!r}")


# ---------------------------------------------------------------------------
# Change-mark persistence — "변경분 표시"의 단일 출처 (owner 2026-07-14)
# ---------------------------------------------------------------------------
# A spec change lands, the diff is computed ONCE, and every consumer (dashboard
# badge, post-run change report, humans) reads the SAME committed file instead
# of each re-deriving the diff. Compact by design: the dashboard only needs
# key -> added|changed marks; the report/humans get sigs + changed fields.

MARK_PATH = Path(__file__).resolve().parent.parent / "data" / "spec_diff_latest.json"


def mark_payload(report: dict, *, old_label: str, new_label: str) -> dict:
    """Compact, consumer-facing shape of a ``diff_catalog`` report."""
    import time
    return {
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        "old": old_label,
        "new": new_label,
        "summary": report["summary"],
        # key -> mark, the dashboard's O(1) badge lookup
        "marks": {
            **{e["key"]: "added" for e in report["added"]},
            **{c["key"]: "changed" for c in report["changed"]},
        },
        "added": [{"key": e["key"], "sig": e["sig"]} for e in report["added"]],
        "removed": [{"key": e["key"], "sig": e["sig"]} for e in report["removed"]],
        "changed": [{"key": c["key"], "sig_old": c["sig_old"],
                     "sig_new": c["sig_new"], "fields": c["fields"]}
                    for c in report["changed"]],
    }


def write_marks(report: dict, *, old_label: str, new_label: str,
                path: str | Path = MARK_PATH) -> Path:
    """Persist the compact diff to ``data/spec_diff_latest.json`` (committed)."""
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(mark_payload(report, old_label=old_label,
                                         new_label=new_label),
                            indent=2, ensure_ascii=False) + "\n",
                 encoding="utf-8")
    return p


# ---------------------------------------------------------------------------
# __main__
# ---------------------------------------------------------------------------

def main(argv: list[str] | None = None) -> int:
    import argparse

    ap = argparse.ArgumentParser(
        description="Diff two api_catalog.json snapshots.",
        epilog="Exit code: 0 = no changes, 1 = changes detected, 2 = error.",
    )
    ap.add_argument("old", help="path to the baseline catalog JSON")
    ap.add_argument("new", help="path to the new catalog JSON")
    ap.add_argument(
        "--json", dest="as_json", action="store_true",
        help="emit the raw diff dict as JSON instead of human text",
    )
    ap.add_argument(
        "-v", "--verbose", action="store_true",
        help="show per-field details for changed endpoints",
    )
    ap.add_argument(
        "--mark", nargs="?", const=str(MARK_PATH), default=None, metavar="PATH",
        help="persist the compact diff to PATH (default data/spec_diff_latest.json)"
             " — the dashboard badge + spec.change_report read this file",
    )
    args = ap.parse_args(argv)

    try:
        report = diff_catalog(args.old, args.new)
    except Exception as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2

    if args.as_json:
        print(json.dumps(report, indent=2, ensure_ascii=False))
    else:
        print_diff(report, verbose=args.verbose)

    if args.mark:
        out = write_marks(report, old_label=str(args.old),
                          new_label=str(args.new), path=args.mark)
        print(f"\nmarks -> {out}")

    s = report["summary"]
    has_changes = s["added"] or s["removed"] or s["changed"]
    return 1 if has_changes else 0


if __name__ == "__main__":
    sys.exit(main())
