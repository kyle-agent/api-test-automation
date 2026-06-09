# ROADMAP — where this project is going

> 이 저장소는 **AI multi-agent 팀**이 SCP Open API를 개발·테스트하는
> 프로젝트입니다. 이 문서는 전체 단계(커버리지 100% → 스케줄 회귀 →
> 전용 서버 실행)와 각 에이전트의 자리를 한눈에 보여주는 로드맵입니다.

This repository develops and tests the **Samsung Cloud Platform (SCP) Open
APIs** with a **multi-agent AI team** (roster in [`agents/`](agents/README.md)).
The phases below are the operating plan; each phase's "done-when" is concrete
so any session can tell where we are.

## Phase 1 — Coverage to 100% (CURRENT)

**Goal:** every one of the ~1,372 catalog endpoints is exercised by a real run
(AXIS 1 regression), with pass/fail + response time recorded.

- Static write-op ceiling reached **85.6%** (every write op reachable by an
  enabled lifecycle — see `knowledge/scenario-catalog.md`, Wave 1–3).
- The residual gap is id-bound GETs discovered at runtime by read-chains and
  CRUD `probe_reads`.
- **Remaining:** lane-scheduled **live** CI runs to convert the static ceiling
  into measured coverage, validating the docs-derived request bodies as they go.
- **Done when:** the dashboard's measured coverage reads 100% (or every
  residual endpoint has a triaged blocker recorded in `knowledge/`).

## Phase 2 — Scheduled regression (NEXT)

**Goal:** once coverage is at 100%, the suite becomes a **regression fence**:
the whole surface (or a scoped slice) re-runs on a schedule and only *new*
breakage alarms (baseline + known-issues muting already exist).

- Re-introduce `schedule:` (cron) triggers in `.github/workflows/api-test.yml`
  — they were deliberately removed while mutation runs were being stabilized;
  scheduled read-only smoke is the first step, opt-in CRUD lanes after.
- Full vs partial runs are already expressible: `--category` / `--service`
  scoping, `crud_filter`, and the lane definitions in
  `regression/scenarios/dependencies.json` (`vpc_schedule.lanes`).
- Widen beyond "does it 200": more parameter combinations per endpoint
  (combination scenarios live in `knowledge/formal/combo-scenarios.yaml`).
- **Done when:** a scheduled run needs no human babysitting — quota skips,
  baseline muting and tag-scoped cleanup keep it green unless the API really
  regressed.

## Phase 3 — Run beyond GitHub Actions (LATER)

**Goal:** the same suite runs on a **dedicated server** (customer network /
VPN-only gateways / longer budgets), not only on GitHub-hosted runners.

The architecture already keeps this cheap: everything is `python -m …` CLI
entrypoints + env vars; GitHub Actions is just one orchestrator shell.

- **Step 1 — self-hosted runner:** register a self-hosted GitHub runner on the
  server; the existing workflow runs unchanged (`runs-on` switch).
- **Step 2 — standalone runner:** a thin `runner/` script (cron/systemd) that
  executes the same sequence — spec refresh → regression → sweep →
  conformance → dashboard — and ships results (the dashboard build already
  reads only `reports/results/*.jsonl`, so publishing is pluggable).
- **Done when:** one documented command provisions a fresh server to run the
  nightly schedule and publish the dashboard.

## End state (the destination all phases serve)

Read the **entire userguide**, and from it build: ① per-service test
scenarios, ② multi-service combination scenarios, ③ option/parameter
variations (C4 coverage in `docs/COVERAGE-CRITERIA.md`). For now only the
docs needed for coverage 100% are prioritized — the full backlog with
priorities and per-service status lives in
[`knowledge/formal/INGESTION.md`](knowledge/formal/INGESTION.md)
(짬짬이 ingest: any session picks P1 rows and converts them into
`knowledge/formal/services/` files).

## Cross-cutting — Domain knowledge, formalized

Domain knowledge (API call order, service dependencies, quotas, combination
scenarios) is **data, not code**: AI agents generate it, humans review and
adjust it.

- Narrative form: `knowledge/*.md` · engine form: `regression/scenarios/*.json`.
- **Formalized, human-editable form (draft): [`knowledge/formal/`](knowledge/formal/)**
  — YAML files a human edits, a validator cross-checks against the engine data
  (`python knowledge/formal/validate.py`). Long-term, the formal files become
  the source of truth from which engine data is generated.

## Agent roster ↔ phases

| Agent (`agents/`) | Phase 1 | Phase 2 | Phase 3 |
|---|---|---|---|
| Orchestrator | drives coverage waves | owns the schedule | owns the server runbook |
| Spec-Intel | keeps catalog fresh | spec-diff triggers partial re-runs | same, on-server |
| Domain-Knowledge | formalizes `knowledge/formal/` | adds combo scenarios | — |
| Service agents | validate bodies live | maintain their lifecycles | — |
| Regression | live runs → 100% | scheduled fence | runs anywhere |
| Conformance | static+runtime probes | scheduled, baseline-gated | runs anywhere |
| AI-Evaluator | AI-usability findings | re-judge on spec change | — |
| Dashboard | unified view | trend over schedule | pluggable publish |
