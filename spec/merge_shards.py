#!/usr/bin/env python3
"""Merge per-category shard files (data/shards/*.json) — plus any pre-shard
data/api_docs.json — into a single data/api_docs.json.

"""
from __future__ import annotations

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SHARDS = ROOT / "data" / "shards"
OUT = ROOT / "data" / "api_docs.json"


def merge() -> dict:
    """Merge all shard files (and any existing api_docs.json) into one store."""
    merged = {"endpoints": {}, "models": {}}
    sources = sorted(SHARDS.glob("*.json")) if SHARDS.exists() else []
    if OUT.exists():
        sources = [OUT] + sources
    for f in sources:
        try:
            d = json.loads(f.read_text())
        except Exception as exc:
            print(f"  skip {f.name}: {exc}")
            continue
        for b in ("endpoints", "models"):
            merged[b].update(d.get(b, {}))
    OUT.write_text(json.dumps(merged, indent=2, ensure_ascii=False))
    print(f"merged {len(sources)} files -> {len(merged['endpoints'])} endpoints, "
          f"{len(merged['models'])} models -> {OUT}")
    return merged


def main() -> int:
    merge()
    return 0


if __name__ == "__main__":
    import sys
    sys.exit(main())
