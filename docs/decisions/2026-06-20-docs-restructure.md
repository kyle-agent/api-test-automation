# Organize docs/ by state (decided vs changing), with lifespan/audience as front-matter metadata

**Date:** 2026-06-20  
**Status:** Accepted  
**Deciders:** solo (kchoic) + session agent

## Context
`docs/` had grown to 36 files in one flat folder that conflated several independent axes at once — lifespan (canon/working/dead), kind (design/plan/standard/handoff/tracker), audience (which agent reads it), and domain (SCP facts vs process docs). The result: nothing had a single obvious home, `INDEX.md` drifted stale, handoffs piled up (10+), and in-file `status` headers contradicted INDEX. Forcing function: a directory tree can cleanly express only **one axis per level**, so the remaining axes must live in metadata or a generated index.

## Decision
Reorganize `docs/` by **state** — `design/` (decided & stable: design + standards + ops guides) vs `working/{plans,handoffs,trackers}/` (changes during execution) vs `decisions/` (ADR lane) — and carry **lifespan as front-matter `status:`** and **audience as `for:`**, with `INDEX.md` becoming a view **generated** from that metadata rather than hand-maintained.

## Alternatives Considered

| Option | Reason Rejected |
|--------|----------------|
| Lifespan-as-folders (`canon/working/archive`) | Lifespan changes constantly → every status flip becomes a file move (churn); superseded docs are better flagged in place |
| Pure by-kind folders, no state grouping (model A) | Workable, but loses the at-a-glance "decided vs still-changing" split the owner wanted |
| Naming `canon/` + `state/` | `state` reads as app/Terraform state; `canon` non-idiomatic; and no single word covers design+standards+guides |
| Flat `docs/` + metadata only | Relies 100% on INDEX; no at-a-glance grouping; the large single directory stays unwieldy |

## Consequences

**Good:**
- One axis per level (folder = state, subfolder = kind, `status`/`for` = metadata) — mixed-axis ambiguity gone.
- Lifespan changes cause **zero file moves** (flip `status: superseded` in place); no `archive/` graveyard.
- INDEX becomes a generated cross-view ("currently valid" = `status:active`, "for orchestrator" = `for:orchestrator`) instead of a list that drifts.
- Repo root clears to `CLAUDE`/`README`/`START_HERE`; `ARCHITECTURE`→`docs/design/`, `ROADMAP`→`docs/working/plans/`.

**Bad / Constraints:**
- Every `docs/` file now needs a front-matter block, and an INDEX generator must be written and maintained — upfront cost.
- Moving `ARCHITECTURE`/`ROADMAP` breaks ~9 references across 7 files that must be fixed in the same step; internal relative links deepen.
- `/retro` hardcodes `docs/lessons.md`, so `lessons.md` stays at `docs/` root (asymmetry); other tool-hardcoded doc paths must be checked before any move.
- `-PLAN` files with ambiguous status (draft vs adopted) need a per-file judgment on `design/` vs `working/plans/`.

## Override Conditions
Revisit if front-matter + INDEX-generator upkeep proves heavier than the staleness it prevents, or if doc volume drops enough that a flat `docs/` + generated INDEX suffices. Otherwise stable — reverse only via a superseding ADR.
