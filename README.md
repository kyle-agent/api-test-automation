# SCP API Regression Test Platform

Automated testing **platform** for the Samsung Cloud Platform (SCP) Open APIs
documented at <https://docs.e.samsungsdscloud.com/apireference/>
(**13 categories / ~60 services / ~1,372 endpoints**). Engineered by a team of
AI agents (`docs/agent-team.md`); session entry point: `START_HERE.md`; current
state: `docs/working/CONTEXT.md`; doc index: `docs/INDEX.md`.

**Three areas** (full blueprint + phase direction: [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md)):

1. **Control plane — [`controlplane/`](controlplane/README.md)** · FastAPI +
   htmx + SQLite work console on the confirmed IA **`Catalog · Modeling ·
   Testing · Reporting (+ Knowledge)`** (canonical:
   `docs/working/plans/PLATFORM-IA-DIRECTION.md` §확정 IA): dispatch, live
   tracking, mid-run intervention, snapshot restore, authoring, draft-only AI
   seams.
2. **Execution plane — the two-axis engine**, run by GitHub Actions
   (`.github/workflows/api-test.yml`) or the same-host `runner/worker.py`
   (M4 cutover last — `docs/PLATFORM-PLAN.md`). Same `python -m …` entrypoints
   either way.
3. **Knowledge & model — [`knowledge/`](knowledge/README.md)** +
   [`knowledge/formal/`](knowledge/formal/FORMAT.md), including the **M5
   resource-task model** (`knowledge/formal/resources/*.yaml`, 275 nodes /
   60 YAML files = 59 services + `_groups.yaml`) from which
   `regression/scenarios/composer.py` *composes* lifecycles — scenarios are
   generated from the model rather than hand-written.

```
                         core/  (shared kernel)
       config · auth · http_client · catalog · registry · results · budgets
                ▲                 ▲                 ▲            ▲
   spec/ (extract+diff)   regression/ (AXIS 1)  conformance/ (AXIS 2)  cleanup/ (reconciler)
                                  │                 │
                                  └──── results store (reports/results/*.jsonl) ────► dashboard/
```

- **AXIS 1 — `regression/` (does it work?)** read-only smoke + list→show
  read-chains + ordered CRUD scenarios that create/delete real resources,
  recording **pass/fail + response time** and widening coverage
  (coverage definitions: `docs/COVERAGE-CRITERIA.md`; current campaign:
  `docs/working/plans/CAMPAIGN-C3-100.md`).
- **AXIS 2 — `conformance/` (is it well designed/built?)** static spec analysis
  + read-only runtime probes with a pluggable rule lens and a baseline so only
  NEW defects alarm.
- **Supports:** `spec/` extracts/diffs the spec · `dashboard/` visualizes both
  axes from one results store · `cleanup/` guarantees teardown via a tag-based
  reconciler.

## Layout

```
core/         kernel: config·auth·http_client·catalog + registry·results·budgets·suites·profiles·oplog·snapshot·commands·baselines
spec/         extract_catalog · extract_bodies · summary · diff · coverage_gap · read_reachability
regression/   smoke · read_chains · scenarios/ (engine·composer·targets·DAG scheduler — see its README)
conformance/  static · runtime · baseline · rules/
cleanup/      reconciler (tag-ownership sweep)
dashboard/    build (index + drilldowns) · ops.html (live ops — docs/OPS-DASHBOARD.md)
controlplane/ platform server (see controlplane/README.md)
console2/     Testing console frontend, served by the spine (see console2/README.md)
runner/       worker.py — same-host executor (M4)
suites/ environments/  named suites × environment profiles (run = suite × profile)
tests/        thin pytest entrypoints (smoke / crud / offline)
knowledge/    domain knowledge (narrative + formal/ YAML model)
drafts/       AI/composer outputs awaiting review (never auto-enabled)
data/         api_catalog.json · api_bodies.json · api_docs.json · baselines/
docs/         ARCHITECTURE · agent-team · working/{CONTEXT,plans,trackers} — see docs/INDEX.md
reports/      per-run output (gitignored)
```

## Setup & canonical commands

```bash
pip install -r requirements.txt    # engine deps
cp .env.example .env               # SCP_REGION + credentials (never commit .env)
python -m spec.extract_catalog     # build/refresh data/api_catalog.json (resumable)
python -m spec.summary             # live coverage summary (trust this over memory)

# AXIS 1 — read-only smoke across the catalog (no resource changes)
pytest tests/smoke -m smoke        #  --category compute --service virtualserver to scope

# AXIS 1 — CRUD lifecycles (create/delete REAL resources)
pytest tests/crud -m crud          # gates: see Safety model below

# AXIS 2 — conformance
python -m conformance.static
python -m conformance.runtime --probe all
python -m conformance.baseline --init-if-missing

# supports
python -m dashboard.build                                  # render dashboard
SCP_ALLOW_DESTRUCTIVE=true python -m cleanup.reconciler    # reclaim leftovers (tag-scoped)
```

Platform server (from the repo root):
`pip install -r requirements.txt -r controlplane/requirements.txt && uvicorn
controlplane.app:app --host 0.0.0.0 --port 8800` — env vars, command-channel
API and the editing model: [`controlplane/README.md`](controlplane/README.md);
Docker Compose deployment: [`docs/DEPLOY.md`](docs/DEPLOY.md).

## Safety model (contract — canonical wording: `CLAUDE.md` Hard Rules)

Mutations default **ON** — the project's purpose is real execution; the
deliberate opt-in is the run **selection** + the console2 pre-flight confirm,
not an env flag (`core/config.py`):

| Operation | Default | Gate |
|-----------|---------|------|
| `GET` (read-only) | runs | always allowed |
| `POST` / `PUT` / `PATCH` | **allowed** | force read-only: `SCP_ALLOW_MUTATIONS=false` (CI smoke/conformance suites set it explicitly) or profile veto `SCP_PROFILE_FORBID` |
| `DELETE` | **allowed** | disable: `SCP_ALLOW_DESTRUCTIVE=false` or profile veto |
| Heavy/billable lifecycles (VM, K8s, DB) | **skipped** | explicit opt-in: `SCP_RUN_HEAVY=true` or a heavy run selection (console2 auto-derives + confirms) |

Smoke + read-chains only call read-only `GET`s; mutating endpoints are exercised
by explicit, ordered CRUD scenarios.

- **Ownership & cleanup:** every created resource is stamped
  (`core.registry`) with an owner/run/axis/TTL tag and recorded in a per-run
  manifest → deterministic reverse-order teardown; `cleanup.reconciler`
  reclaims orphans **only under our owner tag** (name-prefix is a fallback).
  CI exports `APITEST_RUN_ID` so tags are attributable per run.
- **Quotas:** account caps (5-VPC, private-dns, …) are data (`core.budgets` +
  `regression/scenarios/dependencies.json`); the engine **reserves** before a
  capped create and **skips** (not fails) when exhausted — quota pressure is
  never a false regression.
- Mute a tracked backend bug in `data/baselines/known_issues.json`
  (per-profile siblings: `known_issues.<profile>.json`).

## Scenarios (pointer)

CRUD lifecycles are **declarative data**; composed lifecycles are compiled from
the resource model. Authoring contract (per-step features, light vs heavy,
heavy self-trigger): [`regression/scenarios/README.md`](regression/scenarios/README.md).
Model design: [`docs/RESOURCE-MODEL-PLAN.md`](docs/RESOURCE-MODEL-PLAN.md) ·
format: [`knowledge/formal/FORMAT.md`](knowledge/formal/FORMAT.md) ·
scheduler: [`docs/scheduler-system.md`](docs/scheduler-system.md).

## Endpoints & auth (pointer)

SCP endpoints are **per service** (regional
`https://<service>.<region>.<env>.samsungsdscloud.com`, global services have no
region segment); path roots collide across services, so each call targets its
own host. Auth is **Access Key + HMAC-SHA256** with tunable signing
(`core/auth.py`, `SCP_HMAC_*`). Full details + the global-service list:
`docs/working/CONTEXT.md` § "Endpoints, region & auth" and
`knowledge/domain-model.md`. Overrides: `SCP_SERVICE_HOSTS` (JSON),
`SCP_GLOBAL_SERVICES`, `SCP_BASE_URL` (last resort).

## Dashboards & publishing (pointer)

`python -m dashboard.build` renders the self-contained dashboard (verdict
header, C1/C2/C3 coverage ladder, per-service drilldowns) from the unified
results store. Everything publishes to the **`dashboard-data`** branch / Pages
(enable once: Settings → Pages → Deploy from a branch → `dashboard-data` / root):
`index.html` + history + per-service pages, **`ops.html`** (live ops view over
the persistent oplog bucket — `docs/OPS-DASHBOARD.md`), the console2 static
snapshot (`console2/README.md`), and per-run **snapshots** restorable from the
control plane's Reporting screen.

## How runs are triggered (pointer)

**On-demand only** — `api-test.yml`'s automatic push trigger is owner-DISABLED
(2026-06-18). The three real lanes (chat-heavy request file · local platform
console · manual `workflow_dispatch` fallback) and the conformance gating are
canonical in [`docs/agent-team.md`](docs/agent-team.md) § "Run triggers".
Ordinary pushes/PRs run only the offline gate `validate.yml`. One workflow run
at a time (sweep included) — owner rule.

CI configuration (Settings → Secrets and variables → Actions): variables
`SCP_REGION`, `SCP_ENV`, `SCP_RUN_CRUD`, optional `SCP_SERVICE_HOSTS` /
`SCP_HMAC_*` / `SCP_AUTH_SCHEME`; secrets `SCP_ACCESS_KEY` / `SCP_SECRET_KEY`
(+ optional `SCP_PROJECT_ID`). The gateway must be reachable from the runner
(private network → self-hosted runner).

## Results contract

Per-run signals land in `reports/results/` — `observations.jsonl` (AXIS 1,
with response time) + `findings.jsonl` (AXIS 2). Write through `core.results`
(`record(Observation(...))` / `record_finding(Finding(...))`); schema in
`core/results.py`. The dashboard reads this store first.
