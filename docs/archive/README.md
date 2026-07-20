---
status: active
for: all
---

# docs/archive/ — superseded history (frozen)

> **여기 있는 문서는 전부 역사 기록입니다. 현재 상태의 정본이 아닙니다.**
> 현재 상태는 `docs/working/CONTEXT.md`, 구조 정본은
> `docs/working/plans/PLATFORM-IA-DIRECTION.md` §확정IA, 색인은 `docs/INDEX.md`.

## What lands here

- **Superseded design docs** (docs/ 루트에서 이동): `ROADMAP.md` · `M6-DESIGN.md` ·
  `IA.md` — 내구 결정은 `ARCHITECTURE.md`(§Direction · §Autonomy design)와
  `PLATFORM-IA-DIRECTION.md`로 병합 완료.
- **`handoffs/`** — 완료된 세션 핸드오프. 새 핸드오프는 여전히
  `docs/working/handoffs/`에 쓰고, superseded 처리될 때 이곳으로 옮긴다.
- **`plans/` · `trackers/`** — 캠페인 정본(`CAMPAIGN-C3-100.md`)·DAG 스케줄러 등으로
  대체된 일자 스냅샷 플랜/분석.
- **`CONTEXT-history.md`** — `docs/working/CONTEXT.md`의 과거 세션 로그 블록
  (verbatim 이동; CONTEXT는 "현재 상태만 짧게" 원칙 복원).

## Rules

1. **삭제하지 않는다** — 하드윈 사실의 출처 추적용. (`git log --follow`로 이력 유지)
2. **수정하지 않는다** — frozen. 정정이 필요하면 현행 정본 문서에 쓰고 여기엔 링크만.
3. 이동된 문서 **내부의 상대 링크는 깨져 있을 수 있다** (작성 당시 경로 기준).
   외부(활성) 문서 → archive 링크는 이동 시점에 수정됨.
