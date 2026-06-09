# Session handoff — parallel-adopt CRUD re-architecture

State for continuing in a new session. Everything below is **already merged to
`main`** (PRs #49–#52) unless marked PENDING.

## What changed (all on main)

| PR | What |
|----|------|
| #49 | **Shared-infra parallel CRUD** (replaces the old serial lanes) + read-coverage lifecycles (ske/scr/data-analytics/database) + unset-backup 401 retry, subnet CIDR self-containment, mysql set-backup / quick-query fixes. xdist per-worker result shards + merge. |
| #50 | **VPC hard cap = 3**; **unique non-overlapping VPC CIDRs**; **subnet ⊂ VPC CIDR**; `knowledge/domain-constraints.md`; workflow tears down the shared VPC BEFORE the serial VPC-CRUD pass. |
| #51 | **Standalone PORT lifecycle** in `compute-virtualserver-full` (create port → map SG → attach NIC → detach → delete; port CRUD on the `vpc` service) + `knowledge/resource-dependencies.md`. |
| #52 | **Fix**: shared-infra provision must not leak engine stdout into `$GITHUB_ENV` (this broke run #39). |

## Architecture (how the CRUD job runs now)

Single AXIS1 job, `timeout-minutes: 300`. CRUD phase steps (opt-in):
1. **Derive lane filters** — `shared_infra.py --print-filters` → `ADOPT_K / VPC_CRUD_K / PARALLEL_K` (from `dependencies.json:vpc_schedule`).
2. **Provision shared VPC + subnet** — `shared_infra.py --provision`; appends `SCP_SHARED_VPC_ID/SCP_SHARED_SUBNET_ID` to `$GITHUB_ENV` (only `^SCP_SHARED_*=` lines).
3. **ADOPT-class CRUD (parallel)** — `pytest tests/crud -m crud -n 6 -k "$PARALLEL_K"`. These ADOPT the shared VPC+subnet (env ids) and create only their own child → safe in parallel. 117 lifecycles.
4. **Teardown shared VPC** (before VPC-CRUD) — frees the slot so the serial pass gets the full 3-VPC budget.
5. **VPC-CRUD class (serial)** — `pytest ... -n 0 -k "$VPC_CRUD_K"`. 7 lifecycles that self-create a VPC/subnet/peer.
6. **Merge per-worker results** → canonical `observations.jsonl` + `junit-crud.xml`.
7. **Teardown shared VPC** (always, backstop).

Classification (dependencies.json): **ADOPT (10)** ske, virtualserver, mysql, postgresql, heavy-shared-dbaas, privatelink, endpoint, lb-nat, vpn, direct-connect. **VPC-CRUD (7)** networking-vpc-subnet, vpc-internet-gateway, vpc-peering, vpc-transit-gateway-children, heavy-shared-networking, vpc-cidr-secondary, vpc-subnet-vip-nat. Partition: 117 + 7 = all 124 enabled, no overlap.

### Key files
- `regression/scenarios/engine.py` — `provision_shared_vpc` (VPC+subnet, env-aware), `_ADOPT_SHARED`, adopt-skip in `run_lifecycle`.
- `regression/scenarios/shared_infra.py` — CLI: `--provision` / `--teardown` / `--print-filters`.
- `conftest.py` — `shared_vpc` fixture (xdist-safe: env-adopt / no per-worker provision / single-process provision).
- `core/results.py` — per-xdist-worker shards + `merge_worker_shards()`.
- `core/budgets.py` — `DEFAULT_LIMITS["vpc"]=3`.
- `knowledge/domain-constraints.md` — VPC=3, CIDR rules, the per-lifecycle `/20` allocation table.
- `knowledge/resource-dependencies.md` — creation-order DAG + attach/detach patterns + coverage/gap table.

## PENDING / next steps

1. **RE-RUN full heavy CRUD on `main`** (Actions → "SCP API Test (orchestrator)" → Run workflow → branch `main` → check **allow_mutations + allow_destructive + run_heavy**). Run #39 was INVALID (the provision bug fixed in #52). This validates: provision succeeds (`SCP_SHARED_*` set), parallel ADOPT runs, VPC stays ≤ 3, CIDR/subnet consistent, no 5h timeout.
2. **OPEN QUESTION**: in run #39 did the VPC *create itself* also fail (a 2nd issue) or was it only the `$GITHUB_ENV` format? Check run #39 AXIS1 log — `[shared_infra] provisioned vpc=…` (create OK, fix is complete) vs `could not provision shared VPC` (investigate: VPC quota leftovers / a pre-existing `10.124.0.0/20` VPC / create body). Run id 27237823351, job 80434219742.
3. Triage the re-run; then remaining (not yet built) gaps from `resource-dependencies.md`: volume **re-attach** cycle, SKE **parallel-filestorage**, nodepool SG/subnet attach-detach.

## Gotchas for the next session
- **Dispatch**: the GitHub MCP integration can READ runs but CANNOT dispatch workflows (`403 actions:write`) — a human must Run workflow. Poll status via `mcp__github__actions_list/get`; job logs are only downloadable AFTER the job completes (404 while in-progress).
- **Never run two VPC-mutating runs at once** (VPC cap 3).
- **Dashboard `build.py`**: keep the verified/도달 split + `loader.load_lifecycles` fragment-merge (main's #48 was a regressed parallel variant; #49 resolved the conflict by taking ours).
- The branch used this session: `claude/jolly-thompson-dEOI5` (fully merged to main).

## Run history
- **#36** — old serial CRUD, cancelled at the 300-min timeout (motivated the parallel redesign).
- **#39** — parallel design, provision step failed (stdout→`$GITHUB_ENV`); invalid. Fixed in #52.
