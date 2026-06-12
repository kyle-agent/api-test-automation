# COVERAGE-GETID-PLAN — the id-bound GET gap, classified and attacked

- Date: 2026-06-12 · Status: **active** (this session's verify additions committed)
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
