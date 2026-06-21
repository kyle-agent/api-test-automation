"""CRUD collection ordering — longest lifecycle FIRST (xdist-friendly).

The dag dynamic dispatcher already dispatches longest-job-first so the big
DB/K8s/VM clusters start at t=0. The pytest path (api-test.yml's xdist run AND
chat-heavy) had NO such ordering: pytest collects in declaration order, so under
``-n`` the heavy clusters started late in the distribution while light lifecycles
churned first (observed live: 10 min into a run, every live resource was light and
ZERO DB clusters had begun). This hook makes longest-first FUNDAMENTAL for the
pytest path too, sorting the parametrized lifecycle cases by recorded wall time
(``data/optimizer/durations.json`` -> ``avg_s``) descending. So whichever engine
runs — pytest/xdist or the dag dispatcher, local or CI — the heavy clusters lead.

Only the ``lifecycle``-parametrized items are reordered (in their existing slots);
any other collected tests keep their position.
"""
import json
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


def pytest_collection_modifyitems(items) -> None:
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
