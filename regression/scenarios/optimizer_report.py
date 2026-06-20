"""optimizer_report — turn the learned schedule into an ACTIONABLE report.

The scheduler primitives live in three sibling modules:

  * ``schedule_optimizer`` — the learned per-node durations (rolling averages),
    the duration-weighted ``critical_path`` (the wall-time FLOOR), the tail-length
    priority, and the cap-feasible greedy ``schedule`` (estimated makespan).
  * ``catalog_planner`` — the FULL resource dependency graph (vpc, subnet,
    ske-cluster, …) with the real cross-resource ``requires`` edges.
  * ``validate_dag`` — derives, per enabled lifecycle, which budget kinds it
    self-creates (a lifecycle is a VPC self-creator iff ``'vpc'`` ∈ self_creates).

This module composes them into a single human-readable report that answers two
questions a planner actually asks:

  * **what should we prioritize?** — the resource critical path (the structural
    floor) + the lifecycle priority order (longest-tail-first) + which VPC
    self-creators should start FIRST so the cap is never the bottleneck.
  * **how much time can we save?** — the estimated OPTIMAL makespan (the learned,
    cap-aware greedy schedule) vs the CURRENT ``dag_planner`` wave schedule's
    estimated makespan, and the implied time saved.

Every duration is a LEARNED AVERAGE (``data/optimizer/durations.json``), so a
node we have never measured falls back to the default and the numbers are
approximate until more runs land. The report says so explicitly.

Pure + offline: reads only the composed model, the durations store and
``dependencies.json``. No client, no network, credential-free.
"""
from __future__ import annotations

import argparse
import json

from regression.scenarios import (
    catalog_planner,
    dag_planner,
    schedule_optimizer as sopt,
    validate_dag,
)

_DEFAULT_VPC_CAP = 5
_SHARED_VPC = 1


# --------------------------------------------------------------------------- #
# helpers
# --------------------------------------------------------------------------- #
def _enabled_lifecycles() -> list[dict]:
    return validate_dag._load_lifecycles()


def _derived(lifecycles: list[dict] | None = None) -> dict[str, dict]:
    """{lid: {self_creates, adopts}} over the ENABLED lifecycles."""
    if lifecycles is None:
        lifecycles = _enabled_lifecycles()
    deps = validate_dag._load_deps()
    return validate_dag.derive_all(lifecycles, deps.get("budget_paths", {}))


def _vpc_self_creators(derived: dict[str, dict]) -> set[str]:
    """Enabled lifecycles that self-create a 'vpc' (hold a VPC slot)."""
    return {lid for lid, d in derived.items() if "vpc" in d["self_creates"]}


def _node_lifecycle(model: dict, node_id: str) -> str | None:
    """The composer ``source.lifecycle`` that creates+tests a resource node."""
    src = (model.get(node_id) or {}).get("source") or {}
    return src.get("lifecycle") if isinstance(src, dict) else None


# --------------------------------------------------------------------------- #
# 1. resource critical path (structural floor over the full resource graph)
# --------------------------------------------------------------------------- #
def resource_critical_path(*, model: dict | None = None,
                           durations: dict | None = None,
                           default: float = sopt._DEFAULT_S):
    """Critical path over the FULL resource dependency graph.

    Each resource node's duration is estimated from its source lifecycle's
    measured average (``source.lifecycle`` -> durations store), falling back to
    ``default`` for an unmeasured/unmapped node. Returns
    ``(path, total_seconds)`` — the longest duration-weighted chain (the
    structural wall-time floor) through ``catalog_planner.load_graph()``.
    """
    if model is None:
        from regression.scenarios import composer
        model = composer.load_model() or {}
    if durations is None:
        durations = sopt.load_durations()

    graph = catalog_planner.load_graph(model)
    nodes = set(graph)

    def requires(n):
        return graph[n].requires if n in graph else []

    # estimate each node's duration via its source.lifecycle's measured avg.
    # schedule_optimizer.duration_of looks a node up in the durations dict, so
    # build a per-RESOURCE-NODE duration map keyed by node id.
    node_dur: dict[str, dict] = {}
    for nid in nodes:
        lc = _node_lifecycle(model, nid)
        secs = sopt.duration_of(lc, durations, default) if lc else default
        node_dur[nid] = {"avg_s": secs, "n": 1, "last_s": secs}

    return sopt.critical_path(nodes, requires, node_dur, default)


# --------------------------------------------------------------------------- #
# 2. actionable lifecycle schedule (the run order to use)
# --------------------------------------------------------------------------- #
def lifecycle_schedule(vpc_cap: int = _DEFAULT_VPC_CAP, *,
                       lifecycles: list[dict] | None = None,
                       durations: dict | None = None,
                       default: float = sopt._DEFAULT_S):
    """Cap-feasible schedule over the ENABLED lifecycles.

    Lifecycles are treated as INDEPENDENT (``requires -> []``): each is a
    runnable leaf the engine dispatches; the only shared constraint modelled
    here is the VPC cap. ``holds_slot(lid)`` is True iff the lifecycle
    self-creates a 'vpc' (per ``validate_dag.derive_all``). Returns a
    ``schedule_optimizer.Schedule`` — its ``order`` is the longest-tail-first
    priority, ``makespan_s`` the estimated wall-time, ``slot_consumers`` the
    VPC self-creators that must be serialized under the cap.
    """
    if lifecycles is None:
        lifecycles = _enabled_lifecycles()
    if durations is None:
        durations = sopt.load_durations()

    derived = _derived(lifecycles)
    nodes = set(derived)
    holds = _vpc_self_creators(derived)

    return sopt.schedule(
        nodes,
        lambda n: [],                       # independent leaves
        durations,
        vpc_cap=vpc_cap,
        shared_vpc=_SHARED_VPC,
        holds_slot=lambda n: n in holds,
        default=default,
    )


# --------------------------------------------------------------------------- #
# 3. current dag_planner schedule's estimated makespan (the baseline to beat)
# --------------------------------------------------------------------------- #
def current_makespan_estimate(vpc_cap: int = _DEFAULT_VPC_CAP, *,
                              durations: dict | None = None,
                              default: float = sopt._DEFAULT_S) -> float:
    """Estimate the CURRENT ``dag_planner`` wave schedule's wall-time.

    The current plan is a sequence of waves:
      * a 'provision' wave (shared roots) — modelled as fixed setup, folded into
        the pipeline (its lifecycles are not run-leaves, so it adds nothing here);
      * a 'free' wave — VPC-independent leaves, fully parallel, runs CONCURRENTLY
        with the rest of the pipeline (so it costs ``max(free)`` overlapped);
      * an 'adopt' wave — all adopters in parallel (``max(adopt)``);
      * one or more 'self-create' waves — cap-SERIALIZED (back-to-back), each
        costing the max duration within that wave; they sum.

    The estimate is: pipeline = adopt + Σ self-create waves; total =
    max(free_wave, pipeline). Durations come from the learned store.
    """
    if durations is None:
        durations = sopt.load_durations()

    def _dur(lid: str) -> float:
        return sopt.duration_of(lid, durations, default)

    def _wave_max(lids) -> float:
        return max((_dur(x) for x in lids), default=0.0)

    p = dag_planner.plan(vpc_cap=vpc_cap)

    free_s = 0.0
    pipeline_s = 0.0
    for w in p.waves:
        if w.kind == "provision":
            continue                         # shared-root setup, not a run-leaf
        if w.kind == "free":
            free_s += _wave_max(w.lifecycles)   # parallel, overlaps the pipeline
        elif w.kind == "adopt":
            pipeline_s += _wave_max(w.lifecycles)   # one parallel adopt wave
        elif w.kind == "self-create":
            pipeline_s += _wave_max(w.lifecycles)   # cap-serialized: waves sum

    return max(free_s, pipeline_s)


# --------------------------------------------------------------------------- #
# 4. render the report
# --------------------------------------------------------------------------- #
def _fmt_s(seconds: float) -> str:
    seconds = float(seconds)
    if seconds < 90:
        return f"{seconds:.0f}s"
    return f"{seconds:.0f}s ({seconds / 60:.1f} min)"


def build_report(vpc_cap: int = _DEFAULT_VPC_CAP, *,
                 model: dict | None = None,
                 lifecycles: list[dict] | None = None,
                 durations: dict | None = None,
                 default: float = sopt._DEFAULT_S) -> dict:
    """Compute every number the report needs, as a JSON-able dict."""
    if model is None:
        from regression.scenarios import composer
        model = composer.load_model() or {}
    if lifecycles is None:
        lifecycles = _enabled_lifecycles()
    if durations is None:
        durations = sopt.load_durations()

    rc_path, rc_floor = resource_critical_path(
        model=model, durations=durations, default=default)

    sched = lifecycle_schedule(
        vpc_cap, lifecycles=lifecycles, durations=durations, default=default)

    optimal = sched.makespan_s
    current = current_makespan_estimate(
        vpc_cap, durations=durations, default=default)
    saved = current - optimal

    # tail-length priority over the (independent) enabled leaves: longest tail
    # = single longest learned duration (no edges), the LPT priority order.
    nodes = set(sched.order) or {lc["id"] for lc in lifecycles if lc.get("enabled")}
    tails = sopt.tail_lengths(nodes, lambda n: [], durations, default)
    priority = sorted(nodes, key=lambda n: (-tails[n], n))

    # how many of the durations are real measurements vs the default fallback.
    measured = sum(1 for n in nodes if sopt.duration_of(n, durations, -1.0) >= 0)

    return {
        "vpc_cap": vpc_cap,
        "resource_critical_path": rc_path,
        "resource_floor_s": rc_floor,
        "priority": priority,
        "tails": {n: round(tails[n], 1) for n in nodes},
        "vpc_self_creators": list(sched.slot_consumers),
        "optimal_makespan_s": optimal,
        "current_makespan_s": current,
        "time_saved_s": saved,
        "n_leaves": len(nodes),
        "n_measured": measured,
        "floor_s": sched.floor_s,
    }


def render_report(vpc_cap: int = _DEFAULT_VPC_CAP, *,
                  model: dict | None = None,
                  lifecycles: list[dict] | None = None,
                  durations: dict | None = None,
                  default: float = sopt._DEFAULT_S,
                  top: int = 10) -> str:
    """A concise text report: the resource critical path (chain + floor), the
    actionable lifecycle priority order (longest-tail-first), the VPC
    self-creators that should start FIRST, and the optimal-vs-current makespan
    with the implied time saved. Durations are learned averages (approximate
    until more runs land) — the report labels this.
    """
    r = build_report(vpc_cap, model=model, lifecycles=lifecycles,
                     durations=durations, default=default)
    L: list[str] = []
    L.append("=== optimizer report (learned-duration scheduler) ===")
    L.append(f"vpc_cap={r['vpc_cap']} · {r['n_leaves']} enabled lifecycle leaf(s) · "
             f"{r['n_measured']} with a measured duration")
    L.append("NOTE: all durations are LEARNED AVERAGES from prior runs "
             "(data/optimizer/durations.json) and are APPROXIMATE until more "
             "runs land.")
    L.append("")

    # resource critical path (structural floor)
    chain = r["resource_critical_path"]
    L.append("-- resource critical path (structural wall-time floor) --")
    if chain:
        L.append("  " + "  ->  ".join(chain))
        L.append(f"  floor: {_fmt_s(r['resource_floor_s'])} "
                 "(nothing can finish sooner; estimated from source-lifecycle "
                 "averages)")
    else:
        L.append("  (empty graph)")
    L.append("")

    # actionable priority order
    L.append(f"-- run priority (schedule longest-tail-first; top {top}) --")
    for i, lid in enumerate(r["priority"][:top], 1):
        L.append(f"  {i:2}. {lid}  (~{_fmt_s(r['tails'][lid])})")
    if len(r["priority"]) > top:
        L.append(f"  ... +{len(r['priority']) - top} more")
    L.append("")

    # VPC self-creators that should start first
    sc = r["vpc_self_creators"]
    L.append(f"-- VPC self-creators (hold a VPC slot; cap={r['vpc_cap']}-"
             f"{_SHARED_VPC} shared = "
             f"{max(1, r['vpc_cap'] - _SHARED_VPC)} concurrent) — START THESE "
             "FIRST --")
    if sc:
        # surface the longest-tail self-creators first (they gate the cap).
        sc_sorted = sorted(sc, key=lambda n: (-r["tails"].get(n, 0.0), n))
        for lid in sc_sorted:
            L.append(f"  * {lid}  (~{_fmt_s(r['tails'].get(lid, 0.0))})")
    else:
        L.append("  (none — every enabled lifecycle adopts the shared VPC)")
    L.append("")

    # makespan: optimal vs current
    opt, cur, saved = r["optimal_makespan_s"], r["current_makespan_s"], r["time_saved_s"]
    L.append("-- makespan: optimal vs current --")
    L.append(f"  estimated OPTIMAL  (learned cap-aware greedy schedule): {_fmt_s(opt)}")
    L.append(f"  estimated CURRENT  (dag_planner wave schedule):         {_fmt_s(cur)}")
    if saved > 0:
        pct = (saved / cur * 100.0) if cur else 0.0
        L.append(f"  >> implied time SAVED: {_fmt_s(saved)} ({pct:.0f}% faster)")
    elif saved < 0:
        L.append(f"  >> current is already faster by {_fmt_s(-saved)} "
                 "(optimal schedule has no headroom over it here)")
    else:
        L.append("  >> no difference at this cap.")
    return "\n".join(L)


# --------------------------------------------------------------------------- #
# CLI
# --------------------------------------------------------------------------- #
def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        description="Actionable optimization report from learned durations + the "
                    "dependency graph + the VPC cap. Pure/offline, credential-free.")
    ap.add_argument("--vpc-cap", type=int, default=_DEFAULT_VPC_CAP,
                    help=f"account VPC cap (default {_DEFAULT_VPC_CAP})")
    ap.add_argument("--json", action="store_true",
                    help="emit the report numbers as JSON instead of text")
    args = ap.parse_args(argv)

    if args.json:
        print(json.dumps(build_report(args.vpc_cap), indent=2, sort_keys=True))
    else:
        print(render_report(args.vpc_cap))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
