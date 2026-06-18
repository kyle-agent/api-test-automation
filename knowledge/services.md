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

---

## Services not yet deeply explored (stubs — fill in as you go)

database (mysql, mariadb), data-analytics, ai-ml, financial-management,
platform, devops-tools, and the long tail of management/networking/storage.
These have the most uncovered endpoints — see `scenario-catalog.md` gap list.
