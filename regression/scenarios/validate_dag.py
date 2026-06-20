"""validate_dag — scheduler ADR 1.0-a: prove dependencies.json is a *complete*
dependency DAG, the precondition that gates the 1.0 DAG runner.

`dependencies.json` historically held only quota accounting (quota_kinds) + viz
metadata; the DAG *edges* a scheduler needs — which lifecycles adopt which shared
upstream roots (VPC / subnet / DB-subnet) — lived implicitly in the lifecycle
``{"adopt": <kind>}`` steps and were captured nowhere as data. This module derives
those edges from the composed lifecycles and checks dependencies.json declares them
exactly, so the graph can drive ``closure -> shared-roots -> topological waves``.

Per ENABLED lifecycle it derives two signals:

  * **adopts**       — the shared roots it reuses (DAG edges), from ``adopt`` steps.
  * **self_creates** — budget kinds it provisions ITSELF (consumes a cap slot):
    a ``POST`` to a ``budget_paths`` path that does NOT also ``adopt`` that same
    kind. (An ``adopt:vpc`` ``POST /v1/vpcs`` is the adopt-fallback the engine skips
    under xdist via IB-049, NOT a genuine create — so it is an edge, not a slot.)

The DAG the scheduler needs is two NEW sections in dependencies.json:

  * ``adopt_edges``  {lifecycle_id: [roots...]}  == derived adopts (the edges)
  * ``shared_roots`` {root: {parent: ...}}       superset of every adopted root

``self_creates`` (the slot-consumers a cap-aware scheduler must serialize) is
DERIVED, not stored — it falls out of the same pass and is reported as scheduler
input. The legacy ``quota_kinds`` section (broad core.budgets reservations,
path-derived by the engine at runtime) is a separate concern and left untouched.

Run as a report (default) or a CI gate (``--check`` exits 1 on any gap).
"""
from __future__ import annotations

import argparse
import json
from dataclasses import dataclass, field
from pathlib import Path

_DEPS_PATH = Path(__file__).resolve().parent / "dependencies.json"


def _load_deps(path: Path | None = None) -> dict:
    return json.loads((path or _DEPS_PATH).read_text())


def _base_kind(adopt: str) -> str:
    """Map an adopt token to its budget/root base kind: 'subnet#db' -> 'subnet'."""
    return adopt.split("#", 1)[0]


def derive(lc: dict, budget_paths: dict) -> tuple[set[str], set[str]]:
    """Return (self_creates, adopts) for one composed lifecycle dict.

    self_creates: budget kinds genuinely provisioned (POST to a budget path with no
    adopt of that kind). adopts: the shared-root tokens of every ``adopt`` step.
    """
    self_creates: set[str] = set()
    adopts: set[str] = set()
    for step in lc.get("steps", []):
        ad = step.get("adopt")
        if ad:
            adopts.add(ad)
        method = (step.get("method") or "").upper()
        path = step.get("path") or ""
        if method == "POST" and path in budget_paths:
            kind = budget_paths[path]
            # an adopt of the same base kind on this very step = adopt-fallback,
            # not a genuine self-create (IB-049 skips it under xdist).
            if ad and _base_kind(ad) == kind:
                continue
            self_creates.add(kind)
    return self_creates, adopts


def derive_all(lifecycles: list[dict], budget_paths: dict) -> dict[str, dict]:
    """{lifecycle_id: {'self_creates': sorted[], 'adopts': sorted[]}} for ENABLED."""
    out: dict[str, dict] = {}
    for lc in lifecycles:
        if not lc.get("enabled"):
            continue
        sc, ad = derive(lc, budget_paths)
        out[lc["id"]] = {"self_creates": sorted(sc), "adopts": sorted(ad)}
    return out


@dataclass
class Report:
    """Structured DAG-completeness report. ``ok`` is True iff no gaps."""
    derived: dict[str, dict] = field(default_factory=dict)
    # gaps (DAG completeness):
    adopt_missing: list = field(default_factory=list)    # (lid, derived, declared) adopt_edges wrong/absent
    adopt_extra: list = field(default_factory=list)      # (lid, declared) declared for a non-enabled lifecycle
    root_undefined: list = field(default_factory=list)   # (lid, root) adopted root absent from shared_roots
    vpc_crud_missing: list = field(default_factory=list)  # (lid,) VPC self-creator not in vpc_schedule.vpc_crud_lifecycles
    # informational (scheduler input, not gaps):
    shared_roots: dict = field(default_factory=dict)     # {root: [dependents]}
    self_creators: dict = field(default_factory=dict)    # {lid: [kinds]} slot-consumers

    @property
    def ok(self) -> bool:
        return not (self.adopt_missing or self.adopt_extra or self.root_undefined
                    or self.vpc_crud_missing)

    @property
    def gap_count(self) -> int:
        return (len(self.adopt_missing) + len(self.adopt_extra)
                + len(self.root_undefined) + len(self.vpc_crud_missing))


def build_report(lifecycles: list[dict], deps: dict) -> Report:
    budget_paths = deps.get("budget_paths", {})
    declared_adopt = deps.get("adopt_edges", {})
    shared_roots = deps.get("shared_roots", {})

    derived = derive_all(lifecycles, budget_paths)
    enabled_ids = set(derived)
    r = Report(derived=derived)

    for lid, d in derived.items():
        dad = set(d["adopts"])
        cad = set(declared_adopt.get(lid, []))
        if dad != cad:
            r.adopt_missing.append((lid, sorted(dad), sorted(cad)))
        for root in dad:
            if root not in shared_roots:
                r.root_undefined.append((lid, root))
            r.shared_roots.setdefault(root, []).append(lid)
        if d["self_creates"]:
            r.self_creators[lid] = d["self_creates"]

    # stale adopt_edges declared for a lifecycle that is no longer enabled
    for lid in declared_adopt:
        if lid not in enabled_ids:
            r.adopt_extra.append((lid, sorted(declared_adopt[lid])))

    # partition safety: the legacy vpc_schedule.vpc_crud_lifecycles list (which
    # shared_infra --print-filters turns into VPC_CRUD_K, the SERIAL lane in the
    # pre-cutover workflow) must contain every lifecycle that self-creates a VPC.
    # A VPC self-creator NOT listed there falls into PARALLEL_K and self-creates a
    # VPC in the parallel adopt lane -> races the account VPC cap. Keep the
    # hand-list honest against the derived DAG so it can't silently drift.
    vpc_crud = set(deps.get("vpc_schedule", {}).get("vpc_crud_lifecycles", []))
    for lid, d in derived.items():
        if "vpc" in d["self_creates"] and lid not in vpc_crud:
            r.vpc_crud_missing.append((lid,))
    return r


def _load_lifecycles() -> list[dict]:
    # imported lazily: composing the model is heavier than reading the json.
    from regression.scenarios import engine
    return list(engine.LIFECYCLES)


def format_report(r: Report, *, verbose: bool = False) -> str:
    L = []
    n_create = sum(1 for d in r.derived.values() if d["self_creates"])
    n_adopt = sum(1 for d in r.derived.values() if d["adopts"])
    L.append(f"DAG derivation: {len(r.derived)} enabled lifecycle(s) · "
             f"{n_create} self-creator(s) · {n_adopt} adopter(s)")
    L.append("shared roots (derived): " + ", ".join(
        f"{root}<-{len(deps)}" for root, deps in sorted(r.shared_roots.items())))
    L.append("self-creators (slot-consumers, derived): " + (
        ", ".join(f"{lid}={k}" for lid, k in sorted(r.self_creators.items())) or "(none)"))
    if r.ok:
        L.append("✅ dependencies.json is a COMPLETE DAG — 0 gaps "
                 "(adopt_edges + shared_roots match the lifecycles)")
    else:
        L.append(f"❌ {r.gap_count} gap(s):")
    for lid, dv, cv in r.adopt_missing:
        L.append(f"  [adopt_edges]  {lid}: lifecycle adopts {dv} but dependencies.json says {cv}")
    for lid, root in r.root_undefined:
        L.append(f"  [shared_roots] {lid} adopts '{root}' which is not defined in shared_roots")
    for lid, cv in r.adopt_extra:
        L.append(f"  [stale adopt_edges]  {lid} declared {cv} but is not an enabled lifecycle")
    for (lid,) in r.vpc_crud_missing:
        L.append(f"  [vpc_crud_lifecycles] {lid} self-creates a VPC but is missing from "
                 f"vpc_schedule.vpc_crud_lifecycles -> would race the cap in the parallel lane")
    if verbose:
        L.append("\n-- derived edges --")
        for lid in sorted(r.derived):
            d = r.derived[lid]
            if d["adopts"] or d["self_creates"]:
                L.append(f"  {lid:36} adopts={d['adopts']} self_creates={d['self_creates']}")
    return "\n".join(L)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Validate dependencies.json is a complete DAG.")
    ap.add_argument("--check", action="store_true",
                    help="exit non-zero if any gap (CI gate); default just reports")
    ap.add_argument("--verbose", "-v", action="store_true", help="print every derived edge")
    ap.add_argument("--json", action="store_true", help="emit the derived edges as JSON")
    args = ap.parse_args(argv)

    deps = _load_deps()
    r = build_report(_load_lifecycles(), deps)
    if args.json:
        print(json.dumps(r.derived, indent=2, sort_keys=True))
        return 0 if (r.ok or not args.check) else 1
    print(format_report(r, verbose=args.verbose))
    return 1 if (args.check and not r.ok) else 0


if __name__ == "__main__":
    raise SystemExit(main())
