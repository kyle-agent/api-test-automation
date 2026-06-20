# Dependency-DAG test scheduler (replacing the xdist 2-lane split)

**Date:** 2026-06-19
**Status:** Accepted (target architecture; incremental adoption)
**Deciders:** owner (kchoic)

## Context
The heavy run schedules lifecycles with a flat slowest-first sort over pytest-xdist,
split into two hard-coded lanes — ADOPT (share one VPC, parallel) and VPC-CRUD
(self-create VPCs, serial) — purely because xdist distributes tests to workers but
knows nothing about resource dependencies. This hard-codes a single shared root
(one VPC) and breaks down for the upcoming work: (a) multi-service *combination*
tests, where the shared upstream resources differ per case and can't be found
through fixed lanes, and (b) **selective execution** — running only a chosen subset
of services, or a named suite. (The same blind spot caused DB clusters to serialize:
the sort buried the long-poles behind alphabetically-earlier heavy lifecycles.)

## Decision
Adopt a **dependency-DAG-driven scheduler** as the target: from a chosen set of leaf
test targets (all, a specific-service subset, or a `suites/*.yaml` suite), compute the
transitive dependency closure, identify shared upstream resources (VPC/subnet/…)
automatically, provision shared roots once, then fan out dependents in topological
waves with large `-n`, throttled by a `core.budgets` quota semaphore — so the lane
concept disappears and "run only these services" is just a smaller leaf set / smaller
DAG. Adopt incrementally:
- **0.1** long-pole-first ordering heuristic — DONE (commit `6399186b`)
- **0.5** quota-aware unification of the VPC-CRUD lane (`core.budgets` VPC semaphore;
  removes the separate serial job) — IN PROGRESS: (1) the enabling primitive landed
  (`core.budgets.CrossProcessSemaphore`, file-backed + `fcntl.flock`, PID-liveness
  reclaim, offline multi-process tests); (2) the engine is wired (opt-in
  `SCP_VPC_SEMAPHORE`: a VPC self-create acquires/blocks/releases a cross-process
  slot per VPC id, throttle-skips on timeout, offline-validated incl. multi-VPC).
  Remaining: the `.github/workflows/api-test.yml` cutover (enable the flag, drop the
  `regression-vpc-crud` serial job, fold `VPC_CRUD_K` into the parallel pool) — drafted
  but needs a flag-on heavy run to validate (peak concurrent VPCs ≤ 5, 0 survivors)
  before merge; do NOT blind-merge (see `docs/working/trackers/run-parallelism-optimization.md` #3).
- **1.0** custom DAG runner replacing xdist (leaf-set → closure → topological waves →
  budgets throttle), consuming `suites/*.yaml` / `--service` selections as the leaf set

## Alternatives Considered

| Option | Reason Rejected |
|--------|----------------|
| Keep the 2-lane xdist split + ordering heuristics | Doesn't generalize to multi-service combos or selective/suite runs; shared-resource identification stays hard-coded/manual |
| Provision 5 VPCs upfront / per-test VPC fan-out | Bottleneck is worker count + schedule order, not VPC capacity (proven: 3 DB clusters ran concurrently in one subnet); adds 5-cap-cascade risk for ~0 critical-path gain |
| Pure flat slowest-first sort (status quo) | Alphabetical tie-break buries long-poles; zero shared-resource awareness |

## Consequences

**Good:**
- Shared resources auto-derived from the graph per case → no lanes, no per-case manual wiring
- Selective execution is first-class: a specific-service subset or a `suites/*.yaml` suite is just the leaf set; its closure is a smaller DAG scheduled the same way
- Generalizes cleanly to multi-service combination tests (the graph just grows)
- Parallelism bounded only by real dependencies + quota, not by a coarse lane split
- Reuses existing pieces: `regression/scenarios/dependencies.json`, `dashboard/gen_dep_map.py` (parent/depth DAG, today visualization-only), `regression/scenarios/composer.py`, `core.budgets`, `suites/*.yaml`

**Bad / Constraints:**
- 1.0 replaces pytest-xdist → significant build; loses xdist's free test distribution + junit reporting (must be re-implemented)
- Requires the dependency graph in `dependencies.json` to be accurate/complete enough to drive scheduling (today it only feeds quota + viz)
- The quota semaphore must be correct or it reintroduces the 5-VPC-cap race it's meant to prevent
- Interim states (0.1/0.5) are heuristics, not the full model — don't mistake them for the destination

## Override Conditions
Revisit if multi-service combination tests and selective/suite runs don't materialize
(then the 2-lane split is adequate and the 1.0 runner isn't worth the build), or if
pytest-xdist gains dependency-aware scheduling, or if `dependencies.json` proves too
incomplete to drive a real scheduler. Cross-link: `docs/working/trackers/run-parallelism-optimization.md`.

## As-built (2026-06-20)

The full 1.0 runner shipped, and the work went *beyond* the original ADR with a
full-graph catalog layer and a self-learning optimizer. Canonical overview:
[`docs/scheduler-system.md`](../scheduler-system.md).

**Shipped:**
- **0.1** long-pole-first ordering heuristic — DONE (per above).
- **0.5** cross-process VPC semaphore (`core.budgets`) — primitive + engine wiring
  landed and the workflow cutover (drop the serial `regression-vpc-crud` job, fold
  `VPC_CRUD_K` into the parallel pool) was VALIDATED. The wave structure is now the
  primary cap guard; the semaphore stays as defense-in-depth.
- **1.0-a** DAG-completeness gate — `regression.scenarios.validate_dag` derives
  `adopt_edges` + `self_creates` from the composed lifecycles and proves
  `dependencies.json` (`adopt_edges` + `shared_roots`) is a complete DAG;
  `--check` is wired as a CI gate in `.github/workflows/validate.yml`.
- **1.0-b** offline planner — `dag_planner` turns a leaf set into cap-safe
  topological waves (provision / free / adopt / VPC-cap-sized self-create). Pure,
  credential-free.
- **1.0-c** executor — `dag_runner` (orchestration core) + `dag_runner_live` (the
  engine/shared-VPC adapters, behind `SCP_ALLOW_MUTATIONS`). The free wave runs
  concurrently with the provision→adopt→self-create pipeline; a shared thread-safe
  `_SharedBudget` coordinates capped-kind quotas across threads.
- **1.0-d** parity — `dag_diff` compares the runner's pass/fail against the
  pytest-xdist JUnit; the DAG runner matched xdist exactly on a validation leaf set
  (`vpc-endpoint` + 2 self-creators, all passed both sides).

**Beyond the ADR:**
- **Catalog full-graph generalization** — `catalog_planner` plans over the WHOLE
  resource model (~275 nodes / ~303 `requires` edges), and `catalog_run` makes
  "press execute on a topology node" (`--target X`) the entry point: node → closure
  → lifecycle leaf set → planner → runner. `dag_plan_graph` previews any plan as a
  topological SVG/HTML.
- **Self-learning optimizer** — `schedule_optimizer` keeps a rolling per-node
  duration store (`data/optimizer/durations.json`, fed each run by `dag_runner`),
  and re-derives the critical path (wall-time floor), longest-tail-first priority
  and a cap-aware greedy makespan; `optimizer_report` renders the actionable view.

**Measured (full live run of all 184 enabled lifecycles):** 157 passed / 24 failed
/ 3 skipped, **peak VPC 5/5 (cap never breached)**. Failures were NOT
dependency-ordering — lifecycles are self-contained; ~10–12 were concurrency-adjacent
(503 + a private-dns race, addressed by stronger 503 retry + the shared budget), the
rest individual per-service backend issues (many in the #125 baseline). The learned
critical path is `gen-heavy-aimlops` (~49 min) + the DB clusters (~42–46 min), not
private-dns — a correction the measured data produced.

**Open items:**
- **Self-create ∥ adopt overlap** — the current planner runs adopt and self-create
  waves back-to-back; overlapping them is the remaining ~93 min → ~49 min (~47%)
  makespan win.
- **Cutover** — retiring xdist for the live runner in production still needs a
  flag-on CI heavy run (peak VPC ≤ 5, 0 survivors) to validate before the cutover
  commit merges. Do NOT blind-merge.
