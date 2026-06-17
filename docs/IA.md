# IA.md — two-tier platform information architecture (v2, 2026-06-17)

> **v2 supersedes v1's framing.** v1 treated the FastAPI control plane as "the
> platform" and the static export as a read-only mirror. v2 inverts to the
> owner's model: the **Dashboard is the public main** (coverage + API health, for
> everyone); the **per-service catalog → compose → plan → run → report** platform
> is the **backend** that exists to build that coverage. *Viewing* needs no
> server; only *defining/dispatching* needs the FastAPI control plane.
> (v1's WS1–WS5 cleanup still holds — see "Relationship to v1" below.)

## The two tiers

```
┌─ TIER 1 · PUBLIC MAIN (everyone) ───────────────────────────────┐
│  📊 Dashboard — overall coverage (C1/C2/C3) + API health         │
│     the front door: "how healthy & how covered is the SCP API?"  │
│        └ drilldown: category → per-service health/coverage       │
└────────┬─────────────────────────────────────────────────────────┘
         │  "build/improve this service's coverage" → enter backend
         ▼
┌─ TIER 2 · BACKEND PLATFORM (builders) — Plan · Run · Report ────┐
│  🗂  per-service CATALOG  (the organizing unit)                   │
│        each service's endpoints/resources + that service's status │
│  🧩  COMPOSE — combine services into a lifecycle (M5 composer)    │
│  📋  PLAN → ▶ RUN → ✅ REPORT (verify)                            │
│        results feed back into the Dashboard coverage ↑           │
│   static (catalog/) = READ face · FastAPI control plane = WRITE   │
└──────────────────────────────────────────────────────────────────┘
                🔧 Ops — live resource tree / cleanup verdict (static, reads oplog)
```

**The coverage loop (why the platform exists):** Dashboard shows the gap → a
builder enters the platform → picks services and **composes** them → plan → run →
verify (Report) → coverage rises → reflected back on the Dashboard.

## Runtimes — who needs a server

| Surface | Runtime | Audience |
|---|---|---|
| Dashboard (coverage + health) | **static** (Pages) | everyone |
| per-service Catalog · Plan · Run · Report (**read/browse**) | **static** (Pages) | builders |
| Ops (resource tree / cleanup verdict) | **static** (browser reads oplog) | everyone |
| Author **save** · run **dispatch**/schedule · intervene · single-delete · AI | **FastAPI control plane** (`uvicorn`) | builders, deep-linked from catalog |

The static catalog hands every **write** off to the live control plane via a
clearly-marked deep-link (e.g. edit → `/planning/resources/<id>`). No server →
those actions show as "⚙ control plane required", everything else still browses.

## Canonical homes (single-source — render once, link/embed elsewhere)

| Concern | Canonical owner | Everyone else |
|---|---|---|
| Coverage ladder + API health | **`dashboard/build.py`** → `index.html`, `services/*` (Tier-1 main) | catalog `report.html` **embeds/links** it — NO re-render |
| per-service catalog · dependency graph · compose-focus | **`controlplane/graph_export.py`** → `catalog/` (catalog·plan·run·report) | — |
| Live ops (resource tree · run history · verdict) | **`ops.html`** (static, oplog) | catalog links it |
| Writes / live run control / authoring save | **FastAPI control plane** | catalog deep-links in |

## Top-level navigation

- **Public (static dashboard):** `Dashboard(/) · Services · Ops` — with a
  prominent **"build/improve coverage →"** that enters the per-service Catalog.
- **Platform (catalog/):** `Catalog · Plan · Run · Report` — Plan/Run/Report kept
  as the platform basics; every write affordance carries a **"⚙ control plane"**
  deep-link/badge.

## Per-service catalog (the organizing unit)

Each service owns a catalog: its endpoints/resources + that service's
coverage/health + a **"compose into a plan"** entry. The Dashboard's per-service
drilldown (`services/<svc>`) **links into that service's catalog**. **Composing
across services** (M5 `regression/scenarios/composer.py`) is the platform's
primary interaction — plan = a chosen combination of services' resources.

## Read / Write / Live — the bucket rule (migration spec)

Don't reimplement complex platform screens statically — **classify and route**:

| Bucket | Lives in | In catalog |
|---|---|---|
| **READ** (catalog/model/deps/coverage/run-history/report/validate) | catalog/ static (baked at build) | shown directly |
| **WRITE** (edit-save, dispatch, schedule, delete) | FastAPI control plane | a deep-link button only |
| **LIVE** (live run timeline, intervene, inventory) | FastAPI / ops.html (oplog) | last-known snapshot + "open live" |

Progressive disclosure: the catalog top nav stays the 4 basics; rare/power
features (schedules, compare, snapshot restore, AI triage) live one step in,
under the control plane — not in catalog's primary nav.

## Generators (must stay in repo + the publish job)

| Output | Generator | Notes |
|---|---|---|
| `index.html` + `services/*` (Dashboard) + `ops.html` | `dashboard/build.py` | coverage+health; ops DEP map injected at build (WS3) |
| `catalog/` (catalog·plan·run·report + data) | **`controlplane/graph_export.py`** | the platform static **read face**; edits deep-link to `/planning/resources/<id>` |
| **RETIRE** `platform/*` static export | `controlplane/static_export.py` | duplicate of `catalog/` — drop it |

`poc/scenario-viz/` stays a design reference (not published). All generators must
run in the dashboard publish job (`api-test.yml`) → `dashboard-data` → Pages, so
the static surfaces never go stale vs the model.

## Relationship to v1 (WS1–WS5)

- **Still valid:** WS1 (coverage single-source = dashboard — *more* correct under
  v2), WS3 (ops DEP build-time), WS4 (legacy cleanup), WS5 (docs).
- **Rescope / retire:** WS2 (Plan templates *inside* the FastAPI app) and
  `static_export.py`'s `platform/*` are **superseded by `catalog/`** as the static
  face. The FastAPI control plane shrinks to the **write/live API** behind catalog.

## Migration workstreams (v2) — to be detailed & approved before building

> **Already in place (verified 2026-06-17):** `catalog/` is **already generated and
> published** — `api-test.yml` (~line 1248) runs `python -m controlplane.graph_export
> "$dd/catalog"` into the `dashboard-data` branch each build. `graph_export.py` emits
> `catalog/plan/run/report.html` (+ `catalog.js`/`report.js`/`graph.js`); the catalog
> already links **↑ to the dashboard** (`../index.html` "← 대시보드") and carries the
> **"정의/수정은 control plane"** write deep-link. `report.html` is a **per-run** report
> (step timings from `observations.jsonl`), NOT a coverage re-render — so no
> single-source conflict with the dashboard. The 2-tier structure largely EXISTS;
> the remaining work is **dedup + wiring + emphasis**, not building from scratch.

| WS | Scope |
|----|-------|
| **V1 ✅ (mostly done)** | `graph_export.py` → `catalog/` already published by CI as the platform read face. Remaining: spot-check it renders cleanly on Pages. |
| **V2** | Dashboard = front door: per-service drilldown (`services/<svc>`) links **down into** that service's catalog (catalog already links up; add the downlink). |
| **V3** | Read/Write boundary: confirm every catalog write affordance deep-links to the control plane with a clear badge; graceful read-only when no server. |
| **V4** | Per-service catalog + **compose-across-services** as the primary platform action — make "pick services → compose → plan" the foreground flow in `catalog/plan`. |
| **V5** | **Retire `controlplane/static_export.py` (`platform/*`)** — it duplicates `catalog/`. Stop publishing `platform/`; repoint any links to `catalog/`. (Biggest cleanup.) |
| **V6** | Docs reconcile (this file canonical; `controlplane/README.md`, `PLATFORM-PLAN.md`, `OPS-DASHBOARD.md`). |

**Scope guard:** all work on `claude/zealous-heisenberg-irf3xt`; the live public
Pages root is untouched until merged (only the `preview-reorg/` subdir is published).
