# Handoff — CRUD setter validation (PR #44, branch `claude/trusting-curie-Ql75T`)

> Purpose: resume the CRUD write-coverage validation effort in a new session.
> Point the next session at **this file**. It captures everything reviewed,
> concluded, the environment constraints, the commits made, the full failure
> triage, and the recommended next steps. Last updated mid-effort while the
> validation run for `8cd8b27` was still in flight.

## 1. Goal

PR #44 (commit `14f5b4c` "aggressively add 26 grounded write steps") added 26
`xcov-*` write **setters** (PUT/POST/PATCH "in-place update" coverage) plus a
certificatemanager **import** lifecycle, across many CRUD lifecycles in
`regression/scenarios/scenarios.json`. The task: **validate those setters
against the live SCP gateway, fix or isolate the broken ones, and get the CRUD
CI check green** — without leaking real cloud resources or burning unnecessary
billable runs.

## 2. How CRUD runs / environment constraints (READ FIRST)

- Orchestrator workflow: `.github/workflows/api-test.yml`, job **"AXIS1
  regression (smoke + read-chains, opt-in CRUD)"**.
- CRUD lifecycles create+delete **real, billable** SCP resources. They run only
  when gated on: dispatch input `allow_mutations` OR repo var
  `SCP_RUN_CRUD=true`. Heavy lifecycles (real VM/K8s/DB + shared networking) run
  when `run_heavy` OR repo var `SCP_RUN_HEAVY=true`.
- **The repo has `SCP_RUN_CRUD=true` AND `SCP_RUN_HEAVY=true` set as repo
  variables.** Therefore **every push to this PR branch (non-`.md`) triggers a
  full CRUD + heavy billable run (~20 min light, up to ~1h45m with heavy).**
  Budget pushes accordingly — batch fixes into ONE push.
- **`paths-ignore: ["**/*.md"]`** on the `pull_request` trigger → **pushing only
  Markdown files does NOT trigger a run.** (That is why saving this handoff is
  free.)
- `concurrency.cancel-in-progress: false` with a per-run-id group → concurrent
  pushes pile up; multiple runs each create VPCs and can saturate the VPC quota.
  Avoid rapid successive pushes.
- **The GitHub MCP integration token in the web/remote session is read-only for
  Actions**: `actions_run_trigger` (workflow_dispatch) returns **403 "Resource
  not accessible by integration"**. You CANNOT trigger or cancel runs from the
  agent. You CAN read runs/jobs/logs. Triggering a manual dispatch requires the
  human to click Run workflow (or push a non-md commit).
- A **`workflow_dispatch` run does NOT post the smoke/CRUD/sweep PR comments**
  (those steps are gated on `github.event_name == 'pull_request'`). Dispatch
  results must be read from job logs. A **push (pull_request synchronize)** run
  DOES post the comments.
- Reading `mcp__github__actions_list` output often exceeds the tool token limit
  and is saved to a file; parse it with `python3 -c "import json; ..."`.

### VPC quota (the recurring blocker)
- Account VPC limit = **5**. Error: `scp-network.vpc.exceed-max-count`.
- Many lifecycles start with `create-vpc`; when the quota is full they **SKIP**
  (pytest skip, which does NOT fail CI) at create-vpc.
- Saturation has two sources observed: (a) **concurrent runs** each holding
  VPCs, and (b) **intra-run** ordering — earlier db-cluster lifecycles in the
  same run consume the 5 VPCs before `heavy-shared-networking` runs, so the
  heavy networking setters keep getting SKIPPED and have **never been
  validated** yet.
- Each run's **sweep job** (`cleanup.reconciler`, tag-scoped) reclaims that
  run's VPCs after its regression job finishes (seen deleting 51–53 resources
  per run). A run stays `in_progress` through conformance/dashboard AFTER its
  regression+sweep are done, so "run in_progress" over-counts VPC pressure — the
  regression job completing (CI check fires) is the better "VPCs no longer being
  created" signal.

## 3. Engine isolation contract (key mechanic)

`regression/scenarios/engine.py` (~lines 540–640): a step that returns a
non-2xx status:
- if it has **`"optional": true`** → its **`group`** is added to `failed_groups`,
  the group is torn down, a **warning** is emitted (engine.py ~632), and the
  lifecycle continues (does NOT fail CI). This is the documented
  "create→delete spine" contract (see the queueservice lifecycle `_note`).
- if it is **not optional** → a hard `assert` fails the whole lifecycle (reddens
  CI).

`14f5b4c` added all 26 `xcov-*` setters with a `group` **but without
`optional: true`**, so any non-2xx hard-failed CI. That was the core bug.

## 4. Commits made on this branch (all pushed)

| commit | change | status |
|---|---|---|
| `e6499eb` | mint certificatemanager key as PKCS#1 (was PKCS#8) | **INEFFECTIVE** — SCP rejects PKCS#1 too (see §6). Harmless, left in place. |
| `c49cc27` | add `optional: true` to all 26 `xcov-*` setters so failures isolate | **VALIDATED — works** (run `c49cc27` showed 17 group-isolation warnings instead of hard fails) |
| `f0fe204` | remove scf `xcov-updateprivatelinkservice` setter | fix pushed, see §6 |
| `8cd8b27` | mysql/postgresql db-cluster body fixes + disable cert-import lifecycle | **pushed; validation run `27103395462` ran but its CRUD job hit the 120-min timeout (see §6.5) — db/scf/heavy fixes still UNVALIDATED** |
| `ffa7f4d` | this handoff doc (Markdown-only, no CI run) | n/a |

## 5. Verified-good (do not redo)

- `optional: true` isolation fix works for the xcov setters.
- The certificatemanager key generated by `engine._self_signed_pem()` is a valid
  PKCS#1 PEM locally (`openssl rsa -noout -check` → "RSA key ok"); the API still
  rejects it, so the problem is NOT our key encoding (see §6).
- Correct documented request schemas live in **`data/api_docs.json`**
  (`["endpoints"]`, keyed by `category/service/operationId`, each with
  `parameters` + `request_example`). Use these as ground truth for bodies/params
  rather than guessing.

## 6. Full failure triage (from runs `c49cc27` and earlier heavy runs)

### Fixed (mine)
- **certificatemanager-import `validate`** → 400 "This private key is not a PEM
  format". Tried PKCS#1 conversion (`e6499eb`) — STILL rejected. Conclusion: SCP
  check-validation/import will not accept a runtime-minted **self-signed**
  keypair regardless of PEM encoding, and CI has no real CA-signed cert.
  **Resolution: lifecycle `enabled: false` (`8cd8b27`).** The separate
  certificatemanager **self-sign** lifecycle already covers create/read/delete.
- **scf `setcloudfunctioncodefile` / `updateprivatelinkservice`**: the
  privatelink setter enabled PrivateLink (`privatelink_service_enabled: true`),
  which makes the lifecycle's own `delete` fail with
  `CloudFunctionNotDeletableError` ("PrivateLink must be disabled"). Commit
  `3d43e70` had previously removed privatelink enable/disable for exactly this
  reason; `14f5b4c` re-added it. **Resolution: removed again (`f0fe204`).**
- **mysql `set-maintenance`** → 400 ValidationError (×4). Body wrongly used the
  create-cluster `maintenance_option` sub-schema. Correct schema is
  `MaintenanceRequest`: `{start_day_of_week, start_minute, start_time,
  term_hour}`. **Resolution: body corrected (`8cd8b27`)**, matching the
  postgresql lifecycle which already used the right body.
- **postgresql `list-parameters`** (`GET /v1/parameters`) → 400. Endpoint
  **requires query param `dbaas_parameter_group_id`**. **Resolution
  (`8cd8b27`):** reordered so `list-parameter-groups` runs first and
  soft-captures `dbaas_parameter_group_id` (`$.contents[].id`), passed it on the
  query string, and made the read `optional`+grouped (`pg-listparam`).

### Isolated as best-effort warnings via `optional: true` (`c49cc27`) — these
### are orphaned/speculative setters that reference resources their lifecycle
### does not create; they now warn instead of failing CI. Candidates for proper
### grounding OR removal in a future pass:
- `networking-vpc-subnet :: setport` — body `security_groups: [""]` (empty SG) →
  "Security group not found". Could send description-only.
- `filestorage-volume :: setaccessrule` — `object_id` is a fake non-hex UUID and
  needs a real VM; the light volume lifecycle has no server. Remove or ground.
- `security-secretsmanager-secret :: setprivateacl` — `private_acl_resources: []`
  → "Private ACL must not be empty". Needs a non-empty principal/resource list.
- `security-secretsmanager-secret :: setsecretsmanagerlabel` →
  "...UpdateLabelNotAllowedOperation" (truncated) — likely not allowed in this
  state.
- `heavy-shared-networking :: sethostedzone` — references `{hosted_zone_id}` that
  is **never captured** in the lifecycle (no DNS hosted zone created) → literal
  placeholder 404. Remove or add a create+capture of a hosted zone.
- `servicewatch-loggroup-logstream :: listmetricdata` / `listmetricinfos` —
  synthetic metric names (`regr{unique}`) that don't exist → 400. Needs real
  emitted metrics; likely keep optional or remove.
- The remaining heavy-shared-networking `xcov-*` setters (setprivatedns,
  setlbhealthcheck, setlblistener, setlbservergroup, setloadbalancer,
  setnatgateway, setsubnetvip, settransitgateway) and others
  (`setsubnet`, `setinternetgateway`, `updateserverinterface`, `querypolicy`,
  `setresourcepolicy`, `setloggroup`, `updatesecretsmanagersecretvalue`,
  `setsecretaclcidr`) are **untested against the gateway** because
  heavy-shared-networking keeps getting VPC-skipped before they run. Status
  unknown.

### Environmental / upstream (NOT our code; do not "fix" in scenarios)
- `financial-management/billingplan/listplannedcomputeservertypes` → **500**
  (persistent upstream; shows up in every read-only smoke).
- `resourcemanager-resource-group` `updatetags`/`set-rg` → 500
  ContactAdminForAssistance; `tag-rg` → 403 Forbidden (account permission).
- `scf` `create-trigger` → **403** `scf:CreateCloudFunctionTrigger` (account
  lacks the entitlement) — this is a **spine** step, so when it 403s it hard-
  fails the scf lifecycle. May need the account entitlement, or make the trigger
  creation tolerant/skip-on-403. Intermittent across runs.
- `container-ske-cluster-nodepool` / `compute-virtualserver-full` `wait-*` →
  404 / ReadTimeout(20s) — flaky heavy provisioning/infra (cluster/server
  vanishes or times out). Not deterministic.
- `container-scr-registry` → quota (CONTAINER_REGISTRY max 1) skip; `scr-checks`
  401 HmacValidFail; `registry-update-public-endpoint` 500.
- archivestorage list/show → 401 (smoke, entitlement).

### 6.5 Validation run `27103395462` (sha `8cd8b27`) — PARTIAL, hit 120-min timeout

- The **CRUD job was CANCELLED at the workflow's `timeout-minutes: 120`**
  (smoke 20:07–20:20, CRUD 20:20–22:07 = 120 min exactly). The
  "Comment CRUD result on PR" step was **skipped**, so there is **no CRUD PR
  comment** for this run. Conformance/dashboard succeeded; sweep ran.
- Partial results recovered from the **`regression-reports` artifact**
  (`junit-crud.xml`): **10 PASS / 3 SKIP / 0 FAIL** for the lifecycles that
  completed before the cut-off.
  - PASS: resourcemanager-resource-group, networking-vpc-subnet,
    container-scr-registry, filestorage-volume,
    security-certificatemanager-selfsign, application-queueservice-queue,
    networking-security-group, virtualserver-keypair, networking-vpc-publicip,
    networking-vpc-internet-gateway.
  - SKIP (VPC quota): container-ske-cluster-nodepool, compute-virtualserver-full,
    database-mysql-cluster.
- **What this validates:** the `optional`-isolation fix (`c49cc27`), the scf
  privatelink removal (`f0fe204`), and the cert-import disable (`8cd8b27`) — the
  lifecycles that previously hard-failed on xcov setters now **PASS** (setters
  isolate as warnings), with **zero failures**.
- **Still UNVALIDATED** (timeout cut the run off before reaching them):
  database-mysql-cluster `set-maintenance` body fix, database-postgresql-cluster
  `list-parameters` fix, compute-scf-cloud-function-cronjob-trigger delete,
  **heavy-shared-networking (the 9 networking setters)**, secretsmanager,
  apigateway, servicewatch, iam-group, virtualserver-full.
- **NEW BLOCKER — 120-min CRUD timeout.** Now that fixes let more heavy
  lifecycles actually run (instead of failing/skipping fast), and because
  VPC async-deletes linger against the 5-VPC quota, the full CRUD suite no
  longer finishes within 120 minutes and gets cancelled before the later
  lifecycles. Recovering per-lifecycle results requires downloading the
  `regression-reports` artifact's `junit-crud.xml` (the PR comment won't exist
  on a timed-out run). To download: `mcp__github__actions_get`
  `download_workflow_run_artifact` → returns a temporary presigned URL → curl +
  unzip.

## 7. Outstanding / recommended next steps

0. **FIRST — solve the 120-min CRUD timeout (§6.5), it now blocks all further
   validation.** The suite no longer finishes in 120 min, so the db/scf/heavy
   fixes can't be reached. Options (pick one or combine):
   - Raise `timeout-minutes` for the regression job in
     `.github/workflows/api-test.yml` (e.g. 120 → 300). Simple; the run is long
     and billable but completes. (This is a `.yml` change → it WILL trigger a
     run.)
   - Reorder lifecycles in `scenarios.json` so the high-value unvalidated ones
     (postgresql-cluster, scf, heavy-shared-networking, secretsmanager) run
     EARLY, before the time/VPC budget is exhausted.
   - Scope a single targeted run via `crud_filter` (needs human dispatch or repo
     var) — but note `-k $FILTER` in the workflow is unquoted, so multi-term
     `"a or b"` filters break; use a single token. Consider quoting it
     (`-k "$FILTER"`, workflow line ~311) to allow precise multi-lifecycle runs.
1. The fixes in `8cd8b27` (mysql set-maintenance body, postgresql
   list-parameters) and `f0fe204` (scf delete) and the 9 heavy-shared-networking
   setters are **still unvalidated** — confirm them once a run can reach them.
   Read results from the `regression-reports` artifact `junit-crud.xml` if the
   run times out (no PR comment on a cancelled run).
2. **heavy-shared-networking is still unvalidated** (VPC starvation). Options
   discussed with the user:
   - (a) Rework the lifecycle to **adopt an existing VPC** instead of
     `create-vpc`, so it doesn't compete for the 5-VPC quota (most robust).
   - (b) Ensure it runs when quota is free (single non-overlapping run, and/or
     ordering so heavy-networking creates its VPC before the db-clusters
     consume the quota).
   - (c) Defer; the 9 networking setters are `optional` now so they isolate
     rather than redden CI even when they do run.
3. **Decide fix-vs-remove for the orphaned best-effort setters** in §6 (they
   currently just emit warnings = noise, not real coverage). User preference so
   far: properly ground where possible, using `data/api_docs.json` schemas.
4. **scf `create-trigger` 403**: if the account entitlement can't be granted,
   make the trigger-creation step tolerant (skip-on-403) so the scf lifecycle
   stops hard-failing on a pure permission gap.
5. The SUBCREATE expansion (≈74 capture+cleanup pairs) the user mentioned as a
   later goal is **not started**; do it only after the setter coverage is clean
   and VPC/heavy validation is sorted.

## 8. Key files / references

- `regression/scenarios/scenarios.json` — all lifecycles & steps (large).
- `regression/scenarios/engine.py` — isolation contract (~L540–640), cert
  minting `_self_signed_pem()` (~L205–245).
- `.github/workflows/api-test.yml` — orchestrator; gates, paths-ignore, sweep.
- `data/api_docs.json` — authoritative endpoint schemas (`endpoints` + `models`).
- `data/api_bodies.json` — example request bodies keyed by
  `category/service/operationId`.
- PR #44, branch `claude/trusting-curie-Ql75T`, base `claude/determined-turing-V8Xmv`.

## 9. xcov setter inventory (25 after `f0fe204` removed updateprivatelinkservice)

All now carry `optional: true`. Grep: `grep -n '"group": "xcov-' scenarios.json`.
Lifecycles touched: resourcemanager-resource-group, networking-vpc-subnet,
networking-vpc-internet-gateway, filestorage-volume, compute-virtualserver-full,
security-secretsmanager-secret (×4), application-apigateway-api-resource,
compute-scf-cloud-function-cronjob-trigger, heavy-shared-networking (×9),
iam-group, servicewatch-loggroup-logstream (×3).
