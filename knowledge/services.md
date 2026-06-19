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
- **READ-ONLY coverage ceiling (no docker, no mutations):** 8 GETs reachable —
  listregistries, showregistry, listrepositories, showregistry's
  connectable-resources, both check-duplications, showrepository, listimages.
  All 200 on the borrowed resources.
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
  `deleteresourcepolicy`) → 400 (need a real `srn` target).
- **Coverage 2026-06-18:** 15 → **27 / 62** (read-only levers only; no resources
  created, account left clean).

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

## Services not yet deeply explored (stubs — fill in as you go)

database (mysql, mariadb), data-analytics, ai-ml, financial-management,
platform, devops-tools, and the long tail of management/networking/storage.
These have the most uncovered endpoints — see `scenario-catalog.md` gap list.
