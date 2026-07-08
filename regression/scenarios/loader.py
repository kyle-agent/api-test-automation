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

# --------------------------------------------------------------------------- #
# role derivation — HEAVY-PREMISE CONTRACT §1
# (docs/working/plans/HEAVY-PREMISE-CONTRACT.md). ``role ∈ {"verify","probe"}``
# is DERIVED here at load time and never written back to scenarios.json or a
# fragment (the scenarios.json write ban). Contract definitions:
#   * step tolerant  := its expect_status (engine default [200] when absent)
#                       contains any 4xx or 5xx — the step is allowed to fail.
#   * step mutating  := method ∈ {POST, PUT, PATCH, DELETE}.
#   * lifecycle probe := (≥1 mutating step) AND (0 strict-2xx mutating steps,
#                       i.e. every mutating step is tolerant) AND (tolerant
#                       steps ≥ 0.5 of ALL steps) — "only writes that are
#                       allowed to fail" = a write-reachability probe.
#   * everything else := verify (incl. all-GET reads and license-gated
#                       tolerant reads that can still earn a 2xx).
# ROLE_OVERRIDES is applied LAST; each entry carries its reason here and is
# mirrored into the contract doc by the lead.
# --------------------------------------------------------------------------- #
_MUTATING_METHODS = {"POST", "PUT", "PATCH", "DELETE"}

ROLE_OVERRIDES: dict[str, str] = {
    # (비어 있음) vs-autoscaling-coverage 의 verify override 는 2026-07-08 owner
    # 판정으로 철회 — 그 ASG-쓰기 꼬리(7 스텝)는 자기 인프라(서브넷/ASG)를 만들지
    # 않아 구조적으로 2xx 불가(라이브 관찰: POST asg 400 → 이하 전부 placeholder
    # 404)이고, keypair/LC create 포함 전체 표면을 heavy-asg-full-coverage 가
    # 실자원 2xx 로 검증한다. 파생 규칙대로 probe(CI 스윕 전용)가 올바른 분류.
}


def _expected_statuses(step: dict) -> list[int]:
    """A step's expected statuses with the ENGINE's semantics: ``expect_status``
    may be a scalar or a list (engine._as_status_list) and an absent/empty value
    means a strict default 200 (engine: ``_as_status_list(...) or [200]``)."""
    v = step.get("expect_status")
    if v is None:
        v = []
    elif not isinstance(v, (list, tuple, set)):
        v = [v]
    out: list[int] = []
    for s in v:
        try:
            out.append(int(s))
        except (TypeError, ValueError):
            continue
    return out or [200]


def _is_tolerant(step: dict) -> bool:
    return any(400 <= s < 600 for s in _expected_statuses(step))


def _is_mutating(step: dict) -> bool:
    return str(step.get("method") or "").upper() in _MUTATING_METHODS


def derive_role(lc: dict) -> str:
    """HEAVY-PREMISE CONTRACT §1 rule for one lifecycle dict; ROLE_OVERRIDES
    wins last. Non-HTTP steps (e.g. ``probe_reads``) count as strict non-
    mutating steps, exactly like the engine treats a missing expect_status."""
    steps = lc.get("steps") or []
    mutating = [s for s in steps if _is_mutating(s)]
    role = "verify"
    if mutating and all(_is_tolerant(s) for s in mutating):
        tolerant_ratio = sum(1 for s in steps if _is_tolerant(s)) / len(steps)
        if tolerant_ratio >= 0.5:
            role = "probe"
    return ROLE_OVERRIDES.get(lc.get("id"), role)


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

    Every returned lifecycle carries a derived ``"role"`` key (contract §1) —
    computed here, never persisted to the JSON files.
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

    for lc in merged:                       # contract §1: derived, load-time only
        lc["role"] = derive_role(lc)

    return (merged, source) if with_sources else merged
