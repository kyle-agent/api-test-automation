# Coverage session — handoff brief

> For a **separate session** whose job is to RAISE per-service SCP API test
> coverage, using the platform improvements landed on branch
> `claude/brave-edison-jbeqni`. Division of labor: **this session = platform
> improvements; the other session = coverage**. Raised coverage shows up on the
> dashboard.

## Goal
Per service, spawn a coverage agent that runs the service's lifecycles **LIVE**,
reads the resulting **per-API errors**, fixes/extends the lifecycles, re-runs, and
raises **measured** coverage — all while respecting the **shared account VPC cap**.

## What changed this session — the improved platform you'll use
1. **Safety gates default ON** (`core.config`). `SCP_ALLOW_MUTATIONS` /
   `SCP_ALLOW_DESTRUCTIVE` now default **true** — a live coverage run needs **no
   env flags**, just run. (Force read-only with `=false`.) Heavy/billable still
   needs `SCP_RUN_HEAVY=true` or a heavy selection. → no more `MutationBlocked`
   friction.
2. **VPC budget awareness — now a real platform feature** (`core.budgets`):
   - `python -m core.budgets` → table `kind / limit / live / free` (vpc cap **5**,
     live account count, free head-room) + a JSON line for parsing.
   - `core.budgets.live_count("vpc")` / `core.budgets.status()` — programmatic.
   - `core.budgets.CrossProcessSemaphore("vpc")` — file-backed, PID-reclaiming
     slot reservation **shared across processes/agents**. Acquire `n` slots up to
     `limit` before a VPC-consuming create; release on teardown. This is how
     concurrent per-service agents avoid blowing the cap.
   - Cap source: `DEFAULT_LIMITS` (vpc=5, VALIDATED) + env `SCP_BUDGET_LIMITS`.
3. **console2 cross-run admission + queue** (if you drive runs via the server):
   `POST /api/run {mode:"live", services:[svc]}` ADMITS the run if it fits the VPC
   cap, else QUEUES it (FIFO); `GET /api/capacity` shows used/free + running/
   queued. The server derives gates from the selection (mutations+destructive;
   heavy auto) and tears down per-run (owner-scoped). `POST /api/cleanup` is
   **blocked (409) while any run is in flight** — by design.
4. **Per-API error logs for diagnosis** — every live run records, PER STEP:
   method · path · status · request body · response snippet · error. Sources:
   - **console2 events**: `GET /api/runs/<id>/events` (`step-start`/`step-end`
     carry `params`/`req_body`/`resp_snippet`/`error`), or the log file
     `reports/console2-runs/<id>.log`. *Real example from this session:* a
     queueservice step 400'd with `"queue name must end with '.fifo'"` — the exact
     signal for what to fix to raise coverage.
   - **results store**: `reports/results/*.jsonl` (Observations/Findings via
     `core.results`), keyed by service · endpoint · status — the source the
     dashboard reads. Summarize with `python -m spec.summary` (live coverage) and
     `python -m spec.coverage_gap` (gaps/ceiling).
5. **Clean teardown verified**: a live single-service run (queueservice, 11 steps)
   was confirmed end-to-end — create → CRUD → teardown → **0 leftover**.

## The per-service coverage loop (per agent)
The repo already has a **`coverage-service`** agent type (one service each,
max-parallel, capped). For each assigned service `S`:

1. **Know your budget.** `python -m core.budgets`. If `vpc free` < what `S` needs
   (peak from console2 `POST /api/plan {services:[S]}` → `peak_vpcs`, or
   `regression.scenarios.catalog_run`), WAIT on `CrossProcessSemaphore("vpc")` or
   pick a non-VPC service. **Never exceed vpc cap 5 across ALL concurrent agents.**
2. **Run `S` live.** Either console2 `POST /api/run {mode:live, services:[S]}`
   (admission + teardown handled for you), or directly
   `SCP_CRUD_IDS=<S's lifecycle ids> pytest tests/crud -m crud` (gates already on).
   Heavy `S` → also `SCP_RUN_HEAVY=true`.
3. **Read the per-API result.** Run events/log + `reports/results/*.jsonl`. Find
   endpoints that 4xx'd or were never exercised, and **why** (the response detail).
4. **Raise coverage.** Edit `regression/scenarios/lifecycles/<S>.json` — fix
   bodies/params/sequencing per the error, close uncovered endpoints, mark
   `provenance: VALIDATED` on a real 2xx. Persist hard-won facts to `knowledge/`
   in the same commit (Hard Rule 7).
5. **Re-run `S`** until coverage stops rising. Verify clean: `python -m core.budgets`
   (vpc back to baseline). Do **NOT** run the account-wide reconciler while other
   agents are live.
6. **Reflect on the dashboard.** `core.results` already feeds it; refresh via the
   `dashboard` agent → publishes to the `dashboard-data` branch.

## Concurrency rules (shared account — read before parallelizing)
- **VPC cap 5 is SHARED across ALL agents.** Coordinate via
  `CrossProcessSemaphore("vpc")` (same run-id/`SCP_BUDGET_SEM_DIR`) or the console2
  admission queue. Don't free-run VPC creates in parallel.
- **Cleanup is per-run / owner-scoped.** The reconciler reaps ALL owned resources
  (`regr*` / `owner=apitest`) → **never run it while another agent's run is in
  flight** (it would delete their resources). Each run's own teardown is enough.
- CI's "one `api-test.yml` run at a time" still holds; local concurrent agents are
  fine **under the VPC budget**.

## Quick command reference
```bash
python -m core.budgets                          # VPC cap / live / free   (NEW)
python -m spec.summary                          # live coverage summary
python -m spec.coverage_gap                      # coverage ceiling + gaps
python tools/console2_server.py                  # console2: pick S -> live run -> events
SCP_CRUD_IDS=<lifecycle_ids> pytest tests/crud -m crud   # run S's lifecycles live
```

## Environment notes / gotchas
- Creds + region are injected as **env vars** (no `.env` file); `Scp-*` auth
  scheme; live calls work from this environment (verified `GET /v1/vpcs` → 200,
  current VPC 0/5).
- DELETE needs both gates — both default ON now, so a non-issue.
- `.env.example` is corrected (`Scp-*`, no stale `X-Cmp-*`).
- console2 simulate (`mode:"simulate"`) makes **no** cloud calls — use it to dry-run
  the order/closure for free before a live coverage run.
