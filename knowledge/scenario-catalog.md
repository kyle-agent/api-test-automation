# Scenario catalog

The declarative CRUD lifecycles that exist today
(`regression/scenarios/scenarios.json`) and the coverage gap. Each lifecycle
drives create → read → (update) → delete with `capture`/`poll`/`cleanup` and the
safety gates. Re-derive flags with the snippet at the bottom.

## Light scenarios (run in routine opted-in CRUD)

| Lifecycle id | Service | Headline resource(s) |
|--------------|---------|----------------------|
| `resourcemanager-resource-group` | management/resourcemanager | resource group |
| `networking-vpc-subnet` | networking/vpc | vpc + subnet + port |
| `quota-reads` | management/quota | read-only quota endpoints |
| `support-reads` | management/support | read-only support endpoints |
| `container-scr-registry` | container/scr | registry + repository |
| `filestorage-volume` | storage/filestorage | NFS volume |
| `security-certificatemanager-selfsign` | security/certificatemanager | self-signed cert |
| `application-queueservice-queue` | application-service/queueservice | queue |
| `networking-security-group` | networking/security-group | SG + rule |
| `virtualserver-keypair` | compute/virtualserver | SSH keypair (zero-cost) |
| `compute-virtualserver-volume-snapshot` | compute/virtualserver | block volume + snapshot |
| `networking-vpc-publicip` | networking/vpc | public IP |
| `networking-vpc-internet-gateway` | networking/vpc | vpc + internet gateway |
| `security-kms-key` | security/kms | KMS key |
| `security-secretsmanager-secret` | security/secretsmanager | secret |
| `application-apigateway-api-resource` | application-service/apigateway | API + resource |
| `compute-scf-cloud-function-cronjob-trigger` | compute/scf | cloud function + trigger |
| `iam-group` | management/iam | IAM group |
| `iam-policy` | management/iam | IAM policy |
| `servicewatch-loggroup-logstream` | management/servicewatch | log group + stream |

> **Shared VPC (heavy):** the heavy lifecycles below no longer each create a VPC
> — they **adopt one session-shared VPC** (`conftest.py shared_vpc` →
> `engine.provision_shared_vpc`; `create-vpc`/`delete-vpc` steps carry
> `{"adopt":"vpc"}`). Each still makes its own subnet under it (re-homed to
> `10.124.1-6.0/24`). 6 VPC creates → 1; no-op fallback to self-create. See
> `knowledge/vpc-scheduling-strategy.md`. Pending live validation.

## Heavy scenarios (billable; only with `SCP_RUN_HEAVY=true`)

| Lifecycle id | Service | Notes |
|--------------|---------|-------|
| `container-ske-cluster-nodepool` | container/ske | real K8s, ~27 min, consumes 1 vpc |
| `compute-virtualserver-full` | virtualserver | real VM, ~17 min, consumes 1 vpc |
| `database-mysql-cluster` | database/mysql | DB cluster, consumes 1 vpc |
| `database-postgresql-cluster` | database/postgresql | DB cluster, consumes 1 vpc |
| `heavy-shared-dbaas` | networking/vpc | shared DBaaS flow, consumes 1 vpc |
| `heavy-shared-networking` | networking/vpc | consumes vpc + private-dns |

## Disabled (need work before enabling)

| Lifecycle id | Why disabled |
|--------------|--------------|
| `networking-dns-hosted-zone` | needs a private-dns activate sub-flow; must declare `private-dns` quota when re-enabled |
| `iam-role` | not yet validated |
| `security-certificatemanager-import` | cert import flow currently unsatisfiable (see HANDOFF doc) |

> **29 lifecycles total** (24 enabled, 3 disabled here + the heavy ones gated by
> `SCP_RUN_HEAVY`). The setter-coverage expansion (26 write steps, in-place
> updates) added in the trusting-curie merge lives inside several existing
> lifecycles as extra steps — see `docs/HANDOFF-crud-setter-validation.md`.

## Coverage gap (drive AXIS 1 to 100%)

Use `python -m spec.summary` for live numbers. As of last check: 225
directly-testable GETs (smoke floor), 302 id-bound GETs (need read-chain/CRUD),
845 mutating endpoints (need CRUD scenarios). The biggest uncovered surfaces by
endpoint count are **database (255)**, **management (244)**, **networking (205)**,
**compute (181)**, **storage (129)**, **data-analytics (119)** — prioritize CRUD
scenarios there. Record specific missing endpoints here as you triage them.

> **TODO (next sessions):** triage uncovered endpoints per category into concrete
> scenario ideas and list them here so the Regression agent can pick them up.

## How to inspect / extend

```bash
# list all lifecycles with flags
python3 -c "import json;[print(f\"{l['id']:45} enabled={l.get('enabled')} heavy={l.get('heavy',False)}\") for l in json.load(open('regression/scenarios/scenarios.json'))['lifecycles']]"
```

Add a scenario = add an entry to `scenarios.json` (no new Python; the engine
drives it) + declare any quota kinds in `dependencies.json` + record validated
facts in `validated-facts.md`. See `agents/domain-knowledge-agent.md`.

---

## Coverage campaign — fragment lifecycles (Wave 1, 2026-06-08)

Per-service CRUD lifecycles now also live in
`regression/scenarios/lifecycles/<category>__<service>.json` fragments (merged by
`regression/scenarios/loader.py`; one file per service-agent → no collisions).
See `agents/CAMPAIGN.md`. Wave 1 closed **151 write ops across 6 services**,
raising the static coverage ceiling **43.0% → 55.4%** (write gap 547 → ~390).

| Fragment | Lifecycles | Writes | Flags |
|----------|-----------|--------|-------|
| `management__iam.json` | iam-role-full, -policy-extra-writes, -group-bindings, -user-policy-bindings, -resource-policy, -credentials-heavy | 35 | 5 light + 1 heavy |
| `management__organization.json` | org-{organizations,units,accounts,service-control-policies,policy-bindings-and-delegation,invitations}-guarded | 23 | all heavy+optional (blast radius) |
| `management__iam-identity-center.json` | idc-{instance,user,group,permission-set,account-assignment} | 19 | all heavy+optional (SSO) |
| `management__servicewatch.json` | servicewatch-{alert,dashboard,event-rule,custom-ingest} | 16 | light |
| `storage__baremetal-blockstorage.json` | blockstorage-{volume,volume-group} | 30 | heavy (billable) |
| `application-service__apigateway.json` | apigateway-{api-write-coverage,privatelink-endpoint} | 28 | light |

Remaining write-op gap after Wave 1: **~390 across 47 services** (top: compute/
virtualserver 41, networking/vpc 38, then database epas/mariadb/mysql/postgresql
~17 each, storage/archivestorage, security/kms, …). Track in
`agents/coordination/ledger.json`. All Wave-1 bodies are docs-derived and pending
live validation — see `knowledge/validated-facts.md` "Wave 1 facts".

## Coverage campaign — Wave 2 (2026-06-08)

7 cluster-agents added **30 fragment files / 49 lifecycles** closing **302 write
ops**; static ceiling **55.4% → 78.6%** (write gap 390 → 88). Services at write-gap 0:
networking/{vpc,loadbalancer,dns,cdn,gslb,vpn,firewall,direct-connect},
compute/virtualserver, database/{mysql,mariadb,epas,postgresql,sqlserver,cachestore},
storage/{archivestorage,backup,filestorage,parallel-filestorage},
security/{kms,secretsmanager,secretvault,configinspection,certificatemanager},
data-analytics/{data-flow,data-ops,quick-query,searchengine,vertica,eventstreams}.

**Remaining write gap = 88 / ~14 services** (Wave 3 targets): compute/baremetal 12,
container/scr 10, compute/multinodegpucluster 9, management/cloudcontrol 9,
management/resourcemanager 9, compute/scf 7, management/loggingaudit 6,
management/cloudmonitoring 4, ai-ml/{aimlops-platform,cloud-ml} 3 each,
financial-management/{billingplan,budget} 3 each, platform/sts 3, container/ske 2,
devops-tools/devopsservice 2, management/network-logging 2, networking 1.

## Coverage campaign — Wave 3 (2026-06-08) — WRITE COVERAGE COMPLETE

4 cluster-agents closed the final 88 writes / 17 services: compute/{baremetal,
multinodegpucluster,scf}, container/{scr,ske}, management/{cloudcontrol,
resourcemanager,loggingaudit,cloudmonitoring,network-logging}, ai-ml/{aimlops-
platform,cloud-ml}, financial-management/{billingplan,budget}, platform/sts,
devops-tools/devopsservice, networking/security-group.

**Campaign result: every one of the 547 catalog write operations is now reachable
by an enabled lifecycle (write-op gap = 0 across all 53 services).** 113 lifecycles
total (29 base + 84 in 53 fragments). Static ceiling **43.0% → 85.6%**; the residual
198-endpoint gap is exclusively id-bound GETs, which read-chains (list→show) and
CRUD probe_reads discover at runtime — so measured live `cov_op` runs above the
static figure. All bodies are docs-derived and **pending live validation** (see
`validated-facts.md`). Next step: a lane-scheduled live CI run to convert the static
ceiling into measured coverage.

## Coverage expansion — 2026-06-11 (levers ①③④, docs-derived)

No new lifecycles; existing ones extended (see `docs/COVERAGE-WAVE-PLAN.md`):

- **DBaaS sub-op window prep (①)**: `database-mysql-cluster` + `database-postgresql-cluster`
  gained conservative-only window groups (`mysql-subop-window`/`mysql-restart`/
  `pg-subop-window`) — read-only sub-op GETs incl. `show-request` (AsyncResponse
  `request_id` capture), no-body `sync-state`, mysql `restart`+wait. Still gated by
  `heavy:true`.
- **servicewatch (③)**: metric POST bodies fixed to real catalog namespace/metric;
  explicit `get-group` step closes the showloggroup static gap.
- **eventstreams (④)**: guarded sub-op bodies re-derived from the correct api_docs
  models; sync-state/parameters-sync/unset-maintenance added; read-coverage
  literal-uuid bug fixed (closes the shared `/v1/requests/*` static gap for 9
  DBaaS-family services).

Static ceiling **85.6% → 86.3%** (gap_getid 166 → 156; gap_write 32 = all waived).

## Composed lifecycles — M5 resource-task model (2026-06-12)

New lifecycles are no longer hand-written: the resource-task model
(`knowledge/formal/resources/*.yaml`, **127 nodes**) is compiled by
`regression/scenarios/composer.py` into `gen-<node>` / `bundle-<group>`
lifecycles, kept in `regression/scenarios/lifecycles/generated__*.json`
(**136 lifecycles total** now pass the scenarios validator). Status of the
composed set:

| Lifecycle | Steps | Status |
|---|---|---|
| `gen-pilot-net-basics` (vpc/subnet/port/igw/public-ip) | 20 | **enabled** — 20/20 live, kept as the composer-path canary |
| `gen-wave-vslight` (server-group, volume-transfer) | 9 | **enabled** — 9/9 in three consecutive runs (27394211896 · 27395331657 · 27396649009) |
| `gen-wave-apigw` (api→…→api-key/usage-plan/access-control) | 20 | **enabled** — 20/20 twice incl. all deletes |
| `gen-wave-dashboard` | 3 | **enabled** — create 201 proven; capture fixed to flat `$.id` (rev 4) |
| `gen-wave-mgmisc` / `gen-wave-devops` / `gen-wave-net-endpoint` | 3/3/9 | disabled — triaged blockers in node notes (product 400s, console-only ids) |
| `gen-cloudml-chain` | 24 | disabled (gated-ready) — full chain composed, blocked on SCR auth key (console credential) + heavy |

Live wave findings + blocked classes: `docs/RESOURCE-MODEL-PLAN.md` §6.
Current static ceiling per `python -m spec.coverage_gap`: **86.6% (1,188/1,372)**;
latest published run C3 **44.3%**, fail_new 0, 249 waivers. R3 direction:
hand-written lifecycles above get replaced by composed equivalents node-by-node
after live verification — treat this catalog's earlier sections as history.
