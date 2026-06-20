# Handoff — 2026-06-20: scheduler v0.5 cutover LIVE-VALIDATED + path to 1.0

Branch `claude/handover-document-review-f7hpyc`. Continues the scheduler ADR
(`docs/decisions/2026-06-19-dependency-dag-test-scheduler.md`).

## What shipped this session (pushed, signed)
- **v0.5 primitive** `core.budgets.CrossProcessSemaphore` (file-backed + `fcntl.flock`,
  PID-liveness reclaim) + **engine wiring** (opt-in `SCP_VPC_SEMAPHORE`): a VPC
  self-create acquires a run-wide slot (≤ cap−shared), blocks instead of skipping,
  releases per VPC id. Offline: `tests/offline/test_budgets_semaphore.py` (7) +
  `test_engine_vpc_semaphore.py` (6, incl. multi-VPC + leak-on-exit invariant).
- **Workflow cutover** (`ci(api-test)`): enables the flag, runs ALL CRUD in ONE `-n 6`
  pool, **deletes the serial `regression-vpc-crud` job**, rewires sweep/dashboard.
- **`vpc-subnet-vip-nat` robustness fix** (this commit): `create-subnet-vip` now
  `retry_on_status:[400,409] retries:6` — see validation finding #1.

## LIVE validation (local heavy in the Claude-remote container, semaphore ON)
Ran the 6 VPC-self-creating lifecycles `-n 6` with a pre-provisioned shared VPC
(`SCP_VPC_SHARED_RESERVED=1`), exactly the cutover's shape.

**Result: 5 passed / 1 failed (22 min). The semaphore mechanism is sound:**
- VPC-CRUD lifecycles ran **in parallel** (peak **4** concurrent VPCs = shared 1 + self 3)
  vs the old serial lane (1-at-a-time).
- **Account cap (5) never breached** — the whole point.
- After teardown + reconciler sweep: **0 survivors** (verified across vpcs/subnets/
  tgw/servers/volumes/clusters).

**Parallelism surfaced 2 latent timing races (NOT semaphore bugs — masked by serial):**
1. **Readiness race** — `vpc-subnet-vip-nat` `create-subnet-vip` → 400
   `scp-network.subnet.not-active-state` even after `wait-subnet` polled ACTIVE
   (backend consistency lag under parallel load) → vip_id capture miss → vip group
   skip → lifecycle fail. **FIXED this commit** (retry the transient 400/409).
2. **Teardown survivors** — 2 self-created VPCs from *passed* lifecycles weren't torn
   down (related-resource 409 / async ordering, same class as the #125 baseline's
   `delete-* -> 404/409`). The **sweep job backstops** them (cleaned to 0 here). A
   deeper fix is to make own-DELETE teardown tolerate 404/409 idempotently
   (matches the code-review note) and/or `wait-*-gone` before parent delete.

## Baseline for reading any future run (so pre-existing ≠ regression)
Last completed baseline = **#125** (`27819913805`, main): **5 failed, 169 passed**.
The 5 are ALL adopt-class `gen-*` and environmental — ignore if they recur:
`gen-cloudml-image` (404, cloudml unconfigured), `gen-quick-query-validate` (500),
`gen-wave5-apigw-privatelink` ({pls_id} capture miss → 404), `gen-heavy-lb-members`
(static-nat 404), `gen-heavy-vs-netops` (interface 409). **VPC-CRUD-class passed in
#125** → a NEW VPC-CRUD failure or survivors>0 in a cutover run is the signal to scrutinize.

## Cutover status
- ADR **0.1 DONE**, **0.5 mechanism VALIDATED** (this run). Before flipping the flag in
  production, land: vip-nat retry (done) + accept sweep-backstops-survivors (or harden
  teardowns). The cutover commit is on this branch; **do not merge until a CI heavy run
  confirms** (the local run is strong evidence but CI is the gate the ADR named).
- The cross-process semaphore IS the "budgets throttle" 1.0 reuses — 0.5 is a real
  down-payment on 1.0, not just an interim.

## Path to 1.0 (dependency-DAG runner replacing xdist)
Target (ADR): leaf-set → transitive closure → auto-identify shared roots → provision
roots once → topological waves under the `core.budgets` semaphore. Incremental build:

1. **1.0-a — Complete & validate the dependency DAG (FIRST, offline, low-risk).**
   `regression/scenarios/dependencies.json` today only feeds quota + viz; the ADR's
   stated precondition is it be "accurate/complete enough to drive scheduling." Build a
   tool that derives, per lifecycle, its upstream resource deps (what it adopts vs
   self-creates, shared-root membership) from the composed lifecycles, and validates
   `dependencies.json` covers them (fail on gaps). Reuse `dashboard/gen_dep_map.py`
   (parent/depth DAG) + `data/api_catalog.json`. **Deliverable: a green `validate-dag`
   that proves the graph is complete.** Nothing can schedule on an incomplete graph.
2. **1.0-b — Closure + wave planner (offline, pure).** Given a leaf set (all / `--service`
   subset / a `suites/*.yaml`), compute the transitive closure, identify shared upstream
   resources automatically, emit a PLAN: shared roots to provision + ordered topological
   waves. No execution yet — unit-test the planner against known leaf sets. Reuse
   `composer.py` + the 1.0-a graph.
3. **1.0-c — DAG runner (the big build, behind a flag).** Execute a plan: provision shared
   roots, run each wave in a process pool throttled by `core.budgets`
   (`CrossProcessSemaphore` already built), collect junit. Run it ALONGSIDE xdist behind
   `SCP_DAG_RUNNER=true`, diff results against the xdist path on the same leaf set.
4. **1.0-d — Cutover.** Switch the workflow from xdist to the DAG runner on a validated
   heavy run; retire the `-n 6` xdist invocation. (Re-implement xdist's free junit
   distribution — flagged as a 1.0 cost in the ADR.)

**Why 1.0-a first:** it is the only step that gates all others (the ADR's #1 risk is an
incomplete graph), it is fully offline/testable, and it immediately tells us whether the
DAG approach is even viable on today's data. Start there.

## Dispatch note (unchanged)
Claude token cannot `workflow_dispatch` (403). Local heavy is viable from the
Claude-remote container (creds present, API reachable; 503s are transient backend flaps,
ride them with retries). Always pre-`python -m cleanup.reconciler` (5-VPC cap) and
post-sweep; SKE/image→snapshot→volume teardown chains need nodepool-first / image-first
ordering (learned this session).
