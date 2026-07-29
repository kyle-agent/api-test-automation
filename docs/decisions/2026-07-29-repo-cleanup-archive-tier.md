# Repo cleanup: docs/archive tier + retirement of poc and dead tools

**Date:** 2026-07-29
**Status:** Accepted
**Deciders:** owner (지시: "전체 repository 정리 — 흩어져 있는 정보 모으고, 사용하지 않는 소스/문서 정리") + cleanup 세션 (branch `claude/repository-cleanup-bqil1u`)

## Context

문서·소스가 세 갈래로 흩어져 유지비가 실제로 발생하고 있었다: (1)
`docs/working/CONTEXT.md`가 "짧은 현재 상태" 원칙을 잃고 1,138줄의 세션 로그
누적물이 됨 — 매 세션 로드 비용, (2) INDEX 기준 superseded 문서 30건이 활성
디렉터리에 섞여 진입 경로를 흐림, (3) 은퇴 판정(REPO-AUDIT-2026-07-04 R1~R9)을
받고도 계류 중인 죽은 소스(`poc/` 155파일 + 레거시 `/platform` CI 발행 스텝의
formal-모델 파싱이 실제로 계속 깨져 수리비 발생)와 같은 주제 문서 2벌
(`docs/quotas-and-budgets.md` vs `knowledge/quotas-and-budgets.md`).

## Decision

`docs/archive/` tier를 신설해 superseded 문서 33건(핸드오프 11 · 플랜 7 · 트래커 9 ·
루트 설계 3 · working 낱개 3 + CONTEXT 히스토리)을 **삭제 아닌 이동**으로 동결하고, CONTEXT.md를
"최신 CURRENT + 직전 핸드오프 2~3개"로 다이어트한다(밀려나는 블록은 히스토리
파일 상단으로). 죽은 소스는 참조 재검증(2026-07-29 전수 grep) 후 **삭제**한다:
`poc/` 전체(+api-test.yml `/platform` 발행 스텝 제거; `scenario-viz/PLATFORM-PLAN.md`
만 archive로 이관), `tools/{sample_data,gantt_sim,loop_cycle}.py`,
`drafts/{compose_wave5,recompose_ib042}.py`, `controlplane/static_export.py`,
`console2/mockups/`. 중복 quotas 문서는 `knowledge/quotas-and-budgets.md`로 병합.

## Alternatives Considered

| Option | Reason Rejected |
|--------|----------------|
| superseded 문서 전면 삭제 | 하드윈 사실·계보의 출처 추적 상실; 2026-07-04 감사도 핸드오프 "삭제 금지" 명시 — 이동이 안전하고 같은 효과 |
| 헤더만 superseded 표기하고 제자리 유지 (기존 방식) | 이미 하던 방식인데 활성 디렉터리가 계속 비대해짐 — 탐색성 문제가 해소 안 됨 |
| poc/ 유지 + CI 발행 스텝 유지 | 기능은 /ia-demo/ + console2 static이 대체 완료(IA-DIRECTION §정정), formal 모델 형식 변화마다 레거시 빌드가 깨져 유지비 실존 (2026-07-04 실측) |
| tracked `reports/runtime_*.json` untrack (감사 R5 원안) | **재검증에서 반려** — 현재 `conformance/static.py`가 live-confirmed 결함 폴딩 입력으로 읽음(커밋 cbfec854). 감사 시점 판정이 stale |

## Consequences

**Good:**
- CONTEXT.md 1,138줄 → ~200줄 — 매 세션 로드가 다시 "현재 상태"만 담음.
- docs/ 활성 트리가 진짜 활성 문서만 노출; INDEX에 Archive tier 분리 표시.
- 죽은 코드 ~170파일 제거, CI 발행 스텝 1개 제거 (레거시 빌드 수리 루프 종료).
- 아카이브 규약 명문화: 이동·동결·삭제금지 (`docs/archive/README.md`).

**Bad / Constraints:**
- 아카이브된 문서 **내부의** 상대 링크는 깨질 수 있음 (frozen 원칙상 미수정;
  활성 문서 → archive 방향 링크만 수정함).
- Pages의 기발행 `/platform` 사본은 동결 잔존 — 오너 확인 후 발행 스크립트의
  `rm -rf "$dd/platform"` 한 줄로 purge 필요.
- 과거 경로를 인용한 커밋 메시지/외부 북마크는 새 경로를 모름 (`git log --follow`로 추적).

## Override Conditions

poc 계열 코드가 다시 필요하면 git 이력에서 복원한다(`git show <sha>:poc/...`).
아카이브 tier 자체는 docs 구조 재편(예: v2 재구조화)이 확정되면 그 결정에 따라
재배치 가능. reports/ tracked 산출물 판정은 conformance 입력 경로가 바뀌면 재심.
