#!/usr/bin/env python3
"""Print a coverage summary of framework/api_catalog.json.

Shows totals by category and HTTP method, plus how many endpoints the
read-only smoke suite can exercise directly vs. those needing CRUD lifecycles.
"""
from __future__ import annotations

import sys
from collections import Counter, defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from framework.catalog import load_catalog


def main() -> int:
    cat = load_catalog()
    resolved = [e for e in cat if e.http_path]
    by_cat = defaultdict(Counter)
    methods = Counter()
    for e in resolved:
        by_cat[e.category][e.method] += 1
        methods[e.method] += 1

    print(f"Total endpoints discovered : {len(cat)}")
    print(f"Resolved (method+path)     : {len(resolved)}")
    print(f"Unresolved                 : {len(cat) - len(resolved)}\n")

    print("By HTTP method:")
    for m, n in methods.most_common():
        print(f"  {m:7} {n}")

    read_only = [e for e in resolved if e.is_read_only and not e.has_path_params]
    need_id = [e for e in resolved if e.is_read_only and e.has_path_params]
    mutating = [e for e in resolved if e.is_mutating]
    print("\nSmoke coverage:")
    print(f"  directly testable GET (no path params) : {len(read_only)}")
    print(f"  GET needing a resource id (via CRUD)    : {len(need_id)}")
    print(f"  mutating (via CRUD lifecycles)          : {len(mutating)}")

    print(f"\nBy category ({len(by_cat)}):")
    for c in sorted(by_cat):
        total = sum(by_cat[c].values())
        detail = " ".join(f"{m}:{n}" for m, n in sorted(by_cat[c].items()))
        print(f"  {c:24} {total:4}  ({detail})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
