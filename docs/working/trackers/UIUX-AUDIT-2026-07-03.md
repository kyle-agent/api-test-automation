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
