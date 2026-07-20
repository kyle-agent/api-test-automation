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

> **구현 현황 (2026-07-03, IA/CX phase 1 — branch `claude/upbeat-ritchie-ieus5u`):**
> P1-1 ✅ `33bf61f4` · P1-2 ✅ `98c3b25f` · P1-4 부분(✅ `/local-run` 301, `98c3b25f`;
> `/testing` 서브탭 라벨/배너는 미착수) · P1-5 ✅ `f686601d` · P1-6 ✅ `1aa96408`.
> P1-3, P2, P3 은 미착수 (후속 phase).
>
> **phase 3 (2026-07-04, 같은 브랜치 — "Run 관측성 개편", owner 승인 배치;
> 근거 = 페르소나 QA 저널 run `20260704-034346-64b5`):**
> A(F1·F2·신규4·신규6) ✅ — master 흐름 그래프가 **run 에 바인딩**(신규
> `GET /api/runs/{id}/graph` = 그 run 의 라이프사이클 폐쇄집합, 같은
> `resource_graph.js` scene + overlay; 모드 칩 "run 뷰: id ↔ 구성 미리보기"),
> 페이지 로드 시 활성 run **자동 재접속**, 구성 선택 sessionStorage 보존 +
> **기본 선택 = 빈 상태**, **now-playing 바**(lifecycle:step · METHOD path ·
> 경과 / durations.json 평균) 탭 위 상시 표시.
> B(F3·신규3) ✅ — lifecycle-end 시 열린 ⏳ API 행을 `fail (timeout/중단)` 로
> **즉시 닫음**(fail 카운터 동시 반영; 서버측 `_events_summary` 동일 규칙),
> pytest 자식 `PYTHONUNBUFFERED=1` 로 로그 라이브 tail, run 종료 시 로그 1회
> 자동 새로고침 + 완료/실패 **토스트**("run 종료: 6/7 passed — 1 failed: …").
> C(신규1, 치명) ✅ — run 종료 후 **+0/+5m/+15m owned 재스캔**(서버 데몬 스레드,
> fake-clock 테스트 가능), +0 보다 늘면 `late_alert` → 콘솔 토스트+패널 배너 +
> 실행 기록 행 "⚠ 종료 후 자원 늦출현 N건" + 남은 자원 자동 재스캔; run-end
> 로그 문구를 "teardown 시도 완료 — 실측 재스캔 예약됨" 으로 완화.
> D(신규2, 치명 · P2-9 완결) ✅ — 서버 시작 시 `_RUNS` 를
> `reports/console2-runs/*` 에서 **rehydrate**(0-byte 잔해 제외; UI 칩
> '복원됨'), 종료 run 을 controlplane runs DB 에 미러
> (`db.record_local_run`, gh_run_id=`local-<rec id>`) → Reporting ▸ 실행 기록
> + `/runs/{id}` 에 lifecycle pass/fail + api ok/soft/fail **결과 요약** 표시.
> E(신규5) ✅ — /runtime 캐시 윈도우 나이 칩("데이터 기준: N분 전 윈도우"),
> ~2분 초과 시 자동 재수집+페이지 auto-refresh, 첫 로드 진행 힌트.
> F(신규7·8·9·10) ✅ — 강제 클린업 **종료를 기다렸다가** 재스캔(잔존 시
> "의존 잠금 가능성 — 클린업 재실행 필요" 힌트), 삭제 수 = reconciler
> `genuine-removed` 라인 합산(실측), console2 남은 자원 패널에
> known_issues.stuck_resources **접힘 folding**(빨간 카운트 제외), 라벨
> '폐포'→'포함 API'(+툴팁)·preflight "생성 ~N · 삭제 ~M"·서비스 표기 통일·
> 이력 클릭 시 status 타일 "running" 오표시 제거·재계산 스피너, VPC 용량
> '내 실행' 귀속을 run 의 자원 id(공유 VPC 포함)로 키잉(`mine_live`).
> 테스트: `tests/offline/test_console2_run_observability.py` 17건 신설 —
> offline 438 ✅ · controlplane 20/16/18/16 ✅ · runner 16/16 ✅ · validate
> 243/0 ✅ · uvicorn+Playwright 스모크(재부착·run 뷰·now-playing·늦출현·
> fail-closure — 픽스처 기반, live 실행 없음) ✅.
>
> **phase 2 (2026-07-04, 같은 브랜치 — IA 정렬 스프린트, owner 승인 배치):**
> P1-3 ✅ `fb145d71` · P1-4 잔여 ✅ `bd1a9f9d` (완결) · P2-7 ✅ `eb33ec0c` ·
> P2-8 ✅ `49b20d4e` · P2-9 🔶 부분(compare 셀렉트 아카이브 병합은 기존 코드로
> 확인 — 잔여: 아카이브 행 스위트/상태 메타 + run 상세 pass/fail 요약) ·
> P2-10 ✅ `fb145d71` · P2-11 ✅ `1f8e8b68`+`935fff76`(호출EP vs ok관측 단위
> 상이 명시) · P2-12 ✅ `c60f48ee` · P3-16 잔여 ✅ `fdf51864` (+A5 Catalog
> '레시피 편집' 정본 라벨). C-결정: C1 Knowledge 공식화 · C3 /ai 셸 편입+딥링크 ·
> C4 gated 정본 명기 ✅ `21230d65`. 미착수 잔여 = P2-9 잔여 · P3-13(한/영 정책) ·
> P3-14(테마) · P3-15(접근성).

### P1 — 혼란/위험 유발
1. ✅ **DONE `33bf61f4`** — **`/runtime` 스코프 없음** — 최근 6시간 계정 전체 loggingaudit 토폴로지를 렌더 →
   타 run(CI 포함)의 삭제된 자원 취소선 20개+가 "내 라이브 실행"과 혼재. run/origin
   배지 전무, `?hours=`는 UI 미노출. 개선: 기본 스코프 = 현재 로컬 run, "계정 전체"
   토글, origin 배지(local/CI run-id/외부), 시간 윈도우 셀렉트, 삭제됨 기본 숨김. (중)
   → 구현: oplog(runs/<id>/res/*) join 으로 origin 주석, scope=mine 기본(빈 결과 +
   로컬 실행 없음 → all 폴백+배너), hours∈{1,6,24}, deleted 기본 숨김, Testing 셸 헤더.
   → **후속 결함 수정 ✅ `20ab510c` (2026-07-04, 오너 실측):** ACTIVE 로컬 실행 중
   scope=mine 이 빈 화면 — 버킷 join 은 create 폴링 완료 후에야 이벤트가 도달하고
   (수 분 지연) 스탬프 이전 서버 프로세스는 runs/local/ 로 기록해 join 이 비었음.
   수정: mine 귀속을 **버킷 독립** in-process 소스(rec 이벤트 파일 + core.registry
   per-run 샤드; `annotate_local_origins` overlay가 버킷 join 에 우선)로 전환,
   버킷 join 은 CI(gha-*) 배지용으로만 유지; mine 0건 + 실행 ACTIVE → all 폴백 +
   진단 배너("내 실행 귀속 실패 — 계정 전체 표시 중, 귀속 로직 점검 필요") — 실행
   중 빈 화면 금지. 로컬 oplog 미러 자체는 동작 확인(라이브 검증: run
   20260704-113744-7350 → runs/&lt;rec&gt;/res/*). 남은 자원 패널에도 '🕒 마지막
   스캔' 시각 + 실행 중 재스캔 경고 추가(console2 + /testing/resources).
2. ✅ **DONE `98c3b25f`** — **잔존 자원 표면 4개가 서로 다른 답** — `/testing/resources`(ingest만, "0 live") vs
   `/runtime` vs console2 `/api/owned` 패널 vs `/local-run`. 개선: `/api/owned`
   (reconciler 소유 태그)를 단일 정본으로 한 화면에 통합, 타 표면은 인용+출처 명시. (중)
   → 구현: /testing/resources 상단 '지금 남은 것 (실측)' = scan_owned 비동기 스캔
   (행별 삭제 + pre-scan 강제 클린업 모달 + known_issues.stuck_resources 접힘 그룹),
   ingest 표는 '이력'으로 강등 + 출처 한계 명시.
3. ✅ **DONE `fb145d71`** — **ctxbar 스냅샷 불일치** — catalog/modeling/reporting-coverage/ai 라우터가
   `ctx_snapshot` 미주입 → "발행 스냅샷 정보 없음" 오탐 문구. IA.md의 "같은 sha를
   모든 화면에" 계약 위반. 개선: `_catalog()`의 ctx_snapshot을 공유 의존성으로 추출. (소)
   → 구현: `controlplane/common.py` `base_ctx(active)` 단일 빌더 — app/_render ·
   resource_routes · ai_routes · catalog_routes · reporting_routes 전부 이걸 씀.
4. ✅ **DONE `98c3b25f` + `bd1a9f9d`** — **실행 표면 3중화 + 게이트 경로 상이** — 로컬 엔진(`/testing/embed`) / CI dispatch
   (`/testing`) / 고아 `/local-run`. 개선: `/local-run` 제거 또는 301, `/testing` 서브탭
   라벨 "CI 디스패치 · 스케줄"로, 상단에 실행 경로 대비 배너. (중)
   → `/local-run` 은 301 → /testing/resources (러너 UI 은퇴, `98c3b25f`); 서브탭
   라벨 "🛰 CI 디스패치 · 스케줄" + 두 경로 대비 한 줄 배너 (`bd1a9f9d`).
   러너 완전 통합은 후속 phase.
5. ✅ **DONE `f686601d`** — **pre-flight confirm의 blast radius 불충분** — native `confirm()`에 lifecycle 수·heavy
   수·VPC peak만; 서비스/생성·삭제 예상/과금/ETA/"destructive 항상 ON" 없음; preflight
   조회 실패에도 실행 허용. 개선: HTML 모달(대상 서비스·heavy 목록·생성/삭제 예상·ETA·
   게이트 명시, heavy 시 추가 체크), preflight 실패 시 실행 차단. (중)
   → 구현: 서비스별 표 + est_creates/deletes + 실측 ETA(durations.json) + heavy 명명
   목록/필수 체크, preflight 실패 = 완전 차단(우회 없음); 강제 클린업 confirm 2곳도
   fresh /api/owned 목록 모달로.
6. ✅ **DONE `1aa96408`** — **홈 파이프라인 스트립이 구 IA로 연결** — PLAN 타일→구 스테퍼, 어휘 PLAN/RUN/REPORT.
   개선: 4-stage 4칸으로 교체, `/planning`→`/planning/resources/map` 리다이렉트, 스테퍼 은퇴. (소)

### P2 — 효율
7. ✅ **DONE `eb33ec0c`** — **초장문 페이지** — Catalog 55k px·Modeling 17k·Compose 17k·스테퍼 13k. 카테고리
   기본 접힘 + 검색 시 전개, 또는 페이지네이션. (중)
   → 구현: Catalog·Modeling map·compose·자원 목록 카테고리/그룹 기본 접힘 +
   검색/필터 시 자동 전개 + sessionStorage 기억 (실측: catalog 55k→1.6k px,
   map 17k→1.1k, compose 17k→1.0k). 페이지네이션 없음(접힘으로 충분).
   스테퍼는 phase-1에서 은퇴(`1aa96408`).
8. ✅ **DONE `49b20d4e`** — **Reporting 서브탭 이중 정의** — reporting.html(영어 4탭)와 reporting_coverage.html
   (한국어 3탭, 대시보드 누락) 하드코딩 이원화; compare엔 서브탭 없음. 단일 include로. (소)
   → 구현: `templates/_reporting_tabs.html` 6탭(색칠지도·요약·대시보드·실행 기록·
   비교·트리아지) 단일 정의, 활성 탭 = `rtab` 변수. + 대시보드 탭에
   '추세 → 공개 대시보드(면②)' 고정 링크아웃.
9. 🔶 **부분 DONE** — **run 히스토리·비교의 단절** — 아카이브 행에 스위트/상태/결과 없음, compare 셀렉트는
   DB run만(신설 서버 빈 채), run 상세에 pass/fail 요약 없음. index.json 메타 표시 +
   compare에 아카이브 run 포함. (중)
   → compare 셀렉트의 아카이브(index.json) 병합은 기존 코드에 이미 있음을 확인
   (버킷 credential 있으면 신설 서버도 채워짐) + 서브탭 복원(`49b20d4e`).
   잔여: 아카이브 행 스위트/상태 메타 · run 상세 pass/fail 요약.
10. ✅ **DONE `fb145d71`** — **스냅샷 노후 미표시** — 절대 시각만. 상대 시간 + 임계 초과 노후 경고 칩. (소)
    → 구현: ctxbar + 홈 verdict 배너에 'N일 전' 상대 나이, 48h 초과 시 '노후' 칩.
11. ✅ **DONE `1f8e8b68`+`935fff76`** — **지표 라벨 혼란** — "2851/1293 · ok/호출"(분자>분모로 읽힘), cov_op/C1~C3 툴팁 부재
    (IA.md S6 glossary 미완). (소)
    → 구현: C1/C2/C3·cov_op·cov_get·fail_new title 툴팁(정의 출처 =
    dashboard/build.py 사다리 + docs/COVERAGE-CRITERIA.md, 의미 신설 없음).
    '호출/ok'는 단위가 다른 두 수(호출=고유 EP, ok=관측 건수)임을 라벨/툴팁에 명시.
12. ✅ **DONE `c60f48ee`** — **에러/빈 상태 폴리시** — `/planning/edit` 무파라미터 raw 422, 없는 `/runs/{id}` 200
    빈 페이지 → 404/"기록 없음" 안내. (소)
    → 구현: edit/view 무파라미터 = HTML 파일 선택기(file_picker.html), 없는
    run = 404 + '기록 없음 — 전체 목록' 링크 (DB·스냅샷·아카이브·명령 근거가
    하나라도 있으면 기존대로 200). 오프라인 테스트 2건 추가.

### P3 — 폴리시
13. **한/영 혼용 정책 부재** — nav 영어+본문 한국어+서브탭 화면별 혼재, 같은 개념 이중
    표기(Summary↔요약). 규칙 한 곳에 정하고 일괄 적용. (소)
14. **테마 일관성** — 노드 편집·의존 그래프 다크 vs Testing 그래프 라이트. (소)
15. **접근성 기초** — 본문 13.5px/보조 10~11px, confirm() 스크린리더 맥락 부족,
    nav aria-current 없음. (중)
16. ✅ **DONE `33bf61f4`(runtime) + `fdf51864`(console)** — **셸 없는 화면** — `/runtime`·`/testing/console/` 직접 접근 시 복귀 링크 없음. (소)
    → console2 헤더에 '← Testing 셸' 링크 — /testing/console/* 경로에서
    ?embed=1 없이 열렸을 때만 노출(정적 발행본엔 안 보임). 같은 커밋에서
    Catalog 행 딥링크 라벨을 확정 IA 정본 '✏️ 레시피 편집 →'으로 (A5).

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

## 5. 병합 — console2 디자인 백로그 (2026-06-22 리뷰에서 이관, 2026-07-04)

> 출처: `docs/archive/console2-ia-ux-review.md` (SUPERSEDED — 잔여 backlog는 이
> 절이 정본). 원본 ID(Q*/B*) 유지. 원본에서 이미 DONE인 항목(Q1·Q2 build-1/2 ·
> B1 `f73afadb` · B2 `6f5ae673` · B4 Suite ▾ · B6 착수)은 원본에 역사로 남기고
> 여기엔 **미완 항목만** 옮긴다. 북극성 원칙(원본 §4): **구성 DAG는 plan/live/
> coverage 세 삶을 사는 한 객체** — intent로 칠하고, progress로 칠하고,
> coverage로 칠한다.

| # | Prio | 문제 → 개선 | Effort | 2026-07-04 비고 |
|---|------|-------------|--------|------------------|
| Q3 | P0 | 색 채널 과적재(amber=공유dedup/docs/soft-fail · green=의존완료/validated) → 시각 속성별 채널 예산(role=fill, provenance=테두리, run-state=fill(실행), result=badge 모양) + 모드당 범례 1개, hue 단독 금지 | M | 미착수 (P3-14 테마와 연접) |
| Q4 | P1 | DAG 노드 클릭이 상세 탭과 무연결 → 노드 클릭 시 자원/API/로그 탭을 해당 lifecycle로 cross-filter | S–M | 미착수 (B1 master-detail 위에 얹는 다음 단계) |
| Q5 | P1 | run 후 스코프 재구성 → 실행→구성 왕복 간 선택 보존 | S | **사실상 DONE** — phase-3 A: 구성 선택 sessionStorage 보존 |
| Q6 | P1 | 상세 탭에서 전역 run 상태 안 보임 → progress ring + run badge 상시 표시 | S | phase-3 A now-playing 바가 상당 부분 대체 — 잔여: 전체 진행률 링 |
| Q7 | P1 | blocking `alert()` · model-load "로딩 중…" 고착 → 인라인 오류 배너 + 재시도 + 스켈레톤 | S–M | preflight confirm은 P1-5 모달로 대체됨 — 잔여: model-load 실패 경로 |
| Q8 | P2 | 부분 선택 서비스는 모달 재호출 필요 → 행에 "3/8 리소스" 표기 | S | 미착수 |
| Q9 | P2 | 검색이 리소스명 미포함 → 리소스명 + 상위 경로 검색 | S | 미착수 |
| Q10 | P2 | 실행 화면에서 Axis 재제공(중도 변경 오해) → read-only "this run used: …" | S | 미착수 |
| B3 | P1 | 선택 폐포가 집합으로 안 보임 → "닫힘 보기" 드로어(평면·그룹·trim 가능, "pulled by …" 근거) = "suite로 저장"의 토대 | M–L | 미착수 |
| B5 | P1 | run 히스토리 종속적·비교 없음·큐 미정의 → Runs build-list rail(queue=상태) | M–L | phase-3 D가 로컬 run을 Reporting 실행 기록에 미러 — 잔여: rail UI·비교 |
| B7 | P2 | plan↔run 전환 시 order/quota 판독 상실 → order/peak-VPC/dedup 스트립을 실행으로 carry | S–M | 미착수 |

**[later] 스케줄 회귀 HISTORY 대시보드 (status-page 스타일)** — 원본 §5 그대로
이월: 서비스/엔드포인트별 스케줄 run의 pass/soft/fail/delayed/skip 히스토리
스트립(참고 레이아웃 status.claude.com), 데이터는 기존 `reports/results/*.jsonl`
프로젝션(신규 엔진 개념 없음), run id 딥링크. Effort L · 명시적 deferred
(2026-06-22 owner 포인터).

## 6. 페르소나 2차 CX 검증 결과 (2026-07-04, LIVE virtualserver 1사이클 37m29s)

phase-3 "Run 관측성 개편" 수용 판정: **A(run 그래프·now-playing·재부착) 합격 ·
B(fail 가시성·종료 토스트) 합격(부분) · D(rehydrate+DB 미러) 합격 ·
E(runtime 신선도) 부분 · F(클린업 힌트류) 합격 — C(지연 재스캔·늦출현) 불합격.**
전일 F1(재부착)·F2(기본선택 오염) 해소 확인. 상세 저널은 세션 기록,
스크린샷 56장(세션 scratchpad). 수정 배치 완료 (2026-07-04, cdfb6180..0b597c62 + 7c90314e) — 상태는 표 참조.

| ID | 심각도 | 발견 | 수정 상태 |
|---|---|---|---|
| P2C-1 | **치명** | 대기열/미리보기 `▶ 실행`(runStaged)이 pre-flight 모달 없이 곧장 POST /api/run — Hard Rule opt-in 우회. 동일 heavy 구성 2건 2.2s 간격 무경고 admit(중복 가드 부재) | ✅ 수정 (cdfb6180+fd847d86 — preflightRun 통일 + 서버 409 가드) |
| P2C-2 | **치명** | +0/+5m 재스캔이 실잔존 6건을 total 0으로 오보(직접 scan_owned는 즉시 검출 — 서버 상태/경합 의심), late_alert 미발화, +15m 일정은 재시작 시 소실(rehydrate 재예약 없음) | ✅ 수정 (cdfb6180 — 근본원인: reconciler `_CONVERGED` 전역 캐시가 장수 서버에서 재-LIST를 스킵; + rescans.json 영속화·재시작 재예약·0건/스킵/실패 구분) |
| P2C-3 | **치명** | 로컬 run UI 중단 수단 부재(개입 채널 CI 전용) + provision 프로세스 사망을 러너가 삼키고 pytest 속행(중단 시맨틱 부재) | ✅ 수정 (cdfb6180+fd847d86 — /api/runs/{id}/abort: 프로세스그룹 SIGTERM→KILL + run-scoped 스윕 + 'aborted' 기록; ⏹ 버튼+확인 모달) |
| P2C-4 | 불편 | base.html htmx CDN(unpkg) 단일 리소스가 전 페이지 12.7s 블로킹(TTFB 1.6s) — 유일하게 설명 없는 대기 | ✅ 수정 (a543a56b — htmx 1.9.12 /static/vendor/ 벤더) |
| P2C-5 | 불편 | runtime mine-소스가 삭제 이벤트 미반영 → 삭제된 자원 4건이 '생성됨/테스트중' 유령 표시 + 열린 페이지 자동 갱신 없음 | ✅ 수정 (6fa9ec12 — local_deleted 마킹+기본 숨김, 90s 주기 갱신) |
| P2C-6 | 불편 | 홈 TESTING 타일·/testing '진행 중 RUN'에 전일자 stale run(8001·t-run-1) 상시 잔류 = 오신호; 실행 중 로컬 run은 콘솔 밖 완전 비가시 | ✅ 수정 (7c90314e — 24h+ running/dispatched 집계 제외, 회색 안내) |
| P2C-7 | 불편 | ② 실행 기록에 owned/sim 스캔 기록 20여 건이 실제 run 1건을 파묻음 | ✅ 수정 (fd847d86 — 기본 'run만'+전체 토글) |
| P2C-8 | 불편 | run 시작 시 남은 자원 패널이 "아직 확인하지 않음"으로 리셋 | ✅ 수정 (fd847d86 — 직전 스캔 결과/시각 유지) |
| P2C-9 | 라벨 | '포함 API 25' = 실은 폐쇄집합 **자원** 수(실 API 스텝 98) — 단어·단위 불일치 | ✅ 수정 (fd847d86 — '포함 자원 N (API 스텝 ~M)') |
| P2C-10 | 라벨 | now-playing '평균 ~27m'이 step 옆에서 lifecycle 평균으로 읽히지 않음; ok/soft/fail 정의 툴팁 부재(lifecycle 실패인데 API fail=0 혼란); VPC 칩 '기존/예약' 툴팁 부재 | ✅ 수정 (fd847d86 — lifecycle 평균 라벨 + ok/soft/fail·VPC 칩 툴팁) |
| P2C-11 | 사소 | 빈 상태 문구·힌트 필 겹침 / 중단 run "unknown·57s" 오표기(실 393s) / "M0 이전 run" 전문용어 | ✅ 수정 (fd847d86+cdfb6180+0b597c62) |
| P2C-12 | 관찰 | 8일 전 스냅샷 "신규 fail 36" 배너가 현재처럼 읽힘(노후 칩은 있음) — 후속 판단 필요 | ✅ 수정 (0d42037d — 48h+ 스냅샷이면 배너를 중립 회색 톤(.verdict.none)으로 낮추고 "N일 전 스냅샷 기준"을 배너 안에 병기; 신선 스냅샷의 빨강/초록 판정은 그대로) |
| P2C-13 | 관찰 | `tools.live_watch`가 러너 주도 heavy run에서 shared VPC를 BILLABLE_SURVIVOR로 오탐 — heavy_batch_start.txt 마커가 로컬 오케스트레이터 전용이라 러너 run을 인지 못함 (2026-07-04 HB1 감시 중 확인) | ✅ 수정 (73a68a54 — 택일 (b): regrvpcsh 이름이 생성 epoch-hex를 내장(engine.py)하므로 이름 나이 <2h grace 동안 SURVIVOR 판정 유보, 해독 불가/노후 이름은 종전대로 탐지; 오프라인 테스트 6건 추가) |

부수 확인: run 결과 6/7 passed — `delete-server` 400
`VirtualServer.InvalidVirtualServerState.DeleteImpossible` **전일과 동일 재현**
(백엔드 이슈, known-issue 후보). 최종 잔존 0건(독립 프로브 확정).

### 6-1. 오너 피드백 추가분 + IA 개정 구현 (2026-07-07, append-only — P2C 번호 승계)

> 오너 결정(세션 대화): **"Modeling이 Catalog를 흡수"** — 상세 근거·정본은
> `docs/working/plans/PLATFORM-IA-DIRECTION.md` §개정 (2026-07-07). 함께 접수된
> Modeling 표 CX 피드백 3건을 같은 배치로 수정.

| ID | 심각도 | 발견 | 수정 상태 |
|---|---|---|---|
| P2C-14 | IA | Catalog가 최상위 네비 단계로는 약함 — 데이터(분모·미모델 질의·conformance 단위)로는 필수지만 사용자는 "목록 훑기"가 아니라 "모델링/검증"에서 시작 | ✅ 구현 (d7e56c92 + a241d777 — 네비 3단계 `Modeling→Testing→Reporting`, Catalog는 우측 유틸 📖 링크(라우트·딥링크 유지), 홈 파이프라인 4→3칸, Modeling 서비스 행에 "API N (모델됨 M · 미모델 K)" 집계 + htmx lazy 엔드포인트 드로어(모델됨→노드 편집 딥링크), 애매 매핑은 '미매핑' 별도 버킷으로 과대계상 방지 — 규칙: `controlplane/resource_routes.py` 주석; 회귀 테스트 9c7d026d) |
| P2C-15 | 불편 | Modeling 표의 카테고리 접힘 행이 colspan 셀 안 '작은 카드'처럼 떠 보임 | ✅ 수정 (4e483c2e — 전폭 그룹 헤더 행: 배경 줄무늬·좌 캐럿·집계 우측 정렬, 표 문법·접기/세션 기억 유지) |
| P2C-16 | 라벨 | 표 컬럼 과밀·은어 — code/opt 컬럼이 판단에 기여 없음, provenance 헤더 비직관, 헤더 툴팁 부재 | ✅ 수정 (4e483c2e — code→id 셀 툴팁·검색 인덱스, opt 컬럼 제거(편집 화면에 이미 있음), provenance→'검증상태', 4개 헤더 title 툴팁 + 표 상단 한 줄 범례) |
| P2C-17 | 라벨 | 그래프 토글 '그림 (의존 그래프)'이 용도를 안 알림 | ✅ 수정 (4e483c2e — '의존 그래프 (영향 파악)' + 패널 캡션 "노드를 건드리기 전 영향 범위를 보는 뷰 — 편집은 표에서", 기능 변경 없음) |

### 6-2. 오너 피드백 — console2 ② Test Execution CX 재배치 (2026-07-07, append-only — P2C 번호 승계)

> 오너 피드백(2026-07-07): "Test Execution에 과거 히스토리가 전면에 보이고, 현재
> 실행이 Test Planning에서 본 것(DAG·리소스 순서표)과 인라인이 안 되고, 런타임도
> 팝업을 따로 띄워야 보인다." 목표 흐름: 선택→DAG·순서표→대기열→실행→**계획했던
> 그 그림이 라이브로**→로그·런타임 인라인. 구현 배치 = `43a85eec` (console2
> 프런트만 — 서버·controlplane 무변경; 페이즈-3 메커니즘 재사용, 재발명 없음).

| ID | 심각도 | 발견 | 수정 상태 |
|---|---|---|---|
| P2C-18 | CX | ② 화면 중단을 실행 기록 목록이 차지해 "무엇이 지금인가"를 흐림 — 현재 실행이 전면이어야 | ✅ 수정 (43a85eec — 실행 기록을 기본 접힘 섹션("실행 기록 N건 ▸", sessionStorage `c2.histOpen.v1`)으로 강등; 상시 노출은 현재 실행 히어로 + 유휴 시 최근 종료 1건 요약 행뿐, 'run만/전체' 필터(fd847d86)는 접힘 안에서 유지) |
| P2C-19 | CX | run 시작 후 ② 그래프가 ①에서 계획한 것과 같은 그림인지 명시 안 됨 · 생성·검증·삭제 순서표는 ①에만 | ✅ 수정 (43a85eec — run 바인딩 시 "①→② ① 에서 계획한 폐쇄집합 그대로 실행 중 — N 리소스 · 생성 순서 동일" 칩(종료 시 문구 전환); ② 그래프 아래 접힘 순서표 = ①과 동일 빌더(orderRowsData/orderRowHtml 공용 추출) + now-playing active lifecycle 행 하이라이트(.ordnow)를 폴마다 동기, details 열림 유지) |
| P2C-20 | CX | 런타임(계정 실측)이 별도 팝업으로만 — 실행 흐름과 단절 | ✅ 수정 (43a85eec — 자원/API/로그 옆 4번째 탭 '런타임(계정 실측)' = 기존 /runtime?scope=mine iframe 임베드; URL 단일 소스 `runtimeUrl()` (팝업·pre-flight 링크·iframe 공유), 페이지 자체 주기 갱신(6fa9ec12) 그대로 — 로직 복제 없음. 팝업 버튼 유지 + '(새 창)' 라벨) |
| P2C-21 | CX | 실행 admit 후 히어로가 뷰포트 밖일 수 있음 (auto-switch 부분 존재) | ✅ 수정 (43a85eec — postRun 기존 ② 전환 유지 + admit 시 히어로(md-report) scrollIntoView 보강) |

검증: `node --check` · `pytest tests/offline -k console2` 52 passed (계약 테스트
`test_execution_cx_relayout_frontend_contract` 추가) · :8832 read-only smoke
(SCP_ALLOW_MUTATIONS=false — ② 렌더·rt 탭·접힘·/runtime 200·run graph 계약).

### 6-3. 오너 피드백 — ② 실행 뷰 마스터-디테일 2-pane 전환 (2026-07-09, append-only)

> 오너 피드백(2026-07-09, 캡쳐 2장): "위-아래 화면 구성이 불편 — 몇 개가 될지 모르는
> 시나리오가 가로 한 줄씩 세로로 쌓이고, 누르면 상세가 '아래'에 열려 시나리오가 많으면
> 스크롤 왕복. 원하는 구조 = 좌측에 전체 + 그 하위 시나리오 목록(시작/종료 상태 인라인),
> 클릭하면 우측에 자원·API 등. 메인은 전체 — 전체의 로그·자원·API·라이브 런타임이 주."
> P2C-18~21(43a85eec)의 직접 후속 — 시맨틱(히어로 전면·①→②칩·순서표·rt탭)은 보존,
> 배치만 세로 스택 → 2-pane. 실증 규모: 96 lifecycle 런에서 스크롤 왕복 발생.
> 분석(전담 에이전트): 마스터-디테일 시맨틱(selectScope/scopeData/집계 기본선택)은
> 이미 구현돼 있고, 원인은 `console2.css:495` `.md-report{grid-template-columns:1fr}`
> 단일 컬럼 — 프런트 전용 S–M 수술.

| ID | 심각도 | 발견 | 수정 상태 |
|---|---|---|---|
| P2C-22 | CX | ② 실행 뷰가 단일 컬럼 — 라이프사이클 카드 96개 세로 스택 아래에 상세 패널; '전체(집계)' 진입점이 목록 맨 아래; 진행/완료/실패 분포를 한눈에 못 봄 | ✅ 구현 (2026-07-09, 오너 승인 'main 반영 포함' — 커밋 예정분) — 2-pane 전환: 좌 rail(sticky·내부 스크롤·'전체' 행 최상단+진행률 링(Q6 잔여 흡수)·상태 필터 칩·1줄 압축 행(fill=상태 단일 채널, Q3 준수)·대기 행 통합·now-playing 자동 추적) + 우 detail(기존 스코프바/4탭 그대로) + 그래프 전폭 상단(접기 토글, P2C-19 칩 보존) + ≤1180px 단일 컬럼 폴백. 지점: console2.css `.md-report` · index.html lc-picker 이동 · console2.js renderLcPicker · 계약 테스트 확장 |

부수: §5 Q4 → 사실상 DONE (노드 클릭=상세 스코프 열기 구현됨, 43a85eec 계열 —
index.html:143 · console2.js:2383). Q6 잔여 진행률 링은 P2C-22 rail '전체' 행으로 흡수 예정.


### 6-4. 오너 피드백 — Modeling 카테고리 헤더 칩 부유 (2026-07-09, append-only)

> 오너 피드백(2026-07-09, 캡쳐): "모델링에 카테고리가 grid의 중간에 위치하는 것도 이상함"
> — 카테고리 그룹 헤더가 표 전폭이 아니라 좌측 내용-폭 둥근 칩으로 지그재그 부유.

| ID | 심각도 | 발견 | 수정 상태 |
|---|---|---|---|
| P2C-23 | CX·재발 | Modeling 표 카테고리 그룹 헤더가 내용-폭 둥근 칩으로 부유 (P2C-15 재발) — 뿌리는 base.html 전역 `.cat` 배지 규칙이 `<tr class="cat">`에 매칭돼 tr이 display:inline-block(colspan 무력화). 헤드리스 실측 확정 (행 299~374px vs 표 1092px) | ✅ 수리 (2026-07-09) — 배지 규칙 `span.cat` 스코프 + bare `.cat` 재등장 금지 회귀 테스트. 부수: conftest 선임포트로 offline 테스트가 라이브 DELETE 가능하던 게이트 핀 구멍 봉합 (settings 싱글턴 직접 핀) |


### 6-5. 오너 피드백 — 폴링 폭주·깜빡임·진행률·per-lifecycle 중단 (2026-07-09, append-only)

> 오너 피드백(2026-07-09, 터미널 캡쳐 + 발언): "화면이 너무 깜빡거려서 필요한
> 라이프사이클 잘 클릭이 안되기도 하고 … 전체 실시간을 많이 넣어놓다 보니 백엔드에
> api가 너무 많이 날아감" + "run 이 얼마나 진행되고 있는지" + "중간에 특정
> 라이프사이클을 멈출 수는 없네". 실측: 같은 초에 /api/runs 2-3회 + events 2회 +
> capacity 1회 — 뿌리는 ① pollEvents 700ms 전체 재fetch ② drawReport 가 매 폴마다
> loadRunRecords(/api/runs) 호출 ③ capacity 2s 틱의 /api/runs 동승 ④ 로그 탭 틱의
> 이중 fetch(+/api/runs), 그리고 rail/detail 의 폴마다 innerHTML 전체 재빌드.
> 서버 반쪽(per-lifecycle skip 채널)은 main `7624e296` 선랜딩.

| ID | 심각도 | 발견 | 수정 상태 |
|---|---|---|---|
| P2C-24a | 성능 | 라이브 중 백엔드 폴링 폭주 (초당 ~5req) | ✅ 구현 (2026-07-09) — 이벤트 폴 단일 tick 2s(EV_TICK_MS) + **증분 fetch** `?offset=N`(서버 `_events_view` — tail만 전송, offset 초과=0 강등 재동기화) · capacity 30s(대기열 있을 때만 5s) · /api/runs 는 시작/종료/종료 후 30s 감시(startRunsWatch)로만 · 로그 탭 이중 fetch 통합(3s) · document.hidden 이면 전 폴 정지 + 복귀 시 즉시 재개. 라이브 정상 상태 ≈ events 1req/2s |
| P2C-24b | CX | 라이브 중 rail/상세 전체 재렌더로 깜빡임·클릭 유실 | ✅ 구현 — 키 기반 in-place patch(`syncUnits` — 바뀐 행만 교체, data-k 계약) + `setHtmlIfChanged`(불변 조각은 DOM 유지: 배너·스코프바·kpi·soft 분류·now-playing 셸) + **정적 컨테이너 위임 클릭**(rail·scopebar·detail·⏹) — 행이 교체돼도 클릭 불멸, 스크롤 자연 보존 |
| P2C-24c | CX | 런 전체 진행률이 어디에도 없음 | ✅ 구현 — now-playing 바에 진행률 바+텍스트(종결 N/전체 · % · 경과 · 잔여 ~ETA; ETA = durations.json 실측 평균의 미종결 합/병렬 6 가정 — duration_stats 와 동일) `runProgress()`; rail '전체' 카드 링도 같은 소스로 동기 |
| P2C-24d | CX | 특정 라이프사이클만 중단 불가 (UI 부재 — 서버는 7624e296) | ✅ 구현 — detail 스코프바에 조건부 "⏸ 이 라이프사이클 중단" (선택 스코프 진행/대기 + 런 running 일 때만) → POST /api/runs/{rid}/skip-lifecycle, 202 note 토스트. rail 행 hover 아이콘은 보류 (in-place patch 단순성 우선 — 필요시 후속) |
| P2C-25 | IA·결정 대기 | 오너: "Modeling 의 '의존 그래프 (영향 파악)' 탭은 의미 없는 것 같음" | 🔲 **오너 결정 대기** — 옵션: (i) 탭 숨김 + 딥링크/라우트 보존 (저비용, 복구 쉬움 — map.json·resource_graph.js 는 console2 run 뷰와 공유라 제거 불가) (ii) 완전 제거 (UI+graph/map.json 정리 — 단 편집 화면·의존성 검증의 그래프 사용처와 분리 필요, 회귀 테스트 수정) (iii) 유용화 — 6-x 레퍼런스 검토에서 제안한 '우측 정의·사실 패널'(legacy /platform 콘솔 이식 (d)-1)과 결합해 "노드 클릭=사실 요약+편집 딥링크"로 재목적화. **권고 = (iii) 시도 후 불채택 시 (i)** — '영향 파악'이라는 용도 라벨(P2C-17)로도 안 쓰인다면 뷰 자체가 아니라 내용물이 문제라는 신호 |
