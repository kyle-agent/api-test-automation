# API Test Automation — Architecture (restructure)

Two **axes** + three **supporting concerns**, all sitting on one shared **kernel**.

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

## The three supports

- **A · `spec/` — extract & track.** Pull the API spec from the docs into the
  catalog + request bodies, and **diff** versions. A spec change is an input to
  *both* axes and a trigger to (re)evaluate only the affected endpoints.
- **B · `dashboard/` — visualize both axes.** One self-contained HTML built from
  the unified results store: coverage %, pass/fail + response time (axis 1),
  design/behavior findings (axis 2), CRUD grid, trends.
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

## Target layout

```
core/        config auth http_client catalog registry results budgets
spec/        extract_catalog extract_bodies diff
regression/  smoke read_chains   scenarios/{engine,scenarios.json,dependencies.json}
conformance/ static runtime baseline   rules/
cleanup/     reconciler
dashboard/   build  templates/
tests/       thin pytest entrypoints that drive the regression/conformance engines
data/        spec/{catalog,bodies,docs}  baselines/{known_issues,conformance_baseline}
reports/     per-run output (gitignored): results/*.jsonl, registry/*.jsonl, dashboard/
.github/workflows/  one orchestrator + reusable jobs
```

## Migration (incremental; keep the tree working at every step)

`core/` starts as a **facade**: it re-exports the existing kernel (`framework/*`)
and adds the new contracts (`registry`, `results`, `budgets`) additively — zero
breakage. Each domain package is then ported behind the facade, one PR at a time,
followed by the physical move of `framework/*` and `data/*`. The legacy
prefix-sweep stays until the registry reconciler is proven.
