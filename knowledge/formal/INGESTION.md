# Userguide ingestion backlog (짬짬이 내부 지식화 추적표)

> **최종 목표:** userguide 전체를 읽고 ① 서비스별 테스트 시나리오, ② 여러
> 서비스를 엮은 조합 시나리오, ③ 옵션 항목 변형(C4 parameter coverage)까지
> 만든다. **당장은** 커버리지 100%(C3)에 필요한 문서부터 — 이 표가 그
> 우선순위와 진행 상태의 단일 추적표다. 아무 세션이나 P1부터 집어
> `services/<category>__<service>.yaml`로 지식화하고 status를 갱신할 것.

**How to ingest one service** (any session, ~5 min each):

1. WebFetch `https://docs.e.samsungsdscloud.com<path>` — extract 제약 사항
   (constraints/limits/naming rules) + 선행 서비스 (prerequisites).
2. Write/update `knowledge/formal/services/<api-category>__<api-service>.yaml`
   (`provenance: docs`; schema in `FORMAT.md`). Prerequisites that add graph
   edges go to `cross-service.yaml`.
3. `python knowledge/formal/validate.py` → commit → flip status here.

> **M5 이후의 최종 목적지 (2026-06-12):** 형식화의 종착 레이어는
> **`knowledge/formal/resources/<category>__<service>.yaml`의 자원 task
> 모델**이다 (127 노드 — 의존조건·검증된 body 템플릿·옵션; 합성기가 여기서
> lifecycle을 생성한다, `docs/RESOURCE-MODEL-PLAN.md`). 신규 ingest에서
> 생성 전제조건/옵션/한도 같은 *자원 단위* 지식은 해당 resources 파일의
> `requires`/`options`/`notes`로 넣고, 서비스 전역 제약(네이밍 규칙 등)은
> 종전대로 `services/` Layer-1에 넣는다. 둘 다 같은 validator가 검사한다.

Priorities: **P1** = needed for coverage 100% (an enabled lifecycle's writes
are pending live validation, or smoke shows 4xx that naming-rule/prereq
knowledge could fix) · **P2** = has Open APIs, not currently blocking ·
**P3** = no Open API in the catalog (console-only; context for AI-evaluator).

| St | Pri | Userguide page | API service |
|----|-----|---------------|-------------|
| ✅ | P1 | /userguide/networking/vpc/overview/ | networking/vpc |
| ✅ | P1 | /userguide/networking/security_group/overview/ | networking/security-group |
| ✅ | P1 | /userguide/compute/virtual_server/overview/ | compute/virtualserver |
| ✅ | P1 | /userguide/container/k8s_engine/overview/ | container/ske |
| ✅ | P1 | /userguide/container/container_registry/overview/ | container/scr |
| ✅ | P1 | /userguide/storage/file_storage/overview/ | storage/filestorage |
| ✅ | P1 | /userguide/networking/load_balancer/overview/ | networking/loadbalancer |
| ✅ | P1 | /userguide/networking/dns/overview/ | networking/dns |
| ✅ | P1 | /userguide/networking/vpn/overview/ | networking/vpn |
| ✅ | P1 | /userguide/networking/direct_connect/overview/ | networking/direct-connect |
| ✅ | P1 | /userguide/networking/firewall/overview/ | networking/firewall |
| ✅ | P1 | /userguide/networking/gslb/overview/ | networking/gslb |
| ✅ | P1 | /userguide/networking/global_cdn/overview/ | networking/cdn |
| ✅ | P1 | /userguide/database/mysql/overview/ | database/mysql |
| ✅ | P1 | /userguide/database/mariadb/overview/ | database/mariadb |
| ✅ | P1 | /userguide/database/postgresql/overview/ | database/postgresql |
| ✅ | P1 | /userguide/database/epas/overview/ | database/epas |
| ✅ | P1 | /userguide/database/mssql/overview/ | database/sqlserver — Always On Secondary는 SQL Server License Key 필요(owner credential 대기, IB-017) |
| ✅ | P1 | /userguide/database/cachestore/overview/ | database/cachestore |
| ✅ | P1 | /userguide/compute/baremetal_server/overview/ | compute/baremetal |
| ✅ | P1 | /userguide/compute/block_storage_vm/overview/ | compute/virtualserver (volumes) |
| ✅ | P1 | /userguide/compute/cloud_functions/overview/ | compute/scf |
| ✅ | P1 | /userguide/compute/mngc/overview/ | compute/multinodegpucluster (bare-metal GPU; VPC prereq; types g2c96h8_metal/g3c128b8_metal; min 2 +1 scale; cluster-fabric companion-minted; Planned Compute optional; all docs/UNPROVEN) |
| ✅ | P1 | /userguide/storage/archive_storage/overview/ | storage/archivestorage — **대상 영구 제외** (owner 2026-06-11: 전용 인증키 미발급, waiver 25/25; 다시 끌어올리지 말 것) |
| ✅ | P1 | /userguide/storage/backup/overview/ | storage/backup |
| ✅ | P1 | /userguide/storage/block_storage_bm/overview/ | storage/baremetal-blockstorage |
| ✅ | P1 | /userguide/storage/parallel_file_storage/overview/ | storage/parallel-filestorage — **reads-only 범위** (owner 2026-06-12: writes는 owner-exclusion waiver, 복구 lifecycle disabled) |
| ✅ | P1 | /userguide/security/kms/overview/ | security/kms |
| ✅ | P1 | /userguide/security/secrets_manager/overview/ | security/secretsmanager |
| ✅ | P1 | /userguide/security/secret_vault/overview/ | security/secretvault |
| ✅ | P1 | /userguide/security/certificate_manager/overview/ | security/certificatemanager |
| ✅ | P1 | /userguide/security/config_inspection_enterprise/overview/ | security/configinspection |
| ✅ | P1 | /userguide/management/iam/overview/ | management/iam |
| ✅ | P1 | /userguide/management/id_center/overview/ | management/iam-identity-center (smoke 400s) |
| ✅ | P1 | /userguide/management/organization/overview/ | management/organization (waiver class) |
| ✅ | P1 | /userguide/management/cloud_control/overview/ | management/cloudcontrol (smoke 403s) |
| ✅ | P1 | /userguide/management/cloud_monitoring/overview/ | management/cloudmonitoring (smoke 400s = events/event-policies need productResourceId+eventState+queryStartDt+queryEndDt; X-ResourceType header was invention, removed; discontinued after Sep 2026) |
| ✅ | P1 | /userguide/management/logging_audit/overview/ | management/loggingaudit (trail needs Object Storage bucket; create body=TrailCreateRequestV1dot1 account_id+bucket_name+bucket_region; capture $.trail.id) |
| ✅ | P1 | /userguide/management/service_watch/overview/ | management/servicewatch |
| ✅ | P1 | /userguide/management/resource_groups/overview/ | management/resourcemanager (tags ≤50/resource; RG name-len/quota undocumented — confirm live) |
| ✅ | P1 | /userguide/management/quota/overview/ | management/quota (verify the VPC 3-vs-5!) |
| ✅ | P1 | /userguide/application/api_gateway/overview/ | application-service/apigateway (ADD-only deepen — PRIVATE endpoint forces JWT; method ANY + integration HTTP/Cloud Function/PrivateLink; PrivateLink desc ≤50 explains set-PUT 400) |
| ✅ | P1 | /userguide/application/queue_service/overview/ | application-service/queueservice (name 3-64/.fifo; size ≤256KB; retention ≤14d; kr-west1/east1) |
| ✅ | P1 | /userguide/analytics/data_flow/overview/ | data-analytics/data-flow — NiFi-on-SKE (ske-cluster+filestorage, ingress 1/cluster; create body UNPROVEN docs-vs-reality, IB-018) |
| ✅ | P1 | /userguide/analytics/data_ops/overview/ | data-analytics/data-ops — Airflow-on-SKE (executor K8s/Celery; create body UNPROVEN docs-vs-reality, IB-018) |
| ✅ | P1 | /userguide/analytics/event_streams/overview/ | data-analytics/eventstreams (smoke 400s) |
| ✅ | P1 | /userguide/analytics/quick_query/overview/ | data-analytics/quick-query (smoke 400 = bare GET /v1/quick-query missing required size+page; fixed; real create gated on DSC domain, IB-018) |
| ✅ | P1 | /userguide/analytics/search_engine/overview/ | data-analytics/searchengine (ES BYOL / OpenSearch; subnet; data nodes 1-10/50; add-instances model) |
| ✅ | P1 | /userguide/analytics/vertica/overview/ | data-analytics/vertica (masterless MPP; 24.2.0-2 only; no add-instances/patch; backup 7-35d) |
| ✅ | P1 | /userguide/ai_ml/ai_ml_ops_platform/overview/ | ai-ml/aimlops-platform (smoke 400s) |
| ✅ | P1 | /userguide/ai_ml/cloud_ml/overview/ | ai-ml/cloud-ml (smoke 404s) |
| ✅ | P1 | /userguide/devopstools/devops_service/overview/ | devops-tools/devopsservice (PF-05 fixed: create body needs members+tenant_code+tenant_name, NOT name/description; capture $.devops_service.id; dup-check by tenant; 1/account) |
| ✅ | P1 | /userguide/financial_management/planned_compute/overview/ | financial-management/billingplan (smoke 500 = GET /v1/planned-computes/server-types backend 500 — Product Bug, baselined since 2026-06-01; create body corrected to PlannedComputeCreateRequest; NOTE lifecycle JSON still carries old invented body — IB-019) |
| ✅ | P1 | /userguide/financial_management/cost_management/overview/ | financial-management/budget·costexplorer (budget: name≤20, amount+start_month req, currency UNPROVEN, **create 500 = PF-04 baselined product bug**) · (costexplorer: reads-only, 6mo window, smoke 3/3 200) |
| — | P2 | /userguide/compute/gpu_server/overview/ | compute (gpu types) |
| — | P2 | /userguide/compute/auto_scaling/overview/ | compute/auto-scaling |
| — | P2 | /userguide/compute/virtual_server_dr/overview/ | compute (DR) |
| — | P2 | /userguide/storage/object_storage/overview/ | storage/objectstorage |
| — | P2 | /userguide/management/notification_manager/overview/ | management/notification |
| — | P2 | /userguide/management/resource_explorer/overview/ | management (explorer) |
| — | P2 | /userguide/management/support_center/overview/ | management/support |
| — | P2 | /userguide/management/architecture_diagram/overview/ | management (diagram) |
| — | P2 | /userguide/financial_management/marketplace/overview/ | financial-management (marketplace) |
| — | P2 | /userguide/security/webfirewall/overview/ | security (WAF) |
| — | P2 | /userguide/security/ddosprotection/overview/ | security (DDoS) |
| — | P2 | /userguide/platform... (sts 등 catalog 잔여) | platform/sts |
| — | P3 | /userguide/networking/{sase,private_5g_cloud,cloud_wan,lan_campus_enterprise,lan_datacenter,cloud_last_mile,cloud_virtual_circuit}/overview/ | (console-only) |
| — | P3 | /userguide/security/{ess,fpms,ips,log_transmission,secured_firewall,secured_vpn,single_id}/overview/ | (console-only) |
| — | P3 | /userguide/hybrid_cloud/{edge_server,oracle_services}/overview/ | (console-only) |
| — | P3 | /userguide/developers_tools/mcp_server_enterprise/overview/ | (console-only) |
| — | P3 | /userguide/scp_common/overview/ | (platform common) |

> 표는 userguide TOC(2026-06-09 수집) 기준. 카테고리/서비스 추가 발견 시 행을
> 추가. P1 완료 기준: constraints/prereqs가 Layer-1 yaml에 들어가고 validate
> 통과. **beyond-overview**(how_to_guides 등 하위 페이지)는 옵션 변형(C4)
> 단계에서 서비스별로 내려받는다.
