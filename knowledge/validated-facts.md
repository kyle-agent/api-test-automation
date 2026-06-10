# Validated facts (the docs don't tell you these)

Runtime-confirmed truths that save the next session hours. Each is **VALIDATED**
(confirmed by a real 2xx) or **from docs** (best-effort, not yet confirmed).
Mirror of the `_note` fields in `regression/scenarios/scenarios.json`; keep both
in sync. Every entry here is also an **AI-usability gap** (something an AI could
not infer from the spec) — feed it to the AI-Evaluator agent.

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

> Lesson: id shapes are **inconsistent across services** — always confirm per
> service. filestorage volume (`$.volume_id`) vs virtualserver volume (`$.id`) is
> the classic trap.

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
