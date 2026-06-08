# Shared Context (CONTEXT.md)

> Every agent loads this. It is the single source of *current state* and the
> facts all roles share. Keep it short and current — deep detail lives in
> `knowledge/`. (이 문서는 모든 agent가 공유하는 현재 상태입니다.)

## What we are building

Automated testing for the **SCP Open APIs** (docs:
<https://docs.e.samsungsdscloud.com/apireference/>), organized on **two axes**
over a shared kernel (`core/`):

- **AXIS 1 · Regression** (`regression/`) — *does it work?* Read-only smoke,
  list→show read-chains, and ordered CRUD scenarios that create/delete real
  resources. Records **pass/fail + response time**. **Target = 100% endpoint
  coverage; then widen parameter combinations.**
- **AXIS 2 · Conformance** (`conformance/`) — *is it well designed & AI-usable?*
  **Static** spec analysis + **read-only runtime** probes, emitted as findings
  against a baseline so only NEW defects alarm.

Supports: `spec/` (extract+diff the spec), `dashboard/` (visualize both axes),
`cleanup/` (tag-scoped teardown).

## The catalog at a glance (source of truth: `data/api_catalog.json`)

- **1,372 endpoints**, all resolved. 13 categories present in the catalog.
- By method: GET 527 · POST 383 · PUT 244 · DELETE 209 · PATCH 9.
- Smoke-testability split: **225** GETs are directly testable (no path params);
  **302** GETs need a resource id (reached via CRUD/read-chains); **845** are
  mutating (reached via CRUD lifecycles).
- Re-run the live summary any time: `python -m spec.summary`.

> Coverage is what drives AXIS 1 to 100%: directly-testable GETs are the floor;
> the long tail (id-bound GETs + mutating endpoints) is unlocked by writing more
> CRUD scenarios. See `knowledge/scenario-catalog.md` for what exists today.

## Endpoints, region & auth (essentials)

- **Per-service hosts**, not one gateway:
  - regional: `https://<service>.<region>.<env>.samsungsdscloud.com`
  - global (account-scoped, no region): `https://<service>.<env>.samsungsdscloud.com`
- Global services: `billingplan, budget, cloudcontrol, costexplorer, iam,
  organization, pricing, product, quota, resourcemanager, support`
  (override via `SCP_GLOBAL_SERVICES`).
- Path roots collide across services (`/v1/clusters` ∈ ske, mariadb, mysql…) →
  **always target the service's own host**. Set `SCP_REGION` (+ `SCP_ENV`, default
  `e`); odd subdomains via `SCP_SERVICE_HOSTS` (JSON).
- Auth: **Access Key + HMAC-SHA256** over
  `method + encodeURI(url) + timestamp + accessKey + clientType`, Base64, in
  `Scp-*` headers (`clientType = "Openapi"`). Tunables in `core/auth.py` +
  `SCP_HMAC_*`. On 401/403, adjust the signing string / header env vars.

## Safety gates (NON-NEGOTIABLE)

| Operation | Default | Enable with |
|-----------|---------|-------------|
| `GET` (read-only) | runs | always allowed |
| `POST` / `PUT` / `PATCH` | **blocked** | `SCP_ALLOW_MUTATIONS=true` |
| `DELETE` | **blocked** | `SCP_ALLOW_DESTRUCTIVE=true` |
| Heavy/billable lifecycles (VM, K8s, DB) | **skipped** | `SCP_RUN_HEAVY=true` |

Smoke + read-chains only call `GET`s. Mutations happen exclusively through
ordered CRUD scenarios. Never relax these as a shortcut.

## Isolation & teardown (why we don't break other runs)

- Every created resource is stamped by `core.registry` with
  `(owner, run_id, axis, ttl)` and recorded in a per-run manifest →
  deterministic reverse-order teardown.
- `cleanup.reconciler` reclaims account-wide orphans **only when they carry our
  owner tag and are finished/expired**. Name-prefix matching is a *fallback* only.
- Account quotas (vpc=5, private-dns=3, …) are modelled in `core.budgets` +
  `regression/scenarios/dependencies.json`; the engine **reserves** a slot before
  a quota-bound create and **skips** (not fails) when exhausted.
- **VPC scheduling / reuse:** 8 lifecycles touch the 5-VPC cap. The **6 heavy**
  ones now **adopt one session-shared VPC** (`conftest.py shared_vpc` →
  `engine.provision_shared_vpc`; steps marked `{"adopt":"vpc"}`), so heavy runs
  hold 1 shared VPC instead of up to 6 and `heavy-shared-networking` is no longer
  starved (6 creates → 1; no-op fallback to self-create; pending live validation —
  `tests/crud/test_shared_vpc_adopt.py`). The 2 light networking lifecycles still
  self-create for coverage. Cross-run isolation + remaining gaps (the pytest CRUD
  driver still builds a fresh `Budget` per lifecycle and never `sync()`s it live):
  see the lane playbook in
  [`knowledge/vpc-scheduling-strategy.md`](../knowledge/vpc-scheduling-strategy.md)
  (machine-readable: `dependencies.json:vpc_schedule`).

## Where results live (the contract)

One unified store under `reports/results/` (gitignored):

- `observations.jsonl` — AXIS 1 calls (`endpoint_key, method, path, status,
  category∈{ok,soft,fail}, elapsed_ms, source∈{smoke,read_chain,crud_probe}`).
- `findings.jsonl` — AXIS 2 defects (`endpoint_key, rule_id, severity∈{red,
  yellow,green}, detail, source∈{static,runtime}`).

Schema lives in `core/results.py`. The dashboard reads this store first (legacy
flat files are a fallback). Baseline: `data/baselines/known_issues.json`.

## Current state (keep this updated as work progresses)

- Catalog: extracted, 1,372 endpoints, 0 unresolved.
- **29 CRUD lifecycles** today (full list + flags in
  `knowledge/scenario-catalog.md`). Light: resourcemanager resource-group, quota/
  support reads, vpc+subnet+port, scr registry+repo, filestorage volume,
  certificatemanager self-sign, queueservice queue, security-group(+rule),
  virtualserver keypair, virtualserver volume+snapshot, vpc public-ip, vpc
  internet-gateway, kms key, secretsmanager secret, apigateway api+resource, scf
  function+trigger, iam group, iam policy, servicewatch loggroup+stream.
  **Heavy** (`SCP_RUN_HEAVY`): ske cluster+nodepool, virtualserver full VM, mysql
  cluster, postgresql cluster, shared dbaas, shared networking. Disabled:
  dns-hosted-zone, iam-role, certificatemanager-import. Many lifecycles also carry
  **write-setter / in-place-update** steps (coverage expansion) — see
  `docs/HANDOFF-crud-setter-validation.md`.
- Auth/host resolution: implemented & configurable; confirm against a live `200`.
- **Coverage campaign (multi-agent) — RUNNING.** `agents/CAMPAIGN.md` is the
  operating model; `agents/coordination/ledger.json` is the blackboard. Per-service
  CRUD fragments now live in `regression/scenarios/lifecycles/*.json` (merged by
  `regression/scenarios/loader.py`; validate with
  `python -m regression.scenarios.validate`). Real target = the **547 uncovered
  write ops / 53 services** from `python -m spec.coverage_gap` (id-bound GETs are
  auto-covered by read-chains). **Wave 1 done** (6 fragments, 13 new lifecycles):
  iam, organization, iam-identity-center, servicewatch, baremetal-blockstorage,
  apigateway → 151 writes closed. **Wave 2 done** (7 cluster-agents, 30 fragments,
  49 lifecycles): networking/{vpc,loadbalancer,dns,cdn,gslb,vpn,firewall,direct-
  connect}, compute/virtualserver, the 6 database engines, storage/{archive,backup,
  file,parallel-file}, security/{kms,secrets,vault,configinspection,certmgr},
  data-analytics ×6 → +302 writes. **Static ceiling 43.0% → 78.6%** (95 lifecycles,
  validator 0 errors, offline tests pass). **36 services at write-gap 0.** All
  bodies docs-derived, **pending live validation**.
- **What to advance next:** Wave 3 mops up the last **88 writes / ~14 services**:
  compute/{baremetal 12, multinodegpucluster 9, scf 7}, container/{scr 10, ske 2},
  management/{cloudcontrol 9, resourcemanager 9, loggingaudit 6, cloudmonitoring 4,
  network-logging 2}, ai-ml ×2, financial-management ×2, platform/sts 3,
  devops-tools 2. Then a live CI run (needs lane scheduling to fit the 300-min cap)
  to convert the static ceiling into measured `cov_op`.

> When you finish a unit of work that changes any of the above, update this
> section (and the relevant `knowledge/` file) in the same commit.
