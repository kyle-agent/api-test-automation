#!/usr/bin/env python3
"""Catalog validation-status rollup (VERIFICATION track).

Reproduces the numbers in ``docs/CATALOG-VALIDATION-STATUS.md``:

  * per-node provenance totals (VALIDATED vs docs vs other)
  * per-SERVICE 3-way split (full / partial / zero validated)
  * the zero-validated service list with a best-effort BLOCKER guess
    mined from each node's notes / _disabled_reason / comments
  * composition adoption: composed gen-*/bundle-* vs hand-written lifecycles

Read-only. No mutations, no network. Run from repo root:

    python -m tools.catalog_status          # human summary
    python -m tools.catalog_status --json    # machine rollup
"""
from __future__ import annotations

import json
import re
import sys
from collections import defaultdict
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
RES_DIR = REPO / "knowledge" / "formal" / "resources"
LC_DIR = REPO / "regression" / "scenarios" / "lifecycles"

# Blocker keyword heuristics applied to each zero-validated service's pooled
# notes / disabled-reason / comment text. First match wins (ordered).
BLOCKER_RULES = [
    ("license-gated", r"licen[cs]e|라이선스|구독|subscript|entitle|미신청|신청\s*필요|enable.{0,12}console|콘솔에서\s*활성"),
    ("heavy-billable", r"heavy|billable|과금|비용|유료|장시간|long[- ]running|expensive|quota.{0,8}exhaust|쿼터\s*소진"),
    ("console-only-id", r"console-only|콘솔[에서]*만|console.{0,12}provision|선행\s*생성|pre[- ]?provision|사전\s*발급|콘솔\s*발급"),
    ("unproven-body", r"unproven|UNPROVEN|미검증|추정|guess|spec[- ]only|문서만|docs[- ]only|body\s*미확인|400|422"),
    ("owner-action-needed", r"owner|소유자|운영자\s*조치|manual\s*step|수동|account\s*setup|권한\s*필요|승인"),
]


def load_nodes() -> dict:
    import yaml
    nodes = {}
    for path in sorted(RES_DIR.glob("*.yaml")):
        if path.name.startswith("_"):
            continue
        raw = path.read_text()
        data = yaml.safe_load(raw) or {}
        for nid, task in (data.get("resources") or {}).items():
            task = task or {}
            task["_file"] = path.name
            task["_raw"] = raw  # for blocker mining
            nodes[nid] = task
    return nodes


def text_blob(task: dict) -> str:
    parts = []
    for k in ("notes", "_disabled_reason"):
        v = task.get(k)
        if v:
            parts.append(str(v))
    # captured comments + nested notes via a json dump of the task minus _raw
    dump = {k: v for k, v in task.items() if k not in ("_raw",)}
    parts.append(json.dumps(dump, ensure_ascii=False))
    return "\n".join(parts)


def classify_blocker(blob: str) -> str:
    for label, pat in BLOCKER_RULES:
        if re.search(pat, blob, re.IGNORECASE):
            return label
    return "unclassified"


def rollup() -> dict:
    nodes = load_nodes()
    prov_tot = defaultdict(int)
    svc_prov = defaultdict(lambda: defaultdict(int))
    svc_blob = defaultdict(list)
    for nid, task in nodes.items():
        prov = task.get("provenance", "?")
        prov_tot[prov] += 1
        svc = task.get("service", "?")
        svc_prov[svc][prov] += 1
        svc_blob[svc].append(text_blob(task))

    full, partial, zero = [], [], []
    for svc, counts in svc_prov.items():
        total = sum(counts.values())
        val = counts.get("VALIDATED", 0)
        if val == total:
            full.append(svc)
        elif val == 0:
            zero.append(svc)
        else:
            partial.append(svc)

    zero_detail = []
    for svc in sorted(zero):
        blob = "\n".join(svc_blob[svc])
        zero_detail.append({
            "service": svc,
            "nodes": sum(svc_prov[svc].values()),
            "blocker": classify_blocker(blob),
        })

    return {
        "nodes_total": len(nodes),
        "provenance_totals": dict(prov_tot),
        "services_total": len(svc_prov),
        "service_split": {
            "full": sorted(full),
            "partial": sorted(partial),
            "zero": sorted(zero),
        },
        "zero_detail": zero_detail,
    }


def lifecycle_adoption() -> dict:
    composed, handwritten = [], []
    for path in sorted(LC_DIR.glob("*.json")):
        try:
            data = json.loads(path.read_text())
        except Exception:
            continue
        if isinstance(data, dict) and "lifecycles" in data:
            ids = data["lifecycles"]
        elif isinstance(data, list):
            ids = data
        else:
            ids = [data]
        for lc in ids:
            lid = lc.get("id") if isinstance(lc, dict) else None
            if not lid:
                continue
            if lid.startswith("gen-") or lid.startswith("bundle-"):
                composed.append(lid)
            else:
                handwritten.append(lid)
    return {
        "files": len(list(LC_DIR.glob("*.json"))),
        "composed": len(composed),
        "handwritten": len(handwritten),
        "total": len(composed) + len(handwritten),
        "composed_ids": sorted(composed),
    }


def main() -> None:
    r = rollup()
    lc = lifecycle_adoption()
    out = {"rollup": r, "lifecycles": lc}
    if "--json" in sys.argv:
        print(json.dumps(out, ensure_ascii=False, indent=2))
        return
    pt = r["provenance_totals"]
    print(f"nodes={r['nodes_total']}  provenance={pt}")
    sp = r["service_split"]
    print(f"services={r['services_total']}  "
          f"full={len(sp['full'])} partial={len(sp['partial'])} zero={len(sp['zero'])}")
    print("zero-validated services + blocker guess:")
    for z in r["zero_detail"]:
        print(f"  - {z['service']:42s} nodes={z['nodes']:<2} {z['blocker']}")
    print(f"lifecycles: composed={lc['composed']} handwritten={lc['handwritten']} "
          f"total={lc['total']} (files={lc['files']})")


if __name__ == "__main__":
    main()
