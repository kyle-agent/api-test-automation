# START_HERE.md — Session bootstrap (read me first)

> **이 파일은 어떤 Claude Code 세션에서 시작하더라도 동일한 지점에서 이어서
> 작업할 수 있도록 하는 진입점입니다.** 새 세션이 열리면 이 파일 →
> `agents/CONTEXT.md` → 작업에 해당하는 `agents/<agent>.md` 순서로 읽고
> 시작하세요. 도메인 지식은 `knowledge/` 에 누적됩니다.

This repository is the **SCP API Regression Test Platform**: it tests the
**Samsung Cloud Platform (SCP) Open APIs**
(13 categories / ~60 services / **1,372 endpoints**) along two axes —
**regression** ("does it work?") and **conformance** ("is it well designed &
AI-usable?") — and wraps them in a **control plane**
([`controlplane/`](controlplane/README.md): dispatch, schedule, live tracking,
intervention, history/compare, AI seams) plus the **M5 resource-task model**
([`knowledge/formal/resources/`](knowledge/formal/), 275 nodes / 60 service files) from which
scenarios are *composed* (`regression/scenarios/composer.py`). The engineering
is done by **a team of AI agents** (this is a *multi-agent* project) whose
roles, prompts, context and execution harness are documented under
[`agents/`](agents/), and whose shared **SCP domain knowledge** is accumulated
under [`knowledge/`](knowledge/).

## Mission (the two axes)

1. **Regression** — prove each endpoint works; record pass/fail + response time.
   **Goal: 100% of the SCP OpenAPI surface covered.** Once coverage is at 100%,
   widen by exercising more parameter combinations. Evidence comes from **real
   test runs**.
2. **Conformance** — judge whether the API follows good API design (REST/HTTP
   best practices) **and** is easy for an AI agent to consume. Evidence comes
   from **static analysis + real runtime probes**.

See [`README.md`](README.md) and [`ARCHITECTURE.md`](ARCHITECTURE.md) for the
implementation blueprint, and [`ROADMAP.md`](ROADMAP.md) for the phase plan
(coverage 100% → scheduled regression → dedicated-server runs). This file does
not duplicate them.

## How a new session should start

1. Read [`agents/CONTEXT.md`](agents/CONTEXT.md) — shared facts every agent needs
   (goals, current coverage, safety gates, where results live).
2. Read [`agents/README.md`](agents/README.md) — the agent roster and how the
   orchestrator delegates.
3. **Spot-check the handoff before trusting it.** Before acting on the current
   handoff (`docs/SESSION-HANDOFF*.md`) / `agents/CONTEXT.md`, verify 1–2 concrete
   references it names — a file/fragment path (Glob/Grep), a run-id, or a coverage
   number (`python -m spec.summary`) — to confirm they still exist / still hold.
   핸드오프는 run-id·SHA·fragment 경로를 인용하는데 이것들은 금방 stale 됩니다. If a
   cited path, run-id, or number is stale, **flag it and trust current observed
   state over the handoff.**
4. Open the agent doc for your task (e.g. running CRUD = `agents/regression-agent.md`;
   teaching the suite a new service order = `agents/domain-knowledge-agent.md`).
5. Consult [`knowledge/`](knowledge/) before inventing API call orders or request
   bodies — most of it is already captured (and hard-won). Add what you learn back.

> **Kicking off a fresh session?** The minimum prompt is literally:
> *"Read `START_HERE.md` and continue per its instructions."* Ready-to-paste
> kickoff prompts for specific goals (advance coverage, run conformance, curate
> domain knowledge) live in [`agents/PROMPTS.md`](agents/PROMPTS.md#starting-a-new-session-copy-paste-kickoffs).

> **Handoff convention (when you write/end a session handoff):** the **TOP /
> current in-progress item** MUST carry a literal, copy-pasteable **resume
> command** — the exact `pytest …` / dispatch / `grep` / `python -m …` to run
> next — not just a prose description of the next candidate. 다음 세션이 핸드오프를
> 열자마자 그 명령어를 그대로 복사·실행할 수 있어야 합니다.

## Golden rules (do not break these)

- **Safety gates are sacred.** A run never changes cloud state unless explicitly
  opted in: `GET` always runs; `POST/PUT/PATCH` need `SCP_ALLOW_MUTATIONS=true`;
  `DELETE` needs `SCP_ALLOW_DESTRUCTIVE=true`; heavy/billable lifecycles (VM, K8s,
  DB) need `SCP_RUN_HEAVY=true`. Never weaken these defaults. (Canonical table:
  `agents/CONTEXT.md` "Safety gates".)
- **Domain knowledge is data, not code.** Call order, dependencies, quotas and
  scenarios live in `knowledge/` + `regression/scenarios/*.json` so a human can
  read and adjust them. Agents generate them; humans review them. The
  formalized, human-editable form is `knowledge/formal/` (YAML + validator).
- **Every created resource must be owned and torn down.** Use `core.registry`
  tagging + reverse-order cleanup. The `cleanup.reconciler` only deletes *our*
  owner tag — never weaken this into cross-run deletion.
- **Persist what you learn.** A fact discovered at runtime (an undocumented field,
  a state machine, a 500-on-delete race) belongs in `knowledge/validated-facts.md`
  and/or the scenario `_note`, committed to git — so the next session starts ahead.
- **Develop on the assigned branch, commit with clear messages, push when done.**
  Do **not** open a PR unless explicitly asked.

## Map

| Path | What |
|------|------|
| `agents/` | The multi-agent system: roster, shared context, harness, per-agent prompts |
| `knowledge/` | Accumulated SCP domain knowledge (human-readable, AI-maintained); `formal/resources/` = the M5 resource-task model (composer input) |
| `core/` | Shared kernel: config·auth·http_client·catalog·registry·results·budgets·suites·profiles·oplog·snapshot·commands·baselines |
| `spec/` | Extract the API spec from the docs + diff versions |
| `regression/` | AXIS 1 — smoke · read_chains · scenarios (declarative CRUD engine + composer + data) |
| `conformance/` | AXIS 2 — static · runtime · baseline · pluggable `rules/` |
| `dashboard/` | Build the unified HTML dashboard from the results store + `ops.html` live ops viewer |
| `controlplane/` | The platform server (FastAPI+htmx): dispatch · scheduler · live runs · intervention · authoring · AI pipelines · static export (Pages `/platform/`) |
| `runner/` | `worker.py` — same-host executor for the M4 deployment cutover |
| `suites/` · `environments/` | Named suites + environment profiles (run = suite × profile) |
| `drafts/` | Composer/AI draft outputs awaiting human review (never auto-enabled) |
| `cleanup/` | Tag-scoped reconciler (guaranteed teardown) |
| `data/` | Catalog, request bodies, docs, baselines (incl. `coverage_waivers.json`; per-profile suffixed siblings) |
| `reports/` | Per-run output (gitignored): `results/*.jsonl`, dashboard, junit |
| `docs/` | Plans (PLATFORM-PLAN · RESOURCE-MODEL-PLAN · DEPLOY) + handoffs — see `docs/INDEX.md` |
