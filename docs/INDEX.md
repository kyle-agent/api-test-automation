# docs/ — session handoff index

Operational handoff documents: mid-effort state a session leaves behind so the
next one resumes without re-discovery. **Architecture lives elsewhere**
(`README.md` → `ARCHITECTURE.md` → `ROADMAP.md`); these files are
work-in-progress notes.

## Conventions

- Name: `HANDOFF-<topic>.md` (or `SESSION-HANDOFF-<topic>.md`, legacy).
- Top of file: date, branch/PR, status (`active` / `superseded` / `done`).
- When the effort lands, mark the doc `done` here (don't delete — it's history).
- Add every new handoff to the table below.

## Proposals / standards (draft until approved)

| Doc | Topic | Status |
|-----|-------|--------|
| [`COVERAGE-CRITERIA.md`](COVERAGE-CRITERIA.md) | The C0–C4 coverage ladder + waiver mechanism — what "100%" means | **adopted** (2026-06-09) |
| [`PARALLEL-EXECUTION-PLAN.md`](PARALLEL-EXECUTION-PLAN.md) | Staged foundations + per-VPC lanes — cut wall-clock to max(lane) instead of sum | **draft, awaiting review** |

## Handoffs

| Doc | Topic | Status |
|-----|-------|--------|
| [`HANDOFF-crud-setter-validation.md`](HANDOFF-crud-setter-validation.md) | CRUD write/setter coverage validation (PR #44): env constraints, failures, next steps | active |
| [`SESSION-HANDOFF-parallel-crud.md`](SESSION-HANDOFF-parallel-crud.md) | Parallel-adopt CRUD re-architecture (shared VPC + subnet adoption) | active |
| [`HANDOFF-fail-new-triage.md`](HANDOFF-fail-new-triage.md) | The 52 fail_new of the 2026-06-10 full heavy run, classified (body-fix vs domain-hunt vs known-red candidates) | active |
