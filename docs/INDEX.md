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

## Handoffs

| Doc | Topic | Status |
|-----|-------|--------|
| [`HANDOFF-crud-setter-validation.md`](HANDOFF-crud-setter-validation.md) | CRUD write/setter coverage validation (PR #44): env constraints, failures, next steps | active |
| [`SESSION-HANDOFF-parallel-crud.md`](SESSION-HANDOFF-parallel-crud.md) | Parallel-adopt CRUD re-architecture (shared VPC + subnet adoption) | active |
