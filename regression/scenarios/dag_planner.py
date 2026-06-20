"""dag_planner — scheduler ADR 1.0-b: the closure + topological-wave PLANNER.

ADR 1.0-a (``validate_dag``) proved ``dependencies.json`` is a *complete* DAG —
``adopt_edges`` (which lifecycle reuses which shared root) and ``shared_roots``
(the parent chain ``subnet``/``subnet#db`` under ``vpc``) match the composed
lifecycles. This module is the next link: given a **leaf set** of lifecycle ids
(default = all enabled), it turns that DAG into an *ordered schedule*.

It is **pure offline computation** — no API client, no network, no execution. It
emits a plan; something else (the engine / workflow) runs it.

Three computations (per the ADR):

1. **Closure / shared roots needed** — union ``adopt_edges`` over the leaf set =
   exactly the shared roots that must be provisioned once, ordered by the
   ``shared_roots`` parent chain (``vpc`` before ``subnet``/``subnet#db``). A
   leaf set of only DB lifecycles needs ``vpc`` + ``subnet#db`` but NOT
   ``subnet``; an all-VPC-adopter set needs ``vpc`` + ``subnet``.

2. **Self-create VPC-slot demand** — self-creators (derived via
   ``validate_dag.derive``) provision a capped root THEMSELVES, competing for the
   account VPC cap. Each self-created ``vpc`` kind = one slot. The budget for
   concurrent self-created VPCs is ``vpc_cap - shared_vpc_count`` (default
   ``5 - 1 = 4``): the shared VPC, provisioned in wave 0, holds one slot for the
   whole run.

3. **Topological waves** —
   * wave 0 = *provision* the shared roots (vpc, then subnet/subnet#db);
   * one *adopt* wave: every adopter runs in parallel (they only adopt the
     shared roots; their non-VPC quotas are out of scope — see ``MODELING`` note);
   * the self-creators are *capped*: grouped into back-to-back waves of size
     ``vpc_cap - shared_vpc_count`` so no more than that many self-created VPCs
     are ever concurrent. This is the static analogue of the v0.5 runtime VPC
     semaphore. (Self-creators that consume **no** vpc slot — e.g.
     ``networking-dns-hosted-zone-private`` self-creates only ``private-dns`` —
     don't count against the VPC cap and ride along; they're still scheduled in
     the self-create waves so their non-vpc quota is serialized conservatively.)

MODELING DECISIONS
  * Adopters are treated as ONE parallel wave. Their non-VPC quotas
    (security-group, keypair, private-dns child counts, ...) are real but
    out-of-scope for a VPC-cap planner; bounding them is a separate concern
    (``core.budgets`` at runtime). We surface the adopter count so a caller can
    further shard if a non-VPC quota bites.
  * VPC-slot demand counts ONLY the ``vpc`` self-create kind. A self-creator that
    provisions only ``private-dns`` consumes 0 VPC slots, so it never forces a
    new VPC wave — but it is still placed in the capped waves (it shares them) so
    that its own scarce quota isn't run fully-parallel by accident.
  * The shared VPC reserves exactly ONE slot for the whole plan (it is torn down
    only after the run), so the self-create budget is ``cap - 1``.
"""
from __future__ import annotations

import argparse
import json
import sys
from dataclasses import asdict, dataclass, field

from regression.scenarios import validate_dag

# the capped root every account VPC-cap concern is about
_VPC_KIND = "vpc"
_DEFAULT_VPC_CAP = 5
_SHARED_VPC_COUNT = 1  # the ONE session-shared VPC provisioned in wave 0


# --------------------------------------------------------------------------- #
# plan data structures
# --------------------------------------------------------------------------- #
@dataclass
class Wave:
    """One schedulable wave. ``kind`` is 'provision' (shared roots), 'adopt'
    (parallel adopters), or 'self-create' (cap-bounded self-creators)."""
    kind: str
    lifecycles: list[str] = field(default_factory=list)
    # for self-create waves: the vpc slots this wave holds concurrently.
    vpc_slots: int = 0


@dataclass
class Plan:
    """The full offline schedule for a leaf set.

    ``shared_roots`` is the ordered list of roots to provision once (wave 0
    provisions them). ``waves`` is the ordered execution plan. The remaining
    fields are scheduler input / explainability.
    """
    leaf_set: list[str] = field(default_factory=list)
    shared_roots: list[str] = field(default_factory=list)
    waves: list[Wave] = field(default_factory=list)
    adopters: list[str] = field(default_factory=list)
    self_creators: dict = field(default_factory=dict)   # {lid: [kinds]}
    vpc_cap: int = _DEFAULT_VPC_CAP
    shared_vpc_count: int = _SHARED_VPC_COUNT

    @property
    def self_create_budget(self) -> int:
        """Max concurrent self-created VPCs (cap minus the shared VPC)."""
        return max(0, self.vpc_cap - self.shared_vpc_count)

    def to_dict(self) -> dict:
        return asdict(self)


# --------------------------------------------------------------------------- #
# closure (shared roots) ordering
# --------------------------------------------------------------------------- #
def _root_order(root: str, shared_roots: dict) -> tuple:
    """Sort key putting parents before children: depth in the parent chain, then
    name. ``vpc`` (parent=None) has depth 0; ``subnet``/``subnet#db`` depth 1."""
    depth = 0
    seen = set()
    cur = root
    while True:
        meta = shared_roots.get(cur) or {}
        parent = meta.get("parent")
        if not parent or parent in seen:
            break
        seen.add(parent)
        depth += 1
        cur = parent
    return (depth, root)


def _ordered_closure(needed: set[str], shared_roots: dict) -> list[str]:
    """Order the needed shared roots parent-before-child (vpc, then subnets)."""
    return sorted(needed, key=lambda r: _root_order(r, shared_roots))


# --------------------------------------------------------------------------- #
# the planner
# --------------------------------------------------------------------------- #
def _resolve_leaf_set(leaf_set, derived) -> list[str]:
    """Default leaf set = every derived (enabled) lifecycle; else the given ids
    that are actually known, preserving sorted order for determinism."""
    if leaf_set is None:
        return sorted(derived)
    wanted = set(leaf_set)
    return sorted(lid for lid in derived if lid in wanted)


def plan(leaf_set=None, deps=None, lifecycles=None, vpc_cap=None) -> Plan:
    """Compute the offline schedule for ``leaf_set`` (default = all enabled).

    Pure: derives adopts/self_creates from the composed lifecycles via
    ``validate_dag.derive_all`` and reads only ``shared_roots`` / ``vpc_schedule``
    from ``deps``. No client, no I/O beyond the json/composition the callers
    already loaded.
    """
    if deps is None:
        deps = validate_dag._load_deps()
    if lifecycles is None:
        lifecycles = validate_dag._load_lifecycles()

    budget_paths = deps.get("budget_paths", {})
    shared_roots_meta = deps.get("shared_roots", {})
    vpc_sched = deps.get("vpc_schedule", {})
    if vpc_cap is None:
        vpc_cap = vpc_sched.get("vpc_limit", _DEFAULT_VPC_CAP)

    derived = validate_dag.derive_all(lifecycles, budget_paths)
    leaves = _resolve_leaf_set(leaf_set, derived)

    # ---- 1. closure: union of adopt_edges over the leaf set ----------------
    needed_roots: set[str] = set()
    for lid in leaves:
        needed_roots |= set(derived[lid]["adopts"])
    # pull in every ancestor (a leaf adopting only 'subnet#db' still needs 'vpc')
    expanded = set(needed_roots)
    for root in list(needed_roots):
        cur = root
        while True:
            parent = (shared_roots_meta.get(cur) or {}).get("parent")
            if not parent:
                break
            expanded.add(parent)
            cur = parent
    ordered_roots = _ordered_closure(expanded, shared_roots_meta)

    # ---- 2. classify leaves into adopters vs self-creators -----------------
    adopters: list[str] = []
    self_creators: dict = {}
    for lid in leaves:
        d = derived[lid]
        if d["self_creates"]:
            self_creators[lid] = list(d["self_creates"])
        elif d["adopts"]:
            adopters.append(lid)
        # a leaf that neither adopts nor self-creates touches no shared root and
        # no capped kind — it is unconstrained by THIS planner, so we omit it
        # from the VPC-cap waves (it can run any time, fully parallel).

    shared_vpc_count = _SHARED_VPC_COUNT if _VPC_KIND in expanded else 0

    p = Plan(
        leaf_set=leaves,
        shared_roots=ordered_roots,
        adopters=sorted(adopters),
        self_creators=dict(sorted(self_creators.items())),
        vpc_cap=vpc_cap,
        shared_vpc_count=shared_vpc_count,
    )

    # ---- 3. topological waves ---------------------------------------------
    # wave 0: provision shared roots (already parent-ordered).
    if ordered_roots:
        p.waves.append(Wave(kind="provision", lifecycles=list(ordered_roots),
                            vpc_slots=shared_vpc_count))

    # adopt wave: all adopters in parallel (non-VPC quotas out of scope).
    if adopters:
        p.waves.append(Wave(kind="adopt", lifecycles=sorted(adopters)))

    # self-create waves: cap-bounded by (vpc_cap - shared_vpc_count). A
    # self-creator consumes one VPC slot iff it self-creates the 'vpc' kind.
    budget = max(0, vpc_cap - shared_vpc_count)
    if self_creators:
        _append_self_create_waves(p, self_creators, budget)
    return p


def _append_self_create_waves(p: Plan, self_creators: dict, budget: int) -> None:
    """Greedy-pack self-creators into waves so concurrent self-created VPC slots
    never exceed ``budget``. Self-creators consuming 0 vpc slots ride along
    without inflating the slot count (but still occupy a wave for serialization).
    """
    # deterministic order: vpc-slot consumers first, then by id.
    def vpc_slots(lid: str) -> int:
        return 1 if _VPC_KIND in self_creators[lid] else 0

    ordered = sorted(self_creators, key=lambda lid: (-vpc_slots(lid), lid))
    if budget <= 0:
        budget = 1  # degenerate cap — still make progress one-at-a-time

    cur: list[str] = []
    cur_slots = 0
    for lid in ordered:
        s = vpc_slots(lid)
        if cur and cur_slots + s > budget:
            p.waves.append(Wave(kind="self-create", lifecycles=sorted(cur),
                                vpc_slots=cur_slots))
            cur, cur_slots = [], 0
        cur.append(lid)
        cur_slots += s
    if cur:
        p.waves.append(Wave(kind="self-create", lifecycles=sorted(cur),
                            vpc_slots=cur_slots))


# --------------------------------------------------------------------------- #
# rendering
# --------------------------------------------------------------------------- #
def format_plan(p: Plan) -> str:
    L = []
    L.append(f"DAG plan: {len(p.leaf_set)} leaf lifecycle(s) · "
             f"vpc_cap={p.vpc_cap} · shared_vpc={p.shared_vpc_count} · "
             f"self-create budget={p.self_create_budget}")
    L.append("closure (shared roots needed): "
             + (", ".join(p.shared_roots) or "(none)"))
    L.append(f"adopters (parallel): {len(p.adopters)}")
    L.append(f"self-creators (cap-bounded): {len(p.self_creators)}"
             + (("  " + ", ".join(f"{lid}={p.self_creators[lid]}"
                                  for lid in p.self_creators))
                if p.self_creators else ""))
    L.append(f"-- {len(p.waves)} wave(s) --")
    for i, w in enumerate(p.waves):
        slot = f" [{w.vpc_slots} vpc slot(s)]" if w.vpc_slots else ""
        body = ", ".join(w.lifecycles)
        L.append(f"  wave {i} ({w.kind}){slot}: {body}")
    return "\n".join(L)


# --------------------------------------------------------------------------- #
# leaf-set selection (service filter)
# --------------------------------------------------------------------------- #
def _service_leaf_set(service: str, lifecycles) -> list[str]:
    """Enabled lifecycle ids whose ``service`` path matches ``service`` (full
    'category/name' or just the trailing 'name' segment)."""
    out = []
    for lc in lifecycles:
        if not lc.get("enabled"):
            continue
        svc = lc.get("service", "") or ""
        if svc == service or svc.split("/")[-1] == service:
            out.append(lc["id"])
    return out


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(
        description="Offline closure + topological-wave planner (ADR 1.0-b). "
                    "Computes a schedule; runs nothing.")
    ap.add_argument("--service", help="restrict the leaf set to one service "
                    "(full 'category/name' or trailing 'name' segment)")
    ap.add_argument("--vpc-cap", type=int, default=None,
                    help="override the account VPC cap (default = vpc_limit in "
                         "dependencies.json)")
    ap.add_argument("--json", action="store_true", help="emit the plan as JSON")
    args = ap.parse_args(argv)

    deps = validate_dag._load_deps()
    lifecycles = validate_dag._load_lifecycles()

    leaf_set = None
    if args.service:
        leaf_set = _service_leaf_set(args.service, lifecycles)
        if not leaf_set:
            print(f"no enabled lifecycle matches service '{args.service}'",
                  file=sys.stderr)
            return 1

    p = plan(leaf_set=leaf_set, deps=deps, lifecycles=lifecycles,
             vpc_cap=args.vpc_cap)
    if args.json:
        print(json.dumps(p.to_dict(), indent=2, sort_keys=True))
    else:
        print(format_plan(p))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
