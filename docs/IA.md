# IA.md — platform surfaces & canonical homes (2026-06-17 reorg)

> The flow **catalog → model → compose → validate → run → results → ops** is now
> stable. This file is the **canonical information architecture**: which surface
> owns what, and the single-source rule that kills the 3×-duplicated coverage view.
> Supersedes the ad-hoc screen sprawl accumulated stage-by-stage.

## Two runtimes (clean split)

| Runtime | What | Needs |
|---|---|---|
| **Published static** (GitHub Pages, `dashboard-data` branch) | read-only **Results + Ops** — what everyone sees | nothing (static) |
| **Live control plane** (`controlplane/`, FastAPI+SQLite+htmx) | interactive **Plan + Run + intervene + author** | `uvicorn` server |

## Canonical homes (single-source rule — render once, link/embed elsewhere)

| Concern | Canonical owner | Everyone else |
|---|---|---|
| Coverage ladder (C1/C2/C3) + conformance defects | **static dashboard** (`dashboard/build.py` → `index.html`, `services/*`) | platform **embeds** it via `/dashboard/*` proxy — NO re-render |
| Live ops (resource tree · run history · cleanup verdict) | **`ops.html`** (static, reads oplog bucket) | platform links it |
| Authoring (Catalog→Model→Compose→Validate) | **platform → Plan** (single linear flow) | — |
| Run control (trigger/schedule/live/intervene/compare/triage) | **platform → Run + Report(run-centric)** | — |
| Knowledge browse | **platform `/knowledge`** (one route) | — |

## Top-level nav (after reorg)

**Static dashboard (Pages):** `Results · Services · Ops`  (+ "Platform" link if a live server is configured)

**Live platform:** `Plan · Run · Report · Knowledge`
- **Report** = run-centric only (run list / detail / compare / triage / archive) **+ embedded canonical dashboard** for coverage/conformance. The old `coverage`/`conformance`/`trends` re-render tabs are removed.

## Plan = one linear flow (replaces the 4-headed authoring sprawl)

Stepper: **① Catalog → ② Model → ③ Compose → ④ Validate**

1. **Catalog** — endpoint inventory + coverage status. Numbers **link to** the canonical dashboard (no re-render here).
2. **Model** — resource-model nodes (`resources.html`/`resource_form.html`); the dependency graph is folded in here (retire the standalone `resource_graph` "demo" page).
3. **Compose** — composer preview (`resource_compose.html`) → the composed-lifecycle list (folds in today's `/planning/scenarios`).
4. **Validate** — **NEW panel**: surface `python -m regression.scenarios.validate` (clean / errors). This stage had **no UI** before.

AI drafts (`/ai/*`) become an **inline assist** inside Model/Compose, not a top-nav section.

## Retire (legacy / dead / duplicate)

- platform `/reporting` `coverage`+`conformance`+`trends` re-render → **embed** the canonical dashboard instead.
- `build.py` legacy readers (`smoke_status.tsv`, `param_status.tsv`, `data/conformance.json`, `junit-crud.xml`) — the unified `reports/results/*.jsonl` store is authoritative. Stop publishing `smoke_status.tsv`.
- `gen_dep_map.py` manual copy-paste of the `DEP` const → **generate it at dashboard build time** so `ops.html` can't go stale.
- duplicate knowledge route (`/knowledge` ≡ `/planning/knowledge`) → keep one.
- vestigial `/runs` → `/reporting` redirect; composer "미탑재 degrade" stubs (composer is present now).
- `base.html`: two CSS layers (legacy + token) → one token set; decorative header `환경`/`스위트` selects → bound on Run only (or removed).
- `static_export.py` `PAGES`: align to the new IA; drop the per-file `view/*` fan-out (keep per-node `resource__*` pages) to shrink the ~199-page export.

## State stores (consolidate / document)

Five overlapping stores today (`history.jsonl`, `verified_endpoints.json`, `endpoint_status.json`, `verified_endpoints_evidence.json`, oplog `index.json`). Target:
- **Run history**: oplog `index.json` (published) + platform DB (live) — keep both, they serve different runtimes; document the merge in `controlplane/dashdata`.
- **Trends**: `history.jsonl` (canonical).
- **Cumulative endpoint state**: `verified_endpoints.json` + `endpoint_status.json` (merge state) — keep; fold `…_evidence.json` into one documented schema.

## Execution workstreams (commit per WS, offline-test gated)

| WS | Status | Scope |
|----|--------|-------|
| **WS1** | ✅ done | Results canonicalization — platform Report embeds dashboard; remove `build.py` legacy readers + stop publishing `smoke_status.tsv` |
| **WS2** | ✅ done | Plan linear flow — Catalog→Model→Compose→**Validate**; retire `resource_graph` demo + dup knowledge route; demote `/ai/*` to inline assist |
| **WS3** | ✅ done | Ops — generate `DEP` map at build time (retire `gen_dep_map.py` manual paste) |
| **WS4** | ✅ done | Shell/legacy — `base.html` CSS unify + nav update; remove `/runs` redirect + composer stubs; `static_export` PAGES align + view-fan-out drop |
| **WS5** | 🔄 in progress | Docs — this file + `controlplane/README.md`, `docs/OPS-DASHBOARD.md`, `docs/PLATFORM-PLAN.md` nav/IA sections; register in `docs/INDEX.md` |

**Verify each WS** (offline, no network/creds):
`PYTHONPATH=. python3 controlplane/tests_offline.py` · `…/tests_ai_offline.py` ·
`runner/tests_offline.py` · `python -m dashboard.build` (offline render) ·
`python -m controlplane.static_export --out /tmp/pe` · `python -m regression.scenarios.validate`.

**Scope guard:** all work on `claude/zealous-heisenberg-irf3xt`; nothing publishes from this
branch (Pages publishes from `main` runs), so the live public dashboard is untouched until merged.
