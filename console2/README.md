# console2 — local execution console (UI skeleton)

A single-page console that wires the whole loop: **선택 → Plan → 실행 → 라이브 리포트**.

> **RETIRED as a standalone app (convergence S4).** The console is no longer run via
> its own `tools/console2_server.py` server — it has been **absorbed into the
> control-plane spine**: `controlplane/console_api.py` answers the same `/api/*`
> contract by delegating to `console2_server`'s (now library-only) functions, and this
> frontend is served + embedded under controlplane **Testing**.

## Run it (via the spine)

```bash
uvicorn controlplane.app:app --host 0.0.0.0 --port 8800
#  → Testing (this console, embedded):  http://localhost:8800/testing/embed
```

The `index.html` + `assets/*` here are the SAME files the spine serves (mounted at
`/testing/console`). The console fetches `/api/model`, loads the shared graph renderer
(`assets/resource_graph.js`), and renders.

## Concept model (vocabulary used throughout the UI)

- Hierarchy: **category → service → resource → api**. Selecting a category/service/
  resource pulls in its dependency **closure** (can cross service/category boundaries)
  and auto-orders it (longest-path depth = creation order).
- **Execution unit = lifecycle** — the engine runs these; many resources map to one
  `source.lifecycle`. **Reporting unit = api** — each lifecycle step is one API call;
  an api is never selected/executed alone, only observed.
- A run = **Scope (selection) × Axis**. Axis ∈ {smoke, regression-light, regression-heavy,
  conformance} and is **per-run, not per-resource**.
- ~20 resources have no lifecycle (pure deps / lookups). They show **dimmed and
  non-selectable**; they only appear when pulled in as a dependency.

## The four stages

1. **선택 (Select)** — layered-DAG canvas (depth = creation order, category swimlanes).
   Left: a `category → service → resource` tree with per-level "전체 선택". Targets vs
   pulled-in deps are visually distinct; dep-only resources are dimmed. Right: selection
   summary (대상 / 폐포 / 서비스 / heavy / quota peaks). Click a node to toggle it as a target.
2. **Plan** — `POST /api/plan` → the resource DAG (생성 순서) + the **dag_planner 웨이브**
   list (`provision → free → adopt → self-create`, each wave's concurrency + `vpc_slots`,
   plus `peak_vpcs`). Right: per-lifecycle **API step preview**. Shows `skipped_disabled`.
3. **실행 (Run)** — an **Axis** selector (regression-light/heavy run for this build;
   smoke/conformance are present but disabled, "다음 빌드") + a **mode** toggle
   (`simulate | live`, default simulate). `live` requires a confirm dialog surfacing the
   gates. `POST /api/run`, then poll `GET /api/runs/<id>/events`.
4. **리포트 (Report)** — 4 tabs, all driven by the event stream:
   - **R1 진행** — the DAG/wave graph, lifecycle nodes colored by live state
     (queued/running/done/fail) + wave-by-wave progress.
   - **R2 리소스** — per-resource rows from `resource-tracked`/`resource-deleted`
     (type · **resource_id** · 생성/테스트/삭제). In simulate these are synthetic `sim-…` ids.
   - **R3 API** — api-first table of `step-start`/`step-end` (method+path, 결과 badge,
     status, 응답시간), grouped by lifecycle.
   - **R4 로그** — the raw run log (`GET /api/runs/<id>`) + 🧹 강제 클린업 / 🔍 클린업 확인.
   A run-records list (`/api/runs`) keeps past runs reopenable.

## Files

| file | role |
|------|------|
| `index.html` | shell: 4-stage tabs, global context bar, column scaffolding |
| `assets/console2.js` | the app: model fetch, all 4 stages, live event polling |
| `assets/console2.css` | dark theme (adapted from the PoC layered-DAG canvas) |
| `assets/resource_graph.js` | shared graph renderer (scene controller: group/collapse/focus/zoom) — the SAME engine the spine serves at `/static/resource_graph.js` |

## simulate vs live

- **simulate** (default) replays the real `dag_planner` plan deterministically — **no
  cloud calls**. Resource ids are clearly synthetic (`sim-…`). Use it to confirm
  ordering (wave-start → lifecycle-start → step-start/step-end → resource-tracked →
  lifecycle-end, in DAG order) before any real run.
- **live** runs `pytest tests/crud` with the per-run safety gates from the chosen Axis
  (mutations / destructive / heavy). The gates are explicit opt-ins, never set to "make
  a test pass".

## Run records & offline tests

- Run records persist under `reports/console2-runs/` (log + events per run);
  the server **rehydrates** past runs from there on startup and mirrors
  finished local runs into the controlplane runs DB (`local-<rec id>` ids), so
  they appear in Reporting ▸ 실행 기록 and `/runs/{id}`.
- Offline test coverage for the run-observability behaviors (run-bound graph,
  now-playing bar, late-resource rescan, fail-closure on lifecycle end):
  `pytest tests/offline/test_console2_run_observability.py`.

## Static demo snapshot (backend-free, for GitHub Pages)

`build_static.py` assembles a **self-contained snapshot of the REAL app** so the
actual UI (not the design mockups) can be viewed on Pages with **no backend**:

```bash
python console2/build_static.py        # writes reports/console2-static/ (gitignored)
```

It imports the pure builder functions from `tools/console2_server.py` (no running
server, no creds, no cloud) and **bakes** the data: the full `/api/model`, a
`composer.graph_view` DAG per single service (+ a few multi-service / node-id
examples), and one hermetic SIMULATE run (record + events + log) so all four report
tabs are populated. It then copies `index.html` + `assets/*` **verbatim** and adds
two small files — `data/static-data.js` (`window.__C2_STATIC__`) and
`assets/mock-api.js` (a `window.fetch` monkeypatch that answers `/api/*` from the
bake **before** `console2.js` loads). The production front-end therefore runs
**completely unchanged**; only the injected copy of `index.html` carries the shim
`<script>`s + a DEMO banner. In the snapshot, simulate AND live both surface the
same pre-baked run (there is no real execution).

Published to the Pages branch (`dashboard-data:/console2/app/`):
**https://kyle-agent.github.io/api-test-automation/console2/app/**

The real app (`index.html`, `assets/*`) is never modified by the build.
