"""dag_runner — scheduler ADR 1.0-c: execute a dag_planner Plan, the xdist
replacement (first cut, behind SCP_DAG_RUNNER).

The planner (1.0-b) already groups lifecycles into cap-sized waves, so cap-safety
is STRUCTURAL here: the runner provisions the shared roots once, then executes
each wave — waves strictly in order, lifecycles WITHIN a wave concurrently. A
self-create wave is pre-sized to ``vpc_limit - shared`` VPC slots, so running it
fully parallel can never exceed the account VPC cap. (The v0.5 cross-process
semaphore in core.budgets remains available as defense-in-depth for the engine's
own per-create reservation, but the wave structure is the primary guard.)

This module is execution-agnostic: it orchestrates a Plan given an ``executor``
(runs one lifecycle by id -> LifecycleOutcome) and an optional ``provisioner``
(stands the shared roots up / tears them down). The LIVE adapters that wire the
real engine + shared_infra live in ``dag_runner_live`` and are imported lazily so
this module — and its ``--dry-run`` — never need credentials. Pure orchestration
is unit-tested offline with fakes (tests/offline/test_dag_runner.py).

Cutover plan: run this ALONGSIDE pytest-xdist behind ``SCP_DAG_RUNNER=true`` and
diff the pass/fail sets per leaf set (dag_diff) before retiring xdist (1.0-d).
"""
from __future__ import annotations

import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from typing import Callable, Protocol, runtime_checkable

from regression.scenarios import dag_planner


@dataclass
class LifecycleOutcome:
    """Result of executing (or planning) one lifecycle."""
    lifecycle_id: str
    status: str                      # 'passed' | 'skipped' | 'failed' | 'planned'
    reason: str | None = None
    duration_s: float = 0.0


@dataclass
class WaveResult:
    kind: str                        # 'provision' | 'adopt' | 'self-create'
    outcomes: list[LifecycleOutcome] = field(default_factory=list)
    duration_s: float = 0.0


@dataclass
class RunResult:
    waves: list[WaveResult] = field(default_factory=list)
    shared_roots: list[str] = field(default_factory=list)
    provision_error: str | None = None

    @property
    def outcomes(self) -> list[LifecycleOutcome]:
        return [o for w in self.waves for o in w.outcomes]

    def by_status(self) -> dict[str, int]:
        counts: dict[str, int] = {}
        for o in self.outcomes:
            counts[o.status] = counts.get(o.status, 0) + 1
        return counts

    @property
    def ok(self) -> bool:
        """True iff nothing failed and provisioning succeeded."""
        return self.provision_error is None and not any(
            o.status == "failed" for o in self.outcomes)


# An executor runs ONE lifecycle by id and returns its outcome. It must NOT raise
# on a lifecycle failure — it converts a raised engine error into status='failed'.
Executor = Callable[[str], LifecycleOutcome]


@runtime_checkable
class Provisioner(Protocol):
    """Stands the shared roots up before the waves and tears them down after.
    provision() should make the shared ids discoverable by the executor (e.g. set
    SCP_SHARED_VPC_ID/SUBNET_ID env, as the workflow + conftest do)."""
    def provision(self) -> None: ...
    def teardown(self) -> None: ...


def run_plan(
    plan: dag_planner.Plan,
    executor: Executor,
    *,
    provisioner: Provisioner | None = None,
    max_workers: int | None = None,
    on_event: Callable[[str, dict], None] | None = None,
) -> RunResult:
    """Execute a plan: provision shared roots, run each non-provision wave (in
    order; lifecycles within a wave concurrently), then tear roots down.

    Cap-safety is structural — each wave is pre-sized by the planner. ``executor``
    must be side-effect-isolated per lifecycle and must not raise.

    ``on_event(kind, payload)`` (optional) fires progress events for live
    observability: 'provision_start'/'provision_done', 'wave_start'/'wave_done',
    'lifecycle_done' (per lifecycle, may fire concurrently — the callback must be
    thread-safe), and 'teardown_done'. It must never raise.
    """
    import threading

    def emit(kind: str, payload: dict) -> None:
        if on_event is not None:
            try:
                on_event(kind, payload)
            except Exception:  # noqa: BLE001 — observability must not break the run
                pass

    _emit_lock = threading.Lock()

    result = RunResult(shared_roots=list(plan.shared_roots))

    if provisioner is not None:
        emit("provision_start", {"roots": list(plan.shared_roots)})
        try:
            provisioner.provision()
        except Exception as exc:  # provisioning failure aborts the run cleanly
            result.provision_error = f"{type(exc).__name__}: {exc}"
            emit("provision_done", {"error": result.provision_error})
            return result
        emit("provision_done", {})

    def _run_one(lid: str) -> LifecycleOutcome:
        outcome = executor(lid)
        with _emit_lock:
            emit("lifecycle_done", {
                "lifecycle_id": outcome.lifecycle_id, "status": outcome.status,
                "reason": outcome.reason, "duration_s": outcome.duration_s})
        return outcome

    try:
        for idx, wave in enumerate(plan.waves):
            if wave.kind == "provision":
                # roots are stood up by the provisioner, not executed as lifecycles
                result.waves.append(WaveResult(kind="provision",
                                               outcomes=[], duration_s=0.0))
                continue
            ids = list(wave.lifecycles)
            emit("wave_start", {"index": idx, "kind": wave.kind, "lifecycles": ids})
            t0 = time.monotonic()
            workers = max_workers or max(1, len(ids))
            if workers == 1 or len(ids) <= 1:
                outcomes = [_run_one(i) for i in ids]
            else:
                with ThreadPoolExecutor(max_workers=workers) as pool:
                    # preserve input order in the results
                    outcomes = list(pool.map(_run_one, ids))
            wr = WaveResult(kind=wave.kind, outcomes=outcomes,
                            duration_s=time.monotonic() - t0)
            result.waves.append(wr)
            emit("wave_done", {"index": idx, "kind": wave.kind,
                               "duration_s": wr.duration_s})
    finally:
        if provisioner is not None:
            try:
                provisioner.teardown()
            except Exception:  # noqa: BLE001 — teardown is best-effort
                pass
            emit("teardown_done", {})
    return result


def dry_run(plan: dag_planner.Plan) -> RunResult:
    """Produce a RunResult that mirrors what WOULD execute (status='planned'),
    without provisioning or running anything. Safe + credential-free."""
    waves = []
    for wave in plan.waves:
        if wave.kind == "provision":
            # roots are provisioned, not executed as lifecycles — mirror run_plan
            # so dry_run's planned count == what a live run would execute.
            waves.append(WaveResult(kind="provision", outcomes=[]))
            continue
        outs = [LifecycleOutcome(i, "planned") for i in wave.lifecycles]
        waves.append(WaveResult(kind=wave.kind, outcomes=outs))
    return RunResult(waves=waves, shared_roots=list(plan.shared_roots))


def format_run(result: RunResult) -> str:
    L = [f"shared roots: {', '.join(result.shared_roots) or '(none)'}"]
    if result.provision_error:
        L.append(f"❌ provisioning failed: {result.provision_error}")
        return "\n".join(L)
    for i, w in enumerate(result.waves):
        if w.kind == "provision":
            L.append(f"  wave {i} [provision] {', '.join(result.shared_roots)}")
            continue
        tag = "" if all(o.status == "planned" for o in w.outcomes) else f" ({w.duration_s:.1f}s)"
        L.append(f"  wave {i} [{w.kind}]{tag} {len(w.outcomes)} lifecycle(s)")
        for o in w.outcomes:
            mark = {"passed": "·", "skipped": "○", "failed": "✗", "planned": "→"}.get(o.status, "?")
            extra = f"  {o.reason}" if o.reason else ""
            L.append(f"      {mark} {o.lifecycle_id} [{o.status}]{extra}")
    counts = result.by_status()
    L.append("summary: " + ", ".join(f"{k}={v}" for k, v in sorted(counts.items())))
    return "\n".join(L)


def main(argv: list[str] | None = None) -> int:
    import argparse
    import os

    ap = argparse.ArgumentParser(description="Execute a dependency-DAG plan (xdist replacement, behind SCP_DAG_RUNNER).")
    ap.add_argument("--service", help="restrict the leaf set to one service")
    ap.add_argument("--vpc-cap", type=int, default=None, help="override the account VPC cap")
    ap.add_argument("--max-workers", type=int, default=None, help="cap concurrency within a wave")
    ap.add_argument("--dry-run", action="store_true", help="print the execution plan; do not run (default unless SCP_DAG_RUNNER=true)")
    args = ap.parse_args(argv)

    leaf = None
    if args.service:
        from regression.scenarios import validate_dag
        leaf = dag_planner._service_leaf_set(args.service, validate_dag._load_lifecycles())
        if not leaf:
            print(f"no enabled lifecycle matches service '{args.service}'")
            return 2
    plan = dag_planner.plan(leaf_set=leaf, vpc_cap=args.vpc_cap)

    live = os.environ.get("SCP_DAG_RUNNER") == "true" and not args.dry_run
    if not live:
        print(format_run(dry_run(plan)))
        return 0

    # live path: lazily import the credential-bearing adapters
    from regression.scenarios import dag_runner_live
    executor, provisioner = dag_runner_live.build(plan, max_workers=args.max_workers)
    result = run_plan(plan, executor, provisioner=provisioner, max_workers=args.max_workers)
    print(format_run(result))
    return 0 if result.ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
