---
status: active
for: all
---

# Lessons

Process / meta lessons (how the agent team works), written by `/retro`. Distinct
from `knowledge/validated-facts.md` (SCP **domain** facts).

<!-- conf scale: 0.3 (tentative) → 0.5 (moderate) → 0.7+ (verified) -->
<!-- Format: ### [YYYY-MM-DD] Title / > conf: X · seen: YYYY-MM-DD · obs: N / body -->

### [2026-06-17] Read with the Read tool before any Edit — Bash/grep/subagent views don't count
> conf: 0.5 · seen: 2026-06-17 · obs: 1

When about to Edit a file you've only seen via Bash (cat/sed/grep), a persisted
tool-output, or a subagent's read, run the **Read tool** on it first. The harness
read-before-edit gate only counts a Read-tool read and rejects the Edit otherwise;
several edits this session failed with "File has not been read yet" and had to be
re-read + re-applied. Do Read → Edit, batching the Reads for the files you'll touch.
Source: /retro — skills vendoring + harness integration

### [2026-06-17] Verify counts/claims against source-of-truth before editing them in docs
> conf: 0.5 · seen: 2026-06-17 · obs: 1

When a number/fact that appears in multiple docs needs editing (category count,
node count, coverage %), compute it from the source first (`data/api_catalog.json`,
`ls knowledge/formal/resources`, `python -m spec.summary`) — don't trust a review
agent's claim or a remembered value. This repo had 4 disagreeing category counts
and 4 node counts. Fix every copy to the verified value and leave ONE canonical
source with pointers from the rest (Memory-discipline rule).
Source: /retro — skills vendoring + harness integration

### [2026-06-17] Before rebasing onto main "for latest", diff the feature-branch tip
> conf: 0.5 · seen: 2026-06-17 · obs: 1

In this repo the live branch (`claude/work-process-…`) is fast-forwarded to `main`,
and the session handoff is often committed one **doc-only commit AFTER** the ff.
Rebasing onto `main` therefore pulled an older SESSION-HANDOFF and needed a
follow-up checkout. Before assuming main is newest, run
`git diff --name-only main origin/<feature>` and pull any doc-only delta.
Source: /retro — skills vendoring + harness integration

### [2026-06-17] Delegate classification with the decision rule, not a blanket default
> conf: 0.5 · seen: 2026-06-17 · obs: 1

When delegating a bulk tag/metadata/confidence retrofit to a subagent, hand it the
**distinguishing criterion** (e.g. docs-derived/UNPROVEN → conf 0.3; live-2xx → 0.7),
not a single default. A flat "use 0.7" instruction made the agent tag UNPROVEN facts
as verified — contradicting the very convention being introduced — and needed a
correction pass.
Source: /retro — skills vendoring + harness integration
