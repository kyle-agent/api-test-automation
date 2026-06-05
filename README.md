# SCP API Test Automation

Automated testing for the Samsung Cloud Platform (SCP) Open APIs documented at
<https://docs.e.samsungsdscloud.com/apireference/>
(**15 categories / ~60 services / ~1,372 endpoints**).

The suite is **catalog-driven** (the API Reference is parsed once into a
machine-readable inventory; tests are generated from it) and organised around
**two axes** on a shared kernel. See [`ARCHITECTURE.md`](ARCHITECTURE.md) for the
full blueprint.

```
                         core/  (shared kernel)
       config · auth · http_client · catalog · registry · results · budgets
                ▲                 ▲                 ▲            ▲
   spec/ (extract+diff)   regression/ (AXIS 1)  conformance/ (AXIS 2)  cleanup/ (reconciler)
                                  │                 │
                                  └──── results store (reports/results/*.jsonl) ────► dashboard/
```

- **AXIS 1 — `regression/` (does it work?)** read-only smoke + list→show
  read-chains + ordered CRUD scenarios that create/delete real resources, recording
  **pass/fail + response time** and widening coverage.
- **AXIS 2 — `conformance/` (is it well designed/built?)** static spec analysis +
  read-only runtime probes that surface design/implementation defects, with a
  pluggable rule lens and a baseline so only NEW defects alarm.
- **Supports:** `spec/` extracts the spec from the docs and diffs versions ·
  `dashboard/` visualizes both axes from one results store · `cleanup/` guarantees
  teardown via a tag-based reconciler.

## Layout

```
core/         config·auth·http_client·catalog  +  registry·results·budgets
spec/         extract_catalog · extract_bodies · summary · diff
regression/   smoke · read_chains · scenarios/{engine, scenarios.json, dependencies.json}
conformance/  static · runtime · baseline · rules/  (pluggable Rule lens)
cleanup/      reconciler   (tag-ownership sweep; legacy name-prefix fallback)
dashboard/    build        (reads the unified results store; legacy fallback)
tests/        thin pytest entrypoints that drive the regression engines
data/         api_catalog.json · api_bodies.json · api_docs.json · conformance.json
              baselines/known_issues.json
reports/      per-run output (gitignored): results/*.jsonl, registry/*.jsonl, dashboard/
.github/workflows/api-test.yml   one orchestrator (spec → regression → sweep + conformance → dashboard)
```

## Setup

```bash
pip install -r requirements.txt
cp .env.example .env               # fill in SCP_REGION + credentials
python -m spec.extract_catalog     # build/refresh data/api_catalog.json (resumable)
python -m spec.summary             # coverage summary of the catalog
```

`spec.extract_catalog` uses HTTP Range requests (only each page `<head>` is
needed), retries the gateway's intermittent 503s with backoff, and is resumable.
Track spec changes between two catalog snapshots with `python -m spec.diff old.json new.json`.

## Running

```bash
# AXIS 1 — read-only smoke regression across the whole catalog (no resource changes)
pytest tests/smoke -m smoke
pytest tests/smoke -m smoke --category compute --service virtualserver   # scoped

# AXIS 1 — CRUD lifecycles (create/delete REAL resources — opt in explicitly)
SCP_ALLOW_MUTATIONS=true SCP_ALLOW_DESTRUCTIVE=true pytest tests/crud -m crud

# AXIS 2 — conformance (design/impl defects; read-only / empty-body probes)
python -m conformance.static                 # static spec analysis + rule lens
python -m conformance.runtime --probe all    # runtime probes (gated; non-destructive)
python -m conformance.baseline --init-if-missing   # only NEW defects alarm

# support — render the dashboard from the results store
python -m dashboard.build

# support — reclaim any leftover test resources (tag-scoped; destructive)
SCP_ALLOW_DESTRUCTIVE=true python -m cleanup.reconciler
```

Per-run signals land in `reports/results/` (`observations.jsonl` = axis-1 calls
with response time; `findings.jsonl` = axis-2 defects) and the pytest HTML/JUnit
in `reports/`.

## Safety model

The catalog includes destructive operations. A run never changes real cloud state
unless explicitly opted in:

| Operation | Default | Enable with |
|-----------|---------|-------------|
| `GET` (read-only) | runs | always allowed |
| `POST` / `PUT` / `PATCH` | **blocked** | `SCP_ALLOW_MUTATIONS=true` |
| `DELETE` | **blocked** | `SCP_ALLOW_DESTRUCTIVE=true` |

Smoke + read-chains only call read-only `GET`s; mutating/parameterised endpoints
are exercised by explicit, ordered CRUD scenarios.

### Resource ownership & cleanup (registry)

Every resource a run creates is stamped (`core.registry.owner_tags`) with an
owner/run/axis/TTL tag and recorded in a per-run manifest. Teardown is therefore
**deterministic and isolation-safe**: a run tears down its own manifest in reverse
order, and `cleanup.reconciler` reclaims account-wide orphans **only when they
carry our owner tag and are finished/expired** — it never touches another run's
live resources (name-prefix matching remains a fallback for tag-less resources).
CI exports `APITEST_RUN_ID` so the tags are attributable per run.

### Quotas & scheduling (budgets)

Account limits (e.g. the 5-VPC cap, private-dns) are modelled as data
(`core.budgets` + `regression/scenarios/dependencies.json`). The scenario engine
**reserves** a slot before a quota-bound create and environmentally **skips**
(not fails) when exhausted, so quota pressure never shows up as a false regression.

## CRUD scenarios

Scenarios are **declarative** — add an entry to
`regression/scenarios/scenarios.json` (no new Python) and the engine drives
create → read → delete in order. Per-step features:

- `capture` — pull a value from a response (`$.vpc.id`) into a `{placeholder}`;
  `{unique}`/`{region}` are seeded automatically.
- `service` — override the host for that step (a chain can span services).
- `poll` / `wait` — wait for async provisioning (`{field, until, timeout, interval}`).
- `cleanup` — the delete to register for a created resource (reverse-order teardown).
- `group` + `optional` — a multi-engine/family scenario isolates a failing group
  (tears down just that group, keeps the rest) so one bad body costs one family,
  not the whole run.
- `destructive: true` — marks deletes (need `SCP_ALLOW_DESTRUCTIVE`).

**Light** scenarios run in routine opted-in CRUD; **heavy** ones (`heavy: true`,
real billable VM / K8s / DB / shared-networking, ~20–60 min) run ONLY when
`SCP_RUN_HEAVY=true` (manual dispatch). Validate a single heavy scenario with the
dispatch `crud_filter` input. In CI, set repo variable **`SCP_RUN_CRUD=true`** to
opt a run into CRUD; the result + any teardown is posted as a PR comment.
`dependencies.json` maps the seven VPC-creating scenarios to their quota kinds.

## Endpoints (per-service hosts)

SCP Open API endpoints are **per service**, not one gateway, in two flavours:

```
regional: https://<service>.<region>.<env>.samsungsdscloud.com   e.g. vpc.kr-west1.e...
global  : https://<service>.<env>.samsungsdscloud.com            e.g. product.e...
```

Path roots collide across services (`/v1/clusters` is used by ske, mariadb,
mysql, …), so each call targets its own host. Set `SCP_REGION` (+ `SCP_ENV`,
default `e`) and the suite builds each service's host from the catalog name.
**Global (account-scoped) services have no region segment** — the built-in list
(`product, pricing, iam, organization, quota, billingplan, budget, costexplorer,
cloudcontrol, resourcemanager, support`) was DNS-verified and is extendable via
`SCP_GLOBAL_SERVICES`. Override odd subdomains via `SCP_SERVICE_HOSTS` (JSON);
`SCP_BASE_URL` is a last-resort single-host fallback (a concrete URL, not a wildcard).

## Authentication

SCP signs Open API calls with **Access Key + HMAC-SHA256**. The signing-string
layout and header names are not on the public API Reference pages, so
`core/auth.py` keeps the signing string in one overridable method and the header
names configurable via env (`SCP_HMAC_*`). Confirm against a real `200`; on
`401/403`, adjust `HmacSigner.signing_string` / the header env vars.
`SCP_AUTH_SCHEME=bearer|none` is also supported.

## Dashboard

`python -m dashboard.build` renders a self-contained HTML dashboard. It reads the
unified results store (`reports/results/observations.jsonl` + `findings.jsonl`)
first, falling back to legacy flat files (`reports/smoke_status.tsv`,
`data/conformance.json`, `reports/junit-crud.xml`) so nothing regresses mid-migration.
It shows health (new vs known regressions, pass rate, coverage), per-service
drill-down with **status + response time** and **design/behavior defect** columns,
the CRUD grid, and trends. The `index.html` + `history.jsonl` are published to the
**`dashboard-data`** branch each run; publish via **Settings → Pages → Deploy from
a branch → `dashboard-data` / `(root)`**. Mute a tracked backend bug by adding it
to `data/baselines/known_issues.json` so only genuinely new breakage alarms.

## CI (GitHub Actions)

`.github/workflows/api-test.yml` is a single orchestrator:
**spec** (refresh catalog) → **regression** (smoke + read-chains, opt-in CRUD) →
**sweep** (`cleanup.reconciler`) and **conformance** (static + runtime + baseline)
→ **dashboard** (build + publish). Read-only smoke runs on a daily schedule; CRUD
and destructive steps run only via dispatch with the safety gates checked.

Configure once in **Settings → Secrets and variables → Actions**:

| Type | Name | Notes |
|------|------|-------|
| Variable | `SCP_REGION` | e.g. `kr-west1` — builds per-service hosts |
| Variable | `SCP_ENV` | default `e` |
| Secret | `SCP_ACCESS_KEY` / `SCP_SECRET_KEY` | credentials |
| Secret | `SCP_PROJECT_ID` | optional |
| Variable | `SCP_RUN_CRUD` | `true` to opt scheduled/CI runs into CRUD |
| Variable | `SCP_SERVICE_HOSTS` | optional JSON overrides for odd subdomains |
| Variable | `SCP_HMAC_*`, `SCP_AUTH_SCHEME` | optional auth overrides |

> The gateway must be reachable from the runner. If it is on a private
> network/VPN, use a `self-hosted` runner with network access.
