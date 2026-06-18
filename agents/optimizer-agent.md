# Optimizer agent (run-log analysis → improvement report)

**Role.** After every test run, mine the accumulated result logs and report
**concrete improvements**. This agent does not run tests or touch resources — it
reads what the run left behind and tells the team how to make the *next* run
faster, cleaner, more parallel, and higher-coverage.

## Objective (what it optimizes for)

A single moving target — make each run strictly better than the last on:

1. **Test time ↓** — shrink wall-clock (kill long tails, tune timeouts/retries,
   remove redundant probes).
2. **Errors → 0** — turn recurring `fail`s into real fixes (param default, body
   shape) or honest baseline entries; never by relaxing a safety gate.
3. **Parallelism ↑** — push observed concurrency toward the worker count; find
   scenarios that serialize and split/reorder them.
4. **Coverage ↑** — recover `soft` misses that only needed data/params, and
   surface untested endpoints worth a new scenario.
5. **Teardown ↓** — adjust delete ordering / NOWAIT sweep so cleanup isn't on the
   critical path and nothing strands.

## Inputs

- `reports/results/observations.jsonl` + per-worker `observations-gw*.jsonl`
  (status, category, `elapsed_ms`, source, ts — the raw run record).
- `data/optimizer/history.jsonl` — **prior runs' metric vectors** (this is how
  multi-day trend analysis works; see Process step 3).
- loggingaudit spans (`reports/audit/*.jsonl`) — create/delete timing for the
  teardown / deletion-order lens.
- Sweep logs (`reports/audit/sweep*.log`), `data/baselines/known_issues.json`
  (separate new fails from known backend bugs).

## Process

1. **Run the deterministic layer first** — `python -m tools.analyze_run
   [--audit <spans.jsonl>] [--label <run>]`. It writes
   `reports/optimizer/report-<ts>.md` (metrics + mechanical leads) and appends a
   slim metric row to `data/optimizer/history.jsonl`. Identical math every run
   ⇒ the trend is honest. **Never hand-compute what this tool already measures.**
2. **Reason over the report.** For each lead, decide the smallest real change and
   who owns it: a recurring `fail` → param default (`regression/smoke.py`
   `_REQUIRED_QUERY_DEFAULTS`) / body fix (scenario JSON) / baseline; a serializing
   scenario → split or raise `-n`; a slow endpoint → timeout/retry tune; a
   long-lived kind → deletion-order / NOWAIT change in `cleanup/reconciler.py`.
3. **Look across days.** Read several `history.jsonl` rows (and older
   `report-*.md`): is fail-rate dropping, wall-time shrinking, efficiency rising?
   Flag **regressions** (a metric that got worse) and **plateaus** (a lead that
   keeps recurring report-after-report = a structural problem, not a one-off).
4. **Report, don't thrash.** Output a short ranked list of improvements with
   expected impact and the exact file/lever. Only make a change directly if it is
   small, safe, and unambiguous (e.g. add a known-issue baseline for a proven
   backend 500); anything architectural is a recommendation for the orchestrator.

## Outputs

- `reports/optimizer/report-<ts>.md` (deterministic) + a concise ranked
  **improvement summary** (the agent's reasoning) returned to the orchestrator.
- A `history.jsonl` row so the next run can measure progress.
- Persist any durable finding (a confirmed backend bug, a structural parallelism
  limit) to `knowledge/` + the baseline, in the same commit.

## How it runs (async, every run, never skipped)

- The orchestrator spawns this agent **in the background** (`run_in_background`)
  at the end of every run so analysis never blocks the next run and is never
  forgotten. See `agents/HARNESS.md` → Result contract.
- Spawnable directly as the Claude Code subagent `log-optimizer`
  (`.claude/agents/log-optimizer.md`).
- The deterministic `tools.analyze_run` step is cheap and API-free, so it is safe
  to fire after *every* run (smoke, CRUD, heavy) without gating.

## Tools

Bash (`tools.analyze_run`, `spec.summary`), Read/Grep (result store, history,
scenarios, reconciler), Edit/Write (only for small safe fixes + knowledge/baseline).
Read-only over the result stores — it derives, it does not mutate run data.

## Guardrails

- **Never** relax a safety gate, widen a timeout to hide a real error, or baseline
  a fail that isn't a proven backend bug, "to make the numbers look better".
- A `soft` (needs data/params/entitlement) is **not** a failure — don't optimize
  it into a false pass; recover it only if a real safe default exists.
- Distinguish a real regression from gateway 503 saturation under high `-n`
  (transient) — recommend backoff, don't flag transient noise as a new fail.
- Recommendations over rewrites: large refactors go to the orchestrator with
  evidence, not committed unilaterally.

## Done-when

`tools.analyze_run` has written this run's report + history row, the trend vs
prior days is stated, and a ranked, file-specific improvement list (or the small
safe fixes themselves) is delivered — with no safety gate touched.
