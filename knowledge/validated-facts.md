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
  **Shared test Object-Storage bucket = `do-not-delete-apitest`** (owner-provisioned
  2026-06-20, do NOT delete) — use as `object_storage_bucket_name` for `createtrail`
  and any test that needs a real, pre-existing OBS bucket. Object Storage itself is
  NOT in the tested catalog (no objectstorage service), so this bucket is the only
  way to supply a real bucket ref on the shared account.
- **resourcemanager**: tags ≤50 per resource.
- **resourcemanager `showresourcebycomponents`** (`GET /v1/resources/{region}/{service}/{resource_type}/{resource_identifier}`) — **LIVE-VERIFIED 200, 2026-06-20.** Component-addressed sibling of `showresource` (which uses a b64 `{srn}`). The 4 path segments are **PLAIN, not base64** (unlike `{srn}`/`{key}`), and the 4th segment is the resource **`id`** — in the `/v1/resources` list response `$.resources[i].resource_identifier` is **null**, so capture `$.resources[i].id`. Returns `$.resource` (singular). Wired into `resourcemanager-tag-lifecycle` (step `show-resource-by-components`); closed the last resourcemanager id-GET reachability gap.
- **iam resource-policies use the SAME base64-SRN decoder as resourcemanager** (LIVE-CONFIRMED 2026-06-20): `PUT/DELETE /v1/resource-policies/{srn}`, `POST .../statements`, etc. require the `{srn}` path segment base64-encoded — plain SRN -> 400 'SRN decoding error', b64 -> decode succeeds. Wired in `management__iam.json` (iam-resource-policy: list resourcemanager /v1/resources -> capture $.resources[0].srn -> b64_encode -> {iam_srn_b64}). Residual: the policy `Action` must match the target resource's service (a servicewatch SRN rejects `iam:*` with Iam.UnSupportedActionInPolicy).

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

> Authored by parallel service-agents (see `docs/agent-team.md`). Bodies/envelopes
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
Triage: `docs/working/handoffs/HANDOFF-fail-new-triage.md`.

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

> Levers ①③④ of `docs/working/handoffs/SESSION-HANDOFF-run6-and-ops.md`; full analysis in
> `docs/working/plans/COVERAGE-WAVE-PLAN.md`. Promote after a live 2xx.

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
First coverage-max dispatch (`docs/working/plans/COVERAGE-MAX-PLAN.md` Tier 0). Result:
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

## observation `endpoint_key` has TWO shapes — service-aggregation MUST handle both (2026-06-27, branch ecstatic-tesla)

> conf: 0.95 · seen: 2026-06-27 · obs: 2 (reports/results/*.jsonl direct read + live Reporting fix)

`reports/results/observations-*.jsonl` carries `endpoint_key` in **two distinct
formats**, by source:
- **`category/service/op`** (slash) — the read-only smoke/crud sweep
  (`regression/smoke.py`, `read_chains.py`). e.g. `networking/security-group/deletesecuritygroup`.
- **`<lifecycle>:<step>`** (colon) — the **lifecycle engine** (`engine.py`, what an
  actual Testing run records). e.g. `gen-cost-reads:create-cost-reads`.

They are unambiguous: lifecycle ids are hyphenated with no `/`; sweep keys carry no `:`.
**Any code that aggregates observations → service MUST resolve the colon shape via
the lifecycle→service map** (`loader.load_lifecycles()[id]['service']`), else every
live Testing run is invisible to coverage. This bit the new Reporting surface
(`controlplane/reporting_routes.py _service_of_key`) — a run turned 3 services green
(quick-query / costexplorer / kms) yet coverage read +0 until fixed. Format-agnostic
consumers (`tools/derive_verified.py`, which keys by the *whole* endpoint_key) are
unaffected. Note a "soft" 404 (e.g. `gen-cm-account-resource` → `/v1/cloudmonitorings/product/v2/accounts/products`,
a v1/v2 path smell) passes pytest but correctly does **not** count as tested.

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

## IAM `DELETE /v1/policies/bulk` deletes ALL account policies REGARDLESS of body — un-probeable, step REMOVED (DANGER)

**Backend behavior (verified across TWO live runs):** `DELETE /v1/policies/bulk`
(catalog key `management/iam/deletepolicies`) **ignores the `policy_ids` request
body entirely** and fans the call out into a delete-ALL-account-policies attempt.
There is **NO request body that makes this endpoint safe.**

- **Run #124** (2026-06-19T08:20:20–52Z): body was a synthetic/unmatched id
  `{"policy_ids": ["000…0"]}` → 420 `policy.delete.start` over 240 distinct
  NON-test policy names. First read as "unmatched id == delete-all."
- **Run #125** (main @ ea4ade38, 2026-06-19T11:21Z): the EARLIER "fix" was in
  place — `create-policy-for-bulk` created an OWNED `regrpolb*` policy (Create
  End=1 at 11:21:25Z, capture `$.id` correct, same as the working `create-policy`
  step) and the body carried that **real owned id**. The mass-delete fired ANYWAY:
  3s after the create, at 11:21:28Z, a wave of **422 `policy.delete.start`** (28→58/sec)
  / **417 `policy.delete.error`** hit **237 distinct NON-test SYSTEM policies**
  (AdministratorAccess_ACL, ObjectStorageAccess, PaaSCommon-*, OperatorAccess_ACL,
  Support, …). The owned-id body did NOT scope it.

So the correct finding is stronger than the run-#124 reading: the endpoint
**deletes ALL regardless of body**, not just on an unmatched id. All non-owned
deletes are backend-refused (runner identity can't delete built-ins → zero
damage so far), but this is an unfiltered mass-delete of account policies every
time it is called (Hard Rule 3 hazard).

**Rule:** `DELETE /v1/policies/bulk` is **un-probeable safely — never call it.**
The first fix (owned bulk-target id) was proven INSUFFICIENT on run #125. The
`pol-bulk` group (`create-policy-for-bulk` + `delete-policies-bulk`) **has been
removed** from `management__iam.json`, and `management/iam/deletepolicies` is
waived (`data/baselines/coverage_waivers.json`, class `blast-radius`) so it is
never re-added. There is no body shape that scopes this endpoint.

## 2026-06-20 — Full heavy DAG run (tools/dag_run_live.py ALL, 4118s wall)

### 503 gateway storm = load-induced Envoy saturation (NOT our request bug)
> conf: 0.9 · seen: 2026-06-20 · obs: 117

**Pattern:** 117 of 152 obs-level failures (77%) share the exact error string
`upstream connect error or disconnect/reset before headers. retried and the latest
reset reason: connection timeout` or `reset reason: connection timeout`. This is
the Envoy sidecar's upstream-connect timeout, triggered server-side when the
backend service pool is saturated. It is **not** a request shape or HMAC issue.

**Time window:** concentrated 12:07–12:25 KST (run minutes 7–25) when AIMD
concurrency peaked, then clamped to floor 4.

**Affected services during the storm:** aimlops-platform, billingplan, budget,
cachestore-read, certmanager-import, data-flow-read, eventstreams-read, gslb,
loggingaudit-trail, mngc-gpu-node, networking-security-group-create,
quick-query-read, scf-create, ske-create, sts-token, vertica-read, vpc-peering-approve.

**Key rule:** a 503 `upstream connect error … connection timeout` on an
*optional step* is NOT a lifecycle failure — the step skips its group and the
spine continues. A 503 on a *required step* causes lifecycle failure. Under
AIMD floor=4, the storm self-limited after ~18 min.

**Do NOT baseline 503s as product bugs unless they reproduce at low concurrency
(≤4) outside the storm window.** The http_client already retries `{502,503,504}`
with exponential backoff (3 retries). A persistent 503 at low concurrency =
real backend issue; a cluster of 503s under peak concurrency = load-induced saturation.

### Heavy lifecycle failure pattern: storm-transient vs structural
> conf: 0.8 · seen: 2026-06-20 · obs: 8

Of the 8 heavy adopter/self-creator failures in the 2026-06-20 full heavy run:
- **Storm-transient (no code fix needed, safe to re-run):**
  - `gen-heavy-aimlops` (all groups 503-skipped during storm)
  - `gen-heavy-ske-upgrade` (transport timeout on `create-ske-cluster`)
  - `container-ske-cluster-nodepool` (transport timeout on `create-ske-cluster`)
  - `compute-virtualserver-full` (likely storm-hit during VM create polling)
  - `gen-heavy-vs-netops` (cascade from storm-killed LB create)
- **Structural (require a code fix):**
  - `gen-heavy-lb-members` → 400 `SubnetNotAssociatedWithLoadBalancer`: the
    shared subnet `ddcfcc23a22546aab8fa16d7e1d8a2fe` does not contain a Load
    Balancer. The LB must pre-exist in the subnet before `lb-healthcheck-create`.
  - `gen-wave5-apigw-privatelink` → 400 `ip-address-overlap`: DUAL-MODE pitfall,
    NOT a simple IP swap — see the corrected "PrivateLink Service IP must match the
    BOUND subnet" fact below. The lifecycle adopt-falls-back to the shared subnet
    but keeps its own-block IP; proper fix derives the IP from the bound subnet's
    live CIDR. Needs live validation.
  - `gen-wave4-asg` → 400 `InvalidAutoScalingGroupLaunchConfigurationId`: the
    ASG group create rejected the LC ID captured in the prior step. Investigate
    capture ordering.

The 3 big DBaaS lifecycles (`database-mysql-cluster` 2544s,
`database-postgresql-cluster` 2744s, `heavy-shared-dbaas` 929s) all PASSED
despite the storm — their long polling windows rode out the 18-min storm window.

### Scheduling-tail measurement: vpc-peering under static vs dynamic scheduler
> conf: 0.9 · seen: 2026-06-20 · obs: 1 (measured + simulation-confirmed)

`vpc-peering` measured duration: **1267.9s (21.1 min)** in this run
(`data/optimizer/durations.json`). Under the static wave scheduler
(`dag_runner.run_plan`) it was placed in the LAST self-create wave (alphabetical
ordering) and ran as the final lifecycle, adding ~15 min to the tail.

`dag_scheduler.simulate_selfcreate` (pure simulation, no execution) confirms:
- **Static (cap-packed, alpha, wave-barrier): ~44.1 min** self-create portion
- **Dynamic (longest-first, slot-gated): ~23.0 min** self-create portion
- **Expected saving: ~21 min (48% of self-create portion)**

**The fix is pending wiring** — `dag_scheduler.run_dynamic` exists and is
unit-tested but `tools/dag_run_live.py:234` still calls `dag_runner.run_plan`.
Switch is a 3-line change (import + replace the call, same RunResult shape).

### dag_run_live.py does NOT call schedule_optimizer.update_durations (gap)
> conf: 0.9 · seen: 2026-06-20 · obs: 1

`tools/dag_run_live.py` calls `dag_runner.run_plan` directly and exits without
calling `schedule_optimizer.update_durations`. As a result, all entries in
`data/optimizer/durations.json` have `n:1` — the rolling average never learns.
`dag_runner.main()` (lines 270–273) does call `update_durations`, but
`dag_run_live.py` bypasses `dag_runner.main()`.

**Fix:** add after the `result = ...` call in `dag_run_live.py main()`:
```python
try:
    from regression.scenarios import schedule_optimizer
    schedule_optimizer.update_durations(
        schedule_optimizer.measured_from_result(result))
except Exception:
    pass
```

### filestorage-volume teardown conflict (deterministic, not storm)
> conf: 0.8 · seen: 2026-06-20 · obs: 2

`filestorage-volume` lifecycle `delete-volume` step hits
`filestorage.BadRequest.Invalid.volume.purpose: Cannot delete volume because
replication is in use. Delete Policy from replication or backup volume.`
The replication policy (created in `create-replication` step) must be deleted
BEFORE the volume. This is deterministic — it recurs every run with replication
enabled. Fix: add a `delete-replication-policy` step before `delete-volume` in
the filestorage lifecycle teardown sequence.

### PrivateLink Service IP must match the BOUND subnet (dual-mode pitfall) — NOT a simple IP swap
> conf: 0.9 · seen: 2026-06-20 · obs: 1 (400 `ip-address-overlap`) · CORRECTED 2026-06-20 (orchestrator verification)

`gen-wave5-apigw-privatelink` `create-privatelink-service` sends
`service_ip_address: "10.163.8.5"` and got 400
`scp-network.privatelink-service.ip-address-overlap`. **Root cause is subtler than
"IP outside shared subnet" — the lifecycle is DUAL-MODE.** Its `create-vpc` /
`create-subnet` steps carry `adopt: vpc` / `adopt: subnet`, so:
- **self-create mode** (no shared VPC): it creates its OWN block — vpc `10.163.0.0/20`,
  subnet `10.163.8.0/24` — and `10.163.8.5` IS inside that subnet → **correct**.
- **adopt mode** (full heavy run, shared VPC present): it adopts the SHARED subnet
  `10.124.0.0/24`, but `{subnet_id}` then points at the shared subnet while the IP
  is still the own-block `10.163.8.5` → **deterministic 400 overlap**. This is what
  failed in run 2026-06-20.

**Do NOT just swap the hardcoded IP** — `10.124.0.x` would fix adopt mode but BREAK
self-create mode (outside `10.163.8.0/24`). `fixed_ip_map` in dependencies.json is
metadata-only (the engine does NOT consume it), so there is no runtime re-home.
**Correct fix = the `_note`'s "R3": derive service/connected IPs from the live CIDR
of whichever subnet is actually bound** (capture subnet CIDR → compute a free host),
OR force the lifecycle self-contained (drop the adopt so it always self-creates its
own block, at the cost of a VPC cap slot). Either needs a live run to validate —
deferred (do not apply a blind one-liner).

**Optimizer TIER-D mis-diagnoses (verified false 2026-06-20, no change made):**
`servicewatch-dashboard:create-dashboard` already lists 201 in `expect_status`
(`[200,201,202,400,403,404,409,422]`); ALL four dashboard lifecycles include 201.
`filestorage-replication-schedule` already deletes `delete-replication` BEFORE
`delete-volume` — teardown order is already correct; the `createvolume` ×2 fails
were 503-storm-transient, not a teardown conflict.

### IAM Identity Center (idc-*) requires instance_id for all operations
> conf: 0.8 · seen: 2026-06-20 · obs: 4 (idc-group, idc-user, idc-account-assignment, idc-permission-set)

All four `idc-*` lifecycles fail their first list step with 400 `Field required`
(one or two `Field required` detail entries). The missing required field is
`instance_id` — the IdC instance ID is a required query parameter for all IdC
list/CRUD operations. The lifecycles currently soft-capture from a
`list-instances` step that finds nothing (no IdC instance in the account), so
`{instance_id}` stays unresolved and every subsequent call 400s.
These are **not storm failures** — they occur at the very start of the lifecycle
before any storm window. Fix: either gate the entire lifecycle on a
`soft-capture instance_id` from `list-instances` (skip if nothing found) or
supply a known instance ID as a default parameter.

### LB health-check requires a Load Balancer pre-existing in the same subnet
> conf: 0.8 · seen: 2026-06-20 · obs: 2 (gen-heavy-lb-members, heavy-shared-networking)

`POST /v1/lb-health-checks` returns 400
`scp-loadbalancer.lb-health-checks.SubnetNotAssociatedWithLoadBalancer: Unable to
process the request because the chosen subnet does not contain a Load Balancer
(subnet_id: '<id>'). Please ensure a Load Balancer exists within the subnet before
attempting again.`

This means `lb-healthcheck-create` REQUIRES a Load Balancer to already exist in
the target subnet. The cross-service.yaml entry `lb-health-check requires
[vpc, subnet, lb-health-check]` from the 2026-06-17 reconciliation was correct
in listing `lb-health-check` as a lookup, but this evidence shows the LB itself
must pre-exist in the subnet as a physical resource (not just as an ID reference).
Fix: add a `create-loadbalancer-in-subnet` step before `lb-healthcheck-create`
in both `gen-heavy-lb-members` and `heavy-shared-networking` lifecycles, or use
a dedicated LB subnet instead of the shared general subnet.

### The "503 storm" originates in the EGRESS PROXY, not SCP's API (2026-06-20)
> conf: 0.85 · seen: 2026-06-20 · obs: captured 503 headers under controlled burst + env inspection

The recurring `upstream connect error or disconnect/reset before headers …
connection timeout` 503s that fail heavy lifecycles are **NOT generated by the SCP
API**. Evidence (controlled concurrency burst from the Claude-remote container):
- A captured 503 carries ONLY `content-length, content-type: text/plain, date` —
  it has **no `scp-request-id`** and **no `strict-transport-security` (HSTS)**, both
  of which EVERY SCP 200 response carries. So the 503 was generated BEFORE the
  request reached SCP's TLS edge / app (which stamp those headers).
- Body is the generic Envoy proxy error; content-type is text/plain (SCP API is
  JSON). No `x-envoy-*`/`server` headers.
- The Claude remote env exposes `CLAUDE_CODE_PROXY_RESOLVES_HOSTS=true` +
  `CLAUDE_CODE_USE_CCR_V2`/`CCR_*` — a TRANSPARENT egress proxy (no explicit
  HTTP_PROXY; `requests.get_environ_proxies` → {}). The 503 is this egress path's
  Envoy failing to establish the upstream connection to SCP under a concurrent-
  connection BURST.
- SCP itself is healthy: 100 concurrent GETs → 98/100 × 200 (with scp-request-id);
  503s only appear under sustained heavy-run connection pressure and vanish when
  concurrency drops (why AIMD floor-4 + heavy burst-stagger help).

**IMPLICATIONS (do not mis-attribute):**
1. Heavy-lifecycle failures whose root cause is a 503 on a required step
   (e.g. mysql `find-engine-version`, ske creates) are **TEST-ENVIRONMENT /
   egress artifacts, NOT SCP defects** — must NOT count against SCP in
   conformance/regression, and should be re-tried, not baselined as API bugs.
2. The AIMD limiter + dag_scheduler burst-stagger are mitigating the **egress
   proxy's** connection limit, not an SCP rate limit — still the right levers.
3. A dedicated test server on a direct network path (ROADMAP "dedicated-server
   runs") would likely not see this storm → heavy lifecycles pass far more
   reliably. The current pass/fail of storm-sensitive heavies is path-dependent.
4. Residual uncertainty: could not capture the 503 from an alternate network path
   to 100% exclude an SCP outermost-edge Envoy; but the total absence of SCP
   headers + the transparent-proxy env strongly favours the egress path.

#### Egress proxy is TRANSPARENT + policy-enforced — cannot be bypassed in-container (2026-06-20)
Every outbound connection from the Claude-remote container egresses via a synthetic
namespace (local socket src `192.0.2.2`, TEST-NET-1/RFC5737) — confirmed for SCP AND
api.github.com; arbitrary dests (8.8.8.8:53) are blocked. So the egress proxy is a
TRANSPARENT network-layer control (no HTTP_PROXY to unset, no NO_PROXY bypass; the
env's network policy). Therefore the 503 storm CANNOT be avoided by app-level config
from here. Storm-free heavy validation must run on a DIFFERENT egress path:
  * CI / GitHub Actions (api-test.yml) — runner is on GitHub infra, NOT this proxy →
    no storm. (Local-only because the Claude token gets 403 on workflow_dispatch; an
    owner-triggered dispatch uses the clean path.)
  * the M4 dedicated runner/worker on a direct network.
Local in-container heavy runs are intrinsically storm-prone; the dynamic-dispatcher /
stagger validations are sound but their makespan is confounded by this egress storm.

#### In-container heavy runs are storm-DOMINATED and time-variable — mitigations are second-order (2026-06-20)
Cross-run evidence that the egress-proxy 503 storm cannot be tuned away in-container:
- run#2 (NO mitigations): all 4 big DB creates PASSED (postgres/mysql/dbaas/aimlops).
- run#4 (ALL mitigations: warm pool pool_connections=96 + heavy-stagger 5s + AIMD):
  all 4 big DB creates FAILED — each on a 503 `upstream connect error` at a required
  step (find-engine-version / create-kubernetes-version / wait-subnet = first-contact
  cold connect to the service host under burst).
So MORE mitigation produced a WORSE outcome — proof that the storm severity is
TIME-VARIABLE (depends on the egress proxy's momentary load) and DOMINATES; our
levers (AIMD/stagger/warm pool) are second-order and cannot make in-container heavy
runs reliable. Warm pooling reduces REPEAT-host cold connects but cannot prevent the
FIRST-contact 503 to each of ~60 service hosts when longest-first bursts them.
CONCLUSION: do not chase 503 reliability with more in-container tuning. The dynamic
dispatcher + stagger + warm-pool are validated offline and the early-start hypothesis
is proven live (postgres dispatched +0s vs +7.9min static), but storm-free heavy
validation REQUIRES a different egress path — CI/GitHub Actions (owner workflow_dispatch)
or the M4 dedicated runner. Local in-container heavy = coverage-noisy, makespan-confounded.

#### EMPIRICAL CONFIRMATION: 503 storm is the egress proxy, NOT SCP — CI heavy passed (2026-06-20)
End-to-end proof via the dedicated chat-heavy CI workflow (GitHub-runner egress,
NOT the Claude-remote proxy), run 27884093268 — conclusion SUCCESS:
- in-container iteration-4 (warm pool + stagger 5): mysql AND postgresql both FAILED
  at create on egress-proxy 503 (lifespan 0).
- CI same SCP account, clean egress: mysql regrdbadophepa created 21:19:55Z, full
  lifecycle, deleted 21:55:56Z (lived 36m, 14 ops); postgresql regrpgamhkdaae 21:56→
  22:40 (lived 44m, 26 ops). Both created→lifecycle→teardown cleanly; reconciler
  sweep reclaimed everything (survivors 0). Heavy-CRUD step + sweep step both green.
CONCLUSION (now empirical, conf 0.95): SCP creates DB clusters perfectly when reached
over a clean network path. The 503 storm that fails in-container heavies is 100% the
Claude-remote container's transparent egress proxy. Storm-sensitive heavy validation
MUST run via the chat-heavy CI workflow (or M4 dedicated runner), never in-container.

#### ARCHITECTURE RULE: optimizations live in SHARED code, never a single driver (2026-06-21)
Root-cause of "the improvement disappeared in CI": perf/behaviour optimizations were
siloed in the dag driver (`dag_run_live` / `dag_scheduler` / `dag_runner_live`), while
CI (`api-test.yml`) and the standard tests run `pytest tests/crud` (xdist). So the
optimizations applied on the LOCAL/dag path but NOT the pytest/CI path — they "vanished"
depending on which engine ran. FIX = move them into shared code so EVERY path inherits:
  * warm per-host connection pool -> ``core.http_client.ApiClient.__init__`` (was only
    in ``dag_runner_live._build_client``). Every ApiClient — pytest, dag, smoke,
    coverage — now reuses host pools. Env: SCP_POOL_CONNECTIONS(96)/SCP_POOL_MAXSIZE(40).
  * longest-job-first ordering -> ``tests/crud/conftest.py`` pytest_collection_modifyitems
    sorts lifecycle cases by ``data/optimizer/durations.json`` avg_s desc (was only the
    dag dispatcher's longest-first). Under xdist the big DB/K8s clusters now lead the
    distribution (start at t=0) instead of running late in collection order.
Engine is also unified: chat-heavy uses the SAME pytest-xdist path as api-test.yml (no
divergent driver). RULE going forward: if an optimization helps, put it where ALL paths
see it (core/* or conftest), and unify engines — do NOT improve one driver in isolation.

## Cleanup-reconciler teardown gotchas (tag-scoped sweep) — billable leaks + the 8-round loop (2026-06-22)

> conf: 0.85 · seen: 2026-06-22 · obs: 1

Found during a live cleanup that had to resolve these BY HAND after the reconciler
sweep looped 8 rounds (its max) without converging and left billable resources behind.
All four facts are dependency/directionality quirks the spec does not state; the fixes
live in `cleanup/reconciler.py` (offline-tested in `tests/offline/test_reconciler_convergence.py`).

- **virtualserver custom image PINS its source volume — sweep `/v1/images` FIRST.** A
  custom image created from a VM volume (`regrimg*`, `POST /v1/images`, key
  `compute/virtualserver/createimage`) holds its source block volume open:
  `DELETE /v1/volumes/{id}` → `400 Snapshot.InvalidSnapshotDeleteRequest` —
  *"Volume linked to the Server Custom Image cannot be deleted."* So a leaked custom
  image makes its source volume un-reapable forever. Dependency order is
  **image → snapshot → volume**: reap the image first. `DELETE /v1/images/{image_id}`
  (key `compute/virtualserver/deleteimage`) → 204 clears it; the volume then deletes
  normally. The reconciler historically had NO `/v1/images` pass at all → permanent
  image+volume leak. (Owned-only: match `regrimg*`/owner-tag; platform base images
  carry neither and must never be touched.)

- **filestorage replication: pause + delete from the REPLICA (kr-east1) side; the
  SOURCE side always 400s.** A replicated filestorage volume cannot be deleted while
  the replication is live: `DELETE /v1/volumes/{id}` →
  `400 filestorage.BadRequest.Invalid.volume.purpose` (*"Check the volume purpose"* /
  replication in use). The replication must be torn down, and the destructive calls
  are accepted **only from the replica volume's side** (the source side 400s
  "Check the volume purpose"). Proven sequence, all addressed to the REPLICA with its
  id in the query string:
  - `PUT /v1/replications/{rid}?volume_id={replica_id}` body
    `{"replication_update_type":"policy","replication_policy":"paused"}` → 202
    (key `storage/filestorage/setvolumereplication`)
  - `DELETE /v1/replications/{rid}?volume_id={replica_id}` → 202
    (key `storage/filestorage/deletevolumereplication`)
  - THEN `DELETE` the source + replica volumes. The replication delete is async, so an
    immediate volume delete still 400s on the race — retry the volume delete after the
    replication delete settles (the reconciler's round loop does this). List the pair
    with `GET /v1/replications?volume_id={id}` (the `volume_id` query is REQUIRED).

- **the reconciler historically counted a 4xx delete as "deleted" AND only swept the
  primary region → SILENT billable replica leak + non-convergence.** Two compounding
  bugs: (1) the filestorage volume pass did `if vid and _delete(...)` and `_delete`
  returns the raw HTTP status, so a `400` ("replication in use") was truthy and tallied
  as deleted — it logged "deleted" while nothing went away, so the item re-listed every
  round (this is what made the "10 listed / 10 deletable that delete every round but
  reappear" loop). (2) The sweep only visited the primary region (`kr-west1`), so the
  replication's **replica volume in `kr-east1`** was never even listed → leaked
  (billable) forever. Fixes: never count a non-2xx/404 delete as progress
  (`_is_2xx_or_gone`/`_note_progress`); pause+delete the replication (replica-side)
  before the volume; sweep extra regions via `SCP_SWEEP_REGIONS=kr-east1` (a
  `dataclasses.replace(cfg, region=...)` clone of the client — `core.config.Settings`
  resolves the host from `region`, so the same creds reach `kr-east1`).

- **IAM-gated SKE log-group `/scp/ske/regr*` cannot be deleted with this credential —
  it makes the sweep loop.** Its bulk `DELETE /v1/log-groups {ids:[…]}` returns **200**
  but the group PERSISTS, because deleting it requires removing a child log-stream that
  sits behind a **403 IAM gate this credential lacks** (needs the log-stream delete IAM
  action). The deceptive 200 looked like success, so the old code counted it deleted and
  it re-listed every round → ran to max rounds. There is no force path with this
  credential; it must be **reported, not forced**.

- **convergence rule (the actual loop fix): persistent-after-delete = stuck.** Items
  that report a truthy/2xx delete yet RE-LIST (same id) a later round are un-deletable
  with this credential/shape (the two cases above). The reconciler now records the id of
  every owned item whose delete did NOT achieve teardown; if that id is still listed in a
  later round it is marked **stuck** (logged once: `stuck: <id> (<reason>)`) and not
  retried, and a round that removes nothing genuinely-gone ends the sweep. This is
  per-id, layered on the existing per-collection `_CONVERGED` cache, and **never widens
  ownership** — selection still goes through `is_owned`/`is_expired`; stuck-tracking only
  suppresses a known-futile retry so the sweep CONVERGES instead of looping to its cap.

- **a transit-gateway "settling" in CREATING/EDITING (not yet ACTIVE/ERROR) was NOT
  counted in-progress on its own DELETE attempt — a gap distinct from the 2026-07-03
  DELETING-state fix above (CAMPAIGN-C3-100 repair-log #HB4b-2 item 5, 2026-07-07).**
  `DELETE /v1/transit-gateways/{id}` only succeeds while `state` is `ACTIVE` or `ERROR`
  (live error: *"Transit Gateway state is not deletable state(Active, Error)"*) —
  `CREATING`/`EDITING` are transitional (a TGW re-enters `EDITING` for a settle window
  after its OWN create, or after a vpc-connection create/delete on it — HB4b-2 item 2,
  measured >300s live). The reconciler's TGW pass already treated `DELETING`-class
  states specially (`_is_async_deleting`/`_ASYNC_DELETING_STATES`, the 2026-07-03 fix
  above) but a 400 from a `CREATING`/`EDITING` TGW fell through to a bare `print()` with
  NO `_INPROGRESS_THIS_ROUND` increment — so a sweep whose only remaining owned item
  was a transiently-`EDITING` TGW (its vpc-connection already reaped, so
  `_vpc_409_holder` no longer protected the VPC either) could report genuine=0/inprog=0
  and **converge one round before the TGW would have settled**, stranding it (and its
  VPC) for a human FORCE re-sweep hours later (exactly the 2026-07-06 HB4b incident,
  run 28827996068 — a manual re-sweep succeeded cleanly once the TGW had settled on its
  own). Fix: `_is_tgw_settling(item)` (allow-list `{"active","error"}`, deliberately
  excluding the already-handled `_ASYNC_DELETING_STATES`) — when true, the TGW pass
  SKIPS the doomed DELETE and counts it in-progress instead, mirroring the precedent
  `_is_async_deleting` already set for the DELETING state. Offline-tested:
  `tests/offline/test_reconciler_convergence.py::test_is_tgw_settling_predicate` +
  `::test_editing_tgw_delete_skipped_and_counts_in_progress`.

## DBaaS sub-op depth: window-only guarded lifecycles, ExistInprogress pacing, chat-heavy evidence sink (2026-07-02, branch upbeat-ritchie)

> conf: 0.8 · seen: 2026-07-02 · obs: 2 (runs 28595785223 + 28599889165)

- **`*-cluster-subops-guarded` bank NOTHING dispatched alone.** They soft-capture
  an EXISTING cluster from `GET /v1/clusters` (live-cluster-window design, per
  their `_note`); with no cluster up they "pass" in <90s recording only
  reachability 4xx/5xx (run 28595785223: 5 guarded lifecycles, 0 real depth).
  For standalone depth use the **self-sufficient `*-cluster-subops-full`**
  variants (`database__subops-full.json`): dedicated create→wait→subops→delete
  reusing the live-proven heavy-shared-dbaas / database-mysql-cluster blocks.
  Replica/restore groups are EXCLUDED there (a successful create has no
  capture/cleanup → untracked billable cluster); they belong to
  `gen-heavy-*-replica/-restore`.
- **DBaaS serializes cluster ops → 400 `Dbaas.ValidationError.ExistInprogress`.**
  Back-to-back mutating sub-ops on a live cluster 400 with ExistInprogress (run
  28599889165: ALL archive/log-export/patch/kernel ops). This SUPERSEDES the
  earlier "sub-op 500s need a live-cluster window" reading for these ops: with a
  live cluster the 500 becomes a 400 pacing error — i.e. NOT product-blocked.
  Fix = the proven database-mysql-cluster pattern: an optional
  `wait-after-<op>` poll (`$.service_state` until RUNNING/ACTIVE/AVAILABLE;
  accept STOPPED after stop-cluster) after EVERY mutating sub-op.
- **Small DBaaS cluster create→RUNNING ≈ 10 min** (mysql/mariadb/epas created
  15:04 → sub-ops at 15:15, run 28599889165); a 4-cluster parallel
  create+subops+delete CRUD step took 17.7 min wall. Cheap enough to iterate.
- **Real 2xx yield of the first paced-less full run: +73 verified endpoints**
  (1250→1323 in `verified_endpoints.json`): per-engine create/show/delete
  cluster, list backup-histories / engine-version-properties / log-export-configs
  / parameter-values / replicas, show archive-config, set-maintenance,
  parameter set/sync, cachestore list/sync-commands.
- **chat-heavy evidence sink.** The lane's runner is ephemeral and the workflow
  had NO artifact upload → run 28595785223's observations are permanently lost
  (fold impossible; do NOT trust "8 passed" as 2xx evidence). Now: (1)
  `actions/upload-artifact` + (2) an oplog-bucket mirror step
  (`runs/<APITEST_RUN_ID>/artifact/…`) run after every chat-heavy run — chat
  sessions CANNOT download GitHub artifacts (session proxy blocks
  api.github.com; MCP has no download tool) but read the bucket directly.
  For older runs use the push-triggered `fetch-results.yml` bridge
  (`.github/fetch-results-request`, needs `permissions: actions: read`; install
  `requests` alongside boto3 — `core.oplog` imports `core.http_client`).
- **backup-agent/backup-job** need a LIVE VM (`server_uuid` in the create body
  is a stale hardcoded id → 404 `Backup.NotFoundVirtualServerForSearchError`);
  bank them during a future VM-window (compute-virtualserver-full) run.
- **Next-batch prep (2026-07-02 offline, COVERAGE-PREP).** ROI over
  `verified_endpoints.json` (1400 keys → 558 distinct verified catalog ops):
  `postgresql-cluster-subops-guarded` had the largest reachable gap (33/35 ops
  unverified) → built **`postgresql-cluster-subops-full`** (database__subops-full.json,
  67 steps, targets **29** unverified pg ops incl. create-path
  listparametervalues/listlogexportconfigs/deletecluster; replica/restore
  excluded as leak-unsafe). pg create is PROVEN (2xx in verified set,
  database-postgresql-cluster) but FLAKY+SLOW: 500 ContactAdmin 2026-06-19
  (known_issues) + 1 fail 2026-06-29, ~40min — dispatch pg ALONE so it never
  gates other engines. `epas-cluster-subops-full` retry targets **23** ops
  (P3 create 500 = transient; P2 body proven, run 28599889165). **Blocked from
  the -full pattern (create body has NO live 2xx anywhere — do not invent):**
  eventstreams (13 ops unverified), searchengine (18), vertica (15) — their
  guarded lifecycles stay window-only until a proven create exists; sqlserver
  (29) stays license-gated reachability-only. pg archive/log-export/kernel
  sub-ops are baselined 500 ContactAdmin (2026-06-18) — the paced full run
  tests whether the ExistInprogress-supersession seen on mysql/mariadb/epas
  also holds for pg (if not, expect ~7 of the 29 to stay 500-blocked).
- **M4 worker executor E2E-verified in-process (2026-07-02, read-only; conf HIGH,
  obs run `local-1783030980`).** `PLATFORM_EXECUTOR=worker` + `/runs/trigger`
  (suite=smoke, service=quota) queues a `dispatched`/`gh_run_id NULL` record;
  `python -m runner.worker --once` claims it (`local-<ts>`), runs
  validate → smoke (47 live GETs passed) → dashboard → `core.snapshot upload`
  (64 files → `s3://apitest-oplog-permanent/runs/local-…/snapshot/`) → finalize,
  writes milestones directly to the platform DB, and finishes `done`. Two
  gotchas verified: (1) worker `build_env` explicitly forces
  `SCP_ALLOW_MUTATIONS/DESTRUCTIVE/RUN_HEAVY` from the suite gates, so a host
  `.env` that arms the gates CANNOT leak into a read-only worker run; (2) the
  remaining unverified M4 surface is Docker only (image build, compose up,
  mutation run via worker) — needs a real host, not this sandbox.
- **Offline-test hermeticity trap: `os.environ.pop` is undone by `.env`
  (2026-07-02, conf HIGH).** `core.config._load_dotenv()` runs at first `core`
  import and `setdefault()`s every `.env` value back into `os.environ`. A test
  harness that pops `SCP_ALLOW_DESTRUCTIVE` BEFORE importing app code gets it
  re-armed (and `_bool` defaults that gate to **True**, so even `""` flips it
  on — empty counts as unset). Hermetic pattern: SET the gate to the explicit
  string `"false"` (existing env always wins over `.env`). Applied in
  `controlplane/tests_offline.py`.

## Cleanup-map gaps: CDN / IAM-policy / launch-config / server-group leaks + CDN disable-before-delete quirks (2026-07-02, branch upbeat-ritchie)

> conf: 0.9 · seen: 2026-07-02 · obs: full-inventory sweep (225 param-less list-GETs
> across all 59 catalog services) + live teardown of every found leak

- **Why the regular sweeps missed them:** the reconciler walks a FIXED collection
  map; anything not in the map never gets listed. The 2026-07-02 full inventory
  (every catalog GET without path params, `retry=False, timeout=15`) found 19
  owned-flagged items; the genuine leaks were all either (a) collections absent
  from the map — `/v1/cdns` (**7 ACTIVE `regr{ualpha}` CDN distributions**, oldest
  2026-06-20), `/v1/launch-configurations` (`regrlc371da604`),
  `/v1/server-groups` (4 `regrsgrp*`) — or (b) name-prefix lists too narrow for
  the lifecycle's actual template: keypair `regrlckp*` (launch-config lifecycle)
  missed by `("regrkey",)`; IAM policies `regrgrpbpol*` (group-binding test)
  missed by `("regrpol",)`. Map + prefixes extended in `cleanup/reconciler.py`
  (launch-configurations before keypairs — an LC pins a platform-derived
  `regrlckp{run}-{lc_id}` keypair; policy prefixes now
  `regrpol/regrgrpbpol/regrrolepol`; keypair prefixes
  `regrkey/regrlckp/regraskp`).
- **CDN disable-before-delete state machine (all live-proven on the 7 leaks):**
  - `DELETE /v1/cdns/{id}` on an **ACTIVE or STOPPING** distribution returns
    **404 ResourceNotFound** ("Not found with ID …") even though GET/PUT/stop on
    the SAME id work — a **masked state error**. A CDN DELETE 404 must NEVER be
    trusted as "already gone" (the generic `_is_2xx_or_gone` rule would silently
    leak it forever); verify with GET or gate on state instead.
  - `POST /v1/cdns/{id}/stop` (body-less) → 202; `cdn_service_state` sits in
    STOPPING **~10-15 min** before STOPPED.
  - DELETE on STOPPED while `cdn_service_activation_state` is still
    PENDING_DEACTIVATION → **400
    `scp-network.cdn.service.property-invalid-state-delete`** (transitional —
    do NOT stuck-mark it); once deactivation settles (a few more min) DELETE →
    202. The reconciler's cdn pass is therefore a state machine: ACTIVE → issue
    stop and defer; STOPPING → skip; else attempt DELETE counting ONLY 2xx.
- **IAM policy detail body has `policy_name`, not `name`** (list items too) —
  name checks against `item["name"]` silently skip policies.
  `GET /v1/policies/{id}/bindings` → `{count, groups, roles, users, …}`; both
  leaked `regrgrpbpol*` policies had 0 bindings and deleted with a plain 204.
- **Deliberately left in the account** (known, documented): the 2 deadlocked SCF
  functions `regrw5trg57f68be7`/`regrw5trgd7ff680d` (auto-expire 2026-07-31),
  the IAM-gated SKE log-group `47fabeca13f24958a0344a00011a274d`, and
  `regrsec1846e085` already "To be terminated" (secrets self-purge).

## docs→VALIDATED promotion mechanics: service-scoped evidence join + gate markers (2026-07-03, branch upbeat-ritchie)

> conf: 0.9 · seen: 2026-07-03 · obs: 1

- **The promoter is now a tool** — `python -m tools.promote_validated` (dry-run;
  `--apply` rewrites). Rule (IB-041 consumer): a resource-model docs node may
  become VALIDATED **only if its CREATE endpoint has a real-2xx entry in
  `data/baselines/verified_endpoints.json`** (a GET-create lookup node counts if
  that GET has 2xx). Applied 2026-07-03: **18 promotions** (mariadb stop/start/
  restart/kernel-upgrade/add-block-storage · pg parameter/kernel-upgrade/
  add-block-storage · epas instance-group/parameter · mysql-kernel-upgrade ·
  gslb · cdn · account-budget · volume-type · gpu-node-image ·
  cm-account-resource · cm-addrbook) → model 131→149 VALIDATED.
- **Join mechanics (load-bearing).** Verified keys are `category/service/op`
  carrying `{method, norm_path}` (norm_path = query-stripped, leading-slash-
  stripped, id segments→`*`, e.g. `v1/clusters/*/stop`); node create endpoints
  are `"METHOD /path"` with `{ref.field}` placeholders, normalised by the SAME
  `tools.derive_verified.norm_path`. **The join MUST be service-scoped** (node
  `service` tail == verified key middle segment): `/v1/clusters` collides across
  mysql/pg/epas/cachestore/sqlserver/vertica/searchengine/eventstreams/ske — an
  unscoped (method, norm_path) match wrongly promotes sqlserver/vertica off
  cachestore evidence. Regression-tested in
  `tests/offline/test_promote_validated.py` (cachestore-vs-sqlserver collision).
- **Apply is a targeted line edit, not a YAML redump** — the model files carry
  hand-written comments; the tool flips only the `provenance: docs` line,
  appends `# evidence: <verified key> (run <last_run>)`, then reparses and
  diffs against the expected one-field change (reverts on any drift).
- **Account-gate marker `gated: <reason>`** (validate.py `GATED_VALUES`:
  license · entitlement-403 · console-only · org-master · credential; convention
  in `knowledge/formal/FORMAT.md`). 34 docs nodes marked 2026-07-03 (sqlserver
  ×16 + searchengine + vertica = license; archivestorage ×2 = entitlement-403;
  cloudcontrol-landing-zone + organization ×4 + idc-account-assignment =
  org-master; cloud-ml ×2, sts-token, scr-image/scr-tag, certificate-import,
  iam-user, diagnosis = credential). `gated` never changes provenance; the
  Modeling worklist separates 게이트(할 수 없음) from the actionable docs queue.
- **`no_api: true` is UI-complete now** — controlplane `_map_meta` counts an
  API-less node (scr-image/scr-tag, docker-push-born) as complete when its refs
  resolve; the "생성 endpoint 없음" incomplete bucket went 2→0 truthfully (the
  validator always allowed no_api; only the UI check was dishonest).

## Transit-gateway teardown & private-nat pacing — the light-batch-2 leak (2026-07-04, branch upbeat-ritchie)

> conf: 0.9 · seen: 2026-07-04 · obs: 2 (CI run 28648339307 + console2 FORCE cleanup log + live re-observation 2026-07-04)

- **(a) TGW deletion is ASYNC-SLOW and 409-blocks the VPC delete the whole
  time.** State enum `(CREATING, ACTIVE, DELETING, DELETED, ERROR, EDITING)`
  (api_docs `transitgateway`); a 202-accepted DELETE leaves the TGW listing as
  DELETING for minutes+, and while its vpc-connection exists the connected VPC's
  DELETE → 409. The reconciler's genuinely-removed convergence rule (correct
  for KMS/Secrets PF-09 scheduled deletion) misread this as "no progress" and
  STOPPED mid-drain (console2 FORCE log: round 2 "no genuinely-removed resource
  this round (reported=1); converged — stopping" with the TGW still listed) →
  `regrtgw*` + connection + shared `regrvpcsh6a47724b` leaked ~1 day. Fix
  (cleanup/reconciler.py): transitional deleting states (`_ASYNC_DELETING_STATES`)
  count as **in-progress → grant another bounded round** (`_round_verdict`),
  never converge-cache such a collection; PF-09 scheduled states still converge.
  A VPC 409 with a detectable holder burns ONE attempt + a "blocked by <holder>"
  line (was 6 identical 409s/round). NOTE the `reported=1` in that log was a
  truthy-409 counted as a deletion by the old bare `if _delete(...)` TGW pass.
- **(b) Connection enumeration is SPLIT: flat list 403, nested list 200.**
  `GET /v1/transit-gateway-vpc-connections` (flat) → **403 Forbidden** for this
  account (live 2026-07-04, req-97fdee97…) — a product finding: the catalog only
  documents the NESTED `GET /v1/transit-gateways/{id}/vpc-connections`, which
  works (200). So the sweep CAN enumerate connections per owned TGW — and MUST:
  **TGW delete does NOT reliably cascade its connection.** Live 2026-07-04 (the
  owner believed the account clean): TGW `regrtgwhdljjdbg` still present in
  EDITING, its connection `d7544cf6…` stuck DELETING since 02:36:59Z, still
  pinning VPC `regrvpcsh6a47724b` (ACTIVE). The reconciler now deletes an owned
  TGW's connections (nested list) BEFORE the TGW.
- **(c) private-nat create requires TGW state == ACTIVE** — live-proven 400
  `scp-network.private-nat.active-transit-gateway-not-found` ("Cannot found the
  Transit Gateway(…) in ACTIVE state", run 28648339307). Distinct from the older
  `connectable-transit-gateway-not-found` (no ACTIVE connection at all, run
  27466988779). **The TGW re-enters EDITING while a vpc-connection attaches/
  detaches** (live-observed), so "connection ACTIVE" alone is not enough —
  re-poll the TGW show until ACTIVE before create-private-nat. Pacing family,
  same class as DBaaS ExistInprogress (2026-07-02 block). Also: the engine's
  poll TIMEOUT is SILENT when the final HTTP status is in expect_status — the
  old 300s connection wait timed out quietly and the chain marched into the 400;
  the incident lifecycle failed mid-chain and the engine's one-shot teardown
  (single DELETE per cleanup, no retry/wait — engine.py `_run_cleanup`) couldn't
  clear connection→TGW in order, which is what leaked the chain. Fixes:
  `generated__light-batch2.json` gen-private-nat (600s waits + terminal-bad
  `until` + `give_up_status` + new `wait-transit-gateway-active`) and the model
  (`networking__vpc.yaml` transit-gateway/tgw-vpc-connection — two-stage
  `ready:` list, composer now accepts a ready LIST and passes `give_up_status`
  through, so a recompose keeps the fix).

## docs-research — 7 body-unknown endpoints resolved from `data/api_docs.json` (2026-07-04, branch upbeat-ritchie, read-only) — 미검증-docs유래 unless noted

> conf: 0.5 (schema) / 0.3 (value domains still open) · seen: 2026-07-04 · obs: 1
> Full writeup + draft bodies: `docs/working/plans/CAMPAIGN-C3-100-docs-research.md`.
> Source for all of these: `data/api_docs.json` `endpoints[*].request_example` +
> `models[*].fields` — the SCP API Reference "Example HTTP request" block, scraped
> verbatim by `spec.scrape_docs`. **None of the bodies below have a real 2xx** unless
> explicitly marked VALIDATED; provenance stays **docs** until one lands.

- **networking/vpn createvpntunnel/setvpntunnel — official example values differ from
  the current lifecycle.** `models["networking/vpn/vpnphase1createrequestv1dot1"]` /
  `vpnphase2createrequestv1dot1` (docs): `phase1_diffie_hellman_groups`/
  `phase2_diffie_hellman_groups` are `array[integer]` (not a formal enum) — official
  doc example `[30, 31, 32]`; `phase1_encryptions` example `["des-md5",
  "chacha20poly1305-prfsha256"]`, `phase2_encryptions` example `["null-md5",
  "aes128gcm", "chacha20poly1305"]`. The ONE true enum is `perfect_forward_secrecy:
  (ENABLE, DISABLE)`. The CURRENT `regression/scenarios/lifecycles/networking__vpn.json`
  body instead sends `phase1_diffie_hellman_groups:[14]` +
  `phase1_encryptions:["aes256-sha256"]` — self-admitted "docs-example guesses" that do
  NOT actually match the official doc example. Recommend swapping to the values above
  on the next VPN live attempt (HB4). provenance: docs.
- **compute/virtualserver importimage — WRONG FIELD NAME in the current coverage
  lifecycle.** `models["compute/virtualserver/imageimportrequest"]` (docs, official):
  `ImageImportRequest` has exactly ONE field, `url` (required, pattern `.*\.qcow2$`).
  `regression/scenarios/lifecycles/compute__virtualserver.json` `import-image` step
  sends `{"source": "regression-coverage-probe"}` — `source` is not a real field;
  this is a guaranteed schema-level 400 until swapped to `{"url": "<qcow2 URL>"}`.
  provenance: docs.
- **compute/virtualserver createimage — body schema LIVE-CONFIRMED past validation
  (2026-06-18, `vs-image-write-coverage` lifecycle, still short of a real 2xx).**
  `{name, os_distro, disk_format, container_format, min_disk, min_ram, visibility,
  url, tags}` (matches `models["compute/virtualserver/imagecreaterequest"]` verbatim,
  `os_distro` enum `alma|centos|rhel|rocky|ubuntu|windows|oracle`) sent live got
  `Image.InvalidObjectStorageUrl` (a resource-lookup error), NOT a `ValidationError`
  — i.e. the body shape itself is proven to pass validation; only a real uploaded
  `.qcow2` Object Storage URL (heavy/billable, out of scope) stands between this and
  a 2xx. provenance: docs schema + live partial (schema-pass only, not full 2xx).
- **storage/backup createbackup FILESYSTEM — full enum schema confirmed via docs,
  blocked on the Agent-backup prerequisite (owner waiver 2026-06-10).**
  `models["storage/backup/backupcreaterequest1dot2"]`: `policy_category` enum
  `(AGENTLESS, AGENT)`, `policy_type` enum `(VM_IMAGE, FILESYSTEM)`,
  `server_category` enum `(VIRTUAL_SERVER, GPU_SERVER, BAREMETAL_SERVER)`,
  `retention_period` enum `(WEEK_2, MONTH_1, MONTH_3, MONTH_6, YEAR_1)`. FILESYSTEM
  body = `{policy_category: AGENT, policy_type: FILESYSTEM, server_category,
  server_uuid, server_guid, is_all_filesystem, filesystem_paths, schedules,
  retention_period, region, tags}` (draft in the plan doc). Reaching a live 2xx
  needs a server with a Backup Agent installed FIRST (`storage/backup` service
  yaml `server-prereq`, docs) — the 8 agent-family ops are already owner-waived
  ("agent 없는 백업으로만", 2026-06-10), so this stays practically unreachable
  until that waiver is revisited, even though the create-body schema itself is
  fully known. `getbackuptargetlist` with `policy_type=FILESYSTEM` is already
  LIVE-VALIDATED 200 (known_issues.json, 2026-06-20) so the discovery path works;
  it just returns an empty list without an installed agent. provenance: docs
  (schema) / VALIDATED (getbackuptargetlist FILESYSTEM 200 only).
- **networking/dns activateprivatedns — body confirmed, single field.**
  `models["networking/dns/privatednsactivaterequest"]` (docs, official): body is
  just `{"name": "<private-dns name>"}` — activates an ALREADY-CREATED,
  account-global private-dns name in another region (matches
  `private-dns-account-global` quirk above). Same body already used by the
  disabled legacy `dns-activate` step in `regression/scenarios/scenarios.json`
  (disabled because a same-region create never needs it, not because the body was
  wrong). provenance: docs.
- **data-analytics/data-flow createdataflow/createdataflowserviceconsole — full
  official body recovered, already matches current lifecycle.** Docs
  `models["data-analytics/data-flow/dataflowbodycreate"]` /
  `dataflowservicecreaterequest` request_example gives the complete field set
  (`account`, `cluster_id`, `data_flow_name`, `domain`, `dsc_domain`,
  `host_alias_list`, `image_id`, `ingress_controller_name`, `instance_id`,
  `node_selector`, `storage_class_name`, `tags` for create-flow; `service_workload
  {nifi, nifi_registry, zookeeper}` with REAL non-empty example values — cpu 2000,
  memory 1024, nifi/nifi_registry replica 1, zookeeper replica 3, versions
  1.27.1/3.9.2 — for create-service). The current
  `regression/scenarios/lifecycles/data-analytics__data-flow.json` bodies already
  match this schema field-for-field (optional fields account/dsc_domain/
  instance_id/node_selector omitted, harmlessly). Body-shape "unknown" is resolved;
  remaining unknown is whether the doc's example VALUES clear a live create (heavy,
  HB7). provenance: docs.
- **data-analytics/data-ops createdataopsservice service_workload — schema
  confirmed, value DOMAIN still genuinely undocumented (matches the 2026-06-24 live
  400s).** `models["data-analytics/data-ops/dataopsservicecreaterequest"]`:
  `service_workload` is typed as a bare `object` with sub-fields `cpu`/`memory`/
  `replica`/`version` all typed `string` with NO enum/pattern/example (docs leaves
  them blank, unlike data-flow's equivalent) — so the doc scrape cannot supply
  valid values, only the shape (`{scheduler, web_server, worker}` each
  `{cpu, memory, replica, version}`), which already matches what the guarded
  lifecycle sends. `worker_type` is also a bare `string` (not a formal enum) —
  the userguide's `Kubernetes|Celery` wording (`data-analytics__data-ops.yaml`
  `worker-executor-choice`, docs) does not confirm the exact API token
  (`KubernetesExecutor` is a guess). New lead for next live attempt:
  `GET /v1/data-ops/image-versions` (`getdataopsimageversionv1`, read-only) returns
  `contents[].version` — a real discoverable Airflow version string instead of the
  hardcoded guess `"2.7.3"`; not yet tried. Not a waiver candidate — recommend one
  more live attempt (HB7) seeded from this GET before giving up. provenance: docs
  (schema only, value domain open).
- **data-analytics/eventstreams createcluster — schema 100% confirmed; topology
  is a carried-forward HYPOTHESIS (commit `700f72a0`, "ZK quorum" fix), still
  NEVER live-retested since that fix landed (2026-06-19).** `instance_groups[].
  role_type` enum confirmed `{ZOOKEEPER_BROKER, BROKER, ZOOKEEPER, AKHQ, CONSOLE}`
  (docs, `EventStreamsClusterCreateRequestV1Dot1` — the doc's own request_example
  wrongly reuses `role_type: ACTIVE`, a different engine's value). The
  `data/api_bodies.json` entry for this key was hand-edited in commit `700f72a0`
  (2026-06-19, "eventstreams ZK quorum") to 3× combined `ZOOKEEPER_BROKER`
  instances + `is_combined: true` + `server_type_name: db1v2m4` — a reasoned guess,
  not a docs scrape. `knowledge/formal/resources/data-analytics__eventstreams.yaml`
  (commit `ada47e7d`, 2026-06-12, i.e. BEFORE the ZK-quorum fix) still records the
  create as "KNOWN-BLOCKED: undocumented topology value_error" — that failure
  predates this fix and has never been re-tried. A 2026-07-04 SCP userguide summary
  fetch (`.../userguide/analytics/event_streams/overview/`, page render truncated
  the exact quote) suggests combined Zookeeper+Broker deployment is a real
  documented mode ("3 or more is typical") — directionally consistent with the
  ZK-quorum guess but not a confirmation. Recommend this body be the FIRST thing
  tried in the next eventstreams live slot (HB2) before any further guessing.
  provenance: docs (schema) / UNPROVEN (topology hypothesis, untested since fix).

## HB1 deterministic-repair pass — mariadb/mysql/epas/cachestore/postgresql subops-full + eventstreams-full authored (2026-07-04, branch upbeat-ritchie, OFFLINE — no live calls this session)

> conf: 0.5 (docs유래, all UNVERIFIED LIVE this session — see `docs/working/plans/CAMPAIGN-C3-100-repair-log.md`) · seen: 2026-07-04 · obs: 0 (repairs; HB1 obs are the FAILURE evidence that motivated them)

HB1 (run 2026-07-04) re-hit the SAME 10 gap keys with the SAME signature as prior
runs — proof that re-running unmodified bodies is pointless; each needed a real
body/capture/pacing fix. Applied to `regression/scenarios/lifecycles/
database__subops-full.json` (mariadb/mysql/epas/cachestore/postgresql -full
variants) — all **UNVERIFIED LIVE**, apply + observe in HB1b/HB2b:

- **DBaaS log-export `log_type` — "general" is wrong, "alert" is the
  docs-evidenced value (docs유래-미검증).** HB1: `register-log-export-config`
  400'd `Dbaas.InvalidLogType` sending `"general"`. `data/api_docs.json`'s
  `response_example` for `list-log-export-configs` is IDENTICAL across
  mariadb/mysql/postgresql/epas: `{"log_type":"alert","log_label":"DB Alert
  Log",...}` — `"alert"` is the only value appearing in BOTH the request AND
  response doc examples for every engine (the request doc example itself says
  "Log type Example: alert", not "general"). Changed the 4 lifecycles'
  `register-log-export-config` body to `"alert"`, and added a NEW
  `capture-log-type-after-register` step (GET log-export-configs, run AFTER
  register) so the downstream `set/export/delete-log-export-config` steps'
  `{log_type}` path resolves from what was ACTUALLY registered — the OLD
  `capture-log-type` step ran before anything existed and always found an empty
  list (dead capture). cachestore has no log-export subresource (uses
  `/commands` instead, per the 2026-06-20 fact below) so it's untouched here.
- **`patch-minor-version` `software_version` — no enum, but the cluster's OWN
  current value is capturable and at least format-valid (docs유래-미검증).**
  HB1: 400 `ValidationError "Software version is not MARIA_DB"` sending `""`.
  `MinorPatchRequest.software_version` (mariadb/mysql/epas/postgresql) has NO
  enum/example in `api_docs` — but `GET /v1/clusters/{cluster_id}`
  (`ClusterDetailResponse`) exposes the cluster's own `software_version` field
  directly. Added a `capture_soft` on `capture-subop-ids` and wired it into
  `patch-minor-version`'s body. Whether patching to the cluster's OWN current
  version is accepted (no-op upgrade) or rejected ("already at version") is
  unknown — there is no discovery endpoint for the actual list of
  upgrade-eligible target versions. cachestore's patch model is DIFFERENT
  (`MinorPatchDbEngineRequest {dbaas_engine, software_version}`, its own
  `dbaas_engine` value also undocumented) — left untouched, out of scope.
- **`resize-instance-group` same-server-type 400 is BY DESIGN — always capture a
  SECOND, different server type (docs유래-미검증).** HB1: 400
  `Dbaas.ValidationError "The server type is invalid"` on mariadb; the same body
  shape (empty string, or the SAME type used at create) exists in epas/
  cachestore/postgresql too.
  `knowledge/formal/resources/database__mariadb.yaml`'s existing
  `mariadb-resize-instance-group` note already predicted this ("same-type 400 is
  intentional hard [reject]"). Fixed by capturing `$.contents[1].name` from each
  engine's `GET /v1/server-types` list (a genuinely different entry than
  index[0], which create already consumed) and feeding that into
  `resize-instance-group`'s body instead of an empty literal or the create-time
  type. Applied to mariadb/epas/cachestore/postgresql/eventstreams (mysql has no
  `resize-instance-group` step in this file).
- **`resize-block-storage`/`set-block-storage-size` `ExistInprogress` — needs a
  settle-poll between `resize-instance-group` → `add-block-storages` →
  `resize-block-storage` (docs유래-미검증, pacing not schema).** HB1: 400
  `Dbaas.ValidationError.ExistInprogress "There is a request in progress"` —
  the 3 resize ops in the `resize` group fired back-to-back with NO
  `wait-after-<op>` between them (every OTHER subop group in this file already
  has this pattern; `resize` was the one group missing it). Added
  `wait-after-resize-instance-group` + `wait-after-add-block-storages` (mariadb/
  epas/postgresql; cachestore only needed the first — it has no
  `add-block-storages` step, provisioning OS+DATA at create time instead) —
  same poll shape as every other group: until
  RUNNING/ACTIVE/AVAILABLE/FAILED/ERROR/UNKNOWN, `give_up_status:[400,404]`,
  timeout 900s.
- **`showrequest` (`GET /v1/requests/{request_id}`) needs `request_id`
  capture_soft on the cluster-create step — mariadb/epas/cachestore were
  MISSING it (mysql/postgresql already had it, added 2026-06-11).** HB1:
  mariadb-full's `show-request`-equivalent (the auto `probe_reads` GET) 400'd
  because `ctx["request_id"]` was never populated for this engine.
  `AsyncResponse {request_id, resource:{id}}` (api_docs, verbatim across all 5
  DBaaS engines) is returned by every cluster-create — added
  `capture_soft: {request_id: "$.request_id"}` to mariadb's `maria-create`,
  epas's `epas-create`, and cachestore's `cache-create` (mirroring mysql/pg's
  existing pattern in the SAME file).
- **`remove-backup-histories` 401 `Dbaas.Unauthorized.AuthNFailed` (valid HMAC) —
  CONFIRMED backend auth quirk family, re-observed HB1, still not fixable
  client-side (VALIDATED, this is a re-confirmation not a new finding).** Same
  quirk already documented for mysql/mariadb/postgresql/epas/cachestore
  (`knowledge/formal/services/database__*.yaml`, this file's 2026-06-10 entry
  above) — HB1 (2026-07-04) reproduces it on mariadb with a request signed
  identically to every sibling call that DID pass in the same run. No doc/
  header/version difference found for this endpoint. NOT fixing the call
  itself; widened `remove-backup-histories`/`delete-backup`'s `expect_status`
  to include 401(+500) so the KNOWN 401 no longer group-skips the sibling
  `delete-backup` step out of the run — `database__sqlserver.json` already
  carries this exact tolerance, this aligns the -full DB variants with that
  precedent. PF/waiver candidate, not re-attempted as a "fix".
- **mysql `remove-backup-histories` field-name bug found while repairing the
  401 above: body key was `backup_history_ids`, should be
  `backup_history_number` (docs유래, mechanical fix — 100% confidence, just
  never live-tested since mysql-full's create-cluster PFs 500 before reaching
  this step).** `data/api_docs.json`
  `models["database/mysql/backuphistorynumberrequest"]` verbatim field name is
  `backup_history_number` (identical DTO name/shape to mariadb/postgresql/epas/
  cachestore) — mysql's -full variant was the only one with the wrong key.
- **mysql `create-cluster` 500 `ContactAdminForAssistance` — PF, no fix
  possible (re-confirmed, same class as postgresql's known create-500).** Not a
  body problem; recorded as-is for the waiver/PF track.
- **`eventstreams-cluster-subops-full` authored** (new lifecycle,
  `regression/scenarios/lifecycles/data-analytics__eventstreams.json`) — same
  shared-VPC-adopt → create → subops(+settle-poll per op) → delete pattern as
  the DB -full variants, create body per the `docs-research` ZK-quorum
  hypothesis above (topology still UNVERIFIED — this session did not
  live-test it, only authored the scaffold + wired live discovery for
  `dbaas_engine_version_id`/`subnet_id`/`server_type_name`). Full detail:
  `docs/working/plans/CAMPAIGN-C3-100-repair-log.md`.

## VS 서버 SHOW 응답은 래퍼가 없다 (2026-07-08 실측 — 대기 폴 침묵 소진의 뿌리)

- `GET /v1/servers/{id}` (showvirtualserver) 응답은 **top-level 평면 구조**다:
  `state`/`name`/`volumes`… 가 바로 최상위. `{"server": {...}}` 래퍼 없음
  (req 실측 2026-07-08, server 4f0e994c). 반면 list/create 는 `servers[]` 래핑.
- 그래서 대기 폴의 `field: "$.server.state"` 는 영원히 None → **모든 서버 대기
  (active/stopped/resized/restarted/rebuilt/settled, 13곳)가 타임아웃을 전부
  태우고 200 pass-through** 로 통과해 왔다. E2E 20260708-010208 역산: stopped
  +313s(≈300s 소진) · resized +621s(≈600s) · restarted +322s · rebuilt +630s.
  "Windows 부팅 10.2분" 관측도 사실은 600s 소진 + 오버헤드였다.
- 교정: 13곳 전부 `field: "$.state"`. duration_stats 의 기존 실측치(예: VS
  makespan ~52분)는 이 소진으로 부풀려진 값 — 교정 후 런들이 다시 접히며
  자동 재학습된다. 타임아웃 상향(600→1200s)은 헤드룸으로 유지.

## SKE 버전 목록은 최신-우선, 업그레이드는 실행 흐름으로 (2026-07-08 실측·편입)

- `GET /v1/kubernetes-versions` 는 **내림차순(최신 먼저)**: 실측 [0]=v1.34.3,
  [1]=v1.33.5, … 총 6개 (`end_dt` 동반). heavy 라이프사이클은 **[1]로 생성해
  [0]으로 실업그레이드** 한다 (owner 2026-06-13 승인 "구버전 생성→업그레이드→삭제"
  흐름을 2026-07-08 실제 스텝으로 편입; placeholder 프로브 2종 은퇴).
- nodepool 업그레이드 body 는 `{os_version}` (NodepoolUpgradeSetRequestV1Dot4) —
  `kubernetes_version` 이 **아니다** (노드는 컨트롤플레인을 따라감; 은퇴한 프로브가
  kubernetes_version 을 보내던 것은 docs 모순으로 정정).
- SKE `GET /v1/images` 는 `scp_original_image_type=k8s` 가 **필수 쿼리** (누락 시
  400 'Field required'). heavy 의 list-images 가 `[200,400]+optional` 뒤에서
  **매 런 400 을 조용히 기록**해 온 masked-defect 를 2026-07-08 교정 (hard 200).
- kubeconfig 2종 GET 은 `kubeconfig_type` 필수 쿼리, enum **소문자** public/private.
  실 클러스터 대상 200 은 아직 미실측 — optional 자체그룹으로 편입, 첫 라이브가 정정.

## servicewatch log-stream 읽기는 계정 전역 IAM-gated (2026-07-08 실측 확장)

- `GET /v1/log-groups/{id}/log-streams` 가 **모든** log-group에서 403 — 기지
  케이스(47fabeca…, IAM-gated 삭제불가 log-group) 1건 한정이 아니라 이번 런의
  신규 log-group 4개(mysql/mariadb slowlog·alertlog)에서도 동일 재현. 계정에
  log-stream 계열 IAM 액션이 없다 (대사 에이전트 실측). log-group 삭제가
  child-stream 권한을 요구해 실패하는 기지 잔존의 뿌리와 같은 게이트.

## budget 이름은 20자 이하 — docs 미기재 (2026-07-08 실측)

> conf: 0.8 · seen: 2026-07-08 · obs: 1

- `POST /v1/budgets/account` (createaccountbudget)의 `name`은 **최대 20자**:
  25자 템플릿이 400 `"Name length cannot exceed 20."` (BudgetCreateRequest
  docs에 길이 제약 없음 — AI-usability gap). 기존 probe의 `regr-budget-{unique}`
  (정확히 20자)가 201을 받아온 이유이기도 하다. lifecycle 수리:
  `budget-account-budget-full`의 create/set name → `regrbud{unique}` (15자).

## filestorage replication 관리 op는 볼륨 purpose 전제 — DR 측에서만 (2026-07-08 재확인)

> conf: 0.8 · seen: 2026-07-08 · obs: 3

- `purpose`는 **서버 관리 상태 필드** (VolumeCreateRequest에 없음 — 생성 body로
  지정 불가): 복제 source 볼륨 = `original`, kr-east1 replica = `replication`
  (라이브 관찰 2026-07-08). source 측 set/delete replication은 400
  `"Check the volume purpose."` (= 2026-06-24 `Invalid.volume.purpose` 재확인)
  — 2xx는 **DR 리전 호스트**(`filestorage-dr` alias + replica volume_id)로만.
- 잔존 주의: replication 삭제 후에도 **replica 볼륨은 kr-east1에 남는다** —
  teardown은 delete-replication-dr 뒤 replica 볼륨 자체를 지워야 함
  (`delete-replica-volume-dr` 스텝, 2026-07-08 추가; replication 존속 중
  replica delete는 400).

## filestorage 교차리전 복제 정리 절차 — 오너 하사 (2026-07-09), 즉시 라이브 재현 성공

> conf: 0.9 · seen: 2026-07-09 · obs: 1 (owner-granted + 동일 세션 라이브 전 단계 2xx 실증)

**오너 하사 원문 (2026-07-09, 원문 그대로):** "파일스토리지는 다른 리전으로 복제를
한 경우 **해당(상대) 리전에서 복제 정책: 일시중지 → 삭제 로 두 번 변경**한 후
**snapshot 등이 정리**되어야 west/east **둘 다** 정리 가능"

teardown 순서 (상대=복제본 리전 kr-east1 호스트에서):
① 복제정책 **일시중지**: `PUT /v1/replications/{rid}?volume_id={replica_id}`
   body `{"replication_update_type":"policy","replication_policy":"paused"}`
   — enum은 docs ReplicationUpdateRequest 그대로 **`use|paused`** (PAUSE/SUSPEND
   아님) → **202 실증**, show가 `replication_policy/status` 둘 다 `paused`로 전이.
② 복제정책 **삭제**: `DELETE /v1/replications/{rid}?volume_id={replica_id}` →
   **202 실증**; 레코드가 양 리전 list에서 ~20s 내 소멸.
③ **snapshot 등 부속 정리**: 양 리전 `GET/DELETE /v1/snapshots/{sid}?volume_id=`
   (volume_id 쿼리 필수). 실측: 복제 가동 중 **양쪽 볼륨에 `snapmirror.*` 시스템
   스냅샷이 자동 누적** (east replica에 3개 — 이전 조회에서 0이었다가 복제 삭제
   후 노출; west에도 5분마다 재생성) — 전부 DELETE 202.
④ 그 후에야 **양 리전 볼륨 삭제 가능**: east replica DELETE 202→404, west source는
   ②직후 **purpose가 `original`→`none`으로 전이(라이브 실증)** 하고 DELETE 202→404.
   ①∼③ 없이 지우려 하면 400 (아래 소급 설명).

**소급 설명 — 과거 replica/volume delete 400의 뿌리:** (기존 관찰 2026-06-24
`filestorage.BadRequest.Invalid.volume.purpose` / 2026-07-08 "Check the volume
purpose.") = **pause 선행 누락 + source 측 호출**. set/delete는 상대(복제본)
리전 호스트 + replica volume_id로만 2xx; source 측은 purpose=original 인 동안
항상 400. "replication 존속 중 replica delete 400"도 ①②를 안 거친 상태의 같은
현상. 또한 **모든 replication op(list/show/set/delete)는 `?volume_id=` 필수
쿼리** — 이번 런 west의 bare `GET /v1/replications` 400도 이것(누락) 때문이며
docs listvolumereplications에 required로 명기돼 있다.

부속 실증 (2026-07-09 정리 런, 실측 레코드):
- list/show 레코드의 **replica id 필드명은 `replication_volume_id`**
  (`replication_volume_region`과 짝) — reconciler `_replica_id_of`의 종전 후보
  (replica_volume_id/destination_… 등)는 전부 불일치였고 이 필드를 1순위로 보강
  (2026-07-09). create 응답의 동명 필드($.replication_volume_id) 캡처와 일치.
- east 접근: `dataclasses.replace(settings, region="kr-east1")` + `core.ApiClient`
  (reconciler `_extra_region_clients` 패턴) — 동일 자격증명으로 전 호출 2xx.
  시나리오 스텝 레벨은 `filestorage-dr` service alias + `SCP_SERVICE_HOSTS` 로만
  가능; alias 미설정이면 템플릿이 `filestorage-dr.kr-west1...`(DNS 부재, 실측
  NXDOMAIN)을 만들어 **ConnectionError로 라이프사이클이 즉사**한다 (PUT/DELETE는
  전송예외 재시도 후 raise) — storage__filestorage.json의 그룹 동승 수리 참조.

## '관용 create→리터럴 폴' 클래스 — 엔진 차원 차단 (2026-07-09 오프라인, run-2 회고 백로그 완료)

> conf: 0.9 · seen: 2026-07-09 · obs: run-2 aimlops 30분 공회전(라이브) + 전수 정적 스캔

**클래스 정의:** 4xx를 관용하는 탐사용 create(또는 `capture_soft`)가 캡처를 못
남기면, 그 토큰을 경로/쿼리에 쓰는 후속 state-폴이 **리터럴 `{token}` 그대로**
400/404를 timeout 한도까지 재시도한다 (run-2 실측: gen-heavy-aimlops
`{release_id}` 폴 10회차/~30분). 리터럴 토큰은 절대 수렴 불가.

**근본수리 (engine.py `_run_step`):** poll 진입 직전, 치환 후에도 경로 또는
params에 `{`가 남아 있으면 **폴 루프를 통째로 스킵**하고 첫 응답을 그대로 반환
(give_up_status/expect_status 의미론은 불변). optional-retry 캡(`"{" not in
path`)과 같은 판정 기준. 회귀 테스트:
`tests/offline/test_poll_unresolved_placeholder.py` (경로 토큰 · 쿼리 토큰 ·
정상 경로 폴 보존 3건).

**전수 스캔 결과와 남긴 결정:** 관용-캡처 토큰을 폴하는 give_up 미보유
state-폴 20건 검출(DB subops-full 4 · gslb 4 · blockstorage/filestorage 5 ·
igw/nat/pls/fw/dns/lb 등). **일괄 give_up [400,404] 추가는 기각** — 비동기
202 create 직후 리소스가 일시적으로 404일 수 있어(가시성 지연) 정상
프로비저닝 대기를 오판 중단시킬 위험. 리터럴-토큰 서브케이스는 엔진 가드가
전부 덮고, "실ID인데 영영 안 나타남" 서브케이스는 batch-2 규약(모든 wait 폴
until에 FAILED/ERROR/UNKNOWN terminal-bad 포함)이 담당. give_up_status는
aimlops처럼 **탐사용(4xx-관용) create 뒤 폴에만 선별 적용**이 정본.

## 하드실패 teardown이 '조용히' 새는 3중 결함 — run-2b (2026-07-09) 잔존의 엔진 뿌리

> conf: 0.9 · seen: 2026-07-09 · obs: run-2b events.jsonl 전문 + 라이브 잔존 대조 + 코드 확인

run-2b(오너 콘솔, 90/96·fail 5)에서 실패 라이프사이클들만 resource-deleted 0
(lb-members 4/0 · private-nat 2/0 · vpc-endpoint 3/0)이던 뿌리는 데이터가 아니라
**engine._run_cleanup 3중 결함**:
① 응답 상태 미검사 — DELETE가 409/400이어도 예외만 아니면 oplog에 'deleted' 기록
  (매니페스트/스윕이 이미 지워진 것으로 오인),
② 재시도 없음 — 중도 실패 직후의 자원은 EDITING/DELETING이라 1회성 DELETE는
  구조적으로 실패 (LB·TGW 실측),
③ 콘솔 이벤트(_cev) 미발신 — cleanup 시도/실패가 화면에 전혀 안 보여 오너는
  "teardown 시도 완료" 후 잔존만 목격.
수리: 상태검사(404=already-gone은 성공) + 409/invalid-state 사다리
(SCP_CLEANUP_RETRIES=3 × SCP_CLEANUP_RETRY_INTERVAL=20s) + 실패 시
resource-delete-failed 이벤트/큰 로그. 회귀: tests/offline/test_cleanup_ladder.py.

동반 확인(설계 사실): **happy path에서 cleanup은 의도적으로 안 돈다** — 성공 경로의
삭제 정본은 명시 delete 스텝 (engine.py "deletes only on a later failure" 주석).
따라서 '성공했는데 잔존'은 전부 명시 delete 스텝 부재 = 데이터 갭이다. run-2b 실증:
kms sym/hmac 2키(메인 키만 delete 존재) · iam-group-bindings policy(binding만 해제) —
둘 다 명시 delete 추가로 수리. heavy-shared-networking private-dns는 스텝은 있었으나
그룹(dns) 실패로 스킵 → cleanup 폴백이 ①②③에 걸려 잔존 → 엔진 수리가 커버.

기타 run-2b 실측: TGW vpc-connection 생성이 600s+ CREATING (wait 1500s 재상향) ·
vpc-endpoint create 첫 2xx 착지(R2 수리 유효) 후 CREATING-delete 400 (ACTIVE 폴 신설) ·
LB member의 빈 object_id 필드가 그 자체로 403 InvalidVmInMember (필드 제거; docs상
optional) · filestorage-dr 별칭은 이제 core/config가 SCP_DR_REGION(기본 kr-east1)으로
기본 합성 — SCP_SERVICE_HOSTS 없이도 교차리전 스텝 동작.

## apigw resource-policy·iam role 500의 유력 뿌리 — 오너 하사 userguide 대조 (2026-07-09)

> conf: 0.7 (오프라인 대조 확정, 라이브 재검증 대기) · seen: 2026-07-09

오너가 짚어준 userguide 두 페이지로 run-2c의 500 두 클래스를 대조:
- **apigateway setresourcepolicy (PF-19)**: 우리가 보낸 `Resource` SRN이
  `srn:scp::apigateway:api/<id>` — **형식 자체가 불량** (세그먼트 수 부족).
  정식: `srn:{offering}::{accountId}:kr-west1::apigateway:api/{apiId}`
  (userguide resource_policy; offering=e). Principal 객체형은
  `{"scp": [ "srn:e::<acct>:::iam:user/<uid>" ]}`. → 라이프사이클 2곳
  (apigateway·wave5-appsvc) 정식 SRN으로 교정. 여전히 500이면 "불량 입력에
  400 아닌 500" PF로 남는다.
- **iam createrole (PF-20/PF-31)**: 우리 trust policy에 **Principal이 아예
  없었고** top-level principals=[]. userguide role 페이지는 principal 연결
  (계정/유저SRN/IdP/서비스, 최대 20)을 전제, json_guides는 Principal 필수 +
  형식 `{"scp": "srn:e::<account>:::scp-iam:user/<userId>"}` (예시 그대로,
  서비스명 **scp-iam**·리전 슬롯 빈칸) + Version "2024-07-01" 유효를 명시.
  → find-iam-user(계정 유저 목록에서 실유저 id 캡처) 선행 + Principal 구성.
- **LB member (run-2c 403 실측 2회)**: 직접 IP만으로 member 등록 불가 —
  `object_id`(실제 VM id) 필수 (`InvalidVmInMember object_id: 'None'`).
  구모델 "직접 입력 IP는 VM 불필요" 전제 반증. → find-member-vm(regrsrv*
  캡처)+show로 실 IP·id 주입 (owner 방향: "vm의 id를 잘 넣으면 될 것").

## 이 계정의 IAM 유저 목록은 비어 있다 (2026-07-09 run-377e 실측)

> conf: 0.85 · seen: 2026-07-09 · obs: 1 (GET /v1/accounts/ec11538a…/users → 200 {count:0, users:[]})

role trust policy의 Principal에 쓸 실유저가 없다 — iam-group-member /
iam-user-policy-binding이 user_id 부재로 blocked-owner였던 기지 사실의 뿌리와
동일. created_by 필드들에 보이는 90dddfc2…는 이 목록에 안 나오는 주체(콘솔
로그인/오너 계정 계층)로 보임. → iam-role-full은 'Current Account' principal
추정형(srn:e::<acct>:::scp-iam:root, 미검증)으로 전환; 400이 오면 그
ValidationError가 정형을 알려줄 것. apigw resource-policy는 같은 런에서
**정식 SRN 수리로 500→200 착지** (PF-19 = 우리 입력 형식 오류가 뿌리, 남는
결함은 '불량 입력에 400 아닌 500'뿐).

## Private NAT의 'Connectable' TGW = 물리 Uplink 회선 전제 — 이 계정 구조적 불가 (2026-07-10 확정)

> conf: 0.9 · seen: 2026-07-10 · obs: userguide 명문 + 3런 실증 (재배열·재시도 전부 무효)

userguide(vpc/private_nat): **"Transit Gateway는 Uplink 회선 연결 후 선택할 수
있음"** — private-nat가 요구하는 'Connectable state'는 상태 전이가 아니라
**물리 전용회선(Uplink)이 붙은 TGW**라는 전제조건. VPC connection만 있는
TGW는 ACTIVE까지만 가고 CONNECTED로 전이하지 않으며(43폴/883s 실측), create
400 재시도(937s)도 무의미. 이 계정엔 실회선이 없어 **구조적 불가** — 사다리
전부 제거하고 400 관용 도달-증거로 재분류 (waiver 후보). API 에러("Cannot
found ... in Connectable state")가 이 전제를 설명하지 않는 것은 에러-가이드
갭 (PF 후보로 볼 여지). 같은 이유로 Direct Connect 경유 private-nat도 실회선
없이는 동일 게이트로 추정.

## SCR 이미지 픽스처 (오너 수동 준비, 2026-07-10)

- 오너가 콘솔에서 레지스트리 `sample` (id `nayvugfp4154447ab0ab61279cba3d72`,
  public endpoint enabled) + 리포지토리 `test` (id `6c910ed5195842739f9c98a569982064`)
  를 만들어 둠. 목적: image/tags 계열 ~19개 API는 실제 이미지가 있어야 검증 가능.
- docker 경로: `sample-nayvugfp.scr.public.kr-west1.e.samsungsdscloud.com/test/<image>:<tag>`.
- **SCR docker 토큰 인증은 우리 서비스-계정 키를 거부한다** (2026-07-10 실측):
  `auth.scr.public...:/auth/token` (Bearer realm, service=nayvugfp)에
  SCP_ACCESS_KEY/SECRET Basic → 401 invalid credentials (HMAC 서명도 동일).
  scope 형식은 `repository:test/<image>:pull,push` (repo만 쓰면 400
  "you must specify image not repository only"). archivestorage 401
  ("Service Account catalog…")과 같은 축 — 레지스트리 인증은 콘솔 사용자
  인증키 필요 추정. 이미지 push는 오너 로컬 docker 또는 사용자 키 발급 후.
- 레시피 측은 준비 완료: scr-read/image-write/tags-write 시드가 sample/test를
  prefix-지정으로 잡고 `$.images[0].id`/`$.tagses[0].id` 실 캡처. delete류는
  의도적 미해결 토큰({image_delete_id}/{tags_delete_id})으로 픽스처 보호.

## IAM 트러스트 정책 v4 확정 — createrole 202 (run-90e2, 2026-07-10)

Principal `{"scp":["srn:e::<acct>:::scp-iam:root"]}` + `Resource:["*"]` 조합으로
POST /v1/roles **202 성공** (500→400→400→202 사다리 완주). get/set/set-trust-policy
200, delete 204. PF-20/31은 백엔드 버그가 아니라 **입력 정형** 문제로 최종 확정.
createrole/deleterole 검증 fold (+2 → 2,421).

## IAM 유저 라이프사이클 (오너 승인 2026-07-10 "만들어")

iam-user-full 신설 — M5 iam-user 노드의 owner-credential 게이트가 오너 승인으로
해제됨. 안전 설계: 권한 0 유저(그룹/정책 빈 배열) + temporary_password:true +
바인딩 테스트는 **Deny-전용 정책**만 연결 + 전 자원 run 내 삭제. account_id는
GET /v1/access-keys의 $.access_keys[0].account_id로 자가발견 (리터럴 경로는
카탈로그 키 미해석 — validate WARN으로 확인된 제약). 다음 런 판정 대상 9키:
createiamuser·getiamuser·updateiamuser·updateiamuserpassword·deleteiamuser·
addgroupmember·removegroupmember·adduserpolicybinding·removeuserpolicybinding.

## SCR public 엔드포인트 — 두 가지 미해결 결함 (2026-07-10, PF-37/38)

- **PF-37**: public 토큰 서버(auth.scr.public...)가 문서대로의 유효 인증키
  (AccessKey/SecretKey Basic)를 일관 401 거부. 키 유효성은 HMAC Open API 200으로
  증명, scr:LoginContainerRegistry 권한 보유(ScrPullPushOnlyAccess 기본 포함 —
  콘솔 JSON 실측: scr:LoginContainerRegistry/PullRepositoryImages/PushRepositoryImages),
  owner 구키 로컬 docker login도 동일 실패. → **이미지 push 픽스처는 SDS 문의
  해소 전까지 보류**. SCR 커버리지 배선(sample/test 캡처)은 이미지가 들어오는
  즉시 동작 (무퇴행).
- **PF-38**: enable-public-endpoint 토글 후 API 상태는 Running/enabled인데
  실제 엔드포인트 TCP reset 2시간+ (컨트롤/데이터플레인 불일치). 복구는 콘솔
  재토글 또는 SDS 문의.
- SCR push 유저 regrscr856f95 (fd0328e2…) + 정책 3종(regrscrall/regrscrlogin/
  regrscrselfkey) + 인증키는 유지 중 — 해소 후 재사용, 불필요 시 삭제.

## run-923a (2026-07-11 아침 풀런) 4xx 트리아지 — 서비스 커버리지 수리 배치 (2026-07-11)

오너 풀런 `20260711-082618-923a`(119 lifecycle · step-end 1,529 · 4xx/5xx 367)의
실패 전문을 oplog 버킷 artifact(`runs/<id>/artifact/events.jsonl`)에서 폴딩.
미검증 갭 270키 중 **123키가 이번 런에서 4xx 도달** — 아래는 원인 확정분.

### compute/virtualserver — 이미지 계열의 categorical 제약 2건 (실측 확정)

- **볼륨 있는(custom, 서버 유래) 이미지는 visibility/min_disk 변경이 categorical
  거부**: `Image.InvalidVolumeOnVisibilityUpdate` "Image with volumes cannot
  update visibility" (min_disk도 동일 계열 — HB3b-2 기실측). 멤버 API는
  `Image.SharedVisibilityRequired`(members는 shared 이미지만) → **custom 이미지
  에서는 member 4종+share가 구조적으로 불가**. 수리: 전 멤버 체인을 볼륨 없는
  image-shell(POST /v1/images)로 이전, visibility=shared 전환 스텝 신설.
- **createimage/importimage는 `url` 필수** (400: 255자 이하 + `.qcow2$` 규격,
  에러가 object-store 예시 URL 제시). **실존 public qcow2 자산을 상비**:
  `assets/regr-minimal.qcow2` (수제 qcow2 v3 헤더+refcount+L1, 262,144B) —
  oplog 버킷에 public-read 업로드, **RGW tenant path 형식**
  `https://object-store.kr-west1.e.samsungsdscloud.com/<account_id>:apitest-oplog-permanent/assets/regr-minimal.qcow2`
  로 anon GET 200 실측 (path-style `/bucket/...`은 NotFoundBucketNameInPath 400,
  ops.html DEFAULT_BASE와 동일 형식이 정답).
- createimagemember 필드명은 **`member_id`** (`member`는 "Extra inputs are not
  permitted"; 값 규격 `^[A-Za-z0-9-]*$` 1-64).
- updatesnapshot은 **`name` 필수** (description만 보내면 400; `^[a-zA-Z0-9-_ ]+$`).
- revertvolumetosnapshot 400의 뿌리 = **스냅샷 create 직후 status=creating**
  (show 200이어도 not available) → available settle 필수. 스냅샷 상태 필드는
  `$.status` (볼륨은 `$.state` — 필드명이 다름, cinder 계열).
- volume-transfer create 500 = PF-21 기지 제품버그 재확인 (parameter 수리 불가).

### DBaaS 5엔진 공통 (database__subops-full)

- **set-parameters 오염 클래스 (epas·pg 실측)**: PUT parameters 202 수락 → 비동기
  Parameter Modify 실패 → `service_state=UNKNOWN` 추락 → 이후 mutating subop
  **전부 400 InvalidState** (엔진당 12op 연쇄). wait-after-*가 UNKNOWN을 until에
  포함해 즉시 '통과'하는 설계라 오염이 가려짐. **같은 런에서 sync-cluster-state
  202가 RUNNING을 복구**해 후속 upgrade-kernel/resize 202 성공 실측 → 복구
  스텝(sync-state + RUNNING 대기 600s, UNKNOWN은 until 제외)을 param 그룹 직후로
  전진 배치.
- **set-security-group-rules 400 전 엔진 공통 뿌리**: 문서 모델
  `UpdateSecurityGroupRulesRequest = {add_ip_addresses, del_ip_addresses}` —
  빈 리스트 no-op도, OpenStack식 rules 배열도 "invalid security-group-rules".
  실 IP를 add 해야 함.
- **resize-block-storage 400 InvalidBlockStorageRoleType**: OS 롤 그룹은 리사이즈
  불가 — add-block-storages가 만든 **DATA 그룹([1])을 재조회 캡처** 후 리사이즈.
- **add-block-storages size_gb는 multiple_of 제약** (10 거부·104 통과 실측 —
  8의 배수 추정).
- **register-log-export-config InvalidScheduleData**: frequency=DAY에
  day_of_month=28 + day_of_week=MON 동시 지정이 모순 — 미사용 필드는 센티널
  (`-1` / null; day_of_month 패턴에 `-1` 명시).
- patch-minor-version: 자기 자신 버전 송신은 **"Unpatchable version" 400 확정**
  (HB1 수리의 열린 질문 종결). 2xx에는 구버전 create → 신버전 패치 전략 필요
  (SKE upgrade [1]→[0] 선례) — 백로그.
- switchover 404 "switch over host not found" = ha_enabled:false 단일 노드
  categorical — waiver 후보.
- remove-backup-histories/unset-backup 401 = 기지 백엔드 quirk 재확인 (waiver #6).

### 라이프사이클 구조 규약 (신규)

- **create/wait는 hard 스텝이어야 한다**: optional+group create가 실패하면 그룹
  teardown만 하고 **나머지 30-49 스텝이 죽은/삭제된 id로 계속 실행** (mariadb
  create 500 → 49스텝 연쇄, eventstreams 프로비저닝 ERROR → es-wait terminal-bad
  → 그룹 cleanup이 클러스터 삭제 → 이후 전부 404). hard로 승격하면 실패 시
  라이프사이클이 정직하게 중단+전체 teardown. mariadb/eventstreams에 적용.
- eventstreams es-wait의 step-end 이벤트가 category=ok로 기록되고 실제로는
  terminal-bad 실패인 관측 어긋남 존재 (이벤트 발행이 분류 앞) — 트리아지 시 주의.

### networking

- **direct-connect create 400 `not-exist-log-storage`**: `firewall_loggable=true`
  는 계정에 **FIREWALL network-logging 스토리지**를 요구 — gen-wave4-nlog의 검증
  DTO `{bucket_name: apitest-logsink, resource_type: FIREWALL}` 선행 생성으로 해결
  (스토리지가 이미 있으면 그 그룹만 실패하고 DC는 진행).
- vpc-peering 규칙: destination_cidr에 requester CIDR **전체 동일값**은 400
  not-available — 진부분집합 /24로. approve는 same-account에서 "Approval is not
  required" 400 categorical (waiver/PF 후보).

### data-analytics (구조적 — 파라미터 수리 범위 밖)

- data-flow/data-ops/quick-query의 id-bound GET 400은 **계정에 리소스 0개**라
  캡처가 실패해 리터럴 토큰이 형식 검사(`DFLOW-`/`DOPS-` prefix)에 걸리는 것.
  2xx에는 실 리소스 필요 — createdataflow는 cluster_id·ingress_controller·
  storage_class 등 **실 클러스터 전제** (오너 설계 결정 필요, 과금).
  quick-query validate 500 ContactAdmin은 PF 후보.
## SKE nodepools 목록 응답 형태 — `links`가 items보다 먼저 온다 (2026-07-11, 라이브 실측)

`GET /v1/clusters/{id}/nodepools` (service=ske)는 `{"count":1, "links":[],
"nodepools":[…]}` 형태 — **빈 `links` 리스트가 items 키보다 앞**에 온다.
"첫 번째 (빈 또는 dict) 리스트"를 items로 집는 파서는 links를 돌려줘 노드풀을
0개로 오판한다 (역대 스윕이 SKE 노드풀 teardown을 건너뛰고 클러스터 delete가
409-루프하던 근본 원인 — `cleanup.reconciler._items` 수정으로 해소: 비어있지
않은 dict-리스트 우선, `links`는 fallback에서도 제외). dbaas 계열(postgresql/
mysql/mariadb/epas)의 `/v1/clusters`는 `{"contents":[…], "count", "page",
"size", "sort"}` 형태로 contents가 먼저라 무해. flat `/v1/nodepools`(+쿼리
변형)는 이 계정에서 403 — 노드풀 접근은 **네스티드 경로만** 유효.

## SCF 함수 삭제 ladder — PrivateLink 서비스 먼저 비활성화 (2026-07-11, PF-46)

`DELETE /v1/cloud-functions/{id}`는 함수의 **PrivateLink 서비스가 enabled면
400** (`scp-cloud-function.function-not-deletable-error`). 선행 절차:
`PUT /v1/cloud-functions/{id}/configurations/privatelink-services`
body `{"privatelink_service_enabled": false}` → 그 다음 함수 DELETE.
플랫폼 결함: wave5의 regrw5trg* 2건은 privatelink_service_state가 **6/20부터
3주째 CREATING** (requested_endpoints=[])이고, CREATING 상태에선 비활성화도
400 (`privatelink-service-not-allow-state-error`) → 함수가 영구 삭제불가.
백엔드(SDS) 해소 필요 — 스윕은 stuck으로 보고하고 수렴 (reconciler
_pass_scf에 ladder 반영). SCF 전용 privatelink 경로는 scf 서비스의
`/configurations/privatelink-services|endpoints`이며 VPC의
`/v1/privatelink-services`에는 나타나지 않는다.

## 서브넷 기본 목록이 VPC_ENDPOINT 타입을 숨긴다 (2026-07-11, PF-47)

`GET /v1/subnets`(쿼리 없음)는 **type=GENERAL 계열만** 반환하고 VPC 엔드포인트가
만든 **VPC_ENDPOINT 타입 서브넷을 숨긴다** — 콘솔에는 보이는데 API 목록에 없어
"스윕이 못 보는 유령 서브넷"이 되고, VPC 삭제를 409로 잡는데 holder 탐지에도
안 걸린다 (아침 regrsubb6750b93·오후 regrsubc86cfbf3 실측; 수작업 삭제는 됐음 —
show/DELETE by id는 정상 동작). 조회는 `?type=VPC_ENDPOINT` (enum: GENERAL·
LOCAL·VPC_ENDPOINT; 잘못된 값은 400에 enum 명시, 미지의 쿼리키는 조용히 무시됨
— `subnet_type=`은 무시되고 기본 목록 반환). reconciler의 서브넷 패스와
_purge_vpc_children이 두 컬렉션을 모두 훑도록 수정(PF-47). run_scoped의 409
related_resources SRN 폴백은 이런 숨은 서브넷의 기존 완화책이었다.

## VPC 409 본문의 related_resources가 홀더를 직접 알려준다 — SRN 자동회수 (2026-07-11, run-892a)

`DELETE /v1/vpcs/{id}` 409(`scp-network.vpc.related-resource`) 본문의
`related_resources`는 홀더의 SRN 목록
(`srn:e::<acct>:<region>::<service>:<type>/<id>`)을 명시한다 — run-892a에서
**direct-connect**(`regrdc*`)가 어떤 목록 패스에도, holder 탐지(TGW/LB/NAT)
에도 안 잡힌 채 공유 VPC를 스윕 2라운드×6회 409로 잡았고, SRN이 유일한 단서
였다. 스윕은 이제 409 본문 SRN을 파싱해 홀더를 직접 삭제 후 즉시 재시도
(`_purge_409_holders`; run_scoped의 서브넷 전용 폴백을 일반화). DC 자체의
teardown 규약: **자식 routing-rules를 먼저 비워야 DC DELETE가 수락**(rule이
남으면 409; run_scoped도 같은 컬렉션 내 깊은 경로 우선으로 교정). 서비스
호스트 키는 `direct-connect`(하이픈; `directconnect`는 프록시 502).
~~미해결: DC의 network-logging storage delete 400~~ → **해소 (같은 날 오후,
아래 블록)**: 뿌리는 DC delete 202 비동기 수렴 전의 삭제 시도 — DC가 완전히
사라진 뒤에는 스토리지 DELETE 204 (d5637fad 수동 회수 실증). 시나리오에는
wait-direct-connect-gone(404 폴)로 반영.

## run-892a 판정 + 2라운드 수리 (2026-07-11 오후)

**run 20260711-132620-892a (110 pass / 6 fail, 74분)** — 1라운드 수리 판정:

### 명중 (신규 2xx 확정)

- **VS image-shell 체인 7키**: createimage 200 · updateimage(visibility→shared)
  200 · listimagemembers 200 · createimagemember 200 · showimagemember 200 ·
  deleteimagemember 204 · deleteimage 204. updatesnapshot 200 ·
  revertvolumetosnapshot 202 (available settle이 정답) · -full image-update 200.
- **DBaaS**: set-security-group-rules **202 × 5엔진** (add_ip_addresses+실IP가
  정답) · epas/pg **sync-parameters 202** (settle-cluster-after-parameters 복구
  전진 배치가 오염 차단 실증) · cachestore resize-block-storage 202.
- **DC**: FIREWALL 로깅 스토리지 201 → create-direct-connect 202 ·
  showdirectconnect 200 · createroutingrule 202 · listroutingrules 200.

### 라이브 프로브 확정 (2026-07-11, 이미지 API 직접 실험)

- **importimage = 구조적 도달 불가**: import는 `status=queued` 전용인데
  createimage가 url을 강제(생략·null·"" 모두 400; ""는 "URL must end with
  '.qcow2'")하고 **url-생성 이미지는 즉시 active** — queued 이미지를 만들 방법이
  없음. PF 후보(unreachable operation) + reachability waiver 후보.
- **updateimagemember = 단일 계정 도달 불가**: status는 소문자
  accepted/pending/rejected만 유효 형식이나 유효값 전부
  `Image.MemberCannotBeUpdatedToSharingImage` — shared 이미지 member status는
  owner 계정에서 변경 불가(member 수신 계정 전용 op 추정). waiver 후보.
- **createsharingimage는 BDM 요구**: 셸(빈 이미지)에서 400
  `Image.InvalidImageWithBlockDeviceMapping` — BDM은 server-유래 custom image만
  가짐 → share 스텝을 custom-image 그룹 create 직후로 이동 (923a의 404는 verify
  실패가 그룹 teardown을 먼저 부른 순서 문제).

### 2라운드 수리 (다음 런/프로브 판정)

- **DC 잔여 3키**: create 202 직후 CREATING에 set/delete가 400
  not-editable/not-deletable → wait-direct-connect-active(+rule 후 재대기) 신설,
  delete retry에 400 추가. **adopt 제거** — DC-routing이 공유 VPC에 DC를 만들자
  gen-private-nat의 DC create가 400 vpc-already-connected(VPC당 1) — 1라운드
  수리가 만든 신규 충돌, 자체 VPC(10.137/20)로 격리.
- **DB log-export**: DAY+(-1/null)도 InvalidScheduleData → 가설 2 =
  WEEK+MON+day_of_month:null.
- **DB resize**: DATA 그룹([1]) 대상은 맞았으나(RoleType 에러 소멸) 104→112가
  `InvalidBlockStorageSize` (cachestore만 202) → 208 시도(증분 제약 가설).
- **create 백엔드 500 간헐 클래스**: mysql-cluster·servicewatch create-group이
  ContactAdminForAssistance 500 (마리아는 어제, 오늘은 이 둘 — 엔진 무작위 간헐)
  → 5엔진 create + create-group에 500/503 retry 2회 균일화.
- **lb-member-set**: 400 `ExistRequestMemberState`("이미 ENABLE") — set은 멱등
  거부 → DISABLE 전이로 수정.
- **scf logs/metrics**: `time`은 integer ("1h" → parse 400) → time=60.
- **eventstreams**: 프로비저닝 FAILED 2연속 (es-wait terminal-bad가 정확히 잡아
  즉시 중단+teardown — 신규 hard-create 규약 실증). 뿌리는 백엔드/바디 — PF 후보.
- **gen-wave2-scf cronjob-trigger 404**: 타 lifecycle 자원 dup-read가 삭제 후
  도착하는 레이스 — 트리아지 후속.

## 라이브 재검증 2건 완주 — DC 완전 그린 + peering 시맨틱 확정 (2026-07-11 오후)

- **networking-direct-connect-routing 단건 재실행 (pytest, 7:43) = 1 passed,
  전 스텝 2xx**: create 202 → wait-ACTIVE → show 200 → **set 200** → dup-4xx
  400(negative 실증) → rule create 202 → wait → list 200 → **rule delete 202 →
  DC delete 202**. ACTIVE settle 2곳이 not-editable/not-deletable 400의 정답.
  → **direct-connect 8/8 (100%)**, verified store +17 (2,431→2,448,
  run local-dc-reverify-20260711).
- **teardown 누수 재발견·수리**: delete-dc 202 비동기 수렴 전에 teardown 스택이
  돌아 FIREWALL 스토리지+VPC 잔존 → `wait-direct-connect-gone`(404 폴) 신설.
  잔존 3건(스토리지·VPC·pytest 공유 VPC)은 수동 회수 완료 (204 확인).
- **vpc-peering rule 시맨틱 (ACTIVE 피어링 4조합 프로브)**:
  `REQUESTER_VPC + requester-cidr 진부분집합` **만** 202 (교차/반전 조합은 전부
  'must be included in the destination VPC IP cidr' 400; 비ACTIVE 상태에선
  vpc-peering-not-active-state 400 — 별도 에러로 구분됨). 892a의 400 뿌리 =
  create-vpc **adopt로 requester cidr이 공유 VPC(10.124/20)로 치환**되는데 rule
  cidr은 10.130.x 하드코딩 — adopt-cidr 불일치 클래스(DC vpc-already-connected와
  형제). vpc-peering create-vpc adopt 제거.
- **adopt-cidr/자원 충돌 클래스 (신규 일반화)**: 공유 VPC를 adopt하는 lifecycle이
  ① VPC-단위 배타 자원(DC는 VPC당 1)을 만들거나 ② 자기 cidr 하드코딩 파라미터를
  쓰면 충돌/불일치를 만든다 — adopt 채택 전 이 두 조건을 점검할 것.
- 프로브 관측: same-account 피어링도 CREATING이 35분까지 걸릴 수 있음 (892a는
  ~수십 초 — 플랫폼 편차 큼; wait-peering-active timeout 여유 필요).

## 공유 VPC teardown은 subnet 소멸 대기가 필수 — 아니면 VPC만 잔존해 다음 런을 큐에 가둔다 (2026-07-12)

`shared_infra --teardown`이 subnet DELETE(202, **비동기** — DELETING이
30초~3분 지속)를 발행한 직후 곧바로 VPC DELETE를 날리면 409로 거부되는데,
종전 코드는 상태코드를 안 보고 예외만 잡아 "deleted"로 기록했다 → **중단/
실패한 런마다 공유 VPC 1개가 ACTIVE로 잔존** (2026-07-11 regrvpcsh6a5423ea,
2026-07-12 regrvpcsh6a54273f 두 번 연속 같은 패턴 실측). 잔존 VPC는 console2
admission의 baseline/mine_live로 잡혀 다음 런의 headroom을 깎고, **실행 중인
런이 0개면 `_try_admit_queue`를 다시 불러줄 이벤트가 없어**(finish/abort에서만
호출) 큐의 런이 영원히 대기했다. 수리: teardown이 subnet 404 확인까지 대기
(≤240s) 후 VPC를 409 사다리(5×15s)로 삭제 + console2에 20s 재입장 티커
(`_ensure_admit_ticker`, 큐 빌 때까지). 오프라인 회귀:
tests/offline/test_shared_infra_teardown.py · test_console2.py(ticker).

## run-543a (2026-07-13 오너 풀런) 판정 — 115/119 pass, 수리 대량 확정 (2026-07-13)

주의: 이 런은 **2라운드까지의 main**으로 실행됨 (3라운드 커밋 ad7b4530 이전 pull)
— rmtags b64·cloudmonitoring datetime·fifo dedup·scf time=60·secretsmanager CIDR
리스트의 실패 재현은 예상된 것 (수리는 main에 반영 완료, 다음 pull 런이 판정).

### 이번 런으로 라이브 확정된 수리

- **vpc-peering 완전 그린**: wait ACTIVE → rule create 202 → capture(재조회) →
  rule delete 202 — createvpcpeeringrule/deletevpcpeeringrule 신규 2xx
  (adopt-cidr 불일치 수리 + envelope 캡처의 첫 정상 완주).
- **DC 체인 완전 그린 재확인** + wait-direct-connect-gone 404 종착 정상 동작
  (잔존 0 — 로깅 스토리지/VPC 누수 재발 없음).
- **DB resize-block-storage 202 × 4엔진** (epas/pg/mariadb/cachestore) —
  DATA 그룹([1]) 재캡처가 정답으로 확정.
- **createsharingimage 202** — BDM 있는 custom image 대상 이동이 정답.
- mysql-cluster·servicewatch create 정상 (500 간헐 재발 없음, retry 보험 유지).
- publicip RESERVED 폴 즉시 통과 (5분 공회전 제거 확인).
- register-log-export-config: InvalidScheduleData **소멸** (WEEK+MON 형식 통과)
  — 새 에러는 500 ContactAdmin. 유력 원인: bucket_name `regrcoveragebucket`이
  실존하지 않음 (존재 검증이 500으로 표면화 — PF 후보) → 실존 버킷
  `apitest-logsink`로 교체 (다음 런 판정).

### 신규/잔여 실패와 처치

- **gen-heavy-vs-netops create-internet-gateway 400 already-associated** —
  공유 VPC에 타 패밀리가 먼저 IGW 부착 (HB4c 기확정 클래스). vpn.json의
  adopt-or-create 부트스트랩 이식 (list-igw 채택 + owned_igw_id 분리 teardown).
- **eventstreams 프로비저닝 4연속 FAILED** (Kafka 3.9.1, 202 수락 후 6-8분차) —
  PF 후보 승격. 완화 시도: es-prefer-older-version(soft [1]) 신설.
- **gen-heavy-lb-members create-lb-member 400** = {member_vm_ip} 미해석 —
  svc-opt 세션이 잡은 '암묵 VM 의존'과 동일 뿌리 (그쪽 트랙 진행 중, 중복 회피).
- container-scr-registry skip = registry quota=1 환경 (기지).
- import-image 409·update-image-member 400·switchover 404·patch Unpatchable =
  구조적 확정분 재확인 (waiver/백로그 후보 그대로).

## 스케줄 실측: 짧은 VPC-슬롯 소비자가 LPT에서 런 꼬리가 된다 → priority_first 핀 (2026-07-13)

run 20260713-102144-543a (117종, 예측 58.9분 / 실제 53.1분) 실측:

- **provision(공유 인프라) 구간 = 256.9s(4.3분), 그동안 테스트 0개 실행.**
  공유 서브넷 create→ACTIVE 폴 +12s~+140s(≈2.1분), 이어서 DB 서브넷
  +148s~+250s(≈1.7분) — 두 서브넷이 **직렬** 생성이라 4.3분이 통으로
  선행 대기다 (병렬화하면 ~2분 절약 가능; 첫 lifecycle-start +4.36분 실측).
  서브넷 1개 create→ACTIVE는 kr-east1에서 약 2분 안팎으로 봐야 한다.
- **networking-vpc-subnet +35.4분, vpc-subnet-vip-nat +44.2분에야 시작**
  (vip-nat end +53.1분 = 런 makespan 그 자체). 두 시나리오는 자체 VPC를
  만드는 짧은(400s/544s) 슬롯 소비자라 LPT(긴 것 우선)에서 뒤로 밀리는데,
  그 시점엔 슬롯(4)이 장기 점유 중이라 대기까지 겹쳐 런 꼬리가 된다.
  t=0에는 공유 VPC 1개뿐이라 슬롯이 비어 있으므로 **먼저 투입해 먼저
  반납**시키는 게 옳다 (오너 지시 2026-07-13).
- 수리: `dependencies.json vpc_schedule.priority_first`(핀 목록, 현재 위 2종)
  → conftest 수집 정렬(실행)·local_run.simulate_schedule(예측 Gantt)·
  dag_scheduler.priorities(dag 경로) 3곳이 `schedule_optimizer.
  load_priority_first()`로 같은 목록을 읽어 0차 정렬 키로 핀한다.
  회귀: tests/offline/test_crud_schedule_order.py(핀 2건) ·
  test_dag_scheduler.py::test_priority_first_pin_overrides_lpt.

## 백엔드는 같은 VPC의 서브넷 ACTIVE 전이를 직렬화한다 → provision no-wait + adopt 게이트 (2026-07-13)

run-543a 실측: 서브넷 2건을 **동시에 생성**해도(create 선발행은 07-08부터 적용
확인) ACTIVE 도달이 128s/238s로 순차 — 클라이언트 병렬화로는 못 줄이는 백엔드
직렬화다. 종전에는 provision이 둘 다 ACTIVE까지 기다려 **런 머리 4.3분간 전
워커 유휴**였다. 수리(오너 "2번 바로 수정" 2026-07-13):

- provision은 서브넷 create+**track(생성 직후)** 후 즉시 반환 가능
  (`engine.provision_shared_vpc(wait_subnets_active=False)`,
  `SCP_PROVISION_SUBNET_NOWAIT=true` — console2/local_run 경로가 켬, CI 기본은
  종전 유지). VPC ACTIVE 대기(~10s)는 유지 (서브넷 create가 필요로 함).
- ACTIVE 보장은 **첫 adopt 시점**의 `engine._ensure_adopted_active` soft
  게이트로 이동 (프로세스당 id별 1회, 이미 ACTIVE면 GET 1회, 필드 미상/실패/
  타임아웃은 통과 — 이어지는 create가 4xx로 표면화). 그동안 free-class와
  자체 VPC 생성군(priority_first 핀 포함)이 먼저 돈다 → 런 시작 유휴 제거.
- 회귀: tests/crud/test_shared_vpc_adopt.py (게이트 폴/캐시/미상-통과 +
  provision no-wait 4건).
- **오너 도메인 확인 (2026-07-13): DB 클러스터 8종이 공유 DB 서브넷
  (10.124.7.0/24) 하나를 공유해도 플랫폼상 병목 없음** — DB-lane 단일 공유
  서브넷 설계 유지 근거.
- durations.json에 run-543a passed 115종의 events wall span을 fold
  (in-run 실측 — LPT 랭크 정확도 개선; vpc-peering avg 25→31분 등).

## net-VPC A/B 설계 — peering의 두 VPC를 상주 공유해 IGW/DC 배타를 분산 (2026-07-13, 오너 설계)

"peering용 VPC 2개를 각각의 VPC 내부 IGW 등 테스트에도 쓰자"(오너)를 전면 구현:

- provision이 **net-A(10.130.0.0/20)·net-B(10.141.0.0/20)** 를 메인 공유 VPC와
  함께 상주 생성 (adopt 토큰 `vpc#a`/`vpc#b`, 선택-인지 스킵, no-wait+게이트 적용,
  env `SCP_SHARED_NET_VPC_{A,B}_ID`/`_NAME`).
- **A**: vpc-peering requester + vpc-subnet-vip-nat(서브넷 10.130.9.0/24, A의
  유일한 IGW 소유자). **B**: peering accepter + gen-wave5-fw(IGW 방화벽, B의
  유일한 IGW) + networking-direct-connect-routing(B의 유일한 DC).
- 성립 근거: ① peering rule CIDR 하드코딩(10.130.x)이 A의 CIDR과 일치하므로
  2026-07-11의 adopt-cidr 불일치 클래스가 성립 안 함 ② IGW(VPC당 1)·DC(VPC당 1)
  배타가 A/B 분산으로 해소 ③ peering(31분)의 시간 그림자 안에 vip-nat 9분·
  fw 1.6분·DC 체인이 다 들어감. **런당 VPC 생성 7→5회**, 상주 3 + 슬롯 cap-3.
- 세부 수리: peering account_id 소프트 캡처를 wait-vpc-b(GET)로 복제(adopt 시
  create 응답이 없음); fw 방화벽 조회를 `vpc_name={net_b_vpc_name}`로 파라미터화
  (adopt=shared_ctx 시딩, 폴백=create 응답 $.vpc.name 캡처 — 엔진이 adopt 캡처
  시딩 시 기존 ctx 값을 보존하도록 수정); IB-049 스킵 가드를 base-kind(vpc*)로
  일반화 (A/B는 CIDR 고정이라 동시 self-create는 즉시 overlap 400).
- **라이브 미검증 2건 (다음 풀런 판정)**: ① 피어링 걸린 VPC 안 IGW/VIP/DC 생성
  허용 여부 ② IGW 방화벽(product_type=IGW)과 DC 방화벽의 B 동거 무충돌.
- 회귀: tests/crud/test_shared_vpc_adopt.py 5건 신규(양측 시딩/폴백/IB-049/캡처
  보존/provision A·B) · validate_dag --check 0 gaps · 오프라인 545 passed.

## net-VPC A/B 라이브 첫 런 판정 — 설계 성립 확정 (2026-07-13 04:20, 5/5 passed)

선택 5종(peering·vip-nat·fw·dc-routing·nvs), provision→pytest(-n5)→teardown→
잔존 스캔 풀사이클 rc=0:

- **provision 18초** (기존 4.3분): 메인+net-A+net-B 생성, no-wait 반환, DB
  서브넷은 선택-인지로 스킵 — adopt 게이트 경로 실증.
- **미지수 ① 판정: 피어링 걸린 VPC 안 IGW/VIP/포트 생성 허용** — vip-nat(A의
  IGW+VIP)·fw(B의 IGW)가 peering과 같은 VPC에서 완주(각자 자원 생성→삭제),
  peering rule 체인도 adopt-cidr 일치로 정상(DELETING 수렴 관측).
- **미지수 ② 판정: IGW 방화벽(fw)과 DC(dc-routing)의 B 동거 무충돌** — 5/5 pass.
- teardown: 공유 서브넷 gone-대기 후 VPC 3개 모두 204, 잔존 = 알려진 IAM-gated
  로그그룹 1건뿐 (유효 잔존 0).
- 유일 결함: vip-nat VIP IP 하드코딩(10.132.9.6)이 서브넷 재배치를 안 따라가
  400 check-ip-address-overlap → vip 그룹 스킵. 10.130.9.6으로 정렬(커밋 완료),
  단독 재검증 별도 수행.

### 후속 판정 2건 (2026-07-13 04:35)

- **vip-nat 단독 재검증(폴백 self-create 경로): 1 passed, VIP 그룹 경고 없음**
  — 10.130.9.6 정렬로 createsubnetvip/connected-ports/static-nat-ips 체인 복구.
- **engine 인라인 teardown에도 서브넷 gone-대기 + VPC 409 사다리 이식**: 07-12
  수리가 shared_infra.teardown에만 들어가고 conftest/단독 경로의 인라인
  teardown은 빠져 있어 solo 런마다 공유 VPC가 잔존했다(재검증 런
  regrvpcsh6a5467bd 실측, 스윕으로 회수 완료). 이제 두 경로 동일.

### IAM-gated 로그그룹 신규 인스턴스 2건 (2026-07-13 스윕)

중단 런 ff1c의 mariadb가 자동 생성한 `/scp/mariadb/regrmhhkafhdm/alertlog`·
`/scp/mariadb/regrmhhkafhdm/slowlog` — 기존 `/scp/ske/regrske4936128d-0325b`와
동일 클래스(그룹 DELETE 200이지만 IAM-gated 자식 log-stream 때문에 잔존,
log-stream IAM 권한 필요). 스윕은 stuck으로 1회 보고 후 재시도 안 함(수렴 가드
정상 동작). 클린업 루틴의 알려진-예외 목록에 이 2건 추가해 취급할 것.

## run-c373 (2026-07-13 큐 런, 최신 main) 판정 — 3라운드 수리 일괄 그린 + 429 클래스 발견

111 passed / 7 failed / 1 skip. **실패 7 중 5건이 429**(Too Many Requests) —
vpc 호스트 순간 burst 스로틀로 서로 다른 5개 lifecycle이 각 1건씩 하드 실패
(신규 시스템 클래스). http_client `RETRY_STATUS`에 429 추가(전 메서드 재시도
안전 — 처리 전 거부) + Retry-After 존중(상한 30s).

### 이번 런으로 확정 그린 (3라운드 + 이전 수리)

- **rmtags SRN/key b64**: 12스텝 전부 2xx (tags 계열 카탈로그 키 일괄 획득).
- **cloudmonitoring**: 리스트 3키 200 (datetime+과거끝+실 productResourceId).
  show-event-policy-* 는 계정에 이벤트 정책 0이라 리터럴 400 잔존 —
  gen-cm-event-policy 연계(정책 생존 창에서 읽기) 후속.
- **fifo dedup 2키 200** · **secretsmanager reveal 200** (오너 콘솔 IP가 CIDR
  리스트에 포함 — 콤마 리스트 수리 최종 확정).
- **IGW adopt-or-create (vs-netops)**: list→create 202→wait 200 완주.
- peering rule·DC 체인·DB resize 그린 유지.

### 확정/승격된 결함

- **DB register-log-export-config 500 = 백엔드 PF 확정**: 스케줄 형식 통과
  (InvalidScheduleData 소멸) + 실존 버킷(apitest-logsink)으로도 5엔진 전부
  500 ContactAdminForAssistance — 우리 입력 소거 완료, SDS 문의감.
  하류 set/export/unregister는 register 성공 전까지 도달 불가.
- **eventstreams 프로비저닝 = 백엔드 PF 확정 (5연속)**: 구버전([1])으로도
  es-wait terminal-bad — 버전 무관, 202 수락 후 비동기 FAILED. waiver/SDS 문의감.
- **scf logs/metrics `time` 허용값 = {1, 3, 12}** (에러 본문 명시; 60도 거부) —
  time=1로 교정. 문서에는 어떤 제약도 없음 (undiscoverable-params PF 후보).
- **gen-heavy-lb-members create-internet-gateway 400** = vs-netops와 동일
  already-associated → 같은 adopt-or-create 이식 (owned_igw_id 분리).

### 스케줄/설계 관점 판정 (같은 run-c373, 신설계 첫 풀런)

119종, makespan 66.3분:

- **실증**: provision **19.9초**(구 4.3분, no-wait+게이트) · net-A/B 상주 +
  peering adopt 완주(+1.6→34.1분 passed) · fw(+0.4→1.3분)·dc-routing(+2.1→4.7분)
  B에서 passed · 핀 vip-nat/fw/hsn +0.4~0.5분 시작 · 상주 3+자체 2 = cap 5 정확.
  vip-nat/hsn 실패는 설계 문제가 아니라 위 429 클래스 (http_client 수리로 해소).
- **makespan 악화 원인 = pg-cluster +42.2분 지각** (커밋 durations rank 10
  ≠ 실측 지각 → 콘솔 머신 durations.local.json의 하향 오염이 커밋본을 가림).
  **duration 병합을 max-merge로 변경** (conftest·simulate 동일) — LPT에서
  과대추정은 무해, 과소추정은 몬스터 꼬리. 이 수리로 다음 런 pg는 t≈0 시작,
  꼬리 23분 제거 → **기대 makespan ~43분** (66.3-23).
- eventstreams 실패는 기지 PF 후보 재확인. run-c373 passed 111종 스팬 fold (rolling avg).

## 실행계획 미적용의 진범 = --dist=worksteal 연속 블록 선분배 (2026-07-13, run-c373 판정)

schedule_verdict: 첫 배치 겹침율 21%, 실제 시작 순서 vs LPT 스피어만 +0.11
(원시/스텝수/알파벳도 전부 ~0) — **정렬이 무효화**된 무작위 양상. 원인:
local_run/console2가 xdist를 `--dist=worksteal`로 실행하는데, worksteal은
수집 순서를 **워커별 연속 블록으로 통째로 선분배**한다 → LPT 내림차순이
24조각으로 잘려 최상위 몬스터들이 같은 워커에 직렬로 묶임 (pg-cluster
rank 11이 +42분 지각, vs-server-actions +34.5분 — 같은 블록 앞 항목 뒤에서
대기). 543a까지의 --dist=load(청크 2개)용 페어 인터리브도 worksteal에선 역효과
(블록 하나에 heavy 3개).

수리: conftest 정렬을 **라운드로빈 버킷 연접**(`_roundrobin_blocks_for_workers`)
으로 — rank j → 버킷 j%n, 연속 블록 b의 선두가 전체 rank b가 되어 상위 n개가
전부 다른 워커에서 t≈0 출발, 블록 내부 desc라 스틸은 경량 꼬리부터 가져간다.
(--dist=load로 되돌리면 인터리브로 교체할 것 — dist 모드와 정렬은 한 쌍이다.)

## run-c373 "이상함" 정밀 재판정 — 진범은 durations 오염, 정렬은 보조 (2026-07-13)

오너가 "예측 vs 실제" 패널에서 이상 지적 → schedule_verdict를 c373에 재실행하고
xdist 3.8 worksteal 소스를 실측 검증. 정정된 인과:

- **66.3분 makespan의 실제 원인 = 딱 2개 몬스터의 지각**, 전반적 정렬 무효화가
  아니다. 판정 C(시간 기반, 신설): 예측 첫 배치 24개 중 **>5분 지각 2개뿐**
  (`database-postgresql-cluster` +42분, `vs-server-actions-verify` +35분),
  **중앙값 0.9분** — 나머지 22개는 이미 조기 시작했다. pg-cluster가 41.8분에 떠서
  ~24분 돌면 66분 = makespan 정의. 겹침율 21%(판정 A)는 rank 기반이라 낮게 나온
  것이고 실제 지각과는 별개다.
- **진범 = durations 하향 오염(마스킹).** pg-cluster 커밋본 durations는 1448s
  (heavy, rank~11)인데 c373 런타임엔 콘솔 머신 `durations.local.json`의 오염된
  낮은 값(fast-fail 학습 12s)이 커밋본을 가려 **경량으로 오분류 → 꼬리로 디스패치**.
  1차 수리 = **max-merge**(읽기 = max(커밋본, 오버레이)) — 오버레이가 커밋본을
  하향으로 못 가린다. `rm durations.local.json`보다 견고(오너 수동 삭제 불요).
- **worksteal 라운드로빈은 보조 수리(진짜-heavy 직렬화 방지).** xdist 3.8
  `WorkStealingScheduling.schedule()`은 pending=range(N)(수집 순서)로 놓고
  `check_schedule`이 `num_send = len(pending)//nodes_remaining` 연속 프리픽스로
  분배 → 워커 블록은 연속 조각이고 **크기 비균등**(N=119,n=24 → [4,5,5,…]).
  실측 검증(시뮬레이션): 라운드로빈 순서 하에서 **상위 24 몬스터 offset ≤ 1**
  (rank 0만 블록 선두, 1~23은 경량 항목 1개 뒤 = offset 1, 시각 ~1분).
  순수 내림차순은 블록0=[rank0,1,2,3] 직렬(offset 최대 4) = 42분 꼬리의 형상.
  블록 크기 비균등 때문에 offset 0 완전정렬은 아니지만(경량 1개 뒤), makespan
  영향 무시 가능 — 완전정렬은 xdist 분배 알고리즘에 커플링되므로 채택 안 함.
- **다음 런 판정 기준(정본)**: 겹침율(rank)이 아니라 **판정 C의 몬스터 최대
  시작 시각** + makespan. 수리 실효 = 첫 배치 몬스터 >5분 지각 0개 & makespan
  ~40–43분 (최장 = `postgresql-cluster-subops-full` 2346s ≈ 39분이 하한을 정함).
- 회귀 고정: 이전 `test_roundrobin_blocks_top_n_lead_each_block`은 블록 경계를
  0,3,6(균등)으로 잘못 가정(실제 worksteal 8/3 = 0,2,5) → 실제 분배를 흉내 내
  "몬스터는 경량 뒤에만"을 단언하도록 교체. schedule_verdict에 판정 C 헬퍼
  `monster_start_verdict` + offline 테스트 신설.

## worksteal 꼬리 붕괴 → --dist=load --maxschedchunk=1 전환 (2026-07-13, run-afa8 실측)

run-afa8(worksteal 라운드로빈 수리 후 첫 풀런): **몬스터 정렬 수리는 완벽**
(판정 C 최대시작 4.6분·>5분지각 0). c373 66.3분 → **48.3분(−18분)**. 그러나
예측 42.2분엔 못 미쳤고, 오너가 타임라인에서 "중간 것들이 늦어져 makespan을
늘림 + 의존성 없는 시나리오가 큐를 못 잡음"을 지적 → fold로 정범 분해:

- **48.3 = 46.3(진짜 무거운 하한) + 2.0(worksteal 꼬리 붕괴)**. makespan 정범 =
  `scr-repo-borrow-coverage` — **2분짜리 라이트가 46.3분에 시작**해 makespan 정의.
  apigateway(45분)·vpn-tunnel(44분)·gen-vpc-endpoint(36분)·direct-connect(35분)도
  전부 2분짜리 의존성-없는 라이트가 30~46분에 시작.
- 뿌리 = **worksteal의 유휴 워커 shutdown**. worksteal `schedule()`은 수집 순서를
  워커별 연속 블록으로 **전부 선분배**(pending 비움) → 초반 라이트 소진 후 유휴
  워커가 "훔칠 게 없으면" **종료(`check_schedule`의 `node.shutdown()`)**. 나중에
  무거운 게 끝나 일이 풀려도 집을 워커가 없어 라이트 꼬리가 살아남은 소수 워커
  뒤로 밀린다. worksteal이 고치려던 "진행 N≈대기 N" 꼬리를 shutdown으로 재생산.
- "진행 N≈대기 N"은 스케줄러 무관 = xdist **워커당 2-깊이 버퍼**(worksteal
  `MIN_PENDING=2`, load도 노드당 min 2). 살아있는 워커가 (1실행+1대기)를 쥐니
  진행=대기=활성 워커 수. **이전 load→worksteal 전환(b4b9bc7e)이 이걸 고치려
  했으나 실패한 이유** — dist 모드로 못 없애는 구조. 게다가 그 판단은 워커수
  버그(_worker_count=0, 2026-07-11 수리)로 인터리브가 죽어 load 초기 청크가
  직렬화된 오염 런 근거였을 가능성.

**수리 = --dist=load --maxschedchunk=1** (local_run + console2, conftest 정렬은
`_interleave_for_workers`로 페어링): load `check_schedule`은 **글로벌 pending
풀을 유지**해 워커 완료 시마다 리필하고 `node.shutdown()`은 **pending이 빌 때만**
→ pending이 남는 한 워커가 안 죽고 **빈 워커가 다음 대기를 즉시 집는다**(오너가
말한 "큐 잡고 들어가기", work-conserving). load 초기 청크=워커당 2
(`max(node_chunksize,2)`)라 [heavy,light] 인터리브면 **상위 n 몬스터 전부
offset 0(t=0)**, 나머지는 풀에서 동적 리필(시뮬레이션 실측: interleave 24/24
offset0 vs desc 직렬 offset1). worksteal용 라운드로빈은 대안 경로로 보존 —
**dist 모드와 정렬은 한 쌍**. 예측(simulate_schedule=이상적 LPT)이 work-conserving
load와 더 잘 맞아 예측≈실측도 개선. **다음 풀런에서 라이트 꼬리 조기화 실측 대기.**
- 남은 큰 지렛대(백로그): 무거운 DB 하한 46분 자체 단축(provision·병렬) + durations
  과소추정(mariadb 실측 46 vs 커밋 40) 커밋본 fold + prereq-blocking 워커 점유
  (gen-cloudml-chain이 ske 대기하며 워커 잡음 → skip-when-not-ready 검토).
## 배치 ① 4xx→2xx 수리 4종(+filestorage VM배선) + 블로커 2종 라이브 재확인 (2026-07-13, branch api-test-coverage-gzukh0)

> conf: 0.5 · seen: 2026-07-13 · obs: 1 (offline+read-only-live; heavy 2xx는 다음 콘솔 런 판정)

인계된 "①배치 코드로 즉시 노려볼 4xx" 중 **코드로 안전 수리 가능한 3종**을 반영
(heavy 라이프사이클이라 실 2xx는 SCP_RUN_HEAVY 콘솔 런에서 판정):

1. **DBaaS setblockstoragesize (mysql·postgresql) = OS 롤 그룹 리사이즈 금지.**
   `database-mysql-cluster`/`database-postgresql-cluster`(scenarios.json)의 create
   body는 block_storage_group을 **OS 롤 1개**만 만든다. bsg_id=block_storage_groups
   [0]=OS를 리사이즈하면 400 `Dbaas.ValidationError.InvalidBlockStorageRoleType`
   (subops-full run-923a 라이브 확정과 동일 클래스). 수리: subops-full의 검증된
   패턴 이식 — add-block-storages(role_type:DATA)→settle-poll→
   `capture_soft bsg_data_id=$.instance_groups[0].block_storage_groups[1].id`→
   그 DATA 그룹을 resize(size_gb 208=104×2). instance_group_id는 mysql은 미캡처라
   capture-block-storage-group에 추가(pg는 기존 capture-instance-group에 존재).

2. **eventstreams showrequest = async 202의 request_id만이 유일 경로.**
   어떤 list/collection GET도 request_id를 노출하지 않는다(read-only es-read
   라이프사이클 _note). es-create(202 AsyncResponse)가 `$.request_id`를
   capture_soft하므로, **es-wait 직전에** `GET /v1/requests/{request_id}` 스텝
   삽입 → Kafka 프로비저닝이 나중에 async-FAIL해도(2026-07-13 4연속 FAILED, PF
   후보) 202 수락 순간 request 레코드는 존재하므로 showrequest는 실 2xx 획득.

3. **cachestore set-commands = maxmemory-policy는 modifiable 아님.**
   listcommands(GET /v1/clusters/{cluster_id}/commands) 응답
   `{contents:[{id,name,modifiable,applied_value,description}]}`(api_docs
   response_example). 하드코딩 `{name:maxmemory-policy,id:""}`는 modifiable이
   아니라 400. 수리: `where_prefix modifiable=true`로 실 커맨드 id+name+
   applied_value 캡처 → modifycommandrequest {commands:[{id,name,new_value=
   applied_value}]}(no-op 되돌림, set-parameter-values 관용). **미검증**:
   modifiable 필드가 문자열 "true"인지 boolean true인지(where_prefix는 startswith
   문자열 매칭) — 콘솔 런에서 정정. 리터럴 폴백이라 무회귀.

**라이브 read-only 재확인으로 인계 낙관론 정정 (여전히 블로커, 코드 수리 불가):**
- **kms updatemanagedkeydescription/showmanagedkey**: `GET /v1/managed-kms/transit`
  → 200 `{count:0, keys:[]}` (2026-07-13). managed key는 system-managed(create API
  없음)이고 계정에 0개 → 실 id 확보 원천 불가. **영구 블로커**(2026-06-23 이래 불변).
- **filestorage setaccessrule**: `object_id`에 실 VM 요구. 단독 라이프사이클
  (storage__filestorage.json)은 VPC/VM-free라 가짜 UUID → 404
  `VirtualServer.VirtualServerNotFound`(참조자원 부재 fail-fast, 형식/권한 오류
  아님; `GET /v1/servers`→200 `{servers:[]}`, `/v1/virtual-servers`→403 실측).
  **수리 (2026-07-13, 오너 지시 "VM에 dependency 걸어 테스트"):** gen-heavy-vs-netops가
  이미 ACTIVE VM({server_id})을 상비하므로 거기에 filestorage NFS 볼륨 create +
  setaccessrule(add/remove) optional 그룹(`fs-vm-access`) 편입 — object_id=
  {server_id}로 실 2xx 노림. **주의: 모든 filestorage 스텝에 `service:filestorage`
  필수** (`/v1/volumes`가 virtualserver block-volume 호스트와 경로 충돌; service
  태그가 filestorage 호스트로 라우팅; 토큰도 `fs_volume_id`로 분리). remove로
  규칙 되돌린 뒤 cleanup이 볼륨 삭제(잔존 규칙이 deletevolume 400 유발 가능).
  다음 heavy 콘솔 런이 2xx 판정.
- **cloudmonitoring show-event-policy**: `GET .../event/v2/event-policies` → 400
  `InvalidInputValue`(resourceType/productResourceId 필수), 등록 모니터링 리소스 0.
  이벤트 정책 생성 자체가 등록 INSTANCE 필요 → show reads의 실 id 확보 불가.
  **블로커**(2026-09 단종 예정, 깊은 투자 금지).

## filestorage replication 400 = source-side 제약 + DR 별칭 크레딧 버그 (2026-07-13, 오너 질문)

> conf: 0.7 · seen: 2026-07-13 · obs: 2 (2026-06-24/07-08/07-09 라이브 + 이 세션 코드 추적)

**증상**: 대시보드에서 `setvolumereplication`(PUT /v1/replications/{replication_id})
+ `deletevolumereplication`(DELETE 동경로)이 계속 **400**.

**원인 A — 제품 제약(정상 400)**: 교차리전 복제는 볼륨 2개를 만든다 —
source(kr-west1, `purpose=original`) + DR replica(kr-east1, `purpose=replication`).
복제 **정책 변경(set)·삭제(delete)는 source 측에서 금지** → 400
`filestorage.BadRequest.Invalid.volume.purpose`("Check the volume purpose."). 게다가
deletevolumereplication은 **paused 선행**을 요구. `purpose`는 VolumeCreateRequest에
없는 서버관리 상태필드(생성 body는 name/protocol/type_name뿐)라 create 보강으로
못 고친다 — 어느 **리전 호스트에서 부르느냐**가 본질. 실 2xx 경로(오너 절차,
2026-07-09 end-to-end 라이브 실증): DR(복제본) 리전에서 ① set-replication-dr
policy=paused(202) ② delete-replication-dr(202, 레코드 ~20s 내 양 리전 소멸,
source purpose original→none) ③ snapshot 정리 ④ 양 볼륨 삭제. DR 스텝은
`filestorage-dr` service 별칭 = `SCP_SERVICE_HOSTS='{"filestorage-dr":
"https://filestorage.kr-east1.e.samsungsdscloud.com"}'` 런타임 설정 필요(미설정 시
region-template 호스트가 NXDOMAIN).

**원인 B — 크레딧 버그(이 세션 발견·수리)**: `_catalog_key_for`/validate는
`(method, path, service)`로 카탈로그 키를 찾는데 카탈로그 service는 `filestorage`고
DR 스텝은 `filestorage-dr` 별칭 → **매칭 실패 → `_ck` None → `_record_smoke` 미호출**.
즉 DR 측이 202를 내도 카탈로그 키에 크레딧이 안 되고, 크레딧되는 건 source 측
(항상 400 프로브)뿐 → 세 엔드포인트(set/deletevolumereplication + DR replica의
deletevolume)가 **영구 400 고착**. **수리**: `_canon_service()` — 리전 라우팅 별칭
`<service>-dr`을 base service로 정규화(실 SCP 서비스명에 `-dr` 없음 → 무모호).
engine `_catalog_key_for`(크레딧 경로) + validate(경고) 양쪽 반영. 검증: 세 키가
`storage/filestorage/{setvolumereplication,deletevolumereplication,deletevolume}`로
해석됨(단위 확인), validate 경고 6→3, offline 91 pass. 다음 heavy 콘솔 런
(filestorage-dr alias 설정 시)이 이 세 키의 실 2xx 크레딧을 판정.

## read-only/dependent 인지 순서 = _order_for_load (2026-07-13, run-19a5 오너 설계)

run-19a5(load 첫 관측)에서 오너가 지적: gen-volume-type·*-reads·servicewatch 등
**선행자원 없는 읽기전용이 30~40분에야 시작**. 원인 = interleave가 가장 가벼운 n개
(read-only 포함)를 heavy와 페어링해 **heavy 뒤에 strand** → heavy(최대 40분)가 끝나야
시작. worksteal→load 전환은 이걸 안 고침(둘 다 heavy 뒤 묶임).

오너 모델("다 pending 넣고 dequeue 시 dependency 보고"): 이상적이나 **xdist는
dequeue 시점에 테스트의 dependency를 못 본다**(테스트 불투명) → dependency 순서를
**수집 순서에 인코딩**하는 게 achievable. `_order_for_load` (3-tier):
- pair-first(t=0) = heavy(provider·병목) 상위 n
- pair-second(strand) = 가장 가벼운 **non-read-only** n개 (read-only는 여기 안 씀)
- global pending 앞 = **read-only(선행자원 무)** → 빈 워커가 초반에 집음
- global pending 뒤 = **dependent(prereq)** → provider 도는 뒤에 디스패치

heavy를 밀지 않아 makespan 무영향, read-only만 40분→초반으로 당겨진다(t=0은 heavy를
밀어내야 해 makespan 리스크 → "초반"으로 안전 채택). `_is_read_only`(전부 GET)·
`_has_prereq`(dependencies.json prerequisites)로 분류. offline 테스트 3종 신설.
## 큐 누수 = 중단 런 + 스위퍼 사각지대 + API 설계 갭 → ledger-reclaim 패스로 근본 수리 (2026-07-13)

오너 관측: 콘솔 Queue 목록에 regrq* 5개 잔존(06-20·07-12·07-13), verify_clean은 0으로 봄.

**3겹 원인:**
1. **중단 런 누수**: 큐 create(201)까지 성공→id 캡처·registry track까지 됐는데
   run이 delete 스텝 전에 죽음(오늘 ff1c abort 등). 생성 실패가 아니라 **fail 난
   API 호출이 없어** 리포트는 전부 정상 — "정상인데 자원이 남는" 정확한 메커니즘.
2. **API 설계 갭 (conformance)**: listqueue(v1.1 배포본)는 `{count, queue_urls:[URL]}`
   — 이름만 주고 **id를 안 줌**. delete/show/attributes는 전부 32자 큐ID 요구,
   **이름→ID resolver 없음**(check-duplication은 `{result:true}`, 계정ID 조회는 404,
   `/v1/queues/{account}/{name}` 경로는 403 권한없음). 문서의 v1.0(`queues:[{id}]`)은
   어떤 버전 헤더로도 도달 불가. → **생성 시 id를 안 붙잡은 큐는 API로 회수 불가한
   고아**. id 기반 `DELETE /v1/queues/{32hex}`는 정상(404 on gone, VALIDATED).
3. **스위퍼 사각지대**: `_select(queueservice,/v1/queues)`가 queue_urls를 못 파싱해
   항상 [] → 큐 스윕 패스 무동작(6/20 큐 생존 이유). 게다가 엔진이 create마다
   `reports/registry/*.jsonl`에 **RESOLVED delete_path(/v1/queues/<실제id>)**를
   영속 기록하는데 **reconciler가 이 매니페스트를 한 번도 안 읽었다**(registry.py
   주석 "reconciler globs …"는 미구현 — 매니페스트가 dead weight).

**근본 수리**: `cleanup.reconciler._pass_ledger_reclaim` — reports/registry/*.jsonl을
소비해 기록된 delete_path(실제 id)로 삭제. 큐뿐 아니라 **모든 id-주소 자원의 중단-런
고아**를 회수한다(listing이 못 주는 것도 매니페스트가 앎). 안전장치: mtime <
`_LEDGER_MIN_AGE_S`(기본 900s) shard는 활성 런 것이라 건너뜀(Hard Rule 4);
404=회수됨(prune); 409=다음 라운드 재시도(fixed-point 수렴); 모두 gone인 shard만
파일 삭제. _TAIL_PASSES 선두 등록. 회귀: tests/offline/test_ledger_reclaim.py 6건.

**기존 5개 한계**: 이 컨테이너 ledger엔 그 id가 없음(원 실행은 console2 서버 머신).
서버 머신에서 갱신된 reconciler 실행 시 그쪽 shard가 남아있으면 회수됨; 아니면
콘솔 "서비스 해지" 버튼이 유일 경로(콘솔 백엔드는 서버측 이름→id 해석).

## DBaaS 클러스터 UNKNOWN은 sync-state로만 복구 — delete 직전 재동기화 필수 (2026-07-13, 오너 관측)

> conf: 0.8 · seen: 2026-07-13 · obs: 3 (run-923a/c373 라이브 + 오너 콘솔 관측)

**사실**: DBaaS 클러스터(mysql/postgresql/mariadb/epas/cachestore)는 async subop
(set-parameters·patch-minor·kernel-upgrade·resize·add-block-storages)이 202 수락
후 비동기 실패하면 `service_state=UNKNOWN`으로 추락한다. UNKNOWN에서는 후속
mutating op(삭제 포함)이 400 InvalidState로 거부된다. **복구 유일 수단 =
`POST /v1/clusters/{cluster_id}/sync-state` (body {})** — 콘솔의 "synchronize"
버튼과 동일. 라이브 실측(c373): sync-state 10회 전부 202 → 상태 `SYNCHRONIZING`
→ RUNNING 복귀. **DELETE 재시도로는 절대 복구 안 됨**(retry_on_status 400 x20이
있어도 상태를 안 바꾸므로 20회 전부 400) → 빌링 클러스터 잔존.

**로직 규약 (delete 직전 필수)**: `pre-delete-sync-state`(POST sync-state,
optional, 4xx 관용) → `wait-pre-delete-settle`(GET, poll until RUNNING/ACTIVE/
AVAILABLE, give_up_status [400,404], timeout 600, terminal-bad는 엔진 기본 조기
종료) → `delete-cluster`. 건강한 클러스터엔 무해(잠깐 SYNCHRONIZING 후 복귀).
mid-lifecycle sync(param 실패 직후)만으로는 부족 — 마지막 sync 이후의 subop이
UNKNOWN을 유발하면 delete가 그대로 맞는다. 반영: database__subops-full.json 5엔진
+ scenarios.json database-mysql-cluster/database-postgresql-cluster (총 7 teardown).
**SKE(container-ske-cluster-nodepool)는 제외** — 쿠버네티스라 /sync-state
엔드포인트 없음(다른 상태머신).

## cleanup 필드는 실패-안전망일 뿐 — 성공 경로 teardown은 명시적 DELETE 스텝이어야 (2026-07-13, 오너 큐 누수 관측)

> conf: 0.9 · seen: 2026-07-13 · obs: 2 (afa8 큐/볼륨 잔존 + 코드 추적)

**엔진 규약 (engine.py)**: `_teardown()`(등록된 cleanup 발화)은 **`except` 경로에서만**
호출된다 (line 1621-1624). 성공 완주 시 `_finish(..., "passed")`(line 1628)는
teardown을 부르지 **않는다**. 즉 **정상 런의 자원 정리는 명시적 DELETE 스텝이
전담**하고, 스텝의 `cleanup` 필드는 라이프사이클이 중간에 실패했을 때만 도는
안전망이다.

**결함 클래스**: create 스텝(POST)이 `cleanup`(DELETE)만 갖고 **매칭되는 명시적
DELETE 스텝이 없으면**, 그 자원은 **매 성공(green) 런마다 잔존**한다 — 리포트는
전부 정상(실패한 API 호출 없음)이라 조용히 샌다. 라이브 실측(afa8): FIFO 큐
`create-fifo-queue`(474a16423a79…) + gen-heavy-vs-netops의 NFS 볼륨이 콘솔에 잔존
(resource-deleted 이벤트 없음). 반면 명시적 delete 스텝이 있는 main 큐/서버는 정상 삭제.

**수리**: (1) application-queueservice-queue에 `delete-fifo-queue` 명시적 스텝 추가.
(2) gen-heavy-vs-netops에 `fs-delete-volume` 명시적 스텝 추가(내가 이 세션에 넣은
fs-create-volume이 cleanup-only였음 — access-rule remove 뒤 삭제). (3) **validate.py
가드 신설**: create+cleanup인데 매칭 명시적 DELETE 없으면 WARN (쿼리스트링 무시).
남은 경고 3건은 latent(create 블록: vs-image·scr-repo)/singleton(networking
firewall-logging-storage는 "이미 존재하면 create 실패"라 계정당 1개 상한 — 누적
아님, 삭제 시 동시런 레이스 위험이라 보류) — 케이스별 오너 리뷰 대상.

## 목적특화 스케줄러 native_runner — xdist 대체 (2026-07-13, 오너 지시)

xdist는 CPU-bound 테스트 병렬용 범용 라이브러리라 I/O-bound 라이프사이클(의존성·
쿼터·async 대기)엔 미스매치 — 우리가 정렬 인코딩·strand 제거·MIN_PENDING 제안으로
우회하던 4한계(수집순서 디스패치·MIN_PENDING 버퍼→꼬리 붕괴·쿼터 per-worker→400
레이스·의존성 무지)의 근원. engine.run_lifecycle이 이미 pytest 분리라, 얇은 스레드풀
스케줄러(regression/scenarios/native_runner.py)로 직접 구동:
- 동적 LPT pop(빈 슬롯 긴 것 먼저) · 큐 빌 때만 워커 종료(꼬리 붕괴 제거) ·
  **공유 Budget(스레드-안전 RLock)** = 계정-전역 쿼터 조율(private-dns/vpc 400
  레이스 제거) · dependent 후미.
- 시뮬레이션(tools/scheduler_sim, 실 durations): **native 70.1분/쿼터400=0 vs
  xdist-load 89.9분/400=4**, 꼬리 활성워커 native 4 vs xdist 1.
- **opt-in `SCP_NATIVE_RUNNER=true`** — local_run/console2가 pytest 대신 `python -m
  regression.scenarios.native_runner` Popen(별도 프로세스=중단 kill 동일). xdist 폴백
  유지, 라이브 검증 후 기본화. 정렬 해킹(_order_for_load 등)은 xdist 전용이라 native엔
  불요(동적 pop이 버퍼-갇힘 원천 제거).
- 검증: 시뮬 + offline 3종(test_native_runner) + Budget 스레드-안전. 라이브 대기.
  설계·V2 백로그: docs/working/plans/NATIVE-SCHEDULER.md.

## poll not-ready 게이트 — until 미충족 타임아웃은 스텝 실패 (2026-07-13, 오너 "수리해")

`field`/`until`(또는 `until_status`)이 있는 settle-poll이 그 조건을 **못 채우고**
타임아웃하면, 종전엔 마지막 CREATING/HTTP-200 응답을 조용히 반환 → `expect_status
[200]`을 통과 → 뒤 스텝이 **준비 안 된 자원 위에서 진행**됐다(masked-defect 클래스,
TERMINAL-BAD의 자매 결함). 오늘 서브넷 활성이 wait-subnet 타임아웃(180s)보다 느려
VM이 ERROR로 생성된 사건이 이 결함의 라이브 발현.
- 수리(regression/scenarios/engine.py): 폴 루프가 in-loop 성공 반환 없이 끝나면
  (deadline OR operator stop_polling), 마지막 resp가 `until`/`until_status`를
  충족했는지 확인 → 미충족이면 `resp._poll_timed_out=<last state>` 마커. 호출측이
  `_terminal_bad`과 나란히 이 마커를 보고 `status_ok=False`로 실패 분류. assert 메시지에
  사유 포함. **refire 폴(타임아웃이 성공)과 `poll.allow_timeout` escape hatch는 제외.**
- 조사: 활성 시나리오 404 poll = until_status 54 + field/until(비어있지 않음) 350 +
  refire 8. **field-without-until·allow_timeout·inline-poll-on-create = 0** →
  실 시나리오는 create(cleanup 등록)와 wait(GET+poll)가 항상 분리라 wait 실패가
  이미 생성된 자원을 누수시키지 않음(teardown이 회수). 게이트는 안전하게 전 404 폴에 적용.
- stop_polling 재의미: 종전 "타임아웃처럼 처리(=조용히 통과)" → 이제 "타임아웃처럼
  처리(=미충족이면 실패)". operator가 대기를 포기해도 자원이 준비되는 건 아니므로
  정직한 실패. 이미 수렴한 폴만 통과.
- offline 회귀: 577 passed(test_command_channel·test_poll_terminal_bad 갱신 —
  terminal_bad:[]는 조기탈출만 끔, 폴 미수렴은 여전히 실패). 유일 실패는 httpx 미설치
  console2 테스트(환경 이슈, 무관).

## priority_order 정제 — 진짜 inter-lifecycle 의존만 demote (2026-07-13, 오너 지시)

native_runner.priority_order가 `prereq` 있는 것을 전부 dependent-last로 미뤄, 긴
soft-의존 태스크(container-ske-cluster-nodepool 34.6m)가 t=16분에야 착수 →
makespan 50.7분(꼬리 3분 손해). 그러나 SKE의 prereq(vpc/subnet/security-group/
keypair/filestorage-volume)는 전부 **공유-인프라/자체-생성** kind라 다른
라이프사이클을 안 기다린다(soft). 수리: `_SHARED_INFRA_KINDS` 집합을 두고
`_true_dependents()`가 **비-공유 kind를 가진** 라이프사이클만 골라 demote한다.
- 활성 190개 중 진짜 inter-lifecycle 의존은 **2개뿐**: gen-cloudml-chain·
  cloud-ml-write-coverage(둘 다 ske-cluster+container-registry = 다른 라이프사이클
  산출물 필요). 나머지 9개 "dependent"는 전부 soft(공유-인프라만).
- 효과(gantt_sim 실측): SKE가 t=0 착수 → makespan **50.7→47.7분**(=최장 태스크
  gen-heavy-aimlops 47.7, 이론 하한 도달). cloudml(진짜 의존)만 후미(23.5→43.8분,
  임계 경로 밖). 17.9× 가속.
- gantt_sim.load()도 동일 `_true_dependents()` 재사용(러너-시뮬 일관성).
- offline: tests/offline/test_native_runner.py에 test_priority_long_soft_dependent_
  not_demoted 추가(긴 soft-의존 앞단, 진짜 의존만 후미).
- **배경 결정**: 오너가 "앞단 VPC provisioning은 그대로, 이후 시나리오 배치만"을
  제안. native_runner가 이미 그 경계(provision_shared_vpc 재사용, dispatch만 교체)라
  스코프 일치. xdist 정적 배치도 47.7 가능하나 durations 드리프트에 취약(정적 배치는
  런타임 재조정 불가) — 동적 pop이 구조적으로 면역이라 native 유지 + 이 정제로 낙착.
  xdist 경로는 폴백 유지.

## VPC 세마포어 시드 — 상주 VPC를 계정 실사용으로 반영 (2026-07-13, 오너: "세마포어로 b")

native_runner의 공유 Budget은 이미 kind별 세마포어(reserve=atomic check-inc, vpc 캡 5).
그러나 상주 VPC(공유 10.124/20 + net-A 10.130/20 + net-B 10.141/20, provision_shared_vpc가
Budget 밖에서 생성)가 세마포어에 안 잡혀, 시드 없으면 세마포어가 캡 5를 통째로 비었다고
보고 self-create를 무제한 admit → 상주 3 + self-create N이 캡 초과 시 400(IB-047 구조).
- 수리: run() 시작에 `budget.sync("vpc", live_count("vpc"))` — 계정 실사용(상주 3개 +
  **이전 런 잔재까지**)을 소비로 시드. self-create는 남은 슬롯(5−실사용)만 reserve,
  초과분은 조율 skip(400 아님). live_count 조회 실패 시 미시드(종전 동작) 폴백.
- **adopter 27개는 세마포어 무오염**: adopt:vpc POST는 engine.py:1215 `continue`로
  create+reserve를 통째 건너뜀(확인). 순수하게 '새 VPC를 만드는' self-create만 캡에 걸림.
- 이게 (b)=세마포어 시드이고 (a)=2단계 직렬 분리보다 얇다: 세마포어가 "여유 있으면 병렬,
  넘치면 막음"을 동적으로 → 직렬화는 세마포어의 rigid 특수케이스. NATIVE-SCHEDULER.md
  V2 백로그 "live quota sync"를 여기서 실현.
- self-create 실측: heavy-shared-networking(0–15.2m)·networking-vpc-subnet(7.8–15.3m)
  동시 2개 → 상주3+2=동시5(캡5). 시드로 6번째(잔재 포함) 400을 원천 차단.
- offline: test_vpc_semaphore_seeded_from_live_leaves_only_residual_slots (시드 3 →
  self-create 5개 중 2개만 admit·3개 조율 skip; 미시드는 5개 다 admit=구멍 재현).

## VPC-생성자 선두 배치 + 슬롯 대기-재실행 (2026-07-13, 오너 지시)

VPC 생성 슬롯은 희소(캡5 − 상주3 ≈ 2). self-create 라이프사이클은 슬롯을 자기
구간 전체 동안 점유하므로, 늦게 착수하면 슬롯을 늦게까지 붙잡아 다른 VPC-생성
시나리오가 대기한다. 두 가지 반영:
- **선두 배치**(priority_order): `_is_vpc_creator(lc)`(adopt 없는 POST /vpcs 스텝)를
  키에 추가 — (진짜 의존 후미, **VPC-생성자 선두**, LPT). VPC-생성자가 t=0(슬롯 빔)에
  먼저 잡고 일찍 반납 → 슬롯 가용성 최대. 실측: networking-vpc-subnet 착수 7.8→**0m**,
  반납 15.3→**7.5m**(8분 일찍). 워커 30 ≫ 무거운 것이라 makespan 바닥선(47.7)은 불변.
  xdist 하드코딩 priority_first(vip-nat·vpc-subnet)를 스텝에서 파생 → 드리프트 견고.
- **대기-재실행**(worker): VPC-생성자가 슬롯 부족으로 예산-skip되면 skip 기록이 아니라
  `_VPC_WAIT_POLL`(5s)로 대기 후 재실행(`_VPC_WAIT_TIMEOUT` 1800s 상한). 예산-skip은
  create 이전(created=0, 토큰 반납)이라 재실행 멱등. 선두 배치로 이 대기는 생성자 수 >
  여유 슬롯일 때만 드물게 발동. skip-not-fail → wait-then-run으로 커버리지 손실 0.
- 설계 판단: 오너의 "세마포어로 하면 b" + "대기했다 실행"을 종합 — 공유 Budget이
  세마포어이고, 대기는 러너 레벨 재시도 루프로(엔진 무변경·이중 세마포어 없음).
  (a) 2단계 직렬 분리 불요: 선두 배치가 슬롯 점유창을 앞으로 압축, 초과분만 동적 대기.
- offline: test_vpc_creator_detection_and_priority_first, test_vpc_creator_waits_and_
  retries_on_budget_skip.

## net VPC 세분화 provisioning — net-A/net-B 독립 조건 (2026-07-13, 오너 지시)

종전 `_needs_net_vpcs()`는 단일 bool(vpc#a OR vpc#b 아무거나 있으면 A·B 둘 다 생성).
오너 우선순위 모델: 공유 VPC(1순위, VPC 의존 있으면 무조건) → net-A(2순위, vpc#a
사용자 있으면) → net-B(3순위, peering/vpc#b 사용자 있으면 — peering은 vpc#a·vpc#b
둘 다라 자동 A·B). 이후 self-create가 최우선 스케줄로 슬롯 조기 점유·반납.
- 수리: `_needs_net_vpcs()→_needed_net_vpc_tags()`(shared_infra) — vpc#a면 'a',
  vpc#b면 'b' 태그 집합 반환. `provision_shared_vpc(need_net_vpcs=)`가 bool|iterable
  수용(True→{'a','b'} 하위호환, tuple→그 태그만, False/()→없음), 루프가 태그별 생성.
- 효과: 부분 선택에서 안 쓰는 net VPC를 안 만들어 슬롯·시간 절약. 예) peering+net-B
  사용자(dc-routing·fw) 제외 시 tags=('a',) → net-B 미생성 → 상주 3→2 → self-create
  여유 슬롯 +1(하드캡5: 2→3, 보수캡 per_run_vpc_cap4: 1→2). 풀런은 A·B 다 필요 →
  종전과 동일(효과 0). vip-nat만이면 ('a',), dc/fw만이면 ('b',).
- net 상주 조기 teardown(사장님 2번째 아이디어)은 보류: 풀런은 peering이 A·B를
  31.6분까지 잡아 self-create(≤15분 종료) 뒤엔 슬롯 수요가 없어 이득 미미 + ref-count
  mid-run teardown 복잡도 최대. 부분선택 최적화 필요 시 레버.

## 공유 TGW adopt — TGW 계정 캡 3 레이스 제거 (2026-07-13, 오너 "TGW adopt(B)")

TGW는 계정 캡 3(knowledge: "max 3 Transit Gateways per account"). self-create가
3개(vpc-transit-gateway-children·gen-private-nat·heavy-shared-networking) → 헤드룸 0,
이전 런 잔재 1개면 4번째 create가 exceed-max. 라이브 실측(풀런): gen-private-nat이
create-transit-gateway(REQUIRED)에서 하드폴("2 API, 0 자원").
- **역할 분석**: TGW를 진짜 "소유"하는 건 vpc-transit-gateway-children 하나뿐(TGW+
  자식 CRUD 주인공). gen-private-nat은 private-nat 전제조건, heavy-net은 다운스트림
  연결 없는 순수 커버리지(children과 중복). → 뒤 둘은 공유 TGW adopt.
- 수리(공유 VPC 패턴 재사용): engine `_ADOPT_SHARED["tgw"]="shared_tgw_id"`,
  adopt-active 맵에 tgw($.transit_gateway.state), `provision_shared_vpc(need_tgw=)`가
  공유 TGW 1개 생성(account-level, no-wait — 첫 adopter의 _ensure_adopted_active가
  게이트)+teardown, `_ENV_SHARED_TGW`, shared_infra `_needs_shared_tgw()`+emit.
  시나리오: gen-private-nat(create/delete), heavy-net(tgw-create/delete/settgw)에
  adopt:tgw. children은 self 유지.
- **PUT-skip 신설**: adopt 스텝의 PUT(set)도 skip(공유 자원 mutate 방지) — 종전엔
  POST/DELETE/GET만 skip. 활성 adopt+PUT 스텝은 heavy-net settransitgateway 하나라
  무회귀. set 커버리지는 children이 소유.
- 효과: 동시 TGW 3→2(1 shared + 1 self children) → 캡3 헤드룸 1. 공유 TGW provision
  실패/미트리거 시 adopter는 self-create 폴백(현행 동작=무회귀).
- 라이브 미검증(다음 풀런 판정): 공유 TGW에 gen-private-nat이 vpc-connection 생성/
  삭제(shared VPC↔shared TGW), teardown 시 connectionless 확인.
- offline: tests/offline/test_shared_tgw_adopt.py (5종: 등록·감지·children-self·
  adopt-skip(create/set/delete+시딩)·self-create 폴백).

## VPC 세마포어 예약 leak 수리 — 시드+반납 (2026-07-14, 오너 실측 "예약 안 풀림")

풀런 teardown에서 자원을 다 지웠는데도 "VPC 예약 5·여유 0"이 안 풀리는 관측 → 두 버그:
1. **해피패스 반납 누락**(engine.py): self-create VPC가 자기 delete-vpc 스텝(해피패스)
   에서 cross-process sem(_release_vpc_for_path)만 반납하고 **in-process budget은 반납
   안 함** → created→deleted된 VPC가 런 내내 예약을 붙잡음(native 러너의 공유 budget).
   수리: 그 지점에서 `_vpc_id_of(path) and reserved["vpc"]>0`이면 `budget.release("vpc")`.
2. **시드 과다**(native_runner): 시드를 raw `live_count`로 했는데, sync는 baseline을
   통째로 세팅+절대 반납 안 해서 **이전 런 잔재까지 시드하면 여유 0에 영구 고정** →
   self-create가 런 내내 막힘. 수리: **상주 개수**(shared_ctx의 shared_vpc+net-A+net-B,
   결정론)로 시드. 잔재는 시드 흡수(슬롯 영구 점유)가 아니라 pre-run 스윕 대상; 진짜
   캡 초과는 create 400 → skip-not-fail(종전 동작).
- adopter는 delete가 adopt-skip돼 반납 지점에 안 옴 → reserved["vpc"]=0, 무영향.
- offline: test_selfcreate_vpc_releases_budget_on_happy_delete(반납 후 used["vpc"]==0),
  test_vpc_semaphore_seeded_from_residents_leaves_only_residual_slots(상주 시드).

## poll not-ready 게이트 — gone-poll·transient 제외 (2026-07-14, 오너 실측)

masked-defect 게이트(poll 미수렴 → 스텝 실패)가 과잉 발동한 두 케이스:
1. **gone-poll**(until_status에 404 = 자원 소멸 대기, 55개): teardown 정리라 캡 안에
   안 사라져도(mariadb ~90분 drain > 900s 캡) 실패가 아니라 sweep/cleanup 백스톱.
   → until_status에 404 있으면 게이트 제외. masked-defect(다운스트림이 준비 안 된
   자원 위 진행)는 create-side wait(field/until·non-404 until_status, 351개)에만 유효.
2. **transient 429/5xx**: 타임아웃 시점 마지막 응답이 429/5xx면 상태를 '못 읽은'
   unknown이지 not-ready 확정이 아님 → 마킹 제외(heavy-net wait-subnet 지속 429가
   자원 멀쩡한데 'never converged'로 오분류). http_client가 429 이미 재시도하므로
   여기 도달 = 지속. (단 429는 expect_status 밖이면 스텝은 여전히 실패 — 사유만
   정직하게. 근본 완화는 동시성/rate-limit 튜닝.)
- offline: test_gone_poll_timeout_does_not_fail_step, test_transient_429_at_
  timeout_not_classified_not_ready.

## reconciler 종료 정책 — VPC 캡 클리어 후 leaf drain은 리포트, 안 기다림 (2026-07-14, 오너 지시)

각 라이프사이클이 자기 자원을 teardown하는데도 run-end account 스윕이 필요한 이유:
(1) 중간 실패 라이프사이클의 부분-잔여, (2) 비동기 삭제 미확정(drain lag),
(3) 공유 인프라(shared VPC/subnet/net-A/B/TGW)는 per-test가 안 지움, (4) 부산물/고아
(log group·launch-config·server-group·keypair·snapshot), (5) owner-tag 최종 안전망.
→ 정상 런이면 스윕이 지울 게 거의 없어야 정상.

**80분 스윕의 정체**: mariadb 클러스터(~90분 late-drain)가 매 라운드 `DELETING`으로
잡혀 genuine=0/inprog≥1 → `grant-inprog`가 8라운드를 다 소모. 그러나 그 클러스터는
subnet 포트를 진작 반납한 **late 내부 drain leaf**(subnet/VPC 이미 소멸)라 아무것도
막지 않음. 오너 지시: "vpc 모두 삭제되고 부산물 정리되면 끝. 남은 자원은 이슈로 리포트".

**반영**(cleanup/reconciler.py):
- `_owned_vpcs_present(client)`: 소유 VPC(=`_is_deletable`, 캡 대상)를 직접 LIST로
  카운트(리스트 실패 시 1=present, fail-safe). `_list_all`은 에러를 []로 삼켜 오판하므로 직접 GET.
- main() grant-inprog 분기: 소유 VPC==0이면 leaf drain에 라운드를 더 주지 않고 STOP +
  리포트. 소유 VPC가 남으면 기존 grant 유지(2026-07-03 TGW mid-deletion이 VPC를
  409로 막는 인시던트 — 거기선 inprog 항목이 실제 blocker).
- `_leftover_report(client)`: 멈출 때 남은 소유 자원을 read-only(verify_clean.scan_owned)
  로 서비스별 열거 → console2/사람이 다음 end-sweep이 수렴시킬 대상을 봄.
- offline: test_owned_vpcs_present_counts_owned_only, test_owned_vpc_probe_failure_
  assumes_present, test_main_leaf_drain_stops_and_reports_when_no_vpc,
  test_main_keeps_granting_while_a_vpc_is_present.

## C3 커버리지가 81.7%에서 정체된 이유 + 레버 (2026-07-14)

**C3는 누적(cumulative) 지표** — 런이 C3를 올리려면 "한 번도 verified 안 된 엔드포인트에 최초 2xx"가 필요. 같은 스위트 재실행은 이미-verified만 재확인하므로 C3 불변(run 20260714-142501-5387: 유니크 921 touch / 775 verified, 전부 기존 누적 집합 → 신규 0 → 81.7 고정).

**남은 갭(~230)은 대부분 "이 계정에선 설계상 2xx 불가"**, 미테스트가 아님:
- **entitlement**: data-flow(17)+data-ops(17)=34개가 `PRODUCT-AI-ANALYTICS-USER-0001`(계정 미권한) → class=entitlement waive. **denom에서 빠짐**(C3 81.69→83.96%).
- **backup 삭제 비대칭**: DBaaS `setbackup`/create는 2xx인데 `unsetbackup`/`removebackuphistories`(DELETE `/backups`, PUT `/backup-histories`)만 **전 엔진 동일하게 401 `Dbaas.Unauthorized.AuthNFailed`**. AuthN(인증) 실패라 플랫폼-가드 vs 토큰누락 버그 사이 모호 → **추정 waive 금지, 오너 확인/조사 필요**.
- **backend 500** `ContactAdminForAssistance`(set-parameters·log-export-configs·quick-query): SDS 백엔드 결함 → known_issues baseline.

**C3 계산 규약**(dashboard/build.py): `c3_denom = total - excl_waived`. EXCLUDED 클래스(blast-radius|entitlement|unsatisfiable-flow|billing-prohibitive|owner-exclusion)만 denom에서 제외. reachability 클래스는 denom 유지 + 도달 시 numerator 가산. → 정당한 미권한/라이선스-게이트 엔드포인트를 올바른 class로 waive하는 것이 C3의 실질 레버(넘버레이터 2xx 수리와 함께).

## 공유 IGW adopt (2026-07-14, 오너 "igw 지금 반영")

IGW는 VPC당 1개 배타(HB4c 기확정). 메인 공유 VPC(adopt:vpc)를 여러 lifecycle이
나눠 쓰므로 각자 create-igw하면 2번째부터 400 already-associated → gen-heavy-lb-
members가 wait-internet-gateway(expect[200], give_up 없음)에서 hard FAIL. 종전엔
find-or-tolerate 우회(create 400 관용 + find + owned_igw_id만 삭제)로 버텼으나
lb-members는 그 wait가 미해석 id로 실패.

**반영(TGW adopt와 동일 패턴)**:
- 공유 IGW 1개를 provision_shared_vpc(need_igw)가 메인 공유 VPC ACTIVE 직후 그
  VPC에 attach 생성(no-wait, 첫 adopter의 _ensure_adopted_active("igw")가 게이트).
  teardown은 IGW를 VPC보다 먼저 삭제(attached면 VPC DELETE 409).
- adopt:vpc 4개 사용자(gen-heavy-lb-members·gen-heavy-vs-netops·networking-vpn-
  gateway-tunnel·gen-pilot-net-basics)의 create-igw(POST)·set-igw(PUT)·delete-igw
  스텝에 adopt:"igw" → skip+id 시딩 / no-mutate / retain.
- IGW create/PUT/delete **커버리지는 net-VPC IGW 소유자**(vpc-subnet-vip-nat=A·
  gen-wave5-fw=B)가 유지 — 그들은 자기 net-VPC에 IGW를 self-create(배타 충돌 없음).
- 엔진 adopt 시딩 확장: IGW create는 400 관용이라 id를 **capture_soft**로 잡는다
  (internet_gateway_id/owned_igw_id=$.internet_gateway.id). adopt POST-skip 시딩이
  capture+capture_soft 둘 다, **소스 JSONPath가 `.id`로 끝나는 것만** 시드 →
  vpc-peering의 account_id(=$.vpc.account_id)·gen-wave5-fw의 net_b_vpc_name
  (=$.vpc.name)을 올바로 제외(자원 id가 아닌 하위필드 오염 방지).
- dependencies.json: shared_roots.igw(parent:vpc) + adopt_edges에 igw(4개 사용자).
  DAG 검증 통과(igw<-4). igw는 VPC 슬롯 미소비(shared_vpc_count 여전히 3=메인+A+B).
- offline: tests/offline/test_shared_igw_adopt.py(7종 — adopt skip/seed·capture_soft
  .id 필터·self-create 폴백·provision+teardown 순서).
## DBaaS UNKNOWN 상태 — settle 폴 자동복구 (refire→sync-state, 2026-07-14)

**증상**: start/restart/설정변경 후 `wait-*` settle 폴 중 클러스터 `service_state`가 UNKNOWN으로 떨어지면 until(RUNNING/ACTIVE/AVAILABLE)에 영원히 안 걸려 폴이 타임아웃(관측: database-mysql-cluster wait-started 38회차/20분 공회전)까지 공회전 → lifecycle 실패.

**수리**: 콘솔의 synchronize 절차(= `POST /v1/clusters/{cluster_id}/sync-state`, no body, 202→SYNCHRONIZING→RUNNING, LIVE 검증 run-923a/c373)를 폴에 이식 — DBaaS cluster settle 폴 43개(scenarios.json 28: mysql 13·pg 12·heavy-shared-dbaas 3 / subops-full 15: 5엔진)에 `poll.refire {field:$.service_state, when:[UNKNOWN], POST .../sync-state, max:2}` 부착. pre-delete-sync-state(삭제 직전)와 짝을 이루는 mid-lifecycle 복구.

**엔진 규약 변경**: 종전엔 refire가 설정되면 terminal-bad(ERROR/FAILED) fast-exit이 통째로 꺼졌음 → 이제 **refire.when이 직접 처리하는 상태만** terminal-bad에서 제외 (refire on UNKNOWN이어도 ERROR/FAILED는 여전히 즉시 실패). heavy-shared-dbaas의 wait 폴은 cid 토큰이 엔진별({maria_cid}/{epas_cid}/{cache_cid})이므로 refire path도 동일 토큰을 써야 함 — {cluster_id} 하드코딩 금지.

**후속 해소(같은 날, 오너 "설계가 이상한데" 지적)**: '실패 상태를 until(성공)로 수용'하는 안티패턴을 전수 스윕 — 총 125곳 발견. DBaaS subops-full 102 + eventstreams 12 + mysql/pg wait-after-add-block-storages 2 = **116곳을 정석으로 교체**(until에서 ERROR/FAILED/UNKNOWN 제거 + refire UNKNOWN→sync-state 부착; ERROR/FAILED는 엔진 기본 terminal_bad가 fast-exit → optional 그룹 스킵으로 종전과 동일한 격리, 단 정직하게 실패로 기록). VS volume revert의 error 수용 1곳은 sync류 복구 API가 없어 terminal_bad로 교체. **잔여 11곳(재검토 대상)**: networking tgw/direct-connect/vpc-endpoint(7)는 until의 DELETED가 "공유자원이 스윕으로 소멸"이라는 별개 의미라 보존, gslb는 4개 중 3개(active/after-health-check/after-resources)를 terminal_bad로 전환 완료; **wait-gslb-deletable의 ERROR-in-until은 의도적 보존** — GSLB DELETE는 ACTIVE|ERROR 두 상태에서 허용되므로 이 폴은 실패 위장이 아니라 '삭제 가능 종착 게이트'다(LIVE 검증 2026-06-23 lifecycle note).

## 카탈로그 수집 모드 — 변경 감지는 --fresh 필수 (2026-07-14 실측)

`spec.extract_catalog` 기본(resumable) 모드는 method+http_path 보유 항목을
재수집하지 않는다(재개용 cache-hit) → **기존 엔드포인트의 변경을 감지 못 함**
(실측: 1372개 전부 cache-hit no-op). 스펙 변경 diff 절차: ① `--fresh`로 전량
재수집(인덱스 캐시도 폐기) ② `spec.diff <직전커밋본> data/api_catalog.json
--mark` → data/spec_diff_latest.json(marks) ③ 대시보드 NEW/UPD 배지 + 인덱스
스펙변경 패널 자동 표시 ④ 런 후 `spec.change_report`가 변경/기존 버킷 분리
판정. 2026-07-14 기준 드리프트 0 (6/5 커밋본 == 라이브, 1372/1372) — 저녁 변경
diff의 old는 커밋된 카탈로그 그대로 사용.

## 자원정리 wall-time 최적화 (2026-07-14, 오너 "별도 agent로 자원정리 시간 단축")

테스트 ~50분 + 정리 15~80분이던 프로파일의 4개 병목 수리 (별도 에이전트 구현,
의미 보존 검증 후 통합):
1. **engine 공유 teardown 병렬화**: 직렬 사다리(서브넷→TGW→IGW→VPC×3, 최악 ~15분)
   → 독립 체인 4개([main: 서브넷→IGW→VPC]·[tgw]·[net-a]·[net-b]) 병렬. wall ≈ 최장
   체인 1개. 체인 내부 자식→부모 순서·409 간격 유지.
2. **run_scoped reap 버킷 배리어**: per-item `_wait_gone`(≤150s) 직렬 → 버킷당
   `_wait_all_gone` 1회 + leaf 꼬리 배리어 스킵. 잔존 N개: N×150s → 버킷수×150s 이하.
3. **_leftover_report pick-기반**: full dry-scan(scan_owned, 수 분) → 마지막 라운드
   `_select` 픽(_LAST_PICKED, _STATE_LOCK 하 기록) 요약(LIST 0회). 리포트 경로는
   전부 genuine=0 라운드 뒤라 픽=생존자. 픽 비면 scan_owned 폴백.
4. **grant-inprog backoff**: 고정 30s → 30→60→120s(SCP_SWEEP_INPROGRESS_SLEEP_MAX_S,
   genuine 진행 시 리셋) — drain 대기 중 재나열 라운드 수 절감.
- 소유권 게이트·2026-07-03 TGW grant·leaf-drain 종료 정책·자식→부모 순서 전부
  불변 (tests/offline/test_cleanup_walltime.py 12종이 잠금; 전체 오프라인 620 pass).
- 미구현 제안(콘솔 영역): run-end 스윕 수행 시 +0 재스캔 스킵 또는 reconciler가
  사이드카(last_sweep_leftovers.json)를 쓰고 +0이 신선하면 소비.

## 반복 런 실패 2건 수리 (2026-07-15, run-8de6 실측 근거)

- **gen-heavy-lb-members `delete-public-ip` 400**: `delete-lb-static-nat`(204)는 async detach인데 1.2초 뒤 IP 삭제 발사 → `publicip.not-deletable-state(ATTACHED)`. `retry_on_status`에 400 추가([409]→[400,409], 사다리 40×30s)로 detach 전파 흡수 — lb-server-group delete와 동일 검증 패턴.
- **heavy-shared-networking `wait-subnet` 타임아웃**: subnet이 CREATING 305.9s 유지 vs 폴 예산 300s — 아슬아슬 초과로 실패. 누적 op-timings상 createsubnet p90 4:20/max 5:27(327s)이므로 300s는 관측 최대치 미만 → 600s로 확대. (주의: 이 lifecycle의 until은 ACTIVE만 수용; DBaaS 계열 wait-subnet은 CREATED/RUNNING도 수용해 180s로도 미실패 — 건드리지 않음.)
- **eventstreams es-wait FAILED**: 백엔드 프로비저닝 실패(202 접수 후 state FAILED, teardown 정상). 구버전 선택(es-prefer-older-version)이 원인인지 최신 버전 실험은 오너 대기 중.

## eventstreams 프로비저닝 FAILED — 원인 좁힘 + 콘솔 실증 미러링 (2026-07-15)

**기각된 가설 2개**: (1) 구버전 선택 — `es-find-version` 목록에 Kafka 3.9.1 **단일 버전**뿐이라 버전 선택지가 애초에 없음(es-prefer-older-version의 $.contents[1]은 항상 미스). (2) 결합 토폴로지 — 오너가 콘솔에서 ZK&Broker 3노드 결합(combined)으로 **성공(Running)** 재현.

**유력 원인 = 서버 타입 용량**: 우리 body는 `ess1v16m32`(16vCPU/32G)×3(=48vCPU)로 202 접수 후 FAILED; 콘솔 성공본은 `ess1v2m4`(2vCPU/4G)×3. es-find-stype 목록의 contents[0]가 16vCPU라 "첫 항목 캡처"가 대형 타입을 물어온 것.

**반영(콘솔 성공본 미러링)**: es_stype = `where_prefix name:"ess1v2m"` 하드캡처(ess1v2m4), es_stype2(resize 대상) = `ess1v4m*` 소프트캡처, DATA 16GB SSD 블록스토리지 추가, service_watch_log_collection=false, maintenance_option 제거. 콘솔 성공 조합 = Kafka 3.9.1 · combined ZK&Broker 3노드 · OS 104GB SSD + DATA 16GB SSD · 포트 9091/2180 · NAT/로그수집/유지관리 미사용.

## DBaaS subops 40분의 정체 + 적응형 폴 간격 (2026-07-14, 오너 캡쳐 분석)

**진단**: epas-cluster-subops-full은 77스텝 직렬 체인이고 settle 폴 ~30개가 전부
interval=20s. 타이밍 실측의 op 시간이 22s/43-44s/1:04/1:26/2:50/3:35로 양자화 —
정확히 ~21.5s(20s sleep + GET)의 1·2·3·4·8·10배수. 즉 측정 settle은 실제 정착
시간이 아니라 **폴 격자에 걸린 시간**이며, 빠른 config성 op(sync/set류, 실제
2~10s 추정)도 최소 한 격자(22s)를 냈다. 폴 op ~30개 × 초과대기 11~18s =
시나리오당 8~12분 순수 격자 손실. 나머지는 진짜 플랫폼 시간(create ~10분+,
upgrade-kernel 3:35, start 2:50 등)의 직렬 합산.

**반영(엔진 공통, 기본값)**: 폴 재시도 간격 ladder — interval_start(기본
min(3, interval))에서 시작해 2배씩 interval로 수렴 (3→6→12→20…). 빠른 정착은
3~9s에 잡히고 느린 정착은 초기 3~4회 가벼운 GET 뒤 종전과 동일. per-poll
opt-out: poll.interval_start=interval. 429 영향: 폴당 최대 +4 GET(초기 30s
한정)로 유계. offline: test_adaptive_poll_interval_ladder.

**타임라인 도구(신규)**: `python -m tools.lifecycle_timeline <run>.events.jsonl
--lifecycle <id>` — step-start/end/poll-progress로 시간축 워터폴 HTML
([API|settle|idle 갭] 분해 + 폴 횟수). console2 타이밍 탭(op별 합산)의 시간순
보완. 남은 레버(미구현): subops 체인을 2 클러스터로 분할(벽시계 반감, 과금 2배
— 오너 결정 사안).

## DBaaS subops 2-클러스터 분할 (2026-07-14, 오너 "subops 체인을 클러스터 2개로 쪼개")

5개 엔진(mysql·epas·mariadb·cachestore·postgresql)의 `<eng>-cluster-subops-full`
(51~77스텝 직렬, 실측 27~42분)을 `-subops-a`/`-subops-b` 두 lifecycle로 분할:
- **A** = config/백업/아카이브/로그export/커맨드/보안그룹 계열 (CRUD성 sub-op)
- **B** = 상태전이/헤비 계열 (restart·stop/start·switchover·patch·upgrade-kernel·
  resize-ig·block-storages)
- 공통 = 각자 자기 클러스터 create/wait + 캡처 + pre-delete-sync + delete/gone
  (probe-reads·show-request는 A만 — read 중복 제거)
- **커버리지 불변**: A∪B의 (method,path) 집합 == 원본 (5엔진 전부 검증 통과)
- 원본은 enabled:false + _status:stale + _replaced_by 로 보존 (롤백 용이)
- 효과: A/B 병렬 → 벽시계 ≈ create 고정비 + ops/2 (적응형 폴 ladder와 합산 시
  40분 → ~20분대 기대). 과금은 클러스터-시간 합산 ≈ +25% (2×full 아님 — 각
  클러스터 수명이 절반이라).
- 메타: adopt_edges full 5개 제거→a/b 10개 추가([subnet#db,vpc] 동일), durations
  0.6×full 시드(source: split-estimate), DAG 194 enabled 통과, validate 0 error.
- 주의: eventstreams-cluster-subops-full은 별도 파일(플랫폼 PF 조사 중)이라 미분할.
## DBaaS 최소 사이즈 전환 — capture min_by (2026-07-15, 오너 "제일 작은 사이즈로 전부")

**문제**: DB형 엔진들의 서버타입 캡처가 전부 "목록 첫 항목"이라 mysql/pg/epas/mariadb는 `db1v10m120`(10vCPU/120G), cachestore는 `css1v10m160`(10vCPU/160G), ES는 `ess1v16m32`(16vCPU/32G)×3를 매 런 생성 — 목록 정렬에 크기 의존.

**엔진 확장**: `_capture`에 `min_by`(숫자 필드 오름차순 최소 선택, 복수 필드 tie-break) + `nth`(0=최소, 1=차소 — 리사이즈 대상용, 초과시 클램프) 추가. where_prefix와 합성 가능, min_by 없으면 종전 첫-매치 그대로(하위호환). 오프라인 테스트 tests/offline/test_capture_min_by.py 6종.

**적용 30곳**: scenarios.json 6(mysql/pg db_server_type, pg alt→nth1, heavy-shared maria/epas/cache) + subops-full 10(5엔진 + *_stype2/server_type_name/db_server_type2→nth1) + eventstreams 2(es_stype/es_stype2 — prefix 하드코딩 제거) + heavy-dbaas 9 + heavy-pg 3. 버전/파라미터그룹 캡처(engine_version_id 등)는 크기 개념이 아니라 불변.

**관찰 항목(다음 런)**: (1) 실제 잡힌 최소 타입명(오너 추정 2vCPU/4G) — events의 find-* resp로 확인, (2) 생성시간 변화 — op_timings store의 createcluster p50 대비 last_s (기준: mysql 9:24 / pg 8:54 / ES는 종전 FAILED).


## console2 +0 재스캔 스킵 (2026-07-15, 오너 "반영해")

run-end 자동 클린업 스윕이 실제 수행된 런은 직후의 +0 재스캔(scan_owned 전
컬렉션 dry-scan, 수 분)이 순수 중복 — 스윕 라운드 루프가 방금 '무진행'까지
재나열했기 때문. 반영: 스윕 서브프로세스 호출 직후 rec["end_sweep_ran"]=true,
_post_run_rescans가 +0을 스킵 엔트리(사유 포함)로 남기고 +5m/+15m만 실행
(늦출현 감시 유지, late-alert base는 첫 실행 스캔으로 자연 전이). 스윕이
생략된 경로(타 실행 진행/자원 미생성/게이트 off)는 +0 유지. — 정리 표시
시간(마지막 스윕→완료 전환) 수 분 단축.

**중요 운영 규약(같은 날 실측)**: 정리 최적화의 대부분(공유 teardown 병렬화·
run_scoped 배리어)은 console2 **서버 프로세스 안**에서 실행된다 — git pull
후 **서버 재시작 없이는 옛 코드가 메모리에 남아** 적용 안 됨 (run-8de6 실측:
정리 29.1분 = 최적화 전 27.1분과 동일 프로파일). reconciler 스윕만 서브프로세스
라 새 코드를 탄다. 판별: 런 로그에 "shared teardown chain" 라인 유무.

## 서버타입 목록의 purpose/type 축 — min_by 필터 필수 (2026-07-15, 런 실측 400)

**발견**: `/v1/server-types`(DBaaS 계열 공통)의 항목엔 `purpose`(general|zookeeper|akhq|sentinel)와 `type`(Standard-1|Standard-2) 축이 있다. 무필터 min_by가 ES에서 절대최소 `ess1v1m2`(**zookeeper 전용**)를 집어 create가 **400 "The server type is invalid"** (run 실측). 콘솔 성공본의 ess1v2m4는 purpose=general의 최소형.

**규약**: 서버타입 min_by 캡처는 반드시 `where_prefix {purpose:"general", type:"Standard-1"}`을 합성한다(48곳 적용). cachestore는 css/redis 두 패밀리가 동률이라 `name:"css"`도 고정. 라이브 시뮬레이션 결과: mysql/pg/epas=db1v2m4(→db1v2m8), mariadb=db1v1m2(→db1v2m4), cachestore=css1v1m2(→css1v2m4), ES=ess1v2m4(→ess1v2m8). mariadb/cachestore의 1vCPU general 타입은 미검증 — create 400 시 cpu_core 하한 추가가 다음 수순.

## shared_infra --teardown 병렬화 + IGW/TGW 갭 수리 (2026-07-15, run-eac8 실측)

**console2 run-end의 실제 teardown 경로는 `shared_infra --teardown`(서브프로세스
CLI)이지 engine closure가 아니다** — 2026-07-14 wall-time 최적화(engine closure
병렬화)가 이 경로를 못 탄 이유. run-eac8 실측: (1) 완전 직렬 — 서브넷 gone-wait
240s 동안 TGW/net-A/B 미착수 (오너: "그거 지워질 때까지 아래쪽에 다른 작업을
안하네?"), (2) 공유 IGW/TGW를 아예 안 읽음 → IGW가 메인 VPC DELETE를 409×5로
막고 VPC·TGW 통째로 스윕行 (스윕이 29분씩 일하던 큰 지분).

수리: engine closure와 동일한 4체인 병렬([main: 서브넷→IGW→VPC]·[tgw]·[net-a]·
[net-b]) + SCP_SHARED_IGW_ID/TGW_ID 판독 + TGW settle 후 삭제. **서브프로세스라
git pull만으로 다음 런 적용(서버 재시작 불필요)**. 단 +0 재스캔 스킵(end_sweep_
ran)은 서버 프로세스 코드라 재시작 필요. offline: test_shared_infra_teardown.py
+3종(IGW-before-VPC·독립체인 비차단·구 env 무회귀).

run-eac8 잔존 3건의 성격(오너 질문 "시나리오 실패해서?"): 아니다 — children
TGW 409(EDITING drain)·vs-full/vpc-endpoint 서브넷(자식 drain)은 전부 비동기
drain 타이밍 클래스로 reap/스윕 백스톱이 정상 회수. 실패 시나리오 잔존은 별도.

## apigateway 403-on-gone + ledger-reclaim 실존확인 (2026-07-15, 오너 로그 실측)

**PF 확인**: apigateway는 존재하지 않는 리소스에 404 대신 **403**을 반환한다 —
삭제완료된 API의 GET/DELETE 모두 403, LIST /v1/apis는 0건 (콘솔에도 없음).
"404 대신 403" AI-usability 결함 클래스 (resource-기반 인가가 미해석 리소스를
권한거부로 뭉갬).

**영향**: ledger-reclaim(생성 원장 재생 패스 — 목록에 안 잡히는 자원 회수용,
queueservice가 원형)이 404만 gone 확정으로 봐서, 이미 지워진 apigateway 유령
항목 9건을 매 라운드·매 런 영원히 재시도 (오너: "console에는 자원 남은거
안보이는데 왜 삭제 시도를 하는거지?").

**수리**: DELETE 거절(403/400 등) 시 GET 실존확인 — GET 403/404/410 = 이
자격증명으로 관측 불가 = gone 확정(샤드 프룬), GET 200 = 진짜 잔존(재시도
유지), GET 5xx/429 = unknown(보수적 유지). offline 3종.

## ES 전용 subnet 실험 (2026-07-15, 오너 승인)

콘솔 성공(ess1v2m4·combined 3노드·Running) vs API 실패(동일 조합인데 202 후 provision FAILED, run-9c64)의 잔여 격차 후보 = **네트워크 배치**: 콘솔은 전용 VPC/subnet(regrvpcd6d5f60l), API는 공유 VPC의 공유 db-subnet(10.124.7.0/24). 실험: eventstreams-cluster-subops-full의 subnet adopt 해제 → 공유 VPC 내 전용 /24 **10.124.12.0/24** 자체 생성/삭제(waits 600/900s, teardown 스텝 기존재). VPC는 공유 유지. 공유 VPC(10.124.0.0/20) /24 사용 맵: 0=공유, 7=db, 8·9·10=타 lifecycle — 신규는 1~6·11~15에서 고를 것.

## DBaaS A/B 분할 실측 + 시간-균형 재배분 (2026-07-15, run-f5a9)

**A/B 분할 1차 실측(run-f5a9, 테스트 40.0분)**: 생성wait 8.1~11.7분 = 과거 3런과
동일(생성 지연 아님). 분할은 전 엔진 개선(−2.7~−8.5분)했지만 **ops 배분 비대칭**
이 이득을 깎음 — mariadb A ops 4.3분 vs B ops 22.5분 (stop/start·upgrade·resize·
block-storages가 전부 B). makespan 40.0분의 결정자 = mariadb-b 35.2분.

**재배분**: restart·stop/start·sync-cluster-state·switchover(+waits)를 B→A 이동
(mysql/epas/mariadb/postgresql — **cachestore는 이미 6.1/6.1 균형이라 제외**;
일괄 이동 시 역비대칭 10.6/1.6으로 악화 검산). 실측 기반 기대: 전 엔진
max(A,B) ≈ 25.8~27.1분 → DB가 SKE(~35분) 아래로 → makespan 바닥 = SKE.
B 잔류: patch·upgrade-kernel·resize-ig·add/resize-block-storages(+capture-server-type).
합집합 불변 재검증 통과.

## 최소 사이즈 첫 실측 (run-f5a9) — 시간 불변, 1vCPU도 유효 + 후속 수리 2종 (2026-07-15)

**타이밍 판정(오너 질문 "느려지지 않았는지")**: 2vCPU(db2v2m4)로 줄여도 **생성/수정/삭제 시간 변화 없음** — create mysql 9:41~9:45(기준 9:24)/pg 9:11(8:54), resize 4:36~6:24(동일), upgrade-kernel 3:33~4:37(동일), delete 2:53~3:57(동일). DBaaS 오케스트레이션 시간이 지배하고 인스턴스 크기는 무관 → **작게 = 같은 속도 + 비용 절감**이 결론.

**타입 실증**: 무필터 min_by 동률에서 Standard-2가 선착(db2v2m4·db2v1m2·css1v1m2) — **mariadb 1vCPU(db2v1m2)·cachestore 1vCPU(css1v1m2)도 프로비저닝 성공**(1vCPU general 유효성 실증). purpose 필터 적용 후엔 Standard-1(db1v*)로 고정된다. ES만 zookeeper 타입(ess1v1m2) 400 — 필터로 기수리.

**run-f5a9 수리 검증**: gen-heavy-lb-members delete-public-ip **통과**(400 retry 유효). 동일 클래스 잔존 5곳(vpn-gateway-tunnel<-이번 런 실패, vpc-publicip, networking-vpc-publicip, heavy-shared publicip-delete, pilot) 전부 [400,409]로 정렬 — publicip DELETE는 detach 전파 지연으로 ATTACHED 400이 구조적, **모든 publicip delete에 400 retry가 규약**.

**ske scale-up 400 (신규)**: upgrade-nodepool 후 **nodepool은 Running인데 cluster는 UPDATING** — 노드풀 폴만으로 게이트 불충분. scale-up-nodepool 앞에 wait-cluster-before-scale-up($.cluster.status Running, 900s) 삽입. 클러스터-자원 이중 상태는 각각 게이트해야 함.

## teardown 최소화 감사 마무리 (2026-07-15)

- **dbaas 재-DELETE 인질 가드**: 스윕 dbaas 패스가 이미 DELETING인 클러스터
  (라이프사이클이 방금 삭제, mariadb drain ~90분)에 재-DELETE를 발행 → 2xx로
  접수되면 900s dbaas 배리어가 그 drain을 인질로 최대 15분 정지. TGW 패스와
  동일하게 async-deleting 스킵(_select의 in-progress 집계는 유지).
- **run-end 단계별 소요 계측**: console2 로그에 teardown shared/reap/스윕 각
  소요(s)를 남김 — 다음 런부터 "정리 N분 = 어느 단계 몇 분"이 로그로 확정.
- 최소 프로파일 기대(모든 수리 적용 시): teardown_shared ~3-5분(병렬 4체인,
  서브넷 drain이 바닥) + reap ~1-2분(버킷 배리어) + 스윕 ~2-4분(클린 계정
  1라운드 + leaf-drain stop + 유령 프룬) ≈ **총 7-11분** (종전 27-29분).
- 적용 조건: shared_infra/reconciler는 서브프로세스라 pull만으로 적용;
  reap 배리어·+0 스킵·단계 계측은 **console2 서버 재시작 필요**.

## ES 전용-subnet 실험 성공 + run-ddbf 후속 수리 (2026-07-15)

**ES 첫 완주**: run-ddbf에서 eventstreams-cluster-subops-full **passed** — 전용 /24(10.124.12.0/24) + ess1v2m4 + DATA 16GB 조합으로 create 202 → RUNNING → 서브옵 → 삭제까지. **콘솔-API 격차의 원인 = 공유 db-subnet 배치**로 확정. 오너 승인으로 SKE(container-ske-cluster-nodepool)도 동일 전환(전용 10.124.13.0/24) — 생성시간 단축 여부 다음 런 관찰.

**run-ddbf 실패 3건 수리**:
- vs-full delete-server 409 `LbServerGroupAttached`: lb-members가 이 VM을 서버그룹에 차용 — 반납(+738~789s)이 delete 사다리 소진(+732s)보다 몇 초 늦음. 교차-lifecycle 레이스이므로 사다리 연장(6×20s→30×30s=15분)으로 흡수.
- pg create-cluster 500: **런의 첫 /v1/clusters POST만 3런 연속 500 ContactAdmin**(1~2s 뒤 병렬 4건은 202, body 동일 실측) — 백엔드 콜드스타트 추정, [500]×2/60s 재시도 실험. 동일명 재발사라 중복생성 위험 없음(이름충돌로 거부됨).
- subnet ACTIVE-폴 예산 전수 스윕: 42곳 timeout<600 → 600 (vip-nat 240s에 243.8s 실측 등 — createsubnet max 5:27 상회 필수).

## run-ddbf 정리 단계 분해 + 스윕 비용 계측 (2026-07-15)

**단계 실측(계측 라인)**: teardown shared 334s(병렬 체인 — 서브넷 drain 바닥,
목표선) · reap 35s(버킷 배리어 효과, 종전 수 분) · **스윕 1117s(18.6분 — 유일한
잔여 병목)** · +0 재스캔 스킵 작동. 스윕 구조는 이미 최적(2라운드, leaf-drain
stop, 픽 리포트 0-LIST, stuck 1회 보고)인데 NOWAIT(배리어 0)에서 18.6분 —
시간이 리스팅/삭제/슬립 어디에 있는지 로그로 특정 불가 → **라운드별 [cost]
계측 추가**: 라운드 wall + 컬렉션별 리스팅 누적(top 6) + delete 총 시간/건수.
다음 런 로그의 `[cost]` 라인이 마지막 병목을 확정한다.

**run-ddbf 성과 확정**: DB A/B 재배분 적중(전 엔진 max 24~28분, mariadb
35.2→27.9) · SKE 33분 통과 · eventstreams 첫 완주가 50.8분 신규 critical path
(→ 차기 분할 후보 1순위, 안정화 확인 후) · 테스트 55.4/정리 25.7/총 81.0분
(eventstreams 커버리지 신규 추가분 포함으로 기준선과 동시간).

## console2 suite 게이트 소실 — smoke가 풀 CRUD로 돌던 버그 (2026-07-15, 오너 실측)

**증상**: 콘솔에서 smoke suite를 골라 실행하면 VPC/TGW/DB까지 전부 생성 (오너:
"smoke인데도 vpc tgw 등은 다 만들고 있네", "db도 만드는데?"). pre-flight의 과금
경고는 오탐이 아니라 정확했던 것.

**원인**: `applySuite()`가 suite를 '선택 프리셋'으로만 취급 — scope 없는
whole-catalog suite(smoke/full)는 전 카탈로그 lifecycle을 targets로 펼치고,
request 게이트(mutations:false)는 폐기("No axis mapping — gates derive from
selection"). /api/run은 live 런에 mutations=destructive=true 하드코딩 + heavy를
선택에서 역산 → smoke 선택 = 전 카탈로그 LIVE CRUD.

**수리**: (1) UI — suite request.mutations===false면 `suiteReadOnly` 보존(수동
선택 변경 시 해제), selectionPayload에 `read_only:true` 동봉. (2) 서버 /api/run —
read_only면 게이트 전부 OFF + rec.read_only. (3) _run_worker — read_only 런은
공유 VPC provision 스킵 + pytest 타깃을 `tests/smoke -m smoke`(CI smoke job과
동일 의미)로 전환: 자원 생성 0, run-end 스윕도 resource-tracked 0 게이트로 자동
생략. (4) _preflight — read_only면 과금/자원/peak 0 + 안내 워닝. **서버 재시작 +
브라우저 새로고침 필요** (JS+서버 모두 변경).

## 서브넷 동시다발 ERROR — 플랫폼 프로비저닝 장애 신호 (2026-07-15 저녁, 실측)

계정에 서브넷 6개(GENERAL 4 + VPC_ENDPOINT 2, 서로 다른 CIDR/VPC)가 같은 날
state='ERROR'(대문자 확정) — create는 202 접수 후 CREATING으로 폴 캡(240s)을
넘기고 그 뒤 ERROR로 전이. 개별 시나리오 문제가 아니라 **플랫폼측 서브넷
프로비저닝 장애**(당일 저녁 API 변경 롤아웃과 시기 일치 — 인과는 미확정) 신호.
엔진 동작은 정확: wait-subnet not-ready 게이트가 lifecycle을 실패로 분류
(terminal-bad는 폴 도중 ERROR를 봐야 fast-exit — 이번엔 캡 안엔 CREATING이라
타임아웃 경로). API 칩의 'ok'는 개별 HTTP 상태(202/200) 기준이라 정상 표기 —
"생성 실패인데 200"은 비동기 실패가 HTTP로 안 드러나는 202-accepted 패턴
(상태 필드로만 노출). ERROR 잔존은 owner-prefix 스윕 회수 대상(거부되면 stuck
보고).

## 테스트 계정 교체 — oplog 자격 분리 (2026-07-15, 오너 (b) 결정)

새 테스트 계정으로 교체하되 미러 히스토리는 보존: 로컬 .env에
`SCP_ACCESS_KEY/SECRET_KEY`=새 계정, `SCP_OPLOG_ACCESS_KEY/SECRET_KEY`=구 계정.
- **미러/자동수리 버킷**(apitest-oplog-permanent): oplog 키(구 계정) — 연속성.
- **logsink 버킷**(apitest-logsink): 시나리오(network-logging/loggingaudit,
  wave4)가 '테스트 계정 안에서' 참조하는 픽스처 → `ensure_logsink()`가 항상
  테스트 키로 동작하게 분리 수리 (`_cfg(keys="test")`). 새 계정 첫 사용 전
  `python -m core.oplog ensure-logsink` 1회 필요.
- 잔여 갱신처: GitHub repo secrets(CI), 원격 세션 env(모니터링). 새 계정은
  쿼터/entitlement가 다를 수 있어 smoke 먼저 + waiver/베이스라인 재검.
- 구 계정 잔존(DB 10·VPC 5·ERROR 서브넷 등)은 교체 전 구 키로 정리.

## logsink 자동 부트스트랩 (2026-07-15, 오너 "시나리오에 버킷 생성 step" 제안의 절충)

**플랫폼 제약 확정**: Object Storage 버킷 생성은 SCP REST 카탈로그에 없다 —
카탈로그의 /v1/buckets CRUD 14개는 전부 **Archive Storage**. OBS는 S3 프로토콜
(AWS 서명, boto3) 전용이라 엔진 스텝(SCP HMAC)으로 생성 불가 → 시나리오
자체-생성 스텝은 불가능, 상주 픽스처 + ensure가 정답.

**절충 구현**: `shared_infra.provision()`이 선택에 apitest-logsink 참조 스텝
(gen-wave4-nlog 등)이 있으면 `oplog.ensure_logsink()`(테스트 키, 멱등)를 자동
호출 — 새 계정에서 수동 ensure-logsink 명령 불필요, stdout KEY=VALUE 계약은
stderr 리다이렉트로 보존. 실패는 best-effort(해당 시나리오가 4xx로 표면화).
