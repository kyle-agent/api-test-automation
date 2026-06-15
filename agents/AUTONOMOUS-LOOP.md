# AUTONOMOUS-LOOP.md — 4-track parallel multi-agent operating model

> **2026-06-15, owner.** This file replaces ad-hoc "한 번에 한 단위" 디스패치
> 패턴. 새 세션이 열리면 `START_HERE.md` → `agents/CONTEXT.md` →
> **이 문서** → `agents/orchestrator.md`(L0–L3 사다리·STOP 6기준) 순서로 읽고,
> 4 트랙을 동시에 가동한다.

## 왜 다시 짰나

기존 시스템(`agents/README.md`의 8-roster)은 *역할*은 잘 나뉘어 있지만 **언제
누가 동시에 도는지**가 약했다. 결과: 한 단위 끝날 때까지 다른 모두가 놀고,
사람이 매 단위마다 다음 지시를 줘야 했다. 이 문서는 그걸 **항상 4트랙 동시
가동**으로 못박는다(인포그래픽의 Loop Engineering — Schedule·Maker·Checker·
Persistent State 4축).

## 4 트랙 — 항상 동시 가동

```
Meta-Orchestrator  (lead 세션 = 사람-Claude pair)
│   책임: 공유 인덱스 갱신, 커밋·푸시, run dispatch 직렬 게이트,
│         4 트랙 헬스 체크, STOP 6기준 해당 시에만 owner 호출
│
├── ① Platform Track       ─ 플랫폼 자체를 개선
├── ② Coverage Track       ─ 테스트 커버리지 확대 (4-tier 하위)
├── ③ Watcher Track        ─ 다른 트랙이 일 잘하는지 메타 검증
└── ④ Problem-Finder Track ─ 문제 능동 추적
```

### ① Platform Track

| sub-agent | 책임 | 산출물 |
|---|---|---|
| **platform-improver** | `controlplane/`·`dashboard/`·`core/` 개선 (성능·UX·신뢰도) | 커밋된 패치 |
| **tools-developer** | `tools/`·`scripts/`·CI workflows·dispatch 효율화 | 도구 + 측정값 |

스코프: UI/플랫폼 영역. 다른 트랙이 만드는 *데이터*(lifecycles, knowledge)는
건드리지 않는다.

### ② Coverage Track — 4-tier sub-hierarchy

| sub-agent | 책임 | 병렬도 |
|---|---|---|
| **coverage-coordinator** | 트랙 내 sub-orch. gap 계산(`spec.coverage_gap`), 티켓 발급, fragment 머지 정렬 | 1 |
| **docs-mapper** ×N | 서비스 1개씩 맡아 userguide WebFetch → `knowledge/formal/resources/<svc>.yaml` 채움 (L2 ladder의 자동화) | 4~6 동시 |
| **dependency-resolver** | 서비스 간 의존 그래프, precondition 노드(IB-012·013식) 모델링 | 1~2 |
| **lifecycle-composer** | `composer.py` 호출 → `gen-*` 합성 + 오프라인 게이트 통과 | 1~2 |
| **live-verifier** | run-request 큐잉만 — *실제 dispatch는 Meta-Orch* | 1 |

### ③ Watcher Track — 메타 검증 (Checker)

| sub-agent | 책임 | 핵심 원칙 |
|---|---|---|
| **output-reviewer** | 다른 트랙 산출물의 별도 검증 (게이트 재실행·로직 점검·STOP 기준 위반 감지) | 자기 트랙 산출물은 못 봄 (self-justification bias 차단) |
| **drift-detector** | model ↔ lifecycle JSON ↔ code ↔ knowledge drift 스캔 (IB-019식 누수, 96 lookup 노이즈식 노이즈, baseline 만료 waiver) | 자동 IB 발급 |

### ④ Problem-Finder Track

| sub-agent | 책임 | 산출물 |
|---|---|---|
| **product-defect-finder** | 최근 N run의 fail/soft를 PF 후보로 클러스터링 → `docs/PRODUCT-FINDINGS.md` 신규 줄 제안 | PR-candidate 목록 |
| **failure-pattern-clusterer** | `dashboard/history.jsonl` + `reports/results/*.jsonl` 패턴(같은 status code, 같은 path family) | 클러스터 리포트 |
| **debt-finder** | drift-detector 결과 + 코드 TODO + 오래된 `_disabled_reason` + 만료 waiver → `docs/IMPROVEMENT-BACKLOG.md` 신규 IB | IB 후보 |

## 직렬화 지점 (병렬 안 되는 것)

세상엔 동시화 못 하는 게 3가지뿐이다:

1. **커밋·푸시** — Meta-Orch만. sub-agent는 변경 보고만, 커밋은 안 함.
2. **공유 인덱스 파일** — `agents/CONTEXT.md`, `agents/coordination/ledger.json`,
   `docs/IMPROVEMENT-BACKLOG.md`, `docs/PRODUCT-FINDINGS.md`. Meta-Orch가 머지
   시점에 한 번에 갱신.
3. **live run dispatch** — `.github/run-request` push. owner rule: 한 번에 1 run.
   live-verifier가 큐잉, Meta-Orch가 직렬로 dispatch.

그 외(파일 편집, 게이트 실행, WebFetch, composer, 분석)는 모두 병렬 OK.

## sub-agent 격리

- 각 sub-agent는 **자기에게 배정된 파일만** 편집한다(파일 단위 ownership).
- worktree isolation은 필요 시 사용 (Coverage Track의 docs-mapper들은 자기
  서비스 yaml만 만지므로 일반 격리로 충분).
- sub-agent끼리 직접 통신 금지. 통신은 git blackboard
  (`ledger.json`·`CONTEXT.md`·`PRODUCT-FINDINGS.md`)로만, Meta-Orch가 중계.

## 사이클 (한 라운드 = ~30~60분)

1. **dispatch (T0)** — Meta-Orch가 4 트랙에 동시에 sub-agent fan-out.
   - 트랙 안 sub-agent도 fan-out (예: docs-mapper 5명 동시).
2. **work** — 각 sub-agent가 worktree에서 작업, 게이트 통과 시 변경 파일 +
   요약 + 게이트 출력 보고 (커밋은 안 함).
3. **integrate (Tn)** — Meta-Orch가 보고를 받으면:
   - 작업 파일 영역 겹침 점검 → 안 겹치면 cp-merge, 겹치면 3-way merge.
   - 모든 게이트 **재실행**(별도 검증자 — Watcher의 output-reviewer가 paranoid 모드).
   - 통과 시 한 번에 commit & push.
4. **record** — 공유 인덱스 갱신(BACKLOG·ledger·필요 시 CONTEXT의 자기 섹션).
5. **schedule next** — 다음 라운드 ticket 4개 큐잉, 다시 1단계.

## STOP 6기준 (owner를 부르는 유일한 조건 — 변경 없음)

1. credential / license 필요 (콘솔 전용 키 등)
2. 콘솔 전용 단계 (Open API 부재)
3. 제품결함 확정
4. 과금·비가역 게이트가 owner 미승인
5. 엔진 능력 갭으로 설계 결정 필요
6. docs와 관측이 모순되고 안전 기본값 없음

위 6개에 **해당하지 않으면 owner를 부르지 않는다.** Watcher가 위반 감지 시
자동으로 해당 단위만 STOP하고 IB 발급, 다른 트랙은 계속 돈다.

## 측정 (자율성 KPI)

- **owner intervention rate** = (한 라운드에 owner 호출 횟수) / (라운드 수). 목표 ≤ 1/round.
- **track utilization** = 동시에 work 중인 트랙 수 / 4. 목표 ≥ 3.
- **cycle time** = T0 → 다음 dispatch 시각. 목표 ≤ 60분.
- **integration success rate** = Meta-Orch 게이트 재실행에서 통과한 비율. 목표 ≥ 90%.

세 지표가 모두 목표를 벗어나면 사다리·트랙 구성을 재검토 (Watcher의 책임).

## Low-verification first — 라운드 진입 규칙 (그림 원칙 ④)

**한 라운드의 ticket 풀은 다음 우선순위로 채운다** — 인포그래픽의
"Start with Low-Verification Tasks"를 운영화한 것이다:

1. **Tier-L (low verification)** — offline-gate-only:
   model yaml 편집, lifecycle JSON drift fix, validator/composer 코드,
   knowledge 정리, 정적 lookup 노드 분류, WebFetch만으로 끝나는 docs-mapper.
   → 게이트 = R1 + SC + offline pytest. **첫 라운드는 100% 여기서만 채운다.**
2. **Tier-M (medium)** — compose + dry-run:
   composer 결과를 합성해 오프라인 게이트 통과까지. live run 없음.
3. **Tier-H (high)** — live run 필요:
   light/non-destructive 슬라이스 우선, heavy/billable는 owner 승인 ticket으로
   분리해 STOP 4기준(과금·비가역)에 부합하는 한에서만.

라운드 N에서 Tier-L 큐가 비면 N+1에서 Tier-M, 그 다음 Tier-H로 단계 상승.
**역방향 강등도 가능** — Watcher가 통합 실패율 ≥ 20%를 감지하면 자동으로 다음
라운드 풀을 Tier-L로 강등.

## Self-repairing harness — Trace → Diagnose → Verified Fix → Lock

인포그래픽 하단의 "Unlock compounding reliability"를 4 트랙이 어떻게 분담하는지:

| 단계 | 책임 트랙 | 산출물 |
|---|---|---|
| **Trace** | Problem-Finder · failure-pattern-clusterer | 실패의 artifact(oplog/response body/status family) + 발생 run id |
| **Diagnose** | Problem-Finder · product-defect-finder + Coverage · docs-mapper | 원인 분류 + 후보 픽스(model/compose/엔진/제품결함) |
| **Verified Fix** | Coverage(model/compose) 또는 Platform(엔진) → Watcher · output-reviewer 별도 검증 | 게이트 통과한 패치 + (필요 시) 라이브 재시도 결과 |
| **Lock as Regression Test** | Meta-Orch (커밋 시) | **고친 사실을 `knowledge/validated-facts.md`에 영구화** + 해당 lifecycle/노드가 *VALIDATED* 승급 + waiver는 한정 기간만 + 동일 실패 재발 시 history.jsonl diff가 자동 IB 발급 |

**Lock의 의미**: 한 번 고친 사실은 다음 세션의 부트스트랩에 자동 인입되므로
같은 사람-개입을 두 번 요구하지 않는다. 이게 인포그래픽의 *compounding
reliability* — 라운드가 쌓일수록 도메인 지식이 단방향으로 증가한다.

## 매핑 — 인포그래픽 ↔ 이 설계 (점검표)

| 그림의 요소 | 이 설계에서의 위치 | 상태 |
|---|---|---|
| Schedule (다음 작업 결정) | Meta-Orchestrator의 cycle 5단계 | ✅ |
| Maker Agent | 4 트랙의 sub-agents (platform-improver/docs-mapper/etc.) | ✅ |
| Checker Agent (objective grading) | Watcher Track · output-reviewer (자기 트랙 산출물 금지) | ✅ |
| Persistent State on Disk | AUTONOMOUS-LOOP.md · CONTEXT.md · ledger.json · BACKLOG · PRODUCT-FINDINGS.md · knowledge/* | ✅ |
| 원칙 ① Separate Checker | Watcher가 별도 트랙으로 분리, self-review 금지 | ✅ |
| 원칙 ② Pre-set Exit Conditions | L0–L3 사다리 + 3 rev/window + no-progress 감지 + STOP 6기준 | ✅ |
| 원칙 ③ State on Disk (컨텍스트 윈도우 X) | 위의 6개 blackboard 파일 + git history | ✅ |
| 원칙 ④ Low-Verification first | "Low-verification first" 섹션 (Tier-L → M → H) | ✅ |
| 자가복구: Trace → Diagnose → Verified Fix → Lock | "Self-repairing harness" 섹션 | ✅ |
| (왼쪽 안티패턴) Human decides next | 제거 — Schedule을 Meta-Orch가 함 | ✅ |
| (왼쪽 안티패턴) Human manually checks | 제거 — Checker는 Watcher Track | ✅ |

## 병렬도 컨트롤러 — Watcher의 능동 임무 (parallelism floor, owner 2026-06-15)

Watcher Track은 산출물 검증만 하는 게 아니라 **동시 진행 작업 수를 바닥값 이상으로
유지**하는 능동 컨트롤러다. (배경: lead가 Q&A·조사로 포그라운드에 묶이면 동시성이
1 이하로 떨어져 멀티에이전트가 멈춘다 — 이걸 막는다.)

- **목표 동시성 ≥ 3. 활성 sub-agent가 ≤2이면**, Meta-Orch는 ready-queue에서
  **충돌 위험 없는 단위를 즉시 추가 디스패치**해 빈 슬롯을 채운다.
- **점검 시점**: 매 wake(agent 완료 알림 / user 메시지). 지속 데몬이 없으므로
  매 wake마다 "지금 몇 개 도나? <3이면 top-up" 을 **반드시** 먼저 수행한 뒤 응답한다.
- **충돌 회피 = 파일 소유권 맵** (같은 파일을 두 에이전트가 동시에 만지지 않는다):
  - `knowledge/formal/resources/<svc>.yaml` → **서비스 단위 배타** (서비스당 1 에이전트)
  - `regression/scenarios/lifecycles/*.json` + scenarios validator → 1 에이전트
  - `controlplane/`·`dashboard/` → Platform 1 에이전트
  - 공유 인덱스(`CONTEXT.md`·`ledger.json`·`IMPROVEMENT-BACKLOG.md`·`PRODUCT-FINDINGS.md`)
    → **Meta-Orch만** 편집(sub-agent 금지)
  - read-only 스캔(Watcher/Problem-Finder) → **무제한 병렬**
- **ready-queue**(항상 비지 않음): 열린 IB · docs(미검증) 노드의 compose/triage prep ·
  수작성→합성 전환 후보 · drift 발견 · userguide 미인입 서비스. 라이브 호출이 필요한
  단위는 prep/triage만 병렬화하고 실제 run은 Meta-Orch가 배치로 직렬 dispatch.
- **측정**: track utilization(동시 work 트랙/4) ≥ 3 을 매 라운드 로그.
