# docs/ — index

> **Generated** by `python -m tools.gen_index` from each doc's front-matter (`status` / `for`) + H1 title. Do not hand-edit — edit the doc and regenerate.
> 56 docs · 18 active · status ∈ {🟢 active · 🟡 draft · ⛔ blocked · ⚪ superseded}.

## Design & specs — `docs/` root (stable)
| Doc | For | Summary | Status |
|-----|-----|---------|--------|
| [`ARCHITECTURE.md`](ARCHITECTURE.md) | all | SCP API Regression Test Platform — Architecture | 🟢 active |
| [`COVERAGE-CRITERIA.md`](COVERAGE-CRITERIA.md) | all | Coverage criteria — what does "100%" mean? | 🟢 active |
| [`DEPLOY.md`](DEPLOY.md) | human-ops | 호스트 불문 단일 패키지 배포 runbook (M4) | 🟢 active |
| [`IA.md`](IA.md) | all | one-graph / step-overlay console (v3, 2026-06-17) |  SUPERSEDED (2026-06-26 owner decision) |
| [`M6-DESIGN.md`](M6-DESIGN.md) | all | M6 설계 — 자율 운영 가능한 SCP API 회귀 테스트 플랫폼 |  superseded (내구 결정은 ARCHITECTURE.md §Autonomy design으로 병합 — 2026-07-04) |
| [`OPS-DASHBOARD.md`](OPS-DASHBOARD.md) | human-ops | Ops dashboard — 영구 oplog 버킷(apitest-oplog-permanent) + 정적 뷰어 | 🟢 active |
| [`PLATFORM-PLAN.md`](PLATFORM-PLAN.md) | all | SCP API Regression Test Platform — 업그레이드 계획 | 🟢 active |
| [`RESOURCE-MODEL-PLAN.md`](RESOURCE-MODEL-PLAN.md) | all | 자원 모델 기반 시나리오 합성 (Resource Task Model) — 설계 | 🟢 active |
| [`ROADMAP.md`](ROADMAP.md) | all | where this project is going |  superseded (ARCHITECTURE.md §Direction으로 병합 — 2026-07-04) |
| [`agent-team.md`](agent-team.md) | all | The Agent Team — design & operating model | 🟢 active |
| [`lessons.md`](lessons.md) | all | Lessons | 🟢 active |
| [`quotas-and-budgets.md`](quotas-and-budgets.md) | all | Quotas and Budgets | 🟢 active |
| [`scheduler-system.md`](scheduler-system.md) | all | Dependency-DAG scheduler + self-learning optimizer | 🟢 active |

## Working — current state
| Doc | For | Summary | Status |
|-----|-----|---------|--------|
| [`working/CONTEXT.md`](working/CONTEXT.md) | orchestrator | Shared Context (CONTEXT.md) | 🟢 active |
| [`working/console-platform-handoff.md`](working/console-platform-handoff.md) | all | Platform Console — 로컬 실행 플랫폼 핸드오프 |  superseded (legacy poc console/console_server.py — 현행은 controlplane 척추 + console2) |
| [`working/console2-ia-ux-review.md`](working/console2-ia-ux-review.md) | all | console2 — IA + UX Review (design backlog) |  superseded (잔여 backlog는 UIUX-AUDIT-2026-07-03 §5로 이관) |
| [`working/coverage-session-brief.md`](working/coverage-session-brief.md) | all | Coverage session — handoff brief |  superseded (historical brief — the coverage campaign is now governed by docs/working/plans/CAMPAIGN-C3-100.md) |

## Working — plans
| Doc | For | Summary | Status |
|-----|-----|---------|--------|
| [`working/plans/CAMPAIGN-C3-100-docs-research.md`](working/plans/CAMPAIGN-C3-100-docs-research.md) | 워크스트림 A 후속 에이전트 (HB2/HB6/HB7 실행자) — body 초안 입력 | CAMPAIGN-C3-100 — body 미상 7건 docs-research |  DONE (docs-research, read-only) — 2026-07-04 |
| [`working/plans/CAMPAIGN-C3-100.md`](working/plans/CAMPAIGN-C3-100.md) | orchestrator + all campaign agents (다른 세션이 이어받을 때 이 문서가 진입점) | CAMPAIGN — C3 100% · 플랫폼 dogfood 개선 · 리포 정비 (3 워크스트림 병렬) |  ACTIVE campaign (2026-07-04, owner-directed autonomous run) |
| [`working/plans/COVERAGE-GETID-PLAN.md`](working/plans/COVERAGE-GETID-PLAN.md) | coverage | the id-bound GET gap, classified and attacked |  superseded (2026-06-12 스냅샷 플랜 — 커버리지 캠페인 정본으로 대체) |
| [`working/plans/COVERAGE-WAVE-PLAN.md`](working/plans/COVERAGE-WAVE-PLAN.md) | coverage | the remaining static gap, prioritized |  superseded (2026-06-11 스냅샷 플랜 — 커버리지 캠페인 정본으로 대체) |
| [`working/plans/IA-BUILD-CONTRACT.md`](working/plans/IA-BUILD-CONTRACT.md) | the IA-build agents (Catalog · Modeling · Reporting) + integration owner | IA build contract — "그림 하나, 여러 얼굴" parallel build |  superseded (이행 완료된 빌드 계약 — 확정 IA 빌드 완료) |
| [`working/plans/PARALLEL-EXECUTION-PLAN.md`](working/plans/PARALLEL-EXECUTION-PLAN.md) | all | Parallel execution plan — staged foundations + per-VPC lanes (DRAFT) |  superseded (드래프트 설계 — 1.0 의존-DAG 스케줄러로 실현됨) |
| [`working/plans/PLATFORM-CONVERGENCE.md`](working/plans/PLATFORM-CONVERGENCE.md) | owner + platform | 수렴 계획 — console2 → controlplane (척추 흡수) |  superseded (이행 완료된 수렴 계획 — console2가 척추에 흡수됨) |
| [`working/plans/PLATFORM-IA-DIRECTION.md`](working/plans/PLATFORM-IA-DIRECTION.md) | owner + platform | 플랫폼 방향성 — 쉽게 정리 |  CONFIRMED (오너 확정 — 2026-06-26 · IA = Catalog · Modeling · Testing · Reporting) |
| [`working/plans/PROBE-READS-PLAN.md`](working/plans/PROBE-READS-PLAN.md) | coverage | where the probe-read principle still needs applying |  superseded (2026-06-15 스냅샷 워크리스트 — 커버리지 캠페인 정본으로 대체) |
| [`working/plans/COVERAGE-MAX-PLAN.md`](working/plans/COVERAGE-MAX-PLAN.md) | coverage | how to drive C3 coverage to its ceiling | ⚪ superseded |

## Working — handoffs
| Doc | For | Summary | Status |
|-----|-----|---------|--------|
| [`working/handoffs/HANDOFF-2026-06-19-coverage-and-watcher.md`](working/handoffs/HANDOFF-2026-06-19-coverage-and-watcher.md) | all | Handoff — 2026-06-19 (Claude remote): coverage push, per-service agents, live-watcher |  superseded (historical handoff — current state lives in docs/working/CONTEXT.md) |
| [`working/handoffs/HANDOFF-2026-06-19-platform-and-coverage.md`](working/handoffs/HANDOFF-2026-06-19-platform-and-coverage.md) | all | Handoff — 2026-06-19 (session 2): platform fixes + coverage round |  superseded (historical handoff — current state lives in docs/working/CONTEXT.md) |
| [`working/handoffs/HANDOFF-2026-06-20-cutover-validation.md`](working/handoffs/HANDOFF-2026-06-20-cutover-validation.md) | all | Handoff — 2026-06-20: scheduler v0.5 cutover LIVE-VALIDATED + path to 1.0 |  superseded (historical handoff — current state lives in docs/working/CONTEXT.md) |
| [`working/handoffs/SESSION-HANDOFF.md`](working/handoffs/SESSION-HANDOFF.md) | all | SESSION HANDOFF — 2026-06-17 ~01:00 UTC |  superseded (historical handoff — current state lives in docs/working/CONTEXT.md) |
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
| [`working/trackers/CATALOG-VALIDATION-STATUS.md`](working/trackers/CATALOG-VALIDATION-STATUS.md) | coverage | 검증 되었는지 보고 (verification track) |  superseded (2026-06-17 측정 스냅샷 — 수치 stale; 재생성: python -m tools.catalog_status) |
| [`working/trackers/COVERAGE-C3-ANALYSIS-2026-06-20.md`](working/trackers/COVERAGE-C3-ANALYSIS-2026-06-20.md) | coverage | C3 Coverage Analysis & Plan — 2026-06-20 |  superseded (2026-06-20 분석 스냅샷 — 커버리지 캠페인 정본으로 대체) |
| [`working/trackers/IMPROVEMENT-BACKLOG.md`](working/trackers/IMPROVEMENT-BACKLOG.md) | orchestrator | Planner가 유지하는 개선 계획 | 🟢 active |
| [`working/trackers/LIVE-READINESS-GATES.md`](working/trackers/LIVE-READINESS-GATES.md) | all | disabled-lifecycle inventory (IB-023) | 🟢 active |
| [`working/trackers/POSTRUN-2026-06-20-fullheavy.md`](working/trackers/POSTRUN-2026-06-20-fullheavy.md) | orchestrator | Post-run Analysis: Full Heavy DAG Run — 2026-06-20 |  superseded (2026-06-20 run 1회분 사후 분석 스냅샷) |
| [`working/trackers/PRODUCT-FINDINGS.md`](working/trackers/PRODUCT-FINDINGS.md) | validation | consolidated ledger of product/API findings | 🟢 active |
| [`working/trackers/R3-WAVES-2026-06.md`](working/trackers/R3-WAVES-2026-06.md) | coverage | R3 검증 웨이브 — 라이브 결과 로그 (2026-06-12 현재) |  superseded (2026-06-12 웨이브 로그 스냅샷 — RESOURCE-MODEL-PLAN §6에서 이관) |
| [`working/trackers/READ-REACHABILITY.md`](working/trackers/READ-REACHABILITY.md) | coverage | id-bound GET reachability from the resource model |  superseded (2026-06-18 생성 리포트 — 재생성 가능: python -m spec.read_reachability) |
| [`working/trackers/REPO-AUDIT-2026-07-04.md`](working/trackers/REPO-AUDIT-2026-07-04.md) | orchestrator | 리포 하이진 감사 (C1, 2026-07-04) — 인벤토리 · retire 후보 · 문서 모순 · 진입점 | 🟢 active |
| [`working/trackers/SERVICE-GAP-REPORTS.md`](working/trackers/SERVICE-GAP-REPORTS.md) | coverage | 서비스별 커버리지 갭 리포트 (병렬 agent 분석, 2026-06-13) |  superseded (2026-06-13 분석 스냅샷 — 커버리지 캠페인 정본으로 대체) |
| [`working/trackers/UIUX-AUDIT-2026-07-03.md`](working/trackers/UIUX-AUDIT-2026-07-03.md) | all | SCP 컨트롤플레인 UI — IA/UX 감사 보고서 (2026-07-03) | 🟢 active |
| [`working/trackers/VALIDATION-QUEUE.md`](working/trackers/VALIDATION-QUEUE.md) | validation | prioritized order for the coverage-validator | 🟢 active |
| [`working/trackers/harness-tests.md`](working/trackers/harness-tests.md) | all | Harness adversarial safety-rail tests (Tier-0) | 🟢 active |
| [`working/trackers/run-parallelism-optimization.md`](working/trackers/run-parallelism-optimization.md) | all | Heavy-run wall-clock optimization (2026-06-19) |  superseded (2026-06-19 run 최적화 스냅샷 — DAG 스케줄러 정본으로 흡수) |
| [`working/trackers/SECOND-ACCOUNT-BACKLOG.md`](working/trackers/SECOND-ACCOUNT-BACKLOG.md) | all | 2번째 계정 대기 백로그 (owner: "계정 만들고 알려줄께" — 2026-06-13) | ⛔ blocked |
| [`working/trackers/COVERAGE-GAP-REPORT-2026-06-18.md`](working/trackers/COVERAGE-GAP-REPORT-2026-06-18.md) | all | COVERAGE-GAP-REPORT — what is currently NOT covered (C3 gap) | ⚪ superseded |

## Decisions (ADR)
| Doc | For | Summary | Status |
|-----|-----|---------|--------|
| [`decisions/2026-06-19-dependency-dag-test-scheduler.md`](decisions/2026-06-19-dependency-dag-test-scheduler.md) | all | Dependency-DAG test scheduler (replacing the xdist 2-lane split) | 🟢 accepted |
| [`decisions/2026-06-20-docs-restructure.md`](decisions/2026-06-20-docs-restructure.md) | all | Restructure docs/: carve out a working/ tier (keep stable specs at root); dissolve the bespoke agents/ dir into .claude/agents | 🟢 accepted |
