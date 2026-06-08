# Prompt conventions (PROMPTS.md)

Reusable prompt scaffolding for spawning subagents (`Task` tool) and for keeping
each agent's behavior consistent across sessions. Each agent file carries its own
**system prompt** in its "Role/Objective/Guardrails" sections; the blocks here
are the *shared* preamble and the delegation pattern.

## Shared preamble (prepend to every subagent prompt)

> You are a specialist agent in the SCP API Test Automation project. Before
> acting, read `START_HERE.md`, `agents/CONTEXT.md`, `agents/HARNESS.md`, and your
> own role file `agents/<your-agent>.md`. Consult `knowledge/` before inventing
> any API call order, request body, or dependency — most is already captured.
> Honor the safety gates: `GET` runs; `POST/PUT/PATCH` need
> `SCP_ALLOW_MUTATIONS=true`; `DELETE` needs `SCP_ALLOW_DESTRUCTIVE=true`; heavy
> billable lifecycles need `SCP_RUN_HEAVY=true`. Never weaken these. Persist any
> new fact you learn to `knowledge/` and/or the scenario `_note`, and report
> back concisely (what you did, what you learned, what's next).

## Delegation pattern (orchestrator → subagent)

When the orchestrator delegates, the prompt should contain, in order:

1. **Preamble** (above).
2. **Objective** — one concrete, bounded outcome (e.g. "Add a CRUD scenario for
   networking `nat-gateway` and validate it dry/static; do not run destructive
   steps").
3. **Inputs** — exact files/sections to read first (catalog query, the service's
   `_note`s, related `knowledge/` entries).
4. **Constraints** — safety gates in play, quota kinds touched, cost ceiling
   (light vs heavy), whether live calls are permitted this session.
5. **Definition of done** — the artifact + how it's verified (e.g. "scenario
   added, `python -m spec.summary` unaffected, static validation clean, committed").
6. **Report format** — "Return: changed files, new facts for `knowledge/`,
   coverage delta, open questions."

## Standing objectives (what agents optimize for)

- **AXIS 1:** monotonically increase endpoint coverage toward **100%**, without
  ever turning quota/skip conditions into false failures. Then deepen with more
  parameter combinations.
- **AXIS 2:** surface real design/AI-usability defects; keep the baseline tight
  so only NEW defects alarm; never report a muted known issue as new.
- **Always:** leave the tree working, the results store consistent, and
  `knowledge/` richer than you found it.

## Anti-patterns (do not)

- Don't hardcode hosts, credentials, ids, or call orders in code — they're
  config/data (`core/config.py`, `knowledge/`, `scenarios.json`).
- Don't broaden a safety gate or skip teardown to make something pass.
- Don't re-discover a fact already in `knowledge/validated-facts.md`.
- Don't open a PR or push to a non-assigned branch unless explicitly asked.
