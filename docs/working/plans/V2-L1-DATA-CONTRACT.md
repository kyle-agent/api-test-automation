---
status: G1-review
for: v2-session
basis: V2-DECISIONS.md(D2 확정) + 정찰 실측 4건 (2026-07-09, 발행물/배선/로컬런·fold/서비스상세)
---

# V2 L1 데이터 계약 — 모든 수치의 출처·병합·배지·empty-state

> v2 화면의 **숫자 하나하나가 어디서 오는지**를 못박는 문서. 화면 구현은 이
> 계약을 따르기만 하고, 계약 변경은 이 문서 개정을 먼저 한다.

## 0. 대원칙 (D2 확정)

1. **판정성 헤드라인(회귀·커버리지)은 전 앱에서 최신 발행본 단일.**
2. 로컬 관측은 **"이 서버의 런"** 섹션에 격리 + 헤드라인 옆 보조 칩.
3. 스냅샷 화면은 과거형 표기, 판정 재계산 금지.
4. 모든 수치에 출처 배지 3종 중 하나. 한 카드의 기본값은 한 출처.
5. empty-state ≠ 0. "관측 없음"은 0이 아니라 안내문으로 렌더.

## 1. 출처 3종의 정의 (실측 기반)

### S1 — 발행본 (배지: 파랑 `발행 @시각`)

- **실체**: `origin/dashboard-data` 브랜치의 **root 파일들만** 정본.
  `preview-v2/`, `platform/`, `ia-demo/`, `poc/` 트랙은 v2 화면에서 **사용 금지**
  (동일 파일명이 blob 단위로 다르고 수치 트랙이 달라 불일치 원천 — 실측 확인).
- **접근**: `controlplane.dashdata` (git fetch 60s TTL + `git show`). 재구현 금지.
- **발행 시각 정본**: `endpoint_status.json`의 `updated` 필드(예: `"2026-07-09 19:27 KST"`).
  폴백: `history.jsonl` 마지막 줄 `ts`.
- **발행 식별자**: dashboard-data 브랜치 HEAD 단축 sha. 배지 표기:
  `발행 @07-09 19:27 · dd:9d65449b`.
- **노후 규칙**: 발행 시각이 24h+ 경과 → 배지 노랑 + 수치 회색화. 원칙 1-4.

### S2 — 로컬 관측 = 이 서버 (배지: 초록 `지금 이 서버`)

- **실체**: (a) `controlplane/data/platform.db`의 `runs` 테이블 — `gh_run_id`가
  `local-` 접두면 로컬 런, 숫자면 CI 런 (100% 정확한 구분키).
  (b) 라이브/최근 런 상태: `tools.console2_server`의 `_rec_view`/`_events_summary`
  (`console_api.api_runs()` 경유) — id·mode·게이트·pass/fail/skip·api ok/soft/fail.
  (c) 회귀축 관측치: `core.results.load_observations()` (`reports/results/*.jsonl`).
- **주의(실측 한계)**: 로컬 런의 `Observation.run`은 빈 문자열(GITHUB_RUN_ID 부재)
  → 런↔관측 조인은 `started~ended` 시간창 근사만 가능. v2는 이 근사를 쓰되
  화면에 "약 N건"으로 표기. ※ 엔진에 로컬 run-id 주입 요청은 기존 세션 몫
  (v2는 엔진 수정 금지) — 요청 문서화 예정.

### S3 — 런 스냅샷 (배지: 보라 `이 런 @run-id`)

- **실체**: CI 런 = `controlplane.snapshots`(oplog 버킷: `meta/observations/index`).
  로컬 런 = `reports/controlplane-local/<rid>.events.jsonl` + `_events_summary`.
- **규칙**: 런 상세 화면의 모든 값은 이 출처. 판정 배너 재계산 금지, 과거형 표기.

## 2. 화면별 수치 계약

### 2.1 상황실 (홈)

| 요소 | 출처 | 필드/계산 | 배지 | empty/노후 |
|---|---|---|---|---|
| 판정 헤드라인 (회귀 배너) | S1 | `history.jsonl` 마지막 줄: `fail_new` (0=초록 "새 회귀 0 — 배포 안전", >0=빨강 "새 회귀 N건 → 목록") + `fail_known` 병기 | 파랑 | 발행 없음: "발행된 공식 수치가 아직 없습니다"; 24h+: 노랑+회색 |
| 보조 칩 | S2 | `db.list_runs`에서 `local-` 런 중 `ended` > 발행시각 인 건수 → "발행 이후 이 서버 런 N건 →" (판정 불변) | 초록 | 0건이면 칩 숨김 |
| KPI: 검증 커버리지(C3) | S1 | `cov_c3` (+ `tested/total` 툴팁) | 파랑 | 〃 |
| KPI: 호출 커버리지 | S1 | `cov_op` (+ `cov_get` 툴팁 병기) | 파랑 | 〃 |
| KPI: 회귀 | S1 | `fail_new` / `fail_known` | 파랑 | 〃 |
| KPI: 잔존 자원 (D8) | S2 | 마지막 수집 캐시 + "마지막 확인 hh:mm" (자동 수집 금지, 수동 갱신 버튼) | 초록 | "이 서버에서 수집 이력 없음 — 새로 수집" |
| 병합 런 타임라인 | S2+S1 | `db.list_runs(50)` (CI+로컬) ∪ `snapshots.archive_index()` (구형 CI), `gh_run_id`로 dedupe, 시간순. 행별 출처 배지(CI/이 서버) + 결과 요약 인라인 | 행별 | "런 기록 없음 — 공식 이력은 발행 대시보드 →" |
| 이 서버의 런 (격리 섹션) | S2 | `console_api.api_runs()` → `_rec_view` 필드 (id, mode, status, 게이트, summary.lifecycles, summary.api) | 초록 | "이 서버에서 실행된 런 없음 — 공식 수치는 발행본 참조 →" |

### 2.2 서비스 목록 / 서비스 상세 (②서비스 축)

- **계산 방식**: `dashboard.build`의 순수 함수를 **서버에서 직접 import 재사용**
  (`load_catalog`/`endpoint_verdicts`/`per_service`/`findings_to_conf`) —
  HTML 파싱 금지, 로직 복제 금지. 발행 HTML과 같은 숫자를 같은 코드로 재현.
- **입력 계약** (발행 기준 재현):
  - 분모: `data/api_catalog.json` (저장소 HEAD)
  - 검증 누적: S1 `verified_endpoints.json` (dashdata 경유 — main baseline이 아니라
    발행본을 읽는다: 화면은 발행 기준이 정본이므로)
  - 최근 상태/응답시간: S1 `endpoint_status.json`
  - 결함: S1 `conformance.json` (`by_endpoint`) + 저장소 `data/baselines/known_issues.json`
  - 마스킹: `coverage_waivers.json`, `untestable_services.json` (저장소)
- **서비스 목록 행**: 이름(한글 라벨 규약), 카테고리, 검증 n/m·%, 결함 수(red/yellow),
  정렬 기본 = 커버리지 오름차순(백로그 우선, 발행본과 동일). 검색 필수.
- **서비스 상세 섹션**: 개요(검증 n/m, 읽기/쓰기%, 결함 수) | 엔드포인트 표
  (7컬럼: 상태색·메서드·경로·API·커버·최근 status/응답시간·결함) | 실행 딥링크
  `▶ 이 서비스 실행` = `/testing?service=<cat>/<svc>` (기존 prefill 계약 재사용,
  자동 발사 아님 — pre-flight 확인 유지) | 의존 미니그래프(후속, D6 인스펙터).
- **전 수치 배지**: 파랑(발행) 단일. 이 화면에 로컬 수치 병기 금지(후속에
  "이 서버 관측" 오버레이를 별도 결정 지점으로).

### 2.3 런 상세

| 요소 | 출처 | 비고 |
|---|---|---|
| CI 런 | S3 `snapshots.meta/observations` + db/이벤트 | 결과 요약·fail 목록을 이 페이지에 인라인 (M3) |
| 로컬 런 | S3 `_rec_view(full)` + `_events_summary` | pass/fail/skip + api ok/soft/fail + 실패 lifecycle id |
| 공식 반영 상태 | 계산 | 아래 2.4 |

### 2.4 fold(공식 반영) 동선 — D2 오너 추가 요구

- **"공식 집계 미반영" 판정**: 런 시간창(started~ended) 내 2xx 관측의
  `endpoint_key` 집합 − 발행본 `verified_endpoints.json` 키 집합.
  차집합 > 0 → "이 런에 공식 미반영 검증 증거 N건(약)" 배지.
- **동선**: 로컬 런 상세에 [공식 반영 검토] → 미리보기(차집합 endpoint 목록
  = `tools/derive_verified.py`와 동일 필터 로직) → 안내 2택:
  (a) "CI로 재실행" 딥링크(`/testing?...` prefill — 재현 확인 겸 자동 발행),
  (b) "fold 요청" — 절차 안내(derive_verified→promote_validated→커밋)와
  요청 기록 남기기. **v2가 fold를 자동 실행하지 않는다** (main 커밋은 검토
  절차 유지, Hard Rules 정합).
- 용어: 화면 라벨은 "공식 반영(fold)" — D7 규약(한글 1급 + 코드 보조).

### 2.5 결과 축 — 회귀·트리아지 (2026-07-10 보강, 정찰 실측 기반)

| 요소 | 출처 | 필드/계산 | 비고 |
|---|---|---|---|
| 회귀 카운트 | S1 | `history.jsonl` 마지막 줄 `fail_new`/`fail_known` | 현황 헤드라인과 동일 원천 (불일치 원천 차단) |
| **새 회귀 상세 목록** | S1(임시 우회) | 발행 `index.html`의 회귀 배너 블록(`action bad`) 파싱 → (key, status) | ⚠️ **임시**: 전용 발행 파일이 없음(실측). 정공법 = 발행 파이프라인이 `fail_new.json` 발행 — V2-REQUESTS-TO-ENGINE.md #1. 파싱 실패 시 "상세 목록 없음 — 발행 대시보드 참조" empty-state |
| 각 항목의 현재 상태 | S1 | `endpoint_status.json`의 같은 키 최신 [status] — "그때 500 → 지금 201(복구됨)" 병기 | 누적 최신은 재시도 복구를 숨기므로 **"당시"와 "현재"를 반드시 분리 표기** |
| 항목→서비스 연결 | — | 카탈로그 키(`cat/svc/op`)면 서비스 상세 딥링크. 합성 키(`lifecycle:step`)면 라이프사이클 라벨로 표기(링크 없음, 후속) | 같은 호출이 두 키 포맷으로 이중 기록되는 기존 패턴 존재 — 중복 병합(같은 status+유사 키) 시도하되 실패해도 정직하게 둘 다 표시 |
| 기지 실패 목록 | 저장소 | `data/baselines/known_issues.json` | "이미 알던 실패 — 추적 중" 섹션 |
| 정합성(축2) 변화 | S1 | `conformance_new.json` new/regressed/fixed + `conformance.json` summary | 회귀(축1)와 별도 섹션 — 축 혼동 금지 |
| 트리아지 분류 | 후속 | `controlplane.triage` 스키마(환경/스펙변경/테스트버그/실회귀) 재사용 가치 있음. 단 발행 1건 ↔ GH run 1..N 매핑 계약이 선행 — 후속 결정 지점 | v1 범위 제외 |

### 2.6 실행 축 — 계획·모니터링·기록 (2026-07-10 보강)

**안전 원칙 (pre-flight 오너 확정과 Hard Rule 1의 v2 구현)**
- **실효 게이트 표시 의무**: mutations/heavy/destructive 게이트는 실제 설정
  (core.config / env)에서 읽어 표시 — 하드코딩 금지 (기존 콘솔의 오표시가
  이 규칙의 반면교사). LIVE 기동 시 mutations 토글 없음(항상 ON 표기).
- **read-only 기동 = 열람 모드**: 실행 CTA 비활성 + "이 서버는 열람용
  (read-only)으로 기동됨 — 실행하려면 관리자 설정 필요" 표기.
- **계획 경험은 v2의 핵심** (오너 확인 2026-07-10: "test planning으로 선택하고
  DAG 확인하는 게 이 플랫폼의 핵심"): 선택 트리(/api/model, 원자 단위=node id)
  → 조합 DAG(/api/graph + resource_graph.js **scene() 계약**, 컨트롤러 1개
  유지+update()만) → 견적(/api/plan + /api/capacity) → pre-flight 모달
  (**plan+capacity+preflight 3-fetch 필수** — 우회한 발사 UI 금지).
  선택→lifecycle 확장은 서버(_resolve_lifecycle_ids)만 한다(클라 재구현 금지).
- **발사 버튼은 오너 검수 게이트 통과 후에만 활성화** — pre-flight 모달까지
  완성하되 [Run live]는 disabled + legacy 콘솔 딥링크 병기. 실제 클라우드에
  작용하는 기능은 검수 없이 켜지 않는다.

| 요소 | 출처 | 비고 |
|---|---|---|
| 실효 게이트 패널 | 서버 설정 실측 | mutations ON/OFF · heavy · destructive · 프로파일. 배지: This server |
| 계획(선택 폼) | 카탈로그/스위트 목록 (기존 로더) | `?service=` 등 prefill 쿼리 수용 — 서비스 상세 딥링크의 v2 종착지(후속 전환) |
| 실행 위치 | UI 선택지: This server / CI | D5 권고 A의 v2 구현 — v1은 표시+핸드오프만 |
| 라이브 모니터링 | `console_api.api_runs()`·`api_run(rid)` | This server 배지. 이벤트 스트림 상세는 후속 |
| 용량/큐 | `console_api.api_capacity()` | VPC 큐·용량 요약 |
| 기록 | Overview 병합 타임라인과 동일 원천 (limit 100) | 행별 출처 배지 |

### 2.7 Model 축 (2026-07-10 보강)

- 원천: `controlplane.resource_model.load_model()/load_groups()` (저장소 HEAD
  기준 — 발행/로컬 관측이 아니므로 배지 3종 대신 "저장소(main) 기준"
  panel-note로 표기. 새 배지 종류 발명 금지).
- 요약 KPI: 총 노드 · Validated 수/% · docs-only 수 · 그룹 수.
- Model table: 그룹/서비스별 노드 수·provenance 분포, 검색, 행 펼침으로
  노드 목록. D6: 독립 의존그래프 없음 — 의존은 노드 편집기(legacy 핸드오프)
  와 서비스 상세 인스펙터(후속)로.
- Inventory(구 카탈로그)는 중복 화면을 만들지 않는다 — 카탈로그 통계 한 줄
  + Services 축 링크로 갈음.

### 2.8 전역 검색 (2026-07-10 보강)

- GET `/v2/search?q=` — 3종: Services(services_data 재사용) ·
  Endpoints(`data/api_catalog.json` 캐시, 상한 50+총계 표시) ·
  Runs(`db.list_runs(200)` 부분일치, 상한 20).
- 셸 헤더에 컴팩트 검색폼(GET). 2자 미만은 안내 empty-state.
- 결과 행의 출처 배지: Services/Endpoints = 발행/저장소 기준 표기,
  Runs = This server.

### 2.9 실행 뷰 (런 상세의 live/queued/done 3상태) — 오너 승인 목업 기준 (2026-07-10)

> 목업: scratchpad v2-exec-mockup.html (아티팩트 발행됨). 골격은 legacy 실행
> 화면(체크리스트+now-playing) 패리티, 그 위에 3층: A(이상 감지)·B(계획↔실행
> 연속성)·C(종료 후 다음 행동). **런타임 DAG 없음** (기존 결정 존중 — 🕸
> 온디맨드 버튼만).

| 요소 | 출처 | 비고 |
|---|---|---|
| PLAN 스트립 | `POST /api/plan {lifecycle_ids: rec.lifecycle_ids}` 재계산 + durations | 생성/삭제 예상·peak VPC·ETA p50 |
| ACTUAL 스트립 | 이벤트 집계(생성 수·경과) + `/api/capacity` | 편차 칩 = ETA 대비 경과·생성 진척의 가중 판정 |
| VPC 슬롯 미터 | `/api/capacity` (cap·baseline·reserved·headroom·queued) | 기존/이 런/다른 런/여유 구분, cap-bar 흡수 |
| now-playing | 이벤트 tail (열린 step-start) | 진행률·잔여 ETA·⏹ Abort(기존 API) |
| A. 지연 의심 | 이벤트 경과 vs durations 실측 평균 ×3 | ⚠️ **세마포어 대기와 구분 필수**: 대기 이벤트가 없으면 VPC-생성 스텝은 지연 판정에서 제외(오탐 방지) — 엔진 요청 #5 처리 후 정식 구분 |
| A. 실패 군집 | 같은 서비스 연속 fail ≥2 | 인라인 ⏸ 제안 |
| 슬롯 대기 칩 (파랑) | 엔진 요청 #5의 대기 이벤트 (그 전엔 미표시) | "정상 조율, 실패 아님" 문구 고정 |
| queued 상태 | rec.status queued + capacity + 선행 런 잔여 ETA | WHY QUEUED(여유<peak) + 예상 시작 + 대기 취소 |
| 체크리스트/필터 | groupEventsByLifecycle 로직 이식 | ⊘ 쿼터 스킵 글리프(skip≠fail, Hard Rule 6), hover ⏸ + 확인 팝오버 |
| 호출 티커 | step-end 이벤트 최근 5건 | 500은 즉시 눈에 띔 |
| 4탭 | 이벤트(자원/API/로그) 네이티브, Runtime은 새 창 ↗ | iframe 금지(M6) |
| C. 종료 카드 | 이벤트 요약 + fold evidence(기존 run_detail_data) + 잔존 대조 + 쿼터 스킵 목록 | fail→Results, +검증→Fold, 잔존→Leftover 딥링크. 계획 대비 회고 1줄 |
| 폴링 규율 | 이벤트 2s 증분(?offset=) · capacity 30s · 숨은 탭 정지 | P2C-24 다이어트 준수, 렌더는 변경분만 |
| 잔존 | 실행 중 UI 없음(의도) — 종료 카드 1급 + Runs 축 Leftover 패널(후속) | D8 단일 표면 |

## 3. empty-state 표준 문구

| 상황 | 문구 |
|---|---|
| 로컬 관측 0 | "이 서버에서 실행된 런이 없습니다 — 공식 수치는 발행 대시보드 참조 →" |
| 발행본 없음/접근 불가 | "발행된 공식 수치를 가져올 수 없습니다 (오프라인?) — 마지막 확인 시각 hh:mm" |
| 발행 24h+ 노후 | 배지 노랑 + "N시간 전 발행본" + 값 회색 |
| 잔존 수집 이력 없음 | "이 서버에서 수집한 이력이 없습니다 — [새로 수집] (30~90초)" |

## 4. 공용 컴포넌트 계약 (M1에서 1회 구현)

- **배지 매크로** `badge(source, ts=None, ident=None, stale=False)`:
  `source ∈ {published, local, run}` → 파랑/초록/보라. stale=True → 노랑.
  모든 수치 카드·표 헤더는 이 매크로 외 배지 마크업 금지.
- **용어 상수 모듈** `controlplane/v2/terms.py`: 검증됨(C3)·호출됨(C2)·
  도달가능(C1)·잔존·과금 등 라벨+정의 툴팁 문자열. 템플릿은 이 모듈 값만 사용.
- **발행 메타 로더** `controlplane/v2/published.py`: 발행 시각/식별자/노후 판정
  1곳 구현 (`dashdata` 위에 thin layer, root 파일만 노출).

## 5. 한계·후속 (정직하게)

1. 로컬 런↔관측 조인이 시간창 근사 — 엔진에 `Observation.run` 로컬 run-id 주입
   요청(기존 세션 소관)을 별도 문서로 낼 것. 그때까지 "약 N건" 표기.
2. 서비스별 이력/트렌드(시계열)는 발행본에 없음 — v2 범위 밖, 후속 결정 지점.
3. `conformance_runtime.json`은 발행 시점이 별도(더 오래됨) — 서비스 상세 결함
   열은 `conformance.json`(정적) 기준, 런타임 결함 병기는 후속.
