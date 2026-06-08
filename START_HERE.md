# START_HERE.md — Session bootstrap (read me first)

> **이 파일은 어떤 Claude Code 세션에서 시작하더라도 동일한 지점에서 이어서
> 작업할 수 있도록 하는 진입점입니다.** 새 세션이 열리면 이 파일 →
> `agents/CONTEXT.md` → 작업에 해당하는 `agents/<agent>.md` 순서로 읽고
> 시작하세요. 도메인 지식은 `knowledge/` 에 누적됩니다.

This repository tests the **Samsung Cloud Platform (SCP) Open APIs**
(15 categories / ~60 services / **1,372 endpoints**) along two axes —
**regression** ("does it work?") and **conformance** ("is it well designed &
AI-usable?"). The engineering is done by **a team of AI agents** (this is a
*multi-agent* project) whose roles, prompts, context and execution harness are
documented under [`agents/`](agents/), and whose shared **SCP domain knowledge**
is accumulated under [`knowledge/`](knowledge/).

## Mission (the two axes)

1. **Regression** — prove each endpoint works; record pass/fail + response time.
   **Goal: 100% of the SCP OpenAPI surface covered.** Once coverage is at 100%,
   widen by exercising more parameter combinations. Evidence comes from **real
   test runs**.
2. **Conformance** — judge whether the API follows good API design (REST/HTTP
   best practices) **and** is easy for an AI agent to consume. Evidence comes
   from **static analysis + real runtime probes**.

See [`README.md`](README.md) and [`ARCHITECTURE.md`](ARCHITECTURE.md) for the
implementation blueprint (this file does not duplicate them).

## How a new session should start

1. Read [`agents/CONTEXT.md`](agents/CONTEXT.md) — shared facts every agent needs
   (goals, current coverage, safety gates, where results live).
2. Read [`agents/README.md`](agents/README.md) — the agent roster and how the
   orchestrator delegates.
3. Open the agent doc for your task (e.g. running CRUD = `agents/regression-agent.md`;
   teaching the suite a new service order = `agents/domain-knowledge-agent.md`).
4. Consult [`knowledge/`](knowledge/) before inventing API call orders or request
   bodies — most of it is already captured (and hard-won). Add what you learn back.

> **Kicking off a fresh session?** The minimum prompt is literally:
> *"Read `START_HERE.md` and continue per its instructions."* Ready-to-paste
> kickoff prompts for specific goals (advance coverage, run conformance, curate
> domain knowledge) live in [`agents/PROMPTS.md`](agents/PROMPTS.md#starting-a-new-session-copy-paste-kickoffs).

## Golden rules (do not break these)

- **Safety gates are sacred.** A run never changes cloud state unless explicitly
  opted in: `GET` always runs; `POST/PUT/PATCH` need `SCP_ALLOW_MUTATIONS=true`;
  `DELETE` needs `SCP_ALLOW_DESTRUCTIVE=true`. Never weaken these defaults.
- **Domain knowledge is data, not code.** Call order, dependencies, quotas and
  scenarios live in `knowledge/` + `regression/scenarios/*.json` so a human can
  read and adjust them. Agents generate them; humans review them.
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
| `knowledge/` | Accumulated SCP domain knowledge (human-readable, AI-maintained) |
| `core/` | Shared kernel: config·auth·http_client·catalog·registry·results·budgets |
| `spec/` | Extract the API spec from the docs + diff versions |
| `regression/` | AXIS 1 — smoke · read_chains · scenarios (declarative CRUD engine + data) |
| `conformance/` | AXIS 2 — static · runtime · baseline · pluggable `rules/` |
| `dashboard/` | Build the unified HTML dashboard from the results store |
| `cleanup/` | Tag-scoped reconciler (guaranteed teardown) |
| `data/` | Catalog, request bodies, docs, baselines |
| `reports/` | Per-run output (gitignored): `results/*.jsonl`, dashboard, junit |
