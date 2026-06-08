# Agents — the multi-agent system

This repo is engineered by a **team of AI agents**. This directory is their
durable definition: roles, the context they share, the harness they run on, and
each agent's prompt. Any new Claude Code session reconstitutes the team from
here — so the work continues identically across sessions.

## How to read this directory

1. [`CONTEXT.md`](CONTEXT.md) — shared state every agent loads (goals, catalog,
   safety gates, results contract, current state).
2. [`HARNESS.md`](HARNESS.md) — how agents execute (runtime, tools, commands, CI,
   safety rails).
3. [`PROMPTS.md`](PROMPTS.md) — prompt conventions + reusable blocks to paste
   when spawning a subagent.
4. One file per agent (below). Each follows the **same template**: Role ·
   Objective · Inputs · Process · Outputs · Tools · Guardrails · Done-when.

## The roster

| Agent | File | One-line role |
|-------|------|---------------|
| **Orchestrator** | [`orchestrator.md`](orchestrator.md) | Plans the run, delegates slices to the right agent, keeps `CONTEXT.md` current. |
| **Spec-Intel** | [`spec-intel-agent.md`](spec-intel-agent.md) | Keeps the API spec + per-service facts fresh; diffs versions; feeds both axes. |
| **Domain-Knowledge** | [`domain-knowledge-agent.md`](domain-knowledge-agent.md) | Curates `knowledge/`: call order, dependencies, quotas, combo scenarios. |
| **Service agents** | [`service-agent.md`](service-agent.md) | Per-service experts (e.g. virtualserver, filestorage) that drive their endpoints/scenarios. |
| **Regression** | [`regression-agent.md`](regression-agent.md) | AXIS 1: widen coverage to 100%, run smoke + CRUD, record observations. |
| **Conformance** | [`conformance-agent.md`](conformance-agent.md) | AXIS 2: static + runtime defect detection vs baseline. |
| **AI-Evaluator** | [`ai-evaluator-agent.md`](ai-evaluator-agent.md) | Third-party "can an AI use this API?" judge; feeds conformance's AI-usability lens. |
| **Dashboard** | [`dashboard-agent.md`](dashboard-agent.md) | Builds/publishes the unified dashboard from the results store. |

## Collaboration flow

```
                 ┌──────────────────────────────────────────────┐
                 │              Orchestrator                      │
                 │  reads CONTEXT.md, picks the next objective,   │
                 │  delegates a bounded slice, updates state      │
                 └───┬───────────┬───────────┬───────────┬───────┘
                     │           │           │           │
        Spec-Intel ──┘           │           │           └── Dashboard
        (fresh spec + facts)     │           │              (visualize both axes)
                                 ▼           ▼
                     Domain-Knowledge   Service agents (virtualserver, filestorage, …)
                     (call order, deps, ── drive ─►  Regression (AXIS 1)   Conformance (AXIS 2)
                      quotas, scenarios)                 │                      ▲
                                                         └── observations       │ findings
                                                                                AI-Evaluator
                                                                          (AI-usability findings)
```

- **Spec-Intel** refreshes `data/` (catalog/bodies/docs) and surfaces what
  changed → triggers re-evaluation of only affected endpoints.
- **Domain-Knowledge** turns service facts into the *data* the engines consume
  (`knowledge/` + `regression/scenarios/*.json`).
- **Service agents** are the domain experts for one service; they own that
  service's scenarios, validated facts, and quirks.
- **Regression** & **Conformance** execute the two axes and write the unified
  results store; **AI-Evaluator** contributes the "is this usable by an AI?"
  perspective into conformance findings.
- **Dashboard** renders everything from one results store.

## Adding a new agent

Copy the template structure from any agent file, add a row to the roster table
above, and (if it's a long-lived role) note it in `CONTEXT.md`. Keep prompts in
the agent file, not scattered in code.
