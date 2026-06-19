# Heavy-run wall-clock optimization (2026-06-19)

Source: log-optimizer analysis of run `27803424208` (141 min) + live evidence from
run `27811864234`. Full optimizer report: `/tmp/run-optimization.md` (ephemeral).

## Measured problem

Run 1 spent **103 min in the DB phase** that ideal parallelism does in ~11 min.
The DB engines ran largely serially despite `-n 6` and slowest-first ordering.

**Root cause (live-confirmed on run #124):** `-n 6` IS active, but
`engine.active_lifecycles()` ranked ALL `heavy` lifecycles at the same tier (0)
and tie-broke **alphabetically by id**. So the first 6 xdist workers grabbed the
alphabetically-first heavy lifecycles (`aimlops`, `archivestorage-*`, `backup-*`,
`baremetal`...) — NOT the long-pole DB/SKE clusters, which sort later (`d…`/`m…`)
and waited for a worker. Run #124 had only `mysql` of the 5 DB engines started 19
min into the CRUD pass.

## #1 — Applied: promote long-pole provisioners to the front (DONE)

`regression/scenarios/engine.py::active_lifecycles` — new `slowest_markers` tier
(rank 0) for the genuinely-longest provisioners (DB clusters incl. `-cluster-subops`,
`container-ske`, `baremetal`, `compute-virtualserver-full`, `heavy-shared-dbaas`,
`mngc-gpu-node`). They now occupy the first 16 slots, so the first `-n 6` workers
start 6 long-poles concurrently and the heavy phase becomes ~max(longest) per wave
instead of sum. Contained, offline-validated (validator 0 errors), low risk — only
changes the order lifecycles are handed to xdist, not which run.
**Expected saving: ~90 min.**

## #2 — Deferred (needs CI validation before merge): overlap smoke with CRUD

The ADOPT job gates the CRUD pass on the ~14-min read-only smoke finishing, even
though the shared VPC is ready ~14 min earlier and smoke (GET-only) is independent
of CRUD (creates). Split adopt-CRUD into a job that depends on `spec` (not smoke),
flowing the shared-VPC id via job outputs. **Expected saving: ~14 min.**
Risk: MEDIUM — restructures `.github/workflows/api-test.yml` job graph + VPC-id
output plumbing; cannot be validated without a live CI run, so do NOT blind-merge.

## #3 — Deferred (after #1): quota-aware unification of the VPC-CRUD lane

Today the VPC-CRUD lifecycles run in a SEPARATE serial job (~48 min) because each
self-creates its own VPC and parallel self-creates would blow the 5-VPC cap
(IB-047). It is not the critical path *today* (hidden under the 2 h ADOPT lane) —
**but once #1 lands and the ADOPT lane drops to ~35 min, the 48-min serial
VPC-CRUD lane becomes the new bottleneck.** Elegant fix: a quota-semaphore in
`core.budgets` that throttles concurrently-VPC-creating lifecycles to ≤(5 − shared)
inside ONE parallel pool, removing the separate job and letting VPC-CRUD run
2–3-wide (~48 → ~19 min). Risk: HIGH (scheduler change); design before implementing.

## Projected wall: 141 min → ~35 min (after #1+#2; #3 keeps it there post-#1).
