# docs/ — index

> **Generated** by `python -m tools.gen_index` from each doc's front-matter (`status` / `for`) + H1 title. Do not hand-edit — edit the doc and regenerate.
> 84 docs · 33 active · status ∈ {🟢 active · 🟡 draft · ⛔ blocked · ⚪ superseded}.

## Design & specs — `docs/` root (stable)
| Doc | For | Summary | Status |
|-----|-----|---------|--------|
| [`API-VERSIONING.md`](API-VERSIONING.md) | all | API Versioning (microversions) — design & roadmap | 🟢 active |
| [`ARCHITECTURE.md`](ARCHITECTURE.md) | all | SCP API Regression Test Platform — Architecture | 🟢 active |
| [`COVERAGE-CRITERIA.md`](COVERAGE-CRITERIA.md) | all | Coverage criteria — what does "100%" mean? | 🟢 active |
| [`DEPLOY.md`](DEPLOY.md) | human-ops | 호스트 불문 단일 패키지 배포 runbook (M4) | 🟢 active |
| [`HANDOVER-2026-07-29.md`](HANDOVER-2026-07-29.md) | all | 인수인계 — SCP API 회귀 테스트 플랫폼 (2026-07-29 기준) | 🟢 active |
| [`OPS-DASHBOARD.md`](OPS-DASHBOARD.md) | human-ops | Ops dashboard — 영구 oplog 버킷(apitest-oplog-permanent) + 정적 뷰어 | 🟢 active |
| [`PLATFORM-PLAN.md`](PLATFORM-PLAN.md) | all | SCP API Regression Test Platform — 업그레이드 계획 | 🟢 active |
| [`QUALITY-REPORT.md`](QUALITY-REPORT.md) | human-ops | SCP API 품질점검 결과 리포트 — 생성·재검증 런북 (`conformance.quality_report`) | 🟢 active |
| [`RESOURCE-MODEL-PLAN.md`](RESOURCE-MODEL-PLAN.md) | all | 자원 모델 기반 시나리오 합성 (Resource Task Model) — 설계 | 🟢 active |
| [`agent-team.md`](agent-team.md) | all | The Agent Team — design & operating model | 🟢 active |
| [`lessons.md`](lessons.md) | all | Lessons | 🟢 active |
| [`scheduler-system.md`](scheduler-system.md) | all | Dependency-DAG scheduler + self-learning optimizer | 🟢 active |

## Working — current state
| Doc | For | Summary | Status |
|-----|-----|---------|--------|
| [`working/API-QUALITY-DEVTEAM-REPORT-2026-08-20.md`](working/API-QUALITY-DEVTEAM-REPORT-2026-08-20.md) | all | SCP API 품질점검 결과 — 2026-08-20 | 🟢 active |
| [`working/CONFORMANCE-QUALITY-REPORT-2026-08-20.md`](working/CONFORMANCE-QUALITY-REPORT-2026-08-20.md) | all | SCP API 품질 컨포먼스 리포트 — 2026-08-20 | 🟢 active |
| [`working/CONTEXT.md`](working/CONTEXT.md) | orchestrator | Shared Context (CONTEXT.md) | 🟢 active |
| [`working/DOCS-MCP-FEASIBILITY.md`](working/DOCS-MCP-FEASIBILITY.md) | all | SCP Docs MCP Server — 도입 타당성 조사 (2026-07-15) | 🟢 active |
| [`working/NEWAPI-DBAAS-INSTANCE-OPS.md`](working/NEWAPI-DBAAS-INSTANCE-OPS.md) | coverage-service (database/*, data-analytics/*) · coverage-validator · orchestrator | NEWAPI — DBaaS instance-ops 설계 메모 (2026-07-15) | 🟢 active |
| [`working/SPEC-DIFF-20260715.md`](working/SPEC-DIFF-20260715.md) | orchestrator | SPEC-DIFF — 전체 명세 리프레시 + diff (2026-07-15, 버전업 대응) | 🟢 active |

## Working — plans
| Doc | For | Summary | Status |
|-----|-----|---------|--------|
| [`working/plans/C4-PARAM-COVERAGE.md`](working/plans/C4-PARAM-COVERAGE.md) | all | C4 파라미터 커버리지 계획 (owner directive 2026-07-11) | 🟢 active |
| [`working/plans/CAMPAIGN-C3-100-docs-research.md`](working/plans/CAMPAIGN-C3-100-docs-research.md) | 워크스트림 A 후속 에이전트 (HB2/HB6/HB7 실행자) — body 초안 입력 | CAMPAIGN-C3-100 — body 미상 7건 docs-research |  DONE (docs-research, read-only) — 2026-07-04 |
| [`working/plans/CAMPAIGN-C3-100-repair-log.md`](working/plans/CAMPAIGN-C3-100-repair-log.md) | 워크스트림 A 오케스트레이터 — HB1b/HB2b crud_ids 구성 입력 | CAMPAIGN-C3-100 — 결정적 재실패 갭 수리 로그 (2026-07-04) |  DONE (repair pass, OFFLINE — no live SCP calls this session) — 2026-07-04 |
| [`working/plans/CAMPAIGN-C3-100-waivers.md`](working/plans/CAMPAIGN-C3-100-waivers.md) | 오너 — CAMPAIGN-C3-100 waiver 일괄 심사용 단일 문서 | CAMPAIGN-C3-100 — Waiver 제안 통합 (오너 결정용) |  PROPOSED (오너 결정 대기) — 2026-07-07, OFFLINE 세션 (라이브 호출 없음) |
| [`working/plans/CAMPAIGN-C3-100.md`](working/plans/CAMPAIGN-C3-100.md) | orchestrator + all campaign agents (다른 세션이 이어받을 때 이 문서가 진입점) | CAMPAIGN — C3 100% · 플랫폼 dogfood 개선 · 리포 정비 (3 워크스트림 병렬) |  ACTIVE campaign (2026-07-04, owner-directed autonomous run) |
| [`working/plans/CX-IA-DESIGN-2026-07-09.md`](working/plans/CX-IA-DESIGN-2026-07-09.md) | all | CX·IA 디자인 안 — 제3자 UX 컨설턴트 리뷰 (2026-07-09) | 🟢 active |
| [`working/plans/DYNAMIC-INJECTION.md`](working/plans/DYNAMIC-INJECTION.md) | orchestrator | 런 중 시나리오 동적 주입 (native_runner) — 설계 | 🟡 draft |
| [`working/plans/HEAVY-PREMISE-CONTRACT.md`](working/plans/HEAVY-PREMISE-CONTRACT.md) | all | HEAVY-PREMISE CONTRACT — Testing 단순화 (Model B) 공유 계약 | 🟢 active |
| [`working/plans/NATIVE-SCHEDULER.md`](working/plans/NATIVE-SCHEDULER.md) | orchestrator | 목적특화 스케줄러 (native_runner) — xdist 대체 | 🟡 draft |
| [`working/plans/OP-TIMINGS.md`](working/plans/OP-TIMINGS.md) | all | 오퍼레이션 타이밍 (per-API create/delete/update 실제 완료시간) | 🟢 active |
| [`working/plans/PLATFORM-IA-DIRECTION.md`](working/plans/PLATFORM-IA-DIRECTION.md) | owner + platform | 플랫폼 방향성 — 쉽게 정리 |  CONFIRMED (오너 확정 — 2026-06-26 · IA = Catalog · Modeling · Testing · Reporting · **2026-07-07 개정: Catalog→우측 유틸 링크, Modeling이 흡수 — §개정 참조**) |
| [`working/plans/V2-KICKOFF.md`](working/plans/V2-KICKOFF.md) | v2-session (별도 세션 전담) | V2 킥오프 브리프 — B안(재구조화)을 /v2 스트랭글러로 | 🟢 active |

## Working — trackers
| Doc | For | Summary | Status |
|-----|-----|---------|--------|
| [`working/trackers/COMPLETENESS-AUDIT-2026-07-08.md`](working/trackers/COMPLETENESS-AUDIT-2026-07-08.md) | owner | Completeness Audit — 서비스별 catalog 전량이 시나리오 합집합에서 테스트되는가 (2026-07-08) |  active (2026-07-08 오프라인 정적 감사 — owner 편입 worklist) |
| [`working/trackers/DEDUP-AUDIT-2026-07-08.md`](working/trackers/DEDUP-AUDIT-2026-07-08.md) | owner | DEDUP Audit — 단위 서비스 선택 시 lifecycle 최소중복 전수 확인 (2026-07-08) |  done (오너 결정 2026-07-08 반영·집행 — §B "모두 추천 방향으로" + §C ①pg 2기 현행 유지 ②members-nat 은퇴 ③gen-wave2-scr 은퇴 ④gen-wave2-volume 은퇴 ⑤shared-dbaas 현행 유지; status 정정 2026-07-29) |
| [`working/trackers/IMPROVEMENT-BACKLOG.md`](working/trackers/IMPROVEMENT-BACKLOG.md) | orchestrator | Planner가 유지하는 개선 계획 | 🟢 active |
| [`working/trackers/LIVE-READINESS-GATES.md`](working/trackers/LIVE-READINESS-GATES.md) | all | disabled-lifecycle inventory (IB-023) | 🟢 active |
| [`working/trackers/PRODUCT-FINDINGS.md`](working/trackers/PRODUCT-FINDINGS.md) | validation | consolidated ledger of product/API findings | 🟢 active |
| [`working/trackers/REPO-AUDIT-2026-07-04.md`](working/trackers/REPO-AUDIT-2026-07-04.md) | orchestrator | 리포 하이진 감사 (C1, 2026-07-04) — 인벤토리 · retire 후보 · 문서 모순 · 진입점 | 🟢 active |
| [`working/trackers/SERVICE-OPT-CAMPAIGN.md`](working/trackers/SERVICE-OPT-CAMPAIGN.md) | all | 서비스별 단독 실행 최적화 캠페인 (2026-07-11 시작) | 🟢 active |
| [`working/trackers/UIUX-AUDIT-2026-07-03.md`](working/trackers/UIUX-AUDIT-2026-07-03.md) | all | SCP 컨트롤플레인 UI — IA/UX 감사 보고서 (2026-07-03) | 🟢 active |
| [`working/trackers/V1-GRAFT.md`](working/trackers/V1-GRAFT.md) | design-improvements-v1 세션 (v2 → v1 접목) | V1 접목 트래커 — v2에서 검증된 것만 v1에 얹는다 | 🟢 active |
| [`working/trackers/VALIDATION-QUEUE.md`](working/trackers/VALIDATION-QUEUE.md) | validation | prioritized order for the coverage-validator | 🟢 active |
| [`working/trackers/harness-tests.md`](working/trackers/harness-tests.md) | all | Harness adversarial safety-rail tests (Tier-0) | 🟢 active |
| [`working/trackers/SECOND-ACCOUNT-BACKLOG.md`](working/trackers/SECOND-ACCOUNT-BACKLOG.md) | all | 2번째 계정 대기 백로그 (owner: "계정 만들고 알려줄께" — 2026-06-13) | ⛔ blocked |

## Decisions (ADR)
| Doc | For | Summary | Status |
|-----|-----|---------|--------|
| [`decisions/2026-06-19-dependency-dag-test-scheduler.md`](decisions/2026-06-19-dependency-dag-test-scheduler.md) | all | Dependency-DAG test scheduler (replacing the xdist 2-lane split) | 🟢 accepted |
| [`decisions/2026-06-20-docs-restructure.md`](decisions/2026-06-20-docs-restructure.md) | all | Restructure docs/: carve out a working/ tier (keep stable specs at root); dissolve the bespoke agents/ dir into .claude/agents | 🟢 accepted |
| [`decisions/2026-07-29-repo-cleanup-archive-tier.md`](decisions/2026-07-29-repo-cleanup-archive-tier.md) | all | Repo cleanup: docs/archive tier + retirement of poc and dead tools | 🟢 accepted |

## Archive — frozen history (`docs/archive/`, 정본 아님)
| Doc | For | Summary | Status |
|-----|-----|---------|--------|
| [`archive/CONTEXT-history.md`](archive/CONTEXT-history.md) | all | CONTEXT history — 과거 세션 로그 (2026-06-10 ~ 2026-07-13, frozen) |  superseded (CONTEXT.md 과거 로그 아카이브 — 현재 상태는 docs/working/CONTEXT.md) |
| [`archive/IA.md`](archive/IA.md) | all | one-graph / step-overlay console (v3, 2026-06-17) |  SUPERSEDED (2026-06-26 owner decision) |
| [`archive/M6-DESIGN.md`](archive/M6-DESIGN.md) | all | M6 설계 — 자율 운영 가능한 SCP API 회귀 테스트 플랫폼 |  superseded (내구 결정은 ARCHITECTURE.md §Autonomy design으로 병합 — 2026-07-04) |
| [`archive/README.md`](archive/README.md) | all | docs/archive/ — superseded history (frozen) | 🟢 active |
| [`archive/ROADMAP.md`](archive/ROADMAP.md) | all | where this project is going |  superseded (ARCHITECTURE.md §Direction으로 병합 — 2026-07-04) |
| [`archive/console-platform-handoff.md`](archive/console-platform-handoff.md) | all | Platform Console — 로컬 실행 플랫폼 핸드오프 |  superseded (legacy poc console/console_server.py — 현행은 controlplane 척추 + console2) |
| [`archive/console2-ia-ux-review.md`](archive/console2-ia-ux-review.md) | all | console2 — IA + UX Review (design backlog) |  superseded (잔여 backlog는 UIUX-AUDIT-2026-07-03 §5로 이관) |
| [`archive/coverage-session-brief.md`](archive/coverage-session-brief.md) | all | Coverage session — handoff brief |  superseded (historical brief — the coverage campaign is now governed by docs/working/plans/CAMPAIGN-C3-100.md) |
| [`archive/handoffs/HANDOFF-2026-06-19-coverage-and-watcher.md`](archive/handoffs/HANDOFF-2026-06-19-coverage-and-watcher.md) | all | Handoff — 2026-06-19 (Claude remote): coverage push, per-service agents, live-watcher |  superseded (historical handoff — current state lives in docs/working/CONTEXT.md) |
| [`archive/handoffs/HANDOFF-2026-06-19-platform-and-coverage.md`](archive/handoffs/HANDOFF-2026-06-19-platform-and-coverage.md) | all | Handoff — 2026-06-19 (session 2): platform fixes + coverage round |  superseded (historical handoff — current state lives in docs/working/CONTEXT.md) |
| [`archive/handoffs/HANDOFF-2026-06-20-cutover-validation.md`](archive/handoffs/HANDOFF-2026-06-20-cutover-validation.md) | all | Handoff — 2026-06-20: scheduler v0.5 cutover LIVE-VALIDATED + path to 1.0 |  superseded (historical handoff — current state lives in docs/working/CONTEXT.md) |
| [`archive/handoffs/HANDOFF-2026-06-25-abc-tracks.md`](archive/handoffs/HANDOFF-2026-06-25-abc-tracks.md) | all | HANDOFF — 2026-06-25 EOD · A/B/C execution session |  superseded (historical handoff — current state lives in docs/working/CONTEXT.md) |
| [`archive/handoffs/SESSION-HANDOFF.md`](archive/handoffs/SESSION-HANDOFF.md) | all | SESSION HANDOFF — 2026-06-17 ~01:00 UTC |  superseded (historical handoff — current state lives in docs/working/CONTEXT.md) |
| [`archive/plans/COVERAGE-GETID-PLAN.md`](archive/plans/COVERAGE-GETID-PLAN.md) | coverage | the id-bound GET gap, classified and attacked |  superseded (2026-06-12 스냅샷 플랜 — 커버리지 캠페인 정본으로 대체) |
| [`archive/plans/COVERAGE-TO-100-2026-06-25.md`](archive/plans/COVERAGE-TO-100-2026-06-25.md) | all | Coverage → 100% master plan (2026-06-25) |  superseded (2026-06-25 스냅샷 플랜 — CAMPAIGN-C3-100이 대체) |
| [`archive/plans/COVERAGE-WAVE-PLAN.md`](archive/plans/COVERAGE-WAVE-PLAN.md) | coverage | the remaining static gap, prioritized |  superseded (2026-06-11 스냅샷 플랜 — 커버리지 캠페인 정본으로 대체) |
| [`archive/plans/IA-BUILD-CONTRACT.md`](archive/plans/IA-BUILD-CONTRACT.md) | the IA-build agents (Catalog · Modeling · Reporting) + integration owner | IA build contract — "그림 하나, 여러 얼굴" parallel build |  superseded (이행 완료된 빌드 계약 — 확정 IA 빌드 완료) |
| [`archive/plans/PARALLEL-EXECUTION-PLAN.md`](archive/plans/PARALLEL-EXECUTION-PLAN.md) | all | Parallel execution plan — staged foundations + per-VPC lanes (DRAFT) |  superseded (드래프트 설계 — 1.0 의존-DAG 스케줄러로 실현됨) |
| [`archive/plans/PLATFORM-CONVERGENCE.md`](archive/plans/PLATFORM-CONVERGENCE.md) | owner + platform | 수렴 계획 — console2 → controlplane (척추 흡수) |  superseded (이행 완료된 수렴 계획 — console2가 척추에 흡수됨) |
| [`archive/plans/PROBE-READS-PLAN.md`](archive/plans/PROBE-READS-PLAN.md) | coverage | where the probe-read principle still needs applying |  superseded (2026-06-15 스냅샷 워크리스트 — 커버리지 캠페인 정본으로 대체) |
| [`archive/scenario-viz-PLATFORM-PLAN.md`](archive/scenario-viz-PLATFORM-PLAN.md) | all | 실제 플랫폼 반영 계획 — 합성 시나리오 그래프 UI |  superseded (poc/scenario-viz 은퇴와 함께 아카이브 — 확정 IA 정본은 docs/working/plans/PLATFORM-IA-DIRECTION.md §확정IA) |
| [`archive/terraform-api-coverage-gap-2026-06-09.md`](archive/terraform-api-coverage-gap-2026-06-09.md) | all | Samsung Cloud Platform v2 — API ↔ Terraform Provider 커버리지 갭 상세 리포트 |  superseded (2026-06-09 일자 분석 스냅샷 — 카탈로그는 이후 1,416으로 버전업) |
| [`archive/trackers/CATALOG-VALIDATION-STATUS.md`](archive/trackers/CATALOG-VALIDATION-STATUS.md) | coverage | 검증 되었는지 보고 (verification track) |  superseded (2026-06-17 측정 스냅샷 — 수치 stale; 재생성: python -m tools.catalog_status) |
| [`archive/trackers/COVERAGE-C3-ANALYSIS-2026-06-20.md`](archive/trackers/COVERAGE-C3-ANALYSIS-2026-06-20.md) | coverage | C3 Coverage Analysis & Plan — 2026-06-20 |  superseded (2026-06-20 분석 스냅샷 — 커버리지 캠페인 정본으로 대체) |
| [`archive/trackers/POSTRUN-2026-06-20-fullheavy.md`](archive/trackers/POSTRUN-2026-06-20-fullheavy.md) | orchestrator | Post-run Analysis: Full Heavy DAG Run — 2026-06-20 |  superseded (2026-06-20 run 1회분 사후 분석 스냅샷) |
| [`archive/trackers/R3-WAVES-2026-06.md`](archive/trackers/R3-WAVES-2026-06.md) | coverage | R3 검증 웨이브 — 라이브 결과 로그 (2026-06-12 현재) |  superseded (2026-06-12 웨이브 로그 스냅샷 — RESOURCE-MODEL-PLAN §6에서 이관) |
| [`archive/trackers/READ-REACHABILITY.md`](archive/trackers/READ-REACHABILITY.md) | coverage | id-bound GET reachability from the resource model |  superseded (2026-06-18 생성 리포트 — 재생성 가능: python -m spec.read_reachability) |
| [`archive/trackers/SERVICE-GAP-REPORTS.md`](archive/trackers/SERVICE-GAP-REPORTS.md) | coverage | 서비스별 커버리지 갭 리포트 (병렬 agent 분석, 2026-06-13) |  superseded (2026-06-13 분석 스냅샷 — 커버리지 캠페인 정본으로 대체) |
| [`archive/trackers/SPEC-DIFF-2026-07-09.md`](archive/trackers/SPEC-DIFF-2026-07-09.md) | orchestrator | SPEC-DIFF — 카탈로그 리프레시 + diff (2026-07-09) |  superseded (2026-07-15 버전업 diff가 대체 — working/SPEC-DIFF-20260715.md) |
| [`archive/trackers/run-parallelism-optimization.md`](archive/trackers/run-parallelism-optimization.md) | all | Heavy-run wall-clock optimization (2026-06-19) |  superseded (2026-06-19 run 최적화 스냅샷 — DAG 스케줄러 정본으로 흡수) |
| [`archive/handoffs/HANDOFF-2026-06-18-claude-remote.md`](archive/handoffs/HANDOFF-2026-06-18-claude-remote.md) | all | Handoff — 2026-06-18 (→ next session: Claude remote, env vars set) | ⚪ superseded |
| [`archive/handoffs/HANDOFF-2026-06-18-session2.md`](archive/handoffs/HANDOFF-2026-06-18-session2.md) | all | Handoff — 2026-06-18 session 2 (Claude remote, hand-driven runs) | ⚪ superseded |
| [`archive/handoffs/HANDOFF-crud-setter-validation.md`](archive/handoffs/HANDOFF-crud-setter-validation.md) | all | Handoff — CRUD setter validation (PR #44, branch `claude/trusting-curie-Ql75T`) | ⚪ superseded |
| [`archive/handoffs/HANDOFF-fail-new-triage.md`](archive/handoffs/HANDOFF-fail-new-triage.md) | all | HANDOFF — fail_new triage (full heavy run 2026-06-10) | ⚪ superseded |
| [`archive/handoffs/HANDOFF-waveA1-dispatch-prep.md`](archive/handoffs/HANDOFF-waveA1-dispatch-prep.md) | all | HANDOFF — VALIDATION-QUEUE Wave A.1 light-batch dispatch prep | ⚪ superseded |
| [`archive/handoffs/SESSION-HANDOFF-parallel-crud.md`](archive/handoffs/SESSION-HANDOFF-parallel-crud.md) | all | Session handoff — parallel-adopt CRUD re-architecture | ⚪ superseded |
| [`archive/handoffs/SESSION-HANDOFF-run6-and-ops.md`](archive/handoffs/SESSION-HANDOFF-run6-and-ops.md) | all | SESSION HANDOFF — 측정 런 #6 재개 + ops 대시보드 (2026-06-11) | ⚪ superseded |
| [`archive/plans/COVERAGE-MAX-PLAN.md`](archive/plans/COVERAGE-MAX-PLAN.md) | coverage | how to drive C3 coverage to its ceiling | ⚪ superseded |
| [`archive/trackers/COVERAGE-GAP-REPORT-2026-06-18.md`](archive/trackers/COVERAGE-GAP-REPORT-2026-06-18.md) | all | COVERAGE-GAP-REPORT — what is currently NOT covered (C3 gap) | ⚪ superseded |
