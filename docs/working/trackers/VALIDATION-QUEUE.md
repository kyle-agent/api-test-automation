# VALIDATION-QUEUE — prioritized order for the coverage-validator

> Owner: the **coverage-validator** agent (`agents/validation-agent.md`). This is
> the work queue that turns `provenance: docs` model nodes into `VALIDATED` one
> service / small batch at a time, cheapest verification first.
>
> **State at authoring (2026-06-15):** `python knowledge/formal/validate.py` →
> 272 resource task nodes in 59 files; **181 `docs` (unvalidated)** across
> **51 services**, **91 already `VALIDATED`**. Split below: **Wave A (light)
> 95 · Wave B (heavy) 86 · Gated (owner) 23** (the gated count is a subset
> surfaced out of A/B — see the Gated table).
>
> **State (2026-06-17, live):** `python knowledge/formal/validate.py` →
> **275 resource task nodes** in 59 files, 0 errors; **144 `docs` (unvalidated)**,
> **131 `VALIDATED`**. Progress since authoring: +3 nodes total, **+40 promoted to
> VALIDATED** (91 → 131), docs backlog 181 → 144. (Queue rows below were authored
> against the 2026-06-15 snapshot and are not re-struck here — re-derive the open
> set from current `provenance: docs` nodes before dispatching.)

## How the validator uses this

1. Take the top **non-gated** row of **Wave A**; prep offline; ask Meta-Orch to
   dispatch **one** `crud_filter=gen-<service>*` run; triage against
   `data/baselines/verified_endpoints.json` (IB-041); promote on a real 2xx,
   else climb L0→L3 (`orchestrator.md`).
2. Promote → strike the row, surface any newly-unblocked dependents up into Wave A.
3. STOP-6 hit → move the row to **Gated** with the IB id.
4. Exhaust Wave A before Wave B (low-verification-first, `AUTONOMOUS-LOOP.md`).

**Prioritization rules applied:** (a) light/non-billable before heavy;
(b) dependency order — a node's `requires` validated first; (c) among equals,
**deep-modeled-but-unvalidated first** (services with many `docs` nodes);
(d) gated (credential/console/license/billing) split out for the owner.

---

## Wave A — light (non-heavy create, cheap to verify)

Ordered: zero-prereq read/lookup & account-singleton creates first (cheapest, no
heavy closure), then light children that ride on an **already-VALIDATED** heavy
parent (so no new heavy provisioning is needed), then light children that still
pull a heavy prereq into the closure.

### A.1 — zero-prereq read / lookup / account-singleton (cheapest, no closure)

| rank | service | node(s) | light/heavy | depends-on | gated? | notes |
|------|---------|---------|-------------|------------|--------|-------|
| 1 | financial-management/pricing | `pricing-reads` | light | — | no | read-only; 2xx trivially in `verified_endpoints` |
| 2 | financial-management/costexplorer | `cost-reads` | light | — | no | read-only |
| 3 | management/quota | `quota-request` | light | — | no | read/list-only family |
| 4 | management/support | `support-inquiry`, `support-service-request` | light | — | no | create-ticket; low blast radius, non-billable |
| 5 | data-analytics/quick-query | `quick-query-list`, `quick-query-image-versions`, `quick-query-validate` | light | — | no | the 3 read/validate-only QQ nodes (the `quick-query` create itself is gated, see Gated) |
| 6 | platform/sts | `sts-token` | light | — | no | token issue; no teardown, no cost |
| 7 | financial-management/budget | `account-budget` | light | — | no | account-scoped budget create |
| 8 | management/loggingaudit | `trail` | light | — | no | account-scoped trail |
| 9 | management/network-logging | `network-logging-storage` | light | — | no | account-scoped |
| 10 | management/resourcemanager | `resource-group-bulk` | light | — | no | bulk resource-group; reuses existing ids |
| 11 | management/servicewatch | `sw-metric-catalog`, `alert`, `dashboard`, `sw-custom-metric-meta`, `sw-custom-log-collect` | light | — | no | **deep (5 docs nodes)**; mostly metric/alert/dashboard reads+creates, no heavy infra |
| 12 | management/cloudmonitoring | `cm-account-resource`, `cm-event-policy` | light | — | no | account-scoped monitoring |
| 13 | security/configinspection | `diagnosis` | light | — | no | run a diagnosis, read result |
| 14 | security/secretvault | `secretvault-vault` | light | — | no | vault create/delete |
| 15 | security/certificatemanager | `certificate-import` | light | — | no | import a self-signed cert body |

### A.2 — IAM family (deep, 9 light docs nodes; account-scoped, light prereqs)

| rank | service | node(s) | light/heavy | depends-on | gated? | notes |
|------|---------|---------|-------------|------------|--------|-------|
| 16 | management/iam | `iam-user`, `iam-role`, `iam-group-member`, `iam-saml-provider`, `iam-resource-policy` | light | — / `iam-group`(VALIDATED) | no | **deepest light family**; users/roles/members are account-scoped, no infra |
| 17 | management/iam | `iam-user-policy-binding`, `iam-role-policy-binding`, `iam-group-policy-binding`, `iam-policy-binding-set` | light | `iam-user`/`iam-role`(rank 16) | no | bindings ride on the rank-16 principals — validate right after |

### A.3 — light children of an already-VALIDATED parent (no new heavy provisioning)

| rank | service | node(s) | light/heavy | depends-on | gated? | notes |
|------|---------|---------|-------------|------------|--------|-------|
| 18 | networking/firewall | `firewall`, `firewall-rule` | light | vpc(V), igw(V) | no | firewall implicitly created by IGW; rule rides on it |
| 19 | networking/loadbalancer | `lb-member`, `lb-member-bulk`, `lb-static-nat` | light | load-balancer/server-group(V) | no | members attach to an existing VALIDATED LB chain |
| 20 | storage/filestorage | `fs-snapshot-schedule`, `fs-replication` | light | filestorage-volume(V) | no | ride on existing FS volume |
| 21 | storage/parallel-filestorage | `pfs-snapshot` | light | pfs-volume(heavy, see B) | no | snapshot of a PFS volume — pairs with B pfs-volume run |
| 22 | storage/baremetal-blockstorage | `bm-volume-snapshot`, `bm-group-snapshot` | light | bm-block-volume/bm-volume-group(see B) | no | snapshots; pair with the BM-blockstorage heavy run |
| 23 | container/scr | `scr-image`, `scr-tag` | light | container-registry(V) | partial | registry exists; **image push needs SCR auth key** → tag/image-meta reads OK, push gated (see Gated) |
| 24 | application-service/apigateway | `apigw-auth`, `apigw-privatelink-endpoint` | light | api-gateway(V), privatelink-service(B) | no | auth rides on existing apigw; privatelink-endpoint pairs with the VPC privatelink run |
| 25 | compute/scf | `scf-apigateway-trigger`, `scf-privatelink-endpoint` | light | scf-function(V), apigw/privatelink | no | trigger/endpoint wiring on existing function |

### A.4 — light DB sub-ops on an already-VALIDATED cluster (PG cluster is VALIDATED)

PostgreSQL `postgresql-cluster` is already `VALIDATED`, so its light read/lookup
and parameter nodes verify against the existing cluster without new heavy spend.
The other DB engines' clusters are heavy/docs (Wave B) — their light sub-ops
validate in the same run that brings the cluster up.

| rank | service | node(s) | light/heavy | depends-on | gated? | notes |
|------|---------|---------|-------------|------------|--------|-------|
| 26 | database/postgresql | `pg-engine-version-16`, `pg-parameter-group`, `pg-parameter`, `pg-log-export-config` | light | postgresql-cluster(V) | no | **deep DB family, cheapest path** — cluster already VALIDATED; lookups+param-group+log-config only |
| 27 | database/epas | `epas-engine-version`, `epas-engine-version-16`, `epas-server-type`, `epas-parameter-group` | light | — (lookups) | no | engine-version/server-type are pure lookups (no cluster needed) |
| 28 | database/mysql | `mysql-engine-version-8`, `mysql-parameter-group` | light | — (lookups) | no | lookups, no cluster |
| 29 | database/mariadb | `mariadb-engine-version-10`, `mariadb-parameter-group` | light | — (lookups) | no | lookups, no cluster |
| 30 | database/sqlserver | `ss-server-type`, `ss-parameter-group` | light | — (lookups) | no | lookups only; the cluster + HA path is gated (license, see Gated) |

### A.5 — light children that pull one heavy prereq into the closure

These are "light" creates but require a heavy parent that is itself `docs`
(Wave B). Validate them **in the same run** as their parent (batch by closure) so
the heavy provisioning is paid once.

| rank | service | node(s) | light/heavy | depends-on | gated? | notes |
|------|---------|---------|-------------|------------|--------|-------|
| 31 | compute/virtualserver | `image-registration`, `launch-configuration`, `auto-scaling-group`, `asg-policy`, `asg-schedule`, `asg-notification` | light | server(V)/image | no | **deep (7 docs, 6 light)**; ASG family rides on a VALIDATED server+image; validate as one batch |
| 32 | compute/multinodegpucluster | `gpu-node-image`, `gpu-node-product`, `gpu-node-fabric` | light | — (lookups) | no | the 3 GPU lookups (the `gpu-node` create is heavy → B) |
| 33 | database/{mysql,mariadb,epas} | `*-instance-group`, `*-parameter`, `*-log-export-config` | light | `<engine>-cluster`(B) | no | validate in the same run that brings each cluster up (Wave B) |
| 34 | networking/vpc | `endpoint-subnet`, `private-nat`, `tgw-vpc-connection`, `vpc-endpoint`, `privatelink-service` | light* | vpc/subnet(V) + heavy TGW/FS | no | **READY-FOR-LIVE (IB-012/013)**; non-heavy create flag but pulls heavy TGW/FS closure — treat as B-cost; the 5 are the modeled-but-unvalidated VPC frontier |
| 35 | data-analytics/quick-query | `quick-query-update-description` | light | quick-query(gated) | gated | needs the gated `quick-query` create first → stays gated until IB-018 |

---

## Wave B — heavy (billable / slow create; one serial VPC-capped run each)

Ordered by: dependency (cluster before its sub-ops), then deepest-modeled DB
families, then standalone heavy clusters, then the highest-blast-radius last.
Each heavy node's light sub-ops (Wave A.3/A.5) ride the same run.

| rank | service | node(s) | light/heavy | depends-on | gated? | notes |
|------|---------|---------|-------------|------------|--------|-------|
| 36 | database/postgresql | `pg-add-block-storage`, `pg-resize-block-storage`, `pg-resize-server-type`, `pg-switchover`, `pg-restore`, `pg-patch`, `pg-kernel-upgrade` | heavy | postgresql-cluster(V), pg-instance-group(V) | no | **7 heavy sub-ops on the ALREADY-VALIDATED PG cluster** — cheapest heavy family (no new cluster build) |
| 37 | database/mysql | `mysql-cluster`-deps + `mysql-add/resize-block`, `resize-server-type`, `stop`,`start`,`restart`,`switchover`,`restore`,`patch`,`kernel-upgrade` | heavy | (cluster build) | no | **deep (15 docs)**; one run builds cluster + validates the heavy sub-op family |
| 38 | database/mariadb | cluster + `mariadb-add/resize-block`, `resize-server-type`, `stop`,`start`,`restart`,`switchover`,`restore`,`patch`,`kernel-upgrade` | heavy | (cluster build) | no | **deep (15 docs)**; mirror of mysql |
| 39 | database/epas | `epas-instance-group`+ cluster, `epas-add/resize-block`, `resize-server-type`, `switchover`,`restore`,`patch`,`kernel-upgrade` | heavy | (cluster build) | no | **deep (14 docs)** |
| 40 | storage/parallel-filestorage | `pfs-volume` | heavy | vpc/subnet(V) | no | brings up PFS; pairs with A.3 `pfs-snapshot` |
| 41 | storage/baremetal-blockstorage | `bm-block-volume`, `bm-volume-group` | heavy | baremetal-server(B) | no | block volume + group; pairs with A.3 snapshots |
| 42 | compute/baremetal | `baremetal-server` | heavy | vpc/subnet(V) | no | bare-metal provision; prereq for BM blockstorage |
| 43 | compute/multinodegpucluster | `gpu-node` | heavy | gpu-node-image/product/fabric(A.4) | no | GPU node create (validate lookups first) |
| 44 | networking/vpn | `vpn-gateway`, `vpn-tunnel` | heavy | vpc(V) | no | VPN gateway+tunnel chain |
| 45 | networking/cdn | `cdn` | heavy* | — | no | listed light-create but provisions a CDN distribution; treat as heavy/slow |
| 46 | networking/gslb | `gslb` | heavy* | — | no | global DNS LB; slow provisioning |
| 47 | networking/direct-connect | `direct-connect` | heavy | security-group(V) | maybe | **physical-circuit suspicion (owner check pending)** — if console/physical, → Gated |
| 48 | networking/vpc | `private-nat`, `vpc-endpoint`, `tgw-vpc-connection`, `endpoint-subnet`, `privatelink-service` | heavy-closure | TGW(V)/FS(V)/subnet(V) | no | the IB-012/013 READY-FOR-LIVE frontier; one heavy networking run validates all 5 |
| 49 | data-analytics/searchengine | `searchengine-cluster` | heavy | vpc/subnet(V) | no | OpenSearch DBaaS cluster |
| 50 | data-analytics/eventstreams | `eventstreams-cluster` | heavy | vpc/subnet(V) | no | Kafka DBaaS cluster |
| 51 | data-analytics/vertica | `vertica-cluster` | heavy | vpc/subnet(V) | no | analytics DBaaS cluster |
| 52 | financial-management/billingplan | `planned-compute` | heavy | — | no | planned-compute create (billable signal) |
| 53 | management/cloudcontrol | `cloudcontrol-landing-zone` | heavy | organization(gated) | gated | landing zone needs org-master → Gated |
| 54 | ai-ml/aimlops-platform | `aimlops-platform` | heavy | ske-cluster | no | MLOps platform on SKE |
| 55 | management/organization | `organization`, `organization-unit`, `organization-account`, `service-control-policy`, `delegation-policy`, `org-invitation` | heavy | — | gated | **6 docs, HIGHEST BLAST RADIUS**; billable/irreversible + non-master 403 → Gated |
| 56 | storage/backup | `backup-agent`, `backup-target`, `backup-policy`, `backup-manual`, `backup-restore-target`, `backup-restore` | heavy | server(V) | no | **6 docs**; agent install + policy/restore chain on a VALIDATED server |

---

## Gated (owner) — STOP-6 hits; do NOT dispatch until owner unblocks

These are removed from the active waves. The validator skips them and surfaces
them here with the blocking IB. Owner action is the only unblock.

| service | node(s) | STOP-6 | IB | what the owner must provide |
|---------|---------|--------|----|-----------------------------|
| database/sqlserver | `sqlserver-cluster` + all 14 `ss-*` heavy sub-ops, esp. `ss-add-secondary` | ①credential/license | **IB-017** | SQL Server **license key** (Enterprise/Always-On Secondary) — no self-issue path. All 18 sqlserver `docs` nodes blocked except the 2 pure lookups (rank 30) |
| data-analytics/quick-query | `quick-query`, `quick-query-update-description` | ②console-only value | **IB-018** | **DSC domain** real value (+ UNPROVEN SKE-on-k8s body). The 3 read/validate QQ nodes (rank 5) are not gated |
| data-analytics/data-flow | `data-flow`, `data-flow-service` | ①+②credential/value | **IB-018/021** | account id/pw; real **SKE-on-NiFi** body (current body is dbaas-shaped artifact) |
| data-analytics/data-ops | `data-ops`, `data-ops-service` | ①+②credential/value | **IB-018/021** | account id/pw; real **SKE-on-Airflow** body |
| ai-ml/cloud-ml | `cloud-ml` | ①credential | (SCR auth key) | container-registry **SCR auth key** (console-issued; same class as SCR-push blocker). `cloudml-image` rides the same key |
| container/scr | `scr-image` (push) | ①credential | (SCR auth key) | SCR push auth key. `scr-tag` / image-meta **reads** are not gated |
| management/organization | `organization`, `organization-unit`, `organization-account`, `service-control-policy`, `delegation-policy`, `org-invitation` | ④billing/irreversible + ①master account | — | an **org-master test account** (non-master returns 403); org-account create is billable + irreversible. SCP **attach** is never modeled at all |
| management/cloudcontrol | `cloudcontrol-landing-zone` | ④+①master account | — | landing zone requires org-master (depends on the org family above) |
| management/iam-identity-center | `iam-identity-center`, `idc-user`, `idc-group`, `idc-permission-set`, `idc-account-assignment` | ①SSO/identity-store setup | — | **IDC/SSO enablement** (identity store + SSO instance) — console-provisioned prerequisite; 5 docs nodes blocked |
| storage/archivestorage | `archive-bucket`, `archiving-policy` | ①dedicated auth key | — | Archive Storage **dedicated auth key** not issued (precedent: `st-as` group label "전용 인증키 미발급") |
| networking/direct-connect | `direct-connect` | ⑤physical circuit (pending owner) | — | confirm whether DC needs a **physical circuit** (owner check pending); if so it is console/physical-only |
| (cross) | second-account quota split | ①2nd account | **IB-007** | a **second account** credential for account-level quota partitioning |

---

## First 5 the validator should tackle (rationale)

1. **financial-management/pricing `pricing-reads`** — pure read-only, zero
   prereq, zero cost; guaranteed 2xx lands in `verified_endpoints.json`
   immediately. Lowest-verification possible (warms the IB-041 evidence path).
2. **financial-management/costexplorer `cost-reads`** — same class; read-only,
   no closure, instant evidence.
3. **management/quota `quota-request`** + **management/support inquiry/request** —
   account-scoped, non-billable, no heavy infra; cheap creates that exercise the
   triage→promote path with low blast radius.
4. **management/servicewatch (5 nodes)** — **deepest light family with no heavy
   prereq** (metric-catalog/alert/dashboard/custom-metric/custom-log); one cheap
   run promotes 5 nodes at once — best light return-on-run.
5. **management/iam (rank 16–17, 9 nodes)** — second-deepest light family,
   account-scoped, `iam-group` already VALIDATED; principals (user/role/member)
   then their bindings promote 9 nodes across two cheap batches.

Rationale summary: rank 1–5 are all **light, non-billable, low/zero-prereq**, and
the deep families (servicewatch 5, iam 9) maximize promotions-per-run — directly
serving the low-verification-first principle while building the
`verified_endpoints.json` evidence base the harder Wave B promotions will rely on.
