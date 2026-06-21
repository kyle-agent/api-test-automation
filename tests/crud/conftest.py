"""CRUD collection: exact-id allowlist + longest-lifecycle-FIRST ordering (xdist).

Two collection-time behaviours, both keyed off the parametrized ``lifecycle`` case:

1. ``SCP_CRUD_IDS`` (comma-separated EXACT lifecycle ids) — deselect every lifecycle
   not in the set. The platform console emits a precise id list (node -> source.lifecycle)
   so "select services in the UI -> run exactly those" works without a ``-k`` expression
   (hyphenated ids like ``database-mysql-cluster`` parse as subtraction under ``-k``).

2. Longest-first ordering — the dag dynamic dispatcher dispatches longest-job-first so
   the big DB/K8s/VM clusters start at t=0. The pytest path (api-test.yml's xdist run AND
   chat-heavy) had NO such ordering: pytest collects in declaration order, so under ``-n``
   the heavy clusters started late while light lifecycles churned first. Sorting the
   lifecycle cases by recorded wall time (``data/optimizer/durations.json`` -> ``avg_s``)
   descending makes longest-first FUNDAMENTAL for the pytest path too.

Only ``lifecycle``-parametrized items are touched; any other collected tests keep their
position and are never deselected.
"""
import json
import os
from pathlib import Path

_DUR_PATH = Path(__file__).resolve().parents[2] / "data" / "optimizer" / "durations.json"


def _durations() -> dict:
    try:
        raw = json.loads(_DUR_PATH.read_text())
        return {k: float(v.get("avg_s") or 0.0) for k, v in raw.items()}
    except Exception:  # noqa: BLE001 — ordering is best-effort; never break collection
        return {}


def _lifecycle_id(item) -> str | None:
    spec = getattr(item, "callspec", None)
    lc = spec.params.get("lifecycle") if spec else None
    return lc.get("id") if isinstance(lc, dict) else None


def pytest_collection_modifyitems(config, items) -> None:
    # 1) exact-id allowlist — deselect lifecycle cases not in SCP_CRUD_IDS (non-lifecycle
    #    tests are always kept). Precise selection from the console, no -k parsing.
    only = {x.strip() for x in os.getenv("SCP_CRUD_IDS", "").split(",") if x.strip()}
    if only:
        keep, drop = [], []
        for it in items:
            lid = _lifecycle_id(it)
            (drop if (lid is not None and lid not in only) else keep).append(it)
        if drop:
            config.hook.pytest_deselected(items=drop)
            items[:] = keep

    # 2) longest-first ordering of the surviving lifecycle items (in their slots)
    dur = _durations()
    if not dur:
        return
    slots = [i for i, it in enumerate(items) if _lifecycle_id(it)]
    if len(slots) < 2:
        return
    ordered = sorted((items[i] for i in slots),
                     key=lambda it: dur.get(_lifecycle_id(it), 0.0), reverse=True)
    for slot, it in zip(slots, ordered):
        items[slot] = it
