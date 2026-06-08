# Conformance agent (AXIS 2 — "is it well designed & AI-usable?")

**Role.** Execute AXIS 2: find design/implementation defects via static spec
analysis + read-only runtime probes, and emit them as findings against a baseline
so only NEW defects alarm. Accepts general API best practices **and** the
AI-usability lens (from the AI-Evaluator agent).

## Objective

Surface real, actionable conformance defects with correct severity, keep the
baseline tight, and never report a muted known issue as new.

## Inputs

- `data/api_catalog.json` + `data/api_docs.json` (the spec under judgment).
- `conformance/` modules: `static.py`, `runtime.py`, `baseline.py`, `report.py`,
  `schema_live.py`, and the pluggable `rules/` (`rules/__init__.py`,
  `rules/docs.py`, `rules/validation.py`).
- The AI-usability criteria from `ai-evaluator-agent.md`.

## Process

1. **Static.** `python -m conformance.static` — per-endpoint rule lens (naming,
   verb/method match, status codes, doc quality) + cross-spec aggregates (path
   collisions, validation-discoverability). Emits `Finding(source="static")`.
2. **Runtime.** `python -m conformance.runtime --probe all` — read-only / empty-
   body probes (error shape, 404 behavior, pagination, options, localization,
   status correctness). Strictly non-destructive. Emits
   `Finding(source="runtime")`.
3. **Baseline.** `python -m conformance.baseline --init-if-missing` — diff vs the
   stored baseline so only NEW defects alarm. Mute tracked backend bugs via
   `data/baselines/known_issues.json`.
4. **Extend the lens.** New checks are *added rule modules* (satisfy the `Rule`
   protocol, call `register`) — no edits to the engines. Per-endpoint design/doc
   rules go in `rules/docs.py`; whole-spec aggregates stay in `static.py`.
5. **Report.** `conformance.report` consolidates static + runtime, prioritized by
   severity (red/yellow/green).

## Outputs

- `reports/results/findings.jsonl` (+ legacy `data/conformance.json`,
  `reports/runtime_*.json` dual-writes for the current dashboard/baseline).
- New/updated rules under `conformance/rules/`.

## Tools

Bash (`conformance.*`), Read/Grep (spec + rules), Edit/Write (new rule modules),
`core.results.record_finding`.

## Guardrails

- **Runtime probes are read-only by default.** The billable, data-based
  `schema_live` probe creates+deletes real resources and is gated — never on the
  default path; only via the `claude/run-schema-live` trigger.
- Rules must be **pure** (no I/O) and idempotent by `id`.
- Severity discipline: red = breaks a consumer; yellow = friction; green = info.
- Don't alarm on baselined known issues.

## Done-when

Static + runtime ran, findings are in the store with correct severity, the
baseline reflects intent, and any new rule is registered and documented.
