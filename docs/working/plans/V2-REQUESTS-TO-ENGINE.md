---
status: open
for: 기존 세션 (엔진·발행 파이프라인 소관)
from: v2 세션 (경계 규약상 dashboard/**·core/** 수정 불가)
---

# V2 → 기존 세션 요청 목록

> V2-KICKOFF 경계 규약: v2 세션은 `controlplane/v2/**`·`docs/working/plans/V2-*`만
> 쓴다. 아래는 v2가 필요하지만 소관 밖이라 기존 세션에 요청하는 항목.
> 처리 시 이 문서의 해당 항목에 결과를 기입해 달라.

## #1 (우선) — 새 회귀 상세의 전용 발행 파일 `fail_new.json`

- **무엇**: `dashboard/build.py`의 `compute()`가 이미 계산하는 새 회귀 목록
  (key, status, 가능하면 path·lifecycle)을 `fail_new.json`으로 dashboard-data에
  함께 발행.
- **왜**: 발행본에 fail 상세를 담은 기계가독 파일이 없어(실측: smoke_status.tsv는
  레거시 단절, endpoint_status.json은 재시도 복구가 이전 실패를 덮음),
  v2 결과 축이 발행 `index.html`의 배너 블록을 **파싱하는 임시 우회** 중.
  전용 파일이 생기면 우회 제거.
- **참고**: fail_known(기지 실패)도 같은 파일에 배열로 함께 주면 좋음.

## #2 — 로컬 런의 `Observation.run` 채우기

- **무엇**: 콘솔 로컬 실행 경로에서 `GITHUB_RUN_ID` 부재 시 콘솔 run-id
  (`local-…`)를 Observation.run에 주입 (예: `regression/scenarios/local_run.py`의
  live_run이 env로 전달).
- **왜**: 로컬 런 ↔ 관측치 조인 키가 없어 v2가 시간창 근사("약 N건")로 표기 중.
  fold 동선(공식 반영 미리보기)의 정확도가 이 키에 걸림.

## #3 (v2 안정화 후, 오너 재확인 필요) — 발행 대시보드 표기 정렬

- **무엇**: (a) 용어를 v2와 정렬(한국어 1급 + 코드 괄호 — 예: 검증됨(C3)),
  (b) 발행 시각을 페이지 상단에 전면 노출.
- **왜**: 같은 숫자를 콘솔과 공유 페이지가 다른 이름으로 부르게 됨.
  단, 발행 대시보드는 오너 확정(2026-07-10)으로 **유지되는 공유 표면**이므로
  디자인 변경은 오너 재확인 후 진행할 것.
