# SCP API Regression Test Platform

Automated testing **platform** for the Samsung Cloud Platform (SCP) Open APIs
documented at <https://docs.e.samsungsdscloud.com/apireference/>
(**15 categories / ~60 services / ~1,372 endpoints**).

What started as a catalog-driven test suite is now a full platform with
**three areas**:

1. **Control plane — [`controlplane/`](controlplane/README.md)** · a FastAPI +
   htmx + SQLite server (Overview → Plan → Run → Report + Knowledge IA) that
   dispatches runs (suite × environment profile), schedules them (cron), tracks
   them live (oplog event ingest + milestone timeline), lets you intervene
   mid-run (abort / skip-scenario / stop-polling via the M2 command channel,
   single-resource delete), restores any past run's dashboard from its
   snapshot, and hosts the AI seams (`ai_pipelines.py`: triage, spec-impact,
   scenario/task drafts, fact extraction — all draft-only, never on the hot path).
2. **Execution plane — the two-axis engine**, today run by GitHub Actions
   (`.github/workflows/api-test.yml`), at deployment cutover (M4) by the
   same-host [`runner/worker.py`](runner/worker.py). Same `python -m …`
   entrypoints either way.
3. **Knowledge & model — [`knowledge/formal/`](knowledge/formal/FORMAT.md)**,
   including the **M5 resource-task model**
   (`knowledge/formal/resources/*.yaml`, 127 nodes) from which
   `regression/scenarios/composer.py` *composes* lifecycles — scenarios are
   increasingly generated from the model rather than hand-written.

The suite remains **catalog-driven** (the API Reference is parsed once into a
machine-readable inventory; tests are generated from it) and organised around
**two axes** on a shared kernel. See [`ARCHITECTURE.md`](ARCHITECTURE.md) for the
full blueprint, [`ROADMAP.md`](ROADMAP.md) for the phase plan,
[`docs/PLATFORM-PLAN.md`](docs/PLATFORM-PLAN.md) for the platform milestones
(M0–M5), and [`agents/`](agents/README.md) for the multi-agent team that does
the engineering.

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
core/         config·auth·http_client·catalog  +  registry·results·budgets·suites·profiles·oplog·snapshot·commands·baselines
spec/         extract_catalog · extract_bodies · summary · diff · coverage_gap
regression/   smoke · read_chains · scenarios/{engine, composer, loader, scenarios.json,
              lifecycles/*.json (per-service fragments + generated__*), dependencies.json}
conformance/  static · runtime · baseline · rules/  (pluggable Rule lens)
cleanup/      reconciler   (tag-ownership sweep; legacy name-prefix fallback)
dashboard/    build (redesigned index + per-service drilldowns) · ops.html (live ops view)
controlplane/ the platform server: app · dispatch · scheduler · authoring · triage ·
              ai_pipelines/ai_routes · resource_routes · snapshots · compare · static_export
runner/       worker.py — same-host executor for the M4 cutover (queue consume → same stages)
suites/       named suites (smoke/full/full-heavy/conformance) — run = suite × profile
environments/ environment profiles (stage/prod × region; credential *references* only)
tests/        thin pytest entrypoints that drive the regression engines
agents/       the multi-agent system: roster · shared context · harness · per-agent prompts
knowledge/    SCP domain knowledge (human-readable, AI-maintained) + formal/ (editable YAML)
              formal/resources/  ← M5 resource-task model (127 nodes; composer input)
drafts/       AI/composer outputs awaiting human review (never auto-enabled)
data/         api_catalog.json · api_bodies.json · api_docs.json · conformance.json
              baselines/known_issues.json · baselines/coverage_waivers.json (per-profile
              suffixed siblings supported: known_issues.<profile>.json)
docs/         plans + session handoff notes (see docs/INDEX.md)
reports/      per-run output (gitignored): results/*.jsonl, registry/*.jsonl, dashboard/
.github/workflows/api-test.yml   one orchestrator (spec → regression → sweep + conformance → dashboard → snapshot)
```

## Setup

```bash
pip install -r requirements.txt    # engine deps
cp .env.example .env               # fill in SCP_REGION + credentials
python -m spec.extract_catalog     # build/refresh data/api_catalog.json (resumable)
python -m spec.summary             # coverage summary of the catalog
```

Requirements are split so each piece installs only what it needs:
`requirements.txt` (engine) · `controlplane/requirements.txt` (platform server —
FastAPI/uvicorn/jinja2/croniter, dependency-light by design) ·
`controlplane/requirements-ai.txt` (optional: `anthropic` for the AI features;
the server runs fully without it, AI sections show 비활성).

## Platform server (control plane)

```bash
pip install -r requirements.txt -r controlplane/requirements.txt
uvicorn controlplane.app:app --host 0.0.0.0 --port 8800   # run from the repo root
```

The UI follows the **Overview → Plan → Run → Report (+ Knowledge)** IA: trigger
suite × profile runs, watch them live, queue abort/skip commands the engine
polls at step boundaries, browse/edit suites · profiles · scenarios · knowledge
(validator-gated saves, local git commits), view the resource inventory, compare
two runs, and restore any past run's dashboard from its snapshot. See
[`controlplane/README.md`](controlplane/README.md) for env vars and the command
channel API, and [`docs/DEPLOY.md`](docs/DEPLOY.md) for the Docker Compose
deployment bundle (M4 server + worker).

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

### Composed scenarios (M5 resource-task model)

Hand-written lifecycles are progressively being replaced by **composed** ones:
`knowledge/formal/resources/*.yaml` defines per-resource tasks (requires graph
incl. `one_of`/`count`/credential prerequisites, validated body templates,
options, capture/ready/delete) — **127 nodes** across 12 categories with
human-readable codes (`nw-vpc-vpc`, groups in `_groups.yaml`).
`regression/scenarios/composer.py` compiles a target set into an ordinary
lifecycle JSON (`gen-<node>` / `bundle-<group>`): dependency closure →
topological order + capture wiring → verify steps → reverse teardown (with the
conflict-retry delete semantics of the hand-written lifecycles). The engine is
unmodified — composed output is just another lifecycle. Live-proven via the
R3 verification waves (`regression/scenarios/lifecycles/generated__*.json`);
see `docs/RESOURCE-MODEL-PLAN.md` §6 for wave results.

> **Self-trigger for heavy runs:** a committed `.github/heavy.txt` (first
> non-comment line = a `crud_filter` expression) lets a push drive which heavy
> lifecycle runs next — used to chain heavy validations one per run. Empty file
> = no heavy self-trigger.

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

## Dashboards & Pages

`python -m dashboard.build` renders a self-contained HTML dashboard
(**redesigned 2026-06 to the owner's mockups**: verdict header, coverage-ladder
cards, per-service drilldown pages). It reads the unified results store
(`reports/results/observations.jsonl` + `findings.jsonl`) first, falling back to
legacy flat files so nothing regresses mid-migration. It shows health (new vs
known regressions, pass rate, the C1/C2/C3 coverage ladder), per-service
drill-down with **status + response time** and **design/behavior defect** columns,
the CRUD grid, and trends.

Everything is published to the **`dashboard-data`** branch each run (enable via
**Settings → Pages → Deploy from a branch → `dashboard-data` / `(root)`**):

- `index.html` + `history.jsonl` + per-service pages — the results dashboard.
- `ops.html` — the **live ops view**: reads the persistent oplog bucket
  directly and renders a dependency-ordered resource tree per run, with run
  pills, filters and a run-finished verdict (watch a run without GitHub).
- `/platform/` — a **static export of the whole platform UI**
  (`python -m controlplane.static_export`): Overview/Plan/Run/Report tabs,
  knowledge and resource-model pages incl. a read-only page per resource node —
  ~198 pages, every nav/menu clickable; server-only actions show a banner.
- Per-run **snapshots** (results JSONL + built dashboard + meta) are archived to
  the oplog bucket under `runs/<run_id>/snapshot/` and restorable from the
  control plane's Report screen.

Mute a tracked backend bug by adding it to `data/baselines/known_issues.json`
so only genuinely new breakage alarms (per-environment baselines: a
profile-suffixed sibling like `known_issues.<profile>.json` takes precedence).

## How runs are triggered (CI · GitHub Actions)

`.github/workflows/api-test.yml` is a single orchestrator:
**spec** (refresh catalog + resolve suite/profile/run-request options) →
**regression** (smoke + read-chains, opt-in CRUD; the adopt-class and serial
VPC-CRUD passes run as parallel A∥B jobs) → **sweep** (`cleanup.reconciler`)
and **conformance** (static + runtime + baseline) → **dashboard** (build +
publish + `/platform/` static export) → **snapshot** (per-run archive).

**Triggers are on-demand only** (live runs are expensive — no cron, no per-push
runs). Three equivalent ways to start a run:

1. **Run-request file**: touch **`.github/run-request`** and push (runs on that
   branch; this is how a chat session starts a run). The file carries `KEY=VALUE`
   options — `suite/profile/mutations/destructive/heavy/conformance/category/
   service/crud_filter` — so every dispatch capability is chat-controllable.
   Owner sequencing rule: never push a run-request commit while a previous run
   (including its sweep) is still in progress.
2. **workflow_dispatch** with the same inputs (named `suite` from `suites/*.yaml`
   and `profile` from `environments/*.yaml` expand to gate defaults; explicit
   inputs override).
3. **The control plane UI** (`controlplane/`) — manual trigger or cron schedule;
   dispatches via the Actions API today, or queues for `runner/worker.py` when
   `PLATFORM_EXECUTOR=worker` (M4).

Ordinary pushes/PRs run only the cheap offline gate `validate.yml`
(scenario + knowledge validation, no credentials).
**Conformance** is further gated: it runs only when the spec actually changed
(catalog refresh diff), on `claude/run-conformance`/`run-schema-live` pushes,
with dispatch `run_conformance=true`, or repo var `SCP_RUN_CONFORMANCE=true` —
when skipped, the dashboard reuses the last committed conformance data (the two
jobs write disjoint files on `dashboard-data`, nothing is clobbered).

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
