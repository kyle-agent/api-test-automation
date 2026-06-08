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
