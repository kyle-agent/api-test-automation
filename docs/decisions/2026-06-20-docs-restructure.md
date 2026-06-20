# Restructure docs/: carve out a working/ tier (keep stable specs at root); dissolve the bespoke agents/ dir into .claude/agents

**Date:** 2026-06-20  
**Status:** Accepted  
**Deciders:** solo (kchoic) + session agent

## Context
Two adjacent problems, one root cause — **mixed organizing axes**:

- `docs/` had grown to ~39 files in one flat folder conflating lifespan, kind, audience, and domain. `INDEX.md` drifted stale, handoffs piled up (10+), and in-file `status` headers contradicted INDEX.
- `agents/` was a bespoke top-level dir of 10 "role" specs + operating-model essays + shared state + a JSON blackboard. But Claude Code's real convention for an *executable* agent is a flat file in `.claude/agents/` — and only **3 of the 10 roles actually execute** as Task workers; the rest are conceptual hats the lead session wears. So `agents/` was ~70% documentation, and its drift (stale roster, triplicated operating loop, STOP-6 copied across 3 files) was the symptom.

A grep before moving revealed the decisive constraint: the **decided/design docs are cited ~250× across the codebase** as `docs/X.md §N` anchors (`RESOURCE-MODEL-PLAN` alone: 60 code + 13 doc refs, in 30+ `knowledge/formal/resources/*.yaml` headers). None is a functional file read — all are citations — but the flat `docs/X.md` paths are **load-bearing**, so the stable specs are de-facto anchors; moving them fights the grain.

Forcing function: a directory tree expresses cleanly only **one axis per level** — the rest belongs in metadata or a generated index — and documentation belongs in `docs/`, not a parallel top-level dir.

## Decision

**A. `docs/` — carve out a `working/` tier; leave stable specs at root.** Move the volatile, low-coupling docs into `working/{plans,handoffs,trackers}/`. The decided/design specs (`ARCHITECTURE`, `RESOURCE-MODEL-PLAN`, `PLATFORM-PLAN`, `COVERAGE-CRITERIA`, `IA`, `M6-DESIGN`, `DEPLOY`, `OPS-DASHBOARD`, `scheduler-system`) **stay at `docs/` root** — they are the ~250×-cited stable anchors, so moving them is high-churn for low benefit (the conventional "keep stable at root, quarantine the volatile" idiom). `decisions/` unchanged. **No `design/` or `archive/` subfolder** — lifespan is front-matter `status:`, audience `for:`, and `INDEX.md` is **generated** from that metadata; superseded docs are flagged in place (zero file moves).

**B. Dissolve `agents/` entirely** — documentation + state + config in one coat; split each to its idiomatic home:
- Team-design narrative (10 role summaries + AUTONOMOUS-LOOP + CAMPAIGN + orchestrator loop-essays + HARNESS) → **one** design doc at `docs/` root (`docs/agent-team.md`), owning the operating loop, the L0–L3 ladder, and STOP-6 as the **single source**.
- `CONTEXT.md` current-state → `docs/working/`; its stable contracts → the design doc.
- `coordination/ledger.json` (machine blackboard) → `data/`.
- **Executable** agents → `.claude/agents/` (the real convention): expand from 3 to the roles that genuinely dispatch as bounded Task workers; each old role file's Process/Guardrails move into that agent's system prompt. `orchestrator` is the lead session (no file). `PROMPTS.md` becomes largely redundant → folded/retired.
- The `agents/` folder is **deleted**; no new top-level folder is created.

## Alternatives Considered

| Option | Reason Rejected |
|--------|----------------|
| Move stable design specs into `docs/design/` too | ~250 citations across code/yaml/workflows would need a path rename — high churn, low benefit; the specs are already stable anchors |
| Lifespan-as-folders (`canon/working/archive`) | Lifespan changes → every status flip is a file move; flag in place instead |
| Naming `canon/` + `state/` | `state` reads as app/Terraform state; `canon` non-idiomatic |
| Keep `agents/` + add `agents/roles/` | "Roles-as-directories" is not an agent-build convention — it's bespoke docs; the real convention is `.claude/agents/` flat files, and only 3 of 10 roles execute |
| Keep `agents/` as a top-level docs dir | It's documentation, and documentation belongs in `docs/` |

## Consequences

**Good:**
- The real problem — handoff/tracker sprawl — is tamed at low cost; the ~250 spec citations stay valid (no code touched for the design specs).
- One axis per level: `docs/` human docs (stable at root, volatile in `working/`), `data/` machine state, `.claude/agents/` executable agents.
- Single source for the operating loop + STOP-6; executable agents stop drifting from a parallel role-doc set.
- Repo loses a whole bespoke top-level dir (`agents/`); no new folder created.

**Bad / Constraints:**
- The `working/` carve-out still rewrites ~100 references (mostly doc-to-doc, ~13 code) to the moved files — done as a deterministic per-file path replace.
- `agents/` dissolution is the **highest-churn** remaining step: the bootstrap path (`CLAUDE.md`, `START_HERE.md`) and the 3 `.claude/agents/` workers reference `docs/working/CONTEXT.md`/`HARNESS.md`/`<role>.md`; `coordination/ledger.json` may be read by code — grep before moving (same trap as `/retro`→`docs/lessons.md`, which keeps `lessons.md` at `docs/` root).
- INDEX must become generated; until then it is hand-maintained.
- **Open, to finalize during execution:** (1) whether `regression`/`domain-knowledge` become `.claude/agents/` workers or design-doc sections; (2) whether to split `CONTEXT.md` now or keep a thin bootstrap anchor.

## Override Conditions
Revisit if front-matter + INDEX-generator upkeep exceeds the staleness it prevents, or if a future need makes the stable specs worth grouping under `docs/design/` (then do the ~250-ref rename deliberately). Otherwise stable — reverse only via a superseding ADR.
