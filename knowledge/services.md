# Per-service notes

A section per service as agents become expert in it. Keep it practical: the
host/scoping, the request bodies that work, captures, state machine, and quirks.
Cross-link to `validated-facts.md` (the authoritative fact list) rather than
duplicating. Add a new `##` section when you take on a new service.

---

## compute / virtualserver

- **Host:** regional (`virtualserver.<region>.<env>...`). Owns servers, keypairs,
  block volumes, snapshots, images, server-types.
- **Keypair:** `POST /v1/keypairs {name, tags:[]}` — omit `public_key`, SCP
  generates one. Addressed **by name** (`/v1/keypairs/{name}`). Zero-cost, sync.
- **Block volume:** `POST /v1/volumes` with `volume_type` (e.g. `SSD_Provisioned`),
  `size`, `max_iops`, `max_throughput` → capture `$.id`, poll `$.state` →
  `available`. (Different from filestorage volume!)
- **Snapshot:** `POST /v1/snapshots {volume_id, force:true}` → `$.id`.
- **Full VM:** vpc→subnet→sg→keypair→find-image→find-server-type→create-server.
  Critical fields in `validated-facts.md` (server_type prefix `s`, `volume_type`
  not `type`, `$.servers[0].id`, rename regex, stop/start, attach volume).
- **Lookups:** images `/v1/images?status=active&scp_original_image_type=standard&visibility=public&limit=50`;
  server-types `/v1/server-types` (pick id starting with `s`).

## compute / virtualserver — autoscaling

- **28 autoscaling endpoints** (`/v1/auto-scaling-groups/...`, `/v1/launch-configurations/...`).
  Only **2/28 covered** read-only: `listautoscalinggroups`, `listlaunchconfigurations`
  (bare smoke GETs, 200 with empty lists — account has 0 ASGs / 0 LCs).
- **Root cause the other 26 are uncovered:** two lifecycles already exist, are
  `enabled: true`, and cover them — `vs-autoscaling-coverage` (26 steps, in
  `regression/scenarios/lifecycles/compute__virtualserver.json`) and `gen-wave4-asg`
  (28 steps, VPC+subnet chain, `desired_server_count: 0` to avoid billable VMs, in
  `generated__wave4.json`). But **both are mutation-gated**: `run_lifecycle()` returns
  `status: skipped` immediately when `SCP_ALLOW_MUTATIONS=false` (engine ~line 642),
  before any step (including the lifecycle's GET steps) runs. So read-only runs never
  start them; that is why only the 2 bare smoke GETs are covered.
- **Levers:**
  - `SCP_ALLOW_MUTATIONS=true` + `SCP_ALLOW_DESTRUCTIVE=true` alone → `vs-autoscaling-coverage`
    records ~23 catalog keys (LC create/show/delete to 2xx; ASG sub-ops to 4xx via a
    literal placeholder asg_id, still recorded under their catalog key).
  - add `SCP_RUN_HEAVY=true` + a shared VPC/subnet → `gen-wave4-asg` takes ASG and all
    sub-resources to 2xx.
- **Form gate:** `createautoscalinggroupnotification` needs a `user_ids` array with a
  REAL account user id (no default) — set a `user_id` env var from console. Until it
  creates a notification, the 4 child notification GET/PUT/DELETE endpoints stay blocked.
- **Proven create facts:** `createlaunchconfiguration` PROVEN 2xx (`regrlcc1db18b2`, CI heavy
  run 2026-06-19) with a real `image_id` + real `keypair_name`. The image is **NOT
  OS-specific** — any valid **standard** image works (`scp_original_image_type=standard`, the
  same image lookup as the plain VM path). Evidence: `vs-autoscaling-coverage` seeds the image
  from an UNFILTERED `GET /v1/images?limit=50` → `images[0]` and still creates the LC, and
  `gen-wave4-asg` uses `scp_original_image_type=standard&visibility=public`; both succeed.
  (A prior note claimed a **windows** image was required / non-windows rejected with
  InvalidImage — that was a **mis-diagnosis, corrected 2026-06-19**; the real constraint is a
  valid standard image type, not the OS.) volume `size` must be divisible by 8;
  `delete_on_termination` is NOT a valid field. ASG policy `comparison_operator: "ge"` (short-code, not GREATER_THAN_OR_EQUAL_TO);
  schedule `frequency` enum ONCE|DAILY|WEEKLY|MONTHLY. ASG create uses arrays `subnet_ids`,
  `security_group_ids` (not scalar/`security_groups`); notification id is `$.notifications[0].id`
  (list envelope, not `$.id`).

## storage / backup

- **Host:** regional. Service key: `backup`.
- **checkfilesystemduplication** (`GET /v1/backups/check-filesystem-duplication`):
  requires BOTH `filesystem_path` (string) AND `server_uuid` (UUID of a VM) as query params.
  CONFIRMED LIVE (2026-06-20): returns 404 `Backup.NotFoundCreatedBackupAgent` for any
  `server_uuid` when no backup agent is installed on that server. NOT a missing-param issue —
  the params are correct per `api_catalog_params.json`. This endpoint is a **heavy-prereq blocker**:
  needs a real VM + backup agent installation (not REST-provisionable). Cannot be covered
  in a read-only or light-mutation run. Waiver candidate.
- **checkbackupnameduplicate** (`GET /v1/backups/check-name-duplication`):
  requires `backup_name` query param (NOT `name`). CONFIRMED LIVE 200 (2026-06-20):
  `?backup_name=regrtest` → `{"result":false}`.
- **listbackups** list envelope: `{contents:[], count}` (NOT `{backups:[], ...}`).
- **createbackup** returns 500 `ContactAdminForAssistance` — product-bug (baselined).

## storage / filestorage

- **Host:** regional. Owns NFS volumes.
- **Volume:** `POST /v1/volumes {name, protocol:NFS, type_name:HDD}` → capture
  **`$.volume_id`** (flat, service-specific), poll `$.state` → `available` →
  delete → poll 404. No VPC needed.

## networking / vpc (+ subnet, port, public-ip, internet-gateway)

- **Host:** regional. Consumes the **vpc** quota (cap 5).
- vpc `cidr` /20 (e.g. `10.123.0.0/20`), `$.vpc.id`, poll `$.vpc.state` →
  `ACTIVE`. subnet `type: GENERAL`, `$.subnet.id`. port `security_groups: []`,
  `$.port.id`. Teardown reverse with 409 retries (wait 404 before parent delete).
- public-ip `type: IGW` → `$.publicip.id`. internet-gateway needs `vpc_id`,
  `firewall_enabled`, `type: IGW` → `$.internet_gateway.id`.

## networking / firewall

- **Host:** regional. 8 endpoints. Firewalls are VPC-bound resources; the account
  must have at least one VPC/firewall provisioned before most endpoints are reachable.
- `GET /v1/firewalls` (listfirewalls) returns 200 OK even with zero firewalls (empty
  list). No required query params. Covered in read-only smoke.
- `GET /v1/firewalls/rules` (listfirewallrules) requires **`firewall_id` query param**
  (marked required in `data/api_catalog_params.json`); bare call returns 400. Probe
  with dummy id returns 404 — backend is reachable. Not coverable without a real firewall.
- `GET /v1/firewalls/{firewall_id}` (showfirewall) and
  `GET /v1/firewalls/rules/{firewall_rule_id}` (showfirewallrule) return 404 with
  dummy IDs — backend reachable, no resources provisioned in the test account.
- All 4 mutating endpoints (createfirewallrule POST, setfirewall PUT, setfirewallrule
  PUT, deletefirewallrule DELETE) need `SCP_ALLOW_MUTATIONS=true` (and DELETE needs
  `SCP_ALLOW_DESTRUCTIVE=true`) plus existing firewall/rule IDs from a prior create.
- **Coverage path:** create a VPC first (networking/vpc agent), then a firewall attaches
  to the VPC. Capture `$.firewalls[0].id` from listfirewalls. Use for showfirewall,
  listfirewallrules. Create a rule (POST /v1/firewalls/rules) to get `firewall_rule_id`
  for showfirewallrule, setfirewallrule, deletefirewallrule.
- Covered read-only: 1/8 (listfirewalls). Remaining 7 are mutation-gated this round.

## networking / security-group

- **Host:** regional, but **account/region-scoped — no VPC needed**. SG
  `$.security_group.id`; rule `$.security_group_rule.id` (`direction`,
  `ethertype: IPv4`, `protocol`, `port_range_min/max`, `remote_ip_prefix`).

## container / ske (Kubernetes)

- **Host:** regional. Heavy/billable. Needs vpc+subnet+sg+keypair+filestorage
  volume + k8s-version + server-type lookups. Cluster id `$.resource_id`;
  `service_watch_logging_enabled` required; `volume_id` is a string. Nodepool with
  `SSD_Provisioned` needs `volume_max_iops`/`volume_max_throughput`. See
  `validated-facts.md`.

## container / scr (registry)

- **Host:** regional. registry/repository id `$.id`, registry poll `$.state` →
  `Running`. **Registry DELETE 500-races** for minutes after create — retry on 500.
- **Quota = 1 registry** (`CONTAINER_REGISTRY.NON_VISIBILITY.MAX.COUNT applied_value: 1EA`);
  a create when the slot is full returns **403 quota.value.exceeded**, NOT a bug.
  Coverage lever for id-bound GETs is therefore **borrow-and-read-by-id, no create
  needed**: list registries → take a `Running` one → walk its children read-only.
- **Live-confirmed borrowable resources (2026-06-18, kr-west1):** registry
  `sample` id `nayvugfp4154447ab0ab61279cba3d72` (Running), repository `test`
  id `6c910ed5195842739f9c98a569982064` (Active). ids drift — re-harvest via
  `GET /v1/container-registries` then `.../{id}/repositories`; don't hardcode.
- **Detail envelopes:** `showregistry` → `{"registry": {...}}`, `showrepository`
  → `{"repository": {...}}` (single-key wrappers). List shapes:
  `listregistries` → `{count, registries[]}`, `listrepositories` →
  `{count, repositories[]}`, `listimages` → `{count, images[]}`,
  `connectable-resources` → `{count, resources[]}`.
- **`checkrepositorynameduplication`** needs BOTH `registry_id` + `name` query
  params (else 400 `Field required`); `checkregistrynameduplication` needs `name`.
  CONFIRMED LIVE 200 (2026-06-20): `GET /v1/repositories/check-duplication/name?registry_id=nayvugfp4154447ab0ab61279cba3d72&name=regrcheck` → `{"result":false}`.
  Added step `check-repository-name-duplication` to `scr-read-coverage` lifecycle in
  `container__scr.json`; step fires immediately after `list-registries-harvest` captures `registry_id`.
- **READ-ONLY coverage ceiling (no docker, no mutations):** 9 GETs reachable —
  listregistries, showregistry, listrepositories, showregistry's
  connectable-resources, both check-duplications (checkrepositorynameduplication now fixed),
  showrepository, listimages. All 200 on the borrowed resources.
- **Docker-push blocker:** the existing repository has `images:[]` (count 0), and
  images/tags are **born only by `docker push`**, not by any REST POST. So
  `showimage`, `listtagses`, `showtags`, `tags-{packages,secrets,vulnerabilities}`,
  `downloadmanifest`, `showimagelifecyclepolicypreview`, and every image/tags
  PUT/DELETE are **needs-docker-push blockers** — not testable from a no-docker
  runner. (See `regression/scr_docker_probe.py` for the docker-login hypothesis.)
- **Mutating policy PUTs** on registry/repository (description / lock / pull /
  scan / lifecycle / ACL / public-endpoint) are gated behind `SCP_ALLOW_MUTATIONS`
  and would mutate the SHARED borrowed `sample`/`test` resources — do NOT fire
  them just to move coverage; cover via an own-resource lifecycle when the quota
  slot is free. Several historically returned transient **503 upstream connect
  timeout** (product/gateway flap, retry-then-classify, not a deterministic bug).

## networking / gslb (Global Server Load Balancing)

- **Host:** regional (`gslb.<region>.<env>.samsungsdscloud.com`). Account/global-scoped, VPC-free.
- **Region gate:** only kr-west1 and kr-east1 — NOT kr-south1/2/3.
- **Quota:** max 20 GSLB domains per account; max 8 connectable resources per domain.
- **List endpoints (2xx live, 2026-06-19, kr-west1):**
  - `GET /v1/gslbs` → `{count, gslbs:[], page, size, sort}` — no required params.
  - `GET /v1/gslbs/routing-control` → `{count, page, regional_gslbs:[], size, sort}` — no required params.
  - Both return 200 with empty lists when no resources exist. Initial calls hit transient 503 (gateway flap); retry returns 200.
- **Create body (docs-validated, live unproven):** `{algorithm:ROUND_ROBIN, description, env_usage:PUBLIC, health_check:{protocol:TCP, service_port:"80", ...}, name:"label.gslb.e.samsungsdscloud.com", resources:[], tags:[]}`. Name is FQDN; label must be 4-40 lowercase letters+digits only.
- **Health check:** protocol one of ICMP/TCP/HTTP/HTTPS. TCP/HTTP(S) require `service_port`. HTTP(S) additionally require `receive_string` (alnum only) and optionally `send_string` (no `<>` or `#`). Use TCP to avoid HTTP(S)-only constraints.
- **Capture:** `$.gslb.id` from create response.
- **Lifecycle scenario:** `regression/scenarios/lifecycles/networking__gslb.json` (id: `networking-gslb-service`) covers full CRUD chain — POST create → GET show → PUT set → PUT health-check → PUT resources → PUT routing-control → GET resources → DELETE. Enabled, light (heavy:false), needs SCP_ALLOW_MUTATIONS=true + SCP_ALLOW_DESTRUCTIVE=true.
- **Coverage 2026-06-19:** 2/10 (2 list GETs covered read-only). Remaining 8 are mutation-gated — scenario ready, no blockers.

## networking / cdn

- **Host:** regional (`cdn.<region>.<env>...`). VPC-free control-plane resource. 9 endpoints.
- **Region gate:** kr-west1 and kr-east1 ONLY (NOT kr-south variants).
- **List (2xx live 2026-06-19):** `GET /v1/cdns` (listcdnservice) → `{cdn:[], count, page, size, sort}`
  — array key is `cdn` (not `cdns`/`items`). No required params. Empty list when 0 distributions.
  First call may 503 (gateway timeout, 2-15s); client retry → 200. Transient, NOT a product bug.
- **Create capture:** `$.cdn.resource_id` (NOT `$.id`/`$.cdn.id`). Required fields:
  `cache_expiry_time` (3600-2592000s, e.g. "86400"), `cache_key_hostname`, `cdn_origin_hostname`,
  `cdn_origin_port`, `cdn_service_domain_prefix` (globally unique), `forward_host_header`, `name`,
  `origin_hostname_type`. Enum guesses (unvalidated live): cache_key_hostname/forward_host_header=
  `REQUEST_HOST_HEADER`, origin_hostname_type=`DOMAIN`. start/stop/purge use empty body `{}`.
- **Lifecycle:** `regression/scenarios/lifecycles/networking__cdn.json` covers all 7 writes +
  detailcdnservice (via read-chain on `$.cdn.resource_id`). Needs SCP_ALLOW_MUTATIONS +
  SCP_ALLOW_DESTRUCTIVE.
- **Coverage 2026-06-19:** 0→1/9 (listcdnservice). Account has 0 CDN distributions (no borrowable id).

## networking / direct-connect

- **Host:** regional. 8 endpoints. Direct-connect maps to a physical network circuit
  (datacenter pre-provisioning) — mutations are likely hardware/entitlement-gated.
- **List (2xx live 2026-06-19):** `GET /v1/direct-connects` (listdirectconnects) →
  `{count:0, direct_connects:[], page, size}`. No required params. Account has 0 resources.
- **Id-bound GETs (needs-resource):** `showdirectconnect` (GET `/v1/direct-connects/{direct_connect_id}`),
  `listroutingrules` (GET `.../{direct_connect_id}/routing-rules`) — `single_param_chains` already
  wired; fire automatically once a direct-connect exists. List was empty → no id to derive.
- **Writes (mutation-gated):** createdirectconnect (POST), setdirectconnect (PUT), createroutingrule
  (POST sub), deletedirectconnect/deleteroutingrule (DELETE). Need SCP_ALLOW_MUTATIONS
  (+SCP_ALLOW_DESTRUCTIVE); check circuit-provisioning entitlement first.
- **Note:** doc_url fetches returned 503 during the 2026-06-19 run (docs server down, transient).
- **Coverage 2026-06-19:** 0→1/8 (listdirectconnects).

## networking / vpn

- **Host:** regional. 10 endpoints (gateways + tunnels). Resources are `heavy: true`
  (chain: vpc + public-ip → vpn-gateway → vpn-tunnel).
- **Region gate:** kr-west1, kr-east1, kr-south3.
- **Quota:** max 3 VPN gateways/account, 5 tunnels/gateway.
- **Lists (2xx live 2026-06-19):**
  - `GET /v1/vpn-gateways` (listvpngateways) → `{count, page, size, sort, vpn_gateways:[]}`
  - `GET /v1/vpn-tunnels` (listvpntunnels) → `{count, page, size, sort, vpn_tunnels:[]}`
  - sort default `["name:asc","id:desc"]`. No required query params. Both empty (account has 0).
- **Id-bound GETs (needs-resource):** showvpngateway/showvpntunnel — fake-id probes 404 with
  error codes `scp-network.vpn-gateway.not-found` / `vpn-tunnel.not-found` (backend reachable).
- **Naming constraint:** gateway AND tunnel names = 3-20 **alphanumeric chars ONLY** (no
  hyphen/underscore). IKE/IPSec enums (phase1/phase2_encryptions, diffie_hellman_groups) are
  docs-estimates, NOT yet live-validated.
- **Lifecycle:** `networking-vpn-gateway-tunnel` (`networking__vpn.json`) covers all 8 remaining
  (2 id-bound GETs + 6 writes). Needs SCP_ALLOW_MUTATIONS + SCP_ALLOW_DESTRUCTIVE + SCP_RUN_HEAVY,
  with VPC + public-ip prereqs provisioned first.
- **Coverage 2026-06-19:** 0→2/10 (both list GETs).

## management / cloudcontrol

- **Host:** management. 15 endpoints (6 GETs + 9 writes). **Hard entitlement wall.**
- **Entitlement-403:** every tested GET returns `CloudControl.CloudControlForbidden`
  ("Can not access this cloudcontrol resources.") — identical for all 6. Cloud Control
  requires a **Landing Zone** to exist on the **Organization management account** (prereqs:
  Organization + ID Center + Object Storage + Logging&Audit). Shared test account is not the
  org-master and has no landing zone → all reads AND writes denied. NOT a product bug.
- **Required params (already in sidecar, do NOT unlock 2xx on shared account):**
  `listguardrailsfortarget` needs `target_id`; `listtargetsforguardrail` needs `guardrail_id` +
  `target_type` (e.g. ACCOUNT). Without them → 400 ValidationError; with them → still 403.
- **Quirks:** `createaccountfactoryaccount` (POST /v1/accounts) is BILLABLE + IRREVERSIBLE
  (provisions a real member account); landing-zone create cannot be cancelled mid-flight;
  AuditBaseline feature scheduled 2026-07 (endpoints may 404/400 until then).
- **Lifecycle:** `cloudcontrol-landing-zone-guarded` (`management__cloudcontrol.json`,
  heavy:true) covers all 9 writes, expects 403 on a shared account.
- **Coverage 2026-06-19:** 0/15. Unblock only from an org-master (control-tower admin) account
  with a Landing Zone provisioned.

## security / certificatemanager

- **Host:** regional. self-sign needs `cn`, `not_before_dt`, `not_after_dt`,
  `organization`, `region`, `timezone` → `$.certificate.id`. Synchronous.

## management / resourcemanager

- **Host:** **global** (no region segment). resource-group `$.resource_group.id`
  (+ `srn` soft capture).
- **CRITICAL: SRN and key path segments must be base64-encoded** (confirmed
  2026-06-19). All endpoints with `{srn}` or `{key}` path params require the
  value to be `base64.b64encode(value.encode()).decode()` before URL insertion.
  Plain SRN or plain key yields 400 "SRN decoding error occurred. utf-8 codec
  can't decode byte...".
  - `GET/PUT/DELETE /v1/tags/{srn}` → `{srn}` = b64(srn)
  - `GET/PUT/DELETE /v1/tags/{srn}/{key}` → both = b64(srn) and b64(key)
  - `GET/PUT/DELETE /v1/resources/{srn}` → `{srn}` = b64(srn)
  - `GET/PUT/DELETE /v1/tags/{region}/{service}/{resource_type}/{identifier}/{key}`
    → region/service/resource_type/identifier are PLAIN; only `{key}` = b64(key)
  - `/v1/tags/bulk` (PUT) and `/v1/tags` (DELETE bulk) use PLAIN SRN in JSON body
    (not path), so no b64 needed there.
  - **Engine support (2026-06-19):** the scenario engine now implements a
    `b64_encode` step action (`{"action":"b64_encode","input":"{rg_srn}",
    "output":"rg_srn_b64"}`) — it base64-encodes a captured ctx var and publishes
    it as a new placeholder, so `resourcemanager-tag-lifecycle` is runnable (was
    referencing undefined `*_b64` placeholders → 10 validator errors, now fixed).
    Reusable by any service whose path segments are b64-decoded (e.g. the stuck
    iam `srn`-targeted ops: addpermission/setpermission/setresourcepolicy).
- **listresources response shape:** `$.resources[]` (NOT `$.contents[]`). Fields:
  `region`, `service`, `resource_type`, `id` (NOT `resource_identifier`).
- **Coverage 2026-06-19:** 12 → **27/27** (100%). All 27 endpoints covered.
  No remaining gaps.

## application-service / queueservice

- **Host:** regional. queue `$.id`; create body includes retention/size/key-reuse
  periods + units.

## management / iam

- **Host:** global-ish (management). 62 endpoints. Cross-link
  `validated-facts.md`.
- **Coverage levers (2026-06-18):**
  - *Read-only LISTs (no gate, via smoke):* `accesskeylist`, `listendpoints`,
    `listgroup`, `listpolicy`, `listrole`, `listsamlprovider`,
    `listserviceaccount` all return 200 on a bare GET — pure smoke wins.
  - *List→show chains (no gate, via read_chains):* `showrole`,
    `showserviceaccount`, `accesskeyshow`, `listrolepolicybindings` derive an id
    from their sibling list and return 200. `showgroup`/`showpolicy` likewise.
  - *Account-scoped read:* there is **no `/v1/accounts` or `/v1/users`
    collection list** (both 404). `account_id` is only derivable from a real
    role's `$.role.account_id` (a `/v1/roles` item carries `account_id`). Use it
    to call `listiamuser` = `GET /v1/accounts/{account_id}/users` (200, returns
    `{users:[], count,page,size,sort}`). This is why read_chains can't auto-pair
    listiamuser/getiamuser/listuserpolicybindings — the parent list doesn't exist.
  - *Group/policy lifecycles (MUTATIONS gate):* `creategroup` → `$.group.id`,
    `createpolicy` → flat `$.id`. Both delete cleanly. Already enabled.
- **Empty-collection blockers (needs-peer-resource):** this account has **0 SAML
  providers, 0 IAM users, 0 resource-policies** → `showsamlprovider`,
  `getiamuser`, `listuserpolicybindings`, `updateiamuser`,
  `updateiamuserpassword`, `deleteiamuser`, `showresourcepolicy` have no id to
  target and can't be covered read-only until a peer creates one.
- **Product-bug blocker (5xx, baselined):** `createrole` → 500
  ContactAdminForAssistance. This blocks the whole role-mutation chain
  downstream (`setrole`, `addrolepolicybindings`, `removerolepolicybinding`,
  `removebulkrolepolicybindings`, `setroletrustpolicy`, `deleterole`,
  `deletebulkrole`). `iam-role` lifecycle stays `enabled:false`.
- **Entitlement / validation blockers:** `adduserpolicybinding` /
  `removeuserpolicybinding` → 403; resource-policy mutations
  (`addpermission`/`setpermission`/`removepermission`/`setresourcepolicy`/
  `deleteresourcepolicy`) were 400 (SRN decoding error) — **FIXED 2026-06-20** (see
  below).
- **b64-SRN fix (2026-06-20):** The iam gateway decodes `{srn}` path segments
  as base64, the same way resourcemanager does. Plain SRN in
  `/v1/resource-policies/{srn}` yields 400 "SRN decoding error". The fix mirrors
  the resourcemanager pattern: `GET /v1/resources` (resourcemanager cross-service
  step) soft-captures `$.resources[0].srn` → `iam_srn`; a `b64_encode` step
  produces `iam_srn_b64`; all 5 srn-targeted write paths use `{iam_srn_b64}`:
  - `PUT /v1/resource-policies/{iam_srn_b64}` (setresourcepolicy)
  - `GET /v1/resource-policies/{iam_srn_b64}` (showresourcepolicy)
  - `POST /v1/resource-policies/{iam_srn_b64}/statements` (addpermission)
  - `PUT /v1/resource-policies/{iam_srn_b64}/statements/{unique}` (setpermission)
  - `DELETE /v1/resource-policies/{iam_srn_b64}/statements/{unique}` (removepermission)
  - `DELETE /v1/resource-policies/{iam_srn_b64}` (deleteresourcepolicy)
  The `{sid}` path segment (`{unique}`) does NOT need b64 encoding. After the fix
  the calls will pass the SRN decoder; they may still 404 (no resource-policy on
  that resource) or 403 (no write permission). The b64_encode step is `optional`
  so a missed capture degrades gracefully to a placeholder that still calls the
  endpoint. Wired in `iam-resource-policy` lifecycle (`management__iam.json`).
- **Coverage 2026-06-18 → 2026-06-20:** 15 → **28 / 62** (read-only levers +
  wave5-iam-bindings; b64-SRN fix is pre-mutation and awaits the light CRUD run).

## networking / dns

- **Host:** regional (`dns.<region>.<env>...`). 22 endpoints. Three resource
  families: **private-dns**, **hosted-zones** (+ records), **public-domain-names**.
  Cross-link `validated-facts.md` (networking quotas).
- **Upstream flapping (System Issue, 2026-06-18/19):** the dns host intermittently
  returns `503 upstream connect error / connection timeout` at the edge proxy even
  though auth is fine (vpc 200s with the same creds). It recovers in windows — a
  read probe that rides 503 with retry eventually gets the 200. Not auth, not our
  body; an infra reachability blip. If every dns call 503s, retry later rather than
  treating it as a regression.
- **Read-only LISTs (no gate, the live levers):** `listhostedzone`
  (`GET /v1/hosted-zones`), `listprivatedns` (`GET /v1/private-dns`),
  `listpublicdomainnames` (`GET /v1/public-domain-names`) all return **200** on a
  bare GET. Response envelopes (this account is EMPTY — count 0 in all three):
  `{hosted_zones:[],count,page,size,sort}`, `{private_dns:[],...}`,
  `{public_domain_names:[],...}`. These are the only dns endpoints coverable
  read-only; everything else is id-bound or mutating.
- **id-bound reads blocked behind a HEAVY create:** `showprivatedns`,
  `showhostedzone`, `listhostedzonerecords`, `showhostedzonerecord` need a real
  private-dns + hosted-zone + record to exist first. The account is empty and
  private-dns create is a **very slow provisioner (hours, `heavy:true`)** — so on a
  no-heavy run there is no id to target. They are covered (modeled, not
  runtime-proven) by the `networking-dns-hosted-zone-private` heavy lifecycle.
- **createpublicdomainname → 500 (Product Bug, baselined):** `POST
  /v1/public-domain-names` returns 500 InternalServerError / ContactAdminForAssistance.
  **Our body is proven correct** — byte-for-byte the documented `createpublicdomainname/1.3`
  request_example with every required field of `createpublicdomainrequest`
  (`data/api_docs.json`): address_type, name, all four `domestic_*_address_*`, the
  `overseas_*` trio, postal_code, register_email/name/telno. Not a malformed-body
  4xx mis-surfacing — a genuine backend fault, same ContactAdmin class as iam
  createrole / budget createaccountbudget. Also a REAL PAID DOMAIN REGISTRATION
  order, so the lifecycle never retry-chains it. Now in `data/baselines/known_issues.json`.
- **Quotas:** Private DNS = 1/account (account-global; a 2nd create 4xx's —
  `validated-facts.md`); Hosted Zone = 20/account, 100 records/zone; record types
  A/AAAA/CNAME/TXT/MX/SPF/NS/SOA.
- **Body/capture quirks:** hosted-zone create response is a **bare envelope** —
  id at `$.id`, NOT `$.hosted_zone.id`; record id likewise `$.id`. private-dns
  capture is `$.private_dns.id`. private-dns must reach `state: ACTIVE` before
  setters work (400 invalid-state while CREATING). All encoded in
  `regression/scenarios/lifecycles/networking__dns.json`.
- **Coverage 2026-06-19:** 3 → **6 / 22** observed (the 3 list GETs newly live-200;
  the public-domain 500 + 2 record-write probes were already observed). Remaining
  16 are write ops (lifecycle-modeled, gate-only) or id-bound reads blocked behind
  the heavy private-dns create. Account left clean (no resources created).

## data-analytics / quick-query

- **Host:** regional (`quick-query.<region>.<env>...`). 12 endpoints.
- **Timeout quirk:** service responds in ~1-2s, but the default client `timeout=60s`
  with retries can make a probe appear to hang. Use `timeout=5, retry=False` for probes.
- **Read-only 2xx (live 2026-06-19):**
  - `checkduplicationquickquery` — `GET /v1/quick-query/{quick_query_name}/check-duplication`
    works with a SYNTHETIC name in the path (no resource needed) → `{"result":false}`.
  - `getquickqueryimageversions` — `GET /v1/quick-query/image-versions`. No params. Returns
    available image versions.
  - `getquickquerylist` — `GET /v1/quick-query` **requires `page` + `size` query params**
    (both `required:true` in sidecar). Bare call → 400 ValidationError "Field required". With
    `?page=0&size=10` → 200 `{contents:[], page, size, sort:["created_dt:desc"], total_count}`.
    Smoke now defaults page=0/size=10 (commit bdd0550e).
- **Id-bound GET (needs-resource):** `getquickquery` (GET `/v1/quick-query/{quick_query_id}`)
  → fake id 404 "Quick Query Not Found" (PRODUCT-AI-ANALYTICS-USER-0002). 0 resources in account.
- **Writes (mutation-gated, heavy-prereq):** createquickquery + 6 PUT updates + deletequickquery
  + validatequickqueryresources. `create` and `validate` both require a real **k8s `cluster_id`**
  (billable cluster prereq) in the body.
- **Coverage 2026-06-19:** 1→3/12 (+imageversions, +getquickquerylist; checkduplication already counted).

## data-analytics / data-ops

- **Host:** regional. 17 endpoints. Provisions and manages Apache Airflow
  (data-ops-service = cluster, data-ops = workflow/DAG service on top).
- **COMPOSITE-CREATE (HEAVY/BILLABLE):** `POST /v1/data-ops-services` creates a
  billable Airflow cluster. `POST /v1/data-ops` creates a data-ops instance on
  top of an existing service. Both are `heavy:true` in the lifecycle; never fire
  without `SCP_RUN_HEAVY=true`.
- **ID formats (live-confirmed 2026-06-19):**
  - data-ops id: must start with `DOPS-` (e.g. `DOPS-<uuid>`)
  - data-ops-service id: must start with `DOPS_SERVICE-` (e.g. `DOPS_SERVICE-<uuid>`)
  - cluster_id: 32-hex-char UUID (no hyphens, e.g. uuid4().hex)
- **check-duplication endpoints:** both GET endpoints with name path-params
  (`/v1/data-ops/{data_ops_name}/check-duplication` and
  `/v1/data-ops-services/{data_ops_service_name}/check-duplication`) return **200**
  `{"result":false}` for any valid name including probes. No resource needed.
  `params: {}` added to scenario steps so engine records under catalog key.
- **id-bound GETs (non-2xx, soft, needs heavy-prereq):**
  - `getdataopsdetail` (`/v1/data-ops/{data_ops_id}`) → 404 when resource missing
  - `getdataopsservice` (`/v1/data-ops-services/{data_ops_service_id}`) → 404 when missing
  - `getdataopssubversion` (`/v1/data-ops-services/data-ops/{data_ops_id}/sub-versions`) → **400** (NOT 404!) "dataOps is null" when ID valid format but resource missing
  - `getingresscontrollerlistv1` (`/v1/data-ops/clusters/{cluster_id}/ingress-controllers`) → 404 when cluster missing
- **validate-resources POSTs:** dry-run preflight the console calls before create/update.
  Still classified `mutating=True` (POST). Need real cluster_id/service_id AND
  `SCP_ALLOW_MUTATIONS=true`. Heavy-prereq blockers.
- **Response envelope:** list endpoints use `{contents:[], total_count}`. Detail uses
  flat object or wrapped object (not yet confirmed — no existing resources).
- **image-versions:** `GET /v1/data-ops/image-versions` → `{contents:[{image_id, image_name, version}], total_count}`. Currently returns 1 version: `4.1.1`.
- **Coverage 2026-06-19:** 3 → **5/17** read-only. Remaining 12 are heavy-prereq
  blockers (4 id-bound GETs + 8 mutating writes all depend on existing billable cluster).

---

## management / organization

- **Host:** global (`organization.e.samsungsdscloud.com` — no region segment).
  37 endpoints covering the full org-tree: organizations, org-units (OUs), member
  accounts, service-control-policies (SCPs), policy-bindings, delegation-policies,
  and invitations.
- **Account status (2026-06-19, account ec11538abf8f46d2953539521f745366):** this
  test account is a **MEMBER** (not master/payer) of an existing org.
  `listorganizations` returns `{count:0, organizations:[]}` — member accounts
  cannot see the org list (only the master can). `createorganization` returns 409
  `AccountAlreadyExistInOrganization` confirming org membership. Most org management
  operations return **403 Forbidden** (`You do not have permission to List/Read/Create`)
  because the test account lacks org-admin IAM actions.
- **Coverable read-only (2xx) without org-master:**
  - `GET /v1/organizations` → 200 `{count:0, organizations:[], page, size, sort}` (member sees own org slot — empty for non-master)
  - `GET /v1/account-invitations` → 200 `{account_invitations:[], count:0}` (inbound invitations for this account — currently empty)
- **Entitlement-403 blockers (7 GET endpoints):** `listaccounts`, `listorganizationunits`,
  `listservicecontrolpolicies`, `listorganizationinvitations`, `showdelegationpolicy`,
  `listpoliciesfortarget`, `listtargetsforpolicy` — all return 403 Forbidden
  consistently, regardless of query params. These require org-master/admin privilege.
- **Id-bound GET blockers (5 endpoints):** `showorganization`, `showorganizationunit`,
  `showaccount`, `showservicecontrolpolicy`, `listparents` — can't get valid IDs
  without org-master privilege to list the parent resources. `showorganizationunit`
  with a fake ID returns 404; others return 403.
- **Mutating endpoint classification (live-proven 2026-06-19):**
  - `createorganization` → 409 (account already in org) — reachable, not 403
  - `createorganizationunit`, `createservicecontrolpolicy`, `attachpolicybindings`,
    `removepolicybindings`, `deleteorganizationunits`, `deleteservicecontrolpolicies`,
    `deletedelegationpolicy`, `setorganizationunit`, `setservicecontrolpolicy`,
    `leaveorganization`, `deleteorganization`, `deleteaccount` → 403 entitlement
  - `createaccount`, `createdelegationpolicy`, `createinvitation`, `moveaccount`,
    `setorganization`, `setdelegationpolicy`, `removeaccounts` → 400 ValidationError
    (body shape incomplete but endpoint is reachable)
  - `cancelinvitations`, `acceptinvitation`, `declineinvitation` → 404 (no pending
    invitations) — reachable, not 403
- **Proven body fields (2026-06-19):**
  - `createorganizationunit`: needs `name` + `parent_unit_id` (validated: these fields
    move response from 400 to 403, confirming field names are correct)
  - `moveaccount`: needs `account_id` + `parent_unit_id` (NOT `parent_id` — `parent_id`
    gives 400 "Extra inputs not permitted")
  - `createinvitation`: needs `organization_id` (validated field) + 1 unknown field
    (docs JS-rendered, not captured; `email`, `message`, `login_id` are all invalid)
  - `listservicecontrolpolicies`: required query param `organization_id`
  - `listorganizationunits`: required query param `parent_unit_id` ('ROOT' for root level)
- **Response envelopes (live-proven):**
  - `listorganizations` → `$.organizations[0].id`
  - `listaccountinvitations` → `$.account_invitations[0].id`
  - Inferred (403 so not confirmed live): `organization_units`, `service_control_policies`,
    `organization_accounts`, `organization_invitations`
- **Coverage ceiling:** 2/37 without org-master privilege. All 37 endpoints probed
  and observations recorded. The 35-gap is blocked by entitlement-403 (org-master
  required for most ops). Coverage would rise to potentially 25+ on an org-master
  account (the read + write ops that return 403 here would return 200/201).
- **Coverage 2026-06-19:** 0 → **2 / 37**. All 37 endpoints reached and classified.

---

## management / cloudmonitoring

- **Host:** regional (`cloudmonitoring.<region>.<env>...`). 18 endpoints.
  EOL service (sunsets 2026-09 → ServiceWatch). All endpoints coverable as
  static-reachability probes even without deep validation.
- **X-ResourceType header (CONFIRMED REQUIRED for multiple endpoints):**
  Several endpoints require an `X-ResourceType: <productTypeCode>` HTTP header
  (NOT a query param `resourceType`). Without it: 400 `InvalidHeaderValue`
  `{name:"resourceType", value:"X-ResourceType"}`. Confirmed productTypeCodes
  from `GET /v1/cloudmonitorings/product/v1/product-types`: `VM`, `Redis`,
  `Bare Metal Server`, `Kubernetes`, `KAFKA`, `Object Storage`, etc. (37 total).
  - `getaccountproductlist` (`GET /v1/cloudmonitorings/product/v2/accounts/products`):
    requires `X-ResourceType` header. Returns 200 (empty list if no resources of
    that type). Verified live: `X-ResourceType: VM` → 200.
  - `getproducteventpolicylist` (`GET /v1/cloudmonitorings/event/v2/event-policies`):
    requires BOTH `X-ResourceType` header AND `productResourceId` query param.
    `productResourceId` is the resource name (e.g. `apitest-logsink` for Object
    Storage). Verified live: `X-ResourceType: Object Storage` +
    `productResourceId=apitest-logsink` → 200 (empty list).
  - Likely required for `getproducteventlist` too but that endpoint also
    consistently 400s (see bug below).
- **Live resources in test account (2026-06-19, kr-west1):**
  - Object Storage products: `productResourceId=apitest-logsink` (state=NE),
    `apitest-oplog-permanent` (state=NE). Discovered via
    `GET /v1/cloudmonitorings/product/v2/accounts/products` with
    `X-ResourceType: Object Storage`.
  - Block Storage(VM): 1 product (UUID, state=Running). No VMs, no addrbooks.
  - All products have state "NE" (not enrolled in monitoring backend) except
    Block Storage(VM). Even the Running Block Storage product returns 400 for
    event policy create — the "NE" Object Storage products return "InvalidRequest".
  - No addrbooks (totalCount=0). Addrbook steps normalize to placeholder '*' key.
- **getaccounteventlist 400 / backend bug (CONFIRMED):**
  `GET /v1/cloudmonitorings/event/v2/accounts/events` consistently returns 400
  `InvalidInputValue` with `{name:"resourceType", value:<queryEndDt-date>}` regardless
  of what params are provided. The API is erroneously treating the queryEndDt query
  parameter value as the value of the "resourceType" field. This is a backend parameter
  binding bug — the correct params (eventState, queryStartDt, queryEndDt, X-ResourceType
  header) all fail the same way. **Classify: validation-400 / backend-param-binding bug.**
  Dual-recorded in scenario with `params` so it counts as reached (soft category).
- **getproducteventlist 400 / similar bug:**
  `GET /v1/cloudmonitorings/event/v2/events` behaves similarly — after providing
  `productResourceId + eventState + queryStartDt`, still errors on queryEndDt date.
  Tried with `X-ResourceType` header, with/without all params. Backend issue same class.
  **Classify: validation-400 / needs-real-event-data or backend bug.**
- **api_catalog_params.json sidecar gaps vs live behavior:**
  - Sidecar declares `getaccounteventlist` requires `eventState, queryStartDt, queryEndDt`
    but NOT `resourceType`. Live API also needs `resourceType` (as header or query)
    but the backend bug makes it 400 regardless.
  - Sidecar declares `getaccountproductlist` has NO required params. Live API requires
    `X-ResourceType` header — sidecar is missing this (header params are outside the
    sidecar's query_params scope).
- **Confirmed 200 endpoints (bare GETs — smoke-covered):**
  - `getaccountmembers` — bare GET, 200
  - `getadressbooklist` — bare GET, 200 (empty)
  - `getmetriclist` — bare GET, 200 (3510 metrics across 37 product types)
  - `getproducttypelist` — bare GET, 200 (37 product types)
- **Id-bound GETs (5 endpoints, getevent*/geteventpolicy*/getadressbookmember):**
  Need event-id or eventPolicyId (no events in this account without SCP_RUN_HEAVY)
  or addrbookId (no addrbooks). Scenario normalizes to '*' placeholder key when
  no real ID is captured.
- **puteventpolicy body shape (CONFIRMED cascade-revealed 2026-06-19):**
  Must wrap all fields in `eventPolicyRequest` key. Required cascade-field order:
  `disableYn`, `isLogMetric`, `eventLevel`, `ftCount`, `eventThreshold`. Once all
  present the API returns `{"code":"InvalidRequest","params":[null]}` — backend
  business rule validation fails (products in NE state). Full confirmed body:
  `{"eventPolicyRequest": {"eventPolicyName": "...", "productTypeCode": "Object Storage",
  "productResourceId": "apitest-logsink", "metricKey": "<key>", "eventLevel": "WARNING",
  "disableYn": "N", "isLogMetric": false, "eventThreshold": 100.0, "ftCount": 1}}`.
  The `InvalidRequest` with `null` params is an account-level blocker (products not
  enrolled in monitoring). **Classify: account-prereq / entitlement-class blocker.**
- **getmetricperfdatalist body shape (CONFIRMED cascade-revealed 2026-06-19):**
  POST `/v1/cloudmonitorings/product/v2/metric-data`. Required fields:
  `productTypeCode`, `productResourceId`, `queryStartDt` (ISO 8601 with T/Z suffix),
  `queryEndDt` (ISO 8601 with T/Z suffix), `metricDataConditions` (array of objects).
  Without T-suffix dates: 400 `resourceType=queryStartDt` backend bug. With T-suffix
  dates: 404 `productResourceInfos not found` — same account-level prereq.
  Example body: `{"productTypeCode":"Object Storage","productResourceId":"apitest-logsink",
  "queryStartDt":"2026-05-21T00:00:00Z","queryEndDt":"2026-06-19T23:59:59Z",
  "metricDataConditions":[{"metricKey":"objectstorage.usage.bucketSizeBytes",
  "statisticType":"AVG","period":3600}]}`.
  **Classify: account-prereq / products not in monitoring backend.**
- **Mutating endpoints (3):** `puteventpolicy` (POST create), `modifyeventpolicy`
  (PUT), `deleteeventpolicy` (DELETE). All need `SCP_ALLOW_MUTATIONS=true` +
  `SCP_ALLOW_DESTRUCTIVE=true`. Body shape now confirmed from cascade (see above).
  Cannot reach 2xx without monitoring-enrolled resources. Blocker: account-prereq.
- **Coverage 2026-06-19:** 0 → **6/18** confirmed 200 ok:
  getaccountmembers, getadressbooklist, getmetriclist, getproducttypelist (smoke),
  getaccountproductlist, getproducteventpolicylist (X-ResourceType header probes).
  Remaining 12 blocked by: backend date-parsing bug (getaccounteventlist,
  getproducteventlist), missing monitoring enrollment (puteventpolicy,
  modifyeventpolicy, deleteeventpolicy, getmetricperfdatalist), no real resource
  IDs for id-bound GETs (geteventdetail, geteventnotificationstates,
  geteventpolicydetail, geteventpolicyhistories, geteventpolicynotification,
  getadressbookmemberlist).

---

---

## management/network-logging

- **Host / service key:** `network-logging`
- **Catalog endpoints (4):** `listnetworkloggingconfigurations` (GET /v1/network-logging/configurations), `listnetworkloggingstorages` (GET /v1/network-logging/storages), `createnetworkloggingstorage` (POST), `deletenetworkloggingstorage` (DELETE).
- **REQUIRED query param for both GETs:** `resource_type`. Valid enum values (confirmed live 2026-06-20): `FIREWALL`, `SECURITY_GROUP`, `NAT`. The value `VPC_FLOW_LOG` is REJECTED with 400 ("Input should be 'FIREWALL', 'SECURITY_GROUP' or 'NAT'").
- **Backend stability:** Both GET endpoints exhibit transient 503s (no body). In a healthy window they return 200 with empty lists (`count:0`). The client engine retries handle this; scenarios use `expect_status: [200, 403]` so 503 retry path does not red the run.
- **Scenario file:** `regression/scenarios/lifecycles/management__network-logging.json`; both GET steps now carry `"params": {"resource_type": "FIREWALL"}`.
- **Coverage 2026-06-20:** 2 existing + 2 GET list steps now have correct `resource_type` param fix applied (was missing -> 400 ValidationError; fixed to FIREWALL -> 200 in healthy window).

---

## container/ske

- **Host / service key:** `ske`
- **listimages (GET /v1/images):** `scp_original_image_type` is REQUIRED. Only confirmed valid value: `k8s`. Without the param: 400 ValidationError "Field required". With `k8s`: 200, returns `nodepool_images` array (count:15 on 2026-06-20).
- **Scenario files updated:** `container__ske.json` (added `list-images` step with `params: {scp_original_image_type: k8s}` to `ske-read-coverage` lifecycle); `generated__heavy-ske.json` (converted inline query-string path to `params` dict for `create-ske-image` and `verify-ske-image-images-page` steps).
- **Coverage 2026-06-20:** `listimages` unblocked — 400→200 confirmed live.

---

## Services not yet deeply explored (stubs — fill in as you go)

database (mysql, mariadb), data-analytics, ai-ml, financial-management,
platform, devops-tools, and the long tail of management/networking/storage.
These have the most uncovered endpoints — see `scenario-catalog.md` gap list.
