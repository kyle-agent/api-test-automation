"""Single source of truth for loading CRUD lifecycles.

Lifecycles now live in TWO places, merged here so every consumer (the engine,
the dashboard coverage computation, the gap analyzer) sees the same set:

  1. ``scenarios.json``            — the original/base set (kept as-is).
  2. ``lifecycles/*.json``         — per-service fragment files, ONE service per
                                     file (``<category>__<service>.json``).

The fragment split exists so the multi-agent campaign can run in parallel: each
service agent owns exactly one fragment file and never touches the shared 230KB
``scenarios.json`` or another agent's file, so there are no merge collisions.

Each fragment has the same shape as ``scenarios.json``::

    {"lifecycles": [ { "id": ..., "enabled": ..., "steps": [...] }, ... ]}

Lifecycle ``id`` must be globally unique across base + all fragments; a
duplicate id is a hard error (it would silently shadow another agent's work).
"""
from __future__ import annotations

import json
from pathlib import Path

_HERE = Path(__file__).parent
SCENARIOS_PATH = _HERE / "scenarios.json"
FRAGMENTS_DIR = _HERE / "lifecycles"


def _read_lifecycles(path: Path) -> list[dict]:
    data = json.loads(path.read_text())
    if isinstance(data, list):              # tolerate a bare list fragment
        return data
    return data.get("lifecycles", [])


def load_lifecycles(*, with_sources: bool = False):
    """Return the merged lifecycle list (base scenarios.json + every fragment).

    ``with_sources=True`` returns ``(lifecycles, {id: source_filename})`` so
    callers can report where a lifecycle came from. Raises ValueError on a
    duplicate id across files.
    """
    merged: list[dict] = []
    source: dict[str, str] = {}

    def _absorb(path: Path):
        for lc in _read_lifecycles(path):
            lid = lc.get("id")
            if not lid:
                raise ValueError(f"{path.name}: a lifecycle is missing 'id'")
            if lid in source:
                raise ValueError(
                    f"duplicate lifecycle id '{lid}' in {path.name} "
                    f"(already defined in {source[lid]})")
            source[lid] = path.name
            merged.append(lc)

    if SCENARIOS_PATH.exists():
        _absorb(SCENARIOS_PATH)
    if FRAGMENTS_DIR.is_dir():
        for frag in sorted(FRAGMENTS_DIR.glob("*.json")):
            _absorb(frag)

    return (merged, source) if with_sources else merged
