# C3 Coverage Analysis & Plan — 2026-06-20

> Per-service "what's not working & how to advance" analysis, produced by 13
> parallel read-only `coverage-service` agents (one per category/cluster) over the
> cumulative published `endpoint_status.json` + the committed ledger/known-issues/
> waivers/lifecycles. **No live mutations or heavy runs were performed** — this is a
> planning pass. Seed: `reports/coverage_analysis/c3_seed.json` (gitignored).

## Headline

- **Raw 2xx coverage: 566 / 1372 (41.3%)** across 59 services. (The published "C3
  ~50%" uses a smaller denominator that also credits reachability-waivers-reached;
  this doc reports the stricter *verified-2xx* number so the gap is honest.)
- **Static reachability is maxed** (`spec.coverage_gap` 1371/1372; the lone gap is
  the blast-radius-waived iam `deletepolicies`). Every further gain is therefore a
  **runtime / dispatch** problem, not a scenario-authoring one.
- The 560 non-waived uncovered endpoints sort cleanly into **four tiers** by
  cost/risk. ~11 are **free** (read-only edits), ~78 are **light/non-billable**,
  ~120+ are **heavy/billable**, and ~90 are **structurally blocked** (entitlement /
  product-bug / console-only) and should be **waived, not chased**.

## The four tiers (cheapest-first)

### Tier 0 — FREE (read-only edits, NO safety gate, do now)
Pure scenario/smoke edits (add a required query param or a read-chain), validated
by a read-only run. Zero cost, zero account change. Same shape as the
`resourcemanager showresourcebycomponents` fix already landed this session.

| service | +gain | fix |
|---|---|---|
| network-logging | +2 | add `resource_type=VPC_FLOW_LOG` to the 2 list steps |
| ske | +1 | add `scp_original_image_type=k8s` to `list-images` |
| mysql | +1 | `listparameters` ← `dbaas_parameter_group_id` from param-group list |
| data-flow | +2 | `get*checkduplication` with synthetic name in path |
| eventstreams | +1 | `listparameters` required query params (verify doc) |
| scr | +1 | `checkrepositorynameduplication` ← `registry_id`+`name` from list |
| billingplan | +1 | `listplannedcomputeinstances` ← service_id/os_type/server_type/start/end |
| backup | +1 | `checkfilesystemduplication` missing query param (verify doc) |
| secretvault | +1 | `showsecretvault` ← capture `vault_id` from list |
| **≈ 11** | | all read-only; land on the next read-only run |

### Tier 1 — LIGHT mutations (non-billable; `SCP_ALLOW_MUTATIONS`+`SCP_ALLOW_DESTRUCTIVE`, heavy OFF)
Cheap, reversible resources (resource-groups, security-groups, SCF fn, GSLB/CDN,
trails, secrets, images). **The biggest bang-for-buck — ~78 endpoints for $0 billable.**
Several need a one-line **body fix** or an **env input** first (flagged).

| service | +gain | lever / prerequisite |
|---|---|---|
| gslb | +8 | `networking-gslb-service` (VPC-free) |
| cdn | +8 | `networking-cdn-service` (VPC-free) |
| virtualserver | +11 | `vs-image-write-coverage` — **fix `createimage` body first** |
| vpc | +11 | peering/cidr/vip light steps |
| iam | +9 | b64-SRN on addpermission/setresourcepolicy/… (+5) + iam-user chain (+4) |
| loggingaudit | +6 | `createtrail` — **needs a real Object-Storage bucket name (env)** |
| scf | +5 | `gen-wave5-scf-triggers` (creates own CF + API-GW) |
| devopsservice | +4 | create — **needs a real IAM member id + unique tenant name** |
| secretvault | +3 | **inject real `SCP_ACCESS_KEY` as `access_key_id`** |
| dns | +4 | public-domain writes (records 4xx-called) |
| kms | +2 | add a symmetric key for `hmac` + real `managed_key_id` PUT |
| firewall | +2 | `networking-firewall-rule` (needs a firewall_id from VPC IGW) |
| servicewatch | +2 | alert/event-rule create with real metric ids (or borrow-read) |
| security-group | +1 | `gen-wave4-sgrule` supplies `security_group_id` |
| secretsmanager | +1 | reveal-value against a fresh secret |
| **≈ 78** | | one light CRUD run + a few body/env fixes; full teardown |

### Tier 2 — HEAVY billable (`SCP_RUN_HEAVY` + shared VPC; arm live-watcher, verify 0 survivors)
The big frontier. **Run order = proven-first.** Real-2xx subset ≈ 120; the rest record
4xx-reached (guarded lifecycles) or are product-bug-capped.

| group | services (+gain) | notes |
|---|---|---|
| **DB engines** | mariadb +31, epas +28, postgres +31\*, mysql +20, cachestore +19, eventstreams +18 | proven `*-cluster-subops-guarded` pattern; \*postgres createcluster 500 → 4xx-only; 3 sub-op 500s/engine are product-bug |
| **compute** | virtualserver ASG/server-actions/snap +31 | needs shared VPC; ASG notif needs `user_ids` |
| **networking** | loadbalancer +16, vpc TGW/endpoint +20, vpn +8, direct-connect +7? | private-link/private-nat need a peer (LB/DC) first; DC may be entitlement |
| **storage** | filestorage +6, parallel-fs +3 | filestorage lifecycle ready, no body fix |
| **analytics/ml** | aimlops +5, data-flow +14, data-ops +12 | need NiFi/Airflow/SKE clusters (SKE-on-k8s) |
| **financial** | billingplan +4 (4xx-called) | no real commitment purchased |

### Tier 3 — BLOCKED (waive/baseline, do NOT chase) ≈ 90 endpoints
- **Entitlement (account-level, can't fix here):** cloudcontrol 15 (org-master LZ),
  organization 12 (member acct), configinspection 5 (foreign-acct creds), scf-PL 6,
  apigateway-PL 5, cloud-ml ~5 (service 404 / SCR key), sts 3 (404 routing — verify
  host first), loadbalancer privatenat 1, iam user-bindings 5.
- **Product-bug (backend 500, baselined):** postgres createcluster, backup
  createbackup, budget createaccountbudget (PF-04), quick-query createquickquery,
  iam createrole + accesskeycreate, dns createpublicdomainname, apigateway PF-19/23,
  12× DBaaS sub-op 500s, scr 2×, billingplan listplannedcomputeservertypes.
- **Console-only / unsatisfiable:** scr 19 (docker-push only), certificatemanager 2
  (real CA cert), quota showquotarequest (no create API).
- **Reachability-only / billing-prohibitive (already at ceiling by owner policy):**
  searchengine 22, vertica 19, sqlserver 33, archivestorage 25, baremetal-blockstorage
  39, baremetal 13, multinodegpucluster 12, parallel-fs 7, iam-identity-center 32,
  cloudmonitoring 14 — these are *waived*; "covered" = reached, mostly done.

### Already complete (no action)
resourcemanager 27/27, queueservice 12/12, support 4/4, costexplorer 3/3, pricing 3/3, product 4/4.

## Realistic ceiling

`566 → ~577 (Tier 0) → ~635–650 (Tier 1) → ~750–780 (Tier 2 real-2xx)`. **100% is not
reachable** — ~90 endpoints are entitlement/product-bug/console-only blocked. The
honest target is **~70% of the testable (non-waived) surface**, with the cheap tiers
delivering the first ~80 endpoints for ≈ $0.

## Recommended sequence

1. **Tier 0 now** — land the ~11 free read-only fixes + a scoped read-only validation run. (No gate, no cost.)
2. **Tier 1 light batch** — apply the body/env fixes, then one non-billable `MUTATIONS+DESTRUCTIVE` CRUD run; full teardown. ~78 endpoints.
3. **Tier 2 heavy** — DB engines first (proven), then compute/networking/storage; live-watcher armed, 0-survivor verified, owner-rule sequenced. (CI lane confirmed clear 2026-06-20.)
4. **Tier 3** — sweep the blocked set into waivers/`known_issues.json` so they stop showing as gaps.

## Open inputs needed for Tier 1
- a real **Object-Storage bucket name** (loggingaudit `createtrail`)
- a real **IAM member/user id** (devopsservice `members[]`)
- confirm using the runner's **`SCP_ACCESS_KEY`** as secretvault `access_key_id`
