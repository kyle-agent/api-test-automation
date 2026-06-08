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

## Coverage semantics (read before touching `per_service` / `compute`)

Coverage on the dashboard is grounded in **what was actually called** (the unified
observations store), never in the static scenario declaration — for **every**
method. This keeps the ✓ and the "최근 status" column always consistent:

- **✓ exercised** — an observation exists under the endpoint's catalog key, so the
  row shows the real HTTP status + response time. CRUD **write** steps
  (POST/PUT/PATCH) are recorded under their catalog key by the engine
  (`_record_smoke` dual-write), and **teardown DELETEs** run via the cleanup stack
  are recorded too — so writes surface exactly like GETs.
- **◷ declared (미실행)** — the endpoint is declared in a CRUD scenario
  (`crud_write_ops`) but was not exercised this run (heavy gated / skipped /
  timed-out). Distinct marker, **no** status — never a ✓.
- **· uncovered** — neither.

`compute().cov_write` = writes *exercised* / non-GET total; the *declared* surface
is reported separately ("선언 N") so the planned-vs-run gap stays visible.

> History: writes once used a service-agnostic `(method, path)` match against the
> scenario set, which painted a ✓ with no status on endpoints that never ran
> (incl. false ✓ on path-collision services like `/v1/clusters` across
> sqlserver/mariadb/…). POST/PUT/DELETE results "not showing" was that bug. Locked
> by `tests/dashboard/test_coverage_semantics.py`.

## Guardrails

- Read-only over the results store; the dashboard derives, it does not mutate
  source data.
- Keep the legacy fallback path working until the migration is fully proven.
- Surface known-issue muting honestly: a baselined bug is "known", not "new".
- Coverage ✓ means **exercised** (has an observation), for writes as well as GETs.
  Never reintroduce static-declaration-based write ✓ (it hides missing results).

## Done-when

The dashboard builds from the unified store, correctly separates new vs known,
and is published to `dashboard-data`.
