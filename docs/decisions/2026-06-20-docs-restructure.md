# Restructure docs/ by state; dissolve the bespoke agents/ dir into docs/design + .claude/agents

**Date:** 2026-06-20  
**Status:** Accepted  
**Deciders:** solo (kchoic) + session agent

## Context
Two adjacent problems, one root cause — **mixed organizing axes**:

- `docs/` had grown to ~39 files in one flat folder conflating lifespan (canon/working/dead), kind (design/plan/standard/handoff/tracker), audience (which agent reads it), and domain (SCP facts vs process docs). `INDEX.md` drifted stale, handoffs piled up (10+), and in-file `status` headers contradicted INDEX.
- `agents/` was a bespoke top-level dir holding 10 human-readable "role" specs + operating-model essays + shared state + a JSON blackboard. But Claude Code's real convention for an *executable* agent is a flat file in `.claude/agents/` (frontmatter + system prompt) — and only **3 of the 10 roles actually execute** as Task workers; the rest are conceptual hats the lead session wears. So `agents/` was ~70% documentation masquerading as an agent system, and its drift (stale roster 8/9/10, triplicated operating loop, STOP-6 copied across 3 files) was the symptom.

Forcing function: a directory tree expresses cleanly only **one axis per level** — the rest belongs in metadata or a generated index — and "documentation" belongs in `docs/`, not a parallel top-level dir.

## Decision

**A. `docs/` — organize by state.** `design/` (decided & stable: design + standards + ops guides) · `working/{plans,handoffs,trackers}/` (changes during execution) · `decisions/` (ADR lane). Lifespan = front-matter `status:`, audience = `for:`, and `INDEX.md` is **generated** from that metadata. No `archive/` folder — superseded docs are flagged in place, so lifespan changes cause zero file moves.

**B. Dissolve `agents/` entirely** — it is documentation + state + config in one coat; split each to its idiomatic home:
- Team-design narrative (10 role summaries + AUTONOMOUS-LOOP + CAMPAIGN + orchestrator loop-essays + HARNESS) → **one** doc `docs/design/agent-team.md`, which owns the operating loop, the L0–L3 ladder, and STOP-6 as the **single source**.
- `CONTEXT.md` current-state → `docs/working/`; its stable contracts → the design doc.
- `coordination/ledger.json` (machine blackboard) → `data/`, with the other ledgers.
- **Executable** agents → `.claude/agents/` (the real convention): expand from 3 to the roles that genuinely dispatch as bounded Task workers; each old role file's Process/Guardrails move into that agent's system prompt. `orchestrator` is the lead session (no file). `PROMPTS.md` becomes largely redundant → folded/retired.
- The `agents/` folder is **deleted**; no new top-level folder is created (all absorbed into existing `docs/`, `data/`, `.claude/`).

## Alternatives Considered

| Option | Reason Rejected |
|--------|----------------|
| Lifespan-as-folders (`canon/working/archive`) | Lifespan changes → every status flip is a file move (churn); flag in place instead |
| Pure by-kind docs/ folders, no state grouping | Loses the at-a-glance "decided vs still-changing" split the owner wanted |
| Naming `canon/` + `state/` | `state` reads as app/Terraform state; `canon` non-idiomatic; no single word covers design+standards+guides |
| Keep `agents/` + add `agents/roles/` (the earlier plan) | "Roles-as-directories" is not an agent-build convention — it's bespoke docs. The real convention is `.claude/agents/` flat files; a `roles/` tree of 10 specs implies 10 running agents when only 3 execute |
| Keep `agents/` as a top-level docs dir | It's documentation, and documentation belongs in `docs/`; a parallel top-level dir re-introduces the mixed-axis container we are removing |

## Consequences

**Good:**
- One axis per level everywhere: `docs/` = human docs by state, `data/` = machine state, `.claude/agents/` = executable agents.
- Single source for the operating loop + STOP-6 (ends the triplication); executable agents stop drifting from a parallel set of role docs.
- Lifespan changes cause zero file moves; INDEX is generated, not hand-maintained.
- Repo loses a whole bespoke top-level dir (`agents/`) and gains no new folder.

**Bad / Constraints:**
- **Highest reference churn**: the bootstrap path (`CLAUDE.md`, `START_HERE.md`) and the 3 `.claude/agents/` workers reference `agents/CONTEXT.md`/`HARNESS.md`/`<role>.md` — all must be rewired. `ARCHITECTURE`/`ROADMAP` move breaks ~9 refs across 7 files.
- `coordination/ledger.json` is likely read by **code** — grep for hardcoded `agents/coordination/ledger.json` before moving (same trap as `/retro`→`docs/lessons.md`, which keeps `lessons.md` at `docs/` root).
- Every `docs/` file needs a front-matter block + an INDEX generator — upfront cost.
- **Open, to finalize during execution:** (1) whether `regression` and `domain-knowledge` become `.claude/agents/` workers or stay design-doc sections; (2) whether to split `CONTEXT.md` now or keep it as a thin bootstrap anchor.

## Override Conditions
Revisit if front-matter + INDEX-generator upkeep exceeds the staleness it prevents, or if the agent team shrinks enough that a flat `docs/` + a couple of `.claude/agents/` files suffice without a design doc. Otherwise stable — reverse only via a superseding ADR.
