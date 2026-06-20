---
status: active
for: all
---

# IA.md — one-graph / step-overlay console (v3, 2026-06-17)

> **v3 adopts the Claude-design directive** (preview-v2 IA 수정 지시서). Supersedes
> v2's "separate dashboard + per-service static pages" framing. The canonical IA is
> the POC **`poc/scenario-viz/06-ia-walkthrough.html`** model: **one resource-task
> DAG, rendered by one shared component (`viz.js`), with a per-step color OVERLAY** —
> the same graph carries Knowledge → Overview → Plan → Run → Report (feedback loop ↺ =
> model-based testing). One-line: *declarative composition over a resource DAG.*

## Why this supersedes v2

v2 polished the stacked dashboard + built separate per-service catalog pages +
separate plan/run/report pages. The design review's verdict: that is **not the
graph spine** — Overview is a card pile, and the step pages each draw their own
thing. v3 unifies everything onto **one graph + overlays**.

## The model (target)

```
Knowledge ─▶ Overview ─▶ Plan ─▶ Run ─▶ Report ─┐   (one DAG, overlay swaps per tab)
   정의        건강도      합성     실행     결과·커버리지
   ▲                                              │
   └──────────────── ↺ feedback loop ─────────────┘   (model-based testing)
```

| Tab | Overlay on the shared DAG | Data source |
|-----|---------------------------|-------------|
| **Knowledge** | resource/dep definitions (requires·options), validated-facts, formal YAML | `knowledge/` + per-node facts |
| **Overview** | node color = C3 coverage / regression health; regression nodes highlighted → click → Report·Triage | results store (observations) overlaid on nodes |
| **Plan** | target select → closure → composed plan (create→ready→verify→reverse delete), dedup, peak quota; one_of branch toggle | `viz.js plan()` (mirrors composer) |
| **Run** | level-parallel live status coloring + gantt | ops live (oplog) + `viz.js` schedule |
| **Report** | pass/fail · coverage · timing overlay + regression/Triage/trends | results store + critical-path timing |

## Shared engine + data bridge (already exist)

- **`poc/scenario-viz/assets/viz.js`** — pure client-side graph engine: `closure`,
  `topoOrder`, `layout`, `renderGraph`, `plan` (closure→order→steps→dedup→quota),
  `levels`, `dur`. Mirrors `regression/scenarios/composer.py`. Tabs pass only
  `colorOf/strokeOf/badgeOf` overlay fns — node coordinates never move.
- **`poc/scenario-viz/build_data.py`** — emits `window.MODEL` (model.js) from
  `knowledge/formal/resources/*.yaml` (same source as composer). Refresh = re-run it
  (currently 270 nodes → rebuild to 275).
- **NEW data the overlays need** (build-time, alongside model.js):
  - `results.js` (`window.RESULTS`) — per-node/endpoint coverage + regression status
    from `reports/results/observations.jsonl` (sample via `tools/sample_data.py`, real
    from runs). Drives Overview/Report node color.
  - `context.js` (`window.CTX`) — env × suite × snapshot sha · time · LIVE/SNAPSHOT.
  - Knowledge facts per node (validated-facts / formal YAML) for the Knowledge tab.

## Global shell (every page)

- **One tab bar**: `Knowledge · Overview · Plan · Run · Report` + feedback ↺.
- **Context bar**: env × suite × snapshot sha · time · **LIVE/SNAPSHOT badge** → every
  tab points at the same scope (kills the cross-screen number mismatch).
- **Glossary + tooltips**: C1/C2/C3 · cov_op · soft · known-red · waiver standardized;
  one label per concept ("신규 회귀" = "신규 fail" → pick one). Static/live badge;
  non-functional buttons disabled + tooltip ("control plane 전용"), never hidden.

## Retire

- **`platform/*`** (control-plane static mirror) — absorb its unique bits (context bar,
  Report depth: Coverage·Conformance·Triage·Trends·A/B; Knowledge data; schedule/cleanup)
  into the unified tabs, then **301-redirect** `platform/*` → the matching tab. Zero
  duplicate Plan/Run/Report.
- The v2 **per-service static pages** (`catalog/services/*.html`) + the stacked
  **`index.html`** dashboard are **superseded** by the unified app (Catalog/Overview
  tabs). Keep as redirect targets during migration.

## Migration workstreams (Claude-design S1–S6, priority order)

| WS | Scope | Acceptance |
|----|-------|-----------|
| **S1** | Global shell + context bar; 5-tab scaffold over `viz.js` (Catalog/Plan/Run/Report from the POC + Overview/Knowledge) | 5 tabs 1-click; active tab; same sha/env badge on every page |
| **S3** | Plan/Run/Report = the one shared `viz.js` component, overlay arg only | 4+ tabs share graph coordinates; only overlay changes |
| **S2** | `index.html` → **Overview overlay** (node color = C3/regression; "새 회귀 N건" → graph highlight → Report·Triage); old tables → collapsed side panel | Overview uses the same component as 06 |
| **S4** | Absorb + 301-redirect `platform/*`; zero duplicate Plan/Run/Report | platform path → unified tab |
| **S5** | **Knowledge** 1급 tab (constraints·validated-facts·formal YAML; node click → defs/facts) | Knowledge is Plan's upstream, graph-linked |
| **S6** | Glossary + tooltips; unified terms; static/live badges; disabled-not-hidden | glossary consistent on every page |

## Build / publish

The unified app is **static** (Pages, no server) — `viz.js` + `style.css` + generated
`model.js`/`results.js`/`context.js`. Edits/dispatch deep-link to the FastAPI control
plane (read/write split unchanged). Generator: evolve `build_data.py` (+ a results/context
emitter) and the `06-ia-walkthrough` shell into the product app; publish under the
canonical Pages path; wire `dashboard-data` publish job.

**Verification checklist (Claude-design §6):** one `viz.js` renders all tabs (coords
fixed, overlay swaps) · `platform/*` redirected, 0 duplicate Plan/Run/Report · context
bar same sha/env everywhere · Overview is a graph overlay not a card pile · regression
list → graph highlight + Triage link · glossary/tooltips consistent.

## Status (2026-06-17)

- **Done & still valid:** `platform/*` static export retired (v2-V5); coverage
  single-source = results store; `tools/sample_data.py` (populated preview data).
- **Superseded:** v2-V2/V3/V4 per-service pages + stacked dashboard → replaced by the
  unified one-graph app (this v3).
- **Next:** S1 (shell + 5-tab scaffold over viz.js, real model + sample overlays) → preview.

**Scope guard:** all work on `claude/zealous-heisenberg-irf3xt`; public Pages root
untouched until merged (only `preview-v2/` subdir is published).

## Read-plane ↔ write-plane hand-off (Run/Edit/Dispatch)

The static console is **read-only by design**: it visualises the model + results,
and its Catalog/Plan/Run/Report buttons hand off to the FastAPI **control plane**
(the write/dispatch plane) where the real action — and the safety gates — live.
Two stages:

### A — deep-link hand-off (DONE, 2026-06-17)

The console resolves a control-plane base URL `CP` (`?cp=<url>` → `localStorage`
→ default `http://localhost:8000`; header **🧩 컨트롤플레인 ↗** + **⚙** to open/change),
then deep-links each action there as a plain GET:

| Console button | → control plane |
|---|---|
| Catalog **편집(쓰기)** | `GET /planning/resources/<id>` |
| Catalog **+ 새 노드 생성** | `GET /planning/resources/_new?service=<cat/svc>` |
| Plan **dispatch ↗** / Run **실제 실행 ↗** / Report **재실행 ↗** | `GET /testing?suite=full&service=<cat/svc>` (run console, trigger form **prefilled**) |

The actual run is a **server-side POST `/runs/trigger` → `dispatch.dispatch_run`**,
so `SCP_ALLOW_*` / dispatch config are never bypassed by a static page. `/testing`
GET accepts `suite`/`service`/`profile`/`crud_filter` query hints to prefill the
form (carries the picked scope across the hand-off). Forward-compatible with B:
same buttons, the base URL just points at a local install instead of a remote.

### B — packaged local install / live console (ROADMAP, not started)

Bundle the control plane so it installs locally (e.g. `pipx`/desktop) and the
console runs **against it directly** — buttons call the dispatch API + poll the
run id so the **Run tab streams real progress** (replacing the schedule
simulation) and **Report shows live run history**. Requires a runtime (not just
Pages), auth for the dispatch token, and explicit in-UI safety-gate surfacing.
Until then, A is the contract: static read + control-plane write, joined by `CP`.
