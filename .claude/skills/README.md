# Vendored Claude Code Skills

User-invocable [Claude Code skills](https://code.claude.com/docs) for this repo.
Invoke from any session with `/<skill-name>` (e.g. `/pre-push`).

## Provenance

Adapted from **[AlexZio00/claude-code-skills](https://github.com/AlexZio00/claude-code-skills)**
(MIT License, © 2026 AlexZio00). Six of the upstream 13 skills were vendored —
the ones that add value here without colliding with this repo's existing custom
multi-agent harness (`agents/`, `START_HERE.md`, `knowledge/`).

## Adopted skills

| Skill | What it does | Repo adaptation |
|-------|--------------|-----------------|
| `/adr` | Record an architecture/design decision (context, alternatives, consequences) | writes to `docs/decisions/` |
| `/pre-push` | Pre-push secrets scan + tests + lint + AI review gate | scans via local `scan_secrets.pl`; runs read-only `pytest -m smoke`; honors `agents/HARNESS.md` SCP safety gates; never pushes to `main` |
| `/retro` | Milestone retrospective → actionable lessons | writes to `docs/lessons.md` |
| `/brief` | Lock a feature scope (IN/OUT/exit criteria) before coding | writes to `drafts/BRIEF.md` |
| `/freeze` | Declare the editable zone; everything else is read-only | in-context only (no file output) |
| `/token-audit` | Measure actual session token overhead | infographic → `reports/token_audit/` |

## Intentionally NOT adopted

These collide with infrastructure this repo already has — adopting them would run
a parallel, conflicting system:

| Upstream skill | Why skipped |
|----------------|-------------|
| `harness-init` | `agents/HARNESS.md` is the canonical custom harness |
| `team-init` | already a multi-agent team (`agents/orchestrator.md` + roles) |
| `session-start` | `START_HERE.md` + `agents/CONTEXT.md` already do this |
| `session-checkpoint` | `docs/SESSION-HANDOFF*.md` + `data/coordination/ledger.json` |
| `project-init` | `docs/ROADMAP.md` / `docs/ARCHITECTURE.md` already exist (overwrite risk) |
| `collab-audit` | generic; low priority |

**Note — ideas were still mined from these.** We skipped the *skills*, but
backported 9 concrete techniques from `session-start` / `session-checkpoint` /
`harness-init` / `team-init` into the repo's own docs (Memory discipline, subagent
STATUS enum, handoff resume-command rule, stale-reference check, severity→merge
table, output-drift check, `conf·seen·obs` fact metadata, adversarial safety
tests). See `agents/CONTEXT.md` "프로세스/하니스 도구 추가 (2026-06-17)".

## Notes

- The rest of `.claude/` is git-ignored (local config); only `.claude/skills/`
  is tracked — see the exception in `.gitignore`.
- `/token-audit` writes generated artifacts under `reports/`, which is
  git-ignored by design.
- **Two distinct lesson/fact stores — don't conflate them.** `/retro` writes
  *process/meta* lessons to `docs/lessons.md` (created on first use); SCP *domain*
  facts (undocumented fields, state machines, call orders) stay in
  `knowledge/validated-facts.md`. Both use a `conf·seen·obs` confidence line but
  keep their own scale per store (validated-facts: 0.3 docs → 0.7+ live-2xx).
