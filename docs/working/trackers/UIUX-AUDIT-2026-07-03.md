# SCP 컨트롤플레인 UI — IA/UX 감사 보고서 (2026-07-03)

> 전 라우트를 로컬 렌더 + Playwright 스크린샷으로 실측한 감사. 분석 전용
> (코드 무변경). 발주: 소스·문서 전체 정리 전 사전 진단. 스크린샷은 감사
> 세션 스크래치에 있었으며 필요 시 재생성 가능 (uvicorn + chromium).

## 1. 화면 인벤토리 (라우트 · 목적 · IA · UX)

| 라우트 | 목적 | IA | UX |
|---|---|---|---|
| `/` | 홈 — 상태 요약·파이프라인·최근 RUN | **minor** (구 Plan/Run/Report 어휘, PLAN 타일→구 스테퍼 링크) | **minor** (8일 전 스냅샷의 "신규 fail 36" 빨간 배너가 현재 상태처럼 보임) |
| `/catalog` | ① API 인벤토리 (1,372 EP) | OK | **major** (전체 렌더 → 페이지 높이 ~55,000px) |
| `/planning/resources/map` | ② Modeling 표 (category▸service) | OK | **major** (275노드 전체 렌더 ~17,000px; 필터는 우수) |
| `/planning/resources` `/worklist` `/compose` | 노드 목록·작업큐·합성 | minor (map과 진입점 중복) | major (역시 17k px) |
| `/planning/resources/{id}` | 풀페이지 노드 편집 | OK | minor (다크 그래프 ↔ 라이트 셸 테마 충돌) |
| `/planning` (`?step=`) | **구 4단계 스테퍼** ①Catalog→④Validate | **major** (신 4-stage와 완전 중복, 홈에서 여전히 링크) | major (13k px) |
| `/planning/validate` `/dependencies` | 검증 패널 · 의존 그래프 | OK | OK |
| `/planning/edit` `/view` | 파일 편집/보기 | OK | minor (path 없이 접근 시 raw JSON 422) |
| `/knowledge` | 지식 파일 브라우저 | OK | minor (~80파일 평면 나열) |
| `/testing/embed` | ③ 실행 콘솔 (console2 iframe) | OK | minor |
| `/testing` | "스케줄·트리거" = **제2 실행 콘솔**(CI dispatch)+운영뷰 | **major** (라벨이 실체 은폐; 로컬 실행과 엔진·게이트 경로 다름) | minor |
| `/testing/console/` | console2 원본 (직접 접근 가능) | minor (셸 없음) | — |
| `/testing/resources` | 리소스 인벤토리 (ingest 기반) | minor | **major** ("0개 live" ↔ runtime 23개 — 모순) |
| `/runtime` | 라이브 자원 흐름 (loggingaudit) | **major** (nav 셸 없음, 고아 팝업) | **major** (계정 전체 이벤트 혼입 — 소유자 혼란 실증) |
| `/local-run` | 로컬 실행+계정위생 | **major** (**고아 페이지** — 링크 없음, console2와 중복, 강제 클린업 버튼 보유) | minor |
| `/reporting?tab=…` | ④ Summary·대시보드·Runs·Triage | minor (서브탭 이중 정의) | minor |
| `/reporting/coverage` | 커버리지 지도 (기본 진입) | minor (서브탭 세트 불일치 — 대시보드 탭 소실) | minor |
| `/reporting/compare` | run A/B diff | minor (서브탭 상실) | minor (셀렉트 빈 채 — 아카이브 run 미제공) |
| `/runs/{id}` | run 상세·개입·triage | OK | minor (없는 id도 200 빈 페이지; 결과 요약 없이 스냅샷 링크뿐) |
| `/ai` `/ai/task-draft` | AI 초안 파이프라인 | minor (nav에 없고 활성 상태도 없음 — README 주장과 불일치) | OK |
| `/dashboard/*` | 발행 대시보드 프록시 | OK | OK |

## 2. 우선순위 개선 목록

### P1 — 혼란/위험 유발
1. **`/runtime` 스코프 없음** — 최근 6시간 계정 전체 loggingaudit 토폴로지를 렌더 →
   타 run(CI 포함)의 삭제된 자원 취소선 20개+가 "내 라이브 실행"과 혼재. run/origin
   배지 전무, `?hours=`는 UI 미노출. 개선: 기본 스코프 = 현재 로컬 run, "계정 전체"
   토글, origin 배지(local/CI run-id/외부), 시간 윈도우 셀렉트, 삭제됨 기본 숨김. (중)
2. **잔존 자원 표면 4개가 서로 다른 답** — `/testing/resources`(ingest만, "0 live") vs
   `/runtime` vs console2 `/api/owned` 패널 vs `/local-run`. 개선: `/api/owned`
   (reconciler 소유 태그)를 단일 정본으로 한 화면에 통합, 타 표면은 인용+출처 명시. (중)
3. **ctxbar 스냅샷 불일치** — catalog/modeling/reporting-coverage/ai 라우터가
   `ctx_snapshot` 미주입 → "발행 스냅샷 정보 없음" 오탐 문구. IA.md의 "같은 sha를
   모든 화면에" 계약 위반. 개선: `_catalog()`의 ctx_snapshot을 공유 의존성으로 추출. (소)
4. **실행 표면 3중화 + 게이트 경로 상이** — 로컬 엔진(`/testing/embed`) / CI dispatch
   (`/testing`) / 고아 `/local-run`. 개선: `/local-run` 제거 또는 301, `/testing` 서브탭
   라벨 "CI 디스패치 · 스케줄"로, 상단에 실행 경로 대비 배너. (중)
5. **pre-flight confirm의 blast radius 불충분** — native `confirm()`에 lifecycle 수·heavy
   수·VPC peak만; 서비스/생성·삭제 예상/과금/ETA/"destructive 항상 ON" 없음; preflight
   조회 실패에도 실행 허용. 개선: HTML 모달(대상 서비스·heavy 목록·생성/삭제 예상·ETA·
   게이트 명시, heavy 시 추가 체크), preflight 실패 시 실행 차단. (중)
6. **홈 파이프라인 스트립이 구 IA로 연결** — PLAN 타일→구 스테퍼, 어휘 PLAN/RUN/REPORT.
   개선: 4-stage 4칸으로 교체, `/planning`→`/planning/resources/map` 리다이렉트, 스테퍼 은퇴. (소)

### P2 — 효율
7. **초장문 페이지** — Catalog 55k px·Modeling 17k·Compose 17k·스테퍼 13k. 카테고리
   기본 접힘 + 검색 시 전개, 또는 페이지네이션. (중)
8. **Reporting 서브탭 이중 정의** — reporting.html(영어 4탭)와 reporting_coverage.html
   (한국어 3탭, 대시보드 누락) 하드코딩 이원화; compare엔 서브탭 없음. 단일 include로. (소)
9. **run 히스토리·비교의 단절** — 아카이브 행에 스위트/상태/결과 없음, compare 셀렉트는
   DB run만(신설 서버 빈 채), run 상세에 pass/fail 요약 없음. index.json 메타 표시 +
   compare에 아카이브 run 포함. (중)
10. **스냅샷 노후 미표시** — 절대 시각만. 상대 시간 + 임계 초과 노후 경고 칩. (소)
11. **지표 라벨 혼란** — "2851/1293 · ok/호출"(분자>분모로 읽힘), cov_op/C1~C3 툴팁 부재
    (IA.md S6 glossary 미완). (소)
12. **에러/빈 상태 폴리시** — `/planning/edit` 무파라미터 raw 422, 없는 `/runs/{id}` 200
    빈 페이지 → 404/"기록 없음" 안내. (소)

### P3 — 폴리시
13. **한/영 혼용 정책 부재** — nav 영어+본문 한국어+서브탭 화면별 혼재, 같은 개념 이중
    표기(Summary↔요약). 규칙 한 곳에 정하고 일괄 적용. (소)
14. **테마 일관성** — 노드 편집·의존 그래프 다크 vs Testing 그래프 라이트. (소)
15. **접근성 기초** — 본문 13.5px/보조 10~11px, confirm() 스크린리더 맥락 부족,
    nav aria-current 없음. (중)
16. **셸 없는 화면** — `/runtime`·`/testing/console/` 직접 접근 시 복귀 링크 없음. (소)

## 3. 핵심 여정 마찰 요약
- **(a) 상태 파악**: 홈 판독성 양호. 마찰: 스냅샷 노후 비표시 · 파이프라인 어휘 ≠ nav ·
  "최근 RUN 없음" vs 아카이브 수십 건(신설 DB vs 버킷 이력 이원화 미설명).
- **(b) 테스트 실행**: 구성/실행 분리는 좋음. 마찰: pre-flight 정보 부족(P1-5) · 진행
  상태가 콘솔 내부에만 · runtime에 남의 run 혼입(P1-1) · 결과→Reporting 다리 없음.
- **(c) 실패 트리아지**: Triage 탭은 집계표만(신규 fail 목록/링크 없음) → run 상세 →
  스냅샷 대시보드로 3~4회 이탈; baseline 편집 링크 부재.
- **(d) 모델 저작**: 가장 잘 다듬어진 여정. 마찰: 17k px 목록 · compose 별도 17k ·
  편집 그래프 테마 충돌.
- **(e) 잔존 자원 점검/정리**: 최대 마찰 — 표면 4개 수치 상이(P1-2), ingest 없으면 항상
  0, "실행 중 409 차단"이 사후에야 드러남. 단일 삭제 안전장치는 양호.

## 4. IA.md ↔ 실제 구현 불일치
1. **IA.md v3 자체가 stale** — "one-graph 5-tab 정적 앱"을 정본이라 선언하나 실제는
   live 4-stage 콘솔+Knowledge (06-28 컨버전스 미반영).
2. **controlplane/README.md는 제3의 IA** — "nav = Overview·Plan·Run·Report·Knowledge".
   세 문서가 서로 다른 IA — 사실상의 정본은 CONTEXT.md.
3. "같은 sha를 모든 페이지에" — 절반 미충족 (P1-3).
4. README "AI는 Plan 인라인, top-nav 아님" — 실제는 독립 `/ai` 페이지.
5. "하나의 viz.js" — 실제 console2.js/resource_graph.js/runtime SVG 3계열.
6. IA.md deep-link 기본 포트 8000 vs 실제 8800.
