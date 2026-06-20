"""dag_scheduler — dynamic, duration-prioritized, slot-gated execution of a
``dag_planner.Plan`` (scheduler ADR 1.0-c refinement; the makespan-optimal sibling
of the static-wave ``dag_runner``).

WHY THIS EXISTS — the static-wave ``dag_runner`` has two makespan defects:

  1. **duration-blind ordering.** ``dag_planner`` cap-packs self-creators by
     ``(-vpc_slots, lid)`` — i.e. slot-count then ALPHABETICAL — ignoring measured
     durations. So a long self-creator (e.g. ``vpc-peering`` ≈ 21 min) lands in the
     LAST self-create wave and *tails* the run.
  2. **wave barriers.** Within the self-create track ``dag_runner`` runs wave N+1
     only after EVERY node of wave N finishes (``[f.result() for f in futs]``). A
     VPC slot freed early by a short node in wave N sits IDLE until the slowest
     node of wave N completes.

Both waste makespan. This module replaces the static self-create waves with the
**resource-constrained list scheduler** that ``schedule_optimizer`` describes:

  * **priority(n) = tail-length** — the longest remaining duration on any path from
    n (``schedule_optimizer.tail_lengths``). With no lifecycle→lifecycle edges
    today, tail == the node's own measured duration, so this is longest-job-first.
  * a **VPC-slot semaphore** of size ``vpc_cap − shared`` (the cap guard, now
    DYNAMIC instead of pre-packed into waves);
  * **dispatch the highest-priority READY self-creator the instant a slot frees** —
    no wave barrier. Zero-slot nodes (free + adopt + private-dns-only self-creators)
    run as soon as a worker is free.

Same ``Executor`` / ``Provisioner`` contract and ``RunResult`` shape as
``dag_runner``, so it is a drop-in alternative behind a flag and unit-testable
offline with fakes. ``simulate_selfcreate`` is a pure (no-exec) makespan estimator
that quantifies the win — feed it measured durations and it prints static-wave vs
dynamic makespan for the self-create portion (where the two strategies differ;
free/adopt are identical in both).
"""
from __future__ import annotations

import threading
import time
from concurrent.futures import ThreadPoolExecutor

from regression.scenarios import dag_planner, dag_runner, schedule_optimizer
from regression.scenarios.dag_runner import LifecycleOutcome, RunResult, WaveResult


# --------------------------------------------------------------------------- #
# helpers
# --------------------------------------------------------------------------- #
def _vpc_demand(plan: dag_planner.Plan) -> dict[str, int]:
    """VPC-slot demand per node: #'vpc' kinds a self-creator provisions (0 for free
    /adopt, and 0 for a private-dns-only self-creator — it touches no VPC slot)."""
    return {lid: list(kinds).count("vpc")
            for lid, kinds in plan.self_creators.items()}


def priorities(plan: dag_planner.Plan, durations: dict | None = None,
               default: float = schedule_optimizer._DEFAULT_S) -> dict[str, float]:
    """priority[node] = tail-length from the duration store (longest-job-first).

    No node→node edges among lifecycles today, so ``requires`` is empty and the
    tail-length collapses to the node's own measured/avg duration — but we route
    through ``schedule_optimizer.tail_lengths`` so this stays correct the day real
    inter-lifecycle edges appear."""
    durations = durations if durations is not None else schedule_optimizer.load_durations()
    nodes = set(plan.free) | set(plan.adopters) | set(plan.self_creators)
    # `requires` is a CALLABLE node -> its prerequisite nodes. No lifecycle->
    # lifecycle edges today, so the graph is empty and tail-length collapses to
    # each node's own duration (longest-job-first).
    return schedule_optimizer.tail_lengths(nodes, lambda _n: (), durations, default)


# --------------------------------------------------------------------------- #
# dynamic execution
# --------------------------------------------------------------------------- #
def run_dynamic(plan: dag_planner.Plan, executor, *, provisioner=None,
                max_workers: int | None = None, durations: dict | None = None,
                default_duration: float = schedule_optimizer._DEFAULT_S,
                on_event=None) -> RunResult:
    """Execute ``plan`` with dynamic, duration-prioritized, slot-gated dispatch.

    Cap-safety is preserved (now dynamically): a VPC-slot semaphore of size
    ``plan.self_create_budget`` (== cap − shared) bounds concurrent self-created
    VPCs exactly as the static self-create waves did — but a freed slot is handed
    to the highest-priority waiting self-creator immediately, with no wave barrier.

    Drop-in for ``dag_runner.run_plan``: same ``executor`` (run one lifecycle by id
    → LifecycleOutcome, never raises) / ``provisioner`` contract, same RunResult.
    """
    def emit(kind: str, payload: dict) -> None:
        if on_event is not None:
            try:
                on_event(kind, payload)
            except Exception:  # noqa: BLE001 — observability must not break the run
                pass

    result = RunResult(shared_roots=list(plan.shared_roots))

    if provisioner is not None:
        emit("provision_start", {"roots": list(plan.shared_roots)})
        try:
            provisioner.provision()
        except Exception as exc:  # provisioning failure aborts cleanly
            result.provision_error = f"{type(exc).__name__}: {exc}"
            emit("provision_done", {"error": result.provision_error})
            return result
        emit("provision_done", {})
    result.waves.append(WaveResult(kind="provision", outcomes=[], duration_s=0.0))

    demand = _vpc_demand(plan)
    prio = priorities(plan, durations, default_duration)
    budget = max(0, plan.self_create_budget)
    workers = max(1, max_workers or 8)

    # partition: zero-slot nodes run freely (pool-bounded); slot nodes are gated.
    zero = ([lid for lid in plan.free]
            + [lid for lid in plan.adopters]
            + [lid for lid in plan.self_creators if demand.get(lid, 0) == 0])
    slot = sorted((lid for lid in plan.self_creators if demand.get(lid, 0) > 0),
                  key=lambda l: prio.get(l, default_duration), reverse=True)  # longest first

    outcomes: list[LifecycleOutcome] = []
    out_lock = threading.Lock()
    cv = threading.Condition()
    avail = [budget]   # VPC slots available (mutated under cv)
    t0 = time.monotonic()

    def run_one(lid: str) -> LifecycleOutcome:
        o = executor(lid)
        with out_lock:
            outcomes.append(o)
        emit("lifecycle_done", {"lifecycle_id": o.lifecycle_id, "status": o.status,
                                "reason": o.reason, "duration_s": o.duration_s})
        return o

    with ThreadPoolExecutor(max_workers=workers) as pool:
        futs = [pool.submit(run_one, lid) for lid in zero]
        # Dispatch slot nodes in priority order; acquiring is BLOCKING so the
        # highest-priority node always claims the next freed slot (no barrier). The
        # done-callback releases the slot and wakes the next waiter.
        for lid in slot:
            need = demand[lid]
            with cv:
                while avail[0] < need:
                    cv.wait()
                avail[0] -= need

            def _release(_f, n=need):
                with cv:
                    avail[0] += n
                    cv.notify_all()

            f = pool.submit(run_one, lid)
            f.add_done_callback(_release)
            futs.append(f)
        for f in futs:
            f.result()

    if provisioner is not None:
        try:
            provisioner.teardown()
        except Exception:  # noqa: BLE001 — teardown is best-effort
            pass
        emit("teardown_done", {})

    # record as one flat 'dynamic' wave (RunResult is order-agnostic for status).
    result.waves.append(WaveResult(kind="dynamic", outcomes=outcomes,
                                   duration_s=time.monotonic() - t0))
    return result


# --------------------------------------------------------------------------- #
# pure makespan estimator — quantify the win WITHOUT executing anything
# --------------------------------------------------------------------------- #
def _sim_machines(jobs: list[tuple[str, float]], machines: int) -> tuple[float, dict]:
    """List-schedule ``jobs`` [(id, dur)] onto ``machines`` identical slots in the
    GIVEN order (greedy: each job starts on the earliest-free machine). Returns
    (makespan, {id: (start, end)}). This is the dynamic 'slot frees → next job'
    model when ``jobs`` is pre-sorted longest-first (LPT)."""
    if machines <= 0:
        machines = 1
    free_at = [0.0] * machines
    sched: dict[str, tuple[float, float]] = {}
    for jid, d in jobs:
        m = min(range(machines), key=lambda i: free_at[i])
        start = free_at[m]
        free_at[m] = start + d
        sched[jid] = (start, start + d)
    return (max(free_at) if free_at else 0.0), sched


def simulate_selfcreate(plan: dag_planner.Plan, durations: dict | None = None,
                        default: float = schedule_optimizer._DEFAULT_S) -> dict:
    """Estimate the self-create-portion makespan under BOTH strategies (pure, no
    execution) so the win is quantifiable. free/adopt are identical in both, so we
    isolate the slot-gated self-creators where the strategies differ.

    Returns a dict with both makespans, the per-node schedule, and each node's
    start time (so you can see e.g. vpc-peering move earlier under dynamic).
    """
    durations = durations if durations is not None else schedule_optimizer.load_durations()
    demand = _vpc_demand(plan)
    budget = max(1, plan.self_create_budget)
    slot = [lid for lid in plan.self_creators if demand.get(lid, 0) > 0]
    dur = {lid: schedule_optimizer.duration_of(lid, durations, default) for lid in slot}

    # STATIC: planner order (-vpc_slots, lid) == alphabetical here, packed into
    # cap-sized waves WITH a barrier (next wave waits for the slowest in this wave).
    static_order = sorted(slot, key=lambda l: (-demand[l], l))
    static_make = 0.0
    static_sched: dict[str, tuple[float, float]] = {}
    t = 0.0
    for i in range(0, len(static_order), budget):
        wave = static_order[i:i + budget]
        wmax = max(dur[l] for l in wave)
        for l in wave:
            static_sched[l] = (t, t + dur[l])
        t += wmax            # barrier: whole wave must finish before the next
    static_make = t

    # DYNAMIC: longest-job-first onto `budget` slots, no barrier.
    dyn_jobs = sorted(((l, dur[l]) for l in slot), key=lambda x: x[1], reverse=True)
    dyn_make, dyn_sched = _sim_machines(dyn_jobs, budget)

    return {
        "budget": budget,
        "n_selfcreate": len(slot),
        "durations_s": dur,
        "static": {"makespan_s": static_make, "order": static_order, "schedule": static_sched},
        "dynamic": {"makespan_s": dyn_make, "order": [j for j, _ in dyn_jobs], "schedule": dyn_sched},
        "saving_s": static_make - dyn_make,
    }


def format_comparison(sim: dict) -> str:
    L = [f"self-create makespan — budget={sim['budget']} slot(s), "
         f"{sim['n_selfcreate']} self-creator(s)"]
    s, d = sim["static"], sim["dynamic"]
    L.append(f"  STATIC  (cap-packed, alpha, wave-barrier): {s['makespan_s']/60:6.1f} min")
    L.append(f"  DYNAMIC (longest-first, slot-gated)      : {d['makespan_s']/60:6.1f} min")
    L.append(f"  SAVING                                   : {sim['saving_s']/60:6.1f} min "
             f"({100*sim['saving_s']/s['makespan_s']:.0f}% of self-create portion)" if s['makespan_s'] else "")
    L.append("  per-node start time (min into the self-create portion):")
    for lid in sorted(sim["durations_s"], key=lambda l: sim["durations_s"][l], reverse=True):
        ss = s["schedule"].get(lid, (0, 0))[0] / 60
        ds = d["schedule"].get(lid, (0, 0))[0] / 60
        L.append(f"    {lid:38} dur {sim['durations_s'][lid]/60:5.1f}m   "
                 f"static start {ss:5.1f}m  →  dynamic start {ds:5.1f}m")
    return "\n".join(L)


def main(argv: list[str] | None = None) -> int:
    import argparse
    ap = argparse.ArgumentParser(
        description="Compare static-wave vs dynamic self-create makespan (pure, no exec).")
    ap.add_argument("--service", help="restrict the leaf set to one service")
    ap.add_argument("--vpc-cap", type=int, default=None)
    args = ap.parse_args(argv)
    leaf = None
    if args.service:
        from regression.scenarios import validate_dag
        leaf = dag_planner._service_leaf_set(args.service, validate_dag._load_lifecycles())
    plan = dag_planner.plan(leaf_set=leaf, vpc_cap=args.vpc_cap)
    print(format_comparison(simulate_selfcreate(plan)))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
