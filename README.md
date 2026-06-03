# SCP API Regression Test Automation

Automated regression tests for the Samsung Cloud Platform (SCP) Open APIs
documented at <https://docs.e.samsungsdscloud.com/apireference/>
(**15 categories / 60 services / ~1,372 endpoints**).

The suite is **catalog-driven**: the API Reference is parsed once into a
machine-readable inventory, and tests are generated from that inventory — so
new/changed APIs are picked up by re-running the extractor instead of writing
code per endpoint.

## Layout

```
tools/build_catalog.py        # scrape API Reference -> framework/api_catalog.json
framework/
  config.py                   # env-var settings, per-service host resolution, safety gates
  auth.py                     # pluggable Access Key + HMAC-SHA256 signer
  client.py                   # HTTP client: retries/backoff + mutation safety gate
  catalog.py                  # load/query the API inventory
tests/
  smoke/test_catalog_smoke.py # 1 generated reachability test per endpoint
  crud/lifecycles.json        # declarative create->read->update->delete flows
  crud/test_crud_lifecycle.py # runs the lifecycles (opt-in, gated)
conftest.py  pytest.ini  .env.example
```

## Setup

```bash
pip install -r requirements.txt
cp .env.example .env          # then fill in SCP_BASE_URL + credentials
python tools/build_catalog.py # build/refresh framework/api_catalog.json
```

`build_catalog.py` uses HTTP Range requests (only the page <head> is needed),
retries the gateway's intermittent 503s with backoff, and is **resumable**.

## Endpoints (per-service hosts)

SCP Open API endpoints are **per service**, not one gateway, in two flavours:

```
regional: https://<service>.<region>.<env>.samsungsdscloud.com   e.g. vpc.kr-west1.e...
global  : https://<service>.<env>.samsungsdscloud.com            e.g. product.e...
```

Path roots collide across services (`/v1/clusters` is used by ske, mariadb,
mysql, …), so each call targets its own host. Set `SCP_REGION` (+ `SCP_ENV`,
default `e`) and the suite builds each service's host from the catalog service
name. **Global (account-scoped) services have no region segment** — the built-in
list (`product, pricing, iam, organization, quota, billingplan, budget,
costexplorer, cloudcontrol, resourcemanager, support`) was verified by DNS and
is extendable via `SCP_GLOBAL_SERVICES`. If a service's API subdomain differs
from its catalog name, override it via `SCP_SERVICE_HOSTS` (JSON). `SCP_BASE_URL`
is a last-resort single-host fallback — it must be a concrete URL, not a
wildcard (`*.e.samsungsdscloud.com` is only for network allowlists).

## Running

```bash
# read-only smoke regression across the whole catalog (no resource changes)
pytest tests/smoke -m smoke

# limit scope
pytest tests/smoke -m smoke --category compute --service virtualserver

# CRUD lifecycles (creates/deletes REAL resources — opt in explicitly)
SCP_ALLOW_MUTATIONS=true SCP_ALLOW_DESTRUCTIVE=true pytest tests/crud -m crud
```

Reports are written to `reports/report.html` and `reports/junit.xml`.

## Safety model

This catalog includes destructive operations (`Create*`, `Delete*`, …). To make
sure a regression run never changes real cloud state by accident:

| Operation | Default | Enable with |
|-----------|---------|-------------|
| `GET` (read-only) | runs | always allowed |
| `POST` / `PUT` / `PATCH` | **blocked** | `SCP_ALLOW_MUTATIONS=true` |
| `DELETE` | **blocked** | `SCP_ALLOW_DESTRUCTIVE=true` |

The smoke suite only calls read-only `GET`s without path params. Mutating and
parameterised endpoints are exercised by explicit, ordered CRUD lifecycles.

## CRUD lifecycles

CRUD lifecycles are **declarative** — add an entry to `tests/crud/lifecycles.json`
(no new Python) and the runner drives create → read → delete in order. Per-step
features:

- `capture`: pull a value from a response (`$.vpc.id`, `$.servers[0].id`) into a
  `{placeholder}` for later steps; `{unique}`/`{region}` are seeded automatically.
- `service`: override the host for that step — a chain can span services
  (e.g. `vpc` → `security-group` → `virtualserver`, each its own host).
- `poll`: wait for async provisioning (`{field, until, timeout, interval}`),
  e.g. until a server's `state` is `ACTIVE`; `wait` sleeps before a step.
- `cleanup`: a delete to register for the resource a create made — if the
  lifecycle fails partway, the runner tears everything down in reverse so a
  failed run never leaks a **billable** resource (e.g. an orphaned VM).
- `destructive: true` marks deletes (need `SCP_ALLOW_DESTRUCTIVE`).

Shipped lifecycles (all gated, opt-in). **Light** (run in routine opted-in
CRUD): `resourcemanager-resource-group`, `networking-vpc-subnet`,
`container-scr-registry`, `filestorage-volume`,
`security-certificatemanager-selfsign`, `application-queueservice-queue`,
`networking-security-group`. **Heavy** (real billable VM / K8s / DB, ~20-40min
each; run ONLY via dispatch with `run_heavy=true`):
`compute-virtualserver-full` (vpc → subnet → security-group → keypair →
discover image/server-type → server → poll ACTIVE → reverse teardown),
`container-ske-cluster-nodepool` (K8s cluster + worker node pool), and
`database-mysql-cluster` (MySQL DBaaS cluster on its own vpc/subnet). Enable/
disable per entry via `"enabled"`; validate a single heavy lifecycle with the
dispatch `crud_filter` input (e.g. `database-mysql-cluster`).

Run them only when you mean it:

```bash
SCP_ALLOW_MUTATIONS=true SCP_ALLOW_DESTRUCTIVE=true pytest tests/crud -m crud
```

In CI, set the repo variable **`SCP_RUN_CRUD=true`** to opt a run into CRUD
(otherwise CRUD is skipped); the result (and any teardown) is posted as a PR
comment. `compute-virtualserver-full` creates a real, billable VM — keep it
disabled unless you want that.

## Dashboard

Every CI run renders a self-contained HTML dashboard via
`tools/build_dashboard.py` from the run's real artifacts
(`reports/smoke_status.tsv` + `reports/junit-crud.xml` + `framework/api_catalog.json`)
and the `known_issues.json` baseline. It shows:

- **health** — new regressions (vs known baseline), pass rate, operation
  coverage, known-red count;
- **coverage** — operation `tested/total`, read (GET) and write (CRUD) splits,
  plus the swagger-coverage/ReadyAPI measurement axes we do / don't cover;
- **per-category coverage** with blind-spot flags;
- **failure taxonomy** (ReportPortal-style) + CRUD lifecycle grid;
- **trends** — pass-rate and coverage over time.

Coverage is operation-level (a method+path is "covered" once called at least
once). The generated `index.html` + a cumulative `history.jsonl` (trend data)
are force-pushed to the **`dashboard-data`** branch each run; generated outputs
are git-ignored on the working branch. To publish it: repo **Settings → Pages →
Build and deployment → Deploy from a branch → `dashboard-data` / `(root)`**.
New regressions are anything classified `fail` whose endpoint key is *not* in
`known_issues.json` — add an entry there to mute a tracked backend bug (e.g. the
billingplan `500`) so the dashboard only alarms on genuinely new breakage.

## Authentication

SCP signs Open API calls with **Access Key + HMAC-SHA256**. The exact
signing-string layout and header names are not on the public API Reference
pages (they live in the JS-rendered User Guide), so `framework/auth.py` keeps
the signing string in one overridable method and the header names configurable
via env vars (`SCP_HMAC_*`). Confirm them against a real `200` response; if the
gateway returns `401/403`, adjust `HmacSigner.signing_string` / the header env
vars. `SCP_AUTH_SCHEME=bearer|none` is also supported.

## CI (GitHub Actions)

`.github/workflows/api-regression.yml` runs the read-only smoke regression on a
daily schedule (18:00 UTC / 03:00 KST) on a GitHub-hosted runner, and can also
be run on demand from the **Actions** tab with inputs for category/service and
the mutation safety gates.

Configure once in **Settings → Secrets and variables → Actions**:

| Type | Name | Notes |
|------|------|-------|
| Variable | `SCP_REGION` | e.g. `kr-west1` — builds per-service hosts |
| Variable | `SCP_ENV` | default `e` |
| Secret | `SCP_ACCESS_KEY` | |
| Secret | `SCP_SECRET_KEY` | |
| Secret | `SCP_PROJECT_ID` | optional |
| Variable | `SCP_SERVICE_HOSTS` | optional JSON overrides for odd subdomains |
| Variable | `SCP_HMAC_*`, `SCP_AUTH_SCHEME` | optional — override auth header names/scheme |

Scheduled runs are **read-only** (mutations stay blocked). Mutating CRUD
lifecycles only run via a manual dispatch with `allow_mutations` (and
`allow_destructive` for deletes) checked. Each run uploads `report.html` +
`junit.xml` as an artifact and writes a pass/fail summary to the run page.

> The gateway must be reachable from GitHub-hosted runners. If it is on a
> private network/VPN, change `runs-on: ubuntu-latest` to a `self-hosted`
> runner with network access.
