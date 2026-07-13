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

from regression.scenarios import dag_planner, schedule_optimizer
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
    tails = schedule_optimizer.tail_lengths(nodes, lambda _n: (), durations, default)
    # priority_first 핀(오너 2026-07-13): 짧은 VPC-슬롯 소비자가 LPT에서 뒤로
    # 밀려 슬롯 대기 + 런 꼬리가 되는 것을 방지 — 어떤 실측 tail보다 큰 가산치로
    # 항상 먼저 디스패치된다 (핀들 사이 상대 순서는 tail이 결정). 부작용 주의:
    # run_dynamic의 heavy_stagger는 prio >= threshold로 heavy를 판별하므로 핀
    # 노드는 항상 stagger 대상이 된다 (현재 핀 2종은 400/544s로 어차피 heavy).
    for lid in schedule_optimizer.load_priority_first():
        if lid in tails:
            tails[lid] += schedule_optimizer.PIN_BOOST_S
    return tails


# --------------------------------------------------------------------------- #
# dynamic execution
# --------------------------------------------------------------------------- #
def run_dynamic(plan: dag_planner.Plan, executor, *, provisioner=None,
                max_workers: int | None = None, durations: dict | None = None,
                default_duration: float = schedule_optimizer._DEFAULT_S,
                heavy_stagger_s: float = 0.0, heavy_threshold_s: float = 300.0,
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
    # BOTH partitions are dispatched LONGEST-FIRST: under worker/slot contention the
    # critical-path heavy ADOPTERS (DB clusters etc.) must grab the first slots, or
    # they start late behind the light free wave and tail the run. (Measured
    # 2026-06-20: heavy-shared-dbaas, a 46-min critical-path create, started 22.9 min
    # late because it competed with 162 light free nodes for the storm-clamped slots —
    # ~18 min of the run's 26% overhead. Priority on the zero-slot batch fixes that.)
    zero = sorted(
        [lid for lid in plan.free]
        + [lid for lid in plan.adopters]
        + [lid for lid in plan.self_creators if demand.get(lid, 0) == 0],
        key=lambda x: prio.get(x, default_duration), reverse=True)  # longest-first
    slot = sorted((lid for lid in plan.self_creators if demand.get(lid, 0) > 0),
                  key=lambda x: prio.get(x, default_duration), reverse=True)  # longest first

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

    # UNIFIED priority dispatch (fixes the 2026-06-20 self-creator-tail bug): ONE
    # longest-first ready list over ALL nodes; ``workers`` worker threads each pop the
    # highest-priority node whose VPC-slot demand is currently free (zero-slot nodes
    # are always ready). This is the resource-constrained list scheduler — a long
    # self-creator (heavy-shared-networking ~24 min, vpc-peering ~21 min) is picked
    # EARLY alongside the heavy adopters instead of queueing behind the whole free
    # wave. (The earlier two-phase version submitted every zero-slot node to the pool
    # FIRST, so self-creators sat at the BACK of the queue and started ~72 min in,
    # tailing the makespan ~28 min — measured live, dynamic run #2.)
    #
    # Optional BURST STAGGER (``heavy_stagger_s`` > 0): space the START of consecutive
    # HEAVY nodes (prio >= ``heavy_threshold_s``) at least that far apart so their
    # create-time API bursts don't all hit the gateway at once (longest-first packs
    # every heavy DB/K8s/VM create into ~4 s, aggravating the 502/503 storm). Light
    # nodes never wait.
    pending = sorted(zero + slot, key=lambda x: prio.get(x, default_duration),
                     reverse=True)
    stagger_lock = threading.Lock()
    last_heavy = [0.0]

    def _take() -> str | None:
        """Pop the highest-priority node whose slots are free (zero-slot always free).
        Blocks until one is takeable; returns None only when nothing remains."""
        with cv:
            while pending:
                for i, lid in enumerate(pending):
                    if avail[0] >= demand.get(lid, 0):
                        avail[0] -= demand.get(lid, 0)
                        pending.pop(i)
                        return lid
                cv.wait(timeout=2.0)   # all remaining need a slot; wait for a release
            return None

    def _stagger(lid: str) -> None:
        if heavy_stagger_s <= 0 or prio.get(lid, default_duration) < heavy_threshold_s:
            return
        with stagger_lock:
            gap = heavy_stagger_s - (time.monotonic() - last_heavy[0])
            if last_heavy[0] and gap > 0:
                time.sleep(gap)
            last_heavy[0] = time.monotonic()

    def _worker() -> None:
        while True:
            lid = _take()
            if lid is None:
                return
            try:
                _stagger(lid)
                run_one(lid)
            finally:
                with cv:
                    avail[0] += demand.get(lid, 0)
                    cv.notify_all()

    threads = [threading.Thread(target=_worker, daemon=True) for _ in range(workers)]
    for th in threads:
        th.start()
    for th in threads:
        th.join()

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
    static_order = sorted(slot, key=lambda lid: (-demand[lid], lid))
    static_make = 0.0
    static_sched: dict[str, tuple[float, float]] = {}
    t = 0.0
    for i in range(0, len(static_order), budget):
        wave = static_order[i:i + budget]
        wmax = max(dur[lid] for lid in wave)
        for lid in wave:
            static_sched[lid] = (t, t + dur[lid])
        t += wmax            # barrier: whole wave must finish before the next
    static_make = t

    # DYNAMIC: longest-job-first onto `budget` slots, no barrier.
    dyn_jobs = sorted(((lid, dur[lid]) for lid in slot), key=lambda x: x[1], reverse=True)
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
    for lid in sorted(sim["durations_s"], key=lambda x: sim["durations_s"][x], reverse=True):
        ss = s["schedule"].get(lid, (0, 0))[0] / 60
        ds = d["schedule"].get(lid, (0, 0))[0] / 60
        L.append(f"    {lid:38} dur {sim['durations_s'][lid]/60:5.1f}m   "
                 f"static start {ss:5.1f}m  →  dynamic start {ds:5.1f}m")
    return "\n".join(L)


# --------------------------------------------------------------------------- #
# full-run makespan DES — worker pool + VPC slots, baseline vs longest-first
# --------------------------------------------------------------------------- #
def _des(order: list[str], dur: dict, demand: dict, workers: int, budget: int):
    """Discrete-event list-schedule: dispatch jobs in ``order`` priority subject to
    ``workers`` concurrent slots and ``budget`` VPC slots; a job holds 1 worker +
    ``demand[job]`` VPC slots for ``dur[job]``. Returns (makespan, {job: start}).
    Scan-ahead is allowed (a ready low-demand job may pass a slot-blocked one) —
    standard non-blocking list scheduling."""
    import heapq
    pending = list(order)
    free_w, free_s = workers, budget
    running: list[tuple[float, str]] = []
    start: dict[str, float] = {}

    def dispatch(now: float):
        nonlocal free_w, free_s
        i = 0
        while i < len(pending):
            j = pending[i]
            if free_w >= 1 and free_s >= demand.get(j, 0):
                free_w -= 1
                free_s -= demand.get(j, 0)
                start[j] = now
                heapq.heappush(running, (now + dur.get(j, 0.0), j))
                pending.pop(i)
            else:
                i += 1

    dispatch(0.0)
    t = 0.0
    while running:
        t, j = heapq.heappop(running)
        free_w += 1
        free_s += demand.get(j, 0)
        dispatch(t)
    return t, start


def simulate_full(plan: dag_planner.Plan, durations: dict | None = None, *,
                  workers: int = 8, default: float = schedule_optimizer._DEFAULT_S) -> dict:
    """Project the FULL-run makespan under baseline (alphabetical, duration-blind)
    vs dynamic (longest-job-first) order, at a given effective ``workers``
    concurrency, holding the same VPC-slot budget. Isolates the effect of duration
    priority on when the heavy critical-path adopters start."""
    durations = durations if durations is not None else schedule_optimizer.load_durations()
    demand = _vpc_demand(plan)
    budget = max(1, plan.self_create_budget)
    nodes = list(plan.free) + list(plan.adopters) + list(plan.self_creators)
    dur = {n: schedule_optimizer.duration_of(n, durations, default) for n in nodes}
    crit = max(dur.values()) if dur else 0.0

    base_order = sorted(nodes)                                   # duration-blind
    dyn_order = sorted(nodes, key=lambda n: dur[n], reverse=True)  # longest-first
    base_make, base_start = _des(base_order, dur, demand, workers, budget)
    dyn_make, dyn_start = _des(dyn_order, dur, demand, workers, budget)
    return {
        "workers": workers, "budget": budget, "n_nodes": len(nodes),
        "critical_path_s": crit,
        "baseline_makespan_s": base_make, "dynamic_makespan_s": dyn_make,
        "saving_s": base_make - dyn_make,
        "baseline_start": base_start, "dynamic_start": dyn_start, "dur": dur,
    }


def format_full(sim: dict, watch: list[str] | None = None) -> str:
    L = [f"full-run makespan projection — workers={sim['workers']}, "
         f"vpc-budget={sim['budget']}, {sim['n_nodes']} nodes, "
         f"critical-path floor {sim['critical_path_s']/60:.1f} min"]
    b, d = sim["baseline_makespan_s"], sim["dynamic_makespan_s"]
    L.append(f"  BASELINE (duration-blind order): {b/60:6.1f} min")
    L.append(f"  DYNAMIC  (longest-job-first)   : {d/60:6.1f} min")
    L.append(f"  SAVING                         : {sim['saving_s']/60:6.1f} min "
             f"({100*sim['saving_s']/b:.0f}%)" if b else "")
    for lid in (watch or []):
        bs = sim["baseline_start"].get(lid)
        ds = sim["dynamic_start"].get(lid)
        if bs is not None:
            L.append(f"    {lid:30} dur {sim['dur'][lid]/60:5.1f}m  "
                     f"start {bs/60:5.1f}m → {ds/60:5.1f}m")
    return "\n".join(L)


def main(argv: list[str] | None = None) -> int:
    import argparse
    ap = argparse.ArgumentParser(
        description="Compare static-wave vs dynamic makespan (pure, no exec).")
    ap.add_argument("--service", help="restrict the leaf set to one service")
    ap.add_argument("--vpc-cap", type=int, default=None)
    ap.add_argument("--full", action="store_true",
                    help="also project FULL-run makespan (baseline vs longest-first) at --workers")
    ap.add_argument("--workers", type=int, default=8, help="effective concurrency for --full")
    args = ap.parse_args(argv)
    leaf = None
    if args.service:
        from regression.scenarios import validate_dag
        leaf = dag_planner._service_leaf_set(args.service, validate_dag._load_lifecycles())
    plan = dag_planner.plan(leaf_set=leaf, vpc_cap=args.vpc_cap)
    print(format_comparison(simulate_selfcreate(plan)))
    if args.full:
        print()
        watch = ["database-postgresql-cluster", "heavy-shared-dbaas",
                 "database-mysql-cluster", "vpc-peering"]
        print(format_full(simulate_full(plan, workers=args.workers), watch=watch))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
