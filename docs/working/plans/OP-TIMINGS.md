# 오퍼레이션 타이밍 (per-API create/delete/update 실제 완료시간)

> 설계 검토 + MVP 반영 (2026-07-13). 매 런의 op별 소요시간을 서비스별로 표시하고
> 누적을 대시보드에 반영. 원천은 oplog `events.jsonl`(엔진 무수정, 기존 런 소급).

## 문제

`durations.json`은 **라이프사이클** span만, `analyze_run`은 API 호출 `elapsed_ms`만
잡는다. update/upgrade/resize 같은 **async op의 실제 완료시간**(202→상태 정착)이
없다. → op 단위 타이밍 레이어 신설.

## 데이터 모델 (op 1건)

`{run, service, catalog_key, lifecycle, step, kind, api_ms, settle_s, total_s, status}`
- **api_ms**: 호출 왕복(step-end elapsed_ms)
- **settle_s**: async 정착 대기 = op 직후의 `wait`/`settle` GET 폴 wall span (동기 op=None)
- **total_s** = api + settle · **kind**: create/delete/update/resize/upgrade/start/stop/backup/sync/purge

## 파생 (엔진 무수정)

`tools/op_timings.py` — events에서 op(POST/PUT/DELETE)와 그 settle-wait를 **인접
파생**(op 직후 첫 `wait*`/`settle*` GET, 다음 mutation 전)으로 연결. 이름 규약을
보조 신호로. settle 없으면 `settle_s=None`(동기/미측정 — 조용한 누락 금지).

라이브 검증: mysql `start-cluster`가 wait-started 1212s(20:12)로 create(559s)보다
느린 것을 정확히 분리(라이프사이클-레벨은 max로 뭉개 create로 오귀속했음).

## 매 런 화면 표시

`python -m tools.op_timings <run>` → 서비스×op 표(total/api/settle, 이번 런).
[v2] console2 run-detail에 "타이밍" 탭 — **백엔드 API 반영됨(2026-07-14)**:
`GET /api/runs/<id>/op-timings` → 이 런 events.jsonl 로 `derive()` → per-op 레코드
(service·step·catalog_key·kind·api_ms·settle_s·total_s·status, total_s 내림차순).
controlplane(`console_api.py`)·standalone(`console2_server.py`) 양쪽에 추가, 없는 런
404·읽기 실패 500. **콘솔 '타이밍' 탭 렌더도 이 세션이 반영**(오너 요청 2026-07-14
"op timing 화면 아직 없어?") — `reportOp()` + detail-subtabs `data-d="op"`, 스코프
필터·≥1s·전체표시·새로고침·크로스런 링크. 검증: `test_run_op_timings_api`(API) +
`test_op_timings_tab_frontend_contract`(UI 계약) + reportOp 헤드리스 하네스.

## 대시보드 반영 (cross-run)

`data/optimizer/op_timings.json`(tracked, durations.json 방식) — catalog_key당
rolling 50샘플로 p50/p90/max/last/n. `op_timings.py --accumulate`로 런마다 fold,
`--html`로 standalone 페이지 렌더. `publish_dashboard.sh`가 `op_timings.html`을
dashboard-data에 발행(Pages).

## 활용

- **타임아웃 데이터 튜닝**: wait 폴 timeout을 p90×1.5로 자동 제안(예: mysql start
  p90 20:29인데 wait-started timeout이 짧으면 실패) — [v3]
- **백엔드 회귀 탐지**: p90 대비 이번 런 급증 알림 — [v2]
- **스케줄러 정밀화**·**AXIS-2 느린-op 신호**

## 단계

- **MVP(반영됨)**: `tools/op_timings.py`(파생+표+누적+html) + publish 발행 + 문서
- **v2**: 런별 백엔드 API `/api/runs/<id>/op-timings` **반영됨** + **콘솔 '타이밍' 탭
  렌더 반영됨(2026-07-14)** — detail 탭, 탭 열 때 1회 fetch+캐시, 스코프 필터(전체/
  시나리오), total_s 내림차순·기본 ≥1s(전체 표시 토글)·새로고침·크로스런 링크. 추세/
  회귀 알림만 잔여
- **v3**: 엔진 명시 태그(`settle_of`)로 신뢰도 100% + 타임아웃 자동 제안

## 리스크

이름 규약 이탈 op는 settle 미집계(로그로 드러냄) → 비율 측정 후 v3 태그로 승격.
실패한 op의 settle은 "완료"가 아닌 "실패까지 시간"(status로 구분). adopt 공유
프로비저닝은 op 귀속 애매 → 별도 태그(백로그).
