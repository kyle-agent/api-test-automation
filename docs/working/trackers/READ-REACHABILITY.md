---
status: superseded (2026-06-18 생성 리포트 — 재생성 가능: python -m spec.read_reachability)
for: coverage
superseded_by: ../plans/CAMPAIGN-C3-100.md
---

> **⚠️ SUPERSEDED (2026-07-04, 일자 스냅샷).** 이 리포트는 언제든
> `python -m spec.read_reachability`로 **재생성**할 수 있는 정적 조인 산출물이며,
> 갭 작업의 현행 정본은 [`../plans/CAMPAIGN-C3-100.md`](../plans/CAMPAIGN-C3-100.md)다.
> 아래는 2026-06-18 생성분의 역사 기록.

# READ-REACHABILITY — id-bound GET reachability from the resource model

> Generated: **2026-06-18** by `python -m spec.read_reachability` (Piece 2 of the create→조회(show) coverage effort). Pure static catalog×sidecar join (`data/api_catalog_params.json` authoritative producers) — no network, no engine, no live model.
>
> Cross-ref: `docs/working/plans/COVERAGE-GETID-PLAN.md` §7 (probe_reads UNDER-SEEDING — the create→조회 gap) and its Piece 1 (engine auto-probe), Piece 2 (this report), Piece 3 (burn down model-gaps). The **model-gap** section below is Piece 3's worklist.

## Summary

Total id-bound GETs analyzed (services present in the model): **302**

| verdict | count | meaning |
|---|---|---|
| `model-gap` | 4 | a path-param has NO known producer (`produced_by`=null, not a waiver) — Piece 3 backlog |
| `waiver` | 7 | a path-param is an honest waiver (no producer exists: name-addressed / console-only / EOL) |
| `query-param` | 17 | path-params produced but a required query param blocks auto-probe |
| `cat2-needs-child` | 54 | produced via a child beyond the resource's own create spine |
| `cat1-auto` | 220 | auto-probe (Piece 1) fires it for free |

## model-gap worklist (Piece 3)

Every id-bound GET with at least one path-param the enrichment sidecar has NO known producer for (`produced_by`=null, not a waiver). The `∅` param is the one to close — find/declare a producer in `spec.enrich_catalog` (new capture / child node / list-recover sub-step), or tag it a waiver if none exists.

| service | GET path | unproduced param(s) |
|---|---|---|
| apigateway | `/v1/apis/{api_id}/resources/{resource_id}/methods/{method_type}` | `resource_id` |
| resourcemanager | `/v1/resources/{region}/{service}/{resource_type}/{resource_identifier}` | `service`, `resource_type` |
| resourcemanager | `/v1/tags/{region}/{service}/{resource_type}/{resource_identifier}` | `service`, `resource_type` |
| resourcemanager | `/v1/tags/{region}/{service}/{resource_type}/{resource_identifier}/{key}` | `service`, `resource_type`, `resource_identifier` |

**Unproduced path-params by frequency** (a single producer declaration may close several rows):

| param | # GETs blocked |
|---|---|
| `resource_type` | 3 |
| `service` | 3 |
| `resource_id` | 1 |
| `resource_identifier` | 1 |

## Per-service breakdown

Services sorted by `model-gap` count (descending).

### resourcemanager  (8 id-bound GET — model-gap=3 · waiver=1 · cat2-needs-child=2 · cat1-auto=2)

| GET path | path-params | producer node(s) | required query? | verdict |
|---|---|---|---|---|
| `/v1/resource-groups/{resource_group_id}` | `resource_group_id` | `resource_group_id`→management/resourcemanager/createresourcegroup (create) | no | `cat1-auto` |
| `/v1/resource-groups/{resource_group_id}/resources` | `resource_group_id` | `resource_group_id`→management/resourcemanager/createresourcegroup (create) | no | `cat1-auto` |
| `/v1/resources/{region}/{service}/{resource_type}/{resource_identifier}` | `region`, `service`, `resource_type`, `resource_identifier` | `region`→management/resourcemanager/listresources (lookup)<br>`service`→∅<br>`resource_type`→∅<br>`resource_identifier`→waiver | no | `model-gap` |
| `/v1/resources/{srn}` | `srn` | `srn`→management/resourcemanager/listresources (lookup) | no | `cat2-needs-child` |
| `/v1/tags/{region}/{service}/{resource_type}/{resource_identifier}` | `region`, `service`, `resource_type`, `resource_identifier` | `region`→management/resourcemanager/listtags (lookup)<br>`service`→∅<br>`resource_type`→∅<br>`resource_identifier`→waiver | no | `model-gap` |
| `/v1/tags/{region}/{service}/{resource_type}/{resource_identifier}/{key}` | `region`, `service`, `resource_type`, `resource_identifier`, `key` | `region`→management/resourcemanager/listtags (lookup)<br>`service`→∅<br>`resource_type`→∅<br>`resource_identifier`→∅<br>`key`→waiver | no | `model-gap` |
| `/v1/tags/{srn}` | `srn` | `srn`→management/resourcemanager/listtags (lookup) | no | `cat2-needs-child` |
| `/v1/tags/{srn}/{key}` | `srn`, `key` | `srn`→management/resourcemanager/listtags (lookup)<br>`key`→waiver | no | `waiver` |

### apigateway  (19 id-bound GET — model-gap=1 · query-param=1 · cat1-auto=17)

| GET path | path-params | producer node(s) | required query? | verdict |
|---|---|---|---|---|
| `/v1/apis/{api_id}` | `api_id` | `api_id`→application-service/apigateway/createapi (create) | no | `cat1-auto` |
| `/v1/apis/{api_id}/access-controls` | `api_id` | `api_id`→application-service/apigateway/createapi (create) | no | `cat1-auto` |
| `/v1/apis/{api_id}/access-controls/{access_control_id}` | `api_id`, `access_control_id` | `api_id`→application-service/apigateway/createapi (create)<br>`access_control_id`→application-service/apigateway/createaccesscontrols (create) | no | `cat1-auto` |
| `/v1/apis/{api_id}/auths` | `api_id` | `api_id`→application-service/apigateway/createapi (create) | no | `cat1-auto` |
| `/v1/apis/{api_id}/auths/{auth_id}` | `api_id`, `auth_id` | `api_id`→application-service/apigateway/createapi (create)<br>`auth_id`→application-service/apigateway/createauth (create) | no | `cat1-auto` |
| `/v1/apis/{api_id}/connected-endpoints` | `api_id` | `api_id`→application-service/apigateway/createapi (create) | no | `cat1-auto` |
| `/v1/apis/{api_id}/deployments` | `api_id` | `api_id`→application-service/apigateway/createapi (create) | no | `cat1-auto` |
| `/v1/apis/{api_id}/reports` | `api_id` | `api_id`→application-service/apigateway/createapi (create) | **yes**: stage_name, start_date, end_date | `query-param` |
| `/v1/apis/{api_id}/resource-policies` | `api_id` | `api_id`→application-service/apigateway/createapi (create) | no | `cat1-auto` |
| `/v1/apis/{api_id}/resources` | `api_id` | `api_id`→application-service/apigateway/createapi (create) | no | `cat1-auto` |
| `/v1/apis/{api_id}/resources/{resource_id}` | `api_id`, `resource_id` | `api_id`→application-service/apigateway/createapi (create)<br>`resource_id`→application-service/apigateway/createresource (create) | no | `cat1-auto` |
| `/v1/apis/{api_id}/resources/{resource_id}/methods` | `api_id`, `resource_id` | `api_id`→application-service/apigateway/createapi (create)<br>`resource_id`→application-service/apigateway/createresource (create) | no | `cat1-auto` |
| `/v1/apis/{api_id}/resources/{resource_id}/methods/{method_type}` | `api_id`, `resource_id`, `method_type` | `api_id`→application-service/apigateway/createapi (create)<br>`resource_id`→∅<br>`method_type`→application-service/apigateway/createmethod (create) | no | `model-gap` |
| `/v1/apis/{api_id}/stages` | `api_id` | `api_id`→application-service/apigateway/createapi (create) | no | `cat1-auto` |
| `/v1/apis/{api_id}/stages/{stage_name}` | `api_id`, `stage_name` | `api_id`→application-service/apigateway/createapi (create)<br>`stage_name`→application-service/apigateway/createstage (create) | no | `cat1-auto` |
| `/v1/apis/{api_id}/usage-plans` | `api_id` | `api_id`→application-service/apigateway/createapi (create) | no | `cat1-auto` |
| `/v1/apis/{api_id}/usage-plans/{usage_plan_id}` | `api_id`, `usage_plan_id` | `api_id`→application-service/apigateway/createapi (create)<br>`usage_plan_id`→application-service/apigateway/createusageplan (create) | no | `cat1-auto` |
| `/v1/apis/{api_id}/usage-plans/{usage_plan_id}/api-keys` | `api_id`, `usage_plan_id` | `api_id`→application-service/apigateway/createapi (create)<br>`usage_plan_id`→application-service/apigateway/createusageplan (create) | no | `cat1-auto` |
| `/v1/privatelink-endpoints/{privatelink_endpoint_id}` | `privatelink_endpoint_id` | `privatelink_endpoint_id`→application-service/apigateway/createprivatelinkendpoint (create) | no | `cat1-auto` |

### aimlops-platform  (6 id-bound GET — query-param=2 · cat2-needs-child=3 · cat1-auto=1)

| GET path | path-params | producer node(s) | required query? | verdict |
|---|---|---|---|---|
| `/v1/aimlops-platform/clusters/{cluster_id}/check-version` | `cluster_id` | `cluster_id`→container/ske/createcluster (create-xsvc) | **yes**: version | `query-param` |
| `/v1/aimlops-platform/clusters/{cluster_id}/validate-namespaces` | `cluster_id` | `cluster_id`→container/ske/createcluster (create-xsvc) | no | `cat2-needs-child` |
| `/v1/aimlops-platform/clusters/{cluster_id}/validate-resources` | `cluster_id` | `cluster_id`→container/ske/createcluster (create-xsvc) | **yes**: product_type | `query-param` |
| `/v1/aimlops-platform/internal/clusters/{cluster_id}/nodes` | `cluster_id` | `cluster_id`→container/ske/createcluster (create-xsvc) | no | `cat2-needs-child` |
| `/v1/aimlops-platform/internal/clusters/{cluster_id}/storageclasses` | `cluster_id` | `cluster_id`→container/ske/createcluster (create-xsvc) | no | `cat2-needs-child` |
| `/v1/aimlops-platform/{release_id}` | `release_id` | `release_id`→ai-ml/aimlops-platform/releaseaimlopsplatformv1 (create) | no | `cat1-auto` |

### archivestorage  (6 id-bound GET — query-param=2 · cat1-auto=4)

| GET path | path-params | producer node(s) | required query? | verdict |
|---|---|---|---|---|
| `/v1/archiving-policies/{archiving_policy_id}` | `archiving_policy_id` | `archiving_policy_id`→storage/archivestorage/createarchivingpolicy (create) | **yes**: bucket_id | `query-param` |
| `/v1/buckets/{bucket_id}` | `bucket_id` | `bucket_id`→storage/archivestorage/createbucket (create) | no | `cat1-auto` |
| `/v1/buckets/{bucket_id}/encryption` | `bucket_id` | `bucket_id`→storage/archivestorage/createbucket (create) | no | `cat1-auto` |
| `/v1/buckets/{bucket_id}/object-versions` | `bucket_id` | `bucket_id`→storage/archivestorage/createbucket (create) | **yes**: object_path | `query-param` |
| `/v1/buckets/{bucket_id}/objects` | `bucket_id` | `bucket_id`→storage/archivestorage/createbucket (create) | no | `cat1-auto` |
| `/v1/buckets/{bucket_id}/versioning` | `bucket_id` | `bucket_id`→storage/archivestorage/createbucket (create) | no | `cat1-auto` |

### backup  (10 id-bound GET — query-param=2 · cat1-auto=8)

| GET path | path-params | producer node(s) | required query? | verdict |
|---|---|---|---|---|
| `/v1/backup-agents/{backup_agent_id}` | `backup_agent_id` | `backup_agent_id`→storage/backup/createbackupagent (create) | no | `cat1-auto` |
| `/v1/backup-agents/{backup_agent_id}/check-connection-state` | `backup_agent_id` | `backup_agent_id`→storage/backup/createbackupagent (create) | no | `cat1-auto` |
| `/v1/backups/{backup_id}` | `backup_id` | `backup_id`→storage/backup/createbackup (create) | no | `cat1-auto` |
| `/v1/backups/{backup_id}/agent-backup-restore-targets` | `backup_id` | `backup_id`→storage/backup/createbackup (create) | no | `cat1-auto` |
| `/v1/backups/{backup_id}/backup-histories` | `backup_id` | `backup_id`→storage/backup/createbackup (create) | no | `cat1-auto` |
| `/v1/backups/{backup_id}/filesystem-path` | `backup_id` | `backup_id`→storage/backup/createbackup (create) | **yes**: restore_target_id, path | `query-param` |
| `/v1/backups/{backup_id}/restore-histories` | `backup_id` | `backup_id`→storage/backup/createbackup (create) | no | `cat1-auto` |
| `/v1/backups/{backup_id}/restore-targets` | `backup_id` | `backup_id`→storage/backup/createbackup (create) | no | `cat1-auto` |
| `/v1/backups/{backup_id}/restore/restorable-subnets` | `backup_id` | `backup_id`→storage/backup/createbackup (create) | **yes**: region | `query-param` |
| `/v1/backups/{backup_id}/schedules` | `backup_id` | `backup_id`→storage/backup/createbackup (create) | no | `cat1-auto` |

### baremetal  (1 id-bound GET — cat1-auto=1)

| GET path | path-params | producer node(s) | required query? | verdict |
|---|---|---|---|---|
| `/v1/baremetals/{baremetal_id}` | `baremetal_id` | `baremetal_id`→compute/baremetal/createbaremetals (create) | no | `cat1-auto` |

### baremetal-blockstorage  (6 id-bound GET — cat1-auto=6)

| GET path | path-params | producer node(s) | required query? | verdict |
|---|---|---|---|---|
| `/v1/volume-groups/{volume_group_id}` | `volume_group_id` | `volume_group_id`→storage/baremetal-blockstorage/createvolumegroup (create) | no | `cat1-auto` |
| `/v1/volume-groups/{volume_group_id}/replications` | `volume_group_id` | `volume_group_id`→storage/baremetal-blockstorage/createvolumegroup (create) | no | `cat1-auto` |
| `/v1/volume-groups/{volume_group_id}/snapshots` | `volume_group_id` | `volume_group_id`→storage/baremetal-blockstorage/createvolumegroup (create) | no | `cat1-auto` |
| `/v1/volumes/{volume_id}` | `volume_id` | `volume_id`→storage/baremetal-blockstorage/createvolume (create) | no | `cat1-auto` |
| `/v1/volumes/{volume_id}/replications` | `volume_id` | `volume_id`→storage/baremetal-blockstorage/createvolume (create) | no | `cat1-auto` |
| `/v1/volumes/{volume_id}/snapshots` | `volume_id` | `volume_id`→storage/baremetal-blockstorage/createvolume (create) | no | `cat1-auto` |

### billingplan  (1 id-bound GET — cat1-auto=1)

| GET path | path-params | producer node(s) | required query? | verdict |
|---|---|---|---|---|
| `/v1/planned-computes/{planned_compute_id}` | `planned_compute_id` | `planned_compute_id`→financial-management/billingplan/createplannedcomputes (create) | no | `cat1-auto` |

### budget  (1 id-bound GET — cat1-auto=1)

| GET path | path-params | producer node(s) | required query? | verdict |
|---|---|---|---|---|
| `/v1/budgets/account/{budget_id}` | `budget_id` | `budget_id`→financial-management/budget/createaccountbudget (create) | no | `cat1-auto` |

### cachestore  (6 id-bound GET — cat2-needs-child=2 · cat1-auto=4)

| GET path | path-params | producer node(s) | required query? | verdict |
|---|---|---|---|---|
| `/v1/clusters/{cluster_id}` | `cluster_id` | `cluster_id`→database/cachestore/cachestorecreatecluster (create) | no | `cat1-auto` |
| `/v1/clusters/{cluster_id}/backup-histories` | `cluster_id` | `cluster_id`→database/cachestore/cachestorecreatecluster (create) | no | `cat1-auto` |
| `/v1/clusters/{cluster_id}/commands` | `cluster_id` | `cluster_id`→database/cachestore/cachestorecreatecluster (create) | no | `cat1-auto` |
| `/v1/clusters/{cluster_id}/parameters` | `cluster_id` | `cluster_id`→database/cachestore/cachestorecreatecluster (create) | no | `cat1-auto` |
| `/v1/engine-versions/{dbaas_engine_version_id}/properties` | `dbaas_engine_version_id` | `dbaas_engine_version_id`→database/cachestore/cachestorelistengineversions (lookup) | no | `cat2-needs-child` |
| `/v1/requests/{request_id}` | `request_id` | `request_id`→database/cachestore/cachestorecreatecluster (async-op) | no | `cat2-needs-child` |

### cdn  (1 id-bound GET — cat1-auto=1)

| GET path | path-params | producer node(s) | required query? | verdict |
|---|---|---|---|---|
| `/v1/cdns/{id}` | `id` | `id`→networking/cdn/createcdnservice (create) | no | `cat1-auto` |

### certificatemanager  (1 id-bound GET — cat1-auto=1)

| GET path | path-params | producer node(s) | required query? | verdict |
|---|---|---|---|---|
| `/v1/certificatemanager/{certificate_id}` | `certificate_id` | `certificate_id`→security/certificatemanager/createcertificate (create) | no | `cat1-auto` |

### cloud-ml  (4 id-bound GET — cat2-needs-child=3 · cat1-auto=1)

| GET path | path-params | producer node(s) | required query? | verdict |
|---|---|---|---|---|
| `/v1/cloud-ml/clusters/{cluster_id}` | `cluster_id` | `cluster_id`→container/ske/createcluster (create-xsvc) | no | `cat2-needs-child` |
| `/v1/cloud-ml/clusters/{cluster_id}/check-releasable` | `cluster_id` | `cluster_id`→container/ske/createcluster (create-xsvc) | no | `cat2-needs-child` |
| `/v1/cloud-ml/clusters/{cluster_id}/estimate` | `cluster_id` | `cluster_id`→container/ske/createcluster (create-xsvc) | no | `cat2-needs-child` |
| `/v1/cloud-ml/{cloud_ml_id}` | `cloud_ml_id` | `cloud_ml_id`→ai-ml/cloud-ml/createcloudml (create) | no | `cat1-auto` |

### cloudcontrol  (2 id-bound GET — cat2-needs-child=1 · cat1-auto=1)

| GET path | path-params | producer node(s) | required query? | verdict |
|---|---|---|---|---|
| `/v1/guardrails/{guardrail_id}` | `guardrail_id` | `guardrail_id`→management/cloudcontrol/listguardrails (lookup) | no | `cat2-needs-child` |
| `/v1/landing-zones/{landing_zone_id}` | `landing_zone_id` | `landing_zone_id`→management/cloudcontrol/createlandingzone (create) | no | `cat1-auto` |

### cloudmonitoring  (6 id-bound GET — waiver=1 · query-param=1 · cat2-needs-child=2 · cat1-auto=2)

| GET path | path-params | producer node(s) | required query? | verdict |
|---|---|---|---|---|
| `/v1/cloudmonitorings/event/v2/event-policies/{eventPolicyId}` | `eventPolicyId` | `eventPolicyId`→management/cloudmonitoring/puteventpolicy (create) | no | `cat1-auto` |
| `/v1/cloudmonitorings/event/v2/event-policies/{eventPolicyId}/histories` | `eventPolicyId` | `eventPolicyId`→management/cloudmonitoring/puteventpolicy (create) | **yes**: queryStartDt, queryEndDt | `query-param` |
| `/v1/cloudmonitorings/event/v2/event-policies/{eventPolicyId}/notifications` | `eventPolicyId` | `eventPolicyId`→management/cloudmonitoring/puteventpolicy (create) | no | `cat1-auto` |
| `/v1/cloudmonitorings/event/v2/events/{eventId}` | `eventId` | `eventId`→management/cloudmonitoring/getproducteventlist (lookup) | no | `cat2-needs-child` |
| `/v1/cloudmonitorings/event/v2/events/{eventId}/notification-states` | `eventId` | `eventId`→management/cloudmonitoring/getproducteventlist (lookup) | no | `cat2-needs-child` |
| `/v1/cloudmonitorings/product/v2/addrbooks/{addrbookId}/members` | `addrbookId` | `addrbookId`→waiver | no | `waiver` |

### configinspection  (1 id-bound GET — cat1-auto=1)

| GET path | path-params | producer node(s) | required query? | verdict |
|---|---|---|---|---|
| `/v1/configinspection/diagnosis/detail/{diagnosis_id}` | `diagnosis_id` | `diagnosis_id`→security/configinspection/creatediagnosisobject (create) | no | `cat1-auto` |

### data-flow  (6 id-bound GET — cat2-needs-child=1 · cat1-auto=5)

| GET path | path-params | producer node(s) | required query? | verdict |
|---|---|---|---|---|
| `/v1/data-flow-services/data-flows/{data_flow_id}/sub-versions` | `data_flow_id` | `data_flow_id`→data-analytics/data-flow/createdataflow (create) | no | `cat1-auto` |
| `/v1/data-flow-services/{data_flow_service_id}` | `data_flow_service_id` | `data_flow_service_id`→data-analytics/data-flow/createdataflowserviceconsole (create) | no | `cat1-auto` |
| `/v1/data-flow-services/{data_flow_service_name}/check-duplication` | `data_flow_service_name` | `data_flow_service_name`→data-analytics/data-flow/createdataflowserviceconsole (create) | no | `cat1-auto` |
| `/v1/data-flows/clusters/{cluster_id}/ingress-controllers` | `cluster_id` | `cluster_id`→container/ske/createcluster (create-xsvc) | no | `cat2-needs-child` |
| `/v1/data-flows/{data_flow_id}` | `data_flow_id` | `data_flow_id`→data-analytics/data-flow/createdataflow (create) | no | `cat1-auto` |
| `/v1/data-flows/{data_flow_name}/check-duplication` | `data_flow_name` | `data_flow_name`→data-analytics/data-flow/createdataflow (create) | no | `cat1-auto` |

### data-ops  (6 id-bound GET — cat2-needs-child=1 · cat1-auto=5)

| GET path | path-params | producer node(s) | required query? | verdict |
|---|---|---|---|---|
| `/v1/data-ops-services/data-ops/{data_ops_id}/sub-versions` | `data_ops_id` | `data_ops_id`→data-analytics/data-ops/createdataops (create) | no | `cat1-auto` |
| `/v1/data-ops-services/{data_ops_service_id}` | `data_ops_service_id` | `data_ops_service_id`→data-analytics/data-ops/createdataopsservice (create) | no | `cat1-auto` |
| `/v1/data-ops-services/{data_ops_service_name}/check-duplication` | `data_ops_service_name` | `data_ops_service_name`→data-analytics/data-ops/createdataopsservice (create) | no | `cat1-auto` |
| `/v1/data-ops/clusters/{cluster_id}/ingress-controllers` | `cluster_id` | `cluster_id`→container/ske/createcluster (create-xsvc) | no | `cat2-needs-child` |
| `/v1/data-ops/{data_ops_id}` | `data_ops_id` | `data_ops_id`→data-analytics/data-ops/createdataops (create) | no | `cat1-auto` |
| `/v1/data-ops/{data_ops_name}/check-duplication` | `data_ops_name` | `data_ops_name`→data-analytics/data-ops/createdataops (create) | no | `cat1-auto` |

### devopsservice  (2 id-bound GET — cat1-auto=2)

| GET path | path-params | producer node(s) | required query? | verdict |
|---|---|---|---|---|
| `/v1/devops-services/{devops_service_id}` | `devops_service_id` | `devops_service_id`→devops-tools/devopsservice/createdevopsservice (create) | no | `cat1-auto` |
| `/v1/devops-services/{devops_service_id}/check-deletable` | `devops_service_id` | `devops_service_id`→devops-tools/devopsservice/createdevopsservice (create) | no | `cat1-auto` |

### direct-connect  (2 id-bound GET — cat1-auto=2)

| GET path | path-params | producer node(s) | required query? | verdict |
|---|---|---|---|---|
| `/v1/direct-connects/{direct_connect_id}` | `direct_connect_id` | `direct_connect_id`→networking/direct-connect/createdirectconnect (create) | no | `cat1-auto` |
| `/v1/direct-connects/{direct_connect_id}/routing-rules` | `direct_connect_id` | `direct_connect_id`→networking/direct-connect/createdirectconnect (create) | no | `cat1-auto` |

### dns  (5 id-bound GET — cat1-auto=5)

| GET path | path-params | producer node(s) | required query? | verdict |
|---|---|---|---|---|
| `/v1/hosted-zones/{hosted_zone_id}` | `hosted_zone_id` | `hosted_zone_id`→networking/dns/createhostedzone (create) | no | `cat1-auto` |
| `/v1/hosted-zones/{hosted_zone_id}/records` | `hosted_zone_id` | `hosted_zone_id`→networking/dns/createhostedzone (create) | no | `cat1-auto` |
| `/v1/hosted-zones/{hosted_zone_id}/records/{record_id}` | `hosted_zone_id`, `record_id` | `hosted_zone_id`→networking/dns/createhostedzone (create)<br>`record_id`→networking/dns/createhostedzonerecord (create) | no | `cat1-auto` |
| `/v1/private-dns/{private_dns_id}` | `private_dns_id` | `private_dns_id`→networking/dns/createprivatedns (create) | no | `cat1-auto` |
| `/v1/public-domain-names/{public_domain_id}` | `public_domain_id` | `public_domain_id`→networking/dns/createpublicdomainname (create) | no | `cat1-auto` |

### epas  (8 id-bound GET — cat2-needs-child=2 · cat1-auto=6)

| GET path | path-params | producer node(s) | required query? | verdict |
|---|---|---|---|---|
| `/v1/clusters/{cluster_id}` | `cluster_id` | `cluster_id`→database/epas/epascreatecluster (create) | no | `cat1-auto` |
| `/v1/clusters/{cluster_id}/archive` | `cluster_id` | `cluster_id`→database/epas/epascreatecluster (create) | no | `cat1-auto` |
| `/v1/clusters/{cluster_id}/backup-histories` | `cluster_id` | `cluster_id`→database/epas/epascreatecluster (create) | no | `cat1-auto` |
| `/v1/clusters/{cluster_id}/log-export-configs` | `cluster_id` | `cluster_id`→database/epas/epascreatecluster (create) | no | `cat1-auto` |
| `/v1/clusters/{cluster_id}/parameters` | `cluster_id` | `cluster_id`→database/epas/epascreatecluster (create) | no | `cat1-auto` |
| `/v1/clusters/{cluster_id}/replicas` | `cluster_id` | `cluster_id`→database/epas/epascreatecluster (create) | no | `cat1-auto` |
| `/v1/engine-versions/{dbaas_engine_version_id}/properties` | `dbaas_engine_version_id` | `dbaas_engine_version_id`→database/epas/epaslistengineversions (lookup) | no | `cat2-needs-child` |
| `/v1/requests/{request_id}` | `request_id` | `request_id`→database/epas/epascreatecluster (async-op) | no | `cat2-needs-child` |

### eventstreams  (4 id-bound GET — cat2-needs-child=2 · cat1-auto=2)

| GET path | path-params | producer node(s) | required query? | verdict |
|---|---|---|---|---|
| `/v1/clusters/{cluster_id}` | `cluster_id` | `cluster_id`→data-analytics/eventstreams/eventstreamscreatecluster (create) | no | `cat1-auto` |
| `/v1/clusters/{cluster_id}/parameters` | `cluster_id` | `cluster_id`→data-analytics/eventstreams/eventstreamscreatecluster (create) | no | `cat1-auto` |
| `/v1/engine-versions/{dbaas_engine_version_id}/properties` | `dbaas_engine_version_id` | `dbaas_engine_version_id`→data-analytics/eventstreams/eventstreamslistengineversions (lookup) | no | `cat2-needs-child` |
| `/v1/requests/{request_id}` | `request_id` | `request_id`→data-analytics/eventstreams/eventstreamscreatecluster (async-op) | no | `cat2-needs-child` |

### filestorage  (3 id-bound GET — query-param=1 · cat1-auto=2)

| GET path | path-params | producer node(s) | required query? | verdict |
|---|---|---|---|---|
| `/v1/replications/{replication_id}` | `replication_id` | `replication_id`→storage/filestorage/createvolumereplication (create) | **yes**: volume_id | `query-param` |
| `/v1/volumes/{volume_id}` | `volume_id` | `volume_id`→storage/filestorage/createvolume (create) | no | `cat1-auto` |
| `/v1/volumes/{volume_id}/access-rules` | `volume_id` | `volume_id`→storage/filestorage/createvolume (create) | no | `cat1-auto` |

### firewall  (2 id-bound GET — cat2-needs-child=1 · cat1-auto=1)

| GET path | path-params | producer node(s) | required query? | verdict |
|---|---|---|---|---|
| `/v1/firewalls/rules/{firewall_rule_id}` | `firewall_rule_id` | `firewall_rule_id`→networking/firewall/createfirewallrule (create) | no | `cat1-auto` |
| `/v1/firewalls/{firewall_id}` | `firewall_id` | `firewall_id`→networking/firewall/listfirewalls (lookup) | no | `cat2-needs-child` |

### gslb  (2 id-bound GET — cat1-auto=2)

| GET path | path-params | producer node(s) | required query? | verdict |
|---|---|---|---|---|
| `/v1/gslbs/{gslb_id}` | `gslb_id` | `gslb_id`→networking/gslb/creategslb (create) | no | `cat1-auto` |
| `/v1/gslbs/{gslb_id}/resources` | `gslb_id` | `gslb_id`→networking/gslb/creategslb (create) | no | `cat1-auto` |

### iam  (14 id-bound GET — cat2-needs-child=5 · cat1-auto=9)

| GET path | path-params | producer node(s) | required query? | verdict |
|---|---|---|---|---|
| `/v1/access-keys/{access_key_id}` | `access_key_id` | `access_key_id`→management/iam/accesskeycreate (create) | no | `cat1-auto` |
| `/v1/accounts/{account_id}/users` | `account_id` | `account_id`→management/cloudcontrol/createaccountfactoryaccount (create-xsvc) | no | `cat2-needs-child` |
| `/v1/accounts/{account_id}/users/{user_id}` | `account_id`, `user_id` | `account_id`→management/cloudcontrol/createaccountfactoryaccount (create-xsvc)<br>`user_id`→management/iam/createiamuser (create) | no | `cat2-needs-child` |
| `/v1/groups/{group_id}` | `group_id` | `group_id`→management/iam/creategroup (create) | no | `cat1-auto` |
| `/v1/groups/{group_id}/members` | `group_id` | `group_id`→management/iam/creategroup (create) | no | `cat1-auto` |
| `/v1/groups/{group_id}/policy-bindings` | `group_id` | `group_id`→management/iam/creategroup (create) | no | `cat1-auto` |
| `/v1/policies/{policy_id}` | `policy_id` | `policy_id`→management/iam/createpolicy (create) | no | `cat1-auto` |
| `/v1/policies/{policy_id}/bindings` | `policy_id` | `policy_id`→management/iam/createpolicy (create) | no | `cat1-auto` |
| `/v1/resource-policies/{srn}` | `srn` | `srn`→management/resourcemanager/createresourcegroup (create-xsvc) | no | `cat2-needs-child` |
| `/v1/roles/{role_id}` | `role_id` | `role_id`→management/iam/createrole (create) | no | `cat1-auto` |
| `/v1/roles/{role_id}/policy-bindings` | `role_id` | `role_id`→management/iam/createrole (create) | no | `cat1-auto` |
| `/v1/saml-providers/{saml_provider_id}` | `saml_provider_id` | `saml_provider_id`→management/iam/createsamlprovider (create) | no | `cat1-auto` |
| `/v1/service-accounts/{service_account_id}` | `service_account_id` | `service_account_id`→management/iam/listserviceaccount (lookup) | no | `cat2-needs-child` |
| `/v1/users/{user_id}/policy-bindings` | `user_id` | `user_id`→management/iam-identity-center/createuser (create-xsvc) | no | `cat2-needs-child` |

### iam-identity-center  (6 id-bound GET — query-param=5 · cat1-auto=1)

| GET path | path-params | producer node(s) | required query? | verdict |
|---|---|---|---|---|
| `/v1/groups/{group_id}` | `group_id` | `group_id`→management/iam-identity-center/creategroup (create) | **yes**: instance_id | `query-param` |
| `/v1/groups/{group_id}/users` | `group_id` | `group_id`→management/iam-identity-center/creategroup (create) | **yes**: instance_id | `query-param` |
| `/v1/instances/{instance_id}` | `instance_id` | `instance_id`→management/iam-identity-center/createinstance (create) | no | `cat1-auto` |
| `/v1/permission-sets/{permission_set_id}` | `permission_set_id` | `permission_set_id`→management/iam-identity-center/createpermissionset (create) | **yes**: instance_id | `query-param` |
| `/v1/permission-sets/{permission_set_id}/policies` | `permission_set_id` | `permission_set_id`→management/iam-identity-center/createpermissionset (create) | **yes**: instance_id | `query-param` |
| `/v1/users/{user_uuid}` | `user_uuid` | `user_uuid`→management/iam-identity-center/createuser (create) | **yes**: instance_id | `query-param` |

### kms  (3 id-bound GET — cat2-needs-child=1 · cat1-auto=2)

| GET path | path-params | producer node(s) | required query? | verdict |
|---|---|---|---|---|
| `/v1/kms/transit/{key_id}` | `key_id` | `key_id`→security/kms/createkey (create) | no | `cat1-auto` |
| `/v1/kms/transit/{key_id}/users` | `key_id` | `key_id`→security/kms/createkey (create) | no | `cat1-auto` |
| `/v1/managed-kms/transit/{key_id}` | `key_id` | `key_id`→security/kms/listmanagedkeys (lookup) | no | `cat2-needs-child` |

### loadbalancer  (9 id-bound GET — cat2-needs-child=1 · cat1-auto=8)

| GET path | path-params | producer node(s) | required query? | verdict |
|---|---|---|---|---|
| `/v1/lb-health-checks/{lb_health_check_id}` | `lb_health_check_id` | `lb_health_check_id`→networking/loadbalancer/createlbhealthcheck (create) | no | `cat1-auto` |
| `/v1/lb-listeners/{listener_id}` | `listener_id` | `listener_id`→networking/loadbalancer/createlblistener (create) | no | `cat1-auto` |
| `/v1/lb-server-groups/{lb_server_group_id}` | `lb_server_group_id` | `lb_server_group_id`→networking/loadbalancer/createlbservergroup (create) | no | `cat1-auto` |
| `/v1/lb-server-groups/{lb_server_group_id}/members` | `lb_server_group_id` | `lb_server_group_id`→networking/loadbalancer/createlbservergroup (create) | no | `cat1-auto` |
| `/v1/lb-server-groups/{lb_server_group_id}/members/{member_id}` | `lb_server_group_id`, `member_id` | `lb_server_group_id`→networking/loadbalancer/createlbservergroup (create)<br>`member_id`→networking/loadbalancer/addlbservergroupmembers (create) | no | `cat1-auto` |
| `/v1/loadbalancers/certificates/{lb_certificate_id}` | `lb_certificate_id` | `lb_certificate_id`→networking/loadbalancer/listloadbalancercertificates (lookup) | no | `cat2-needs-child` |
| `/v1/loadbalancers/{loadbalancer_id}` | `loadbalancer_id` | `loadbalancer_id`→networking/loadbalancer/createloadbalancer (create) | no | `cat1-auto` |
| `/v1/loadbalancers/{loadbalancer_id}/private-static-nats` | `loadbalancer_id` | `loadbalancer_id`→networking/loadbalancer/createloadbalancer (create) | no | `cat1-auto` |
| `/v1/loadbalancers/{loadbalancer_id}/static-nats` | `loadbalancer_id` | `loadbalancer_id`→networking/loadbalancer/createloadbalancer (create) | no | `cat1-auto` |

### loggingaudit  (2 id-bound GET — cat2-needs-child=1 · cat1-auto=1)

| GET path | path-params | producer node(s) | required query? | verdict |
|---|---|---|---|---|
| `/v1/logs/{logging_id}` | `logging_id` | `logging_id`→management/loggingaudit/listlogs (lookup) | no | `cat2-needs-child` |
| `/v1/trails/{trail_id}` | `trail_id` | `trail_id`→management/loggingaudit/createtrail (create) | no | `cat1-auto` |

### mariadb  (8 id-bound GET — cat2-needs-child=2 · cat1-auto=6)

| GET path | path-params | producer node(s) | required query? | verdict |
|---|---|---|---|---|
| `/v1/clusters/{cluster_id}` | `cluster_id` | `cluster_id`→database/mariadb/mariadbcreatecluster (create) | no | `cat1-auto` |
| `/v1/clusters/{cluster_id}/archive` | `cluster_id` | `cluster_id`→database/mariadb/mariadbcreatecluster (create) | no | `cat1-auto` |
| `/v1/clusters/{cluster_id}/backup-histories` | `cluster_id` | `cluster_id`→database/mariadb/mariadbcreatecluster (create) | no | `cat1-auto` |
| `/v1/clusters/{cluster_id}/log-export-configs` | `cluster_id` | `cluster_id`→database/mariadb/mariadbcreatecluster (create) | no | `cat1-auto` |
| `/v1/clusters/{cluster_id}/parameters` | `cluster_id` | `cluster_id`→database/mariadb/mariadbcreatecluster (create) | no | `cat1-auto` |
| `/v1/clusters/{cluster_id}/replicas` | `cluster_id` | `cluster_id`→database/mariadb/mariadbcreatecluster (create) | no | `cat1-auto` |
| `/v1/engine-versions/{dbaas_engine_version_id}/properties` | `dbaas_engine_version_id` | `dbaas_engine_version_id`→database/mariadb/mariadblistengineversions (lookup) | no | `cat2-needs-child` |
| `/v1/requests/{request_id}` | `request_id` | `request_id`→database/mariadb/mariadbcreatecluster (async-op) | no | `cat2-needs-child` |

### multinodegpucluster  (2 id-bound GET — cat2-needs-child=1 · cat1-auto=1)

| GET path | path-params | producer node(s) | required query? | verdict |
|---|---|---|---|---|
| `/v1/cluster-fabrics/{cluster_fabric_id}` | `cluster_fabric_id` | `cluster_fabric_id`→compute/multinodegpucluster/listclusterfabrics (lookup) | no | `cat2-needs-child` |
| `/v1/gpu-nodes/{gpu_node_id}` | `gpu_node_id` | `gpu_node_id`→compute/multinodegpucluster/creategpunodes (create) | no | `cat1-auto` |

### mysql  (8 id-bound GET — cat2-needs-child=2 · cat1-auto=6)

| GET path | path-params | producer node(s) | required query? | verdict |
|---|---|---|---|---|
| `/v1/clusters/{cluster_id}` | `cluster_id` | `cluster_id`→database/mysql/mysqlcreatecluster (create) | no | `cat1-auto` |
| `/v1/clusters/{cluster_id}/archive` | `cluster_id` | `cluster_id`→database/mysql/mysqlcreatecluster (create) | no | `cat1-auto` |
| `/v1/clusters/{cluster_id}/backup-histories` | `cluster_id` | `cluster_id`→database/mysql/mysqlcreatecluster (create) | no | `cat1-auto` |
| `/v1/clusters/{cluster_id}/log-export-configs` | `cluster_id` | `cluster_id`→database/mysql/mysqlcreatecluster (create) | no | `cat1-auto` |
| `/v1/clusters/{cluster_id}/parameters` | `cluster_id` | `cluster_id`→database/mysql/mysqlcreatecluster (create) | no | `cat1-auto` |
| `/v1/clusters/{cluster_id}/replicas` | `cluster_id` | `cluster_id`→database/mysql/mysqlcreatecluster (create) | no | `cat1-auto` |
| `/v1/engine-versions/{dbaas_engine_version_id}/properties` | `dbaas_engine_version_id` | `dbaas_engine_version_id`→database/mysql/mysqllistengineversions (lookup) | no | `cat2-needs-child` |
| `/v1/requests/{request_id}` | `request_id` | `request_id`→database/mysql/mysqlcreatecluster (async-op) | no | `cat2-needs-child` |

### organization  (5 id-bound GET — cat1-auto=5)

| GET path | path-params | producer node(s) | required query? | verdict |
|---|---|---|---|---|
| `/v1/organization-accounts/{account_id}` | `account_id` | `account_id`→management/organization/createaccount (create) | no | `cat1-auto` |
| `/v1/organization-units/{unit_id}` | `unit_id` | `unit_id`→management/organization/createorganizationunit (create) | no | `cat1-auto` |
| `/v1/organization-units/{unit_id}/parents` | `unit_id` | `unit_id`→management/organization/createorganizationunit (create) | no | `cat1-auto` |
| `/v1/organizations/{organization_id}` | `organization_id` | `organization_id`→management/organization/createorganization (create) | no | `cat1-auto` |
| `/v1/service-control-policies/{policy_id}` | `policy_id` | `policy_id`→management/organization/createservicecontrolpolicy (create) | no | `cat1-auto` |

### parallel-filestorage  (2 id-bound GET — cat1-auto=2)

| GET path | path-params | producer node(s) | required query? | verdict |
|---|---|---|---|---|
| `/v1/volumes/{volume_id}` | `volume_id` | `volume_id`→storage/parallel-filestorage/createvolume (create) | no | `cat1-auto` |
| `/v1/volumes/{volume_id}/access-rules` | `volume_id` | `volume_id`→storage/parallel-filestorage/createvolume (create) | no | `cat1-auto` |

### postgresql  (8 id-bound GET — cat2-needs-child=2 · cat1-auto=6)

| GET path | path-params | producer node(s) | required query? | verdict |
|---|---|---|---|---|
| `/v1/clusters/{cluster_id}` | `cluster_id` | `cluster_id`→database/postgresql/postgresqlcreatecluster (create) | no | `cat1-auto` |
| `/v1/clusters/{cluster_id}/archive` | `cluster_id` | `cluster_id`→database/postgresql/postgresqlcreatecluster (create) | no | `cat1-auto` |
| `/v1/clusters/{cluster_id}/backup-histories` | `cluster_id` | `cluster_id`→database/postgresql/postgresqlcreatecluster (create) | no | `cat1-auto` |
| `/v1/clusters/{cluster_id}/log-export-configs` | `cluster_id` | `cluster_id`→database/postgresql/postgresqlcreatecluster (create) | no | `cat1-auto` |
| `/v1/clusters/{cluster_id}/parameters` | `cluster_id` | `cluster_id`→database/postgresql/postgresqlcreatecluster (create) | no | `cat1-auto` |
| `/v1/clusters/{cluster_id}/replicas` | `cluster_id` | `cluster_id`→database/postgresql/postgresqlcreatecluster (create) | no | `cat1-auto` |
| `/v1/engine-versions/{dbaas_engine_version_id}/properties` | `dbaas_engine_version_id` | `dbaas_engine_version_id`→database/postgresql/postgresqllistengineversions (lookup) | no | `cat2-needs-child` |
| `/v1/requests/{request_id}` | `request_id` | `request_id`→database/postgresql/postgresqlcreatecluster (async-op) | no | `cat2-needs-child` |

### product  (2 id-bound GET — cat2-needs-child=2)

| GET path | path-params | producer node(s) | required query? | verdict |
|---|---|---|---|---|
| `/v1/product-categories/{category_id}` | `category_id` | `category_id`→platform/product/listproductcategories (lookup) | no | `cat2-needs-child` |
| `/v1/products/{product_id}` | `product_id` | `product_id`→platform/product/listproducts (lookup) | no | `cat2-needs-child` |

### queueservice  (2 id-bound GET — query-param=1 · cat1-auto=1)

| GET path | path-params | producer node(s) | required query? | verdict |
|---|---|---|---|---|
| `/v1/queues/{queue_id}` | `queue_id` | `queue_id`→application-service/queueservice/createqueue (create) | no | `cat1-auto` |
| `/v1/queues/{queue_id}/attributes` | `queue_id` | `queue_id`→application-service/queueservice/createqueue (create) | **yes**: attributes, name | `query-param` |

### quick-query  (2 id-bound GET — cat1-auto=2)

| GET path | path-params | producer node(s) | required query? | verdict |
|---|---|---|---|---|
| `/v1/quick-query/{quick_query_id}` | `quick_query_id` | `quick_query_id`→data-analytics/quick-query/createquickquery (create) | no | `cat1-auto` |
| `/v1/quick-query/{quick_query_name}/check-duplication` | `quick_query_name` | `quick_query_name`→data-analytics/quick-query/createquickquery (create) | no | `cat1-auto` |

### quota  (2 id-bound GET — cat2-needs-child=2)

| GET path | path-params | producer node(s) | required query? | verdict |
|---|---|---|---|---|
| `/v1/account-quotas/{account_quota_id}` | `account_quota_id` | `account_quota_id`→management/quota/listaccountquota (lookup) | no | `cat2-needs-child` |
| `/v1/quota-requests/{request_id}` | `request_id` | `request_id`→management/quota/listquotarequests (lookup) | no | `cat2-needs-child` |

### scf  (12 id-bound GET — cat2-needs-child=1 · cat1-auto=11)

| GET path | path-params | producer node(s) | required query? | verdict |
|---|---|---|---|---|
| `/v1/cloud-functions/{cloud_function_id}` | `cloud_function_id` | `cloud_function_id`→compute/scf/createcloudfunction (create) | no | `cat1-auto` |
| `/v1/cloud-functions/{cloud_function_id}/codes` | `cloud_function_id` | `cloud_function_id`→compute/scf/createcloudfunction (create) | no | `cat1-auto` |
| `/v1/cloud-functions/{cloud_function_id}/configurations` | `cloud_function_id` | `cloud_function_id`→compute/scf/createcloudfunction (create) | no | `cat1-auto` |
| `/v1/cloud-functions/{cloud_function_id}/configurations/config` | `cloud_function_id` | `cloud_function_id`→compute/scf/createcloudfunction (create) | no | `cat1-auto` |
| `/v1/cloud-functions/{cloud_function_id}/configurations/environment-variables` | `cloud_function_id` | `cloud_function_id`→compute/scf/createcloudfunction (create) | no | `cat1-auto` |
| `/v1/cloud-functions/{cloud_function_id}/configurations/privatelink-endpoints` | `cloud_function_id` | `cloud_function_id`→compute/scf/createcloudfunction (create) | no | `cat1-auto` |
| `/v1/cloud-functions/{cloud_function_id}/configurations/privatelink-services` | `cloud_function_id` | `cloud_function_id`→compute/scf/createcloudfunction (create) | no | `cat1-auto` |
| `/v1/cloud-functions/{cloud_function_id}/configurations/resource-policies` | `cloud_function_id` | `cloud_function_id`→compute/scf/createcloudfunction (create) | no | `cat1-auto` |
| `/v1/cloud-functions/{cloud_function_id}/configurations/url` | `cloud_function_id` | `cloud_function_id`→compute/scf/createcloudfunction (create) | no | `cat1-auto` |
| `/v1/cloud-functions/{cloud_function_id}/logs` | `cloud_function_id` | `cloud_function_id`→compute/scf/createcloudfunction (create) | no | `cat1-auto` |
| `/v1/cloud-functions/{cloud_function_id}/metrics` | `cloud_function_id` | `cloud_function_id`→compute/scf/createcloudfunction (create) | no | `cat1-auto` |
| `/v1/triggers/{trigger_id}` | `trigger_id` | `trigger_id`→compute/scf/listcloudfunctiontriggers (lookup) | no | `cat2-needs-child` |

### scr  (12 id-bound GET — waiver=5 · cat2-needs-child=3 · cat1-auto=4)

| GET path | path-params | producer node(s) | required query? | verdict |
|---|---|---|---|---|
| `/v1/container-registries/{registry_id}` | `registry_id` | `registry_id`→container/scr/createregistry (create) | no | `cat1-auto` |
| `/v1/container-registries/{registry_id}/repositories` | `registry_id` | `registry_id`→container/scr/createregistry (create) | no | `cat1-auto` |
| `/v1/images/{image_id}` | `image_id` | `image_id`→compute/virtualserver/createimage (create-xsvc) | no | `cat2-needs-child` |
| `/v1/images/{image_id}/lifecycle-policy/preview` | `image_id` | `image_id`→compute/virtualserver/createimage (create-xsvc) | no | `cat2-needs-child` |
| `/v1/images/{image_id}/tagses` | `image_id` | `image_id`→compute/virtualserver/createimage (create-xsvc) | no | `cat2-needs-child` |
| `/v1/repositories/{repository_id}` | `repository_id` | `repository_id`→container/scr/createrepository (create) | no | `cat1-auto` |
| `/v1/repositories/{repository_id}/images` | `repository_id` | `repository_id`→container/scr/createrepository (create) | no | `cat1-auto` |
| `/v1/tagses/{tags_id}` | `tags_id` | `tags_id`→waiver | no | `waiver` |
| `/v1/tagses/{tags_id}/download/manifest` | `tags_id` | `tags_id`→waiver | no | `waiver` |
| `/v1/tagses/{tags_id}/packages` | `tags_id` | `tags_id`→waiver | no | `waiver` |
| `/v1/tagses/{tags_id}/secrets` | `tags_id` | `tags_id`→waiver | no | `waiver` |
| `/v1/tagses/{tags_id}/vulnerabilities` | `tags_id` | `tags_id`→waiver | no | `waiver` |

### searchengine  (4 id-bound GET — cat2-needs-child=2 · cat1-auto=2)

| GET path | path-params | producer node(s) | required query? | verdict |
|---|---|---|---|---|
| `/v1/clusters/{cluster_id}` | `cluster_id` | `cluster_id`→data-analytics/searchengine/searchenginecreatecluster (create) | no | `cat1-auto` |
| `/v1/clusters/{cluster_id}/backup-histories` | `cluster_id` | `cluster_id`→data-analytics/searchengine/searchenginecreatecluster (create) | no | `cat1-auto` |
| `/v1/engine-versions/{dbaas_engine_version_id}/properties` | `dbaas_engine_version_id` | `dbaas_engine_version_id`→data-analytics/searchengine/searchenginelistengineversions (lookup) | no | `cat2-needs-child` |
| `/v1/requests/{request_id}` | `request_id` | `request_id`→data-analytics/searchengine/searchenginecreatecluster (async-op) | no | `cat2-needs-child` |

### secretsmanager  (2 id-bound GET — cat1-auto=2)

| GET path | path-params | producer node(s) | required query? | verdict |
|---|---|---|---|---|
| `/v1/secrets/{secret_id}` | `secret_id` | `secret_id`→security/secretsmanager/createsecretsmanager (create) | no | `cat1-auto` |
| `/v1/secrets/{secret_id}/versions` | `secret_id` | `secret_id`→security/secretsmanager/createsecretsmanager (create) | no | `cat1-auto` |

### secretvault  (2 id-bound GET — cat1-auto=2)

| GET path | path-params | producer node(s) | required query? | verdict |
|---|---|---|---|---|
| `/v1/secretvault/{secret_vault_id}` | `secret_vault_id` | `secret_vault_id`→security/secretvault/createsecretvault (create) | no | `cat1-auto` |
| `/v1/temporarykey/{secret_vault_id}` | `secret_vault_id` | `secret_vault_id`→security/secretvault/createsecretvault (create) | no | `cat1-auto` |

### security-group  (2 id-bound GET — cat1-auto=2)

| GET path | path-params | producer node(s) | required query? | verdict |
|---|---|---|---|---|
| `/v1/security-group-rules/{security_group_rule_id}` | `security_group_rule_id` | `security_group_rule_id`→networking/security-group/createsecuritygrouprule (create) | no | `cat1-auto` |
| `/v1/security-groups/{security_group_id}` | `security_group_id` | `security_group_id`→networking/security-group/createsecuritygroup (create) | no | `cat1-auto` |

### servicewatch  (5 id-bound GET — cat1-auto=5)

| GET path | path-params | producer node(s) | required query? | verdict |
|---|---|---|---|---|
| `/v1/alerts/{id}` | `id` | `id`→management/servicewatch/createalert (create) | no | `cat1-auto` |
| `/v1/dashboards/{dashboard_id}` | `dashboard_id` | `dashboard_id`→management/servicewatch/createdashboard (create) | no | `cat1-auto` |
| `/v1/event-rules/{event_rule_id}` | `event_rule_id` | `event_rule_id`→management/servicewatch/createeventrule (create) | no | `cat1-auto` |
| `/v1/log-groups/{log_group_id}` | `log_group_id` | `log_group_id`→management/servicewatch/createloggroup (create) | no | `cat1-auto` |
| `/v1/log-groups/{log_group_id}/log-streams/{log_stream_id}` | `log_group_id`, `log_stream_id` | `log_group_id`→management/servicewatch/createloggroup (create)<br>`log_stream_id`→management/servicewatch/createloggrouplogstream (create) | no | `cat1-auto` |

### ske  (6 id-bound GET — query-param=2 · cat1-auto=4)

| GET path | path-params | producer node(s) | required query? | verdict |
|---|---|---|---|---|
| `/v1/clusters/{cluster_id}` | `cluster_id` | `cluster_id`→container/ske/createcluster (create) | no | `cat1-auto` |
| `/v1/clusters/{cluster_id}/kubeconfig` | `cluster_id` | `cluster_id`→container/ske/createcluster (create) | **yes**: kubeconfig_type | `query-param` |
| `/v1/clusters/{cluster_id}/nodepools` | `cluster_id` | `cluster_id`→container/ske/createcluster (create) | no | `cat1-auto` |
| `/v1/clusters/{cluster_id}/user-kubeconfig` | `cluster_id` | `cluster_id`→container/ske/createcluster (create) | **yes**: kubeconfig_type | `query-param` |
| `/v1/nodepools/{nodepool_id}` | `nodepool_id` | `nodepool_id`→container/ske/createnodepool (create) | no | `cat1-auto` |
| `/v1/nodepools/{nodepool_id}/nodes` | `nodepool_id` | `nodepool_id`→container/ske/createnodepool (create) | no | `cat1-auto` |

### sqlserver  (6 id-bound GET — cat2-needs-child=2 · cat1-auto=4)

| GET path | path-params | producer node(s) | required query? | verdict |
|---|---|---|---|---|
| `/v1/clusters/{cluster_id}` | `cluster_id` | `cluster_id`→database/sqlserver/sqlservercreatecluster (create) | no | `cat1-auto` |
| `/v1/clusters/{cluster_id}/backup-histories` | `cluster_id` | `cluster_id`→database/sqlserver/sqlservercreatecluster (create) | no | `cat1-auto` |
| `/v1/clusters/{cluster_id}/log-export-configs` | `cluster_id` | `cluster_id`→database/sqlserver/sqlservercreatecluster (create) | no | `cat1-auto` |
| `/v1/clusters/{cluster_id}/parameters` | `cluster_id` | `cluster_id`→database/sqlserver/sqlservercreatecluster (create) | no | `cat1-auto` |
| `/v1/engine-versions/{dbaas_engine_version_id}/properties` | `dbaas_engine_version_id` | `dbaas_engine_version_id`→database/sqlserver/sqlserverlistengineversions (lookup) | no | `cat2-needs-child` |
| `/v1/requests/{request_id}` | `request_id` | `request_id`→database/sqlserver/sqlservercreatecluster (async-op) | no | `cat2-needs-child` |

### support  (2 id-bound GET — cat2-needs-child=2)

| GET path | path-params | producer node(s) | required query? | verdict |
|---|---|---|---|---|
| `/v1/inquiries/{inquiry_id}` | `inquiry_id` | `inquiry_id`→management/support/getinquirylist (lookup) | no | `cat2-needs-child` |
| `/v1/service-requests/{service_request_id}` | `service_request_id` | `service_request_id`→management/support/getservicerequestlist (lookup) | no | `cat2-needs-child` |

### vertica  (4 id-bound GET — cat2-needs-child=2 · cat1-auto=2)

| GET path | path-params | producer node(s) | required query? | verdict |
|---|---|---|---|---|
| `/v1/clusters/{cluster_id}` | `cluster_id` | `cluster_id`→data-analytics/vertica/verticacreatecluster (create) | no | `cat1-auto` |
| `/v1/clusters/{cluster_id}/backup-histories` | `cluster_id` | `cluster_id`→data-analytics/vertica/verticacreatecluster (create) | no | `cat1-auto` |
| `/v1/engine-versions/{dbaas_engine_version_id}/properties` | `dbaas_engine_version_id` | `dbaas_engine_version_id`→data-analytics/vertica/verticalistengineversions (lookup) | no | `cat2-needs-child` |
| `/v1/requests/{request_id}` | `request_id` | `request_id`→data-analytics/vertica/verticacreatecluster (async-op) | no | `cat2-needs-child` |

### virtualserver  (29 id-bound GET — cat2-needs-child=3 · cat1-auto=26)

| GET path | path-params | producer node(s) | required query? | verdict |
|---|---|---|---|---|
| `/v1/auto-scaling-groups/{auto_scaling_group_id}` | `auto_scaling_group_id` | `auto_scaling_group_id`→compute/virtualserver/createautoscalinggroup (create) | no | `cat1-auto` |
| `/v1/auto-scaling-groups/{auto_scaling_group_id}/lb-server-groups` | `auto_scaling_group_id` | `auto_scaling_group_id`→compute/virtualserver/createautoscalinggroup (create) | no | `cat1-auto` |
| `/v1/auto-scaling-groups/{auto_scaling_group_id}/notifications` | `auto_scaling_group_id` | `auto_scaling_group_id`→compute/virtualserver/createautoscalinggroup (create) | no | `cat1-auto` |
| `/v1/auto-scaling-groups/{auto_scaling_group_id}/notifications/{notification_id}` | `auto_scaling_group_id`, `notification_id` | `auto_scaling_group_id`→compute/virtualserver/createautoscalinggroup (create)<br>`notification_id`→compute/virtualserver/createautoscalinggroupnotification (create) | no | `cat1-auto` |
| `/v1/auto-scaling-groups/{auto_scaling_group_id}/policies` | `auto_scaling_group_id` | `auto_scaling_group_id`→compute/virtualserver/createautoscalinggroup (create) | no | `cat1-auto` |
| `/v1/auto-scaling-groups/{auto_scaling_group_id}/policies/{policy_id}` | `auto_scaling_group_id`, `policy_id` | `auto_scaling_group_id`→compute/virtualserver/createautoscalinggroup (create)<br>`policy_id`→compute/virtualserver/createautoscalinggrouppolicy (create) | no | `cat1-auto` |
| `/v1/auto-scaling-groups/{auto_scaling_group_id}/schedules` | `auto_scaling_group_id` | `auto_scaling_group_id`→compute/virtualserver/createautoscalinggroup (create) | no | `cat1-auto` |
| `/v1/auto-scaling-groups/{auto_scaling_group_id}/schedules/{schedule_id}` | `auto_scaling_group_id`, `schedule_id` | `auto_scaling_group_id`→compute/virtualserver/createautoscalinggroup (create)<br>`schedule_id`→compute/virtualserver/createautoscalinggroupschedule (create) | no | `cat1-auto` |
| `/v1/auto-scaling-groups/{auto_scaling_group_id}/virtual-servers` | `auto_scaling_group_id` | `auto_scaling_group_id`→compute/virtualserver/createautoscalinggroup (create) | no | `cat1-auto` |
| `/v1/images/{image_id}` | `image_id` | `image_id`→compute/virtualserver/createimage (create) | no | `cat1-auto` |
| `/v1/images/{image_id}/members` | `image_id` | `image_id`→compute/virtualserver/createimage (create) | no | `cat1-auto` |
| `/v1/images/{image_id}/members/{member_id}` | `image_id`, `member_id` | `image_id`→compute/virtualserver/createimage (create)<br>`member_id`→compute/virtualserver/createimagemember (create) | no | `cat1-auto` |
| `/v1/keypairs/{keypair_name}` | `keypair_name` | `keypair_name`→compute/virtualserver/createkeypair (create) | no | `cat1-auto` |
| `/v1/launch-configurations/{launch_configuration_id}` | `launch_configuration_id` | `launch_configuration_id`→compute/virtualserver/createlaunchconfiguration (create) | no | `cat1-auto` |
| `/v1/server-groups/{server_group_id}` | `server_group_id` | `server_group_id`→compute/virtualserver/createservergroup (create) | no | `cat1-auto` |
| `/v1/server-types/{server_type_id}` | `server_type_id` | `server_type_id`→compute/virtualserver/listservertypes (lookup) | no | `cat2-needs-child` |
| `/v1/servers/{server_id}` | `server_id` | `server_id`→compute/virtualserver/createvirtualserver (create) | no | `cat1-auto` |
| `/v1/servers/{server_id}/console-log` | `server_id` | `server_id`→compute/virtualserver/createvirtualserver (create) | no | `cat1-auto` |
| `/v1/servers/{server_id}/interfaces` | `server_id` | `server_id`→compute/virtualserver/createvirtualserver (create) | no | `cat1-auto` |
| `/v1/servers/{server_id}/interfaces/{port_id}` | `server_id`, `port_id` | `server_id`→compute/virtualserver/createvirtualserver (create)<br>`port_id`→compute/virtualserver/createserverinterface (create) | no | `cat1-auto` |
| `/v1/servers/{server_id}/ips` | `server_id` | `server_id`→compute/virtualserver/createvirtualserver (create) | no | `cat1-auto` |
| `/v1/servers/{server_id}/ips/{subnet_id}` | `server_id`, `subnet_id` | `server_id`→compute/virtualserver/createvirtualserver (create)<br>`subnet_id`→networking/vpc/createsubnet (create-xsvc) | no | `cat2-needs-child` |
| `/v1/servers/{server_id}/security-groups` | `server_id` | `server_id`→compute/virtualserver/createvirtualserver (create) | no | `cat1-auto` |
| `/v1/servers/{server_id}/volumes` | `server_id` | `server_id`→compute/virtualserver/createvirtualserver (create) | no | `cat1-auto` |
| `/v1/servers/{server_id}/volumes/{volume_id}` | `server_id`, `volume_id` | `server_id`→compute/virtualserver/createvirtualserver (create)<br>`volume_id`→compute/virtualserver/createservervolume (create) | no | `cat1-auto` |
| `/v1/snapshots/{snapshot_id}` | `snapshot_id` | `snapshot_id`→compute/virtualserver/createsnapshot (create) | no | `cat1-auto` |
| `/v1/volume-transfer/{transfer_id}` | `transfer_id` | `transfer_id`→compute/virtualserver/createvolumetransfer (create) | no | `cat1-auto` |
| `/v1/volume-types/{volume_type_id}` | `volume_type_id` | `volume_type_id`→compute/virtualserver/listvolumetypes (lookup) | no | `cat2-needs-child` |
| `/v1/volumes/{volume_id}` | `volume_id` | `volume_id`→compute/virtualserver/createvolume (create) | no | `cat1-auto` |

### vpc  (20 id-bound GET — cat1-auto=20)

| GET path | path-params | producer node(s) | required query? | verdict |
|---|---|---|---|---|
| `/v1/internet-gateways/{internet_gateway_id}` | `internet_gateway_id` | `internet_gateway_id`→networking/vpc/createinternetgateway (create) | no | `cat1-auto` |
| `/v1/nat-gateways/{nat_gateway_id}` | `nat_gateway_id` | `nat_gateway_id`→networking/vpc/createnatgateway (create) | no | `cat1-auto` |
| `/v1/ports/{port_id}` | `port_id` | `port_id`→networking/vpc/createport (create) | no | `cat1-auto` |
| `/v1/private-nats/{private_nat_id}` | `private_nat_id` | `private_nat_id`→networking/vpc/createprivatenat (create) | no | `cat1-auto` |
| `/v1/private-nats/{private_nat_id}/private-nat-ips` | `private_nat_id` | `private_nat_id`→networking/vpc/createprivatenat (create) | no | `cat1-auto` |
| `/v1/privatelink-endpoints/{privatelink_endpoint_id}` | `privatelink_endpoint_id` | `privatelink_endpoint_id`→networking/vpc/createprivatelinkendpoint (create) | no | `cat1-auto` |
| `/v1/privatelink-services/{privatelink_service_id}` | `privatelink_service_id` | `privatelink_service_id`→networking/vpc/createprivatelinkservice (create) | no | `cat1-auto` |
| `/v1/privatelink-services/{privatelink_service_id}/connected-endpoints` | `privatelink_service_id` | `privatelink_service_id`→networking/vpc/createprivatelinkservice (create) | no | `cat1-auto` |
| `/v1/publicips/{publicip_id}` | `publicip_id` | `publicip_id`→networking/vpc/createpublicip (create) | no | `cat1-auto` |
| `/v1/subnets/{subnet_id}` | `subnet_id` | `subnet_id`→networking/vpc/createsubnet (create) | no | `cat1-auto` |
| `/v1/subnets/{subnet_id}/sap-secondary-subnets` | `subnet_id` | `subnet_id`→networking/vpc/createsubnet (create) | no | `cat1-auto` |
| `/v1/subnets/{subnet_id}/vips` | `subnet_id` | `subnet_id`→networking/vpc/createsubnet (create) | no | `cat1-auto` |
| `/v1/subnets/{subnet_id}/vips/{vip_id}` | `subnet_id`, `vip_id` | `subnet_id`→networking/vpc/createsubnet (create)<br>`vip_id`→networking/vpc/createsubnetvip (create) | no | `cat1-auto` |
| `/v1/transit-gateways/{transit_gateway_id}` | `transit_gateway_id` | `transit_gateway_id`→networking/vpc/createtransitgateway (create) | no | `cat1-auto` |
| `/v1/transit-gateways/{transit_gateway_id}/routing-rules` | `transit_gateway_id` | `transit_gateway_id`→networking/vpc/createtransitgateway (create) | no | `cat1-auto` |
| `/v1/transit-gateways/{transit_gateway_id}/vpc-connections` | `transit_gateway_id` | `transit_gateway_id`→networking/vpc/createtransitgateway (create) | no | `cat1-auto` |
| `/v1/vpc-endpoints/{vpc_endpoint_id}` | `vpc_endpoint_id` | `vpc_endpoint_id`→networking/vpc/createvpcendpoint (create) | no | `cat1-auto` |
| `/v1/vpc-peerings/{vpc_peering_id}` | `vpc_peering_id` | `vpc_peering_id`→networking/vpc/createvpcpeering (create) | no | `cat1-auto` |
| `/v1/vpc-peerings/{vpc_peering_id}/routing-rules` | `vpc_peering_id` | `vpc_peering_id`→networking/vpc/createvpcpeering (create) | no | `cat1-auto` |
| `/v1/vpcs/{vpc_id}` | `vpc_id` | `vpc_id`→networking/vpc/createvpc (create) | no | `cat1-auto` |

### vpn  (2 id-bound GET — cat1-auto=2)

| GET path | path-params | producer node(s) | required query? | verdict |
|---|---|---|---|---|
| `/v1/vpn-gateways/{vpn_gateway_id}` | `vpn_gateway_id` | `vpn_gateway_id`→networking/vpn/createvpngateway (create) | no | `cat1-auto` |
| `/v1/vpn-tunnels/{vpn_tunnel_id}` | `vpn_tunnel_id` | `vpn_tunnel_id`→networking/vpn/createvpntunnel (create) | no | `cat1-auto` |

