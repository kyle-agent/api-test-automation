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

## security / certificatemanager

- **Host:** regional. self-sign needs `cn`, `not_before_dt`, `not_after_dt`,
  `organization`, `region`, `timezone` → `$.certificate.id`. Synchronous.

## management / resourcemanager

- **Host:** **global** (no region segment). resource-group `$.resource_group.id`
  (+ `srn` soft capture).

## application-service / queueservice

- **Host:** regional. queue `$.id`; create body includes retention/size/key-reuse
  periods + units.

---

## Services not yet deeply explored (stubs — fill in as you go)

database (mysql, mariadb), data-analytics, ai-ml, financial-management,
platform, devops-tools, and the long tail of management/networking/storage.
These have the most uncovered endpoints — see `scenario-catalog.md` gap list.
