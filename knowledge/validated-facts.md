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
| product (read) | `$.products[0].product_id` | global svc, list envelope `$.products[]`; show id-bound on `{product_id}` |
| product-category (read) | `$.product_categories[0].category_id` | global svc; show id-bound on `{category_id}` |
| billingplan planned-compute (read) | `$.planned_computes[0].id` | global svc; list envelope `$.planned_computes[]`; show id-bound `/v1/planned-computes/{planned_compute_id}` returns `$.planned_compute.id` (from docs) |
| costexplorer bill / usage (read) | `$.bills[0].id` / `$.usages[0].id` | global svc; ids EXIST in list envelopes but there is **no** `/v1/bills/{id}` or `/v1/usages/{id}` id-bound GET — nothing to bind, so no probe (from docs) |
| pricing report (read) | (none) | global svc; `$.billing_item_ids` is a scalar string, `$.offerings` is a list of strings, `$.prices` is a scalar inside a page envelope — **no nested object id** to capture; all 3 GETs are direct, no id-bound GET (from docs) |
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

**financial-management reads (`pricing-reads` / `costexplorer-reads` /
`billingplan-reads`, from docs):** all three are global/account-scoped (no region).
Only **billingplan** has an id-bound GET (`showplannedcompute`,
`/v1/planned-computes/{planned_compute_id}`), so only it carries a `probe_reads`
step (key = catalog path-param name `planned_compute_id`, captured from
`$.planned_computes[0].id`). pricing has 3 direct report GETs with no nested id and
no id-bound GET; costexplorer has 3 direct GETs whose list envelopes do carry ids
(`$.bills[].id`, `$.usages[].id`) but with no matching id-bound GET to bind them to
— so pricing/costexplorer have **no probe_reads** and add no coverage over the smoke
floor (added anyway as explicit, attributable read coverage and as the documented
`*-reads` pattern). All env-dependent: record SOFT if the account has no
planned-computes / bills / usages / payments.

## Placeholders the engine seeds automatically

`{unique}` (collision-free token), `{ualpha}` (alpha-only unique), `{region}`,
`{today}`, `{today_plus_5y}`. Use these instead of hardcoding values, so runs
don't collide and resources are attributable.

## Teardown races

Deletes that touch a resource still releasing a dependency return `409` (or `500`
for scr/snapshot/igw) — retry with backoff (`retry_on_status`, `retries`,
`retry_interval`). Always wait for the dependent resource to be `404` before
deleting its parent (e.g. subnet 404 before vpc delete).
