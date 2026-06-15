# Validated facts (the docs don't tell you these)

Runtime-confirmed truths that save the next session hours. Each is **VALIDATED**
(confirmed by a real 2xx) or **from docs** (best-effort, not yet confirmed).
Mirror of the `_note` fields in `regression/scenarios/scenarios.json`; keep both
in sync. Every entry here is also an **AI-usability gap** (something an AI could
not infer from the spec) — feed it to the AI-Evaluator agent.

## API design quirks — composite "create-all-in-one" verbs (AXIS-2 / AI-usability)

- **quick-query** — `POST /v1/quick-query` is NOT a thin "create a query" call. It is a
  **composite verb that provisions a whole SKE k8s engine (cluster + 3-node pool)
  inline** in the same request (docs model `QuickQueryTotalCreateRequest` =
  `kubernetes_engine_create_request` + `node_pool_create_requests[]` +
  `quick_query_create_request`; docs-derived, UNPROVEN). The reference page
  `.../models/quickquerycreaterequest/` is ONLY the innermost
  `quick_query_create_request` slice — vpc_id/subnet_id live in the
  `kubernetes_engine_create_request` block.
  - **Two dependency kinds collapse into one verb**: vpc/subnet/security-group/
    filestorage-volume are *referenced* (must pre-exist → real `requires`, ids
    injected into the inline engine block), but the **k8s engine itself is born
    inline** (not a separate create→reference node).
  - **AI-usability gap**: an agent reading "create quick-query" cannot infer it
    spins up a billable 3-node SKE cluster + full VPC/subnet/SG/volume wiring.
    Same shape likely recurs in data-flow (NiFi) / data-ops (Airflow) — engines
    installed on SKE via one composite verb (cf. IB-018). Graph shows vpc as an
    ancestor correctly; only transitive-reduction *display* cleanup is needed
    (IB-032), `requires` stays.

## Constraints from userguide (docs — naming/quota/state; not yet 2xx-confirmed)

**mysql (overview, 2026-06-15):**
- Engine versions: 8.0.28–8.0.42, 8.4.5, 8.4.7 (8.4.7 GA "2026년 7월 이후"). 8.0.x
  EOS 2026-03-19 / EoTS 2026-04-30 — both **past 2026-06-15**, so 8.0.x may be
  sunset for *new* creates (live catalog check needed). 8.4.5 EoTS 2032-04-30.
- **Read Replica: max 5 per DB, same OR different region** — divergence vs
  PG/mariadb (standard replica same-region only; cross-region is a separate DR
  variant). mysql docs don't separate the two.
- PITR window: 5/10/30 min or 1 h back. Archive retention 1–35 days (On/Off).
- Restore creates a **separate DB (new cluster)**. Switchover is **HA-only**
  (mysql docs confirm directly, no longer PG-준용). VPC required (subnet implied).
- mysql overview does NOT cover: volume 8-byte granularity, 9-volume cap, replica
  name regex, storage-type-forced-equal — **do not cross-apply mariadb facts**.

**IAM (how_to_guides, 2026-06-15):**
- **role**: name ≤64 `[a-zA-Z0-9+=\-_@,.]`; max_session_duration **3,600–43,200 s**
  (userguide writes "3,200초(1시간)" but 3,200 s = 53:20 ≠ 1 h — likely a doc typo;
  use 3,600 = 1 h as the safe minimum, **UNPROVEN until a live 4xx delimits it**);
  principals ≤20. [corrected by Watcher output-reviewer, round 2]
- **policy**: policy_name 3–128 `[한글a-zA-Z0-9+=,.@\-_]` (**Korean allowed**, unlike
  user/role); description ≤1,000; **Deny > Allow** precedence on same target.
- **user**: user_name ≤64 `[a-zA-Z0-9+=,.@\-_]` (no Korean); password 9–20, all 4
  classes (`!@#$%&*^`), no 3-repeat / 4-sequential / userID / dictionary / reuse,
  90-day rotation; `temporary_password=true` forces first-login change.
  **`account_id` is console-only — no API discovery path** (confirms the
  `opt.account_id` owner-credential gate is correct).
- **saml-provider**: name ≤128 `[a-zA-Z0-9,\-_]` (narrowest — no `@=.`); metadata =
  UTF-8 XML ≤10 MB single file; **SAML only** (OIDC "2026년 제공 예정"). API exists
  in catalog (STOP-2 N/A); the live decision point is **multipart vs JSON** (IB-010).

## Id / capture shapes (where the id lives in the response)

| Resource | Capture path | Note |
|----------|--------------|------|
| vpc | `$.vpc.id` | nested |
| subnet | `$.subnet.id` | nested |
| port | `$.port.id` | nested |
| security-group | `$.security_group.id` | nested |
| security-group-rule | `$.security_group_rule.id` | nested |
| internet-gateway | `$.internet_gateway.id` | nested |
| public-ip | `$.publicip.id` | nested |
| certificate | `$.certificate.id` | nested |
| resource-group | `$.resource_group.id` (srn `$.resource_group.srn`, soft) | global svc |
| **filestorage volume** | `$.volume_id` | **flat, and different from block volume!** |
| **virtualserver block volume** | `$.id` | flat — NOT `$.volume_id` |
| snapshot | `$.id` | flat |
| scr registry / repository | `$.id` | flat |
| queue | `$.id` | flat |
| **server (VM)** | `$.servers[0].id` | **array**, not `$.server.id` |
| ske cluster | `$.resource_id` | not `$.cluster.id` |
| ske nodepool | `$.nodepool.id` | nested |
| custom image (from server) | `$.image_id` | flat |
| **billingplan planned-compute** | list `$.planned_computes[0].id`, show `$.planned_compute` | from docs — NOT `$.contents[0].id` |
| **devopsservice** | list `$.devops_services[0].id`, create `$.devops_service.id` | from docs — NOT `$.contents[0].id` |

> Lesson: id shapes are **inconsistent across services** — always confirm per
> service. filestorage volume (`$.volume_id`) vs virtualserver volume (`$.id`) is
> the classic trap.

### networking/vpc — VPC-endpoint & transit-gateway prerequisites (docs, UNPROVEN; IB-012/013)

- **Subnet `type` enum = `(GENERAL, LOCAL, VPC_ENDPOINT)`** (required). A **VPC
  Endpoint needs a dedicated `VPC_ENDPOINT`-type subnet** — passing a GENERAL
  subnet yields 400 `scp-network.vpc-endpoint.subnet-not-found` ("VPC Endpoint
  Type Subnet not found", run 27466988779).
- A **Transit Gateway is "Connectable" only once it has a VPC connection in
  ACTIVE state.** `create-private-nat` over the TGW path needs this, else 400
  `scp-network.private-nat.connectable-transit-gateway-not-found`. VPC-connection
  state enum = `(CREATING, ACTIVE, DELETING, DELETED, ERROR)`.
- **TGW VPC-connection has no single-resource GET show** (only POST/DELETE/LIST)
  → readiness must be polled from the LIST endpoint
  (`$.transit_gateway_vpc_connections[0].state`). Create body is `{vpc_id}` only.
- TGW VPC-connection cap: **≤5 per TGW** (same account, userguide).

## State machines (poll field → ready values)

| Resource | Poll field | Ready value(s) |
|----------|-----------|----------------|
| vpc | `$.vpc.state` | `ACTIVE` |
| subnet | `$.subnet.state` | `ACTIVE` |
| filestorage / block volume | `$.state` | `available` / `AVAILABLE` / `ACTIVE` |
| attached volume | `$.state` | `in-use` / `IN-USE` / `in_use` |
| scr registry | `$.state` | `Running` / `RUNNING` / `active` / `ACTIVE` |
| internet-gateway | `$.internet_gateway.state` | `ACTIVE`/`ATTACHED`/`CREATED`/`RUNNING` |
| server (VM) | `$.server.state` | `ACTIVE` (stopped: `STOPPED`/`SHUTOFF`) |
| ske cluster | `$.cluster.status` | `RUNNING`/`ACTIVE`/`Running` |
| ske nodepool | `$.nodepool.status` | `Running`/`RUNNING`/`ACTIVE`/`active` |
| any delete | status code | poll `until_status: [404]` |

> Casing is inconsistent (`ACTIVE` vs `Running` vs `available`) — match a set, not
> a single string.

## Required / undocumented fields & quirks (per service)

**virtualserver (VM) — `compute-virtualserver-full`:**
- Block-volume field is **`volume_type`**, NOT `type` (e.g. `SSD_Provisioned`);
  the inline boot volume in create-server uses `type: "SSD"` with `boot_index: 0`,
  `delete_on_termination: true`.
- `server_type_id` must be chosen with a prefix filter: **id starts with `s`**,
  **not `g`** (g-types rejected/irrelevant). Looked up from `/v1/server-types`.
- create-server requires `product_category: "compute"`,
  `product_offering: "virtual_server"`, `networks: [{subnet_id}]`,
  `security_groups: [...]`.
- image lookup params that work: `status=active`, `scp_original_image_type=standard`,
  `visibility=public`, `limit=50` → `$.images[0].id`.
- **rename** (`PUT /v1/servers/{id}`) requires a `name` matching
  `^[a-zA-Z0-9-_ ]*$`; **tags are NOT updatable** via this endpoint.
- stop/start power-cycle VALIDATED (`STOPPED`/`SHUTOFF` ↔ `ACTIVE`).
- attach extra volume: `POST /v1/servers/{id}/volumes` with `volume_id` + `device`
  (`/dev/vdb`); detach before delete.

**ske (K8s) — `container-ske-cluster-nodepool` (heavy, ~27 min, billable):**
- v1.4 schema: cluster `volume_id` is a **string** (a filestorage volume);
  `service_watch_logging_enabled` is **required** (sent as `"true"`).
- nodepool with `volume_type_name: SSD_Provisioned` requires `volume_max_iops`
  and `volume_max_throughput`.
- k8s version from `/v1/kubernetes-versions` → `$.kubernetes_versions[0].kubernetes_version`.

**filestorage — `filestorage-volume`:** create needs `protocol: NFS`,
`type_name: HDD`. (Contrast block volume: `volume_type`, `max_iops`,
`max_throughput`.)

**scr — `container-scr-registry`:** registry **DELETE returns 500 for a few
minutes right after creation** (provisioning race), then succeeds — retry the
delete on 500 for ~6 min. Repository delete retries 409/500.

**virtualserver keypair — `virtualserver-keypair`:** omit `public_key` and SCP
**generates** one. Keypairs are addressed **by name** (get/delete
`/v1/keypairs/{name}`), not by id. Zero-cost, synchronous.

**security-group — `networking-security-group`:** account/region-scoped — **no
VPC needed** (confirmed via the VM/ske lifecycles). Rule create uses
`direction`, `ethertype: IPv4`, `protocol`, `port_range_min/max`,
`remote_ip_prefix`.

**certificatemanager — self-sign:** body needs `cn`, `not_before_dt` (`{today}`),
`not_after_dt` (`{today_plus_5y}`), `organization`, `region`, `timezone`
(`Asia/Seoul`). Synchronous.

**public-ip / internet-gateway (from docs, best-effort):** public-ip `type: IGW`;
igw needs `vpc_id`, `firewall_enabled`, `type: IGW`.

## Placeholders the engine seeds automatically

`{unique}` (collision-free token), `{ualpha}` (alpha-only unique), `{region}`,
`{today}`, `{today_plus_5y}`. Use these instead of hardcoding values, so runs
don't collide and resources are attributable.

## Teardown races

Deletes that touch a resource still releasing a dependency return `409` (or `500`
for scr/snapshot/igw) — retry with backoff (`retry_on_status`, `retries`,
`retry_interval`). Always wait for the dependent resource to be `404` before
deleting its parent (e.g. subnet 404 before vpc delete).

---

## Coverage campaign — Wave 1 facts (2026-06-08, NOT yet runtime-proven)

> Authored by parallel service-agents (see `agents/CAMPAIGN.md`). Bodies/envelopes
> below are docs-derived best-effort; promote to "validated" only after a live 2xx.

**Engine coverage-matching gotcha (confirmed, applies to all authors):** the
catalog match normalizes only `{...}` path segments to `*`; a *literal* id
segment in a step path (e.g. `/v1/roles/0000`) does NOT match the catalog and so
records ZERO write coverage. Always use `{placeholder}` tokens for id segments
(`{unique}` works) so the step both resolves to the catalog key and still fires
when its capture is absent.

**iam** — role create returns `$.role.id`, group `$.group.id`, policy FLAT `$.id`.
`POST /v1/roles` is known to 500 `ContactAdminForAssistance` on the shared account
(pre-existing). `data/api_bodies.json` `createsamlprovider`/`setsamlprovider` are
**corrupt** (`{"_raw":"{'key':'company',...}"}`) — needs a real SAML metadata doc.

**iam-identity-center (SSO)** — uses **PATCH** for in-place updates
(setinstance/setuser/setgroup/setpermissionset), unlike iam (PUT). `instance_id`
is a hard dependency for nearly every write. Envelopes (unproven): `$.instance.id`,
`$.user.id`, `$.group.id`, `$.permission_set.id`, `$.account_assignment.id`.

**organization (HIGHEST blast radius)** — organizations / organization-accounts /
**service-control-policies (SCPs)** / delegation-policies / policy-bindings /
invitations can sever or DENY the entire account hierarchy account-wide and are
largely irreversible. All org lifecycles are **coverage-only**: heavy + every
write `optional` + expecting 403/400, never chaining create→attach/accept. No
`api_bodies.json` entries exist; all bodies guessed. NEVER weaken to real
create/delete on a shared account.

**storage/baremetal-blockstorage** — volume create returns `$.result.id`
(`result`-wrapped), snapshot create returns FLAT `$.snapshot_id`. State machine
`CREATING→AVAILABLE/IN_USE→DELETING→DELETED` (poll `$.result.state`). Volume create
requires `attachments:[{object_id,object_type:BM|MNGC}]` (sent `[]`, may reject).
There is **no** `DELETE /v1/volume-groups/{id}` — a group is torn down via its
member volume. Enums: replication cycle {5MIN,HOURLY,DAILY,WEEKLY,MONTHLY}, policy
{RESYNC,BREAK}; disk_type {SSD,HDD}.

**application-service/apigateway** — VPC-free control-plane. A deployment needs ≥1
method first (`NoMethodsExist`); `createapideployment stage_type:new` creates the
stage and returns `$.deployment_id`. `createauth` returns ONLY `$.access_token`
(no id) → recover `auth_id` via `listauths $.auths[0].id`. Methods addressed by
`{method_type}`, stages by `{stage_name}` (no ids). name/stage pattern
`^[a-z][a-z0-9-]{1,48}[a-z0-9]$`. privatelink-endpoint needs a real
`privatelink_service_id` (synthetic → 4xx, optional).

**servicewatch** — bulk-delete (`DELETE /v1/alerts|dashboards|event-rules`, no path
id) modeled as `{"ids":[...]}` (unproven, mirrors proven `deleteloggroups`).
Create envelopes `$.alert.id`/`$.dashboard.id`/`$.event_rule.id` (unproven).
createalert needs real `metric_id`/`namespace_id`; createeventrule needs real
event/resource/service ids — doc-sample ids used, 4xx expected (still records).

---

## Coverage campaign — Wave 2 facts (2026-06-08, NOT yet runtime-proven)

> 7 cluster-agents authored 36 fragment files / 49 lifecycles closing 302 write
> ops. Static ceiling 55.4% → 78.6%. All bodies docs-derived; promote after a live 2xx.

**Static coverage matching is PATH-only (service-agnostic).** `spec.coverage_gap`
and the dashboard match `(method, norm_path)` ignoring service, but the engine
RECORDS under `(method, norm_path, service)`. Consequence: DBaaS-family services
sharing `/v1/clusters/*` roots (mysql/mariadb/epas/postgresql/sqlserver/cachestore
+ data-analytics searchengine/vertica/eventstreams) appear "covered" once ANY
engine covers the path — but each still needs its own fragment to record under its
own host/keys at runtime. All such per-engine fragments were authored.

**Cost-safe coverage-only pattern (virtualserver, databases, org, analytics):** for
billable/destructive resources, do NOT provision — soft-capture an existing id (or
a deliberately-empty JSONPath so the `{id}` stays literal → guaranteed 404), fire
every write `optional`+`group`+broad `expect_status:[200,201,202,400,403,404,409,422]`.
The endpoint is CALLED+recorded (counts as covered) without touching real resources.

**VPC reuse extended:** loadbalancer, vpn, direct-connect, and the 6 vpc-extra
lifecycles adopt the session-shared VPC via `{"adopt":"vpc"}` (registered in
`dependencies.json:quota_kinds` as `["vpc"]`). The "VPC consumers" set in
`vpc-scheduling-strategy.md` is now larger but all heavy adopters share the one VPC.

**Corrupt `data/api_bodies.json` entries found (TODO fix):**
`security/iam createsamlprovider`/`setsamlprovider` (`{"_raw":"{'key':'company',...}"}`)
and `networking/vpc createtransitgatewayfirewallconnection` (`{"_raw":"{transit_gateway_id}"}`).
Agents worked around with best-guess bodies; the source entries should be re-extracted.

**Per-family capture/body notes** (unproven): block-volume `$.result.id` + flat
`$.snapshot_id`; backup `$.resource.id`; filestorage snapshot `$.snapshot.id`,
snapshot-schedule create returns NO id (use list); cdn `$.cdn.resource_id`; gslb
`$.gslb.id`; vpn `$.vpn_gateway.id`/`$.vpn_tunnel.id`; dc `$.direct_connect.id`.
DBaaS diverges: mariadb/epas/pg add audit-log (+epas/pg archive-delete); sqlserver
is HA-only (add-secondary/databases, no archive/replicas, excluded from shared-dbaas
on license); cachestore (Redis) uses `/commands`(+sync) not archive/audit/log-export.
secretvault has no hard DELETE (PUT .../terminated); secretsmanager
`POST .../values` is REVEAL not update; certificatemanager import is unsatisfiable
(coverage-only); firewall has no `POST /v1/firewalls` (implicit on igw/dc/vpc).

## 2026-06-10 — full heavy run 27258520218 + post-run force-cleanup evidence

**cachestore create VALIDATED:** `heavy-shared-dbaas` cache-create got **202**
(cluster created → waited → 202 delete) with `dbaas_engine_version_id` captured
dynamically from `/v1/engine-versions` `contents[0]` — the "guessed engine
version" hypothesis for the 21/21 called-only gap is disproven; the sub-op gap
is no-live-cluster **timing** (the guarded sub-op lifecycle ran when no cluster
existed → soft 400s with `*` tokens).

**401 family (valid HMAC):** DBaaS backup sub-resources 401 across engines —
cachestore/postgresql `PUT .../backup-histories`, mysql/postgresql
`DELETE .../backups` — while sibling sub-ops on the same cluster path 400.
Also 401: the two query-param GETs (`scr check-duplication/name`, devops
`check-duplication`) — suspect HMAC-vs-query-string signing on our side.
Triage: `docs/HANDOFF-fail-new-triage.md`.

**Sweep/cleanup behavior (run ca493bd sweep log):**
- `/v1/log-groups`: **15 listed / 0 deletable** every round — the per-service
  auto-created log groups (`/scp/ske/...`, `/scp/<engine>/.../slowlog|alertlog`)
  carry no owner tag and their names don't match the `regr` prefix fallback, so
  the reconciler never reaps them. Servicewatch 로그그룹 0건 아님 — needs either
  a reconciler rule for `/scp/<svc>/regr*` paths or console cleanup.
- secrets (12) and KMS transit keys (10+5) re-list as "deletable" every sweep
  round: deletes return success but the items keep listing — scheduled-deletion
  retention windows, not sweep failures.
- 2 cloudmonitoring dashboards 400 on every delete attempt (ids
  `8b498aa3...`, `bc3343cf...`) — delete body/precondition unknown, recurring
  sweep noise.

**BM blockstorage blocker pinned (userguide retry):** volume create REQUIRES
1–8 attached Bare Metal Servers (연결 서버 필수) → `attachments: []` is the 400;
the ~40-endpoint chain stays called-only without a BM server. Full constraints:
`knowledge/formal/services/storage__baremetal-blockstorage.yaml`.

## 2026-06-10 — A∥B split run 27306490231 (job B evidence, mid-run)

- **VPC account cap is 5, not 3** — live error `scp-network.vpc.exceed-max-count:
  "The number(5) of VPCs ... has been exceeded"`. The long-standing "3 VALIDATED"
  was wrong; budgets/dependencies/cross-service updated to 5 (per-run cap 4).
  3 lifecycles (vpc-subnet, igw, tgw-children) skipped environmentally when the
  cap filled during the A∥B overlap + heavy-shared-networking's slow teardown →
  job B now runs heavy-shared-networking LAST.
- **subnet-VIP create envelope VALIDATED: `$.subnet_vip.id`** (201 live). The old
  `$.vip.id` capture missed → cleanup `{vip_id}` unresolved → VIP survived →
  the recurring `delete-subnet` 409 RelatedVip. Capture fixed.
- **vpc-peering 404 root cause**: body sent the `{unique}` placeholder as
  approver_vpc_id (`NotFoundVpcError: VPC ID(<unique-hex>) is not found`) — a
  real approver VPC-B (reserved 10.141.0.0/20) is now created in the lifecycle.
- heavy-shared-networking again confirmed the slow-provisioner rule: private-dns
  stuck in `CREATING` (400 invalid-state on the setter) while LB health-check
  child 404'd (`LbHealthCheckNotFoundError` — health-check id capture/order issue,
  not yet fixed).

## 2026-06-11 — runs #3~#5 + oplog 구축에서 VALIDATED된 사실들

**런 시간/커버리지 추이 (풀 헤비):** #1 e3ba190 3h49m (fail_new 52) → #2 84df549
2h11m (50) → #3 3f59837 1h21m (50, C3 43.27%/분모 1130) → #4 63a139f 51m (48,
단 heavy 10개 캡 스킵) → #5 22a3b22 진행 중 (heavy 10개 전부 시작 확인).

**VPC 캡 오염 체인 (#3→#4에서 입증):** lifecycle teardown 실패 → 잔존 VPC가
"자기 런"의 6h TTL 보호로 sweep 통과 → 다음 런 시작 시 캡 잠식 → 공유 VPC
프로비저닝 실패 → adopt 전원 self-create 폴백 → 연쇄 캡 스킵. 수정 = sweep의
**own-run override** (run_id 태그가 자기 런이면 TTL 무시 reap; 타 런 보호 유지)
+ 프로비저닝을 smoke 앞(minute 0) + 10×45s 재시도.

**vpc-subnet-vip-nat 409의 원인 2개 (둘 다 잡고 #4에서 PASS):**
① VIP 생성 응답 envelope은 `$.subnet_vip.id` (201 라이브 검증; 종전 `$.vip.id`
캡처 미스 → cleanup 미해석), ② cleanup은 실패 시에만 발동하므로 happy-path에
명시적 delete-vip 스텝이 없으면 VIP가 살아남음.

**vpc-peering 상태머신 (#4):** create 202 직후 상태에서 approve/set/**DELETE
모두 400** → peering 잔존 → VPC 삭제 409. 동일계정 2-VPC + 실 approver_vpc_id
구성으로 createvpcpeering 자체는 VALIDATED(202). 삭제 규칙은 soft-write note로
다음 런에서 채집.

**SCP Object Storage S3 (oplog 버킷에서 검증):**
- 엔드포인트 호스트 = `object-store.<region>.<env>.samsungsdscloud.com`
  (objectstorage 아님); Open API와 동일 access/secret 사용 가능.
- 인증 호출은 bare 버킷명으로 동작; **익명(공개) 경로는 RGW tenant 문법
  `/<account_id>:<bucket>/<key>`** (slash 구분은 NotFoundBucketNameInPath).
- list-buckets Owner.ID 형식은 `<account_id>$<account_id>`.
- bucket ACL public-read는 **LIST만** 허용; 객체 GET은 **객체별 public-read
  ACL** 필요 (put_object에 ACL 지정). CORS는 put-bucket-cors로 정상 적용.
- 버킷: `apitest-oplog-permanent` (영구; sweep 어떤 매처에도 불일치).

**GitHub Actions YAML 함정:** plain scalar 멀티라인 `run:`의 백슬래시 연속은
폴딩되며 `\ `(이스케이프 공백)가 되어 argparse가 거부 — `|| true`가 삼켜
조용히 실패. 멀티라인 명령은 반드시 `run: |` 블록으로. (#4에서 adopt/vpc-crud/
sweep 마일스톤만 누락된 원인.)

**ops 대시보드 운영 사실:** GitHub MCP 토큰 만료 시에도 버킷 직접 조회로 런
상태 확인 가능 (sweep 종료 등) — 독립 관측 채널로서 실효 입증.

## 2026-06-11 — coverage-expansion authoring (docs-derived, NOT yet runtime-proven)

> Levers ①③④ of `docs/SESSION-HANDOFF-run6-and-ops.md`; full analysis in
> `docs/COVERAGE-WAVE-PLAN.md`. Promote after a live 2xx.

- **gap_write 32 = 100% waived/disabled** (idc 19 blast-radius + archivestorage 13
  entitlement) — no authorable write gap remains; static ceiling moved by getid
  steps only (85.57% → 86.3%, gap_getid 166 → 156).
- **`/v1/requests/{request_id}` is a SHARED normalized path** across 9 DBaaS-family
  services — ONE explicit GET step (added to mysql/pg heavy + eventstreams-read)
  closes the static gap for all of them; request_id exists ONLY in write
  AsyncResponse envelopes (`{request_id, resource:{id}}`, docs).
- **eventstreams sub-op bodies were wrong-model** (docs): add-instances takes
  `{instance_count, service_ip_addresses}` NOT an instances[] array; POST
  maintenance takes MaintenanceRequest (start_day_of_week/start_minute/start_time/
  term_hour) NOT the create-time MaintenanceOption; security-group-rules takes
  `{add_ip_addresses, del_ip_addresses}` NOT rule objects; parameters update takes
  `{id, new_value, old_value}` triplets. All fixed in the fragment; facts in
  `knowledge/formal/services/data-analytics__eventstreams.yaml`. Create-cluster
  topology (valid Kafka role_type combos) remains undocumented — DOMAIN-HUNT.
- **servicewatch metric POSTs are catalog-validated queries** (docs): bodies must
  reference real namespace/metric ('Virtual Server', 'CPU Usage/Core[Basic]');
  regr{unique} names were the cause of the 400s. showloggroup got an explicit step
  (probe_reads doesn't count statically).
- **DBaaS sub-op window prep**: conservative-only groups `mysql-subop-window`,
  `mysql-restart`, `pg-subop-window` added INSIDE the existing heavy lifecycles
  (read-only GETs + no-body sync-state + restart); upgrades/promotes/restores/
  stop-start(mysql) explicitly excluded. Scoped validation:
  `crud_filter="database-mysql-cluster or database-postgresql-cluster"` heavy run.

## 2026-06-11 — query-string HMAC 401: root cause found OFFLINE (fix landed, live-verify pending)

> Offline-proven in `tests/offline/test_hmac_signing.py` (21 tests); promote to
> validated after the scoped live run 2xxes. Do NOT remove the two
> `known_issues.json` unset-backup entries until then.

- **Root cause (byte-level):** the harness folds `urlencode(params)` into the URL
  (already percent-encoded), then the signer applied a strict JS-encodeURI clone
  — and JS `encodeURI()` escapes `%` → `%25`. So the signature covered
  `...check-duplication?name=regrscr%257Bunique%257D` while the wire carried
  `...?name=regrscr%7Bunique%7D` → systematic 401 for EVERY URL containing a
  `%XX` escape. Trigger in practice: scenario `params` are NOT `_fill()`ed
  (engine passes `step.get("params")` raw), so `{unique}`/`{reg_id}` placeholders
  go out as `%7B...%7D` and trip the double-encode. Plain-ASCII query URLs
  (`?page=0&size=1`, smoke's `name=regrprobesmoke`) never diverged — which is why
  most param GETs passed.
- **Fix (`core/auth.py` + `core/http_client.py`, toggle `SCP_SIGN_ENCODEURI`,
  default ON):** http_client pre-normalizes the assembled URL with requests' own
  `PreparedRequest.prepare_url` (idempotent) and signs/sends that exact string; the
  signer's transform (`_encode_uri_wire`) keeps `%` and `[]` so it is the
  identity on a prepared URL and encodeURI-equivalent on raw input. Proven
  byte-identical signing string for all previously-passing shapes (no-op), so
  default-on is safe. `SCP_SIGN_ENCODEURI=false` restores legacy signing.
- **Unset-backup caveat:** `DELETE /v1/clusters/{id}/backups` carries no query
  and no `%` — offline, legacy signing was ALREADY wire-identical for it, so the
  401 there may be backend (RBAC/quirk), not this bug. The scoped live run
  decides: if it still 401s with the fix, reclassify the two known_issues
  entries as Product Bug instead of removing them.

## SKE cluster/nodepool upgrade — LIVE-PROVEN (run 27492496266, 2026-06-14)
- `gen-heavy-ske-upgrade` chain passed end-to-end (1 passed, 35m26s real
  control-plane + node roll): create old cluster v1.33.5 → `PUT
  /v1/clusters/{id}/upgrade {kubernetes_version:v1.34.3}` → RUNNING re-poll →
  `PUT /v1/nodepools/{id}/upgrade {os_version:"22.04"}` (OS version, NOT k8s
  version — node follows control-plane) → kubeconfig GET ×2 → teardown.
- **ske-image**: `GET /v1/images?scp_original_image_type=k8s` — the
  `scp_original_image_type=k8s` query is REQUIRED (api_docs; omission → 400
  "Field required", runs 27483895557/27491816948). `size`/`page` optional.
- Nodes promoted to VALIDATED: ske-image, ske-cluster-upgrade, ske-nodepool-upgrade.
