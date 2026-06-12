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

## Plans / standards (adopted unless marked draft)

| Doc | Topic | Status |
|-----|-------|--------|
| [`PLATFORM-PLAN.md`](PLATFORM-PLAN.md) | The platform upgrade plan: control plane + execution plane, milestones M0–M5 | **adopted** — M0–M3 done, M4 built (cutover last), M5 R3 in progress |
| [`RESOURCE-MODEL-PLAN.md`](RESOURCE-MODEL-PLAN.md) | M5 resource-task model → composer (scenarios generated from the model); §6 = live wave findings | **adopted** — R1·R2 done, R3 waves live |
| [`COVERAGE-CRITERIA.md`](COVERAGE-CRITERIA.md) | The C0–C4 coverage ladder + waiver mechanism — what "100%" means | **adopted** (2026-06-09) |
| [`DEPLOY.md`](DEPLOY.md) | Operations runbook: Docker Compose bundle, executor switch, host migration (M4) | **adopted** — awaiting live/docker verification |
| [`OPS-DASHBOARD.md`](OPS-DASHBOARD.md) | 영구 oplog 버킷 + 정적 ops 뷰어 (의존순서 라이브 자원 트리 · run 필터 · verdict) | active |
| [`PARALLEL-EXECUTION-PLAN.md`](PARALLEL-EXECUTION-PLAN.md) | Staged foundations + per-VPC lanes — cut wall-clock to max(lane) instead of sum | **draft** (현 구현 A∥B split + shared adopt가 부분 반영; 전면 일반화는 미승인) |

## Handoffs

| Doc | Topic | Status |
|-----|-------|--------|
| [`HANDOFF-crud-setter-validation.md`](HANDOFF-crud-setter-validation.md) | CRUD write/setter coverage validation (PR #44): env constraints, failures, next steps | **superseded** — by the write-coverage campaign + fail_new triage + M5 waves |
| [`SESSION-HANDOFF-parallel-crud.md`](SESSION-HANDOFF-parallel-crud.md) | Parallel-adopt CRUD re-architecture (shared VPC + subnet adoption) | **done** — merged (PR #49–52), live-proven (A∥B full runs) |
| [`HANDOFF-fail-new-triage.md`](HANDOFF-fail-new-triage.md) | The 52 fail_new of the 2026-06-10 full heavy run, classified (body-fix vs domain-hunt vs known-red candidates) | **done** — fail_new 0 policy holding since; residual levers carried into `COVERAGE-WAVE-PLAN.md` |
| [`SESSION-HANDOFF-run6-and-ops.md`](SESSION-HANDOFF-run6-and-ops.md) | 측정 런 #6 재개 절차 + peering 근원수정 + ops 대시보드 인수인계 | **done** — run #6 landed (27329026254), ops viewer live on Pages |
| [`COVERAGE-WAVE-PLAN.md`](COVERAGE-WAVE-PLAN.md) | 잔여 정적 갭 전수 분석 (write 32 = 전부 waived/disabled) + 다음 웨이브 (DBaaS 윈도우 prep ①, servicewatch ③ done, eventstreams ④ partial) | active — 신규 커버리지 웨이브는 M5 합성 경로(`crud_filter=gen-wave`, RESOURCE-MODEL-PLAN §6)로 진행 |
