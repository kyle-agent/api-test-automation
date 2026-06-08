# Dashboard agent

**Role.** Turn the unified results store into a single, self-contained HTML
dashboard that shows both axes, and publish it.

## Objective

Give humans one place to see health: coverage, regression status + response time
(AXIS 1), design/AI-usability findings (AXIS 2), the CRUD grid, and trends —
distinguishing NEW regressions from known issues.

## Inputs

- `reports/results/observations.jsonl` + `findings.jsonl` (primary).
- Legacy fallbacks (kept working mid-migration): `reports/smoke_status.tsv`,
  `data/conformance.json`, `reports/junit-crud.xml`.
- `data/baselines/known_issues.json` (to separate new vs known).

## Process

1. `python -m dashboard.build` — reads the unified store first, falls back to
   legacy flat files so nothing regresses.
2. Verify the render: health (new vs known regressions, pass rate, coverage),
   per-service drill-down (status + response time, design/behavior defect
   columns), CRUD grid, trends.
3. Publish: `index.html` + `history.jsonl` go to the **`dashboard-data`** branch
   each run (Settings → Pages → Deploy from `dashboard-data` / root).

## Outputs

- Rendered dashboard under `reports/dashboard/` and published to `dashboard-data`.

## Tools

Bash (`dashboard.build`), Read (results store), GitHub MCP / git (publish to the
data branch — only as part of the normal run, not a PR).

## Guardrails

- Read-only over the results store; the dashboard derives, it does not mutate
  source data.
- Keep the legacy fallback path working until the migration is fully proven.
- Surface known-issue muting honestly: a baselined bug is "known", not "new".

## Done-when

The dashboard builds from the unified store, correctly separates new vs known,
and is published to `dashboard-data`.
