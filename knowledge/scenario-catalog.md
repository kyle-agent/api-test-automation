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
