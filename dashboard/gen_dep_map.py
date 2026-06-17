#!/usr/bin/env python3
"""Generate the kind-level dependency map embedded in ops.html.

Derives {child_kind: parent_kind} + topological depth from the resource-task
model (knowledge/formal/resources). `kind` is the first path segment after
/v1 — the same value core.oplog stamps on resource events. Parent = the
DEEPEST plain require (nearest ancestor), so the ops tree nests under the
closest dependency.

This is now consumed at **dashboard build time** (`dashboard.build` injects the
fresh map into the published reports/dashboard/ops.html between the DEP-MAP
markers), so the map can never go stale — no manual paste. Run this module
directly only for manual inspection of the computed const:

    python3 dashboard/gen_dep_map.py
"""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


def kind_of(endpoint: str) -> str:
    path = (endpoint or "").partition(" ")[2]
    segs = [s for s in path.split("?")[0].split("/") if s]
    return segs[1] if len(segs) > 1 else (segs[0] if segs else "")


def dep_map_dict() -> dict:
    """Compute the {parent, depth} dependency map from the resource model.

    Tolerant by design: a malformed/empty model (or an import failure of the
    composer) yields an empty {"parent": {}, "depth": {}} rather than raising,
    so the dashboard build never crashes on a stale/partial resource model.
    """
    try:
        from regression.scenarios import composer  # noqa: E402
        model = composer.load_model() or {}
    except Exception:
        return {"parent": {}, "depth": {}}
    if not isinstance(model, dict):
        return {"parent": {}, "depth": {}}

    kind = {}
    for nid, t in model.items():
        if not isinstance(t, dict):
            continue
        ep = ((t.get("create") or {}).get("endpoint")) or ""
        k = kind_of(ep)
        if k and (ep.startswith("POST") or ep.startswith("GET")):
            kind.setdefault(nid, k)

    def plain_refs(t):
        out = []
        for r in (t.get("requires") or []) if isinstance(t, dict) else []:
            if isinstance(r, str):
                out.append(r)
            elif isinstance(r, dict) and "ref" in r:
                out.append(r["ref"])
        return out

    depth_memo = {}

    def depth(nid, stack=()):
        if nid in depth_memo:
            return depth_memo[nid]
        if nid in stack:
            return 0
        refs = [r for r in plain_refs(model.get(nid) or {}) if r in model]
        d = 0 if not refs else 1 + max(depth(r, stack + (nid,)) for r in refs)
        depth_memo[nid] = d
        return d

    parent, kdepth = {}, {}
    for nid, t in model.items():
        k = kind.get(nid)
        if not k:
            continue
        kdepth[k] = max(kdepth.get(k, 0), depth(nid))
        refs = [r for r in plain_refs(t) if r in kind]
        if refs:
            best = max(refs, key=lambda r: depth(r))
            pk = kind[best]
            if pk != k:
                # first writer wins unless the new parent is deeper
                cur = parent.get(k)
                if cur is None or kdepth.get(pk, 0) > kdepth.get(cur, 0):
                    parent[k] = pk
    return {"parent": dict(sorted(parent.items())),
            "depth": dict(sorted(kdepth.items()))}


def dep_map_js() -> str:
    """The `const DEP={...};` JS line that ops.html embeds between markers."""
    out = dep_map_dict()
    return "const DEP=" + json.dumps(out, ensure_ascii=False, sort_keys=True) + ";"


def main() -> str:
    return dep_map_js()


if __name__ == "__main__":
    print(main())
