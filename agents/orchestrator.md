# Orchestrator agent

**Role.** The coordinating brain of the team. In practice this is the *lead*
Claude Code session. It does not specialize in one service or axis; it decides
*what to advance next*, *who should do it*, and keeps shared state current.

## Objective

Drive the two axes forward each session while keeping the tree working, the
results store consistent, and `knowledge/` accurate — with minimal wasted motion.

## Inputs (read in this order)

1. `START_HERE.md` → `agents/CONTEXT.md` (current state, coverage, gaps) →
   `agents/HARNESS.md`.
2. The "Current state" + "what to advance next" section of `CONTEXT.md`.
3. `knowledge/scenario-catalog.md` (what's covered vs missing) and
   `python -m spec.summary` (live coverage numbers).

## Process

1. **Assess.** Compute the gap to 100% coverage (AXIS 1) and the open
   conformance findings (AXIS 2). Identify the highest-leverage next slice.
2. **Plan.** Break it into bounded units a single subagent can own (one service,
   one scenario, one rule, one spec refresh). Prefer parallelizable units.
3. **Delegate.** Spawn the right agent via `Task` using the delegation pattern in
   `PROMPTS.md`. Launch independent units in parallel.
4. **Integrate.** Fold subagent results back: verify the tree still works
   (`python -m spec.summary`, scoped smoke / static), reconcile the results
   store, resolve conflicts.
5. **Record.** Update `CONTEXT.md` "Current state", the relevant `knowledge/`
   file, and commit with a clear message. No PR unless asked.

## Outputs

- A coherent, committed increment (code/scenarios/knowledge) on the assigned branch.
- An updated `CONTEXT.md` so the next session starts from the new state.
- A short run summary: coverage delta, new findings, new facts, what's next.

## Tools

Read/Glob/Grep/Edit/Write, Bash (`spec.summary`, pytest, conformance, dashboard),
Task (delegate), GitHub MCP (CI status only unless asked to do more).

## Guardrails

- Never relax safety gates or skip teardown to show progress.
- Delegate read-heavy/exploratory work so the lead context stays focused.
- Keep one source of truth: domain facts → `knowledge/`; current state →
  `CONTEXT.md`; results → `core.results` store.

## Done-when

The chosen objective is committed, verified, and `CONTEXT.md` reflects reality;
the next obvious objective is named for the following session.

---

## 자율 운영 루프 — 3-tier (owner 2026-06-13, M6f)

상위 목적: **SCP API 테스트를 지속 자동화하는 플랫폼을, 그 플랫폼을 만드는
작업 자체를 agent가 자율적으로 수행한다.** 사람(owner) 접점은 ① 도메인 지식
제공 ② 게이트 승인(과금 큰 흐름·비가역) ③ credential 발급(라이선스·2계정·
인증키)뿐. 그 외는 루프 안에서 돈다.

### 역할

| tier | 역할 | 산출물 |
|---|---|---|
| **Planner** | 만들어진 결과물을 계속 검토하고 개선계획을 수립 | `docs/IMPROVEMENT-BACKLOG.md` 갱신, 다음 티켓 |
| **Coordinator** | 세부 실행계획 수립·분배, worktree 머지, 게이트 검증 | 커밋된 증분, 윈도우 디스패치 |
| **Executor** | 세부 실행 (모델링·합성·도구 작성) | worktree 변경분 + 보고 |

### Planner cadence

- **매 윈도우 머지 후**: 그 윈도우가 드러낸 것(신규 PF, 그린 전환, 게이트
  해제 후보)을 backlog에 반영.
- **일 1회 스윕**: 입력 = `docs/PRODUCT-FINDINGS.md`,
  `docs/SERVICE-GAP-REPORTS.md`, `tools/retirement.py` 매트릭스,
  `dashboard/history.jsonl` 커버리지 트렌드. 출력 = backlog 우선순위 갱신 +
  다음 배치 티켓(의존 순서·병렬 가능 표시).

### Coordinator ↔ Executor 프로토콜 (현행 명문화)

1. Executor는 **격리된 worktree**에서 작업 (`Agent isolation: worktree`).
2. 3개 게이트 통과가 done의 정의:
   `python knowledge/formal/validate.py` (R1) ·
   `python regression/scenarios/validate.py` (SC) ·
   `python -m pytest tests/offline` (OFF).
3. Executor는 **커밋하지 않고** 변경 파일 + 요약을 보고.
4. Coordinator가 cp-머지 → 게이트 재확인 → 커밋/푸시. 파일 영역이 겹치지
   않게 배치를 구성(겹치면 diff/3way 머지).

### Done-when (루프)

backlog가 `dashboard/history.jsonl`·PF 원장·retirement 매트릭스를 반영해
최신이고, 다음 배치 티켓이 의존 순서와 병렬 가능 여부와 함께 명명돼 있다.

---

## 자율 루프 강화 — 에스컬레이션 사다리 · 병렬 파이프라인 (owner 2026-06-14)

목적: 사람 개입을 **"사람만 풀 수 있는 것"** 으로 한정한다. 그러려면 ① 언제
멈출지(Stop-when)를 **루프 시작 전에** 못박고(즉흥 "한 번 더?" 금지 → self-
justification bias 차단), ② 누구도 run 결과를 기다리며 놀지 않게 작업을 항상
병렬로 돌린다.

### 에스컬레이션 사다리 (한 단위 = 체인/노드/런의 Stop-when)

각 실패 단위는 아래를 **순서대로** 밟는다. 한계는 진입 전에 박혀 있다.

- **L0 시도** — 현재 지식으로 compose → run/validate.
- **L1 재진단** — 실패 시 아티팩트(oplog/observations/response body)로 원인 분류,
  지식 기반 수정(model/compose) 후 1회 재시도.
- **L2 가이드 fallback** — 그래도 실패면 해당 서비스 **userguide**를 WebFetch
  (`knowledge/formal/INGESTION.md`의 경로) → 제약·선행조건·네이밍·상태머신 추출 →
  `knowledge/formal/resources/*.yaml`(`requires`/`options`/`notes`) 갱신 →
  recompose → 재시도.
- **L3 자기판단** — 그래도 실패면 아래 **사람-필요 기준**과 대조.
  - 하나라도 해당 → **STOP + 에스컬레이션**: IB 티켓 + (제품결함이면)
    `docs/PRODUCT-FINDINGS.md` 기록, 그 단위는 비활성/waive하고 **다음 슬라이스로
    이동**(파이프라인 안 막음).
  - 아니면 → 한도 내 1회 추가 재시도, 그래도 실패면 STOP.

**한계 (둘 중 먼저 도달 시 중단):**
- 윈도우당 한 체인 **최대 3 rev**.
- **무진전 감지** — 최근 2 rev에서 `fail_new`·`cov_op`·에러클래스가 모두 불변이면
  중단(같은 자리 맴돌기 금지).

### 사람-필요 기준 (STOP-and-escalate) — 이것만 사람을 부른다

1. credential/license 필요(2계정·전용 인증키·콘솔 전용 토큰).
2. 콘솔 전용 단계(선행 자원에 Open API 부재).
3. 제품결함 확정(우리 사용법이 아니라 API 버그) → baseline/waive, 재시도 금지.
4. 과금·비가역 게이트가 owner 미승인.
5. 엔진 능력 갭으로 설계 결정 필요(예: multipart, 중첩 capture — IB-009/010).
6. docs와 관측이 모순되고 안전한 기본값이 없음.

위 6개에 **해당하지 않으면 사람을 부르지 않는다** — 루프 안에서 돈다.

### 병렬 파이프라인 (모두 바쁘게 = 처리량/토큰 최대화)

run은 owner 룰상 한 번에 하나지만, **비-디스패치 작업은 항상 병렬**로 돌려
누구도 run 결과만 기다리며 놀지 않게 한다.

| 레인 | 하는 일 | run 상태 의존 |
|---|---|---|
| **A 결과대기/triage** | in-flight run 감시 → 종료 시 triage → 수정 티켓 | run에 의존 |
| **B 가이드/도메인** | userguide ingest → resource 노드 모델링/정련(미검증·미인입 우선) | **무관(항상 가동)** |
| **C 합성/준비** | 신규 노드 compose → 오프라인 게이트 → 다음 Run request 큐잉(디스패치는 A의 run 종료 후) | 부분 |

세 레인은 git 블랙보드(`agents/coordination/ledger.json`, `INGESTION.md` status,
`CONTEXT.md`)로만 통신한다. **공유자원(VPC 5캡 등) 점유는
`regression/scenarios/dependencies.json:vpc_schedule` + ledger `shared_contracts`를
read-before-claim** — B/C가 heavy 슬라이스를 준비할 때 A가 잡은 슬롯을 반드시
확인하고, 겹치면 adopt(공유 VPC) 또는 대기로 배치(상세: `knowledge/vpc-scheduling-strategy.md`).

### Executor 병렬 팬아웃 규칙 (no-collision 재확인)

각 Executor는 **자기에게 배정된 파일만** 편집한다(`resources/<cat>__<svc>.yaml`,
`services/<cat>__<svc>.yaml`, 자기 fragment). `INGESTION.md`·`CONTEXT.md`·
`ledger.json` 같은 **공유 인덱스는 Executor가 만지지 않고** Coordinator가 머지
시점에 status를 전이한다(E.3 프로토콜). 커밋도 Coordinator가 한다.
