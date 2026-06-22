# console2 — local execution console (UI skeleton)

A single-page console that wires the whole loop: **선택 → Plan → 실행 → 라이브 리포트**,
served by `tools/console2_server.py`. Pure client-side (one `/api/model` fetch on load)
until you start a run.

## Run it

```bash
python tools/console2_server.py          # http://127.0.0.1:9100/
PORT=9123 python tools/console2_server.py # override the port
```

Then open the URL. The console fetches `/api/model`, sets `window.MODEL`, loads the
shared graph engine (`assets/viz.js`), and renders.

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
| `assets/viz.js` | shared graph engine (copied from `poc/scenario-viz/assets/`) — closure/depths/layout |

## simulate vs live

- **simulate** (default) replays the real `dag_planner` plan deterministically — **no
  cloud calls**. Resource ids are clearly synthetic (`sim-…`). Use it to confirm
  ordering (wave-start → lifecycle-start → step-start/step-end → resource-tracked →
  lifecycle-end, in DAG order) before any real run.
- **live** runs `pytest tests/crud` with the per-run safety gates from the chosen Axis
  (mutations / destructive / heavy). The gates are explicit opt-ins, never set to "make
  a test pass".
