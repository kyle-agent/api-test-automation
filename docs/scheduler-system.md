# Dependency-DAG scheduler + self-learning optimizer

> Canonical overview of the scheduler subsystem under
> `regression/scenarios/` — the xdist replacement the ADR
> ([`docs/decisions/2026-06-19-dependency-dag-test-scheduler.md`](decisions/2026-06-19-dependency-dag-test-scheduler.md))
> set out, plus the self-learning optimizer that grew on top of it. This is the
> design doc; the modules' own docstrings carry the per-function detail.

## The vision: one dependency graph, four overlays

The old heavy run split lifecycles into two hard-coded xdist lanes — ADOPT (share
one VPC, parallel) and VPC-CRUD (self-create VPCs, serial) — because xdist
distributes tests but knows nothing about resource dependencies. The scheduler
replaces the lanes with **one dependency graph** and four overlays that all read
from it:

```
          Catalog ──► Plan ──► Run ──► Optimize
   (full resource    (cap-safe   (execute   (learn durations,
    closure +         topological  the plan)   re-derive critical
    lifecycle map)    waves)                    path + makespan)
```

- **Catalog** — the FULL resource model (`catalog_planner`, ~275 nodes / ~303
  `requires` edges): pick a resource node on the topology, get its dependency
  closure and the lifecycles that exercise it.
- **Plan** — the cap-aware wave scheduler (`dag_planner`): turn a leaf set of
  lifecycles into ordered, VPC-cap-safe execution waves.
- **Run** — the executor (`dag_runner` + `dag_runner_live`): run the waves, the
  free wave concurrently with the provision→adopt→self-create pipeline.
- **Optimize** — the self-learning layer (`schedule_optimizer` +
  `optimizer_report`): fold each run's measured durations into a store, re-derive
  the critical path, priorities and the optimal-vs-current makespan.

"Run only these services" is no longer a special case — it is just a smaller leaf
set / smaller DAG, scheduled the same way.

## Module map

| module | role |
|---|---|
| [`validate_dag.py`](../regression/scenarios/validate_dag.py) | **DAG-completeness gate (ADR 1.0-a).** Derives, per enabled lifecycle, its `adopt` edges (shared roots it reuses) and `self_creates` (capped kinds it provisions itself), and checks `dependencies.json` declares them exactly (`adopt_edges` + `shared_roots`) and that every VPC self-creator is listed in `vpc_schedule.vpc_crud_lifecycles`. `--check` is a CI gate (`.github/workflows/validate.yml`). |
| [`dependencies.json`](../regression/scenarios/dependencies.json) | **The DAG as data.** `shared_roots` (`vpc` parent of `subnet` / `subnet#db`) + `adopt_edges` (lifecycle → shared roots), plus the legacy quota/viz metadata. The single source of truth the planner reads. |
| [`catalog_planner.py`](../regression/scenarios/catalog_planner.py) | **Full-graph dependency brain.** Works on `composer.load_model()`: `closure(targets)`, `topo_layers` (topological CREATE order), `plan(targets) → CatalogPlan` (closure, layers, capped/shared/heavy annotations), and `lifecycles_for(targets)` mapping resource nodes → the lifecycles that exercise them (via `source.lifecycle`). |
| [`catalog_run.py`](../regression/scenarios/catalog_run.py) | **"Press execute on the topology."** The full chain: select a resource node → catalog closure → lifecycle leaf set → `dag_planner` waves → `dag_runner`. CLI `--target X`. |
| [`dag_planner.py`](../regression/scenarios/dag_planner.py) | **Cap-aware wave scheduler (ADR 1.0-b).** Pure offline. Turns a leaf set into ordered `Wave`s: `provision` (shared roots, parent-ordered) / `free` (VPC-independent, parallel) / `adopt` (all adopters parallel) / `self-create` (VPC-cap-sized waves). |
| [`dag_runner.py`](../regression/scenarios/dag_runner.py) | **Executor (ADR 1.0-c).** Execution-agnostic `run_plan`: the free wave runs CONCURRENTLY with the provision→adopt→self-create pipeline; a ThreadPool per wave. Credential-free `--dry-run`. |
| [`dag_runner_live.py`](../regression/scenarios/dag_runner_live.py) | **Live adapters.** `build(plan)` wires the executor (`engine.run_lifecycle` per lifecycle, fresh client per thread) + the shared-VPC `SharedInfraProvisioner`, behind `SCP_ALLOW_MUTATIONS`. A shared thread-safe `_SharedBudget` coordinates capped-kind quotas across concurrent threads. |
| [`dag_diff.py`](../regression/scenarios/dag_diff.py) | **Parity gate (ADR 1.0-d).** Maps a pytest-xdist JUnit XML and a `dag_runner` RunResult both to `lifecycle_id → status` and diffs them; exits 1 on any disagreement. |
| [`schedule_optimizer.py`](../regression/scenarios/schedule_optimizer.py) | **Self-learning algorithm.** Rolling-average duration store (`data/optimizer/durations.json`, fed each run), `critical_path` / `tail_lengths` (longest duration-weighted chain = wall-time floor + priority), and `schedule()` — greedy critical-path list-scheduling under the VPC-slot constraint → estimated makespan. |
| [`optimizer_report.py`](../regression/scenarios/optimizer_report.py) | **Actionable report.** Resource critical path, longest-tail-first run priority, VPC self-creators to start first, optimal-vs-current makespan + implied time saved. Pure/offline. |
| [`dag_plan_graph.py`](../regression/scenarios/dag_plan_graph.py) | **Topological preview.** Renders a `dag_planner.Plan` as a self-contained SVG/HTML layout (provision/free/adopt/self-create bands, parent + dashed adopt edges). |

## The data

Two files carry the scheduler's state; everything else is derived live.

- **`regression/scenarios/dependencies.json`** — the dependency DAG.
  - `shared_roots` — `{root: {parent, ...}}`: `vpc` (parent `null`), `subnet` and
    `subnet#db` (parent `vpc`). The parent chain orders provisioning (parent
    before child).
  - `adopt_edges` — `{lifecycle_id: [roots…]}`: which shared roots each lifecycle
    reuses. These are the DAG edges.
  - `self_creates` is **NOT** stored — it is derived live by `validate_dag.derive`
    (a `POST` to a `budget_paths` path with no `adopt` of that same kind), so a
    lifecycle's slot demand can never drift out of sync with its steps.
  - The legacy `quota_kinds` / `vpc_schedule` / `prerequisites` sections (quota
    accounting + the pre-cutover two-lane filters) are left in place and untouched.
  - `validate_dag --check` is the guard that keeps `adopt_edges` + `shared_roots`
    exactly matching the composed lifecycles. Regenerate intent: after adding or
    removing an `adopt` step, run the check and bring the JSON back into agreement.

- **`data/optimizer/durations.json`** — the learned duration store. Per node:
  `{avg_s, n, last_s}` — a rolling average over `n` runs. `dag_runner` folds each
  live run's measured per-lifecycle wall-times in via
  `schedule_optimizer.update_durations(measured_from_result(result))`. An unseen
  node falls back to a 30 s default until its first measurement lands.

## The chain (select → closure → waves → execute → learn)

```
  pick resource node(s) on the topology        catalog_run --target ske-cluster
    → catalog_planner.closure(targets)          (vpc, subnet, security-group, …)
    → lifecycles_for(closure)                   source.lifecycle → runnable leaf set
    → dag_planner.plan(leaf_set)                cap-safe topological waves
    → dag_runner.run_plan(plan, executor)       free ∥ (provision→adopt→self-create)
    → schedule_optimizer.update_durations(...)  learn measured wall-times
    → optimizer_report                          re-derive critical path + makespan
```

Selecting `ske-cluster` therefore runs exactly the lifecycles that stand up + test
ske-cluster and its transitive closure (vpc/subnet, security-group, keypair,
filestorage-volume, …), in a VPC-cap-safe order — the same `dag_runner` that was
parity-validated against pytest-xdist.

## The cap-safety guarantee (structural, not runtime)

The account VPC cap (5) and private-dns cap (3) are the scarce, billable resources.
Cap-safety here is **structural** — it falls out of how the planner *sizes* the
waves, not out of a runtime race-check:

1. The session-shared VPC (provisioned in wave 0) holds exactly **one** slot for
   the whole run, so the self-create budget is `vpc_cap - shared_vpc_count` (default
   `5 - 1 = 4`).
2. **Adopters** reuse the shared VPC — they consume **no** new VPC slot and run as
   one parallel wave.
3. **Self-creators** that provision a `vpc` are greedy-packed into back-to-back
   `self-create` waves of at most `vpc_cap - shared_vpc_count` slots each. Running
   a wave fully parallel can therefore *never* exceed the cap. A self-creator that
   provisions only `private-dns` consumes 0 VPC slots and rides along.
4. The **free** wave (leaves that neither adopt nor self-create) touches no shared
   root and no capped kind, so it runs fully parallel, concurrently with the rest.

This is the static analogue of the v0.5 runtime cross-process VPC semaphore. The
shared, thread-safe `_SharedBudget` in `dag_runner_live` is kept as
defense-in-depth: it lets the engine's own per-create reservations of capped kinds
coordinate across the concurrent executor threads, so an over-cap create *skips*
(reserve → False) rather than erroring.

## The self-learning loop

```
   run ──► measured wall-times ──► durations.json (rolling avg)
     ▲                                    │
     └──── re-derived critical path  ◄─────┘
           + priorities + makespan
```

Each live run feeds `data/optimizer/durations.json`. From the graph + the learned
durations + the cap, `schedule_optimizer` derives:

- **critical path** — the longest duration-weighted chain through the dependency
  graph; its length is the wall-time floor (nothing can finish sooner).
- **priority (tail-length)** — for each node, the longest remaining duration on any
  path from it; scheduling longest-tail-first (critical-path / LPT order) minimises
  makespan.
- **a cap-feasible schedule + estimated makespan** — greedy list scheduling that
  dispatches the highest-priority ready node whenever a VPC slot is free.

It is self-updating: add a new service (node) and the next plan re-derives the
critical path, priorities and makespan automatically. `optimizer_report` renders
all of this as an actionable report (and always labels the numbers as learned
averages, approximate until more runs land).

## Measured results (full live run, 2026-06-20)

From this session's full live run of all 184 enabled lifecycles:

- **1.0-d parity** — the DAG runner matched pytest-xdist exactly on a validation
  leaf set (`vpc-endpoint` + 2 self-creators all passed on both sides).
- **Full run** — 157 passed / 24 failed / 3 skipped; **peak VPC 5/5** (cap held,
  never breached).
- **Failure analysis** — the failures are **NOT** dependency-ordering: lifecycles
  are self-contained (each builds its own dependency chain in order and deletes in
  reverse). ~10–12 were concurrency-adjacent (503 upstream-gateway + a private-dns
  quota race); the rest were individual per-service backend issues (404
  unconfigured / 400 validation / 403 / 500), many already in the #125 baseline.
  Fixes applied: a stronger 503 retry (`SCP_MAX_RETRIES`) + the shared thread-safe
  budget so private-dns/VPC quotas coordinate across concurrent threads.
- **Makespan (learned wall-times)** — the current `dag_planner` sequential-wave
  schedule is ~**93.5 min** (matches the observed run); the optimal cap-aware
  overlap is ~**49.4 min** → a ~**47%** win, achievable by overlapping the
  self-create wave with the adopt wave (free is already concurrent). The critical
  path / long-pole is `gen-heavy-aimlops` (~49 min) + the DB clusters (~42–46 min),
  **NOT** private-dns (~23 min) — a correction the measured data produced.

## CLI entrypoints

```bash
# DAG completeness — the CI gate (also wired in .github/workflows/validate.yml)
python -m regression.scenarios.validate_dag --check          # exit 1 on any gap
python -m regression.scenarios.validate_dag --verbose        # report every edge

# Catalog plan over the full resource graph
python -m regression.scenarios.catalog_planner --target ske-cluster

# Press execute on the topology (dry-run unless SCP_DAG_RUNNER=true)
python -m regression.scenarios.catalog_run --target ske-cluster
python -m regression.scenarios.catalog_run --target ske-cluster --dry-run

# Offline wave plan / live execution (live needs SCP_DAG_RUNNER=true + safety gates)
python -m regression.scenarios.dag_planner                    # dry plan, all leaves
python -m regression.scenarios.dag_planner --service vpc
python -m regression.scenarios.dag_runner --dry-run
python -m regression.scenarios.dag_runner --service vpc       # live behind SCP_DAG_RUNNER

# Parity gate against xdist
python -m regression.scenarios.dag_diff --junit run.xml --runresult run.json

# Live run + DAG-run dashboard + adaptive concurrency (writes ./dag-run.html)
python tools/dag_run_live.py ALL              # full plan, AIMD on by default
python tools/dag_run_live.py ske-cluster      # a target's closure only

# Self-learning optimizer report
python -m regression.scenarios.optimizer_report
python -m regression.scenarios.optimizer_report --json

# Topological SVG/HTML plan preview
python -m regression.scenarios.dag_plan_graph -o plan.html
```

The live runner refuses to build its adapters unless `SCP_ALLOW_MUTATIONS=true`
(plus `SCP_ALLOW_DESTRUCTIVE=true` for teardown, `SCP_RUN_HEAVY=true` for heavy
lifecycles) — CLAUDE.md Hard Rule 1. The planner, report, diff and graph paths are
pure/offline and never need credentials.

## Adaptive concurrency (AIMD) — finding the sustainable parallelism

`dag_runner_live.AdaptiveLimiter` (gated by `SCP_ADAPTIVE=true`) self-tunes live
concurrency to whatever the gateway sustains: every `SCP_ADAPTIVE_INTERVAL`
seconds it reads `core.http_client.retry_status_count()` (cumulative 502/503/504)
and either **halves** the limit (any new transient since last check, floor
`SCP_ADAPTIVE_MIN`) or **probes +1** (healthy, ceiling = `max_workers`). Lifecycles
`acquire()` a slot before running, so live concurrency *is* the current limit and
**converges to the sustainable level — watching where it settles is how we find the
optimal concurrency.** Unit-tested offline: `tests/offline/test_adaptive_limiter.py`.

Run the experiment (live dashboard at `./dag-run.html`, AIMD on by default):

```bash
# start mid, probe up to a high ceiling, back off on gateway 503s
python tools/dag_run_live.py ALL
# sweep a different envelope:
SCP_ADAPTIVE_START=8 SCP_ADAPTIVE_MIN=4 CATRUN_MAX_WORKERS=24 \
  SCP_ADAPTIVE_INTERVAL=15 python tools/dag_run_live.py ALL
```

The dashboard surfaces the live `adaptive limit / ceiling`, the `503/502/504`
counter, and a sparkline of the limit over time. **Default envelope:** start=10,
min=4, ceiling=20, interval=15s.

> **Status (2026-06-20):** harness wired + verified; AIMD probed 10→11 cleanly with
> 503=0 in an early window before the run was stopped. The settling point (=
> optimal concurrency) is **not yet measured** over a full run — that's the next
> session's job (see Open items). Note: shared-root provisioning (`vpc → subnet,
> subnet#db`) is a ~7–8 min serial prelude before any wave runs — the SCP subnets
> sit in `CREATING` a long time — so budget for it when reading the trajectory.

## Open items

- **Adaptive sweet-spot, full run** — let `tools/dag_run_live.py ALL` run to
  completion with AIMD on and record where `limit` settles (and whether it
  oscillates against a 503 ceiling). That settling value is the optimal
  `max_workers`. Re-run across a couple of `SCP_ADAPTIVE_START`/ceiling envelopes to
  confirm it's stable.
- **Self-create ∥ adopt overlap** — the ~93 → ~49 min win. The current planner runs
  the adopt wave and the self-create waves back-to-back in the pipeline; overlapping
  them (the cap permits it once the shared VPC's slot is accounted for) is the
  remaining makespan saving.
- **Cutover** — retiring pytest-xdist for the live runner needs a flag-on CI heavy
  run (peak VPC ≤ 5, 0 survivors) to validate before it goes to production.

See also: the ADR
[`docs/decisions/2026-06-19-dependency-dag-test-scheduler.md`](decisions/2026-06-19-dependency-dag-test-scheduler.md)
and [`docs/run-parallelism-optimization.md`](run-parallelism-optimization.md).
