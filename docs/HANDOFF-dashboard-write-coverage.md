# HANDOFF — dashboard write (POST/PUT/DELETE) coverage & results

## Symptom (reported)

On the dashboard, regression **POST/PUT** (and DELETE) endpoints were not showing
their HTTP response code + execution time; many write rows showed a coverage ✓
with a blank status.

## Root cause

Write coverage was computed from the **static scenario declaration**
(`crud_write_ops`, a service-agnostic `(method, normalized-path)` match), while
the per-endpoint "최근 status" column is fed by **actual observations**. The two
diverged:

1. **Declared-but-not-run** writes (heavy lifecycles gated, light lifecycles
   skipped, or the run hit the 120-min CI timeout) got a ✓ with no status.
2. **Path-collision false ✓** — e.g. `POST /v1/clusters` is declared once (mysql)
   but the same path exists for sqlserver / mariadb / vertica / eventstreams /
   cachestore, so all of them got a ✓ though they were never called.

The engine *does* record write steps correctly (verified): each POST/PUT/PATCH
step is dual-written under its real catalog key with status + `elapsed_ms`, and
the dashboard renders that fine — the bug was purely the coverage ✓ semantics.

## Fix (this branch)

- `dashboard/build.py` — coverage ✓ now means **exercised** (an observation
  exists under the catalog key) for *all* methods, identical to GET. A scenario-
  declared but un-run write is a distinct **◷** marker (no status). `cov_write` =
  writes *exercised* / non-GET total; the *declared* surface is shown separately
  ("선언 N"). GET/operation denominators now intersect with real catalog keys so
  synthetic `lifecycle:step` observation keys don't inflate coverage.
- `regression/scenarios/engine.py` — teardown **DELETE**s run via the cleanup
  stack are now recorded under their catalog key too (resolved from the templated
  path), so failure-path teardown deletes also surface on the dashboard.
- `tests/dashboard/test_coverage_semantics.py` — locks the contract (exercised vs
  declared vs uncovered, collision → no false ✓, `cov_write` = exercised).

## Verified

- Mock-client engine run: POST/PUT/DELETE recorded under catalog keys with
  status + elapsed; teardown DELETE (`networking/vpc/deletepublicip`) recorded.
- Dashboard rebuild from synthetic observations: exercised writes show ✓ + status,
  declared-not-run show ◷, collision services show no false ✓.
- `pytest tests/dashboard` green.

## TODO (next sessions — pick up here)

- [ ] **Conformance runtime probes don't record Observations.** `conformance/
      runtime.py` (probe_status, probe_validation, probe_l10n) actually call
      POST/PUT endpoints and have status + a response time, but record only
      `Finding`s — so those endpoints get no status/time on the dashboard. Add an
      `_observe(...)` that records an `Observation` (source e.g. `runtime_probe`)
      per real call, mirroring `regression/smoke.py::_record`. This was the user's
      original "conformance put/post" instinct and is a real gap.
- [ ] **Response-code donut double-counts writes.** The engine records each write
      step twice (synthetic `lifecycle:step` key + catalog key); `compute()`'s
      `dist`/pass-rate count both. De-dupe by source or key for the distribution.
- [ ] **CI artifact merge.** Both `regression-reports` and `conformance` artifacts
      ship `reports/results`; the dashboard job downloads both. Today only
      conformance writes findings (no observations) so nothing is clobbered, but
      if conformance ever emits observations (e.g. the TODO above, or schema-live
      via the engine) the second download could overwrite the first. Make the
      dashboard merge multiple obs/findings files instead of relying on
      last-writer-wins.
