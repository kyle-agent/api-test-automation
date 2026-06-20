# PROBE-READS-PLAN — where the probe-read principle still needs applying

- Date: **2026-06-15** · Owner: coverage-coordinator (Track ② Coverage).
- Principle (durable): **every node whose create captures an id MUST attach the
  id-bound GETs that key off that id as `verify` reads** — see
  `knowledge/formal/FORMAT.md` § "Probe-read completion". This turns each
  `gap_getid` endpoint into one that is auto-covered the moment its parent node
  is composed into an enabled lifecycle and run.
- Source: `python -m spec.coverage_gap` (the `gap_getid` line) →
  **80 id-bound GET endpoints still uncovered** across 26 services. Provenance
  per parent node read from `knowledge/formal/resources/*.yaml`. Detailed
  A/B/C/D classification: `docs/working/plans/COVERAGE-GETID-PLAN.md` (this doc is the
  endpoint→parent→action map keyed on the probe-read rule).
- Regenerate the total any time: `python -m spec.coverage_gap` (heavy=on);
  current ceiling 91.8% reachable, gap 113 = 80 getid + 33 write.

## 0 · Headline — the cheapest-wins picture

| bucket | count | what it costs to close |
|---|---|---|
| **gap_getid total** | **80** | — |
| **A · VALIDATED parent, verify already present** | **7** | /bin/bash modeling — closes on next compose+run of the parent's lifecycle |
| **B · VALIDATED parent, verify NOT yet added** | **4** | one-line `verify` read each, then compose+run (no new create) — **cheapest authoring** |
| **C · docs parent — validate create first, then add verify** | **55** | C5 promotion: live-prove the parent create, flip provenance, then one-line verify |
| **D · no node / blocked** | **14** | new lookup/create node, foreign id, or owner/console gate |

Only **11 / 80** sit on an already-VALIDATED parent (A+B) — those are the
cheapest wins and need **no new create**, just (for the 4 in B) a one-line verify
read. The other 69 are gated on first validating a `docs` parent create (55) or
modeling/credentials (14).

## 1 · PRIORITIZE — top cheapest, validated-parent first (the answer to "what next")

Ranked by *validated-parent getids reachable* (cheapest = no new create needed).
Because only 5 services have any validated-parent getid, the "top 10" tapers:
ranks 6-10 are the cheapest **docs-parent** services (single `gen-<svc>` run
validates the parent + closes its getids together).

| # | service | getid | from VALIDATED parent | cheapest action |
|---|---|---|---|---|
| 1 | networking/vpc | 8 | **3** | 3 already wired (transit-gateway ×2, vpc-peering) → close on gen-vpc run; private-nat/vpc-endpoint/privatelink-service need create-validate first |
| 2 | management/resourcemanager | 3 | **3** | **B: add 3 SRN verify reads** to `resource-group` (rg_srn already captured) — promote $.resource_group.srn envelope, then resources/{srn} + tags/{srn}(+/{key}) |
| 3 | networking/dns | 4 | **2** | hosted-zone show + records list verifies present → close on gen-dns run; records/{record_id} needs a dns-record node, public-domain blocked |
| 4 | application-service/apigateway | 2 | **2** | both wired (apigw-api connected-endpoints, apigw-deployment list) → **close on next gen-wave-apigw run** (other agent is wiring this concretely) |
| 5 | compute/virtualserver | 7 | **1** | **B: add servers/{id}/volumes/{vol} verify** to `server` (needs boot-volume capture); asg family is docs (validate create first); volume-type + image-member need new nodes |
| 6 | storage/backup | 8 | 0 | validate `backup-policy` create (1 create) → 7 backup_id getids close together + backup-agent check-connection |
| 7 | management/iam-identity-center | 5 | 0 | validate idc nodes — **but owner-disabled blast-radius family** (idc-* enabled:false, waivered): effectively gated, do not pull forward |
| 8 | management/cloudmonitoring | 5 | 0 | **another agent owns cloudmonitoring.yaml** — coordinate; cm-event-policy validate closes 2, 3 are data-dependent event lookups |
| 9 | management/iam | 8 | 0 | validate iam-role / iam-user / iam-saml-provider / iam-access-key creates (several 403/500-prone, shared-account) → 5 getids; 3 need foreign account_id / new service-account node |
| 10 | storage/archivestorage | 6 | 0 | **owner permanent exclusion** (no dedicated auth key) — 25/25 waivers; parked, do NOT resurrect |

**Genuinely actionable top of queue** (strip the gated/owned rows 7,8,10):
**resourcemanager (B, 3) → virtualserver server-volume (B, 1)** are the only
zero-new-create authoring wins; then the **A rows close for free** on the next
vpc / dns / apigw composed runs (7 endpoints). After that, cheapest create-validate
services are **storage/backup (1 create → 8)**, then iam, secretvault, devops,
gslb, configinspection.

## 2 · Full map — endpoint → parent node → action (grouped by service)

Ordered by validated-parent count desc, then total desc. `wired?`: **yes** =
verify read already on the node; **partial** = some of the node's id-bound GETs
wired, this one or a sibling still missing; **no** = not wired.

### networking/vpc — 8 getid (3 from VALIDATED parent)

| endpoint | parent node (id source) | parent prov | wired? | action |
|---|---|---|---|---|
| `GET /v1/private-nats/{private_nat_id}` | private-nat | docs | no | validate parent create first, then add verify |
| `GET /v1/private-nats/{private_nat_id}/private-nat-ips` | private-nat | docs | no | validate parent create first, then add verify |
| `GET /v1/privatelink-services/{privatelink_service_id}/connected-endpoints` | privatelink-service | docs | no | validate parent create first, then add verify |
| `GET /v1/subnets/{subnet_id}/vips/{vip_id}` | (subnet-vip node - missing) | (none) | no | new node/lookup/capture needed (or blocked) |
| `GET /v1/transit-gateways/{transit_gateway_id}/routing-rules` | transit-gateway | VALIDATED | yes | compose+run (verify present) → closes |
| `GET /v1/transit-gateways/{transit_gateway_id}/vpc-connections` | transit-gateway | VALIDATED | yes | compose+run (verify present) → closes |
| `GET /v1/vpc-endpoints/{vpc_endpoint_id}` | vpc-endpoint | docs | no | validate parent create first, then add verify |
| `GET /v1/vpc-peerings/{vpc_peering_id}/routing-rules` | vpc-peering | VALIDATED | yes | compose+run (verify present) → closes |

### management/resourcemanager — 3 getid (3 from VALIDATED parent)

| endpoint | parent node (id source) | parent prov | wired? | action |
|---|---|---|---|---|
| `GET /v1/resources/{region}/{service}/{resource_type}/{resource_identifier}` | resource-group (srn) | VALIDATED | no | ADD verify read on VALIDATED parent (cheapest) |
| `GET /v1/tags/{region}/{service}/{resource_type}/{resource_identifier}` | resource-group (srn) | VALIDATED | no | ADD verify read on VALIDATED parent (cheapest) |
| `GET /v1/tags/{region}/{service}/{resource_type}/{resource_identifier}/{key}` | resource-group (srn) | VALIDATED | no | ADD verify read on VALIDATED parent (cheapest) |

### networking/dns — 4 getid (2 from VALIDATED parent)

| endpoint | parent node (id source) | parent prov | wired? | action |
|---|---|---|---|---|
| `GET /v1/hosted-zones/{hosted_zone_id}` | hosted-zone | VALIDATED | partial | compose+run (verify present) → closes |
| `GET /v1/hosted-zones/{hosted_zone_id}/records` | hosted-zone | VALIDATED | partial | compose+run (verify present) → closes |
| `GET /v1/hosted-zones/{hosted_zone_id}/records/{record_id}` | (dns-record node - missing) | (none) | no | new node/lookup/capture needed (or blocked) |
| `GET /v1/public-domain-names/{public_domain_id}` | (public domain purchase) | (none) | no | new node/lookup/capture needed (or blocked) |

### application-service/apigateway — 2 getid (2 from VALIDATED parent)

| endpoint | parent node (id source) | parent prov | wired? | action |
|---|---|---|---|---|
| `GET /v1/apis/{api_id}/connected-endpoints` | apigw-api | VALIDATED | yes | compose+run (verify present) → closes |
| `GET /v1/apis/{api_id}/deployments` | apigw-deployment | VALIDATED | yes | compose+run (verify present) → closes |

### compute/virtualserver — 7 getid (1 from VALIDATED parent)

| endpoint | parent node (id source) | parent prov | wired? | action |
|---|---|---|---|---|
| `GET /v1/auto-scaling-groups/{auto_scaling_group_id}/notifications` | auto-scaling-group | docs | partial | validate parent create first, then add verify |
| `GET /v1/auto-scaling-groups/{auto_scaling_group_id}/notifications/{notification_id}` | asg-notification | docs | no | validate parent create first, then add verify |
| `GET /v1/auto-scaling-groups/{auto_scaling_group_id}/policies` | auto-scaling-group | docs | partial | validate parent create first, then add verify |
| `GET /v1/auto-scaling-groups/{auto_scaling_group_id}/schedules` | auto-scaling-group | docs | partial | validate parent create first, then add verify |
| `GET /v1/images/{image_id}/members/{member_id}` | (image-member node - needs 2nd acct) | (none) | no | new node/lookup/capture needed (or blocked) |
| `GET /v1/servers/{server_id}/volumes/{volume_id}` | server (boot-vol capture) | VALIDATED | no | ADD verify read on VALIDATED parent (cheapest) |
| `GET /v1/volume-types/{volume_type_id}` | (volume-type lookup - missing) | (none) | no | new node/lookup/capture needed (or blocked) |

### management/iam — 8 getid (0 from VALIDATED parent)

| endpoint | parent node (id source) | parent prov | wired? | action |
|---|---|---|---|---|
| `GET /v1/access-keys/{access_key_id}` | iam-access-key | docs | no | validate parent create first, then add verify |
| `GET /v1/accounts/{account_id}/users` | (foreign account_id - D) | (none) | no | new node/lookup/capture needed (or blocked) |
| `GET /v1/accounts/{account_id}/users/{user_id}` | (foreign account_id - D) | (none) | no | new node/lookup/capture needed (or blocked) |
| `GET /v1/resource-policies/{srn}` | iam-resource-policy (srn from rm) | docs | no | validate parent create first, then add verify |
| `GET /v1/roles/{role_id}/policy-bindings` | iam-role | docs | no | validate parent create first, then add verify |
| `GET /v1/saml-providers/{saml_provider_id}` | iam-saml-provider | docs | no | validate parent create first, then add verify |
| `GET /v1/service-accounts/{service_account_id}` | (service-account node - missing) | (none) | no | new node/lookup/capture needed (or blocked) |
| `GET /v1/users/{user_id}/policy-bindings` | iam-user | docs | no | validate parent create first, then add verify |

### storage/backup — 8 getid (0 from VALIDATED parent)

| endpoint | parent node (id source) | parent prov | wired? | action |
|---|---|---|---|---|
| `GET /v1/backup-agents/{backup_agent_id}/check-connection-state` | backup-agent | docs | no | validate parent create first, then add verify |
| `GET /v1/backups/{backup_id}/agent-backup-restore-targets` | backup-policy | docs | no | validate parent create first, then add verify |
| `GET /v1/backups/{backup_id}/backup-histories` | backup-policy | docs | no | validate parent create first, then add verify |
| `GET /v1/backups/{backup_id}/filesystem-path` | backup-policy | docs | no | validate parent create first, then add verify |
| `GET /v1/backups/{backup_id}/restore-histories` | backup-policy | docs | no | validate parent create first, then add verify |
| `GET /v1/backups/{backup_id}/restore-targets` | backup-policy | docs | partial | validate parent create first, then add verify |
| `GET /v1/backups/{backup_id}/restore/restorable-subnets` | backup-policy | docs | no | validate parent create first, then add verify |
| `GET /v1/backups/{backup_id}/schedules` | backup-policy | docs | no | validate parent create first, then add verify |

### storage/archivestorage — 6 getid (0 from VALIDATED parent)

| endpoint | parent node (id source) | parent prov | wired? | action |
|---|---|---|---|---|
| `GET /v1/archiving-policies/{archiving_policy_id}` | archiving-policy | docs | partial | validate parent create first, then add verify |
| `GET /v1/buckets/{bucket_id}` | archive-bucket | docs | no | validate parent create first, then add verify |
| `GET /v1/buckets/{bucket_id}/encryption` | archive-bucket | docs | no | validate parent create first, then add verify |
| `GET /v1/buckets/{bucket_id}/object-versions` | archive-bucket | docs | no | validate parent create first, then add verify |
| `GET /v1/buckets/{bucket_id}/objects` | archive-bucket | docs | no | validate parent create first, then add verify |
| `GET /v1/buckets/{bucket_id}/versioning` | archive-bucket | docs | no | validate parent create first, then add verify |

### management/cloudmonitoring — 5 getid (0 from VALIDATED parent)

| endpoint | parent node (id source) | parent prov | wired? | action |
|---|---|---|---|---|
| `GET /v1/cloudmonitorings/event/v2/event-policies/{eventPolicyId}/histories` | cm-event-policy | docs | no | validate parent create first, then add verify |
| `GET /v1/cloudmonitorings/event/v2/event-policies/{eventPolicyId}/notifications` | cm-event-policy | docs | no | validate parent create first, then add verify |
| `GET /v1/cloudmonitorings/event/v2/events/{eventId}` | (event lookup - data-dependent) | (none) | no | new node/lookup/capture needed (or blocked) |
| `GET /v1/cloudmonitorings/event/v2/events/{eventId}/notification-states` | (event lookup - data-dependent) | (none) | no | new node/lookup/capture needed (or blocked) |
| `GET /v1/cloudmonitorings/product/v2/addrbooks/{addrbookId}/members` | (addrbook node - missing) | (none) | no | new node/lookup/capture needed (or blocked) |

### management/iam-identity-center — 5 getid (0 from VALIDATED parent)

| endpoint | parent node (id source) | parent prov | wired? | action |
|---|---|---|---|---|
| `GET /v1/groups/{group_id}/users` | idc-group | docs | no | validate parent create first, then add verify |
| `GET /v1/instances/{instance_id}` | iam-identity-center | docs | no | validate parent create first, then add verify |
| `GET /v1/permission-sets/{permission_set_id}` | idc-permission-set | docs | no | validate parent create first, then add verify |
| `GET /v1/permission-sets/{permission_set_id}/policies` | idc-permission-set | docs | no | validate parent create first, then add verify |
| `GET /v1/users/{user_uuid}` | idc-user | docs | no | validate parent create first, then add verify |

### ai-ml/cloud-ml — 3 getid (0 from VALIDATED parent)

| endpoint | parent node (id source) | parent prov | wired? | action |
|---|---|---|---|---|
| `GET /v1/cloud-ml/clusters/{cluster_id}` | cloud-ml | docs | no | validate parent create first, then add verify |
| `GET /v1/cloud-ml/clusters/{cluster_id}/check-releasable` | cloud-ml | docs | no | validate parent create first, then add verify |
| `GET /v1/cloud-ml/clusters/{cluster_id}/estimate` | cloud-ml | docs | no | validate parent create first, then add verify |

### compute/multinodegpucluster — 2 getid (0 from VALIDATED parent)

| endpoint | parent node (id source) | parent prov | wired? | action |
|---|---|---|---|---|
| `GET /v1/cluster-fabrics/{cluster_fabric_id}` | gpu-node-fabric (lookup) | docs | no | validate parent create first, then add verify |
| `GET /v1/gpu-nodes/{gpu_node_id}` | gpu-node | docs | no | validate parent create first, then add verify |

### data-analytics/data-flow — 2 getid (0 from VALIDATED parent)

| endpoint | parent node (id source) | parent prov | wired? | action |
|---|---|---|---|---|
| `GET /v1/data-flow-services/{data_flow_service_name}/check-duplication` | data-flow-service | docs | no | validate parent create first, then add verify |
| `GET /v1/data-flows/{data_flow_name}/check-duplication` | data-flow | docs | no | validate parent create first, then add verify |

### data-analytics/data-ops — 2 getid (0 from VALIDATED parent)

| endpoint | parent node (id source) | parent prov | wired? | action |
|---|---|---|---|---|
| `GET /v1/data-ops-services/{data_ops_service_name}/check-duplication` | data-ops-service | docs | no | validate parent create first, then add verify |
| `GET /v1/data-ops/{data_ops_name}/check-duplication` | data-ops | docs | no | validate parent create first, then add verify |

### devops-tools/devopsservice — 2 getid (0 from VALIDATED parent)

| endpoint | parent node (id source) | parent prov | wired? | action |
|---|---|---|---|---|
| `GET /v1/devops-services/{devops_service_id}` | devops-service | docs | partial | validate parent create first, then add verify |
| `GET /v1/devops-services/{devops_service_id}/check-deletable` | devops-service | docs | partial | validate parent create first, then add verify |

### security/secretvault — 2 getid (0 from VALIDATED parent)

| endpoint | parent node (id source) | parent prov | wired? | action |
|---|---|---|---|---|
| `GET /v1/secretvault/{secret_vault_id}` | secretvault-vault | docs | no | validate parent create first, then add verify |
| `GET /v1/temporarykey/{secret_vault_id}` | secretvault-vault | docs | no | validate parent create first, then add verify |

### storage/baremetal-blockstorage — 2 getid (0 from VALIDATED parent)

| endpoint | parent node (id source) | parent prov | wired? | action |
|---|---|---|---|---|
| `GET /v1/volume-groups/{volume_group_id}/replications` | bm-volume-group | docs | no | validate parent create first, then add verify |
| `GET /v1/volumes/{volume_id}/replications` | bm-block-volume | docs | no | validate parent create first, then add verify |

### compute/baremetal — 1 getid (0 from VALIDATED parent)

| endpoint | parent node (id source) | parent prov | wired? | action |
|---|---|---|---|---|
| `GET /v1/baremetals/{baremetal_id}` | baremetal-server | docs | no | validate parent create first, then add verify |

### data-analytics/quick-query — 1 getid (0 from VALIDATED parent)

| endpoint | parent node (id source) | parent prov | wired? | action |
|---|---|---|---|---|
| `GET /v1/quick-query/{quick_query_name}/check-duplication` | quick-query | docs | no | validate parent create first, then add verify |

### management/cloudcontrol — 1 getid (0 from VALIDATED parent)

| endpoint | parent node (id source) | parent prov | wired? | action |
|---|---|---|---|---|
| `GET /v1/landing-zones/{landing_zone_id}` | cloudcontrol-landing-zone | docs | no | validate parent create first, then add verify |

### management/loggingaudit — 1 getid (0 from VALIDATED parent)

| endpoint | parent node (id source) | parent prov | wired? | action |
|---|---|---|---|---|
| `GET /v1/logs/{logging_id}` | (logging_id source unknown) | (none) | no | new node/lookup/capture needed (or blocked) |

### networking/gslb — 1 getid (0 from VALIDATED parent)

| endpoint | parent node (id source) | parent prov | wired? | action |
|---|---|---|---|---|
| `GET /v1/gslbs/{gslb_id}/resources` | gslb | docs | no | validate parent create first, then add verify |

### networking/loadbalancer — 1 getid (0 from VALIDATED parent)

| endpoint | parent node (id source) | parent prov | wired? | action |
|---|---|---|---|---|
| `GET /v1/loadbalancers/certificates/{lb_certificate_id}` | (lb-certificate node - missing) | (none) | no | new node/lookup/capture needed (or blocked) |

### security/configinspection — 1 getid (0 from VALIDATED parent)

| endpoint | parent node (id source) | parent prov | wired? | action |
|---|---|---|---|---|
| `GET /v1/configinspection/diagnosis/detail/{diagnosis_id}` | diagnosis | docs | no | validate parent create first, then add verify |

### security/kms — 1 getid (0 from VALIDATED parent)

| endpoint | parent node (id source) | parent prov | wired? | action |
|---|---|---|---|---|
| `GET /v1/managed-kms/transit/{key_id}` | (managed-kms lookup - missing) | (none) | no | new node/lookup/capture needed (or blocked) |

### storage/filestorage — 1 getid (0 from VALIDATED parent)

| endpoint | parent node (id source) | parent prov | wired? | action |
|---|---|---|---|---|
| `GET /v1/replications/{replication_id}` | fs-replication | docs | no | validate parent create first, then add verify |

## 3 · Blocked / gated (D) — not authorable without owner/console/modeling

- **storage/archivestorage (6)** — owner permanent exclusion (no dedicated auth
  key; 25/25 waivers). Parked.
- **management/iam-identity-center (5)** — owner-disabled blast-radius family
  (idc-* `enabled:false`, waivered); getid follows the write decision.
- **ai-ml/cloud-ml (3)** — needs `scr-auth-key` console credential (env-skip
  precondition).
- **management/iam accounts/* (2)** + service-account (1) — needs a real foreign
  `{account_id}` / a service-account node this caller does not own.
- **management/cloudmonitoring (2 of 5)** — `events/{eventId}` are
  data-dependent lookups (only resolvable if the account has fired events);
  the whole file is owned by another agent.
- **security/secretvault (2)** — needs `iam-temp-auth-key` console credential.
- **networking/dns public-domain (1)** — real public domain purchase, out of scope.
- **security/configinspection (1)** — needs `inspectable-account-auth-key`.
- **storage/baremetal-blockstorage (2)**, **compute/baremetal (1)** — likely need
  BM entitlement; bodies UNPROVEN.
- **new-node items** (subnet-vip, dns-record, volume-type lookup, lb-certificate,
  managed-kms lookup, image-member, addrbook, logging_id source) — cheap modeling
  (mostly read-only lookup nodes); see `docs/working/plans/COVERAGE-GETID-PLAN.md` §4.

## 4 · How a row gets closed (handoff to validation-agent)

1. **A rows** — nothing to author; the verify is present. Closes when
   `coverage-validator` composes the parent's `gen-<svc>` lifecycle and gets a
   live 2xx (the verify read rides the same run).
2. **B rows** — add the one-line `verify` read on the VALIDATED parent node
   (`expect_status: [200]`; `[200,404]` only for a legitimately-empty child +
   a `note:` why), recompose, run.
3. **C rows** — validate the parent **create** first (C5 promotion: live 2xx →
   flip `docs → VALIDATED` → cite run id), *then* add the verify (B becomes A).
   Adding a verify on an UNPROVEN create is dead weight — the composer must not
   run unproven creates.
4. **D rows** — stay parked until the owner/console/entitlement changes; tracked
   in `docs/working/plans/COVERAGE-GETID-PLAN.md` §5.
