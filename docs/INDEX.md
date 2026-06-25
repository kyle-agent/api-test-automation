# docs/ — index

> **Generated** by `python -m tools.gen_index` from each doc's front-matter (`status` / `for`) + H1 title. Do not hand-edit — edit the doc and regenerate.
> 50 docs · 34 active · status ∈ {🟢 active · 🟡 draft · ⛔ blocked · ⚪ superseded}.

## Design & specs — `docs/` root (stable)
| Doc | For | Summary | Status |
|-----|-----|---------|--------|
| [`ARCHITECTURE.md`](ARCHITECTURE.md) | all | SCP API Regression Test Platform — Architecture | 🟢 active |
| [`COVERAGE-CRITERIA.md`](COVERAGE-CRITERIA.md) | all | Coverage criteria — what does "100%" mean? | 🟢 active |
| [`DEPLOY.md`](DEPLOY.md) | human-ops | 호스트 불문 단일 패키지 배포 runbook (M4) | 🟢 active |
| [`IA.md`](IA.md) | all | one-graph / step-overlay console (v3, 2026-06-17) | 🟢 active |
| [`M6-DESIGN.md`](M6-DESIGN.md) | all | M6 설계 — 자율 운영 가능한 SCP API 회귀 테스트 플랫폼 | 🟢 active |
| [`OPS-DASHBOARD.md`](OPS-DASHBOARD.md) | human-ops | Ops dashboard — 영구 oplog 버킷(apitest-oplog-permanent) + 정적 뷰어 | 🟢 active |
| [`PLATFORM-PLAN.md`](PLATFORM-PLAN.md) | all | SCP API Regression Test Platform — 업그레이드 계획 | 🟢 active |
| [`RESOURCE-MODEL-PLAN.md`](RESOURCE-MODEL-PLAN.md) | all | 자원 모델 기반 시나리오 합성 (Resource Task Model) — 설계 | 🟢 active |
| [`ROADMAP.md`](ROADMAP.md) | all | where this project is going | 🟢 active |
| [`agent-team.md`](agent-team.md) | all | The Agent Team — design & operating model | 🟢 active |
| [`lessons.md`](lessons.md) | all | Lessons | 🟢 active |
| [`quotas-and-budgets.md`](quotas-and-budgets.md) | all | Quotas and Budgets | 🟢 active |
| [`scheduler-system.md`](scheduler-system.md) | all | Dependency-DAG scheduler + self-learning optimizer | 🟢 active |

## Working — current state
| Doc | For | Summary | Status |
|-----|-----|---------|--------|
| [`working/CONTEXT.md`](working/CONTEXT.md) | orchestrator | Shared Context (CONTEXT.md) | 🟢 active |
| [`working/console-platform-handoff.md`](working/console-platform-handoff.md) | all | Platform Console — 로컬 실행 플랫폼 핸드오프 | 🟢 active |
| [`working/console2-ia-ux-review.md`](working/console2-ia-ux-review.md) | all | console2 — IA + UX Review (design backlog) |  backlog |
| [`working/coverage-session-brief.md`](working/coverage-session-brief.md) | all | Coverage session — handoff brief | 🟢 active |

## Working — plans
| Doc | For | Summary | Status |
|-----|-----|---------|--------|
| [`working/plans/COVERAGE-GETID-PLAN.md`](working/plans/COVERAGE-GETID-PLAN.md) | coverage | the id-bound GET gap, classified and attacked | 🟢 active |
| [`working/plans/COVERAGE-WAVE-PLAN.md`](working/plans/COVERAGE-WAVE-PLAN.md) | coverage | the remaining static gap, prioritized | 🟢 active |
| [`working/plans/PARALLEL-EXECUTION-PLAN.md`](working/plans/PARALLEL-EXECUTION-PLAN.md) | all | Parallel execution plan — staged foundations + per-VPC lanes (DRAFT) | 🟡 draft |
| [`working/plans/PLATFORM-CONVERGENCE.md`](working/plans/PLATFORM-CONVERGENCE.md) | owner + platform | 수렴 계획 — console2 → controlplane (척추 흡수) |  draft (오너 결정 반영 — 2026-06-25) |
| [`working/plans/PLATFORM-IA-DIRECTION.md`](working/plans/PLATFORM-IA-DIRECTION.md) | owner + platform | 플랫폼 방향성 — 쉽게 정리 |  draft (오너 논의용 — 2026-06-25, 실제 코드 확인 후 정정) |
| [`working/plans/PROBE-READS-PLAN.md`](working/plans/PROBE-READS-PLAN.md) | coverage | where the probe-read principle still needs applying | 🟢 active |
| [`working/plans/COVERAGE-MAX-PLAN.md`](working/plans/COVERAGE-MAX-PLAN.md) | coverage | how to drive C3 coverage to its ceiling | ⚪ superseded |

## Working — handoffs
| Doc | For | Summary | Status |
|-----|-----|---------|--------|
| [`working/handoffs/HANDOFF-2026-06-19-coverage-and-watcher.md`](working/handoffs/HANDOFF-2026-06-19-coverage-and-watcher.md) | all | Handoff — 2026-06-19 (Claude remote): coverage push, per-service agents, live-watcher | 🟢 active |
| [`working/handoffs/HANDOFF-2026-06-19-platform-and-coverage.md`](working/handoffs/HANDOFF-2026-06-19-platform-and-coverage.md) | all | Handoff — 2026-06-19 (session 2): platform fixes + coverage round | 🟢 active |
| [`working/handoffs/HANDOFF-2026-06-20-cutover-validation.md`](working/handoffs/HANDOFF-2026-06-20-cutover-validation.md) | all | Handoff — 2026-06-20: scheduler v0.5 cutover LIVE-VALIDATED + path to 1.0 | 🟢 active |
| [`working/handoffs/SESSION-HANDOFF.md`](working/handoffs/SESSION-HANDOFF.md) | all | SESSION HANDOFF — 2026-06-17 ~01:00 UTC | 🟢 active |
| [`working/handoffs/HANDOFF-2026-06-18-claude-remote.md`](working/handoffs/HANDOFF-2026-06-18-claude-remote.md) | all | Handoff — 2026-06-18 (→ next session: Claude remote, env vars set) | ⚪ superseded |
| [`working/handoffs/HANDOFF-2026-06-18-session2.md`](working/handoffs/HANDOFF-2026-06-18-session2.md) | all | Handoff — 2026-06-18 session 2 (Claude remote, hand-driven runs) | ⚪ superseded |
| [`working/handoffs/HANDOFF-crud-setter-validation.md`](working/handoffs/HANDOFF-crud-setter-validation.md) | all | Handoff — CRUD setter validation (PR #44, branch `claude/trusting-curie-Ql75T`) | ⚪ superseded |
| [`working/handoffs/HANDOFF-fail-new-triage.md`](working/handoffs/HANDOFF-fail-new-triage.md) | all | HANDOFF — fail_new triage (full heavy run 2026-06-10) | ⚪ superseded |
| [`working/handoffs/HANDOFF-waveA1-dispatch-prep.md`](working/handoffs/HANDOFF-waveA1-dispatch-prep.md) | all | HANDOFF — VALIDATION-QUEUE Wave A.1 light-batch dispatch prep | ⚪ superseded |
| [`working/handoffs/SESSION-HANDOFF-parallel-crud.md`](working/handoffs/SESSION-HANDOFF-parallel-crud.md) | all | Session handoff — parallel-adopt CRUD re-architecture | ⚪ superseded |
| [`working/handoffs/SESSION-HANDOFF-run6-and-ops.md`](working/handoffs/SESSION-HANDOFF-run6-and-ops.md) | all | SESSION HANDOFF — 측정 런 #6 재개 + ops 대시보드 (2026-06-11) | ⚪ superseded |

## Working — trackers
| Doc | For | Summary | Status |
|-----|-----|---------|--------|
| [`working/trackers/CATALOG-VALIDATION-STATUS.md`](working/trackers/CATALOG-VALIDATION-STATUS.md) | coverage | 검증 되었는지 보고 (verification track) | 🟢 active |
| [`working/trackers/COVERAGE-C3-ANALYSIS-2026-06-20.md`](working/trackers/COVERAGE-C3-ANALYSIS-2026-06-20.md) | coverage | C3 Coverage Analysis & Plan — 2026-06-20 | 🟢 active |
| [`working/trackers/IMPROVEMENT-BACKLOG.md`](working/trackers/IMPROVEMENT-BACKLOG.md) | orchestrator | Planner가 유지하는 개선 계획 | 🟢 active |
| [`working/trackers/LIVE-READINESS-GATES.md`](working/trackers/LIVE-READINESS-GATES.md) | all | disabled-lifecycle inventory (IB-023) | 🟢 active |
| [`working/trackers/POSTRUN-2026-06-20-fullheavy.md`](working/trackers/POSTRUN-2026-06-20-fullheavy.md) | all | Post-run Analysis: Full Heavy DAG Run — 2026-06-20 | 🟢 active |
| [`working/trackers/PRODUCT-FINDINGS.md`](working/trackers/PRODUCT-FINDINGS.md) | validation | consolidated ledger of product/API findings | 🟢 active |
| [`working/trackers/READ-REACHABILITY.md`](working/trackers/READ-REACHABILITY.md) | coverage | id-bound GET reachability from the resource model | 🟢 active |
| [`working/trackers/SERVICE-GAP-REPORTS.md`](working/trackers/SERVICE-GAP-REPORTS.md) | coverage | 서비스별 커버리지 갭 리포트 (병렬 agent 분석, 2026-06-13) | 🟢 active |
| [`working/trackers/VALIDATION-QUEUE.md`](working/trackers/VALIDATION-QUEUE.md) | validation | prioritized order for the coverage-validator | 🟢 active |
| [`working/trackers/harness-tests.md`](working/trackers/harness-tests.md) | all | Harness adversarial safety-rail tests (Tier-0) | 🟢 active |
| [`working/trackers/run-parallelism-optimization.md`](working/trackers/run-parallelism-optimization.md) | all | Heavy-run wall-clock optimization (2026-06-19) | 🟢 active |
| [`working/trackers/SECOND-ACCOUNT-BACKLOG.md`](working/trackers/SECOND-ACCOUNT-BACKLOG.md) | all | 2번째 계정 대기 백로그 (owner: "계정 만들고 알려줄께" — 2026-06-13) | ⛔ blocked |
| [`working/trackers/COVERAGE-GAP-REPORT-2026-06-18.md`](working/trackers/COVERAGE-GAP-REPORT-2026-06-18.md) | all | COVERAGE-GAP-REPORT — what is currently NOT covered (C3 gap) | ⚪ superseded |

## Decisions (ADR)
| Doc | For | Summary | Status |
|-----|-----|---------|--------|
| [`decisions/2026-06-19-dependency-dag-test-scheduler.md`](decisions/2026-06-19-dependency-dag-test-scheduler.md) | all | Dependency-DAG test scheduler (replacing the xdist 2-lane split) | 🟢 accepted |
| [`decisions/2026-06-20-docs-restructure.md`](decisions/2026-06-20-docs-restructure.md) | all | Restructure docs/: carve out a working/ tier (keep stable specs at root); dissolve the bespoke agents/ dir into .claude/agents | 🟢 accepted |
