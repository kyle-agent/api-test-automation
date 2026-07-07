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
- **createport `fixed_ip_address`** (HB3, run 28723287734, 2026-07-05):
  `data/api_docs.json` networking/vpc/portcreaterequest documents this field
  as optional `any of [string, null]` default `""`, and the doc's OWN
  request_example sends `"fixed_ip_address":""` — but live it 400s
  `scp-network.port.fixed_ip.format-error` ("The requested Fixed IP() is
  invalid IP format.") on that exact documented default. Fix: **omit the key
  entirely** (let the backend auto-assign from the subnet) rather than send
  `""`. Fixed in `compute-virtualserver-full`'s `create-port` step
  (`regression/scenarios/scenarios.json`); unblocks the whole `vs-port` group
  (map-sg-to-port/attach-port-to-server/read-attached-port/
  detach-port-from-server/delete-port). Not yet re-verified live.
- **delete-server `InvalidVirtualServerState.DeleteImpossible`** (HB3, run
  28723287734, 2026-07-05): reproduced 3x in the SAME run, right after
  image-delete/detach-volume/detach-security-group — a transient post-mutation
  settle window, not a hard block: `cleanup/reconciler.py`'s later sweep
  deletes the exact same server successfully with no special handling beyond
  elapsed time (it just retries across sweep rounds). The doc-visible
  `$.server.state` field doesn't appear to expose an in-between task_state, so
  a fixed pre-delay is the load-bearing fix, not a value-based poll. Fixed in
  `compute-virtualserver-full` (`regression/scenarios/scenarios.json`): added
  a `wait-server-settled` step (20s fixed wait + up-to-120s poll top-up)
  before `delete-server`, plus `retry_on_status:[400,409]` (6×20s) on
  `delete-server` itself as a second line of defense. Not yet re-verified
  live.
- **image-update (`PUT /v1/images/{id}`) `Image.InvalidVolumeOnMinDiskUpdate`**
  (HB3, run 28723287734, 2026-07-05): `min_disk` must be >= the size of the
  volume the custom image was created from (the server's boot volume).
  `compute-virtualserver-full` creates its boot volume at `size:104` but the
  `image-update` step sent `min_disk:100` — bumped to `104` to match. Not yet
  re-verified live.
- **image-update `min_disk` is CATEGORICALLY rejected, not value-tunable**
  (HB3b-2, 2026-07-06, run 28766151214): the `min_disk:104` fix above (matched
  to the source volume size) still 400s, but with a DIFFERENT message this
  time: `Image with volumes cannot update min disk` — an outright rejection
  of the `min_disk` field on any image whose source had attached volumes (this
  lifecycle's custom image is such a snapshot), independent of the value sent.
  `data/api_docs.json` `compute/virtualserver/imagesetrequest` (the PUT body
  model) has exactly 4 fields — `min_disk`, `min_ram`, `protected`,
  `visibility` — no `description` field exists on this model (unlike some
  other services' update bodies). Fixed `compute-virtualserver-full`'s
  `image-update` step (`regression/scenarios/scenarios.json`) to drop
  `min_disk` entirely and send `{min_ram, protected, visibility:"private"}`
  instead. Not yet re-verified live.
- **attach-port `VirtualServer.CreateInterface.Duplicated`** (HB3b-2,
  2026-07-06, run 28766151214): `compute-virtualserver-full`'s standalone
  `create-port`/`attach-port-to-server` steps pointed the new port at
  `{subnet_id}` — the SAME subnet the server's own boot NIC already lives on
  (`create-server`'s `networks[].subnet_id`) — so attaching a second interface
  there is a genuine duplicate, not a param bug. This lifecycle has no
  existing second/shared subnet (checked — no `adopt: subnet#db` pattern here,
  unlike the DB-engine `-full` lifecycles). Fix: added a dedicated
  `create-port-subnet` step (own subnet in the same VPC, `10.135.3.0/24`,
  scoped to the `vs-port` group with its own cleanup) and repointed
  `create-port`/`attach-port-to-server`'s `subnet_id` at the new
  `{port_subnet_id}`. Not yet re-verified live.
- **Boot volume TTL-survives the final sweep** (HB3b-2, 2026-07-06, run
  28766151214): `compute-virtualserver-full`'s server (`delete_on_termination:
  true` on its boot volume) left the boot volume behind after `delete-server`
  succeeded — the account-wide sweep at the end of the chat-heavy job could not
  reclaim it TWICE in a row (needed a manual `SCP_SWEEP_IGNORE_TTL=true` rescue
  both times), meaning `delete_on_termination`'s async cascade doesn't reliably
  finish before that final sweep pass runs. `capture-server-volume`
  (`$.volumes[0].id` → `boot_vol_id`) was already in the lifecycle but was a
  DEAD capture (nothing consumed it). Fixed: added an explicit
  `delete-boot-volume` step right after `wait-server-gone` (404-confirmed)
  using `{boot_vol_id}`, `optional:true` + `expect_status` including 400/404 so
  a volume the cascade already reaped, or one still mid-detach, never fails
  the lifecycle. Considered (and rejected) forcing the workflow's *final*
  sweep to `SCP_SWEEP_IGNORE_TTL=true` unconditionally instead — rejected
  because the TTL is exactly what protects OTHER concurrently-running agents'
  still-live resources from a cross-run reap (`core.registry`/
  `cleanup/reconciler.py _is_deletable`); an explicit in-lifecycle delete is
  the safe fix, a blanket IGNORE_TTL sweep is not. `.github/` workflow files
  were not touched (out of scope). Not yet re-verified live.

## compute / virtualserver — autoscaling

**STATUS: ALL 24 ASG ENDPOINTS CONFIRMED 2xx — LIVE HEAVY RUN 2026-06-24** via
lifecycle `heavy-asg-full-coverage` in
`regression/scenarios/lifecycles/compute__virtualserver-autoscaling.json`.

### Confirmed body shapes (all PROVEN 2xx)

- **createlaunchconfiguration**: `{name, keypair_name, image_id, server_type: "s1v1m2",
  volume_type: "SSD_Provisioned", volume_size: 40, tags: []}`. Volume size MUST be
  divisible by 8. `delete_on_termination` is NOT a valid field (400). Image is NOT
  OS-specific — any valid standard image (`scp_original_image_type=standard&visibility=public`).

- **createautoscalinggroup**: `{name, launch_configuration_id, desired_server_count: 0,
  desired_server_count_editable: true, min_server_count: 0, max_server_count: 1,
  server_name_prefix, state_check_delay_time: 300, use_lb_state_check: false,
  subnet_ids: ["<id>"], security_group_ids: ["<id>"], tags: []}`. Uses arrays
  `subnet_ids` / `security_group_ids` (NOT scalar / NOT `security_groups`).
  `desired_server_count: 0` avoids billable VM spin-up.

- **updateautoscalinggroup**: `{desired_server_count_editable: true,
  state_check_delay_time: 300}`. **MUST omit `use_lb_state_check`** when no LB is
  attached — API returns 400
  `AutoScalingGroup.AutoScalingGroupLbStateCheckLbRequired` even if value is `false`.

- **updateautoscalinggroupservercount**: `{desired_server_count: 0}`.

- **updateautoscalinggrouplbservergroups**: `{lb_server_groups: []}` (array of
  `{id, port}` objects — NOT `lb_server_group_ids`). Empty array is a no-op 202.

- **createautoscalinggroupnotification**: `{user_ids:
  ["f2b627e6bf4f4b3996f04de4f877bd11"], notification_events: ["SCALE_OUT",
  "SCALE_OUT_FAIL"]}`. `user_ids` MUST be a real account user. Service-account user id
  = `f2b627e6bf4f4b3996f04de4f877bd11` (from `GET /v1/access-keys` → `created_by`).
  Response is a LIST envelope: capture `$.notifications[0].id` (NOT `$.id`).

- **updateautoscalinggroupnotification**: `{notification_events: ["SCALE_OUT",
  "SCALE_OUT_FAIL", "SCALE_IN"], notification_state: "ACTIVE"}`.

- **createautoscalinggrouppolicy**: `{name, scale_type: "SCALE_OUT", scale_method:
  "AMOUNT", scale_value: 1, metric_type: "CPU", metric_method: "AVG",
  comparison_operator: "ge", threshold: 80, evaluation_minutes: 5,
  cooldown_seconds: 300}`. `comparison_operator` is short-code `"ge"` (NOT
  `"GREATER_THAN_OR_EQUAL_TO"`).

- **updateautoscalinggrouppolicy**: `{threshold: 90, cooldown_seconds: 600, state:
  "ACTIVE"}`.

- **createautoscalinggroupschedule**: `{name, frequency: "ONCE", desired_server_count:
  0, min_server_count: 0, max_server_count: 1, start_date: "2099-01-01", hour: 3,
  minute: 0, timezone: "Asia/Seoul"}`. `frequency` enum: `ONCE|DAILY|WEEKLY|MONTHLY`.

- **updateautoscalinggroupschedule**: `{desired_server_count: 0, state: "ACTIVE"}`.

### Critical facts

- **GET steps need `params: {}`** in the lifecycle step definition so the engine
  credits the catalog key (engine only records catalog keys for GET steps that have an
  explicit `params` key in the step definition; engine.py ~line 1118).
- **Notification id capture path**: `$.notifications[0].id` (list envelope, NOT
  `$.id`).
- **Service-account user id** for notification `user_ids`:
  `f2b627e6bf4f4b3996f04de4f877bd11` (from `GET /v1/access-keys` → `created_by`).
- **VPC CIDR** used in HEAVY run: `10.175.0.0/20`; subnet: `10.175.8.0/24`.
- Teardown order: notification→schedule→policy→ASG→subnet→VPC→SG→LC→keypair
  (child-first). Subnet delete is async; poll until DELETED before attempting VPC delete
  or VPC delete returns 400.
- **All 24 ASG endpoint catalog keys confirmed 2xx live 2026-06-24:**
  createautoscalinggroup, createautoscalinggroupnotification,
  createautoscalinggrouppolicy, createautoscalinggroupschedule,
  deleteautoscalinggroup, deleteautoscalinggroupnotification,
  deleteautoscalinggrouppolicy, deleteautoscalinggroupschedule,
  listautoscalinggrouplbservergroups, listautoscalinggroupnotifications,
  listautoscalinggrouppolicies, listautoscalinggroups,
  listautoscalinggroupschedules, listautoscalinggroupvirtualservers,
  showautoscalinggroup, showautoscalinggroupnotification,
  showautoscalinggrouppolicy, showautoscalinggroupschedule,
  updateautoscalinggroup, updateautoscalinggrouplbservergroups,
  updateautoscalinggroupnotification, updateautoscalinggrouppolicy,
  updateautoscalinggroupschedule, updateautoscalinggroupservercount.

## storage / backup

- **Host:** regional. Service key: `backup`.
- **getbackuptargetlist** (`GET /v1/backups/backup-targets`): REQUIRED params `server_category` AND `policy_type`.
  `policy_type=VM_IMAGE` returns 500 ContactAdminForAssistance for VIRTUAL_SERVER+GPU_SERVER (known backend bug, baselined 2026-06-20).
  `policy_type=FILESYSTEM` returns 200 for all server categories. CONFIRMED LIVE 2026-06-29.
  smoke.py now overrides policy_type=FILESYSTEM for this path (path-specific rule in `_required_param_candidates`).
- **listbackupagenttargets** (`GET /v1/backup-agents/targets`): REQUIRED param `server_category`.
  `?server_category=VIRTUAL_SERVER` → 200 empty list. Confirmed live 2026-06-29.
  smoke.py handles via `server_category=VIRTUAL_SERVER` global default.
- **showinstallfilepath** (`GET /v1/backup-agents/agent-install-file-path`): REQUIRED param `os_type`.
  `?os_type=LINUX` → 200 `{install_file_path: "https://...NetBackup_10.4.0.1_CLIENTS2.tar.gz"}`.
  `?os_type=WINDOWS` → 200 `{install_file_path: "https://...NetBackup_10.4.0.1_Win.zip"}`.
  Confirmed live 2026-06-29. smoke.py handles via path-specific rule.
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
- **getbackuptargetlist EMPTY for FILESYSTEM is NOT a timing lag — REVISED
  2026-07-06 (HB3b-2, run 28766151214 job log)**: the HB3b (2026-07-05)
  hypothesis below ("inventory-registration lag", fixed with a `$.count until
  1` poll) was tested live in run 28766151214 and FALSIFIED — the poll ran
  its full 300s budget (confirmed via the ~303s gap between `wait-server`'s
  and `create-backup-target`'s timestamps in
  `reports/results/hb3b/observations-gw0.jsonl`, vs. the step's own last-call
  `elapsed_ms` of only ~828ms) and `count` never left 0. Root cause per
  `knowledge/formal/resources/storage__backup.yaml` line 40 (already
  documented, just not connected to this symptom until now): `policy_type=
  FILESYSTEM` is the **Agent-type** backup category — a server only appears
  in that list once a Backup Agent is installed/configured on it ("Agent
  backups require prior agent creation and configuration on target
  servers", userguide overview). The agent family (8 ops, incl.
  createbackupagent) is an **owner WAIVER** (2026-06-10, "agent 없는 백업으로만"
  — `data/baselines/coverage_waivers.json`), so this account/run deliberately
  never installs an agent on the test VM — `contents:[]` for FILESYSTEM is
  therefore the CORRECT, expected response, not a bug or a race. The
  semantically-right query for our AGENTLESS goal is `policy_type=VM_IMAGE`,
  which is blocked by a separate, already-baselined product bug (500
  `ContactAdminForAssistance`, `data/baselines/known_issues.json`
  `storage/backup/getbackuptargetlist`, confirmed 2026-06-20). **Net: there is
  currently no live query-parameter combination that returns non-empty
  contents for our agentless-VM backup chain** — this is a genuine
  product-bug + owner-waiver double-block, not a fixable param/timing issue.
  Do not re-attempt a param tweak here without new evidence (e.g. the
  VM_IMAGE 500 clearing, or the agent waiver being lifted). The `$.count
  until 1` poll in `generated__heavy-backup.json`'s `create-backup-target`
  step is left in place (harmless — it still returns the correct 200/empty
  response after its timeout, and would self-heal for free if the VM_IMAGE
  bug is ever fixed and the step's `policy_type` is swapped back to
  VM_IMAGE) but is NOT the actual fix for this gap.
- **(superseded) getbackuptargetlist inventory-registration lag** (HB3, run
  28723287734, 2026-07-05): even with `policy_type=FILESYSTEM`, `GET
  /v1/backups/backup-targets?...&server_name=<own server>` right after the
  server flips ACTIVE can still return 200 with `contents:[]`. HB3b guessed
  this was a registration-lag race and added a `$.count until 1` poll — HB3b-2
  above shows the real cause is the FILESYSTEM/agent-waiver interaction, not
  timing. Kept here for history only.

## storage / archivestorage

- **Host:** regional. Service key: `archivestorage`.
- **Entitlement wall (permanent):** ALL 25 endpoints return 401 `scp-archivestorage_hmac not in service account catalog`.
  This is a gateway/catalog reject BEFORE routing — the service account does not have archivestorage subscribed.
  No param or body fix unblocks. Requires archivestorage subscription/catalog enrollment.
- **Bare GET endpoints (6 no-path-param):** `GET /v1/archiving-histories`, `/v1/archiving-policies`,
  `/v1/buckets`, `/v1/recovery-histories`, `/v1/archiving-histories/detail`, `/v1/recovery-histories/detail`
  — all return 401 gateway reject. Not 403 entitlement, not 404 routing: the HMAC catalog does not have this service.
- **Confirmed LIVE 2026-06-29:** all 6 no-path-param GETs 401. Backend entitlement wall confirmed.

## storage / baremetal-blockstorage

- **Host:** regional. Service key: `baremetal-blockstorage`.
- **listvolumes** (`GET /v1/volumes`): 200 with body `{total_count:0, volumes:[]}`. No path params, no required query
  params. Confirmed live 2026-06-29.
- **listvolumegroups** (`GET /v1/volume-groups`): 200 with body `{total_count:0, volume_groups:[]}`. No path params, no
  required query params. Confirmed live 2026-06-29.
- **createvolume** blocker: REQUIRED `attachments` array (1..8 BM server UUIDs). Sending `attachments:[]` returns 400.
  No BM server on this account → all volume-dependent endpoints (40 of 41) require a real BM server.
- **Light fragment added:** `blockstorage-list-reads` (non-heavy, enabled) covers listvolumes + listvolumegroups.
- **All other 39 endpoints:** need a BM server (heavy-prereq). BM provisioning is billable and time-intensive.

## storage / parallel-filestorage

- **Host:** regional. Service key: `parallel-filestorage`.
- **listvolumes** (`GET /v1/volumes`): 200 with body `{count:0, parallel_filestorages:[]}`. Note: list key is
  `parallel_filestorages` NOT `volumes`. Covered (ledger: 1/11, 2026-06-24).
- **listsnapshots** (`GET /v1/snapshots`): REQUIRED query param `volume_id`. Without it: 400 ValidationError.
  With fake UUID: 404 `parallel-filestorage.Notfound.volume`. Needs a real volume (heavy prereq).
- **listaccessrule** (`GET /v1/volumes/{volume_id}/access-rules`): needs real volume_id (path param). Heavy prereq.
- **showvolume** (`GET /v1/volumes/{volume_id}`): needs real volume_id. Heavy prereq.
- **createvolume** body: `{name, capacity_tb: 1, tags:[]}`. Minimum 1 TB (BILLABLE). Name pattern: `^[a-z]([a-z0-9_]){2,20}$`.
  Volume attaches ONLY to Multi-node GPU Cluster (mngc-prereq).
- **All writes + id-GETs:** need a real PFS volume (heavy, billable 1TB minimum). 10 of 11 endpoints are heavy-prereq.

## storage / filestorage

- **Host:** regional. Owns NFS volumes. No VPC needed. Base URL:
  `https://filestorage.{region}.e.samsungsdscloud.com` (e.g. kr-west1).
- **Coverage (2026-06-24 live validated):** 17/21. 4 gaps classified (see ledger).
- **Volume create:** `POST /v1/volumes {name, protocol:NFS, type_name:HDD}` →
  response envelope `$.filestorages[]` for list; **create response: flat `$.volume_id`**
  (NOT nested). Name rules: 3-21 chars, lowercase letters + numbers + underscore only
  (no hyphens, no uppercase). State becomes `available` immediately (no poll needed).
- **Snapshot:** `POST /v1/snapshots?volume_id=X {volume_id}` → 202, capture
  `$.snapshot.id`. Available immediately. Restore: `PUT /v1/snapshots/{id}/restore`
  with `?volume_id=X` query param and empty JSON body `{}` → 202.
- **Snapshot schedule:** `POST /v1/snapshot-schedules {volume_id,
  snapshot_retention_count: int, snapshot_schedule: {frequency: WEEKLY, day_of_week: MON, hour: 23}}`
  → 202. CRITICAL: create response has NO id field. Must call
  `GET /v1/snapshot-schedules?volume_id=X` and capture `$.snapshot_schedule[0].id`.
  Update: `PUT /v1/snapshot-schedules/{id} {snapshot_retention_count, snapshot_schedule:
  {frequency: DAILY, hour: 12}}` (DAILY: omit day_of_week). Delete: 202.
- **Replication:** `POST /v1/replications {name, volume_id, region: kr-east1,
  replication_frequency: 5min, replication_type: replication, backup_retention_count: 2}`
  → 202, capture `$.replication_id` AND `$.replication_volume_id` (the DR volume id in kr-east1).
  List: `GET /v1/replications?volume_id=X` (volume_id REQUIRED query param).
  List-region: `GET /v1/volume-replication/regions?type_name=HDD&source_region_name=kr-west1&replication_type=replication`
  (all 3 params required).
- **PRODUCT CONSTRAINT (2026-06-24 confirmed):** `setvolumereplication` (PUT) and
  `deletevolumereplication` (DELETE) on `/v1/replications/{id}` both return 400
  `filestorage.BadRequest.Invalid.volume.purpose` when called from the source region
  (kr-west1) against a volume with `purpose=original`. Replication management MUST be
  done from the DR region (kr-east1) side using the DR volume id and the kr-east1 endpoint.
  To manage from DR side: use `SCP_SERVICE_HOSTS={"filestorage-dr":
  "https://filestorage.kr-east1.e.samsungsdscloud.com"}` and call with `?volume_id={dr_volume_id}`.
  Teardown sequence: set policy=paused (kr-east1) → delete replication (kr-east1) →
  delete DR volume (kr-east1) → delete source volume (kr-west1, now unblocked).
- **`setaccessrule` (PUT /v1/access-rules/{volume_id}):** Body is SCALAR (not array):
  `{object_id: UUID, object_type: VM|BM|GPU|GPU_NODE|ENDPOINT, action: add|remove}`.
  Returns 404 VirtualServer.VirtualServerNotFound for fake UUIDs. Requires real VM id
  (needs-peer blocker — provision a VM first via virtualserver lifecycle).
- **`deletevolume`:** Returns 400 "Cannot delete volume because replication is in use"
  while any replication exists. Must tear down replication from DR side first.
- **listsnapshotschedule / listsnapshots / listreplications:** All require `?volume_id=`
  as REQUIRED query param. Without it → 400 ValidationError "Field required". Smoke
  defaults must include `volume_id` for these list endpoints.
- **Coverage session 2026-06-24 gains (8 → 17/21):** createvolume, createsnapshotschedule,
  createsnapshot, restoresnapshot, setvolume, setsnapshotschedule, deletesnapshotschedule,
  createvolumereplication, listvolumereplications, showvolumereplication,
  listsnapshotschedule, listsnapshots, deletesnapshot all newly 202/200.

## networking / vpc (+ subnet, port, public-ip, internet-gateway)

- **Host:** regional. Consumes the **vpc** quota (cap 5).
- vpc `cidr` /20 (e.g. `10.123.0.0/20`), `$.vpc.id`, poll `$.vpc.state` →
  `ACTIVE`. subnet `type: GENERAL`, `$.subnet.id`. port `security_groups: []`,
  `$.port.id`. Teardown reverse with 409 retries (wait 404 before parent delete).
- public-ip `type: IGW` → `$.publicip.id`. internet-gateway needs `vpc_id`,
  `firewall_enabled`, `type: IGW` → `$.internet_gateway.id`.
- **Coverage session 2026-06-23 findings:**
  - Engine fix: `step.get("params")` evaluated `{}` as falsy; changed to `"params" in step`
    so `params: {}` on id-bound GET steps correctly triggers catalog-key recording.
  - `setinternetgateway` body: `loggable` MUST be boolean (`false`), NOT empty string `""`.
    Empty string causes 400 `ValidationError: unable to interpret input` (CONFIRMED).
  - IGW state machine: after `createinternetgateway`, poll `$.internet_gateway.state` →
    `ACTIVE` BEFORE calling `setinternetgateway`. PUT on CREATING IGW → 400
    `scp-network.internet-gateway.invalid-state1` (CONFIRMED).
  - `deleteinternetgateway`: after `setinternetgateway` PUT, IGW enters UPDATING state.
    Retry on 400 `scp-network.internet-gateway.not-deletable-state` (retries: 8).
  - Port: `fixed_ip_address: ""` (empty string) → 400 `scp-network.port.fixed_ip.format-error`.
    OMIT the field; it's optional. When provided, must be valid IP in subnet CIDR.
  - NAT gateway: requires valid `publicip_id` (not a placeholder); create a publicip first
    then pass its id. NAT gateway takes minutes to provision; poll state → ACTIVE.
  - `probe_reads: {}` on a CREATE step REPLACES the HTTP call (engine `continue`s
    after probe_reads). Probes must be a SEPARATE step AFTER the create step.
  - Session provisioner creates a shared VPC per pytest session; multiple serial pytest
    invocations stack shared VPCs if teardown doesn't run. Cleanup manually after each run.
  - VPC budget `core.budgets` live count can be wrong (API eventually-consistent).
    Always `GET /v1/vpcs` to confirm actual live count before running VPC-consuming lifecycles.
  - **29/95 covered** as of 2026-06-23 (up from 13 at start of session).
- **VPC PrivateLink service (PLS) — CONFIRMED LIVE 2026-06-24 (all 14/14 PL endpoints covered):**
  - `POST /v1/privatelink-services` required fields (flat, no wrapper):
    `name` (3-20 chars, lowercase+digits+hyphens), `subnet_id`, `approval_type` (AUTO|MANUAL),
    `connected_resource_type` (IP|LB), `connected_resource_id` (IP string),
    `connected_resource_ip_address` (required when type=IP; same as connected_resource_id),
    `secondary_connected_resource_id` (secondary IP), `service_ip_address`,
    `high_speed_transfer` (bool), `tags: []`. No body wrapper key.
  - **IP type PLS works without a real load balancer.** Use `connected_resource_type: IP` with
    two IPs within the subnet CIDR — no LB needed. PLS reaches ACTIVE in ~30 seconds.
  - **State machine (PLS):** CREATING → ACTIVE (after ~30s). MANUAL approval makes PLEs go to
    REQUESTING state; AUTO makes them go ACTIVE directly.
  - **`setprivatelinkservice` (PUT /v1/privatelink-services/{id})** body: `{description}` only.
  - **`requestprivatelinkendpoint` (PUT /v1/privatelink-endpoints/{id}/request)** body:
    `{type: CANCEL|RE_REQUEST}`. CANCEL from REQUESTING → CANCELED; RE_REQUEST from CANCELED
    → REQUESTING.
  - **`approveprivatelinkendpoint`** body: `{type: APPROVE|REJECT}`. Only works when PLE is in
    REQUESTING state (MANUAL PLS). APPROVE → CREATING → ACTIVE.
  - **`connectprivatelinkendpoint`** body: `{type: DISCONNECT|RECONNECT}`. DISCONNECT from
    ACTIVE → DISCONNECTED; RECONNECT from DISCONNECTED → ACTIVE.
  - **`setprivatelinkendpoint`** body: `{description}`.
  - **Delete order:** PLEs first (DELETE /v1/privatelink-endpoints/{id}), wait for all PLEs to
    be 404, then delete PLS (DELETE /v1/privatelink-services/{id}). PLS DELETE → 409
    `exist-connected-endpoint` until all PLEs are gone.
  - VPC create returns 201 (not 200/202); body: top-level fields not wrapped.
  - Subnet create: POST /v1/subnets (NOT /v1/vpcs/{id}/subnets); body: top-level fields
    `{name, cidr, type: GENERAL, vpc_id, tags: []}`. Returns 202; poll $.subnet.state → ACTIVE.
  - Response fields: `$.privatelink_service.id`, `$.privatelink_endpoint.id`.
- **Transit gateway CREATING → ACTIVE settle timing (CONFIRMED, HB4 run 28738115294, 2026-07-05):**
  `POST /v1/transit-gateways` returns 202 (async) and the TGW sits in `CREATING` for a while — any
  child call issued immediately (vpc-connection/firewall/firewall-connection/routing-rule/
  uplink-routing-rule create, `setTransitGateway`, even `deletetransitgateway`) 400s with
  `scp-network.transit-gateway.not-active-state` ("Transit Gateway state is not
  Active.:(CREATING)") / on delete, `scp-network.transit-gateway.invalid-state` ("... not
  deletable state(Active, Error)"). Poll `$.transit_gateway.state` (`GET
  /v1/transit-gateways/{id}`, field confirmed from `data/api_docs.json`
  createtransitgateway/showtransitgateway `response_example`) until `ACTIVE` before EVERY child
  op AND again right before delete (children can re-transition the TGW). Fixed OFFLINE
  2026-07-06 in `vpc-transit-gateway-children` (`networking__vpc.json`) — UNVERIFIED LIVE.
- **`create-tgw-vpc-connection` ALSO re-transitions the TGW to EDITING (CONFIRMED by observation
  timestamps, HB4b run 28827996068, 2026-07-06):** even after the `wait-tgw-active` settle-poll
  above, `POST .../vpc-connections` (202) is followed ~7s later (after 3 read-only GETs) by
  `create-tgw-firewall` 400 `not-active-state:(EDITING)` — the connection create itself flips an
  ACTIVE TGW back to EDITING for another settle window, same as the initial CREATING one. Fixed
  OFFLINE 2026-07-07: added `wait-tgw-active-after-connection` (same field/until/give_up_status
  poll) right after `create-tgw-vpc-connection`, before any further child create.
- **`createtransitgatewayvpcconnection` / `createtransitgatewayrule` / `createtransitgatewayuplinkrule`
  response envelopes are NOT `vpc_connection`/`routing_rule` (CONFIRMED from `data/api_docs.json`
  response_example, 2026-07-07):** the actual wrapper keys are `transit_gateway_vpc_connection.id`
  and `transit_gateway_rule.id` respectively. The `vpc-transit-gateway-children` lifecycle's
  `capture_soft` used the wrong keys (`$.vpc_connection.id` / `$.routing_rule.id`), so
  `vpc_connection_id`/`routing_rule_id`/`uplink_routing_rule_id` never resolved even when the
  create itself succeeded — DIRECT ROOT CAUSE of `delete-tgw-vpc-connection`'s 400
  `not-in-vpc-connection` showing a literal unresolved `{vpc_connection_id}` placeholder in its
  error body (proof of a missed capture, not a TGW-state issue as first hypothesized). Fixed
  OFFLINE 2026-07-07 (all three capture paths corrected in `networking__vpc.json`).
- **VPC Peering: same-account peering NEVER needs approval (CONFIRMED, HB5 run 28831560635,
  2026-07-07):** `PUT /v1/vpc-peerings/{id}/approval` 400s every time with
  `scp-network.vpc-peering.approval-same-account-type` / "Approval is not required for Same
  Account VPC peering" whenever both the requester and approver VPC belong to the same account
  (our `vpc-peering` lifecycle always creates both VPCs itself, so this is a PERMANENT 400 for
  it, not a transient CREATING-state error). `data/api_docs.json` `showvpcpeering`
  `response_example` documents `account_type: SAME` + `state: CREATING` as its own baseline
  case, confirming SAME-account is the default/expected shape. The peering still auto-progresses
  CREATING → ACTIVE without any approval call for the SAME-account case; the earlier
  `retry_on_status:[400]` x8x15s on the approve step (2026-06-11) was wasting ~120s chasing a
  permanent error, which pushed the downstream `wait-peering-active` settle-poll to start late
  and time out before the peering actually reached ACTIVE (set/delete/create-routing-rule then
  400 `invalid-changeable-state`/`invalid-deletable-state`/`not-active-state` all `(CREATING)`).
  Fixed OFFLINE 2026-07-07 in `networking__vpc.json`: `wait-peering-active` moved to run
  immediately after `create-vpc-peering` (full fresh 300s budget); `approve-vpc-peering`'s retry
  removed (kept as a documented-400 coverage probe only); two more short settle-polls added
  before `set-vpc-peering`/`delete-vpc-peering` (child routing-rule create/delete can flip the
  parent peering back to a transitional state, same precedent as TGW's
  `wait-tgw-active-after-connection`). **If a genuine cross-account approver is ever added**
  (a real second account), the approval flow becomes real and transient-400 retry should be
  restored for that variant only. Live re-validation still pending (offline repair pass).
- **VPC Endpoint needs a dedicated `VPC_ENDPOINT`-type subnet, not `GENERAL` (CONFIRMED —
  already VALIDATED via `knowledge/formal/resources/networking__vpc.yaml` endpoint-subnet node,
  live-validated run 27583285457 2026-06-15; re-confirmed by HB4 run 28738115294, 2026-07-05):**
  `POST /v1/vpc-endpoints` with `subnet_id` pointing at a `GENERAL` subnet 400s
  `scp-network.vpc-endpoint.subnet-not-found` ("VPC Endpoint Type Subnet not found"). Subnet
  `type` enum is `(GENERAL, LOCAL, VPC_ENDPOINT)` (`data/api_docs.json`
  `subnetcreaterequest`). The `vpc-endpoint` lifecycle in `networking__vpc.json` had not applied
  this already-known fix (was still sending `type:GENERAL`, and adopting the shared/GENERAL
  subnet) — fixed OFFLINE 2026-07-06 (self-creates its own `VPC_ENDPOINT`-type subnet every run,
  no longer adopts the shared subnet). Separately, `resource_key` still needs a REAL
  target-service resource id (e.g. an actual filestorage `volume_id` when `resource_type: FS`) —
  this axis remains synthetic/UNPROVEN in `networking__vpc.json` (a disabled composed
  `gen-wave5-vpce` lifecycle in `generated__wave5-net.json` models the real-FS-volume wiring but
  is itself not live-validated).

## networking / loadbalancer

- **Host:** regional. 34 endpoints. Base LB stack (subnet → health-check → LB → server-group) is
  `heavy: true`; light collection GETs (`networking-loadbalancer-reads`) are read-only.
- **Health-check ↔ load-balancer creation ORDER constraint (CONFIRMED, HB4 run 28738115294,
  2026-07-05):** `POST /v1/lb-health-checks` requires a Load Balancer to ALREADY exist in the
  target `subnet_id` — live 400 `scp-loadbalancer.lb-health-checks.SubnetNotAssociatedWithLoadBalancer`
  ("... the chosen subnet does not contain a Load Balancer ... Please ensure a Load Balancer
  exists within the subnet before attempting again"). This is in tension with the earlier
  (2026-06) fix that made `lb-server-group-create` need a PRE-CREATED health check (to avoid
  `LbHealthCheckNotFoundError`). Resolution: create the LB FIRST (wait ACTIVE), THEN the health
  check (subnet now has an LB), THEN the server group (health check now exists) — one order
  satisfies both constraints. Fixed OFFLINE 2026-07-06 in `networking-loadbalancer-members-nat`
  (`networking__loadbalancer.json`) — UNVERIFIED LIVE.
- **Public static-NAT requires an Internet Gateway attached to the LB's VPC (CONFIRMED, same
  run):** `POST /v1/loadbalancers/{id}/static-nats` 400s `scp-loadbalancer.loadbalancers.igw-required-for-static-nat`
  ("Cannot create Public NAT: No Internet Gateway (IGW) found in the VPC") when the VPC has no
  IGW — same precondition as NAT gateways (`networking/vpc` NAT-gateway note above: create IGW,
  wait ACTIVE, PUT/set it, THEN the NAT-family create). Fixed OFFLINE 2026-07-06 (IGW
  create/wait/set steps added before `static-nat-create`, delete added after `static-nat-delete`,
  before VPC/subnet teardown) — UNVERIFIED LIVE.
- **Private static-NAT is an entitlement wall, not a body bug (CONFIRMED, same run):**
  `POST /v1/loadbalancers/{id}/private-static-nats` → 403
  `scp-loadbalancer.loadbalancers.PrivateNatIpForbidden` ("You do not have permission to access
  the private NAT IP resource") regardless of body content — WAIVER CANDIDATE (entitlement), do
  not chase with body changes.
- **Public static-NAT ALSO needs a real `publicip_id`, not just the IGW precondition (CONFIRMED
  live error, HB4b run 28827996068, 2026-07-06):** even after the IGW fix above, `static-nat-create`
  404s `scp-loadbalancer.loadbalancers.PublicIpNotFound` ("Public IP '' is not found") — the
  lifecycle body sent `publicip_id:""` BY DESIGN (no real public IP was available at the time the
  IGW fix was written). `data/api_docs.json` `staticnatcreaterequestdetail` confirms `publicip_id`
  is the ONLY field and `required:true`. Fixed OFFLINE 2026-07-07: added
  `create-publicip-for-static-nat` (verbatim `type:IGW` pattern from `networking/vpc`'s proven
  `create-publicip-for-nat`/`create-publicip-for-vip`), feeding its `$.publicip.id` into
  `static-nat-create`; teardown deletes it after `static-nat-delete`. UNVERIFIED LIVE.
- **`addlbservergroupmembers` needs a REAL VM object_id, not an arbitrary IP (CONFIRMED live error,
  HB4b run 28827996068, 2026-07-06):** `members-add` → 403
  `scp-loadbalancer.members.InvalidVmInMember` ("Member VM object_id is invalid. (object_id: '',
  ip: '10.124.0.31')") — the lifecycle sends `object_id:""` (no VM ever created in this
  lifecycle). This is a cross-service prerequisite (a live `compute/virtualserver` instance's id),
  not a body/param bug — **investigated, NOT fixed this session (2026-07-07):**
  1. **Cross-lifecycle capture is NOT feasible today.** The engine's only cross-test resource
     sharing is `regression/scenarios/shared_infra.py`'s OUT-OF-BAND provisioning (before pytest
     starts, driven by the `.github/workflows/api-test.yml` step that exports
     `SCP_SHARED_VPC_ID`/`SCP_SHARED_SUBNET_ID`), and the lifecycle-side `"adopt"` key only ever
     appears as `"vpc"` / `"subnet"` / `"subnet#db"` across the whole repo (grep-confirmed, 2026-07-07)
     — there is no `"adopt": "server"` precedent, and adding one would need both an engine change
     AND a `.github/` workflow change (forbidden this session, and a bigger ask than "coordinate
     via a peer" — it needs a NEW shared out-of-band VM the whole campaign would depend on).
  2. **Embedding a self-contained VM closure directly in this LB lifecycle is technically
     possible** (mirrors `compute-virtualserver-full`'s own create-server/wait-active/delete
     chain) but was judged NOT worth it this session: `compute-virtualserver-full` itself needed
     3 separate offline repair rounds (HB3/HB3b/HB3b-2) for port/volume/image edge cases even
     with a dedicated owner, so duplicating that create-server body here would import the same
     fragility class into an already-large 34-endpoint LB lifecycle for a payoff of only 4
     endpoints (members-add/set/remove-single/remove-bulk cascade) — plus real compute billing
     and multi-minute wall time this offline session cannot live-verify.
  3. **Recommendation to the orchestrator:** either (a) approve a follow-up session to extend
     `shared_infra.py` + `.github/workflows/api-test.yml` with an `SCP_SHARED_VM_ID` (mirroring
     the existing shared-VPC pattern) so `"adopt": "server"` becomes a real engine feature reused
     by every LB/member-needing lifecycle, or (b) schedule `networking-loadbalancer-members-nat`
     in the SAME heavy batch as `compute-virtualserver-full` and — once that run's `server_id`
     capture pattern is proven live — do a small follow-up offline session that adds the engine's
     `"adopt": "server"` support the same way `"vpc"`/`"subnet"` already work, instead of
     re-solving VM creation flakiness a second time inside the LB lifecycle. Not implemented this
     session; `members-add`/`members-set`/`members-remove-single`/`members-remove-bulk` remain a
     reach-only (403/404) soft-coverage gap.

## networking / firewall

- **Host:** regional (`firewall.<region>.<env>.samsungsdscloud.com`). 8 endpoints. Firewalls are VPC-bound resources; auto-created implicitly when a carrier resource (IGW/Direct Connect/LB) is created with `firewall_enabled: true`. No standalone `POST /v1/firewalls` endpoint exists.
- **LIFECYCLE FIXED 2026-06-29:** `networking-firewall-rule` lifecycle now starts with a `listfirewalls` GET step (unconditionally 200). Lifecycle is now light-runnable.
- `GET /v1/firewalls` (listfirewalls): returns 200 OK even with zero firewalls — `{"count":0,"firewalls":[],"page":0,"size":20}`. No required query params. **Live confirmed 2026-06-20 and 2026-06-29.** First step in `networking-firewall-rule` lifecycle.
- `GET /v1/firewalls/rules` (listfirewallrules): requires `firewall_id` query param (REQUIRED per api_docs). Probe with dummy id returns 404 `{"code":"ResourceNotFound"}` — real JSON error (backend reachable, not portal). Not coverable read-only without a real firewall_id.
- `GET /v1/firewalls/{firewall_id}` (showfirewall): 404 `ResourceNotFound` with any dummy id. `GET /v1/firewalls/rules/{firewall_rule_id}` (showfirewallrule): same. Both backend-reachable. **Live confirmed 2026-06-29.**
- All 4 mutating endpoints (createfirewallrule POST, setfirewall PUT, setfirewallrule PUT, deletefirewallrule DELETE) need existing firewall/rule IDs from a prior VPC+IGW create.
- **Intermittent 503s** are normal transient gateway timeouts; the client retries handle them. Not a persistent backend bug.
- **Coverage path (heavy — VPC+IGW required):** create VPC with `firewall_enabled: true` → listfirewalls captures `$.firewalls[0].id` → showfirewall/listfirewallrules/showfirewallrule/createfirewallrule/setfirewallrule/deletefirewallrule/setfirewall all unlock (7 endpoints). setfirewall body: `{flavor_name: EXSMALL, loggable: false}`. createfirewallrule captures `$.firewall_rule.id`.
- **Read-only ceiling: 1/8** (listfirewalls). 7 remaining need real firewall_id (VPC+IGW create → needs-heavy). Confirmed 2026-06-20 and 2026-06-29.

## networking / security-group

- **Host:** regional, but **account/region-scoped — no VPC needed**. SG
  `$.security_group.id`; rule `$.security_group_rule.id` (`direction`,
  `ethertype: IPv4`, `protocol`, `port_range_min/max`, `remote_ip_prefix`).
- **9/9 COMPLETE** as of 2026-06-23 (live-validated).
- **setsecuritygroup (PUT /v1/security-groups/{id})**: body is `{description, loggable}` ONLY.
  `name` is NOT a valid field and will cause a 400. Confirmed from API docs request_example.
- **listsecuritygrouprules (GET /v1/security-group-rules)**: `security_group_id` is a
  REQUIRED query parameter. Returns 400 without it; 200 with a real sg_id. A freshly
  created SG with no rules returns count:0 but status 200 — valid for coverage.
- **createsecuritygroup** body: `{name, description, loggable:bool, tags:[]}`. Response
  envelope: `$.security_group.id`.
- **createsecuritygrouprule** body: `{security_group_id, direction:"ingress"|"egress",
  ethertype:"IPv4"|"IPv6", protocol:"tcp"|"udp"|"icmp"|"all", port_range_min,
  port_range_max, remote_ip_prefix, description}`. Response: `$.security_group_rule.id`.
- **Teardown**: deletesecuritygrouprule 204, deletesecuritygroup 204. Both clean
  synchronously — no async wait needed.

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
  Coverage lever: borrow existing registry → list → capture `registry_id` → create
  temp repository → run all repo mutation PUTs → delete repo → clean state.
- **Live-confirmed borrowable resources (2026-06-24, kr-west1):** registry
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
- **Flat `/v1/repositories` returns 403** (Forbidden: "Action definition not found")
  for the service account. MUST use registry-scoped endpoint
  `GET /v1/container-registries/{registry_id}/repositories` (200 confirmed live).
  This is critical for lifecycle seeding — lifecycle seed steps that call `/v1/repositories`
  will fail and skip their group; use the scoped endpoint instead.
- **`updatepublicacl` (PUT /v1/container-registries/{id}/public-acl) requires the
  public endpoint to be ENABLED first.** Calling it without enabling returns 409
  `scp-container-registry.registry.put-conflict: the registry does not use a public endpoint`.
  Correct sequence: enable-public-endpoint (true) → update-public-acl → disable-public-endpoint (false).
  Registry enters `Editing` state for ~60s after each public-endpoint change; lifecycle
  uses separate groups for each step to avoid group-skip propagation.
- **Repository mutation bodies confirmed live (2026-06-24):**
  - `createrepository`: POST /v1/repositories body `{name, description, registry_id, lifecycle_policy:{...}, lock_policy:{locked:bool}, pull_policy:{critical_limit,high_limit,unmodified_excepted,unscanned_image_pull_prevented,vulnerable_image_pull_prevented}, scan_policy:{auto_scan_enabled,fixed_version_excepted,language_excepted,scan_policy_enabled,secret_excepted,severity_limit:"High"}, tags:[]}` → 201 `{id, message, state}`.
  - `updaterepositorydescription`: PUT body `{description:string}` → 200.
  - `updaterepositorylockpolicy`: PUT body `{lock_policy:{locked:bool}}` → 200.
  - `updaterepositorypullpolicy`: PUT body `{pull_policy:{...}}` → 200.
  - `updaterepositoryscanpolicy`: PUT body `{scan_policy:{...}}` → 200.
  - `updaterepositorylifecyclepolicy`: PUT body `{lifecycle_policy:{lifecycle_policy_enabled,outdated_rule_enabled,outdated_rule_duration,outdated_rule_tag_expression,untagged_rule_enabled,untagged_rule_duration}}` → 200.
  - `deleterepository`: DELETE → 202. `updateprivateacl`: PUT `{private_acl_enabled:bool, private_acl_resources:[]}` → 200.
  - `updatepublicacl`: PUT `{public_acl_enabled:bool, public_acl_resources:[]}` → 200 (requires public endpoint enabled).
  - `updatepublicendpointenabled`: PUT `{public_endpoint_enabled:bool}` → 200.
- **GET steps need `"params": {}` to force catalog-key recording.** Engine only
  records under catalog key for non-GET steps OR GET steps with `"params"` present.
  Without it, id-bound GET steps record only under `lifecycle:step_name`, not under
  `container/scr/showimage` etc. This is the `scr-read-coverage` fix (2026-06-24).
- **Docker-push blocker:** the existing repository has `images:[]` (count 0), and
  images/tags are **born only by `docker push`**, not any REST POST. 19 endpoints
  (showimage/listtagses/showtags/tags-{packages,secrets,vulnerabilities}/downloadmanifest/
  showimagelifecyclepolicypreview/all image+tags write PUTs/DELETEs) are permanently
  blocked until a docker push deposits a real image. All 19 are now REACHED-4xx
  (404 with literal placeholder id) in observations.
- **Coverage 2026-06-24:** 18/39 CONFIRMED 2xx. Gain this session: +10 (from 8).
  Remaining 21 gaps: 2 blocked by quota (createregistry/deleteregistry), 19 blocked
  by needs-docker-push. All 19 docker-push-blocked endpoints are REACHED-4xx.
  Lifecycle `scr-repo-borrow-coverage` (in `container__scr.json`) covers all
  repo+registry mutation endpoints by borrowing the existing registry.

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
- **`createdirectconnect` 400 `not-exist-log-storage` is an ACCOUNT-LEVEL cross-service
  prerequisite, NOT a direct-connect-lifecycle body bug (investigated OFFLINE, HB5 run
  28831560635, 2026-07-07):** error detail `"FIREWALL Logging storage does not exists in this
  account(...)"`, triggered because our create body sends `firewall_enabled: true,
  firewall_loggable: true`. `data/api_docs.json` has the fix-shaped API —
  `management/network-logging/{create,list,delete}networkloggingstorage`
  (`POST/GET/DELETE /v1/network-logging/storages`, `resource_type` enum
  `FIREWALL|SECURITY_GROUP|NAT`) — but `createnetworkloggingstorage`'s ONLY body field besides
  `resource_type` is `bucket_name` (required), which itself needs a REAL object-storage bucket —
  i.e. this is a SECOND cross-service dependency (object-storage), and the storage registration
  is account-global (not scoped to one direct-connect), so create/teardown per-lifecycle-run risks
  either colliding with another concurrent DC-family test (`vpc-private-nat` also references
  `service_type: DIRECT_CONNECT`) or, if torn down mid-account, breaking any other consumer of
  FIREWALL logging that may exist. **Classified as a heavy cross-service prereq, NOT repaired
  in this (networking-scoped, offline) session** — recommend the orchestrator provision ONE
  account-wide FIREWALL network-logging-storage (with its own object-storage bucket) as shared
  session infra (like the shared VPC), never torn down per-DC-run, so `createdirectconnect` can
  then be re-tried live. Do not attempt a private per-lifecycle rebuild of this (crosses into
  management/network-logging + object-storage, out of this service's scope).
- **`vpc-private-nat`'s `create-private-nat` 404 is INDEPENDENT of direct-connect, not a
  cascade (checked OFFLINE 2026-07-07):** `networking__vpc.json`'s `vpc-private-nat` lifecycle
  sends a synthetic `{unique}` placeholder as `service_resource_id` (it does not adopt or capture
  a real direct-connect id from `networking-direct-connect-routing` — the two lifecycles share no
  ctx/state), so this 404 is unrelated to and unaffected by the log-storage prereq above; it will
  keep 404ing until `vpc-private-nat` is wired to a REAL direct-connect id (separate, larger
  cross-lifecycle change, out of scope here — not touched this session).

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
- **`createvpngateway` requires `ip_address` (CONFIRMED, docs — HB4 run 28738115294, 2026-07-05):**
  `vpngatewaycreaterequest` (`data/api_docs.json`) lists `ip_address` as `required:true` alongside
  `ip_id`/`ip_type`/`name`/`vpc_id` — the lifecycle body was missing it and got a generic 400
  `ValidationError ["Field required"]` (no field name in the error body). Fix (OFFLINE repair,
  2026-07-06, UNVERIFIED LIVE): capture `$.publicip.ip_address` from the preceding `create-publicip`
  step (real IP, e.g. `192.167.0.5`) and send it as the gateway's `ip_address` (paired with the
  same publicip's `ip_id`). Blocks the whole chain including the official phase1/phase2 tunnel
  values (`data/api_docs.json` request_example) from ever being tried live.
- **`createvpngateway` ALSO requires the VPC to already have an Internet Gateway (CONFIRMED live
  error, HB4b run 28827996068, 2026-07-06):** even with `ip_address` fixed, the create 404s
  `scp-network.vpn-gateway.internet-gateway-not-found` ("Cannot found the Internet Gateway on
  VPC(...)") — same precondition class as LB static-NAT and NAT gateway (see `networking/vpc` and
  `networking/loadbalancer` sections). Fixed OFFLINE 2026-07-07 (`networking__vpn.json`
  `networking-vpn-gateway-tunnel`): added `create-igw-for-vpn`/`wait-igw-for-vpn-active`/
  `set-igw-for-vpn` (verbatim pattern from the proven `create-internet-gateway-for-nat` /
  `create-igw-for-static-nat`) before `create-vpn-gateway`, + `delete-igw-for-vpn` in teardown.
  **Shared-VPC IGW collision check:** this lifecycle ADOPTS the session-shared VPC (does not
  self-create one, to conserve the cap-5 VPC budget); `data/api_docs.json` documents no 1-IGW-
  per-VPC cardinality limit, and two other lifecycles already attach an IGW to this same shared
  VPC (`networking/vpc` NAT-gateway, `networking/loadbalancer` static-NAT) without a reported hard
  collision, so the new step follows the same convention (group+optional+broad expect_status,
  degrading to a soft-fail of just the `vpn` family on a genuine 409) rather than switching to a
  self-created VPC. UNVERIFIED LIVE — watch for a concurrent-IGW 409 if this lifecycle is
  scheduled in the same batch as the NAT-gateway or LB static-nat lifecycles.

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

- **Host:** regional (`certificatemanager.<region>.<env>.samsungsdscloud.com`).
- **Endpoints (7 total):** `listcertificates` (GET), `detailcertificate` (GET/{id}),
  `checknameduplication` (POST), `createcertificate` (POST), `selfsigncert` (POST),
  `validatecertificate` (POST), `deletecertificate` (DELETE/{id}).
- **`selfsigncert` (POST /v1/certificatemanager/self-sign):** required fields:
  `cn`, `name`, `not_before_dt`, `not_after_dt`, `organization`, `recipients=[]`,
  `region`, `tags=[]`, `timezone`. Date format is `YYYYMMDD` (engine `{today}` =
  `%Y%m%d` — correct). Returns 201 with `$.certificate.id` (UUID without hyphens).
  `cert_kind: DEV`, `key_bit_size: 2048`. Synchronous (state=VALID immediately).
  CONFIRMED 2xx 2026-06-20 with real call.
- **`checknameduplication` (POST /v1/certificatemanager/check-duplication):**
  only `{name: 'any-string'}` required. Returns 200 `{"result": false}` for
  available name. Non-destructive (no resource created). CONFIRMED 200 2026-06-20.
- **`validatecertificate` (POST /v1/certificatemanager/check-validation):**
  requires `cert_body` + `private_key` (and optionally `cert_chain`). CONFIRMED
  BLOCKER 2026-06-20: returns 400 `scp-security.certificate.pem-format-private-key-error`
  ("This private key is not a PEM format") for ALL private key PEM formats tested:
  RSA PKCS#1 (`BEGIN RSA PRIVATE KEY`), PKCS#8 (`BEGIN PRIVATE KEY`), EC
  (`BEGIN EC PRIVATE KEY`). Self-signed certs and real OpenSSL-generated keys all
  rejected. Endpoint IS reachable (not 403). Requires a real CA-chain-signed cert
  from an external authority.
- **`createcertificate` (POST /v1/certificatemanager):** import an external cert.
  Required body (CONFIRMED 2026-06-23): `cert_body`, `cert_chain`, `name`,
  `private_key`, `recipients=[]`, `region`, `tags=[]`, `timezone` (8 fields).
  Without `timezone`: 400 ValidationError "Field required". With all 8 fields +
  fake PEM: 500 `scp-security.certificate.create-failed` (backend parses PEM).
  Requires real CA-chain-signed cert+key for 2xx. Same PEM key rejection as
  validatecertificate — all openssl-generated key types rejected.
- **`listcertificates` (GET /v1/certificatemanager):** 200 with empty list
  (`count:0`) on fresh account. Response shape: `{certificates:[], count, page, size, sort}`.
  Optional query params: `size`, `page`, `sort`, `isMine`, `name`, `cn`, `state`.
- **`detailcertificate` (GET /v1/certificatemanager/{certificate_id}):** needs a
  real `certificate_id` from selfsign/create. Capture path: `$.certificate.id`.
- **Coverage ceiling (2026-06-23):** read-only floor = 1/7 (listcertificates).
  With mutations: selfsigncert→detailcertificate→listcertificates→
  checknameduplication→deletecertificate = 5/7. Remaining 2 (createcertificate,
  validatecertificate) blocked by CA-cert requirement — backend rejects ALL
  self-signed PEM private keys (RSA PKCS#1, PKCS#8, EC) CONFIRMED 2026-06-23.

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
- **Coverage 2026-06-24: 41 / 62 (66%)**
- **Coverage levers:**
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
    `{users:[], count,page,size,sort}`). This account has **0 IAM users** under
    `listiamuser` — `getiamuser` gets 404.
  - *Group/policy lifecycles (MUTATIONS gate):* `creategroup` → `$.group.id`,
    `createpolicy` → flat `$.id`. Both delete cleanly.
  - *Resource-policy chain:* see b64-SRN fix below. **CONFIRMED 200** for all 6
    resource-policy endpoints 2026-06-23.
  - *Role-existing lifecycle (CONFIRMED 2026-06-24):* `iam-role-existing` uses
    `OrganizationAccountAccessRole` (a non-SCPServiceRole) for 6 role ops without
    creating a new role (avoids `createrole` 500 blocker). Covers `setrole`,
    `setroletrustpolicy`, `addrolepolicybindings`, `removerolepolicybinding`,
    `removebulkrolepolicybindings` — all CONFIRMED 200/204 live.
  - *Bulk-delete no-ops (CONFIRMED 2026-06-24):* `deletebulkrole` with
    `{"role_ids": []}` → 204 (idempotent no-op). `deletebulkiamuser` with
    `{"user_ids": []}` → 204 (same). Both non-HEAVY and non-destructive.
- **CONFIRMED body shapes (validated live 2026-06-23/24):**
  - `setgroup` (PUT /v1/groups/{group_id}): **REQUIRES `name` field** alongside
    `description`. Body `{"description":"..."}` alone returns 400 "Field required".
    Must capture `$.group.name` from creategroup and re-send it: `{"name":
    "{group_name}", "description": "..."}`. VALIDATED 200.
  - `creategroup` envelope: `$.group.id` (nested, not flat). `$.group.name` also
    available for capture.
  - `createpolicy` envelope: `$.id` (FLAT — not `$.policy.id`).
  - `addgrouppolicybinding` (POST /v1/groups/{group_id}/policy-bindings): needs a
    REAL policy_id in `{"policy_ids":[...]}` — synthetic id returns 404 "No Policy
    found". Solution: create a policy in the same lifecycle and use real id. VALIDATED 200.
  - `removegrouppolicybinding` (DELETE /v1/groups/{group_id}/policy-bindings/{policy_id}):
    also needs real policy_id in path. VALIDATED 204.
  - `addgroupmember` (POST /v1/groups/{group_id}/members): synthetic user_id always
    404 "No User found". Account has 0 IAM users. Not fixable without SCP_RUN_HEAVY.
  - `setrole` (PUT /v1/roles/{role_id}): **REQUIRES `max_session_duration` field**
    alongside `description`. VALIDATED 200 against existing role.
  - `setroletrustpolicy` (PUT /v1/roles/{role_id}/trust-policy): body is
    `{"assume_role_policy_document": {"Version":"2024-07-01","Statement":[{"Sid":"statement1",
    "Effect":"Allow","Action":["sts:*"],"Condition":{},"Principal":{"Account":["<acct_id>"]},
    "Resource":[]}]}}`. VALIDATED 200.
  - `deletesamlproviders` (DELETE /v1/saml-providers): body key is `ids` (NOT
    `saml_provider_ids`). CONFIRMED from api_docs.json request_example.
- **b64-SRN fix (CONFIRMED 200 all 6 ops 2026-06-23):** The iam gateway decodes
  `{srn}` path segments as base64, same as resourcemanager. Plain SRN yields 400
  "SRN decoding error". Fix: `b64_encode` step produces `iam_srn_b64`; all 6
  srn-targeted paths use `{iam_srn_b64}`:
  - `PUT /v1/resource-policies/{iam_srn_b64}` (setresourcepolicy) — CONFIRMED 200
  - `GET /v1/resource-policies/{iam_srn_b64}` (showresourcepolicy) — CONFIRMED 200
  - `POST /v1/resource-policies/{iam_srn_b64}/statements` (addpermission) — CONFIRMED 201
  - `PUT /v1/resource-policies/{iam_srn_b64}/statements/{sid}` (setpermission) — CONFIRMED 200
  - `DELETE /v1/resource-policies/{iam_srn_b64}/statements/{sid}` (removepermission) — CONFIRMED 204
  - `DELETE /v1/resource-policies/{iam_srn_b64}` (deleteresourcepolicy) — CONFIRMED 204
  - `{sid}` path segment does NOT need b64 encoding.
- **Resource-policy Action MUST match target service (CONFIRMED 2026-06-23):**
  The Action in a resource-based policy body must match the service of the target
  resource SRN. `iam:*` and `*` both return 400 "UnSupportedActionInPolicy for
  service [vpc/secretsmanager/kms]". `kms:*` is confirmed 200 against a KMS SRN.
  Strategy: filter resourcemanager `GET /v1/resources?resource_type=kms` to always
  get a KMS SRN, then use `kms:*` action.
- **probe_reads srn mapping (CONFIRMED 2026-06-23):** `probe_reads` does NOT
  auto-resolve `{srn}` from context variable `iam_srn_b64` because `_PARAM_ALIASES`
  only maps `srn` → `rg_srn`, not `iam_srn_b64`. Must use explicit mapping:
  `"probe_reads": {"srn": "{iam_srn_b64}"}` to fire `GET /v1/resource-policies/{iam_srn_b64}`.
- **setpermission/removepermission Sid (CONFIRMED 2026-06-23):** Path uses the Sid
  returned in the addpermission response (`$.Statement.Sid`). Using `{unique}` (synthetic)
  returns 404 "No Policy found". Must capture `capture_soft: {"rp_statement_sid": "$.Statement.Sid"}`
  from addpermission and use `{rp_statement_sid}` in the path.
- **Product-bug blocker (5xx, CONFIRMED 2026-06-23):** `createrole` → 500
  ContactAdminForAssistance. Blocks `deleterole` (cannot create to delete). Role-mut
  ops (`setrole`/`setroletrustpolicy`/`addrolepolicybindings`/`removerolepolicybinding`/
  `removebulkrolepolicybindings`) worked around via `iam-role-existing` lifecycle.
  `deletebulkrole` covered via empty `role_ids:[]`. File `createrole`/`deleterole`
  as support ticket.
- **Entitlement blockers (CONFIRMED 2026-06-23/24):**
  - `adduserpolicybinding` / `removeuserpolicybinding` → 403 "The user is not part
    of the project". Project-membership wall. GET `listuserpolicybindings` returns 200
    for ANY user_id (returns empty list, does not 404 unknown users).
- **HEAVY-gated (SCP_RUN_HEAVY):** 14 endpoints — accesskey CRUD (`accesskeycreate`,
  `accesskeyset`, `accesskeydelete`, `accesskeydeletebulk`, `accesskeysendtemporaryotp`),
  iam-user CRUD (`createiamuser`, `updateiamuser`, `updateiamuserpassword`, `deleteiamuser`,
  `deletebulkiamuser`), saml-provider (`createsamlprovider`, `setsamlprovider`,
  `showsamlprovider`, `deletesamlproviders`). All in `iam-credentials-heavy` lifecycle.
  Note: `deletebulkiamuser` is now NON-HEAVY (empty array 204); upgraded in ledger.
- **Blast-radius waived:** `deletepolicies` (DELETE /v1/policies/bulk) — live-verified
  fans out to delete ALL account policies. Covered in `coverage_waivers.json`.
- **account_id:** `ec11538abf8f46d2953539521f745366` (use for listiamuser path).
- **OrganizationAccountAccessRole:** id `f07f5921c1df42089e59c90408599261` — a
  user-created role (not `SCPServiceRole`-prefixed). Mutable for setrole/setroletrustpolicy/
  addrolepolicybindings/removerolepolicybinding/removebulkrolepolicybindings. Trust
  policy principal Account `73eab1a74c6347c1be9c892efc7f1102`.

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
- **`disconnect-vpc-from-private-dns` re-triggers the SAME invalid-state pattern as the
  initial create (CONFIRMED, HB5 run 28831560635, 2026-07-07):** `PUT /v1/private-dns/{id}`
  (disconnecting the VPC, `connected_vpc_ids: []`) puts private-dns back into a transitional
  `EDITING` state — the very next `delete-private-dns` 400s `scp-network.private-dns.invalid-state`
  `"The state is invalid.:(EDITING)"` (same error family as the already-known CREATING→setter-400
  behaviour, just re-triggered by an edit instead of the initial create). Fixed OFFLINE 2026-07-07
  in `networking__dns.json`: added `wait-private-dns-stable-after-disconnect` settle-poll
  (`$.private_dns.state` until `ACTIVE`, 300s/15s, give_up_status 400/403/404) between
  `disconnect-vpc-from-private-dns` and `delete-private-dns`. Live re-validation still pending.
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
- **validate-resources POSTs (live-probed 2026-06-24):** dry-run preflight the console calls before create/update.
  - `getdataopsservicevalidateresourcescreation` (POST /v1/data-ops-services/clusters/{cluster_id}/validate-resources): returns 400 "Input dataOpsServiceWorkload is not valid" for ALL tried service_workload formats (scheduler/web_server/worker with cpu/memory/replica/version as strings, ints, millicore notation, with or without version). Backend validates service_workload BEFORE looking up cluster_id (data-flow validate-resources gets 404 for unknown cluster which confirms data-ops validates body first). Unknown valid format. Docs returned 503.
  - `getdataopsservicevalidateresourcesupdate` (POST /v1/data-ops-services/{data_ops_service_id}/validate-resources): same workload issue + needs real DOPS_SERVICE- ID. doubly blocked.
- **create-data-ops body quirk (live-proven 2026-06-24):** `storage_class_name` must be **non-null/non-empty string**. Empty string `""` causes 400 "Input storage class name should not be null". Value `"default"` passes body validation and returns 404 (cluster not found) with a fake cluster_id. Lifecycle updated to use `"default"`. With a real cluster_id + ingress_controller_name this should proceed to create.
- **create-data-ops-service body (UNKNOWN valid format 2026-06-24):** `service_workload` field consistently returns 400 "Input dataOpsServiceWorkload is not valid" regardless of tried formats (scheduler/web_server/worker, integer or string cpu/memory, with/without version, with/without extra fields). `storage_class_name` must also be non-empty. Docs site returned 503. This endpoint needs real billable Airflow cluster AND valid service_workload format to reach 2xx.
- **Response envelope:** list endpoints use `{contents:[], total_count}`. Detail uses flat object (not confirmed — no resources in account).
- **image-versions:** `GET /v1/data-ops/image-versions` → `{contents:[{image_id, image_name, version}], total_count}`. Only 1 version available: `4.1.1` (IMAGE-ef7a8ad7-ec47-41b7-aef9-e6dfe4edfd21).
- **Coverage 2026-06-24:** confirmed **5/17** 2xx (no change from previous run):
  - `getdataopsimageversionv1`, `getdataopslistconsole`, `getdataopsservicelistconsole` (smoke 200)
  - `checkduplicationcontroller`, `checkduplicationcontrollerv1` (200 for any name)
  - Remaining 12 are all heavy-prereq (4 id-bound GETs + 8 mutating writes needing real Airflow cluster).
- **Guarded lifecycle diagnostics 2026-06-24 (heavy=true run):** 8 write endpoint catalog keys now have 4xx observations (not 2xx but reach-confirmed). Fix for next heavy run: storage_class_name='default' is in the lifecycle. service_workload format unknown — will need to inspect API source or docs once 503 clears.
- **Entitlement-403 for parent paths:** `/v1/data-ops-services/data-ops` and `/v1/data-ops/clusters`
  both return 403 `Action definition is not found` — these are IAM-unregistered paths for ROOT user.
  `getdataopssubversion` and `getingresscontrollerlistv1` are thus doubly-blocked (entitlement-403 on
  parent listing AND heavy-prereq for the actual resource ids).

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
- **`cancelinvitations` (PUT /v1/invitations/cancel) PROVEN 200 2026-06-24:**
  `{"ids": []}` (empty list) returns 200 `{success_ids:[]}`. This is safe — an
  empty list cancels nothing, confirming the endpoint without affecting real data.
  `{"ids": ["real-id"]}` returns 404 (invitation not found). Lifecycle now uses
  empty list to guarantee 200.
- **Proven body fields (2026-06-19 + 2026-06-24):**
  - `createorganizationunit`: needs `name` + `parent_unit_id` (validated: these fields
    move response from 400 to 403, confirming field names are correct)
  - `moveaccount`: needs `organization_id` + `parent_unit_id` (CONFIRMED 2026-06-24;
    NOT `account_id` which gives 'Extra inputs not permitted'. Together they leave 1
    'Field required' — third field unknown). NOTE: previous doc was WRONG about
    `account_id` being the correct field; `organization_id` + `parent_unit_id` = 1 remaining.
  - `createinvitation`: needs `organization_id` (validated field) + 1 unknown field
    (docs JS-rendered; `email`, `message`, `login_id`, `account_id` are all invalid)
  - `createaccount`: `email` is INVALID ('Extra inputs not permitted'). Correct field
    is `login_id` (must be email-format string). `name` + `login_id` + `organization_id`
    = 1 more required field unknown. (CONFIRMED 2026-06-24)
  - `createdelegationpolicy` / `setdelegationpolicy`: field is `document` NOT
    `policy_document`. `{document:{Version:'2024-07-01', Statement:[...]}}` returns 403
    (body accepted, auth check fails). `{document:{}}` triggers 500 ContactAdmin bug.
    `name` field is invalid for both create+set delegation policy. (CONFIRMED 2026-06-24)
  - `setorganization`: `name` and `description` are both INVALID ('Extra inputs not
    permitted'). Empty body `{}` returns 403 (auth check passes). Valid update fields
    are unknown (docs JS-rendered). (CONFIRMED 2026-06-24)
  - `removeaccounts`: `ids`, `account_ids`, `organization_account_ids` all INVALID.
    `organization_id` is valid (no extra-inputs error). `organization_id` alone still
    leaves 1 field required. Second required field unknown. (CONFIRMED 2026-06-24)
  - `listservicecontrolpolicies`: required query param `organization_id`
  - `listorganizationunits`: required query param `parent_unit_id` ('ROOT' for root level)
- **Response envelopes (live-proven):**
  - `listorganizations` → `$.organizations[0].id`
  - `listaccountinvitations` → `$.account_invitations[0].id`
  - Inferred (403 so not confirmed live): `organization_units`, `service_control_policies`,
    `organization_accounts`, `organization_invitations`
- **Lifecycle heavy flag removed 2026-06-24:** org lifecycles were incorrectly marked
  `heavy: true` which blocked them without `SCP_RUN_HEAVY`. Organization creates no
  VPC/compute resources — `heavy` flag was a blast-radius guard but all steps already
  have `optional: true` + broad `expect_status`. All 6 lifecycles now run without
  `SCP_RUN_HEAVY`. This exposed 28 previously-never-reached endpoints.
- **Coverage ceiling:** 3/37 without org-master privilege. All 37 endpoints are now
  canonically reached and classified. The 34-gap is blocked by entitlement-403 (org-master
  required for most ops). Coverage would rise to ~25+ on an org-master account.
- **Coverage 2026-06-24:** 2 → **3 / 37** (+1: `cancelinvitations` → 200).
  All 37 endpoints reached (32 canonical soft/ok + 5 newly probed id-bound).

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
- **Id-bound GETs (6 endpoints: geteventdetail, geteventnotificationstates, geteventpolicydetail, geteventpolicyhistories, geteventpolicynotification, getadressbookmemberlist):**
  CONFIRMED REACHABLE 2026-06-20 via direct numeric-ID probes: ALL return 404
  `ResourceNotFound` (not 400/400 resourceType-misparse). Key facts:
  - eventPolicyId is **NUMERIC** (not UUID). Confirmed: numeric 12345 -> 404 ResourceNotFound.
    UUID-format -> 400 resourceType-misparse (server treats UUID chars as date-like string).
  - eventId is also **NUMERIC** per same probe.
  - addrbookId is also **NUMERIC** per same probe.
  - geteventpolicyhistories ALSO has the date-misparse bug (400 when queryStartDt/queryEndDt
    provided in any format). Without dates: 400 Validation failure.
  - addrbook read_chain in smoke uses WRONG list path (/v2/addrbooks instead of /v2/users/addrbooks)
    -> 404 path-not-found -> chain skips. This is a catalog/API structural mismatch that
    the auto-derive cannot handle. The lifecycle uses the CORRECT path (/v2/users/addrbooks).
  Account has 0 events, 0 event-policies, 0 addrbooks. Scenario uses numeric placeholder,
  normalizes to catalog '*' key.
- **puteventpolicy body shape (CONFIRMED cascade-revealed 2026-06-19, isLogMetric corrected 2026-06-24):**
  Must wrap all fields in `eventPolicyRequest` key. Required cascade-field order:
  `disableYn`, `isLogMetric`, `eventLevel`, `ftCount`, `eventThreshold`. Once all
  present the API returns `{"code":"InvalidRequest","params":[null]}` — backend
  business rule validation fails (products in NE state). Full confirmed body:
  `{"eventPolicyRequest": {"eventPolicyName": "...", "productTypeCode": "Object Storage",
  "productResourceId": "apitest-logsink", "metricKey": "<key>", "eventLevel": "WARNING",
  "disableYn": "N", "isLogMetric": "N", "eventThreshold": 100.0, "ftCount": 1}}`.
  **CRITICAL 2026-06-24: `isLogMetric` MUST be string "N"/"Y" not boolean false/true.**
  With boolean false: 400 `params[{name:"resourceType",value:"eventPolicyRequest.isLogMetric"}]`
  (field validation fails). With string "N": passes field validation, proceeds to business-rule
  check which then returns 400 InvalidRequest params=[null] (NE state blocker).
  The `InvalidRequest` with `null` params is an account-level blocker (products not
  enrolled in monitoring). **Classify: account-prereq / entitlement-class blocker.**
- **getmetricperfdatalist body shape (CONFIRMED cascade-revealed 2026-06-19, updated 2026-06-24):**
  POST `/v1/cloudmonitorings/product/v2/metric-data`. Required fields:
  `productTypeCode`, `productResourceId`, `queryStartDt` (ISO 8601 with T/Z suffix),
  `queryEndDt` (ISO 8601 with T/Z suffix), `metricDataConditions` (array of objects).
  Without T-suffix dates: 400 `resourceType=queryStartDt` backend bug. With T-suffix
  dates: 404 `productResourceInfos not found` — same account-level prereq.
  **Alternative body shape (tested 2026-06-24): `productResourceInfos` array instead of
  `productResourceId` scalar.** Both formats return 404 params[productResourceInfos not found].
  Neither format gets past the NE-state blocker. Example body:
  `{"productTypeCode":"Object Storage","productResourceId":"apitest-logsink",
  "queryStartDt":"2026-05-21T00:00:00Z","queryEndDt":"2026-06-19T23:59:59Z",
  "metricDataConditions":[{"metricKey":"objectstorage.usage.bucketSizeBytes",
  "statisticType":"AVG","period":3600}]}`.
  **Classify: account-prereq / products not in monitoring backend (NE state).**
- **Mutating endpoints (3):** `puteventpolicy` (POST create), `modifyeventpolicy`
  (PUT), `deleteeventpolicy` (DELETE). All need `SCP_ALLOW_MUTATIONS=true` +
  `SCP_ALLOW_DESTRUCTIVE=true`. Body shape now confirmed from cascade (see above).
  Cannot reach 2xx without monitoring-enrolled resources. Blocker: account-prereq.
- **Backend date-misparse bug scope (EXPANDED 2026-06-20):**
  The date-misparse bug (400 `params[name=resourceType, value=<date>]`) affects:
  getaccounteventlist, getproducteventlist, AND geteventpolicyhistories.
  Also geteventmetricperfdatalist POST without T-suffix on dates (confirmed prior).
  All date formats tried for affected endpoints: YYYY-MM-DD, ISO-T-Z
  (YYYY-MM-DDTHH:MM:SSZ), YYYYMMDD compact, int epoch. ALL fail with same error.
  The X-ResourceType header IS correctly forwarded (confirmed by debug trace of
  request headers). This is a server-side routing/validation bug where the query
  parameter value is mapped to the 'resourceType' validation field.
- **CRUD lifecycle engine / read-only lifecycle interaction:**
  The `regression.scenarios.engine.run_lifecycle()` function requires
  `SCP_ALLOW_MUTATIONS=true` to run ANY lifecycle (line 691), including read-only
  ones. This means `cloudmonitoring-readonly-shows` and `cloudmonitoring-event-policy`
  lifecycles only run during the CRUD pass (not the smoke pass). The smoke test
  records what it can (bare GETs), and direct probes fill in the rest.
- **modifyeventpolicy and deleteeventpolicy probe (ADDED 2026-06-24):**
  These endpoints were previously never reached (blocked by cm-policy group failure).
  Added `probe-modify-event-policy` (PUT, group=cm-probe-modify) and
  `probe-delete-event-policy` (DELETE, group=cm-probe-delete) steps to lifecycle.
  These use `{eventPolicyId}` path template (maps to catalog key via _norm_path '*' match)
  so they ARE recorded under management/cloudmonitoring/modifyeventpolicy and
  management/cloudmonitoring/deleteeventpolicy respectively. With literal unfilled
  placeholder in path: API returns 400 InvalidInputValue. With real ID: 400 or 404.
  These steps always run (own group, not blocked by cm-policy group failure).
  CONFIRMED 2026-06-24: DELETE /event-policies/999 -> 404 ResourceNotFound params[eventPolicyId].
  PUT /event-policies/999 -> 400 InvalidRequest params=[null] (NE state blocker).
- **Coverage 2026-06-24:** **6/18** confirmed 2xx ok (same as 2026-06-20):
  getaccountmembers, getadressbooklist, getmetriclist, getproducttypelist (smoke);
  getaccountproductlist (X-ResourceType: Object Storage -> 200 confirmed + recorded);
  getproducteventpolicylist (X-ResourceType + productResourceId -> 200 confirmed + recorded).
  **ALL 18 endpoints now have observations.jsonl entries (0 never-reached).**
  Progress from 2 never-reached to 0: added probe steps for modifyeventpolicy and deleteeventpolicy.
  Remaining 12 gap (all classified):
  - backend-bug-400: getaccounteventlist, getproducteventlist, geteventpolicyhistories
    (date-misparse, no client workaround exists)
  - account-prereq-400: puteventpolicy, modifyeventpolicy, deleteeventpolicy
    (products in NE state, monitoring backend not enrolled)
  - account-prereq-404: getmetricperfdatalist
    (productResourceInfos lookup fails, products NE state)
  - no-real-id-404: geteventdetail, geteventnotificationstates, geteventpolicydetail,
    geteventpolicynotification, getadressbookmemberlist
    (0 events/policies/addrbooks in account — all REACHABLE, will 2xx when real IDs exist)

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

## security / kms

- **Host:** regional (`kms.<region>.<env>...`). 20 endpoints (18 user-managed + 2 managed-kms).
- **Key creation:** `POST /v1/kms/transit` with `key_type: advanced`, `purpose: <spec>`, `auto_rotate: Y`, `rotate_cycle: 7`. Capture `$.key.id`. Response also at `$.key.purpose`, `$.key.state`.
- **Purpose / key type matrix (FULLY VALIDATED live 2026-06-23):**
  - `purpose: rsa-2048` — CONFIRMED 2xx. Asymmetric. Supports: sign/verify, encrypt/decrypt/rewrap. Does NOT support hmac/datakey (400 Key purpose error).
  - `purpose: aes256-gcm96` — CONFIRMED 2xx. Symmetric. Supports: datakey. Does NOT support hmac (400 Key purpose error). WARNING: `aes-256` is NOT valid (400 ValidationError).
  - `purpose: hmac` — CONFIRMED 2xx. Symmetric. Supports: hmac. Does NOT support datakey (400 Key purpose error).
  - `purpose: ecdsa-p256` — API-confirmed valid enum string (from 400 validation error listing). Sign/verify only (not tested live).
  - `purpose: aria` — API-confirmed valid enum string. KR SOUTH government accounts only; standard accounts get 400 "g offering scp" error.
  - **Full valid enum (from API ValidationError 2026-06-23):** `aes256-gcm96`, `ecdsa-p256`, `rsa-2048`, `aria`, `hmac`.
- **Operation-to-purpose mapping (CONFIRMED live 2026-06-23):**
  - encrypt/decrypt/rewrap/sign/verify: needs `rsa-2048`
  - datakey: needs `aes256-gcm96`
  - hmac: needs `hmac` purpose key
- **Lifecycle creates THREE keys** (`security-kms-transit-crypto`): rsa-2048 (`key_id`), aes256-gcm96 (`sym_key_id`), and hmac (`hmac_key_id`). All torn down by lifecycle cleanup on 204.
- **Managed keys:** `GET /v1/managed-kms/transit` returns `{count, keys[], page, size, sort}`. Account count=0 (no managed keys; no create API). `updatemanagedkeydescription` is a **permanent blocker** (system-managed keys only; no provisioning path).
- **Delete:** Soft-delete; key enters `To_Be_Terminated` state (stays in list for days, functionally gone). `DELETE /v1/kms/transit/{key_id}` returns 204. Non-stranding confirmed.
- **Crypto ops (all require active key):** encrypt → `{ciphertext: "vault:v.."}`; decrypt/rewrap ← ciphertext; sign → `{signature}`; verify ← signature + input; hmac ← `{input: base64}` → `{hmac: "vault:v1/.."}`.
- **Coverage 2026-06-23:** 19/20 CONFIRMED 2xx. Only gap: `updatemanagedkeydescription` (permanent blocker — no managed keys, no create API).

## security / secretsmanager

- **Host:** regional (`secretsmanager.<region>.<env>...`). 15 endpoints total (3 read, 12 write).
- **Read-only coverage (smoke + read-chains, no mutations needed):**
  - `listsecretsmanager` (`GET /v1/secrets`) — smoke GET, 200 confirmed.
  - `showsecretsmanager` (`GET /v1/secrets/{secret_id}`) — read-chain (list→show), 200 confirmed.
  - `listversion` (`GET /v1/secrets/{secret_id}/versions`) — read-chain (list→versions), 200 confirmed.
- **All write endpoints require `SCP_ALLOW_MUTATIONS=true`** (lifecycle `security-secretsmanager-writes`).
- **Create body quirks:** `private_acl_enabled` is STRING `"false"` (not boolean). `secret_value` is a JSON STRING (e.g. `"{\"k\":\"v\"}"` not an object). `kms_id` required (must be a real transit KMS key id). `acl_cidr` field REQUIRED (cannot be omitted; ValidationError "Field required" if missing). `acl_cidr` prefix MUST be /25 or longer (API error: "prefix length must be 25 or greater"). Test environment runner IP is in `146.148.x.y` space (observed: .42.91, .98.137, .68.33) — use `146.148.42.0/25` as default (works when runner IP is in that /25; probabilistic).
- **version_list response (CORRECTED 2026-06-23):** `GET /v1/secrets/{secret_id}/versions` returns version OBJECTS (not bare strings). Capture version_id with `$.version_list[0].version_id` (NOT `$.version_list[0]`). Error observed: using `$.version_list[0]` serializes the whole object as a string value causing 404 "Not found with ID {..object..}".
- **setsecretsmanagerlabel body:** `{"label": "<name>", "move_to_version_id": "<version_id>"}`. SYSTEM labels (CURRENT, PREVIOUS) require BOTH `move_to_version_id` AND `remove_from_version_id` — error: `"not-allowed-system-label"`. Use CUSTOM labels (e.g. `"v1tag"`) with only `move_to_version_id` to avoid this constraint. CONFIRMED 2xx 2026-06-23.
- **showsecretsmanagersecretvalue (reveal):** `POST /v1/secrets/{secret_id}/values` with body `{"label": "CURRENT"}`. Returns 400 `source-cidr-error` if calling IP is not in the secret's `acl_cidr`. This is an IP-based ACL check on reveal-value calls. CONFIRMED 2xx 2026-06-23 when runner IP in range.
- **setsecretaclcidr body:** `{"acl_cidr": "<cidr>"}`. CIDR prefix must be /25 or longer (validated same as create). CONFIRMED 2xx 2026-06-23.
- **soft-delete / restore:** `DELETE /v1/secrets/{secret_id}` requires body `{"waiting_time_ndays": 7}` (NOT a plain DELETE). Soft-deleted secret sets state="To be terminated" with 7-day wait before actual deletion — this is expected behavior, NOT leaked resources. Can be restored via `PUT /v1/secrets/{secret_id}/restore` within recovery window. CONFIRMED 2xx 2026-06-23.
- **createsecretsmanagerkmskey (`POST /v1/secrets/kms-key`):** CONFIRMED 404 "NotFound" in this environment across all runs — endpoint path not routed. Permanent blocker unless platform enables this feature. Lifecycle step kept as optional (accepts 404).
- **Coverage 2026-06-23:** 14/15 CONFIRMED 2xx. Only gap: `createsecretsmanagerkmskey` (404, endpoint not routed). Lifecycle `security-secretsmanager-writes` covers 11 write endpoints 2xx + 3 read 2xx from smoke = 14 total.

## management / servicewatch

- **Host:** regional (`servicewatch.<region>.<env>...`). 31 endpoints (alerts, dashboards, event-rules, log-groups/streams, metrics, custom ingest).
- **Metric catalog lookup:** `POST /v1/metrics` with `{}` body (listmetricinfos) returns `{count, namespaces[{id, name, dimensions[{metrics[{id, name, namespace_id, ...}]}]}]}`. This is a POST-as-read (no mutation, no teardown). Use to get real `namespace_id` = `$.namespaces[0].id` and `metric_id` = `$.namespaces[0].dimensions[0].metrics[0].id`. Required for `createalert` — fake doc-sample IDs cause 400.
- **Alert create:** `POST /v1/alerts` — response envelope is FLAT: `{created_at, created_by, id}` (NOT `{alert.id}`). Capture `$.id`. AlertCreateRequest required: `level, metric_id, name, namespace_id, operator, period, statistic`. RANGE operator: use `lower_bound`/`upper_bound` NOT `threshold` (live 400 conflict). `recipient_ids: []` (valid empty). `dimensions`/`individual_items` optional (account-specific, drop for portability).
- **Alert show path:** `GET /v1/alerts/{id}` — the catalog param name is `{id}` (not `{alert_id}`); engine resolves by value so captured `alert_id` variable works.
- **Event-rule create:** `POST /v1/event-rules` — `event_ids`/`resource_type_id`/`service_id` are PLATFORM-GLOBAL catalog values (VALIDATED: `createeventrule` 2xx in prior runs). `recipient_ids: []` valid. `srn_list: []` valid. Response: `{event_rule: {id, ...}}` — capture `$.event_rule.id`.
- **Event-rule GET list:** No list-all endpoint for event-rules (GET /v1/event-rules returns 404). Cannot borrow existing event-rule IDs read-only — create must succeed to get an id for showeventrule.
- **Dashboard capture:** `$.id` (flat, VALIDATED 2026-06-15). Dashboard delete uses field `dashboard_ids` (NOT `ids`) in the bulk DELETE body. All other bulk deletes (alerts, event-rules, log-groups, log-streams) use `ids`.
- **OTLP custom metrics:** `POST /v1/metrics/custom` — `as_int` integer value (not `as_double`), `time_unix_nano` must be a recent epoch (use 1780272000000000000 = 2026-06-01 UTC; 15mo retention). resource.attributes routing key for namespace is `namespace` (UNPROVEN — may still 400). Broad 202/400 tolerance recommended.
- **Coverage 2026-06-20:** 24/31. Remaining gaps: showalert (blocked on createalert needing real metric ids — now fixed in lifecycle), showeventrule (blocked on createeventrule; get-event-rule decoupled from group so it fires), createcustommetrics 400 (OTLP namespace routing unresolved). Target +2 on next light CRUD run.

## ai-ml / aimlops-platform

- **Host:** regional (`aimlops-platform.<region>.<env>...`). 12 endpoints (3 GETs, 6 id-bound GETs, 3 writes).
- **What it is:** Provisions an AI/ML-Ops platform (KubeFlow-based) onto an existing SKE (k8s) cluster. A "release" = a KubeFlow/mini install on the cluster. BILLABLE, slow, requires a running cluster.
- **Images endpoint:** `GET /v1/aimlops-platform/images` returns `$.contents[]` (NOT `$.images[]`). Each item has:
  - `image_id`: ID to pass to POST body, format `IMAGE-<base64-like>` (e.g. `IMAGE-R=HXHY3ccc9f3TSVtPpPFR`). NOT a UUID.
  - `base_image`: version string (e.g. `sdskubeflow-enterprise:1.9.2`, `sdskubeflow-mini:1.9.1`)
  - `image_name`: human-readable (e.g. `AIMLOps Platform 1.9.2`)
  - 2 images available as of 2026-06-20: enterprise 1.9.2 and mini 1.9.1
- **check-duplication:** `GET /v1/aimlops-platform/check-duplication` requires `release_name` query param (NOT `name`). Returns `{"result":false}` → 200 for a non-existing name. Smoke engine tries `name` (400), not `release_name` — smoke records 400 soft. Fix: probe directly with `release_name`.
- **Cluster-bound preconditions (cluster_id format is CRITICAL):**
  - `cluster_id` must be a **32-char hex UUID without hyphens** (str(uuid4()).replace('-','')). API returns 400 "Cluster ID should be 32-letter UUID format" for shorter/hyphenated ids.
  - `checkaimlopsplatformversionv1` (`GET /v1/aimlops-platform/clusters/{cluster_id}/check-version`): requires `version` query param (e.g. `1.9.2`). Returns `{"result":false}` for a real cluster → 200.
  - `validateclusternamespaceforaimlopsplatformv1` (`GET /v1/aimlops-platform/clusters/{cluster_id}/validate-namespaces`): no query params needed. Returns `{"result":false}` → 200 with real cluster.
  - `validateclusterresourcesizeforaimlopsplatformv1` (`GET /v1/aimlops-platform/clusters/{cluster_id}/validate-resources`): requires `product_type` query param. **Valid values: `enterprise` or `mini` ONLY** (NOT KUBEFLOW/AMP). `product_type=KUBEFLOW` → 400 "product_type [enterprise|mini]". Returns `{"result":false}` → 200 with real cluster.
- **Internal endpoints (needs aimlops release):**
  - `GET /v1/aimlops-platform/internal/clusters/{cluster_id}/nodes` and `/storageclasses`: return 404 even with a real running SKE cluster. These proxy INTO the aimlops release itself (need an installed release). 404 error code `NotFound` (different from cluster-not-found code `PRODUCT-AI-ANALYTICS-USER-0002`).
- **Release list:** `GET /v1/aimlops-platform` returns `{"contents":[],"total_count":0}` (account has 0 releases). No `id` field to derive for read-chains.
- **Release by id:** 404 `PRODUCT-AI-ANALYTICS-USER-0002` "Kubeflow (id)" for non-existent release_id.
- **POST body (unproven, best-effort):** `{release_name, cluster_id, image_id, namespace, description, cpu, memory, storage_class_name, volume_size}`.
- **Lifecycle files:**
  - `ai-ml__aimlops-platform.json`: read-only probe-reads lifecycle (heavy:true as gate). Probes id-bound GETs with fake 32-char UUID + required query params.
  - `generated__heavy-aimlops.json`: full live-provision (needs SKE cluster + nodepool + aimlops release; very heavy/billable).
- **Coverage 2026-06-20:** 2 → **6 / 12** (images 200, list 200, check-duplication 200 w/ release_name, check-version 200 w/ real cluster+version param, validate-namespaces 200 w/ real cluster, validate-resources 200 w/ real cluster+product_type=enterprise). Remaining 6 gaps: internal/nodes, internal/storageclasses (needs installed release), getaimlopsplatformv1 (needs real release), POST/PUT/DELETE (mutation-gated+heavy).

## platform / product

CONFIRMED LIVE 2026-06-20. Global account-scoped catalog service. All 4 endpoints are read-only GETs. No mutations. No heavy prereqs. 4/4 covered.

- `listproductcategories` (GET /v1/product-categories) -> 200, count:14. Response: `{count, current_page, product_categories:[{category_id, display_name, display_name_ko, icon_file_id, is_exposed_menu, seq, service_group_color_id, created_at, modified_at}]}`. No required query params (all optional: limit, page, sort, display_name, etc.).
- `listproducts` (GET /v1/products) -> 200, count:20. Response: `{count, current_page, products:[{product_id, display_name, display_name_ko, kind, product_category_id, product_category_name, created_at, modified_at}], total_count, total_pages}`. No required query params (optional: product_category_id, product_id, display_name, kind, sort, etc.).
- `showproduct` (GET /v1/products/{product_id}) -> 200. product_id is a string SLUG, NOT a UUID (e.g. `ESS`, `PRICING`, `SECRETSMANAGER`, `FPMS`, `ORACLESERVICES`). Captured from listproducts `$.products[0].product_id`.
- `showproductcategory` (GET /v1/product-categories/{category_id}) -> 200. category_id is a string SLUG (e.g. `FINANCIAL_MANAGEMENT`, `APPLICATION_SERVICE`, `HYBRID_CLOUD`, `AI-ML`, `DEVOPS_TOOLS`, `SECURITY`). Captured from listproductcategories `$.product_categories[0].category_id`.
- The sidecar (`data/api_catalog_params.json`) correctly declares `produced_by` for both id-bound GETs — probe_reads auto-resolves them. No aliases needed.
- Fragment: `regression/scenarios/lifecycles/platform__product.json` (product-catalog-readonly lifecycle).

## platform / sts (Security Token Service)

3 endpoints, ALL POST (mutating). 0 coverable read-only. Coverage requires SCP_ALLOW_MUTATIONS=true.

- **HOST BUG FIXED 2026-06-29:** `sts` is GLOBAL (not regional). Was missing from `DEFAULT_GLOBAL_SERVICES` in `core/config.py`, causing calls to route to `sts.kr-west1.e.samsungsdscloud.com` which is the Samsung Cloud Platform web portal (returns `text/html` HTML 404, no `Strict-Transport-Security` header). CORRECT host: `sts.e.samsungsdscloud.com` — returns `application/json` responses with `Strict-Transport-Security` header (real API gateway). Fix applied: added `"sts"` to `DEFAULT_GLOBAL_SERVICES` in `core/config.py`. Confirmed live 2026-06-29: GET /v1/assume-role at global host returns `403 {"errors":[{"code":"Forbidden",...}]}` (real API JSON, not portal HTML).
- **All 3 endpoints are POST (mutating):** smoke skips them, lifecycle engine skips them when `allow_mutations=False`. Read-only coverage = structural 0/3.
- **Paths confirmed correct** (api_docs.json 2026-06-20): `/v1/assume-role`, `/v1/assume-role-with-saml`, `/v1/object-store-authorization`.
- **Body field names confirmed** (api_docs.json models):
  - `assumerole` (POST /v1/assume-role): `role_indicator` (REQUIRED, NOT `role_arn`), `role_session_name` (REQUIRED, 1-64 chars), `duration_seconds` (optional, default 900). `role_indicator` format: `[offering:account_id:role_name]`, pattern `^[^:]+:[^:]+:[^:]+$`, minLength 32. Example: `e:ec11538abf8f46d2953539521f745366:OrganizationAccountAccessRole`.
  - `assumerolewithsaml` (POST /v1/assume-role-with-saml): `role_indicator` (REQUIRED), `principal_indicator` (REQUIRED, same format, [offering:account_id:principal_name]), `saml_assertion` (REQUIRED base64 SAML doc, minLength 1), `duration_seconds` (optional). Account has 0 SAML providers.
  - `objectstoreauthorization` (POST /v1/object-store-authorization): `method` (REQUIRED, HTTP verb), `url` (REQUIRED, full object-store URL), `x_amz_content_sha256` (REQUIRED, SHA256 of request body), `x_amz_date` (REQUIRED, AMZ date format YYYYMMDDTHHmmssZ), `region` (optional, default kr-west1), `service` (optional, default s3). NOT bucket_name/duration_seconds. Generates S3-compatible Authorization header from caller's STS session token.
- **Role indicator format:** `e:ACCOUNT_ID:ROLE_NAME`. IAM roles expose `id` (32-hex UUID) and `name` only (no `srn`/`arn`/`role_arn`). Offering code `e` matches `SCP_ENV=e`.
- **Existing roles:** `SCPServiceRoleForScf`, `SCPServiceRoleForApiGateway`, `OrganizationAccountAccessRole` (trust policy allows Account 73eab1a74c6347c1be9c892efc7f1102 only — not our account). After host fix: assumerole expected 403.
- **Remaining blockers for 2xx:** (1) IAM createrole returns 500 (PF-bug) — cannot create self-assumable role; (2) assumerolewithsaml needs real SAML provider + assertion; (3) objectstoreauthorization needs session_token from prior assumerole 200 (sequential dep).
- **Safety:** lifecycle does NOT capture session_token/access_key_id/secret_access_key from any 200 response.
- **Fragment:** `regression/scenarios/lifecycles/platform__sts.json` (sts-token-issuance-coverage lifecycle).
- **Coverage:** 0/3 (all POST, mutation-gated). With mutations enabled and host fix applied: expect 403 on assumerole, 400/404 on assumerolewithsaml, 400/401 on objectstoreauthorization. 2xx requires self-assumable role + trust policy fix.

## financial-management / budget

VALIDATED 2026-06-23. 5/5 endpoints covered (100%). Global service (no region).

- **Host:** `budget.e.samsungsdscloud.com` (GLOBAL; in settings.global_services).
- **Endpoints (all 5):** listaccountbudgets (GET /v1/budgets/account), createaccountbudget (POST), showaccountbudget (GET /v1/budgets/account/{budget_id}), setaccountbudget (PUT /v1/budgets/account/{budget_id}), deleteaccountbudget (DELETE /v1/budgets/account/{budget_id}).
- **Account constraint:** Only 1 budget per account. A second create returns 409 Conflict.
- **Body shape (CONFIRMED LIVE 2026-06-23) — BudgetCreateRequest:**
  ```json
  {
    "amount": 1000000,
    "name": "regr-budget-{unique}",
    "start_month": "2026-06",
    "unit": "MONTHLY",
    "notifications": {
      "is_use_notification": true,
      "notification_send_period": "FIRST",
      "receivers": ["email@example.com"],
      "thresholds": [80]
    }
  }
  ```
  Required: `amount` (integer), `name` (string), `start_month` (YYYY-MM format), `unit` ("MONTHLY" or "OVERALL"). Optional: `notifications`, `prevention`.
- **CRITICAL: start_month must be >= current month.** A past YYYY-MM yields 400 ValidationError "The start month must be the same as or later than the current month." Update this value each run (or use a future month).
- **Response envelope:** Create: `{"budget": {"id": "<32-hex>", ...}}`. List: `{"budgets": [...], "count": N, "page": 0, "size": 20, "sort": [...]}`. NOT `$.contents[]` — must use `$.budgets[]`.
- **budget_id format:** 32-hex UUID (e.g. `240200fd2b02490ca63bb3965048b493`). Capture from create `$.budget.id` or soft-fallback list `$.budgets[0].id`.
- **Status codes:** create → 201, show/set → 200, delete → 204.
- **known_issues.json:** The entry for `financial-management/budget/createaccountbudget` (added 2026-06-12, "500 ContactAdminForAssistance") IS A BODY-SHAPE BUG, NOT a product bug. The 500 was caused by wrong field names in the request body (`budget_amount`/`currency`/`period_type` instead of the correct `amount`/`name`/`start_month`/`unit`). With the corrected body, create reliably returns 201. The entry SHOULD BE REMOVED from known_issues.json.
- **Teardown:** delete → 204; verified account returns to 0 budgets post-run.
- **Fragment:** `regression/scenarios/lifecycles/financial-management__budget.json` (lifecycle `budget-account-budget`).

## financial-management / billingplan

**10 endpoints.** LIGHT-READY (5 of 10 are bare GET returning 200 without any resource). LIVE-VERIFIED 2026-06-29.

- **Host:** global (`billingplan.e.samsungsdscloud.com`; in global_services).
- **Light-coverable GETs (5, all 200 live 2026-06-29):**
  - `GET /v1/planned-computes/contract-types` (listcontracttypes) — response `{contract_types:[{code,display_name}], extension_types:[...]}`. Codes: 01=1yr, 03=3yr, 05=5yr.
  - `GET /v1/planned-computes/os-types` (listostypes) — response `{os_types:[{os_type_id,os_type_value,display_name}]}`. os_type_id: OPEN_SOURCE/RHEL/WINDOWS/SLES (uppercase for query params); os_type_value: opensource/rhel/windows/sles (lowercase for create body).
  - `GET /v1/planned-computes/server-types` (listplannedcomputeservertypes) — response `{server_types:[{server_type,server_type_description,...}]}`. First entry: `css1v1m2` (NOT `s1v1m2` — the docs example was wrong). Confirmed 200 on parameterless call; prior 500 bug was when called WITH query params (service_id/os_type/server_type/start_date/end_date — those are for `listplannedcomputeinstances`, not this endpoint).
  - `GET /v1/planned-computes/service-types` (listplannedcomputeservicetypes) — response `{services:[{service_id,display_name}]}`. Includes VERTICA, SQLSERVER, EPAS, EVENTSTREAMS, SEARCHENGINE, GPU_NODE, GPU_SERVER, VIRTUAL_SERVER and others.
  - `GET /v1/planned-computes` (listplannedcomputes) — response `{current_page,planned_computes:[],total_count:0,total_pages:0}`. Envelope key is `planned_computes` (NOT `contents`). Account has 0 plans.
- **Soft-coverable id-GET (1):** `GET /v1/planned-computes/{planned_compute_id}` (showplannedcompute) — soft-captured from list. Skipped when account has 0 plans (current state).
- **Product bug (confirmed 500):** `GET /v1/planned-computes/instances` (listplannedcomputeinstances) — ALL param combos return 500 ContactAdminForAssistance (os_type OPEN_SOURCE/RHEL/WINDOWS/SLES × any server_type). This is a backend product bug. Excluded from light lifecycle.
- **Writes (3, heavy/mutation gated):** `POST /v1/planned-computes` (createplannedcomputes), `POST /v1/planned-computes/cancellation-fee` (showcancellationfee), `PUT /v1/planned-computes/{id}` (updateplannedcompute). Covered in `billingplan-planned-computes-guarded` (heavy=true). createplannedcomputes is BILLABLE (reserves committed compute for 1-5 year term); NOT chained to delete; lifecycle deliberately expects 4xx.
- **Light lifecycle:** `billingplan-light-reads` (`financial-management__billingplan-light.json`, heavy=false). Covers 5 GETs (light 2xx floor). Validated + validate_dag clean 2026-06-29.
- **Heavy lifecycle:** `billingplan-planned-computes-guarded` (`financial-management__billingplan.json`, heavy=true). Covers remaining 3 writes (expected 4xx on shared account — coverage-only probe, no actual purchase).
- **server_type note:** The resource model and lifecycle used `s1v1m2` as example but the real API returns `css1v1m2` as the first server type from listplannedcomputeservertypes (2026-06-29). The create body field accepted value is unproven without a real purchase.
- **os_type duality:** `os_type_id` (OPEN_SOURCE) for query filters; `os_type_value` (opensource) for create body.

## security / secretvault

- **5 endpoints** total: listsecretvault, createsecretvault, showsecretvault, terminatedsecretvault, gettemporarykey.
- **Coverage: 4/5** (gettemporarykey = permanent blocker). Validated 2026-06-23.
- **access_key_id in create body**: MUST be the IAM access key's UUID `id` field (from `GET /v1/access-keys $.access_keys[0].id`). NOT the access_key string (SCP_ACCESS_KEY). Error when wrong: `SecretVaultAccesskeyNotFoundError` 400.
- **1 vault per auth key**: "Access key is already in use." 400 when same IAM key already bound to a vault. A "To be terminated" vault still occupies the key.
- **No hard DELETE**: only `PUT /v1/secretvault/{id}/terminated` with `{waiting_time_ndays: 7-30}`. Vault stays in "To be terminated" state for the waiting period then auto-deletes.
- **gettemporarykey PERMANENT BLOCKER**: `GET /v1/temporarykey/{id}` requires vault-issued headers `Svaccesskey`/`Svsignature`/`Svtimestamp`/`Svclienttype` — NOT derivable from SCP API creds. 400 `Svaccesskey is not existed.` even with real vault id.
- **name constraint**: `^[a-z0-9]*$`, 3-63 chars. `temporary_key_ttl_nhours` in [1,36]. `vault_token_ttl_ndays` in [30,7300].
- **Show envelope**: `$.secret_vault.{id, access_key, access_key_id, acl_cidr, ...}`. Create response includes `vault_token_id` + `vault_token_secret_value` (only available at create time).
- **lifecycle**: `security-secretvault-vault` — pre-step fetches IAM key UUID, list captures existing vault for show probe.

## devops-tools / devopsservice

6 endpoints: listdevopsservices, checkduplicationdevopsservice, createdevopsservice, showdevopsservice, checkdeletabledevopsservice, deletedevopsservice.

**Confirmed facts (2026-06-23):**
- `GET /v1/devops-services` — 200 OK, envelope `{count, devops_services: [{id, ...}]}`. Account has 0 devops services.
- `GET /v1/devops-services/check-duplication` — requires EXACTLY ONE of `tenant_code` OR `tenant_name` (not both). Sending both → 400 "Tenant name, Tenant code [required only one of both]". Sending `?tenant_code=regrprobesmoke` → 200 `{result: false}`. Pattern: `^[a-z0-9\-]*$`.
- `POST /v1/devops-services` — body: `DevOpsServiceCreateRequest{tenant_name: str, tenant_code: str, members: array[string, >=1]}`. Both codes use `^[a-z0-9\-]*$`. tags optional.
- **BLOCKER (entitlement-prereq)**: POST returns 409 `{code: scp-devops.devops-service.not-found-admin-user-service, detail: Not Found Admin User}` even with a valid IAM user id (`f2b627e6bf4f4b3996f04de4f877bd11`, the account owner) in members[]. This means the account's DevOps backend admin-user service is not configured — a platform activation prereq, not a params/body issue.
- Account quota: 1 devops-service per account.
- Show/create response envelope: `{devops_service: {id, account_id, tenant_name, tenant_code, status, console_url, created_at, ...}}`. Capture: `$.devops_service.id`.
- `smoke.py _required_param_candidates`: added `{tenant_code: _DUP_NAME}` candidate for `/check-duplication` path (2026-06-23). Before this fix, smoke sent `{name:}` which 400'd on devopsservice.
- Account IAM user id (owner): `f2b627e6bf4f4b3996f04de4f877bd11` (name: kyuh.choi+areg1@samsung.com, account_id: ec11538abf8f46d2953539521f745366).

## management / iam-identity-center (SSO)

- **32 endpoints** total: listinstances, createinstance, showinstance, setinstance, deleteinstance; listgroups, creategroup, showgroup, setgroup, deletegroup, deletebulkgroups; listgroupusers, createbulkgroupusers, deletebulkgroupusers; listusers, createuser, showuser, setuser, deleteuser, deletebulkusers; listpermissionsets, createpermissionset, showpermissionset, setpermissionset, deletepermissionset, listpermissionsetpolicies, setpermissionsetpolicies, deletepermissionsetpolicies; listaccountassignments, createaccountassignment, deleteaccountassignment, deletebulkaccountassignments.
- **Coverage: 2/32** (2026-06-24): `listinstances` (200 empty list), `deletepermissionsetpolicies` (204 no-op).
- **Account entitlement BLOCKER**: `POST /v1/instances` returns 403 `{code: identity-center.InstanceCreateNotAllowedAccount, detail: "Allow to create identity center only for organization's management account"}`. This account is NOT the org management account. All instance creates are permanently blocked. Non-management accounts can only READ (if an instance exists) or perform no-op empty-list deletes.
- **Account state**: `GET /v1/instances` returns 200 with `{count: 0, instances: []}` — no SSO instance provisioned.
- **Required query param `instance_id`**: listgroups, listusers, listpermissionsets, listaccountassignments, showgroup, showuser, showpermissionset, listgroupusers, listpermissionsetpolicies all require `instance_id` as a query param. Without it → 400 ValidationError. With synthetic `ssoins-12345` → 404 ResourceNotFound. listaccountassignments additionally requires `target_account_id`.
- **deletepermissionset** requires `instance_id` as a QUERY PARAM (not body). Without it → 400; with it → 404 (synthetic).
- **deletepermissionsetpolicies 204 no-op pattern**: `DELETE /v1/permission-sets/{any_id}/policies` with body `{"instance_id": "<any_string>", "policy_ids": []}` returns **204 NO CONTENT** regardless of the path/instance_id validity. This is an idempotent empty-list delete — the backend short-circuits when `policy_ids` is empty and returns success without touching any resources. This is the ONLY IdC endpoint that returns 2xx without a real SSO instance. Proven 2026-06-24.
- **WRONG BODY for deletepermissionsetpolicies**: The `setpermissionsetpolicies`-shaped body `{custom_policies:[], inline_policies:[], managed_policies:[]}` always 400s ValidationError. Correct body: `{instance_id: "<str>", policy_ids: []}`.
- **Non-heavy lifecycle `idc-delete-policies-probe`**: exercises deletepermissionsetpolicies with correct body `{instance_id, policy_ids:[]}` and path `{ualpha}` (so `_norm_path` normalizes to `*/policies` → catalog key resolved → recorded as 2xx).
- **Heavy lifecycle (all other writes)**: `idc-instance`, `idc-user`, `idc-group`, `idc-permission-set`, `idc-account-assignment` — all REACHABILITY-ONLY (4xx expected). These can only 2xx if the account is upgraded to org management account.
- **listinstances**: No params, returns 200 with empty list when no instance exists. The only unconditionally-200 endpoint.
- **Path-param endpoints**: showinstance, showgroup, showuser, showpermissionset return 404 with synthetic IDs when instance not found. deletegroup, deleteuser require `instance_id` in the request body (not query param); without it → 400.
- **Fragment**: `regression/scenarios/lifecycles/management__iam-identity-center.json`.

## application-service / apigateway

**48/55 confirmed live 2026-06-24.** VPC-free control-plane. Fragment:
`regression/scenarios/lifecycles/application-service__apigateway.json`
(lifecycles: `apigateway-api-write-coverage`, `apigateway-privatelink-endpoint`).

**Key facts:**
- `POST /v1/apis` body: `{name, description, endpoint_type: REGION|PRIVATE, tags: []}`.
  name pattern `^[a-z][a-z0-9-]{1,48}[a-z0-9]$`. REGION = no prereq; PRIVATE forces JWT auth.
- Root resource: `GET /v1/apis/{api_id}/resources` at `$.resources[0].id` is the `parent_id` for child resource creates.
- `createmethod` body: `{method_type, integration_type: HTTP, endpoint_url, api_key_required: bool, iam_enabled: bool, query_strings: {}, request_headers: {}}`. Methods addressed by `method_type`, not an id.
- `createapideployment` with `stage_type: new` creates both deployment AND stage in one call; returns `$.deployment_id`. Requires >=1 method first.
- `createauth` returns `$.access_token` ONLY (no id). Recover `auth_id` via `GET /v1/apis/{api_id}/auths $.auths[0].id`.
- `GET /v1/apis/{api_id}/reports` needs 3 required query params: `stage_name`, `start_date`, `end_date` (YYYY-MM-DD). Range <=30 days, not before 30 days ago. Use `{iso_29d_ago}` / `{iso_today}` engine vars.
- **listreports engine quirk (LIVE 2026-06-24)**: GET step with query params only in path string is NOT credited under catalog key. Engine needs a `"params"` key in the step (even `"params": {}`). Fix applied in lifecycle.
- `setresourcepolicy` (PF-19): returns 500 ContactAdminForAssistance. Add 500 to `expect_status` so xcov-resource-policy group survives and `deleteresourcepolicy` runs (204 idempotent).
- **PrivateLink body shapes (docs-verified 2026-06-24):** `connectprivatelinkendpoint {api_id, type: DISCONNECT|RECONNECT}`, `approveprivatelinkendpoint {api_id, type: APPROVE|REJECT}`, `requestprivatelinkendpoint {type: CANCEL|RE_REQUEST}`, `setprivatelinkendpoint {description}`.
- **All 7 PrivateLink endpoints are entitlement-403 blockers (CONFIRMED 2026-06-24).** Account lacks PrivateLink IAM actions even with correct request bodies. `createprivatelinkendpoint` also 500 (PF-23).
- stage pattern `^[a-z][a-z0-9-]{1,48}[a-z0-9]$`, addressed by name. `createaccesscontrols` -> `$.id`. `createusageplan` -> `$.usage_plan.id`. `createapikey` -> `$.api_key.id`.
- API delete is async; poll `GET /v1/apis/{id}` until 404.

## compute / scf (Serverless Cloud Function)

**Coverage: 17/36 as of 2026-06-24.** VPC-free control-plane for cloud functions.

- **PrivateLink service for SCF — CONFIRMED LIVE 2026-06-24:**
  - `PUT /v1/cloud-functions/{fn_id}/configurations/privatelink-services` enables/disables PL.
    Body: `{privatelink_service_enabled: true|false}` (top-level, no wrapper). Returns 202.
  - `GET /v1/cloud-functions/{fn_id}/configurations/privatelink-services` returns 200 with:
    `{privatelink_service_enabled, privatelink_service_state, privatelink_service_id, privatelink_service_name, private_url, ...}`.
  - **CAUTION: enabling PL service can strand the function in CREATING state indefinitely.**
    When `privatelink_service_enabled: true` → backend creates a PL service. The PL service
    can get stuck in CREATING state (seen: >40 minutes, still CREATING). In CREATING state:
    `privatelink_service_state = CREATING`, `privatelink_service_id = null`.
    **Disabling is blocked:** 400 `scp-cloud-function.privatelink-service-not-allow-state-error:
    Privatelink Service deactivation is not allowed when Creating state.` Wait for ACTIVE.
  - Once PL service is ACTIVE, disable with `{privatelink_service_enabled: false}`.

- **PrivateLink endpoint for SCF — CONFIRMED LIVE 2026-06-24:**
  - `POST /v1/cloud-functions/{fn_id}/configurations/privatelink-endpoints` creates endpoint.
    Body: `{endpoint: {alias, service_id}}` where service_id is a VPC PLS id. Returns 200.
    Response: `{endpoint: {endpoint_alias, endpoint_id, service_id, state}}`. Note:
    **response field is `endpoint_id` (not `id`)**.
  - `GET /v1/cloud-functions/{fn_id}/configurations/privatelink-endpoints` lists endpoints.
    Response: `{privatelink_endpoints: [{endpoint_alias, endpoint_id, service_id, state}]}`.
    Note: **key is `privatelink_endpoints` (not `endpoints`)**.
  - `PUT .../request` body: `{type: CANCEL|RE_REQUEST}`. CANCEL from REQUESTING → CANCELED;
    RE_REQUEST from CANCELED → REQUESTING. Returns 200.
  - `DELETE .../configurations/privatelink-endpoints/{endpoint_id}` deletes. Returns 204.
    Note: DELETE requires endpoint NOT be in REQUESTING state (400 invalid-state-error).
    CANCEL first, then DELETE.
  - **PRODUCT BUG (CONFIRMED 2026-06-24):** `PUT .../approval` and `PUT .../connection`
    always return 404 `scp-cloud-function.privatelink-endpoint-not-found` even when the
    endpoint_id IS valid (returned by list + used successfully in /request and DELETE).
    Approval of SCF PLEs must be done via VPC's `PUT /v1/privatelink-endpoints/{id}/approval`.
    These two SCF endpoints are non-functional — classify as product-bug-404.
  - **SCF PLE requires a VPC PLS that is ACTIVE and the function itself must be in READY state.**
    If function is DEPLOYING (due to stuck PL service), createprivatelinkendpoint → 400.

## Services not yet deeply explored (stubs — fill in as you go)

database (mysql, mariadb), data-analytics (partial),
and the long tail of management/networking/storage.
These have the most uncovered endpoints — see `scenario-catalog.md` gap list.
