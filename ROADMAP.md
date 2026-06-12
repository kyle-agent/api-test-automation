# ROADMAP — where this project is going

> 이 저장소는 **AI multi-agent 팀**이 SCP Open API를 개발·테스트하는
> 프로젝트입니다. 이 문서는 전체 단계(커버리지 100% → 스케줄 회귀 →
> 전용 서버 실행)와 각 에이전트의 자리를 한눈에 보여주는 로드맵입니다.

This repository develops and tests the **Samsung Cloud Platform (SCP) Open
APIs** with a **multi-agent AI team** (roster in [`agents/`](agents/README.md)).
The phases below are the operating plan; each phase's "done-when" is concrete
so any session can tell where we are.

> **Platform milestone status at a glance** (detail:
> [`docs/PLATFORM-PLAN.md`](docs/PLATFORM-PLAN.md) ·
> [`docs/RESOURCE-MODEL-PLAN.md`](docs/RESOURCE-MODEL-PLAN.md)):
> **M0–M3 DONE** (engine groundwork · control-plane MVP · ops/intervention ·
> authoring + AI pipelines) · **M4 built, cutover deliberately LAST**
> (`runner/worker.py` + Docker Compose await live/docker verification) ·
> **M5 resource-task model: R1 done** (128 nodes / 50 files) · **R2 done**
> (composer live-proven) · **R3 verification waves IN PROGRESS**
> (waves 1·2 + heavy window: ~10 composed chains stably green, 56 nodes
> VALIDATED — findings ledgered in `docs/PRODUCT-FINDINGS.md`) ·
> **R4 (C4 variants) pending**.

## Phase 1 — Coverage to 100% (CURRENT)

**Goal:** every one of the ~1,372 catalog endpoints is exercised by a real run
(AXIS 1 regression), with pass/fail + response time recorded.

- ✅ Static write-op ceiling reached **85.6%** (every write op reachable by an
  enabled lifecycle — see `knowledge/scenario-catalog.md`, Wave 1–3), since
  raised to **88.1% (1,209/1,372)** by explicit-GET conversions and the M5
  verify-step waves (gap_getid 151 → 130, `docs/COVERAGE-GETID-PLAN.md`;
  `python -m spec.coverage_gap`).
- Measured (live) coverage as of the latest published run: **C3 44.79%**
  (cov_op 36.73) · reachable ceiling 88.1% · **fail_new 0 policy holding** ·
  249 approved waivers (`data/baselines/coverage_waivers.json`).
- The residual gap is id-bound GETs discovered at runtime by read-chains and
  CRUD `probe_reads`, plus owner-excluded scopes (archivestorage permanently
  excluded; Parallel File Storage reads-only — writes coverage-waived,
  `owner-exclusion` class).
- The coverage engine is shifting from hand-written lifecycles to the **M5
  resource-task model → composer** path (see M5 above): coverage waves are now
  *composed* verification runs (`crud_filter=gen-wave`).
- **Remaining:** keep running R3 verification waves to convert docs-derived
  nodes into VALIDATED measured coverage; triage loop per wave.
- **Done when:** the dashboard's measured coverage reads 100% (or every
  residual endpoint has a triaged blocker recorded in `knowledge/`).

## Phase 2 — Scheduled regression (mechanics BUILT — fence pending coverage)

**Goal:** once coverage is at 100%, the suite becomes a **regression fence**:
the whole surface (or a scoped slice) re-runs on a schedule and only *new*
breakage alarms (baseline + known-issues muting already exist).

- ✅ **Named suites** (`suites/*.yaml`: smoke/full/full-heavy/conformance) and
  **environment profiles** (`environments/*.yaml`) are first-class (M0) — a run
  is suite × profile, per-environment baselines are file-suffixed
  (`core/baselines.py`), and multi-tenancy columns exist (owner-confirmed
  requirement).
- ✅ **The scheduler lives in the control plane** (`controlplane/scheduler.py`:
  cron(UTC) × suite × profile, 30s polling daemon) rather than Actions
  `schedule:` — Actions cron stays deliberately removed; the platform owns
  "what runs when".
- Full vs partial runs are already expressible: `--category` / `--service`
  scoping, `crud_filter`, and the lane definitions in
  `regression/scenarios/dependencies.json` (`vpc_schedule.lanes`).
- Widen beyond "does it 200": more parameter combinations per endpoint —
  this is M5 **R4** (`vary:` option variants), the successor of
  `knowledge/formal/combo-scenarios.yaml`.
- **Done when:** a scheduled run needs no human babysitting — quota skips,
  baseline muting and tag-scoped cleanup keep it green unless the API really
  regressed.

## Phase 3 — Run beyond GitHub Actions (BUILT — cutover is the LAST step, M4)

**Goal:** the same suite runs on a **dedicated server** (customer network /
VPN-only gateways / longer budgets), not only on GitHub-hosted runners.

The architecture already keeps this cheap: everything is `python -m …` CLI
entrypoints + env vars; GitHub Actions is just one orchestrator shell.

- **Step 1 — self-hosted runner:** register a self-hosted GitHub runner on the
  server; the existing workflow runs unchanged (`runs-on` switch).
- ✅ **Step 2 — standalone runner: built.** `runner/worker.py` consumes the
  control plane's run queue and executes the same sequence — spec refresh →
  regression (A∥B) → sweep → conformance → dashboard/snapshot — with offline
  tests (`runner/tests_offline.py`). Packaging: `Dockerfile` +
  `docker-compose.yml` (server + worker + shared repo volume), runbook in
  `docs/DEPLOY.md`. Executor switch: `PLATFORM_EXECUTOR=actions|worker`.
- **Remaining:** the actual cutover (flip to `worker`, Docker/compose
  verification on a real host) — deliberately scheduled **after** the M5/R3
  live-verification work, per `docs/PLATFORM-PLAN.md` M4.
- **Done when:** one documented command provisions a fresh server to run the
  nightly schedule and publish the dashboard. (The command exists —
  `docker compose up` per `docs/DEPLOY.md`; it needs live verification.)

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
- **Formalized, human-editable form: [`knowledge/formal/`](knowledge/formal/)**
  — YAML files a human edits, a validator cross-checks against the engine data
  (`python knowledge/formal/validate.py`).
- ✅ **The "formal files become the source of truth" direction has landed as the
  M5 resource-task model**: `knowledge/formal/resources/*.yaml` (128 nodes) is
  the formal destination from which `regression/scenarios/composer.py`
  *generates* engine lifecycles. R1 (model + reverse-extraction) and R2
  (composer, live-proven) are done; R3 (replace hand-written lifecycles after
  per-node live verification) is in progress; R4 (C4 option variants) pends.
  See `docs/RESOURCE-MODEL-PLAN.md`.

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
