---
status: active
for: orchestrator
---

# 리포 하이진 감사 (C1, 2026-07-04) — 인벤토리 · retire 후보 · 문서 모순 · 진입점

> CAMPAIGN-C3-100 워크스트림 C 1차 산출물. 방법: 전 문서/모듈 인벤토리
> (`git log -1 --format=%cs` + 저장소 전역 참조 grep) → 죽은 파일 retire 후보
> (증거 첨부, **삭제 실행은 오케스트레이터 승인 후**) → 정본 체인 교차 검증
> (PLATFORM-IA-DIRECTION §확정IA > CONTEXT.md > README/START_HERE > INDEX >
> per-dir README) → C2 목표 문서 트리 제안. 소규모 무해 모순은 이 감사에서
> 직접 수정함 (§3 "✅ fixed" 표기, 같은 브랜치 커밋).

## 0. 요약 카운트

| 항목 | 값 |
|---|---|
| docs/*.md 총계 | **56** (이 감사 문서 포함; INDEX는 자기 제외 54행) |
| retire 후보 (소스/산출물) | **9 묶음** (§2 — 승인 대기) |
| supersede 처리한 문서 (삭제 아님, 헤더만) | **6** (§3) |
| 발견 모순/불일치 | **14** (§3) |
| 직접 수정 완료 | **10** (§3 ✅) / 미수정·보고만 4 |

## 1. 인벤토리

### 1a. docs/ (55 md) — 날짜 = 마지막 커밋, 참조 = 저장소 전역 grep(자기 제외)

| 문서 | 목적 (1줄) | 최종변경 | 참조 |
|---|---|---|---|
| ARCHITECTURE.md | 2축 엔진+커널 설계 청사진 | 06-20 | 6 |
| COVERAGE-CRITERIA.md | C1/C2/C3 커버리지 정의 정본 | 06-20 | 13 |
| DEPLOY.md | M4 Docker Compose 배포 runbook | 06-20 | 8 |
| IA.md | 구세대 one-graph IA — **SUPERSEDED** 헤더 있음 | 07-03 | 20 |
| INDEX.md | gen_index 생성 문서 색인 | 07-03 | 5 |
| M6-DESIGN.md | 자율 운영 플랫폼 M6 설계 | 06-20 | 4 |
| OPS-DASHBOARD.md | 영구 oplog 버킷 + ops.html 뷰어 | 06-20 | 4 |
| PLATFORM-PLAN.md | 플랫폼 마일스톤 M0–M5 | 07-02 | 26 |
| RESOURCE-MODEL-PLAN.md | M5 자원-태스크 모델 설계+웨이브 기록 | 06-20 | 128 |
| ROADMAP.md | 페이즈 로드맵 | 06-20 | 8 |
| agent-team.md | 에이전트 팀 로스터·하니스·안전레일 정본 | 06-20 | 36 |
| lessons.md | /retro 프로세스 교훈 | 06-20 | 4 |
| quotas-and-budgets.md | 계정 쿼터 모델링 | 06-20 | 6 |
| scheduler-system.md | DAG 스케줄러 설계 | 06-20 | 3 |
| decisions/ (ADR ×2) | DAG 스케줄러 · docs 재구조화 결정 | 06-20 | 6/1 |
| working/CONTEXT.md | 공유 현재 상태 (정본 2순위) | 07-04 | 23 |
| working/console-platform-handoff.md | 레거시 콘솔(port 9000) 핸드오프 → **이번에 SUPERSEDED 처리** | 06-22 | 2 |
| working/console2-ia-ux-review.md | console2 디자인 백로그 | 06-22 | 2 |
| working/coverage-session-brief.md | 06-23 커버리지 브리프 → **이번에 SUPERSEDED 처리** | 06-23 | 2 |
| working/handoffs/ ×11 | 세션 핸드오프 (역사) — 6 superseded 기존 + **4 이번에 superseded** + SESSION-HANDOFF | 06-20 | 1–7 |
| working/plans/ ×10 | CAMPAIGN-C3-100(정본, 07-04) · PLATFORM-IA-DIRECTION(§확정IA 정본) · IA-BUILD-CONTRACT · PLATFORM-CONVERGENCE(수렴 완료됨—후속 §4) · COVERAGE-{GETID,WAVE,MAX}-PLAN · PROBE-READS · PARALLEL-EXECUTION | 06-20~07-04 | 0–26 |
| working/trackers/ ×14 (+이 문서) | UIUX-AUDIT(활성) · IMPROVEMENT-BACKLOG · PRODUCT-FINDINGS · CATALOG-VALIDATION-STATUS(수치 stale, §3-C11) · VALIDATION-QUEUE · READ-REACHABILITY · LIVE-READINESS-GATES · harness-tests · POSTRUN/GAP-REPORT/C3-ANALYSIS(스냅샷) · SECOND-ACCOUNT(blocked) · run-parallelism | 06-20~07-04 | 1–13 |

### 1b. 소스 모듈 top-level

| 모듈 | 목적 | 최종변경 | 상태 |
|---|---|---|---|
| tools/console2_server.py | console2 백엔드(라이브러리化, controlplane이 위임) | 07-04 | **활성** (14 refs) |
| tools/console_server.py | 레거시 poc 콘솔 서버 (port 9000) | 06-22 | **retire 후보 R2** |
| tools/{derive_verified,promote_validated,new_service,retirement,coverage_headroom,catalog_status,gen_index,dag_run_live,live_watch,analyze_run}.py | 증거 승격·서비스 스캐폴드·색인·DAG 라이브·워처·run 분석 | 06-13~07-03 | 활성 (하니스/에이전트 참조) |
| tools/loop_cycle.py | 자율 루프 1사이클 헬퍼 | 06-20 | 저참조(1) — 관찰 |
| tools/sample_data.py | poc 콘솔용 샘플 데이터 | 06-17 | poc 전용 → R1 동반 |
| tools/*.sh (publish_live·publish_live_obs·publish_dagrun_live·rerun_when_clean) | 세션 1회성 게시/재시도 스크립트 | 06-18~21 | **retire 후보 R4** (참조 0) |
| tools/publish_dashboard.sh | 대시보드 수동 게시 | 06-18 | 참조 3 — 유지 |
| poc/scenario-viz/ (155 tracked) | IA 검증 POC → 플랫폼의 모태 | 06-14~07-04 | **retire 후보 R1** (단 CI 경로 잔존!) |
| dashboard/{build.py,gen_dep_map.py,ops.html} | 면② 정적 대시보드 + ops 뷰어 | 06-17~24 | 활성 |
| dashboard/{index.html,services/,history.jsonl} | 로컬 빌드 산출물 (**untracked** — git 조치 불요) | — | 정상 |
| console2/ (index.html+assets+build_static.py) | 면① Testing 콘솔 프런트 (spine이 서빙) | 07-04 | 활성 |
| console2/mockups/ ×4 | 디자인 목업 (완성 후 역사) | 06-22 | **retire 후보 R6** (약) |
| controlplane/*.py | 척추 서버 (app·routes·db·dispatch·authoring·ai·…) | ~07-04 | 활성 |
| controlplane/build_local_demo.py + templates/local_run.html | /local-run 정적 데모 빌더 (라우트는 301 은퇴) | 06-25 | **retire 후보 R3** |
| controlplane/build_ia_demo.py | ia-demo 정적 발행기 (Pages /ia-demo/) | 07-03 | 활성 |
| controlplane/{dashdata,snapshots,compare,…}.py | Reporting 데이터·스냅샷·비교 | 06-11~07-03 | 활성 (import 확인) |
| regression/{smoke,read_chains,scenarios/}.py | AXIS 1 엔진 | 06-16~07-04 | 활성 |
| regression/scr_docker_probe.py | SCR 자격증명 실험 (INCONCLUSIVE) | 06-13 | 관찰 (실험 기록) |
| tests/{smoke,crud,offline} | pytest 진입점 3종 | 활성 | 활성 |
| reports/ tracked ×10 (runtime_*.json·junit-crud.xml·validation_probe.json) | 06-05 커밋된 run 산출물 스냅샷 | 06-05 | **retire 후보 R5** |

## 2. Retire 후보 (증거 + 리스크 — 삭제는 오케스트레이터 승인 후)

| # | 대상 | 증거 | 대체 | 리스크 / 선행 조건 |
|---|---|---|---|---|
| **R1** | `poc/scenario-viz/` 전체 (155 tracked: html PoC ×6, build_*.py, console.html, knowledge.html, assets, kdocs, data) | IA-DIRECTION §정정: "poc = 아이디어를 검증한 곳", 수렴(S4) 완료로 기능 대체. **그러나 살아있는 참조 2곳**: ① `.github/workflows/api-test.yml:1121`이 `build_console.py`로 레거시 `/platform` 정적 콘솔을 Pages에 계속 발행, ② `tools/console_server.py`가 서빙. `build_data.py`가 07-04에 수정된 이유 = 이 레거시 빌드가 formal 모델을 직접 파싱해서 ready-LIST 형식 변화에 깨짐 — **유지비가 실제 발생 중** | controlplane `build_ia_demo`(/ia-demo/) + console2 static (`/console2/app/`) | **중**: api-test.yml 발행 스텝 교체/제거 필요(.github = 오케스트레이터 전용). Pages `/platform` URL 사용자가 있는지 오너 확인 후 제거. R2·tools/sample_data.py 동반 |
| **R2** | `tools/console_server.py` | 참조 = poc/console.html + console-platform-handoff.md(이번에 SUPERSEDED)뿐. console2/README: standalone 앱 은퇴(S4) 명시 | controlplane 척추 + console2 | **하**: R1과 동일 배치로 |
| **R3** | `controlplane/build_local_demo.py` + `templates/local_run.html` | build_local_demo 호출처 **0** (워크플로/스크립트/문서 없음, 최종변경 06-25). local_run.html은 오직 build_local_demo 때문에 보존 중 (app.py:919 주석, CONTEXT 07-03) — 순환 보존 | /local-run 라우트는 이미 301, run 관측성은 console2/Reporting이 대체 | **하**: app.py:919 주석도 함께 정리. reports/local-run-demo 재발행 필요 없음 확인만 |
| **R4** | `tools/publish_dagrun_live.sh` · `publish_live_obs.sh` · `publish_live.sh` · `rerun_when_clean.sh` | 저장소 전역 참조 0 (자기 자신 제외), 06-18~21 이후 미변경 — 특정 세션의 1회성 헬퍼 | publish_dashboard.sh + CI publish 스텝 | **최하** |
| **R5** | tracked `reports/runtime_*.json` ×8 + `reports/junit-crud.xml` + `reports/validation_probe.json` | 2026-06-05 커밋 이후 불변 — run 산출물 스냅샷이 실수로 커밋된 것. 소비자는 런타임에 새로 생성된 파일을 읽음(spec/export_csv.py, worker junit fold) | 매 run 재생성 (reports/는 gitignored 설계) | **최하**: `git rm --cached` 급 |
| **R6** | `console2/mockups/` ×4 html | 참조 0 (README 프로즈 언급뿐), 구현 완료로 목적 소멸 | 실제 console2 UI | **최하**: 디자인 역사 보존 원하면 유지 가능 (약한 후보) |
| **R7** | `poc/scenario-viz/FOLLOWUP.md` | 참조 0 | R1에 포함 | R1과 동반 |
| **R8** | `tools/sample_data.py` | 참조 = poc build_overlays/build_console뿐 | R1에 포함 | R1과 동반 |
| **R9** | `regression/scr_docker_probe.py` (약) | 1회성 실험(INCONCLUSIVE, 06-13), CONTEXT/knowledge에 결과 기록됨 | 결과는 knowledge에 보존 | **하**: cloud-ml 재도전 시 참고 가치 — C2에서 판단 |

**비후보 판정 (의심했으나 활성):** `dashboard/ops.html`(OPS-DASHBOARD 정본 뷰어, CI가 발행) · `controlplane/dashdata.py`(common/app/reporting이 import) · `controlplane/snapshots.py`(app import) · `tools/gen_index.py`(INDEX 생성기) · `tools/coverage_headroom.py`(coverage-service 에이전트 표준 도구) · `controlplane/build_ia_demo.py`(ia-demo 발행 활성) · `poc/scenario-viz/PLATFORM-PLAN.md`(26 refs — IA-DIRECTION이 정본 지위 인용; R1 실행 시 docs/로 이관 필요).

## 3. 문서 모순 (정본 우선 교차검증; ✅ = 이번에 직접 수정)

| # | 위치 | 문서가 말한 것 | 실제 (정본/코드) | 조치 |
|---|---|---|---|---|
| C1 | README.md §Safety model (구 153-158) | POST/PUT/PATCH/DELETE 기본 **blocked**, `=true`로 켬 | `core/config.py:161,165` 기본 **True** (opt-in = run 선택 + pre-flight; CLAUDE.md Hard Rule 1) | ✅ 표+문단 교체 |
| C2 | START_HERE.md 골든룰 (구 100-104) | 동일 stale 게이트 서술 | 동일 | ✅ 교체 |
| C3 | CONTEXT.md §Safety gates (구 58-68) | 동일 stale 게이트 표 | 동일 | ✅ 교체 |
| C4 | README.md §How runs are triggered (구 296-310) | "run-request 파일 push = 실행 시작(챗 세션 방식)" 등 3경로 | api-test.yml push 트리거 **오너 비활성(2026-06-18, 주석 처리)**; 챗 레인 = `.github/chat-heavy-request`→chat-heavy.yml; workflow_dispatch = 수동 폴백 | ✅ 재작성 |
| C5 | controlplane/README.md:7,35 | "actions(기본) — api-test.yml workflow_dispatch 트리거 / 수동 실행" 무조건 서술 | dispatch 자체는 동작하나 CI 자동화 비활성 + 실운영 레인은 로컬/chat-heavy | ✅ 주의 문구 추가 |
| C6 | docs/INDEX.md | 52 docs · CAMPAIGN-C3-100.md 누락 · 4개 구 핸드오프 status 🟢 active | 실제 55(+2 신규) · 핸드오프는 역사 | ✅ front-matter 정정 후 gen_index 재생성 |
| C7 | 구 핸드오프 4건 front-matter `status: active` | active | CONTEXT가 현재 상태 정본 (06-20 cutover 핸드오프는 CONTEXT가 명시적으로 STALE 판정) | ✅ superseded + superseded_by |
| C8 | working/console-platform-handoff.md | port 9000 레거시 콘솔을 현행처럼 서술 | console2+척추가 대체 (S4) | ✅ SUPERSEDED 헤더 |
| C9 | working/coverage-session-brief.md | 죽은 브랜치(claude/brave-edison-jbeqni) 기준 브리프 | 캠페인 정본 = CAMPAIGN-C3-100.md | ✅ SUPERSEDED 헤더 |
| C10 | .claude/skills/README.md:47 · :34 | ".claude는 skills만 tracked" · `docs/SESSION-HANDOFF*.md` 경로 | agents/·hooks/·settings.json도 tracked (.gitignore:10-17) · 경로는 docs/working/handoffs/ | ✅ 2곳 수정 |
| C11 | trackers/CATALOG-VALIDATION-STATUS.md:32 | VALIDATED 131/275 (≈48%) | 현재 149 VALIDATED / 126 docs (CONTEXT 07-03) | ⏳ 보고만 — 문서 자체가 "재생성: `python -m tools.catalog_status`" 지시 포함; 데이터 갱신은 트래커 오너 몫 |
| C12 | docs/IA.md:128 | 콘솔 기본 port **8000** | 현행 8800 (README/DEPLOY/console2 일치 확인) | ⏳ 없음 — SUPERSEDED 문서 내 역사 기술 (8000/8800 잔재는 이곳 1건뿐) |
| C13 | README.md:24 등 "275 nodes / **60** service files" | 60 | 실측 60 yaml = 59 서비스 + `_groups.yaml` (CONTEXT 구 블록은 59 files) | ⏳ 보고만 — 서술 애매성, C2 추상화 때 "60 YAML(서비스 59+그룹)"로 통일 권고 |
| C14 | CONTEXT.md 2026-06-17 블록 "static ceiling 100.0%" | 100.0% | 99.9% (1371/1372, waived 1) — 06-20 블록이 **문서 내에서 이미 supersession 명시** | ⏳ 없음 (in-doc 정정 존재; append-only 로그 특성) |

## 4. INDEX / 진입점 정합 + C2 목표 문서 트리 제안

- **진입 체인 검증**: CLAUDE.md → START_HERE.md → CONTEXT.md → agent-team.md → INDEX.md 링크 전부 실존 ✅. gen_index 재생성으로 INDEX가 55+2 문서/신규 status 반영 ✅.
- **정본 서열 재확인**: PLATFORM-IA-DIRECTION §확정IA(구조) > CONTEXT.md(현재 상태) > knowledge/(사실) > 트래커/플랜(작업). README/START_HERE는 "요약+포인터" 역할 — 이번 수정으로 게이트/트리거 서술이 정본과 재일치.

### C2 (2차 내용 정비) 목표 트리 — 제안만, 적용은 C2에서

| 문서 | 판정 | 근거 (1줄) |
|---|---|---|
| CLAUDE.md · START_HERE.md · CONTEXT.md · agent-team.md · INDEX.md | 유지 | 진입 체인 정본 |
| README.md | **추상화** | 332줄 과상세(트리거·컴포즈 세부) — "목적+계약+포인터"로 압축, 세부는 per-dir README로 |
| ARCHITECTURE.md + ROADMAP.md + M6-DESIGN.md | **병합→ARCHITECTURE.md** | 셋 다 06-20 동결, 로드맵/M6은 방향 서술 중복 |
| PLATFORM-PLAN.md | 보강 | M0–M5 완료 표시 + M4 cutover 잔여만 남기고 완료 절 접기 |
| IA.md | 유지(superseded) | 20 refs — 역사 anchor |
| PLATFORM-IA-DIRECTION.md | 유지 | §확정IA = 구조 정본 |
| PLATFORM-CONVERGENCE.md · IA-BUILD-CONTRACT.md | **retire(supersede 헤더)** | 수렴/빌드 완료 — 이행 완료된 계약서 |
| COVERAGE-MAX-PLAN(이미 ⚪) · COVERAGE-WAVE · COVERAGE-GETID · PROBE-READS · PARALLEL-EXECUTION | **병합→CAMPAIGN-C3-100 §A 레저 or supersede** | 캠페인 정본으로 대체된 구 플랜들 |
| RESOURCE-MODEL-PLAN.md | 추상화 | 설계(유지) vs 웨이브 로그(§6, knowledge/트래커로 이관) 분리 |
| DEPLOY.md · OPS-DASHBOARD.md · quotas-and-budgets.md · scheduler-system.md · COVERAGE-CRITERIA.md | 유지 | runbook/정의 정본, 참조 활발 |
| lessons.md · decisions/ | 유지 | append-only 역사 |
| trackers: UIUX-AUDIT · IMPROVEMENT-BACKLOG · PRODUCT-FINDINGS · VALIDATION-QUEUE · LIVE-READINESS-GATES · harness-tests | 유지 | 활성 작업 장부 |
| trackers: POSTRUN-2026-06-20 · COVERAGE-C3-ANALYSIS-2026-06-20 · GAP-REPORT(⚪) · run-parallelism · READ-REACHABILITY · SERVICE-GAP-REPORTS · CATALOG-VALIDATION-STATUS | **supersede/아카이브 절** | 일자 스냅샷 — 결론은 CONTEXT/캠페인 레저가 흡수 |
| handoffs/ 전체 | 유지(superseded 헤더) | 역사 — 삭제 금지 |
| console2-ia-ux-review.md | 병합→UIUX-AUDIT | 같은 성격의 백로그 두 곳 |
| per-dir README (controlplane·console2·knowledge/formal) | 유지+보강 | 이번 수정으로 정합; C2에서 운영 runbook 보강 |

## 5. 후속 (오케스트레이터 액션)

1. R1~R8 승인 여부 결정 — R1은 `.github/workflows/api-test.yml:1121` 발행 스텝 교체가 선행 (오케스트레이터 전용 영역) + Pages `/platform` 사용 여부 오너 확인.
2. R5(reports tracked 산출물)는 무위험 — 즉시 `git rm --cached` 승인 가능.
3. C11(CATALOG-VALIDATION-STATUS 수치)은 다음 `tools.catalog_status` 재생성 때 자연 해소.
4. C2 착수 시 §4 표를 작업 목록으로 사용.
