# SCP API Regression Test Platform — Architecture

The platform is split into a **control plane** (manage/observe/intervene) and an
**execution plane** (the deterministic test engine). The execution plane is the
original two-**axis** design: two axes + three **supporting concerns**, all
sitting on one shared **kernel**.

## Control plane / execution plane

```
┌────────────────────────── Control Plane (controlplane/) ─────────────────────┐
│  FastAPI + htmx + SQLite — Overview · Plan · Run · Report (+ Knowledge) IA    │
│  ├─ dispatch: suite × environment profile → Actions workflow_dispatch         │
│  │            (or the worker queue when PLATFORM_EXECUTOR=worker, M4)         │
│  ├─ scheduler: cron(UTC) × suite × profile (30s polling daemon)               │
│  ├─ live tracking: oplog event ingest → run state + milestone timeline        │
│  ├─ intervention (M2): command channel (abort/skip/stop-polling — the engine  │
│  │            polls at step boundaries), resource inventory + single delete   │
│  ├─ authoring (M3): validate→write→local-git-commit pipeline for suites,      │
│  │            profiles, scenarios, knowledge (+ quota simulation warnings)    │
│  ├─ reporting: run history, per-run snapshot restore, run-A-vs-B compare      │
│  ├─ AI seams (ai_pipelines.py): triage (B1), summaries (B2), spec-impact (A1),│
│  │            scenario/task drafts (A2), fact extraction (A3) — draft-only    │
│  └─ static_export.py → Pages /platform/ (~199 read-only pages, all clickable) │
└──────────────┬───────────────────────────────────────────────┬───────────────┘
               │ dispatch / commands                 heartbeat / │ results
┌──────────────▼────────────── Execution Plane ──────────────────▼──────────────┐
│  today: GitHub Actions (.github/workflows/api-test.yml)                       │
│  M4 cutover: runner/worker.py on the same host (same stages, same code)       │
│  spec → regression (smoke · read-chains · CRUD, A∥B) → sweep → conformance    │
│  → dashboard/publish → per-run snapshot                                       │
└────────────────────────────────────────────────────────────────────────────────┘
```

The control plane **wraps the engine, never replaces it** — every stage is the
same `python -m …` CLI either way, and the regression hot path stays
deterministic (AI sits at authoring time and post-run only).

## The execution plane — two axes on one kernel

```
                         ┌──────────────────────────────────────────┐
   spec/  (support A) ─► │  core/  (kernel — the shared contracts)   │ ◄─ everything depends only on core
   extract + diff        │  config · auth · http_client · catalog    │
                         │  registry · results · budgets             │
                         └───────┬───────────────────────┬──────────┘
                                 │                        │
                  ┌──────────────▼─────────┐   ┌──────────▼───────────────┐
   AXIS 1         │  regression/           │   │  conformance/   AXIS 2   │
   "does it work" │  smoke · read_chains   │   │  static · runtime · rules│  "is it well designed/built"
   pass/fail+時間 │  scenarios(engine+data)│   │  baseline                │
                  └──────────┬─────────────┘   └──────────┬───────────────┘
                             │  observations               │  findings
                             ▼                             ▼
                         ┌───────────────  results store  ───────────────┐
                         │       reports/results/*.jsonl (one schema)     │
                         └───────────────────────┬───────────────────────┘
                                                 ▼
   cleanup/ (support C) ◄─ registry ─►   dashboard/ (support B)  ─► dashboard-data branch / Pages
   reconciler (tag-scoped)              build (reads results store)
```

## The two axes

1. **`regression/` — does the API work?** Call endpoints (read-only smoke, list→show
   read-chains, and ordered CRUD scenarios that create/delete real resources),
   record pass/fail **+ response time**, and keep widening coverage. Needs careful
   *ordering & scenarios* — service prerequisites and account quotas (e.g. 5-VPC
   cap) are modelled as **data** (`scenarios/dependencies.json`, `core/budgets`),
   not baked into code, so a scheduler can serialize/parallelize safely.

2. **`conformance/` — is the API well designed & implemented?** Find design/impl
   defects via (a) **static** analysis of the spec and (b) **runtime** probes
   (read-only / empty-body) that never create resources. Defects are emitted as
   **findings** against a baseline so only NEW defects alarm. The "lens" is
   extensible via pluggable **`rules/`**.

## The M5 composer layer (resource model → scenarios)

Axis-1 scenarios are no longer only hand-written. The **resource-task model**
(`knowledge/formal/resources/*.yaml` — 128 nodes, readable codes
`<cat>-<group>-<resource>` such as `nw-vpc-vpc`, groups in `_groups.yaml`)
declares per resource: its dependency requirements (`requires`, incl. `one_of`
branches, `count` multiplicity and console-issued `credential` prerequisites),
a **validated create-body template**, options, capture paths, readiness polling
and delete. `regression/scenarios/composer.py` is a *compiler* from that model
to ordinary lifecycle JSON:

```
resources/*.yaml ──load_model()──► dependency closure ──► topological order
   + capture wiring ──► verify steps ──► reverse teardown (conflict-retry
   deletes incl. per-node retry_on_status passthrough, delete bodies /
   PUT-style teardown, filter-object captures, step headers, lookup nodes,
   credential surfacing) ──► gen-<node> / bundle-<group> lifecycle JSON ──► engine
```

The engine is **unmodified** — composed lifecycles run, sweep and report like
hand-written ones, and must pass the scenarios validator (R1 checks live in
`knowledge/formal/validate.py`). Provenance discipline: reverse-extracted or
live-proven nodes are `VALIDATED`; docs-derived ones stay `docs` until a scoped
live run passes. Full design: `docs/RESOURCE-MODEL-PLAN.md`.

## The three supports

- **A · `spec/` — extract & track.** Pull the API spec from the docs into the
  catalog + request bodies, and **diff** versions. A spec change is an input to
  *both* axes and a trigger to (re)evaluate only the affected endpoints.
- **B · `dashboard/` — visualize both axes.** One self-contained HTML built from
  the unified results store: the C1/C2/C3 coverage ladder, pass/fail + response
  time (axis 1), design/behavior findings (axis 2), CRUD grid, trends — with
  redesigned index + per-service drilldown pages (owner mockups, 2026-06).
  Published to the `dashboard-data` branch / Pages alongside **`ops.html`** (the
  live ops view, below) and the **`/platform/`** static export of the control
  plane UI.

### Observability: oplog, ops view, snapshots

- **`core/oplog.py`** writes every milestone and resource event of a run to a
  **persistent S3-compatible bucket** (`apitest-oplog-permanent`), and — when
  `APITEST_PLATFORM_URL` is set — mirrors each event to the control plane's
  `/api/ingest/events` (fire-and-forget; a dead platform never blocks a run).
- **`dashboard/ops.html`** is a static viewer over that bucket: a
  **dependency-ordered live resource view** per run (resources attach under the
  node they depend on; the kind-level dependency map is generated from the
  resource model by `dashboard/gen_dep_map.py`), in-flight-only run pills with
  history-row selection, filters (incl. an `oplog-test-*` dev-prefix guard), a
  run-finished **cleanup-integrity verdict** (testing / leaked /
  cleanup-failed / deleted), paginated S3 listing and KST timestamps — works
  on Pages with no server and no GitHub access.
- **`core/snapshot.py`** archives every run's results JSONL + built dashboard +
  `meta.json` (suite, profile, catalog sha256) under `runs/<run_id>/snapshot/`
  on the same bucket; the control plane proxies it to restore any past run's
  dashboard, and `/reporting/compare` diffs two snapshots.
- **C · `cleanup/` — guarantee teardown.** A **registry-driven reconciler**:
  every created resource is *tagged* with an owner+run+ttl, and cleanup deletes
  by tag (not by name-prefix guessing). Prefix matching remains a fallback only.

## The kernel (`core/`) — shared contracts

Everything depends only on `core`; the axes never import each other.

| module | responsibility |
|---|---|
| `config` | env-driven settings, per-service host resolution, safety gates |
| `auth` | HMAC request signing |
| `http_client` | signed HTTP with retries + the `Response` (incl. `elapsed_ms`) |
| `catalog` | spec model (`Endpoint`) + loader + queries + **diff** |
| **`registry`** | tag every created resource `(owner, run_id, axis, ttl)`; per-run manifest for ordered teardown; `is_owned()` predicate for the reconciler |
| **`results`** | one **observation** (regression/probe) + **finding** (conformance) schema; JSONL writers + readers the dashboard consumes |
| **`budgets`** | account limits (vpc=5, private-dns, …); reserve/release so the scenario scheduler respects quotas |

## Interference & isolation (first-class — this is what bit us before)

The account is shared across runs and both axes. The cross-run sweep deleting live
resources, VPC-quota saturation, and cancelled runs were all *interference*. The
structure removes it at the root:

- **Run namespace + owner tag** on every created resource → the reconciler deletes
  *only its own* tag. No cross-run nuking.
- **Budget reservation** shared by axis-1 scenarios and any axis-2 active probe →
  neither starves the other of VPCs.
- **Conformance probes are read-only by default**; when a probe must create, it
  uses the same `registry` + `budgets`.
- **Concurrency groups per axis/run** (not one global group).
- **Spec-diff incremental runs** → re-test only changed endpoints → smaller blast
  radius + lower cost.

## Layout (current)

```
core/        config auth http_client catalog registry results budgets
             suites profiles oplog snapshot commands baselines
spec/        extract_catalog extract_bodies diff coverage_gap
regression/  smoke read_chains   scenarios/{engine,composer,loader,
             scenarios.json,lifecycles/*.json,dependencies.json}
conformance/ static runtime baseline   rules/
cleanup/     reconciler
dashboard/   build (index + per-service drilldowns)  ops.html (live ops view)
controlplane/ app dispatch scheduler authoring triage ai_pipelines/ai_routes
             resource_routes snapshots compare static_export  templates/
runner/      worker.py (M4 same-host executor)
suites/      environments/   named suites · environment profiles (data)
knowledge/   formal/{services,cross-service,flows,combo-scenarios,resources/}
drafts/      composer/AI outputs awaiting review
tests/       thin pytest entrypoints that drive the regression/conformance engines
data/        spec/{catalog,bodies,docs}  baselines/{known_issues,coverage_waivers,
             conformance_baseline}(+ per-profile suffixed siblings)
reports/     per-run output (gitignored): results/*.jsonl, registry/*.jsonl, dashboard/
.github/workflows/  one orchestrator (api-test.yml) + offline gate (validate.yml)
```

## Migration (done — kept for history)

`core/` started as a **facade**: it re-exported the existing kernel
(`framework/*`) and added the new contracts (`registry`, `results`, `budgets`)
additively — zero breakage. Each domain package was then ported behind the
facade, followed by the physical move of `framework/*` and `data/*`. The
registry reconciler is proven and the layout above is the live one; the platform
milestones that followed (M0–M5) are tracked in `docs/PLATFORM-PLAN.md`.
