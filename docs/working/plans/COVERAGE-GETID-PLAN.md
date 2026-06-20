---
status: active
for: coverage
---

# COVERAGE-GETID-PLAN — the id-bound GET gap, classified and attacked

- Date: 2026-06-12 · Status: **active — wave A largely landed: gap_getid
  151 → 130** (live run history row, sha 1bea554 — matches a local
  `reachable_ceiling` recompute; ceiling 88.1% = 1209/1372, gap_write 33).
  The §2 verifies were composed into the wave-2 vpc-free chains
  (`generated__wave2.json`) and the heavy VS chain; queue/sec/rg/iam/scf/
  volume green, scr/fs recomposed, heavy rev 3 dispatched — see
  `docs/RESOURCE-MODEL-PLAN.md` §6. Product findings the waves surfaced
  are ledgered in `docs/working/trackers/PRODUCT-FINDINGS.md` (e.g. PF-03 filestorage 403,
  PF-06 scf time-or-period — the §2 run-risk note proved right).
- Input: reproduction of `dashboard.build.reachable_ceiling` (catalog
  `data/api_catalog.json` + `regression/scenarios/loader.load_lifecycles()`,
  enabled lifecycles only). Snapshot at session start: **gap_getid = 151**
  (dashboard history row said ≈149; drift = scenario edits since that run).
- Mechanism: gap_getid counts GET endpoints with path params that no ENABLED
  lifecycle step touches. The resource-task model
  (`knowledge/formal/resources/*.yaml`, plan §1) closes them by carrying
  `verify` read steps on create nodes — **each closes only when its node is
  composed into an enabled lifecycle and run** (C3 needs a live 2xx).

## 0 · Headline

> **Update 2026-06-18:** the §7 read-reachability worklist (Piece 3b, the 89
> `produced_by:null` self-params) is **RESOLVED** — producer-match on catalog
> self path-params went **90.9% → 98.1%** (960/979), **100% non-null** (70 newly
> produced + 19 honest waivers, 0 unexplained). Source of truth:
> `data/api_catalog_params.json`. Full closure: §7 dated section
> *"2026-06-18 — produced_by 98.1%, gap worklist resolved"*.

| bucket | count | meaning |
|---|---|---|
| gap_getid total | **151** | id-bound GETs unreachable from enabled scenarios |
| A — closable by model verifies (this change) | **49** | verify steps now exist on VALIDATED nodes; close on compose+run (48 added this session + 1 pre-existing `server-type` read) |
| B — node exists but is `docs` — validate create, then verify | **45** | no new modeling needed; create body/capture must be live-proven first |
| C — needs NEW model node / capture / lookup node | **29** | modeling work (mostly cheap read-only lookup nodes) |
| D — blocked (entitlement · console credential · foreign ids · owner waiver · other agent) | **28** | not authorable without an owner/console action |

## 1 · Gap counts by service (before this change)

```
 19 compute/virtualserver      6 management/resourcemanager   2 management/quota
 13 application-service/apigw  6 storage/archivestorage       2 management/support
 12 networking/vpc             5 management/cloudmonitoring   2 networking/firewall
 11 compute/scf                5 management/iam-id-center     2 platform/product
 11 management/iam             4 ai-ml/cloud-ml               2 security/secretvault
  8 networking/loadbalancer    4 networking/dns                2 storage/bm-blockstorage
  8 storage/backup             2 compute/multinodegpucluster  2 storage/filestorage
  6 ai-ml/aimlops-platform     2 data-analytics/data-flow     1 × 9 services
  5 compute/scf… (see list)    2 data-analytics/data-ops          (queue, baremetal,
  …                            2 devops-tools/devopsservice        quick-query, cloud-
                                                                   control, loggingaudit,
                                                                   gslb, configinspection,
                                                                   secretsmanager, kms…)
```

(Exact endpoint list reproducible with the §0 recipe; the per-endpoint
classification below is the authoritative breakdown.)

## 2 · A — 49 closed by verify entries on VALIDATED nodes (this session)

All path params are satisfiable from the node's own captures + direct
requires' captures (validator-enforced, 0 errors). `expect_status: [200]`
throughout; pre-existing exceptions kept ([200,403] apigw api-key per-id,
[200,404] server-type lookup).

| file (node) | endpoints closed |
|---|---|
| networking__vpc.yaml (subnet) | subnets/{id}/vips · subnets/{id}/sap-secondary-subnets |
| networking__vpc.yaml (transit-gateway) | transit-gateways/{id}/routing-rules · /vpc-connections |
| networking__vpc.yaml (vpc-peering) | vpc-peerings/{id}/routing-rules |
| networking__loadbalancer.yaml (lb-health-check, lb-server-group, lb-listener, load-balancer) | lb-health-checks/{id} · lb-server-groups/{id} · lb-server-groups/{id}/members · lb-listeners/{id} · loadbalancers/{id}/static-nats · /private-static-nats |
| networking__dns.yaml (hosted-zone) | hosted-zones/{id} · hosted-zones/{id}/records |
| security__kms.yaml (kms-key) | kms/transit/{key_id}/users |
| security__secretsmanager.yaml (secret) | secrets/{id}/versions |
| storage__filestorage.yaml (filestorage-volume) | volumes/{id}/access-rules |
| management__resourcemanager.yaml (resource-group) | resource-groups/{id}/resources |
| management__iam.yaml (iam-group, iam-policy) | groups/{id}/members · groups/{id}/policy-bindings · policies/{id}/bindings |
| application-service__apigateway.yaml (8 nodes) | apis/{id}/connected-endpoints · /reports · /resource-policies · resources/{rid} · resources/{rid}/methods · methods/{type} · /deployments · /stages · /access-controls · /usage-plans · usage-plans/{up}/api-keys (11) |
| application-service__queueservice.yaml (queue) | queues/{id}/attributes |
| compute__scf.yaml (scf-function, scf-cronjob-trigger) | cloud-functions/{id}/codes · /configurations · /configurations/{config,environment-variables,privatelink-endpoints,privatelink-services,resource-policies,url} · /logs · /metrics · triggers/{id} (11) |
| compute__virtualserver.yaml (server, custom-image) | servers/{id}/ips · /ips/{subnet_id} · /security-groups · /console-log · images/{id}/members (5) |
| *(pre-existing)* compute__virtualserver.yaml (server-type) | server-types/{id} |

Run-risk notes carried in the YAMLs: apigw `reports` and scf `logs`/`metrics`
may want period query params (no docs model) — if a live run 400s, add params
rather than widening expect_status.

**Next step for wave planning:** compose+enable these node lifecycles. Cheap,
VPC-free chains first (queue, kms→secret, resource-group, iam-group/policy,
scf spine, apigw spine = ~30 endpoints with no VPC); the vpc/lb/dns/server
chains ride existing shared-infra lanes.

## 3 · B — 45 endpoints whose node exists but is `docs` (validate, then verify)

Adding verifies now would be dead weight (composer must not run UNPROVEN
creates); the work is C5 promotion: live-prove create body + capture envelope,
flip provenance, then add the verify (one line each, same pattern as §2).

| group | n | nodes / what blocks promotion |
|---|---|---|
| vs auto-scaling family | 10 | launch-configuration → auto-scaling-group → asg-policy/schedule/notification (bodies docs-derived; per-id + list GETs all wireable from own captures once create proven) |
| storage/backup | 8 | backup-policy (7 backup_id GETs) + backup-agent check-connection-state; cross-service says backup needs a server — wire `requires` during validation |
| ai-ml/aimlops-platform | 6 | aimlops-platform node (requires ske-cluster — heavy chain); cluster_id GETs come from the ske require, release_id own capture |
| data-analytics check-duplication | 5 | data-flow(-service), data-ops(-service), quick-query — name-addressed paths; use the `stg{unique}` same-expansion trick from apigw-stage delete, or add name captures |
| networking/vpc | 3 | private-nat ×2 (uplink wiring exists, body UNPROVEN), vpc-endpoint (R3 blocker: needs real FS volume resource_key) |
| compute/multinodegpucluster | 2 | gpu-node/cluster-fabric — docs nodes, no captures yet; GPU quota risk |
| devops-tools/devopsservice | 2 | devops-service node has capture; create cost/duration unknown — validate first |
| management/iam | 2 | iam-access-key (heavy, shared-account 403 expected), iam-role policy-bindings (create may 500 ContactAdminForAssistance) |
| storage/bm-blockstorage | 2 | bm-block-volume/volume-group replications — bodies UNPROVEN; likely needs BM entitlement, may move to D |
| lb-member per-id | 1 | capture $.members[0].id UNPROVEN — prove and the read is one line |
| filestorage fs-replication | 1 | replication_id capture UNPROVEN; cross-region target kr-east1 |
| parallel-filestorage volume | 1 | pfs-volume docs; same access-rules pattern as filestorage |
| compute/baremetal | 1 | baremetal-server docs, no capture, expensive create |
| management/cloudcontrol | 1 | landing-zone — requires iam-identity-center (see D: IDC family is owner-disabled) — effectively gated |

## 4 · C — 29 endpoints needing NEW nodes / captures (modeling wave)

Cheap read-only **lookup nodes** (server-type pattern: GET list → capture id →
per-id verify; no create/teardown) — highest value next:

| new lookup node | closes | n |
|---|---|---|
| vs volume-type | volume-types/{id} | 1 |
| platform/product (new file) | products/{id} · product-categories/{id} | 2 |
| management/quota account-quota | account-quotas/{id} (quota-requests/{id} needs a created request — write op) | 1–2 |
| security managed-kms | managed-kms/transit/{key_id} (may be empty on bare account — [200,404] candidate) | 1 |
| networking firewall lookup | firewalls/{id} · firewalls/rules/{rule_id} (firewalls are implicitly created via igw `firewall_enabled: true` — pair the lookup with an igw variant) | 2 |

Real new create nodes / capture additions:

| work item | closes | n |
|---|---|---|
| **privatelink-service node** (requires lb chain) — unblocks 3 dependents | vpc privatelink-services/{id} ·  /connected-endpoints · vpc privatelink-endpoints/{id} · apigw privatelink-endpoints/{id} | 4 |
| dns-record node (POST records under hosted-zone) | hosted-zones/{id}/records/{record_id} | 1 |
| resourcemanager SRN family — promote `$.resource_group.srn` capture (envelope proof needed, node note) then verify resources/{srn}, tags/{srn}/{key}; region/service/type/id scheme derivable from the same SRN | 5 |
| iam: saml-provider node (api_bodies entry corrupt — re-research body), service-account node, iam-user node (for users/{id}/policy-bindings), resource-policies/{srn} (reuse rm SRN) | 4 |
| apigw-auth: auth_id list-recover capture (composer list-capture sub-step, R3 feature) | auths/{auth_id} | 1 |
| networking/gslb (new file — no model yet) | gslbs/{id}/resources | 1 |
| lb certificate upload node (wire certificatemanager cert into loadbalancers/certificates) | loadbalancers/certificates/{id} | 1 |
| vs: server boot-volume capture (envelope research) → servers/{id}/volumes/{vol} · image-member node (needs a second account id — may be D) → images/{id}/members/{mid} · subnet vip node (research: how are vips created?) → subnets/{id}/vips/{vip_id} | 3 |
| loggingaudit: research what issues `logging_id` (trail node exists but id semantics unknown) | logs/{logging_id} | 1 |

## 5 · D — 28 blocked (owner/console action required)

| group | n | reason |
|---|---|---|
| storage/archivestorage | 6 | **owner permanent exclusion** (2026-06-11): no dedicated auth key will be issued; 25/25 waivers in place — do not resurrect |
| management/cloudmonitoring | 5 | another agent owns `management__cloudmonitoring.yaml` (this session did not touch it) |
| management/iam-identity-center | 5 | owner-disabled blast-radius family (idc-* lifecycles `enabled:false`, waivered) — getid follows the write decision |
| ai-ml/cloud-ml | 4 | needs `scr-auth-key` console credential (env-skip precondition, plan §1) |
| management/iam accounts/* | 2 | needs a real `{account_id}` this caller does not own |
| management/support | 2 | inquiries/service-requests are console-created tickets; only viable as data-dependent lookups if the account happens to have any |
| security/secretvault | 2 | needs `iam-temp-auth-key` console credential |
| networking/dns public-domain | 1 | real public domain purchase — out of scope for regression |
| security/configinspection | 1 | needs `inspectable-account-auth-key` console credential |

## 6 · Orchestration order (suggested waves)

1. **Compose+run wave A** (this change): queue → kms/secret → resource-group →
   iam-group/policy → scf spine → apigw spine (VPC-free, ~30 endpoints), then
   vpc/lb/dns adopt-lane chains, then the heavy server chain (5).
2. **Lookup-node wave** (C, ~7 endpoints in a day: volume-type, product ×2,
   account-quota, managed-kms, firewall ×2) — read-only, no teardown risk.
3. **C5 promotion wave** (B): asg family + backup + devops first (20 endpoints),
   the rest opportunistically as live runs prove bodies.
4. **privatelink-service node** — single node unblocking 4 endpoints + the
   scf/apigw privatelink synthetic-id placeholders.
5. D stays parked until owner/console decisions change.

---

## 7 · probe_reads UNDER-SEEDING — the create→조회(show) gap (SYSTEMIC, 2026-06-18)

> Owner-flagged: "createusageplan 했으면 이후 usage-plan 조회가 다 돼야 하는 거
> 아닌가? 생성→조회·업데이트→삭제가 잘 인지 안 된 것 같다." Confirmed — and it is
> **repo-wide**, not apigw-only. Record so it is not missed.

**Mechanism.** A lifecycle's `probe_reads: {seed}` step fires every catalog GET
in the service whose path-params are **all** present in `seed`
(`regression/scenarios/engine.py:_probe_reads`, line 207 `params <= keys`). It is
purpose-built to "exercise path-parameter GETs reusing a resource a lifecycle
just created" (engine.py:74) and never fails the lifecycle (read-only). **The
gap:** most lifecycles seed it with only the TOP id, so **nested child-id GETs
are never fired** — the resource is created, updated and deleted, but never
SHOWN. This is a pure seeding omission, NOT an ordering problem (the child ids
are already captured when probe-reads runs).

**Scope: 47 of 92 probe_reads lifecycles seed only 1 id** (survey 2026-06-18).
Worst offenders = anything with nested children:
- DBaaS cluster-subops (mysql/mariadb/epas/postgresql/cachestore/searchengine/
  eventstreams `*-cluster-subops-guarded`) seed only `cluster_id` → miss
  instance-group/{id}, parameter-group/{id}, etc.
- iam-group/policy/role, idc-*, backup-job/agent, filestorage, archivestorage,
  apigateway-api-write-coverage, application-apigateway-api-resource, …

**Fix pattern (zero-risk — probe-reads never fails a lifecycle):** seed
`probe_reads` with EVERY captured child id in the lifecycle. Where a child GET
needs **query params** (not just path ids), probe-reads cannot supply them — add
an explicit GET step instead (see apigw `list-reports` below).

### apigateway — STATUS: FIXED on branch (regression/scenarios/lifecycles/application-service__apigateway.json)
`apigateway-api-write-coverage` seeded only `{api_id}`. The 8 child-id GETs the
owner listed split as:
| endpoint | was | now |
|---|---|---|
| `…/reports` listreports | probe-reads fired it param-less → **400** | explicit `list-reports` step w/ `stage_name=dev&start_date&end_date` (3 required query params, model read-reports) |
| `…/resources/{resource_id}` showresource | uncovered | probe-reads (seed `resource_id`) |
| `…/resources/{resource_id}/methods` listmethods | uncovered | probe-reads (`resource_id`) |
| `…/methods/{method_type}` showmethod | uncovered | probe-reads (`resource_id`+`method_type`) |
| `…/stages/{stage_name}` showstage | uncovered | probe-reads (`stage_name`=stg{unique}) |
| `…/auths/{auth_id}` showauth | uncovered | probe-reads (`auth_id`, captured via listauths) |
| `…/access-controls/{access_control_id}` showaccesscontrol | uncovered | probe-reads (`access_control_id`) |
| `…/usage-plans/{usage_plan_id}` showusageplan | uncovered | probe-reads (`usage_plan_id`) |
| `…/usage-plans/{usage_plan_id}/api-keys` listapikeys | uncovered | probe-reads (`usage_plan_id`) |

PENDING LIVE VALIDATION (needs a Tier-0 apigw run to confirm the 2xx).
- **api-keys per-id GET** `…/api-keys/{key_id}` stays **PF-02** (403 missing-IAM-action; LIST works) — not seeded.
- **resource-policies** PUT setresourcepolicy = **PF-19** (500 ContactAdminForAssistance, baselined) → the GET read 404s downstream (no policy ever created). Separate investigation for the correct call / backend bug.

### Piece 1 — DONE: engine auto-probe (supersedes per-lifecycle seeding)
`regression/scenarios/engine.py` now AUTO-SEEDS `probe_reads` from the full
capture context (`ctx`), merged with explicit entries, so EVERY lifecycle fires
every id-bound GET reachable from what it just created — no per-lifecycle
hand-seeding. The manual 46-lifecycle TODO is **obviated** (one engine change
covers all). Offline-proven on apigw: seed{api_id}=10 GETs → auto-seed=18 (the 8
child shows showresource/listmethods/showmethod/showstage/showauth/
showaccesscontrol/showusageplan/listapikeys). Explicit seeds still apply for
NAME-addressed segments not captured as vars (apigw `stage_name: "stg{unique}"`).
Query-param GETs (apigw `reports`) still need an explicit step/model verify.
PENDING LIVE VALIDATION. The apigw explicit child-id seed (46ef5d9) is now
redundant-but-harmless.

### Piece 2 — read-reachability report (`python -m spec.read_reachability`)
Static catalog×model join classifying every id-bound GET: cat1-auto /
cat2-needs-child / query-param / **model-gap**. The model-gap list is Piece 3's
worklist. Output `docs/working/trackers/READ-REACHABILITY.md`. (in progress)

### Piece 3 — burn down model-gaps (IN PROGRESS)
**3a DONE — central param-alias map** (`engine._PARAM_ALIASES`): the name-mismatch
subset of the 48 model-gaps (catalog `registry_id` vs captured `reg_id`, etc.) is
closed centrally in the auto-probe — service-scoped so a shared synonym like
`group_id` only resolves within its own service. Offline-proven: scr {reg_id,repo_id}
→ 4 registry/repository GETs; security-group {group_id,rule_id} → 2 GETs. Aliases:
registry_id←reg_id, repository_id←repo_id, dbaas_engine_version_id←engine_version_id
(×9 DBaaS), srn←rg_srn (×4), certificate_id←cert_id, resource_group_id←group_id,
security_group_id←group_id, security_group_rule_id←rule_id, service_account_id←account_id.
~22 GETs closed, zero file-churn. PENDING LIVE VALIDATION.

**3b REMAINING — genuinely-unmodeled / special** (need a real capture or are
name-addressed; NOT aliasable): `tags_id` (scr ×5), resourcemanager composite
`{region}/{service}/{resource_type}/{resource_identifier}` paths, `keypair_name`,
the `*_name` check-duplication GETs (data-flow/data-ops/quick-query), bare `{id}`
(cdn, servicewatch alerts — name-addressed, need the lifecycle's own show step),
`record_id` (dns), `baremetal_id`, `guardrail_id`, `vip_id`, `public_domain_id`,
`logging_id` (ambiguous near-miss — left out of aliases deliberately). Track here;
close per-service as each lifecycle/model gains the capture.

**Note:** "model-gap" is MODEL completeness; some are already covered at RUNTIME
because a hand-written lifecycle captures the catalog param directly (e.g.
`lb_certificate_id`, `dbaas_engine_version_id` in the dbaas read-coverage lifecycles)
+ Piece-1 auto-probe. A live run reconciles model-gap vs true runtime-gap.

### Query-param GETs (17) — separate bucket
Auto-probe issues param-less GETs, so id-bound GETs that ALSO need required query
params (apigw `reports`, dbaas `check-duplication` by name, date windows) need an
explicit lifecycle step / model `verify` with the params. Listed in
`docs/working/trackers/READ-REACHABILITY.md`.

### Piece 3b — PRECISE worklist (89 self-params, no producer; from data/api_catalog_params.json 2026-06-18)

> **RESOLVED 2026-06-18.** The 89 `produced_by:null` self-params below are now
> closed — the regenerated sidecar `data/api_catalog_params.json` carries a real
> `produced_by` + `producer_kind` + `capture` on every self path-param. **70 got
> real producers, 19 are honest waivers, 0 remain unexplained.** Across all 979
> self path-params in the catalog the producer-match is now **960/979 = 98.1%**
> (was 90.9%), **100% non-null** (19 waivers + 960 produced). See the dated
> closure section **"2026-06-18 — produced_by 98.1%, gap worklist resolved"**
> below for the per-kind table, the explicit 19-waiver list, and how these
> producers now feed the engine.py stage-2 identity auto-probe. The original
> worklist table is kept verbatim for history.

| service | unproduced self-param → endpoint | likely class |
|---|---|---|
| aimlops-platform (5) | cluster_id, cluster_id, cluster_id, cluster_id, cluster_id | needs capture / console-only id |
| apigateway (3) | parent_id, resource_id, resource_id | needs capture / console-only id |
| baremetal (2) | baremetal_id, baremetal_id | needs capture / console-only id |
| cachestore (3) | block_storage_group_id, instance_group_id, request_id | needs capture / console-only id |
| cloud-ml (3) | cluster_id, cluster_id, cluster_id | needs capture / console-only id |
| cloudmonitoring (1) | addrbookId | needs capture / console-only id |
| configinspection (2) | diagnosis_id, diagnosis_id | needs capture / console-only id |
| data-flow (3) | cluster_id, cluster_id, data_flow_id | needs capture / console-only id |
| data-ops (3) | cluster_id, cluster_id, data_ops_id | needs capture / console-only id |
| epas (4) | block_storage_group_id, instance_group_id, instance_group_id, request_id | needs capture / console-only id |
| eventstreams (3) | block_storage_group_id, instance_group_id, request_id | needs capture / console-only id |
| iam (2) | srn, srn | composite/srn path (no single create) |
| kms (7) | key_id, key_id, key_id, key_id, key_id, key_id | needs capture / console-only id |
| mariadb (4) | block_storage_group_id, instance_group_id, instance_group_id, request_id | needs capture / console-only id |
| mysql (4) | block_storage_group_id, instance_group_id, instance_group_id, request_id | needs capture / console-only id |
| postgresql (4) | block_storage_group_id, instance_group_id, instance_group_id, request_id | needs capture / console-only id |
| resourcemanager (5) | key, key, resource_identifier, resource_identifier, resource_identifier | composite/srn path (no single create) |
| scr (7) | tags_id, tags_id, tags_id, tags_id, tags_id, tags_id | unmodeled (scr tag id) |
| searchengine (4) | block_storage_group_id, instance_group_id, instance_group_id, request_id | needs capture / console-only id |
| secretvault (1) | secret_vault_id | needs capture / console-only id |
| sqlserver (4) | block_storage_group_id, instance_group_id, instance_group_id, request_id | needs capture / console-only id |
| vertica (4) | block_storage_group_id, instance_group_id, instance_group_id, request_id | needs capture / console-only id |
| virtualserver (1) | subnet_id | needs capture / console-only id |

---

## 8 · 2026-06-18 — produced_by 98.1%, gap worklist resolved

A multi-agent enrichment pass regenerated the sidecar
`data/api_catalog_params.json`: every self path-param now carries
`produced_by` + `producer_kind` (one of `create`, `create-xsvc`, `lookup`,
`lookup-xsvc`, `detail-read`, `async-op`, `waiver`) + `capture`. Catalog-wide
self path-param **producer-match went 90.9% → 98.1% (960/979)**, **100%
non-null**. The Piece-3b worklist of **89** previously-`null` self-params closed
as **70 real producers + 19 honest waivers, 0 unexplained null**. All figures
below are mechanically derived from `data/api_catalog_params.json`.

### (1) The 70 resolved — by producer_kind × service

Evidence: `knowledge/formal/resources/*.yaml` + `data/api_docs.json`.

| producer_kind | service | self-param(s) | producer (capture) |
|---|---|---|---|
| **detail-read** | mysql · mariadb · postgresql · epas · sqlserver · vertica · searchengine · eventstreams · cachestore (9 DBaaS svc) | `instance_group_id`, `block_storage_group_id` | `<svc>showcluster` cluster DETAIL read — nested capture `$.instance_groups[0].id` and `$.instance_groups[0].block_storage_groups[0].id` |
| **async-op** | same 9 DBaaS svc | `request_id` | `<svc>createcluster` async envelope `$.request_id` |
| **create-xsvc** | aimlops-platform · cloud-ml · data-flow · data-ops | `cluster_id` | external SKE cluster `container/ske/createcluster` `$.resource_id` (consumed as a body field) |
| **create-xsvc** | virtualserver | `subnet_id` | vpc `createsubnet` `$.subnet.id` (cross-service) |
| **create** | kms | `key_id` | `createkey` `$.key.id` (pseudo-resource ops keyed off the parent create) |
| **create** | secretvault | `secret_vault_id` | parent create (`createsecret`/vault create) |
| **create** | configinspection | `diagnosis_id` | parent create (run-diagnosis op) |
| **create** | data-flow | `data_flow_id` | same-service create — 202/no-body convention; jsonpath **UNPROVEN** |
| **create** | data-ops | `data_ops_id` | same-service create — 202/no-body convention; jsonpath **UNPROVEN** |
| **create** | baremetal | `baremetal_id` | same-service create — 202/no-body convention; jsonpath **UNPROVEN** |
| **create** | apigateway | `resource_id`, `parent_id` | parent `createapi`/`createresource` create |
| **lookup** | kms | `key_id` (lookup-addressed reads) | key list/lookup |

Counts by `producer_kind` for the resolved self-params (catalog-occurrence
basis): `create`, `detail-read` (25 — the DBaaS instance/block-storage group
reads), `create-xsvc`, `async-op` (9 — the DBaaS `request_id`), `lookup`. The
**same-service 202/no-body creates** (`data_flow_id`, `data_ops_id`,
`baremetal_id`) are matched by convention; their capture jsonpath is **UNPROVEN**
and must be live-confirmed before a `verify` is added.

### (2) The 19 honest waivers (producer_kind == "waiver")

These have **no producer** — the id is not minted by any REST create we can
drive, so a null `produced_by` is correct, not a gap.

| service | param | # endpoints | why (waiver reason) |
|---|---|---|---|
| resourcemanager | `key` | 6 | composite **name-addressed** tag key (showresourcetag, showcomponentstag, updateresourcetagvalue, updatecomponentstagvalue, deleteresourcetag, deletecomponentstag) — no single create mints it |
| resourcemanager | `resource_identifier` | 4 | composite **name-addressed** path (listcomponentstags, showresourcebycomponents, updatecomponentstags, deletecomponentstags) — region/service/type/identifier scheme, not a created id |
| scr | `tags_id` | 8 | container image tag is **docker-pushed**, no REST create (checktagsvulnerability, downloadmanifest, showtags, showtagspackages, showtagssecrets, showtagsvulnerabilities, updatetagslockpolicy, deletetags) |
| cloudmonitoring | `addrbookId` | 1 | **EOL / IB-034** (getadressbookmemberlist) — feature retired, no create path |

(19 total: 6 + 4 + 8 + 1. Verified by filtering `producer_kind=="waiver"` over
`data/api_catalog_params.json`.)

### (3) Downstream: these producers feed the stage-2 identity auto-probe

The `produced_by` + `capture` now populated on every self path-param is consumed
by the stage-2 **identity auto-probe** in `regression/scenarios/engine.py`
(§7 "Piece 1"): an id-bound GET resolves by identity from the producing step's
capture context, so a lifecycle that creates / reads / async-completes a
resource also **SHOWs** every nested child it minted — no per-lifecycle
hand-seeding. The DBaaS `detail-read` chain is the headline beneficiary: the
`<svc>showcluster` capture supplies `instance_group_id` /
`block_storage_group_id`, and the `createcluster` `$.request_id` supplies
`request_id`, so the cluster-subops GETs that §7 flagged as never-shown now
resolve by identity. UNPROVEN same-service captures (`data_flow_id`,
`data_ops_id`, `baremetal_id`) and the 19 waivers are deliberately excluded from
auto-probe until a live run proves the envelope (or, for waivers, never).
