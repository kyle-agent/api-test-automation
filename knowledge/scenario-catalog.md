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
| `platform-product-reads` | platform/product | read-only product + product-category endpoints |
| `pricing-reads` | financial-management/pricing | read-only pricing report endpoints (3 direct GETs; no id-bound, no probe) |
| `costexplorer-reads` | financial-management/costexplorer | read-only bills/usages/monthly-payment (3 direct GETs; no id-bound, no probe) |
| `billingplan-reads` | financial-management/billingplan | read-only planned-computes (+ 5 enum GETs) + probe id-bound showplannedcompute |
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

> **33 lifecycles total** (30 enabled = 24 light + 6 heavy gated by
> `SCP_RUN_HEAVY`; 3 disabled here). The setter-coverage expansion (26 write steps, in-place
> updates) added in the trusting-curie merge lives inside several existing
> lifecycles as extra steps — see `docs/HANDOFF-crud-setter-validation.md`.

## Coverage gap (drive AXIS 1 to 100%)

Use `python -m spec.summary` for live numbers. As of last check: 225
directly-testable GETs (smoke floor), 302 id-bound GETs (need read-chain/CRUD),
845 mutating endpoints (need CRUD scenarios). The biggest uncovered surfaces by
endpoint count are **database (255)**, **management (244)**, **networking (205)**,
**compute (181)**, **storage (129)**, **data-analytics (119)** — prioritize CRUD
scenarios there.

### Triage: services with NO lifecycle yet (by endpoint count)

Derived statically from `data/api_catalog.json` minus the `service` of every
lifecycle in `scenarios.json` (re-derive with the snippet at the bottom of this
section). `directGET` = directly-testable GETs (no path param = smoke floor).
**Cost class** gates how soon we can write it: *read* = pure GET surface, can be
a zero-cost read-only lifecycle today (model on `quota-reads` / `support-reads` /
`platform-product-reads`); *light* = creatable without billable cluster/VM;
*heavy* = billable cluster/DB/VM, gate behind `SCP_RUN_HEAVY`.

| Endpoints | directGET | Category | Service | Cost | Smallest concrete scenario idea |
|----------:|----------:|----------|---------|------|---------------------------------|
| 47 | 5 | database | `epas` | heavy | epas create→show→delete cluster (mirror mysql/postgresql heavy) |
| 46 | 5 | database | `mariadb` | heavy | mariadb create→show→delete cluster (mirror mysql) |
| 41 | 2 | storage | `baremetal-blockstorage` | light | volume-group + volume create→show→delete (note: red in conformance — 5xx-on-bad-input) |
| 38 | 5 | database | `sqlserver` | heavy | sqlserver create→show→delete cluster |
| 37 | 9 | management | `organization` | read | read-only: list orgs/accounts/OUs + probe id-bound GETs (mutations risky — defer) |
| 34 | 5 | networking | `loadbalancer` | light | LB needs a vpc+subnet: create LB → listener → show → delete |
| 32 | 5 | management | `iam-identity-center` | light | create group/permission-set → show → delete (red in conformance) |
| 32 | 5 | database | `cachestore` | heavy | cachestore create→show→delete cluster |
| 31 | 9 | storage | `backup` | read→light | read-only first: list backup policies/vaults + probe; later create policy |
| 26 | 3 | data-analytics | `searchengine` | heavy | searchengine create→show→delete cluster |
| 25 | 6 | storage | `archivestorage` | light | bucket/vault create→show→delete |
| 24 | 5 | data-analytics | `eventstreams` | heavy | eventstreams create→show→delete cluster |
| 23 | 3 | data-analytics | `vertica` | heavy | vertica create→show→delete cluster |
| 18 | 8 | management | `cloudmonitoring` | read | read-only: list metrics/event-policies + probe id-bound GETs |
| 17 | 3 | data-analytics | `data-ops` | light | read-only first (list image-versions etc.) |
| 17 | 3 | data-analytics | `data-flow` | light | read-only first (list flows) |
| 16 | 5 | compute | `multinodegpucluster` | heavy | GPU cluster — billable, defer |
| 16 | 3 | compute | `baremetal` | heavy | bare-metal server — billable, defer |
| 15 | 4 | management | `cloudcontrol` | light | resource-control create→show→delete |
| 12 | 3 | ai-ml | `aimlops-platform` | heavy | platform create→show→delete (cluster-backed) |
| 12 | 2 | data-analytics | `quick-query` | light | query create→run→show→delete |
| 11 | 2 | storage | `parallel-filestorage` | light | parallel volume create→show→delete (mirror filestorage) |
| 10 | 6 | financial-management | `billingplan` | ✅ done | covered by `billingplan-reads` (6 direct GETs + probe showplannedcompute) |
| 10 | 2 | networking | `vpn` | light | vpn-gateway/tunnel (needs vpc) |
| 10 | 2 | management | `loggingaudit` | read→light | read-only: list audit logs/trails |
| 10 | 2 | networking | `gslb` | light | gslb create→show→delete |
| 9 | 2 | ai-ml | `cloud-ml` | heavy | cloud-ml cluster — billable, defer |
| 9 | 1 | networking | `cdn` | light | cdn distribution create→show→delete |
| 8 | 2 | networking | `firewall` | light | firewall rule (needs vpc) |
| 8 | 1 | networking | `direct-connect` | light | direct-connect connection create→show→delete |
| 7 | 3 | security | `configinspection` | read→light | read-only: list inspection results + probe |
| 6 | 2 | devops-tools | `devopsservice` | light | devops project create→show→delete |
| 5 | 1 | security | `secretvault` | light | vault create→show→delete (mirror secretsmanager) |
| 5 | 1 | financial-management | `budget` | read→light | read-only: list account budgets + probe showaccountbudget |
| 4 | 2 | platform | `product` | ✅ done | covered by `platform-product-reads` |
| 4 | 2 | management | `network-logging` | read→light | read-only: list logs |
| 3 | 3 | financial-management | `pricing` | ✅ done | covered by `pricing-reads` (3 direct GETs; no id-bound, adds no coverage over smoke) |
| 3 | 3 | financial-management | `costexplorer` | ✅ done | covered by `costexplorer-reads` (3 direct GETs; no id-bound, adds no coverage over smoke) |
| 3 | 0 | platform | `sts` | light | token mint (POST-only; no GET to read back) |

**Recommended next wins (cheapest coverage, no billing, low mutation risk):** the
*read* class. `pricing`, `costexplorer`, `billingplan` are now ✅ done
(`pricing-reads` / `costexplorer-reads` / `billingplan-reads`). Remaining read-class
picks — `cloudmonitoring`, `organization` (read-only slice) — each adds a `*-reads`
lifecycle exactly like `platform-product-reads` and unlocks both direct + id-bound
GETs at zero cost.
Then the small *light* CRUD services (`secretvault`, `archivestorage`,
`cloudcontrol`, `gslb`, `cdn`, `direct-connect`). Defer all *heavy* DB/cluster
services until a heavy session.

> Re-derive this table any time:
> ```bash
> python3 -c "import json,collections;cat=json.load(open('data/api_catalog.json'));sc=json.load(open('regression/scenarios/scenarios.json'))['lifecycles'];cov={l['service'].split('/',1)[1] for l in sc if '/' in l.get('service','')};bs=collections.defaultdict(collections.Counter);cc={};dg=collections.Counter();[ (bs[e['service']].update([e['method']]), cc.__setitem__(e['service'],e['category']), dg.update([e['service']] if e['method']=='GET' and '{' not in e['http_path'] else [])) for e in cat];rows=sorted(((sum(c.values()),dg[s],cc[s],s) for s,c in bs.items() if s not in cov),reverse=True);[print(f'{t:4d} dG={d:3d} {cat:18s} {s}') for t,d,cat,s in rows]"
> ```

## How to inspect / extend

```bash
# list all lifecycles with flags
python3 -c "import json;[print(f\"{l['id']:45} enabled={l.get('enabled')} heavy={l.get('heavy',False)}\") for l in json.load(open('regression/scenarios/scenarios.json'))['lifecycles']]"
```

Add a scenario = add an entry to `scenarios.json` (no new Python; the engine
drives it) + declare any quota kinds in `dependencies.json` + record validated
facts in `validated-facts.md`. See `agents/domain-knowledge-agent.md`.
