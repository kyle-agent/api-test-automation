---
status: active
for: design-improvements-v1 세션 (v2 → v1 접목)
basis: V2-WRAP-AND-PIVOT.md (branch claude/v2-redesign-planning-aufboo — 오너 선회 결정 2026-07-10)
---

# V1 접목 트래커 — v2에서 검증된 것만 v1에 얹는다

> 오너 결정(2026-07-10): v2가 v1을 대체하는 방향은 **중단**. v1을 본체로 두고
> v2에서 검증된 디자인·사상만 골라 이식한다. 접목 후보·우선순위의 정본은
> `V2-WRAP-AND-PIVOT.md` §3 (v2 브랜치 `claude/v2-redesign-planning-aufboo`에
> 존치 — donor 코드 `controlplane/v2/**` 포함, 접목 완료 후 정리 권고).
> 데이터 계약 정본: 같은 브랜치의 `V2-L1-DATA-CONTRACT.md`.

## 접목 상태 (우선순위순 — V2-WRAP-AND-PIVOT §3)

| # | 후보 | 상태 | 비고 |
|---|---|---|---|
| 1 | 출처 배지 3종 + empty-state 규율 | **완료 (2026-07-11, 이 브랜치)** | 아래 상세 |
| 2 | 계획↔실행 연속성 (PLAN vs ACTUAL 스트립) | **완료 (2026-07-11, 이 브랜치)** | 아래 상세 |
| 3 | 종료 후 다음 행동 카드 | 대기 | 토스트 대신 3줄 카드 |
| 4 | 실행 중 이상 감지 (지연 의심·실패 군집) | 대기 | **엔진 요청 #5(세마포어 대기 이벤트) 선행** |
| 5 | 판정 시각 분리 표기 (발행 시각 ≠ 판정 런 시각) | 부분 | 배지 ts는 판정 런 시각(history ts) 사용 — 분리 병기는 후속 |
| 6 | (검토) 용어 툴팁·정의 노출 | 대기 | 오너 확인 후 |

## 접목 1 상세 — 무엇을 어디에 얹었나

- **배지 매크로** `controlplane/templates/_badges.html` (donor: v2 `_badges.html`):
  `badge(source, ts, ident, stale)` — `published`(파랑)/`local`(초록)/`run`(보라)
  /+`ci`(파랑 재사용, 병합 타임라인 행별 출처용 — 계약 §2.1 "CI/이 서버").
  stale=True → 노랑. CSS는 base.html 디자인 토큰으로 재구현.
- **홈** (`home.html` + `app.py home()`): 판정 배너에 Published 배지(기존 '노후
  스냅샷' chip 흡수) + **D2 보조 칩** "발행 이후 이 서버 런 N건 →"(완료된
  `local-` 런만, 판정 불변). 타일 5장 배지 + 노후 시 수치 회색화(`is-stale`).
  파이프라인 스트립: Modeling="저장소 기준" note(§2.7 — 새 배지 종류 발명 금지),
  Testing=local, Reporting=published.
- **리포팅** (`reporting.html`): summary 배너/사다리/타일·triage에 Published
  배지 + 노후 회색화. empty-state 표준 문구(§3 — "관측 없음 ≠ 0").
- **런 타임라인** (`_runs_table.html`): source 컬럼 신설 — `gh_run_id`
  `local-` 접두=This server, 숫자=CI. empty-state: "이 서버에 run 기록 없음 —
  공식 수치는 발행본 기준" (v1 최대 약점 "fail N vs run 없음" 모순 해소).
- **런 상세** (`run_detail.html`): This run 배지(S3 — 과거형 고정).
- **발행 시각 라벨** `common.snap_ts_short()` — history ts(=판정 런 시각, v2
  published.py 실측 교훈: 발행 저장소 갱신 시각과 구분)를 KST 짧은 라벨로.
- 테스트: `tests_offline.py::test_source_badges_and_empty_states` (24/24 통과).

## 접목 2 상세 — PLAN vs ACTUAL 스트립 (console2 실행 화면)

- **위치**: `console2/index.html` `#planactual` — now-playing 바로 위, run이
  in-flight(running/queued)일 때만 표시 (donor: v2 `run_exec.js` B층 +
  `run_detail.html` plan-strip, 오너 승인 목업 §2.9).
- **PLAN**: `POST /api/plan {lifecycle_ids: rec.lifecycle_ids}` 서버 재계산
  (run별 1회 캐시) — 생성 ~n · 삭제 ~n · peak VPC · ETA. **ETA는 donor의
  순차합산이 아니라 v1 now-playing 잔여와 같은 병렬 6 가정**으로 통일 (같은
  화면에서 두 ETA의 가정이 다르면 비교 불능 — 결정 지점 4).
- **ACTUAL**: 이벤트 실측(resource-tracked/-deleted 집계) + 경과 + VPC 슬롯
  미터(`/api/capacity` — 기존/이 런 peak/다른 런/여유 구분). queued면
  **WHY QUEUED**(여유 < 필요 peak 수치).
- **편차는 보수적으로**: "ETA 초과" 칩만. 지연 의심(실측 평균 ×3) 판정은
  접목 4로 미룸 — 엔진 요청 #5(세마포어 대기 이벤트) 전에는 VPC 대기가
  지연으로 오탐된다 (§2.9 명시). 테스트가 `avg * 3` 부재를 고정.
- 검증: 오프라인 계약 테스트(`test_plan_actual_strip_frontend_contract`, 33/33)
  + simulate 런 실주행(Playwright headless — PLAN 고정·ACTUAL 폴링 갱신·종료 시
  숨김 확인).

## 결정 지점 (오너가 뒤집을 수 있게 기록)

1. **노후 기준 48h 유지** — v2 계약은 24h, v1은 기존 P2C-12 결정(공용
   `common.STALE_AFTER_H=48`)이 있어 하나의 규칙(48h)로 통일 유지. 24h로
   낮추려면 `common.STALE_AFTER_H` 한 곳만 바꾸면 됨.
2. **타일 배지 밀도** — 홈 타일은 카드마다 배지(계약 §0-4), 리포팅 타일 4장은
   섹션 헤더 배지 1개 + 회색화만(같은 출처 반복 노이즈 절충). 오너 검수 시 조정.
3. 배지 라벨은 donor 그대로 영어(Published/This server/This run/CI), 툴팁
   한국어 — D7 개정과 정합. v1 전반의 용어 정비는 접목 6에서 별도.
4. **PLAN ETA 가정 = 병렬 6** (donor v2는 p50 순차합산) — v1 now-playing 잔여
   ETA와 가정 통일이 우선이라 판단. durations 근사의 한계는 툴팁에 명시.

## 경계 메모

- 이 접목은 `controlplane/*`(v1) 수정 — V2-KICKOFF의 구 경계 규약(v2 세션은
  v1 불가침)은 선회로 소멸, 접목 주체는 **이 세션**(오너 확정, V2-WRAP §5 P3).
- 엔진(`regression/**`, `core/**`)·발행 파이프라인(`dashboard/**`)은 불변.
