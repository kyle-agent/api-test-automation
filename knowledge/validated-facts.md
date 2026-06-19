# Validated facts (the docs don't tell you these)

Runtime-confirmed truths that save the next session hours. Each is **VALIDATED**
(confirmed by a real 2xx) or **from docs** (best-effort, not yet confirmed).
Mirror of the `_note` fields in `regression/scenarios/scenarios.json`; keep both
in sync. Every entry here is also an **AI-usability gap** (something an AI could
not infer from the spec) — feed it to the AI-Evaluator agent.

> **Confidence metadata convention.** Each fact entry carries a lightweight meta line:
> `> conf: 0.3–0.9 · seen: YYYY-MM-DD · obs: N` where
> - **conf** = confidence: `0.3` tentative (docs-derived, UNPROVEN) → `0.5` moderate
>   (partial/offline evidence) → `0.7+` verified (live 2xx / runtime-proven).
> - **seen** = the last date this fact was confirmed still relevant (YYYY-MM-DD).
> - **obs** = number of times the fact was observed/confirmed.
>
> This lets **session-start surface only high-confidence, recently-seen facts**
> (e.g. `conf ≥ 0.7` and `seen` within N days) instead of re-reading the whole
> store every session. Bump `conf`/`obs` and refresh `seen` when a fact re-confirms;
> lower `conf` (or prune) when a fact drifts or is contradicted by current state.

## API design quirks — composite "create-all-in-one" verbs (AXIS-2 / AI-usability)

> conf: 0.3 · seen: 2026-06-17 · obs: 1

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
> conf: 0.5 · seen: 2026-06-15 · obs: 1
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
> conf: 0.5 · seen: 2026-06-15 · obs: 1
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

**CDN (global_cdn overview, 2026-06-15, docs/UNPROVEN):**
> conf: 0.5 · seen: 2026-06-15 · obs: 1
- Global CDN is offered **only in kr-west1/kr-east1** — explicitly NOT in
  kr-south1/2/3 → **region-gate any CDN lifecycle**.
- **20 domains/account** cap. Origin protocols HTTP/HTTPS/HTTP2. Akamai-backed →
  async provisioning. `origin_hostname_type=DOMAIN` allows empty protocol/port;
  IP mode requires both.

**Userguide infra note (2026-06-15):**
> conf: 0.5 · seen: 2026-06-15 · obs: 1
docs.e.samsungsdscloud.com had **intermittent 503s** —
`/userguide/security/` first, then briefly full-host, but `/overview/` pages
(networking, financial) were reachable while most `how_to_guides/` sub-pages 503'd.
→ docs-mapper is **unreliable this window**; suspend `provenance: docs` deep enrichment
until stable; prior-curated constraints stay intact/UNPROVEN. Path base uses
**underscore** (`financial_management`, not hyphen — hyphen 404s).

**networking (loadbalancer/gslb/vpn, userguide 2026-06-15, docs/UNPROVEN):**
> conf: 0.5 · seen: 2026-06-15 · obs: 1
- LB **Public NAT IP requires an IGW** on the VPC (docs-confirms PF-13 → justifies
  `lb-static-nat requires internet-gateway`). LB quotas/region: 50 LB · 1000
  listeners · 1000 server-groups · 3 service-subnets/VPC.
- **GSLB**: monitoring needs **Firewall + Security Group allow-rules on the target**
  (HC fails otherwise); `env_usage=PUBLIC` is the only documented value; region
  kr-west1/east1 only; FQDN label 4-40 lowercase+digits.
- **VPN gateway is NOT deletable while tunnels are attached** (teardown order —
  explains the 409-retry); 3 gw/account, 5 tunnels/gw; IKE phase1/2 specifics are
  NOT in the userguide (PSK/DH/lifetimes stay UNPROVEN docs-examples).

**database/analytics (cachestore/searchengine/vertica, userguide 2026-06-15, docs/UNPROVEN):**
> conf: 0.5 · seen: 2026-06-15 · obs: 1
- **cachestore**: both ports 1200-65535; password 8-30 excluding `$ " '`; block
  storage 16-5120 GB (×8). **Docs prescribe HA replica 1-2, but `replica_count:0`
  is LIVE-PROVEN (202, run 27258520218)** — keep the live value; conflict noted
  (masked-defect avoidance).
- **searchengine**: dedicated-master = **exactly 3**; data nodes min 2 (separate
  master) / 1 (combined); ports **9300 & 5301 reserved** (unusable as db port).
- **vertica**: all field rules userguide-re-confirmed (no drift vs 2026-06-14).

**networking quotas (firewall/dns/direct-connect, userguide 2026-06-15, docs/UNPROVEN):**
> conf: 0.5 · seen: 2026-06-15 · obs: 1
- **Private DNS = 1 per account** (account-wide across regions; `POST /private-dns/activate`
  = multi-region activation of the same name) → a 2nd create hits quota 4xx. Verify
  `quota: private-dns == 1` in cross-service.yaml.
- **Direct Connect = 5 per service-zone, 1:1 per VPC**; prerequisite includes a
  **Security Group** (candidate missing edge in `dc-prereq`).
- **Hosted Zone = 20/account, 100 records/zone**; record types A/AAAA/CNAME/TXT/MX/SPF/NS/SOA.
- **Firewall rule quota by size**: EXSMALL=5 (implicit-create default)/SMALL=100/MEDIUM=200/
  LARGE=500/EXLARGE=1000; default policy "Any Deny" (ALLOW rule required to pass traffic).

**compute/container (baremetal/scf/scr, userguide 2026-06-15, docs/UNPROVEN):**
> conf: 0.5 · seen: 2026-06-15 · obs: 1
- **baremetal**: server_type `s3v{vCore}m{memGB}_metal` (vCore = physical×2 HT);
  **Security Group NOT supported** (Firewall + Transit Gateway only); name lowercase
  3-15, multi-server → `prefix-###`; single-delete leaves BM Block Storage orphan.
- **SCF**: regions **kr-west1/east1 only**; runtimes Go/Java17/Node18-24/PHP/Python3.9-3.14;
  triggers only **Cronjob + API Gateway**; Deploying state blocks trigger/config modify.
- **SCR**: registry name lowercase-start 3-25; **images/tags born via Docker push**
  (Registry V2, no REST create) → `scr-image`/`scr-tag` correctly `no_api`.

**management (cloudmonitoring/loggingaudit/resourcemanager, userguide 2026-06-15, docs/UNPROVEN):**
> conf: 0.5 · seen: 2026-06-15 · obs: 1
- **Cloud Monitoring is EOL after the 2026-09 release → migrate to ServiceWatch**
  (deprioritize further cloudmonitoring investment).
- CM event grades = {Fatal, Warning, Information}; comparison = 7 (GE/GT/LE/LT/EQ/NE + Range).
- **loggingaudit**: trail_name 5-26 alnum+hyphen; **bucket region immutable** post-create.
- **resourcemanager**: tags ≤50 per resource.

## Id / capture shapes (where the id lives in the response)

> conf: 0.7 · seen: 2026-06-17 · obs: 1

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

> conf: 0.3 · seen: 2026-06-17 · obs: 1

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

> conf: 0.7 · seen: 2026-06-17 · obs: 1

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
> conf: 0.7 · seen: 2026-06-17 · obs: 1
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
> conf: 0.7 · seen: 2026-06-17 · obs: 1
- v1.4 schema: cluster `volume_id` is a **string** (a filestorage volume);
  `service_watch_logging_enabled` is **required** (sent as `"true"`).
- nodepool with `volume_type_name: SSD_Provisioned` requires `volume_max_iops`
  and `volume_max_throughput`.
- k8s version from `/v1/kubernetes-versions` → `$.kubernetes_versions[0].kubernetes_version`.

**filestorage — `filestorage-volume`:**
> conf: 0.7 · seen: 2026-06-17 · obs: 1
create needs `protocol: NFS`,
`type_name: HDD`. (Contrast block volume: `volume_type`, `max_iops`,
`max_throughput`.)

**scr — `container-scr-registry`:**
> conf: 0.7 · seen: 2026-06-17 · obs: 1
registry **DELETE returns 500 for a few
minutes right after creation** (provisioning race), then succeeds — retry the
delete on 500 for ~6 min. Repository delete retries 409/500.

**virtualserver keypair — `virtualserver-keypair`:**
> conf: 0.7 · seen: 2026-06-17 · obs: 1
omit `public_key` and SCP
**generates** one. Keypairs are addressed **by name** (get/delete
`/v1/keypairs/{name}`), not by id. Zero-cost, synchronous.

**security-group — `networking-security-group`:**
> conf: 0.7 · seen: 2026-06-17 · obs: 1
account/region-scoped — **no
VPC needed** (confirmed via the VM/ske lifecycles). Rule create uses
`direction`, `ethertype: IPv4`, `protocol`, `port_range_min/max`,
`remote_ip_prefix`.

**certificatemanager — self-sign:**
> conf: 0.7 · seen: 2026-06-17 · obs: 1
body needs `cn`, `not_before_dt` (`{today}`),
`not_after_dt` (`{today_plus_5y}`), `organization`, `region`, `timezone`
(`Asia/Seoul`). Synchronous.

**public-ip / internet-gateway (from docs, best-effort):**
> conf: 0.5 · seen: 2026-06-17 · obs: 1
public-ip `type: IGW`;
igw needs `vpc_id`, `firewall_enabled`, `type: IGW`.

## Placeholders the engine seeds automatically

> conf: 0.7 · seen: 2026-06-17 · obs: 1

`{unique}` (collision-free token), `{ualpha}` (alpha-only unique), `{region}`,
`{today}` (`YYYYMMDD`), `{today_plus_5y}`, `{iso_today}` (`YYYY-MM-DD`),
`{iso_29d_ago}` (`YYYY-MM-DD`, today−29d — a rolling in-bounds window for
bounded report/metric ranges, see apigateway listreports below). Use these
instead of hardcoding values, so runs don't collide and resources are
attributable.

## Teardown races

> conf: 0.7 · seen: 2026-06-17 · obs: 1

Deletes that touch a resource still releasing a dependency return `409` (or `500`
for scr/snapshot/igw) — retry with backoff (`retry_on_status`, `retries`,
`retry_interval`). Always wait for the dependent resource to be `404` before
deleting its parent (e.g. subnet 404 before vpc delete).

---

## Coverage campaign — Wave 1 facts (2026-06-08, NOT yet runtime-proven)

> Authored by parallel service-agents (see `agents/CAMPAIGN.md`). Bodies/envelopes
> below are docs-derived best-effort; promote to "validated" only after a live 2xx.

**Engine coverage-matching gotcha (confirmed, applies to all authors):**
> conf: 0.7 · seen: 2026-06-08 · obs: 1
the
catalog match normalizes only `{...}` path segments to `*`; a *literal* id
segment in a step path (e.g. `/v1/roles/0000`) does NOT match the catalog and so
records ZERO write coverage. Always use `{placeholder}` tokens for id segments
(`{unique}` works) so the step both resolves to the catalog key and still fires
when its capture is absent.

**iam** —
> conf: 0.3 · seen: 2026-06-08 · obs: 1
role create returns `$.role.id`, group `$.group.id`, policy FLAT `$.id`.
`POST /v1/roles` is known to 500 `ContactAdminForAssistance` on the shared account
(pre-existing). `data/api_bodies.json` `createsamlprovider`/`setsamlprovider` are
**corrupt** (`{"_raw":"{'key':'company',...}"}`) — needs a real SAML metadata doc.

**iam-identity-center (SSO)** —
> conf: 0.3 · seen: 2026-06-08 · obs: 1
uses **PATCH** for in-place updates
(setinstance/setuser/setgroup/setpermissionset), unlike iam (PUT). `instance_id`
is a hard dependency for nearly every write. Envelopes (unproven): `$.instance.id`,
`$.user.id`, `$.group.id`, `$.permission_set.id`, `$.account_assignment.id`.

**organization (HIGHEST blast radius)** —
> conf: 0.3 · seen: 2026-06-08 · obs: 1
organizations / organization-accounts /
**service-control-policies (SCPs)** / delegation-policies / policy-bindings /
invitations can sever or DENY the entire account hierarchy account-wide and are
largely irreversible. All org lifecycles are **coverage-only**: heavy + every
write `optional` + expecting 403/400, never chaining create→attach/accept. No
`api_bodies.json` entries exist; all bodies guessed. NEVER weaken to real
create/delete on a shared account.

**storage/baremetal-blockstorage** —
> conf: 0.3 · seen: 2026-06-08 · obs: 1
volume create returns `$.result.id`
(`result`-wrapped), snapshot create returns FLAT `$.snapshot_id`. State machine
`CREATING→AVAILABLE/IN_USE→DELETING→DELETED` (poll `$.result.state`). Volume create
requires `attachments:[{object_id,object_type:BM|MNGC}]` (sent `[]`, may reject).
There is **no** `DELETE /v1/volume-groups/{id}` — a group is torn down via its
member volume. Enums: replication cycle {5MIN,HOURLY,DAILY,WEEKLY,MONTHLY}, policy
{RESYNC,BREAK}; disk_type {SSD,HDD}.

**application-service/apigateway** —
> conf: 0.3 · seen: 2026-06-08 · obs: 1
VPC-free control-plane. A deployment needs ≥1
method first (`NoMethodsExist`); `createapideployment stage_type:new` creates the
stage and returns `$.deployment_id`. `createauth` returns ONLY `$.access_token`
(no id) → recover `auth_id` via `listauths $.auths[0].id`. Methods addressed by
`{method_type}`, stages by `{stage_name}` (no ids). name/stage pattern
`^[a-z][a-z0-9-]{1,48}[a-z0-9]$`. privatelink-endpoint needs a real
`privatelink_service_id` (synthetic → 4xx, optional).

**servicewatch** —
> conf: 0.3 · seen: 2026-06-08 · obs: 1
bulk-delete (`DELETE /v1/alerts|dashboards|event-rules`, no path
id) modeled as `{"ids":[...]}` (unproven, mirrors proven `deleteloggroups`).
Create envelopes `$.alert.id`/`$.dashboard.id`/`$.event_rule.id` (unproven).
createalert needs real `metric_id`/`namespace_id`; createeventrule needs real
event/resource/service ids — doc-sample ids used, 4xx expected (still records).

---

## Coverage campaign — Wave 2 facts (2026-06-08, NOT yet runtime-proven)

> 7 cluster-agents authored 36 fragment files / 49 lifecycles closing 302 write
> ops. Static ceiling 55.4% → 78.6%. All bodies docs-derived; promote after a live 2xx.

**Static coverage matching is PATH-only (service-agnostic).**
> conf: 0.7 · seen: 2026-06-08 · obs: 1
`spec.coverage_gap`
and the dashboard match `(method, norm_path)` ignoring service, but the engine
RECORDS under `(method, norm_path, service)`. Consequence: DBaaS-family services
sharing `/v1/clusters/*` roots (mysql/mariadb/epas/postgresql/sqlserver/cachestore
+ data-analytics searchengine/vertica/eventstreams) appear "covered" once ANY
engine covers the path — but each still needs its own fragment to record under its
own host/keys at runtime. All such per-engine fragments were authored.

**Cost-safe coverage-only pattern (virtualserver, databases, org, analytics):**
> conf: 0.7 · seen: 2026-06-08 · obs: 1
for
billable/destructive resources, do NOT provision — soft-capture an existing id (or
a deliberately-empty JSONPath so the `{id}` stays literal → guaranteed 404), fire
every write `optional`+`group`+broad `expect_status:[200,201,202,400,403,404,409,422]`.
The endpoint is CALLED+recorded (counts as covered) without touching real resources.

**VPC reuse extended:**
> conf: 0.7 · seen: 2026-06-08 · obs: 1
loadbalancer, vpn, direct-connect, and the 6 vpc-extra
lifecycles adopt the session-shared VPC via `{"adopt":"vpc"}` (registered in
`dependencies.json:quota_kinds` as `["vpc"]`). The "VPC consumers" set in
`vpc-scheduling-strategy.md` is now larger but all heavy adopters share the one VPC.

**Corrupt `data/api_bodies.json` entries found (TODO fix):**
> conf: 0.7 · seen: 2026-06-08 · obs: 1
`security/iam createsamlprovider`/`setsamlprovider` (`{"_raw":"{'key':'company',...}"}`)
and `networking/vpc createtransitgatewayfirewallconnection` (`{"_raw":"{transit_gateway_id}"}`).
Agents worked around with best-guess bodies; the source entries should be re-extracted.

**Per-family capture/body notes** (unproven):
> conf: 0.3 · seen: 2026-06-08 · obs: 1
block-volume `$.result.id` + flat
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

> conf: 0.7 · seen: 2026-06-10 · obs: 1

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

> conf: 0.7 · seen: 2026-06-10 · obs: 1

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

> conf: 0.7 · seen: 2026-06-11 · obs: 1

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

> conf: 0.3 · seen: 2026-06-11 · obs: 1

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

> conf: 0.5 · seen: 2026-06-11 · obs: 1

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
> conf: 0.7 · seen: 2026-06-14 · obs: 1
- `gen-heavy-ske-upgrade` chain passed end-to-end (1 passed, 35m26s real
  control-plane + node roll): create old cluster v1.33.5 → `PUT
  /v1/clusters/{id}/upgrade {kubernetes_version:v1.34.3}` → RUNNING re-poll →
  `PUT /v1/nodepools/{id}/upgrade {os_version:"22.04"}` (OS version, NOT k8s
  version — node follows control-plane) → kubeconfig GET ×2 → teardown.
- **ske-image**: `GET /v1/images?scp_original_image_type=k8s` — the
  `scp_original_image_type=k8s` query is REQUIRED (api_docs; omission → 400
  "Field required", runs 27483895557/27491816948). `size`/`page` optional.
- Nodes promoted to VALIDATED: ske-image, ske-cluster-upgrade, ske-nodepool-upgrade.

## Resource-model ↔ cross-service.yaml requires reconciliation (R1 validator WARNs, 2026-06-17)
> conf: 0.6 · seen: 2026-06-17 · obs: 1 (offline reconciliation against VALIDATED create bodies)
Five "requires diverges" WARNs resolved by aligning cross-service.yaml (the coarse
L2 graph) to the resource-model node's VALIDATED create body — the body is the API
truth for create-order:
- **lb-health-check** requires **[vpc, subnet]** (was [] in L2). POST /v1/lb-health-checks
  body keys on vpc_id + subnet_id — it is a STANDALONE top-level resource (no
  loadbalancer_id), region-scoped/reusable but still needs vpc+subnet live first.
- **lb-server-group** requires **[vpc, subnet, lb-health-check]** (was [load-balancer]
  + lookups:[lb-health-check] in L2). POST /v1/lb-server-groups body keys on vpc_id +
  subnet_id + lb_health_check_id — NOT loadbalancer_id. The LB link is INDIRECT: a
  listener binds the server-group to the LB. health-check is a HARD prereq, not a lookup.
- **direct-connect** requires **[vpc]** (was [vpc, security-group] in L2). POST
  /v1/direct-connects body carries vpc_id ONLY. Security Group is a documented 선행
  서비스 for the WORKLOAD behind the DC, not a DC create-body field → it stays as the
  cross_constraint `dc-prereq-security-group`, NOT a create-order requires (IB-035).
- **secret** (secretsmanager) requires **[kms-key]** (was [] on the top `secret` L2
  node — the sibling docs node `secretsmanager-secret` already had [kms-key]). POST
  /v1/secrets body sends kms_id = an encrypt-capable KMS key id.
- **backup-policy** requires **[backup-target]** (was [server] in L2). POST /v1/backups
  consumes server_uuid/server_guid DISCOVERED via the backup-target LOOKUP (GET
  /v1/backups/backup-targets), which itself requires a live server. Added a
  `backup-target` lookup node to cross-service.yaml so the L2 graph carries the same
  edge (IB-031: closure unchanged, body uses {backup-target.*} only).

**create-without-delete WARNs confirmed NO-TEARDOWN-BY-DESIGN** (not modeling gaps;
SCP API exposes no DELETE — parent/cluster delete reclaims, or op is fire-and-forget):
- sqlserver ss-* family (add-block-storage, resize-block-storage, resize-server-type,
  restart, sync-state, set-audit-log, patch, switchover, add-secondary, databases) —
  cluster sub-ops; parent sqlserver-cluster DELETE reclaims everything.
- storage/backup backup-manual (manual-backup trigger) & backup-restore (creates a NEW
  billable server reclaimed via compute DELETE — cross-service teardown unsupported,
  node kept enabled:false).
- billingplan planned-compute (no DELETE API; cancellation is a fee-incurring read-like
  POST), storage/archivestorage archiving-policy (policy is immutable/deactivate-only),
  storage/baremetal-blockstorage bm-volume-group (no DELETE /v1/volume-groups/{id} in
  catalog; teardown via member-volume removal).
- platform/sts sts-token & management/servicewatch sw-* are owned by other agents —
  their create-without-delete WARNs are likewise expected-by-design (expiring token /
  catalog-read / fire-and-forget sub-ops).

## Tier-0 LIGHT run (27725293499, 2026-06-18) — findings
> conf: 0.8 · seen: 2026-06-18 · obs: 1 (run 27725293499, kr-west1)
First coverage-max dispatch (`docs/COVERAGE-MAX-PLAN.md` Tier 0). Result:
**134 passed / 2 failed / 27 skipped** (adopt-CRUD 44m; smoke+read-chains ✅).
- **`heavy=false` in `.github/run-request` did NOT propagate to the env** — the
  adopt-CRUD job ran with `SCP_RUN_HEAVY: true` and the heavy ids in `ADOPT_K`.
  No billable infra resulted because every heavy ADOPTER self-skips without a
  shared VPC in the parallel xdist lane (`tests/crud/test_crud_lifecycle.py:43`
  "no shared VPC … skipping adopter instead of self-creating": heavy-shared-dbaas,
  compute-virtualserver-full, database-mysql/postgresql-cluster,
  container-ske-cluster-nodepool, networking-vpn-gateway-tunnel,
  vpc-privatelink-service, direct-connect). **Implication:** a Tier-0 "light"
  dispatch still pulls in heavy *reachability-only* lifecycles (archivestorage)
  because the heavy flag isn't gated; the no-shared-VPC fallback is what keeps it
  non-billable. To force a truly light run, the run-request heavy gate needs to
  reach `SCP_RUN_HEAVY` (workflow option-resolution bug — not yet fixed).
- **archivestorage reachability 401:** archivestorage has NO dedicated auth key
  (owner reachability-only override), so id-bound GETs return **401**, which the
  4 non-optional reachability GETs (show-bucket-versioning/encryption/objects/
  object-versions) did not tolerate → hard fail. Fixed: added 401 to their
  expect_status (4xx-tolerated intent; `storage__archivestorage.json`).
- **iam-policy-extra-writes ReadTimeout** (iam.e.samsungsdscloud.com, 20s) =
  transient network flake, not a defect; passes on re-run (SCP_TIMEOUT=20 is tight
  for iam under load). No code change.
- **Design (A) stage 2 — identity-based auto-probe (2026-06-18).** The auto-probe
  now resolves an id-bound GET's path-param by IDENTITY — the create that
  `produced_by` it (per `data/api_catalog_params.json`), recorded per-lifecycle in
  `produced{create_key→id}` / `produced_rtype{resource_type→id}` — instead of by
  capture-var STRING name. Priority in `_resolve_param`: exact name → identity →
  legacy `_PARAM_ALIASES`. Proven offline (`tests/offline/test_probe_identity.py`,
  5 tests): EVERY `produced_by` param in the sidecar (incl. 8/9 alias targets)
  resolves via identity from an empty name-seed — so the hand alias map is now
  vestigial (only `srn`, name-addressed/no producer, still needs it). Map kept as
  a LAST-resort fallback pending LIVE proof of the identity path; delete the 8
  redundant entries once a live run shows the produced-index path firing 2xx.
  118 offline pass, validator 212/0. (engine: `regression/scenarios/engine.py`.)
- **Leaked privatelink VPC teardown (2026-06-18).** A VPC can be permanently
  stuck at 409 because a **privatelink-service** in it owns an auto-created
  `prvlink-*` customer port (blocks subnet/VPC delete), and the service itself
  409s (`privatelink-service.exist-connected-endpoint`) while it has a connected
  endpoint. The connection is AUTO-approved → endpoint state **ACTIVE**, which is
  **NOT REJECT-able** (`/approval type=REJECT` → `invalid-rejectable-state`; REJECT
  only works on pending). Correct teardown (provider side): GET
  `/v1/privatelink-services/{id}/connected-endpoints` → for each, PUT
  `/v1/privatelink-endpoints/{eid}/connection {"type":"DISCONNECT"}` (enum is
  DISCONNECT/RECONNECT, not on `/connection`'s sibling `/approval` which is
  APPROVE/REJECT) → DELETE the service (202, async) → wait gone (its customer port
  reaps with it) → delete subnet → delete VPC. Now encoded in
  `cleanup.reconciler._purge_vpc_children` (runs first, before LB/NAT/ports).
  NOTE: the disconnect PUT needs `SCP_ALLOW_MUTATIONS=true` (DELETE needs BOTH
  MUTATIONS+DESTRUCTIVE — DELETE is in the MUTATING set too).
- **FAST sweep mode (`SCP_SWEEP_NOWAIT=true`, 2026-06-18).** The reconciler's
  per-resource `_wait_gone` (blocking poll, up to 150–900s each) was the teardown
  bottleneck (audit: DBaaS clusters ~165s ea, SKE nodepools ~380s ea, all serial).
  NOWAIT skips the blocking wait — issue EVERY owned delete, let the fixed-point
  round loop retry whatever still 409s (dependency) next pass. Dependencies resolve
  through retries, not serial waits, so a sweep finishes in rounds not sum(teardown).
  Round count + inter-round pause are env-tunable (`SCP_SWEEP_ROUNDS` default 8 in
  nowait, `SCP_SWEEP_ROUND_SLEEP_S` default 12s so async deletes clear between
  rounds). Owner-tag scoping UNCHANGED (still `_select`-gated). Hand-driven cleanup:
  `SCP_ALLOW_DESTRUCTIVE=true SCP_SWEEP_IGNORE_TTL=true SCP_SWEEP_NOWAIT=true python -m cleanup.reconciler`.
- **(A) enrichment producer-match 90.9%→98.1% (2026-06-18, multi-agent residual pass).**
  The 89 `produced_by:null` self-params were NOT mechanically closable by deeper
  collection matching — diagnosis showed ALL 89 have NO collection-prefix POST.
  Four parallel investigation agents found the real producers (evidence in
  `data/api_docs.json` + `knowledge/formal/resources/*.yaml`); encoded as a
  residual layer in `spec/enrich_catalog.py` (`_DBAAS_SERVICES`/`_RESIDUAL_EXPLICIT`/
  `_RESIDUAL_WAIVERS`, applied ONLY to null self-params so it can't regress the
  890 mechanical matches). Result: **960/979 produced_by (98.1%) + 19 honest
  waivers = 0 unexplained null.** Patterns: (1) DBaaS instance_group_id/
  block_storage_group_id born in the cluster DETAIL read `showcluster`
  (`$.instance_groups[0].id`, nested `…block_storage_groups[0].id`), request_id =
  async-op from createcluster (`$.request_id`); (2) cross-service cluster_id ←
  `container/ske/createcluster` `$.resource_id` (aimlops/cloud-ml/data-flow/data-ops
  consume an external SKE cluster as a body field); (3) pseudo-resource ops
  kms key_id←createkey `$.key.id`, secretvault, configinspection, virtualserver
  subnet_id←vpc createsubnet; (4) waivers: resourcemanager key/resource_identifier
  (name-addressed), cloudmonitoring addrbookId (EOL), scr tags_id (docker-pushed).
  These produced_by directly feed the stage-2 identity probe. 118 offline pass,
  validator 212/0. NOTE: bare `Endpoint.service` (e.g. `mysql`), not `database/mysql`.
- **`-n 2 → 6` parallelism change (dab8a41) was applied COSMETICALLY only**
  (found 2026-06-18 via loggingaudit audit-optimizer on run 27735741382). The
  comments (`api-test.yml:333,572`) and the echo (`:583` "in parallel (-n 6)")
  were updated, but the actual adopt-class pass still hardcoded
  `ARGS=(... -n 2 ...)` at `:587` — so the workflow kept serializing at 2 xdist
  workers. Fixed: `:587` `-n 2 → -n 6`. (The handoff §1 hand-run recipe already
  used `-n 6`; only the workflow file was stale.) **Evidence the lever is real:**
  in the `-n 2` baseline (run 27735741382, df8fb87) the four DB engine families
  (epas 24m / mariadb 31m / mysql 43m / postgresql 44m) ran **staggered, peak
  concurrency 2** → DB-phase wall **120 min** (span-sum 142m). All four are
  `requires=None` adopting the same shared VPC, so `-n 6` fans them out →
  target wall ≈ **max(single engine ~44 min)**, i.e. ~76 min saved on the DB
  phase alone. Re-raising is safe (IB-049 xdist-gated adopter-skip + IB-050
  pre-run reclaim/concurrency-group are the real cap-poisoning guards, NOT the
  `-n` lowering — handoff key-fact #2).
- **`-n 6` DBaaS fan-out VALIDATED LIVE (2026-06-18 heavy n6 run).** The
  `heavy-shared-dbaas` worker fired 3 real billable cluster creates back-to-back
  (within 28s) adopting the shared VPC, so they ran concurrently. From
  loggingaudit create/delete Start/End: mariadb live-span 16.0m / epas 15.4m /
  cache-store 14.6m. **Peak concurrent live DB clusters = 3** (> the -n2 baseline
  peak of 2); **DBaaS-phase wall = 16.0m ≈ max(engine span), NOT sum (46.0m)** →
  ~30m / **65% saved** vs serial on the DB phase. Confirms the `requires=None`
  engines fan out under `-n 6` exactly as predicted (wall→max). 0 billable
  survivors after teardown+sweep (3 independent rechecks). Coverage: +26 newly-
  verified endpoints, 25 distinct DB sub-op 2xx. The 3 DB sub-ops set-archive /
  register-log-export-config / upgrade-kernel return 500 ContactAdminForAssistance
  on all 4 engines — product bugs, now baselined (`known_issues.json`).

## id-bound GET coverage: the "create→get-by-id should raise coverage" question (2026-06-18, branch adoring-heisenberg)

> conf: 0.8 · seen: 2026-06-18 · obs: 1 (full static cross-ref of 302 id-GETs vs reports/results/observations.jsonl)

**The premise is right and the engine already honours it** — `engine._probe_reads` +
`_resolve_param` resolve an id-bound GET's path-param by IDENTITY (which create
produced the id, recorded in `produced`/`produced_rtype`) or by capture-var name,
then fire the GET. Proven live: ALL 12 `compute/scf/show*` id-GETs are 2xx in
crud_probe (showcloudfunction/code/configuration/trigger/etc.) — the probe works.

**Bucketed truth for the 302 `is_read_only & has_path_params` GETs** (against the
current observations.jsonl, a smoke + NON-heavy crud_probe partial run):
- (a) COVERED 2xx ........ **84**
- (b) reached, non-2xx ... **14** (12×400, 2×403 — need real query data / perms)
- (c) NEVER reached ...... **204**

**The (c)=204 breakdown is the answer to "why aren't they covered":**
- **6** — self-param has NO producer (genuine static gap; see waivers below).
- **198** — self-param HAS a producer, but its create never produced a usable id
  *in this run*. Of those 198:
  - **149** the producer create was NEVER OBSERVED — because **57 enabled
    lifecycles never ran in this file (all 61 heavy ones + a few)**; 139 of those
    149 producers ARE steps in an existing (heavy/gated) lifecycle, so they cover
    on a full `SCP_RUN_HEAVY=true` run. Only ~10 producers are in NO lifecycle.
  - **38** the producer create RAN but never got 2xx (id never born — upstream
    create 4xx/skip, a separate problem from id-resolution).
  - **11** the producer RAN & got 2xx but the read still wasn't issued in this
    file (mostly list-lookup producers / cross-service subnet — recover on rerun).

**So the dominant cause is RUNTIME/SCHEDULING (heavy lifecycles gated off this
run), NOT a linking or substitution bug.** Static ceiling: **292/302 id-GETs are
fully producer-linked** in the sidecar (after the ancestor-residual fix below);
only 10–11 are true gaps.

**The 10–11 genuine static gaps are correct WAIVERS** (no producer exists — name-
addressed / console-only / EOL): scr `tags_id` family (docker-pushed image tags,
5 GETs), cloudmonitoring `addrbookId`, resourcemanager component/tag composite
paths keyed by `{region}/{service}/{resource_type}/{resource_identifier}` + `key`/
`srn`. These can only be covered by name-addressing a pre-existing resource.

**The literal-`{cloud_function_id}` 404 (`POST .../privatelink-endpoints`) is BY
DESIGN, not a substitution bug.** It comes from lifecycle `scf-privatelink-
apigateway-coverage`, which deliberately NO-OP soft-captures `cloud_function_id`
so it stays LITERAL → guaranteed 404 → the write endpoint is still RECORDED
(catalog `_norm_path` collapses `{..}`→`*`). It never creates a real function (a
real PL-enable strands the function in 'Creating' and blocks teardown — see that
lifecycle's `_note`). The READ probe is safe: `_probe_reads` filters `mapping` for
`"{"` and `_resolve_param` returns None → the GET is SKIPPED (never sent with a
literal brace). Only LIFECYCLE WRITE steps pass unresolved `{x}` through `_fill`
(intentional for these coverage-404 lifecycles).

**Fix applied (durable, purely additive): `spec/enrich_catalog.py` now applies the
`_RESIDUAL_EXPLICIT` map to ANCESTOR params too** (previously self-only via
`name == last`). This linked 5 ancestor params that were `produced_by=null`:
apigateway `resource_id` on showmethod/setmethod/deletemethod →
`createresource`, and iam `srn` on set/removepermission → `createresourcegroup`.
id-GET static-linkage 291→**292/302**; sidecar self-link 98% (960/979). Offline
127/127 pass, validator 212/0. The remaining recovery is OPERATIONAL: run the
heavy lifecycles (`SCP_RUN_HEAVY=true`) to birth the ~149 producer ids whose
reads then auto-probe — that is where the bulk of the missing 204 lives.

## queueservice getqueueattributes — required query params (LIVE-PROVEN 2026-06-18)

`GET /v1/queues/{queue_id}/attributes` (catalog key
`application-service/queueservice/getqueueattributes`) REQUIRES **two** query
params: `attributes` and `name`.
- Bare call → 400 `ValidationError`, `detail: ["Field required", "Field required"]`.
- Only one supplied → 400 with a single `"Field required"`.
- `attributes=All` **AND** `name=<the queue's own name>` → **200** (full attributes
  payload). `attributes` is **case-sensitive**: `ALL`/`all` → 400.
The earlier "empty 400 body" was a bare-call ValidationError. This was the one
failing id-bound GET in queueservice; the suite now exercises it correctly
(scenario `application-queueservice-queue:get-attributes` step with
`params:{attributes:"All", name:"regrq{unique}"}`, and `_probe_reads` now feeds
sidecar-declared REQUIRED query params via `_QUERY_DEFAULTS` (attributes=All)).
queueservice catalog coverage: 12/12 endpoints OK.

queueservice `update-deduplication` / `update-deduplication-scope` 400 on a
**Standard** queue by design: `content_based_deduplication` and
`deduplication_scope` are **FIFO-only** (`scp-queueservice.invalid-*`, "queue name
must end with '.fifo'"). The per-service catalog ops 200 elsewhere because they
ran against a `.fifo` queue. Not a regression — correctly isolated as optional
groups in the queue lifecycle (qdedup/qdedupscope), so the create→delete spine
still passes.

## apigateway listreports / 503 LISTs / createprivatelinkendpoint — LIVE-PROVEN (2026-06-18)

**listreports date window (root-caused, FIXED).** `GET /v1/apis/{api_id}/reports`
(`application-service/apigateway/listreports`) takes 3 REQUIRED query params
`stage_name`, `start_date`, `end_date` (dates `YYYY-MM-DD`). The 400 was NOT a
missing-param / entitlement problem — the gateway enforces **two** range rules,
confirmed live against a throwaway api:
- `start_date=2025-01-01 end_date=2025-12-31` → 400
  `scp-application-apigateway.api.invalid-date-range` **"Date range cannot exceed 30 days."**
- any window starting >30 days ago → 400
  `scp-application-apigateway.api.invalid-past-date` **"Dates cannot be earlier than 30 days ago."**
- bare (no `stage_name`) → 400 `ValidationError` "stage name should be 3~50 characters …".
A **rolling 29-day window ending today** satisfies both rules → **200**
`{"count":0,"reports":[],"top_resources":[]}` (empty for a no-traffic api, even
with a not-yet-created stage). Fix: scenario step now uses
`?stage_name=dev&start_date={iso_29d_ago}&end_date={iso_today}` (new engine
placeholders). The old hardcoded calendar-year range always 400'd.

**503 on plain LISTs = transient gateway saturation, NOT a defect.**
`GET /v1/apis` (`listapis`) and `GET /v1/privatelink-endpoints`
(`listprivatelinkendpoints`) intermittently return **503 "upstream connect error
… connection timeout"** under heavy parallel load (15s connect-timeout). On direct
re-issue both return **200** consistently (~1s): `listapis` → `{"apis":[],"count":0}`,
`listprivatelinkendpoints` → `{"count":0,"privatelink_endpoints":[]}`. The HTTP
client already retries `RETRY_STATUS={502,503,504}` with exponential backoff, which
recovers them in normal conditions; no code change needed (a blanket 5xx→soft would
mask real server defects). Both recorded OK (covered).

**createprivatelinkendpoint 500 = genuine PRODUCT BUG (PF-23, baselined).**
`POST /v1/privatelink-endpoints`. DOCS-VERIFIED our body is CORRECT:
`privatelinkendpointcreaterequest` requires exactly `{name (^[a-zA-Z0-9]*$,
min 3 / max 20), privatelink_service_id (required), description? (optional)}` and
the scenario sends precisely that (`regrple{ualpha}` = 15 lowercase chars, valid).
A bad/non-existent `privatelink_service_id` returns **500 ContactAdminForAssistance**
(req-f088cd1d) instead of the expected 400/404 — same ContactAdmin-class as budget
createaccountbudget / apigateway setresourcepolicy (PF-19). NOT a body-shape gap we
can fix. Baselined in `known_issues.json`; the `xcov-pl-create` group tolerates 500.

**privatelink approve/connect/request/set/delete 403 = entitlement (NOT coverable).**
The privatelink mutation ops 403 "You do not have permission to Action" — the
missing-IAM-action-definition class (PF-01/02/03/15/18). Correctly `soft`, already
tolerated in `expect_status`; the CALL is recorded for coverage. Not a regression.

## HEAVY_STALL root cause: DB-engine lifecycles env-skipped when shared-VPC env not propagated (2026-06-19, branch adoring-heisenberg)

> conf: 0.9 · seen: 2026-06-19 · obs: 1 (live heavy-dbaas recovery run, 46:41 wall)

**Root cause of the 00:18 heavy stall (provisioned the shared VPC, created 0 DB
clusters, then died).** The shared VPC was provisioned out-of-band at 00:16
(`shared_provision.stderr.log`) but its `SCP_SHARED_VPC_ID` / `SCP_RUN_HEAVY` env
were **not propagated into the heavy DB pytest subprocess**. Every DB-engine
lifecycle is `heavy=True` with `requires=None`; with `SCP_RUN_HEAVY` unset in that
shell the engine takes the gate at `engine.run_lifecycle` (`engine.py:645`,
"heavy lifecycle — set SCP_RUN_HEAVY=true to run") and returns `status='skipped'`
BEFORE firing any create. Smoking gun: the stalled `heavy_dbaas.log` is literally
`bringing up nodes...\n\ns.....` — `s`=skipped, no creates. The host-reachability
preflight DNS failures (eventstreams/searchengine 503/NameResolution) were a RED
HERRING — transient and unrelated to the skip. **Lesson: the env that gates heavy
lifecycles (`SCP_RUN_HEAVY` + the three `SCP_SHARED_*_ID`) must be exported in the
SAME shell as `python -m pytest`; a separate provisioning step that only writes
`shared_ids.txt` does NOT make pytest adopt them.** `provision_shared_vpc` IS
env-aware (`engine.py:1185`, adopts `SCP_SHARED_VPC_ID` → no 2nd VPC, no-op
teardown) — so once the env is correctly exported the adopt path works perfectly.

**Recovery run RESULT (gates+shared-VPC env exported in-shell, `-n 6`).** 8 real
billable `POST /v1/clusters → 202` across mysql/mariadb/epas/cachestore plus the
`heavy-shared-dbaas` mariadb/epas/cachestore trio; postgresql `createcluster`
500'd (see baseline below). **Peak concurrent live DB clusters = 3** (the
`requires=None` engines adopting the same shared VPC fan out under `-n 6` exactly
as the 2026-06-18 fact predicted — wall ≈ max(engine), not sum). Each ACTIVE
cluster let the engine identity-probe fire its id-bound sub-op GETs: **22 distinct
DB sub-op id-GET 2xx** newly covered — per engine `showcluster`,
`listbackuphistories`, `listparametervalues` (+ mysql/mariadb/epas also
`listlogexportconfigs` / `listreplicas` / `showarchiveconfig`; cachestore also
`listcommands`). id-bound-GET 2xx coverage **158 → 167 (+9 endpoints)** by the
fixed-matcher recount (`reports/audit/count_idget.py`). Lifecycles self-tore-down
(10× DELETE `/v1/clusters/<id>` → 202); reconciler sweep + 3 independent rechecks =
**0 owned billable survivors** incl. the shared VPC `regrvpcsh6a348985` (DELETE
204). 4 pytest FAILs were env/backend, not harness: postgresql create 500, and
3 `*-subops-guarded` hit `upstream connect error / connection timeout` (gateway
resets under the concurrent heavy load).

**NEW PRODUCT BUG: `database/postgresql/postgresqlcreatecluster` → 500
ContactAdminForAssistance.** Live 2026-06-19, fired twice (lifecycle create +
subops-guarded create), both 500 `{"code":"ContactAdminForAssistance"}`. The other
4 engines (mysql/mariadb/epas/cachestore) created fine with the identical
shared-VPC body shape, so this is a postgresql-backend create fault, not a body/
linking bug. Baselined in `known_issues.json` (Product Bug). Same `ContactAdmin`
class as the already-baselined `*registerlogexportconfig` 500s.
