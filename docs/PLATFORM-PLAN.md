# SCP API Regression Test Platform — 업그레이드 계획

> 현재의 "엔진 + GitHub Actions" 시스템을 **완결된 플랫폼**으로 승격하는 계획.
> [ROADMAP.md](../ROADMAP.md)의 Phase 2(스케줄 회귀)·Phase 3(전용 서버)을
> 포괄하면서, 그 위에 **관리 UI·실행 개입·히스토리 리포팅**을 얹는다.

---

## 0. 현재 위치 진단 — 무엇이 있고 무엇이 없는가

핵심 진단: **데이터 평면(엔진)은 이미 플랫폼 수준이고, 제어 평면(Control
Plane)이 없다.** 시나리오는 선언적 JSON이고, 할당량·의존성도 데이터로
모델링되어 있으며, 결과는 통합 JSONL 스키마로 쌓인다. 빠진 것은 이것들을
사람이 (콘솔/git 없이) 보고·고치고·실행하고·개입하는 층이다.

| 요구 기능 | 현재 자산 | 갭 |
|---|---|---|
| 환경(검증계/운영계, 리전) 설정 | `core/config.py` 환경변수 (`SCP_REGION`, `SCP_ENV`, `SCP_SERVICE_HOSTS`) | 환경이 **일급 엔티티가 아님** — 한 번에 한 환경, 프로파일 저장/선택 불가 |
| 최신 API Spec 관리 | `spec/extract_catalog.py` + `spec/diff.py` (1,372 endpoints) | 갱신이 수동, 버전 보관·diff 알림·영향 분석 자동화 없음 |
| 계정/secret 관리 | `.env` / GitHub Secrets | 환경별 credential 세트 개념 없음 (환경 프로파일에 묶여야 함) |
| 서비스별 knowledge | `knowledge/formal/*.yaml` + validator, `validated-facts.md` | 편집이 git 직접 수정, 검증상태(VALIDATED/from-docs) 추적이 산문에 묻혀 있음 |
| 시나리오 저작 | `scenarios.json` + 53 service fragments + `dependencies.json` | 저작 UI 없음, **named suite**(full/smoke/서비스별) 개념이 CLI 필터에 암묵적으로만 존재 |
| 스케줄 실행 | GitHub Actions (cron 의도적 제거 상태) | 스케줄 관리 화면 없음, "어떤 시나리오를 어떤 시간에" 선언 불가 |
| 진행현황 대시보드 | `dashboard/ops.html` (S3 oplog viewer) | **실행 중** run의 라이브 뷰 없음, run 도중 개입(인스턴스 정리·중단) 불가 |
| 리소스 조치 | `cleanup.reconciler` (태그 기반 전량 sweep) | 특정 리소스 1개만 골라서 삭제하는 액션 없음 (콘솔 로그인 필요) |
| 결과 리포팅 | `dashboard/build.py` → GitHub Pages, `history.jsonl` | run별 결과가 **덮어써짐** — run 목록 → 클릭 → 그 시점 대시보드 복원이 안 됨 |
| AI 활용 | `agents/*.md` 역할 정의 (Claude Code 세션, 수동 위임) | API 통합 없음 — triage·시나리오 초안·spec 영향분석이 사람이 띄우는 세션에 의존 |

---

## 1. 목표 아키텍처

```
┌────────────────────────── Control Plane (신규) ──────────────────────────┐
│  Platform Server (FastAPI + DB)                                          │
│  ├─ 설정: 환경 프로파일 · spec 버전 · credential 참조                       │
│  ├─ 저작: 시나리오/스위트/knowledge 편집 + 검증                             │
│  ├─ 실행: 스케줄러 + 수동 트리거 → run 생성/큐잉                            │
│  ├─ 관제: 라이브 run 상태 · 리소스 인벤토리 · 개입 명령                      │
│  ├─ 리포팅: run 히스토리 · per-run 대시보드 스냅샷                          │
│  └─ AI 서비스: triage · 시나리오 초안 · spec 영향분석 (Claude API)          │
└──────────────┬────────────────────────────────────────────┬─────────────┘
               │ dispatch / 명령                  하트비트 / 결과 │
┌──────────────▼────────────────── Execution Plane (기존) ───▼─────────────┐
│  개발 기간: GitHub Actions runner  →  배포 전환: 동일 호스트 worker        │
│  spec → regression(smoke·read-chains·CRUD) → sweep → conformance        │
│  엔진 그대로: scenarios engine · budgets · registry · cleanup            │
└──────────────────────────────────────────────────────────────────────────┘
```

원칙:

1. **엔진은 건드리지 않고 감싼다.** 모든 실행이 이미 `python -m …` CLI라서
   GitHub Actions든 전용 worker든 같은 코드를 돈다 (ROADMAP Phase 3 설계
   그대로). 플랫폼 서버는 트리거·추적·개입만 담당.
2. **모든 설정은 데이터.** 환경 프로파일, 스위트, 시나리오, knowledge —
   전부 repo의 선언적 파일(`scenarios.json`·`knowledge/formal/`·`suites/`·
   `environments/`). **개발 기간**에는 git이 source of truth (편집 = 커밋 +
   push, 실행 = GitHub Actions가 클론). **배포 전환 후**에는 플랫폼 호스트의
   working copy가 source of truth — UI 편집 = 파일 직접 변경(validator
   게이트), worker가 같은 디렉토리를 읽어 즉시 반영, git은 이력·백업 용도
   (§3.1). 파일 포맷·검증 로직은 두 모드에서 동일하므로 전환은 "어디서
   읽고 쓰는가"만 바뀐다.
3. **secret은 플랫폼이 저장하지 않는다.** 환경 프로파일은 credential의
   *참조*(호스트 secret 파일 경로 또는 env var 이름)만 보관.
4. **regression 핫패스는 결정적으로 유지.** AI는 저작·후처리·옵트인 탐색에
   배치한다 (§4).
5. **호스트 불문 동일 배포 (최종 형태).** AWS·로컬 PC·내부 VM 어디든 같은
   형태로 뜨도록 Docker Compose 한 벌(server + worker + 공유 repo 볼륨)로
   패키징, DB는 파일 기반 SQLite. 호스트 이전 = 디렉토리(repo + DB +
   스냅샷) 이동. **개발 기간에는 이 형태를 강제하지 않는다** — 플랫폼
   서버 코드도 repo 안에서 git으로 개발하고, 실행은 GitHub Actions로
   계속하며, Compose 패키징과 worker 전환은 배포 직전 마일스톤(M4)에서
   수행.

---

## 2. 기능 영역별 설계

### 2.1 Planning — Global 설정

**환경 프로파일 (Environment Profile)** — 신규 일급 엔티티:

```yaml
# environments/stage-kr-west1.yaml (예시)
id: stage-kr-west1
label: 검증계 · kr-west1
region: kr-west1
env_code: e
host_template: https://{service}.{region}.{env}.samsungsdscloud.com
service_host_overrides: { ... }          # 기존 SCP_SERVICE_HOSTS
credentials_ref: secrets/stage-west      # 참조만 — 값은 secret store에
quota_overrides: { vpc: 5 }              # core/budgets.py DEFAULT_LIMITS 대체
safety_defaults: { allow_mutations: true, run_heavy: false }
```

- `core/config.py`의 `Settings`는 그대로 두고, 프로파일 → 환경변수로
  풀어주는 로더 한 겹만 추가 (기존 코드 무수정).
- 운영계 프로파일은 `allow_mutations: false` 같은 **프로파일 단위 안전
  게이트**를 갖는다 — 운영계에서 실수로 CRUD가 돌 수 없게.
- run은 항상 "스위트 × 환경 프로파일" 조합으로 생성 → 여러 리전/환경
  매트릭스 실행이 자연스럽게 가능.

**API Spec 관리:**

- spec refresh를 스케줄화 (매일): `extract_catalog` → 이전 버전과
  `spec.diff` → 변경이 있으면 **spec 버전 레코드** 생성 + diff 저장.
- 카탈로그 버전을 보관 (현재는 단일 파일 덮어쓰기) → "이 run은 어느 spec
  버전으로 돌았나"를 리포트에 표시.
- diff 발생 시 영향 분석 → 해당 서비스 부분 재실행 제안 (AI 활용 지점 §4-A1).

### 2.2 Planning — 서비스별 Knowledge

`knowledge/formal/`이 이미 머신 가독 YAML + validator를 갖췄으므로 구조는
유지하고 다음을 추가:

- **knowledge 브라우저/편집 UI**: 서비스별 제약·필수 파라미터·상태머신·ID
  필드 경로를 표 형태로 보기/수정. 저장 = git 커밋 + `validate.py` 통과 게이트.
- **검증 상태를 필드로 승격**: 각 fact에 `verified: {run_id, date}` vs
  `source: docs`를 구조화 — 현재 `validated-facts.md` 산문의 "(run
  27258520에서 검증)"을 데이터로. run이 어떤 fact를 실증하면 자동 갱신.
- 엔진이 쓰는 `scenarios.json`과의 정합성은 기존 validator가 담당
  (ROADMAP의 "formal이 장기적으로 source of truth" 방향 그대로).

### 2.3 Planning — 시나리오 저작도구

**Suite(스위트)를 일급 엔티티로** — 지금은 CLI 필터 조합으로만 표현되는
것을 명명된 선언으로:

```yaml
# suites/smoke.yaml
id: smoke
label: Smoke (읽기 전용, ~10분)
axes: [regression]
include: { smoke: true, read_chains: true }
mutations: false

# suites/full.yaml
id: full
axes: [regression, conformance]
include: { smoke: true, read_chains: true, crud: all, heavy: true }
lanes: from dependencies.json            # 기존 vpc_schedule 그대로

# suites/service-deep-filestorage.yaml
id: service-deep-filestorage
include: { crud: "storage/filestorage*", combos: [filestorage-*] }
```

**저작 UI** (엔진 데이터는 그대로 JSON):

- 시나리오 목록/검색 + step 편집기 (capture·poll·expect_status·태그).
- **의존 그래프 뷰**: `dependencies.json` + `knowledge/formal/cross-service.yaml`
  을 시각화 — "동시 VPC 몇 개, 어떤 시나리오가 공유 VPC adopt, 어떤 게
  직렬" 전략이 눈에 보이고 편집 가능하게.
- 저장 시 검증: JSON 스키마 + placeholder 참조 무결성 + 할당량 시뮬레이션
  ("이 스위트는 peak VPC 6개 — 한도 5 초과" 사전 경고).
- 신규 시나리오 초안은 AI 생성 → 사람 검토 (§4-A2).

### 2.4 Testing — 실행 관리 (Regression + Conformance 공통)

Regression과 Conformance는 이미 같은 결과 스키마를 쓰므로 **실행 관리도
하나의 모델**로: run = (suite, environment, trigger, options).

- **스케줄러**: `schedules` 테이블 — cron 식 × suite × 환경. 예:
  - 매일 02:00 `smoke` × 검증계-west (저비용 fence)
  - 매주 토 `full` × 검증계-west (heavy 포함)
  - spec-diff 발생 시 해당 서비스 suite 자동 큐잉
- **수동 실행**: UI에서 suite·환경·옵션(crud_filter, run_heavy…) 골라
  실행 — run 레코드 생성. **개발 기간**: `workflow_dispatch` API로 GitHub
  Actions 트리거 (현재 `.github/run-request` 파일 편집 방식을 대체).
  **배포 전환 후**: 동일 호스트 worker가 큐에서 직접 소비. run 레코드
  스키마와 UI는 두 모드에서 동일 — 디스패치 구현만 교체.
- **동시성 제어를 플랫폼이 소유**: 지금은 Actions `concurrency` 그룹이
  전부 직렬화 — 플랫폼 큐는 `dependencies.json`의 할당량 모델을 읽어
  "겹치지 않는 환경/할당량이면 병렬 허용"으로 개선.
- run 레코드: 상태(queued/running/sweeping/done/failed/aborted), spec 버전,
  옵션, 트리거 주체, 결과 아티팩트 경로.

### 2.5 Testing — 진행현황 대시보드 (관제 + 개입)

콘솔 로그인 없이 보고·조치하는 화면. 두 가지 데이터 소스:

1. **라이브 run 상태** — 엔진에 가벼운 보고 훅 추가:
   - `core/oplog.py`가 이미 리소스 이벤트를 S3에 쓰므로, 같은 자리에서
     플랫폼 API로도 이벤트 POST (scenario started/step done/polling/
     cleanup). 실패해도 run은 계속 (fire-and-forget).
   - 화면: 진행 중 시나리오, 현재 step, 폴링 대기 중인 리소스, 경과 시간,
     실시간 ok/soft/fail 카운터.
2. **리소스 인벤토리** — `core/registry`의 owner-tag 체계가 이미 모든 생성
   리소스를 식별하므로, "현재 살아있는 테스트 리소스" 목록을 환경별로 조회.

**개입 액션** (전부 기존 능력의 노출):

| 액션 | 구현 |
|---|---|
| 특정 리소스 삭제 | `cleanup.reconciler`를 단일 리소스 scope로 호출하는 API (이미 태그·삭제 호출 로직 보유) |
| run 전체 sweep | 기존 reconciler 그대로, run_id scope |
| 시나리오 skip / run abort | **명령 채널**: 엔진이 step 경계마다 pending 명령 확인 (`engine.py`에 체크포인트 1곳 추가). 단순 cancel보다 정밀 — abort 시에도 역순 cleanup은 수행 |
| 막힌 폴링 강제 타임아웃 | 같은 명령 채널 |

명령 채널은 처음부터 **엔진이 플랫폼 API를 HTTP로 폴링**하는 형태로 구현
(GitHub Actions runner에는 인바운드가 불가하므로). 배포 전환 후 동일
호스트 worker에서도 localhost HTTP로 그대로 동작 — 전환 시 변경 없음.

### 2.6 Reporting — 결과 + 히스토리

- **per-run 스냅샷**: run 종료 시 `observations.jsonl`·`findings.jsonl`·
  빌드된 대시보드를 run_id 디렉토리(S3 또는 서버 디스크)에 보관. 현재의
  "최신만 Pages에 덮어쓰기"에서 → **히스토리 목록 → run 클릭 → 그 시점
  대시보드 전체(커버리지 포함) 복원**.
- **히스토리 화면**: run 목록 (일시·suite·환경·spec 버전·pass/fail/soft·
  커버리지%·new-fail 수·소요시간) + 추세 그래프 (`history.jsonl` 확장).
- **비교 뷰**: run A vs B diff — 무엇이 새로 깨졌나/고쳐졌나. 기존
  baseline 비교 로직(`conformance/baseline.py`, known_issues)을 양 축으로
  일반화.
- 알림: new-fail 발생 시 채널 통보 + AI triage 요약 첨부 (§4-B1).

---

## 3. 기술 선택 (확정 + 권고)

| 항목 | 선택 | 근거 |
|---|---|---|
| 플랫폼 서버 | **FastAPI (Python)** | 엔진과 같은 언어 — catalog/budgets/results 모듈 직접 import 가능 |
| 프론트 | **서버 렌더 + htmx** (확정) | 기존 dashboard/build.py HTML 자산 재활용, 별도 빌드체인 없음 |
| 호스팅 (최종) | **호스트 불문** (AWS·로컬 PC·내부 VM) — Docker Compose 패키징 (확정) | server + worker + 공유 repo 볼륨 한 벌. 어디서 떠도 동일 동작, 이전 = 디렉토리 이동. 패키징은 배포 직전(M4) |
| DB | **SQLite** (파일 기반) | 호스트 불문 요구에 부합 — 외부 DB 의존 없음. runs/schedules 규모상 충분 |
| 실행기 | **개발 기간: GitHub Actions** (dispatch API) → **배포 전환(M4): 동일 호스트 worker** | 개발 중에는 git push가 협업 매체라 Actions가 자연스러움. 파일 직접 편집 모델(§3.1)이 켜지는 시점에 worker로 전환 (ROADMAP Phase 3 Step 2) |
| 결과 저장 | 기존 JSONL 유지 + run별 스냅샷 (호스트 디스크, S3는 선택 백업) | 스키마 변경 없음 — `dashboard.build`가 이미 JSONL만 읽음 |
| AI 통합 | **Claude Agent SDK** (headless) | `agents/*.md` 역할 정의를 그대로 시스템 프롬프트로 재사용 가능 |

### 3.1 파일 기반 편집 모델 (최종 형태 — 배포 직전 M4에서 전환)

> 개발 기간 동안 UI 저작 기능은 같은 검증 로직을 통과시키되 결과를 **git
> 커밋 + push**로 반영한다 (Actions runner가 클론해서 읽으므로). 아래
> 모델은 배포 전환 시점에 활성화되는 최종 동작이며, "검증 → 쓰기" 코드는
> 두 모드가 공유하고 마지막 반영 단계(push vs 로컬 파일 교체)만 다르다.

UI에서의 모든 저작(시나리오·스위트·환경 프로파일·knowledge)은 **플랫폼
호스트에 있는 repo working copy의 파일을 직접 변경**한다:

1. UI 저장 → 임시 파일에 기록 → validator 실행 (JSON 스키마 ·
   `knowledge/formal/validate.py` · placeholder 무결성 · 할당량 시뮬레이션)
2. 통과 시 원자적 교체(rename) + **자동 로컬 git 커밋** (작성자·시각 포함)
   → 변경 이력과 1-click 되돌리기 확보
3. 실패 시 원본 유지 + 오류를 UI에 표시
4. 원격 push는 운영자 재량 (백업/공유 목적) — 플랫폼 동작에는 불필요

run 시작 시점에 working copy의 git revision을 run 레코드에 기록 → "이
run은 어떤 시나리오/knowledge 버전으로 돌았나" 추적 가능. 실행 중 편집이
진행 중인 run에 영향을 주지 않도록 worker는 run 시작 시 정의 파일을
스냅샷(복사)해서 사용한다.

---

## 4. AI 활용 지도 — 어디에 AI가 필요한가

설계 원칙: **테스트 실행의 정상 경로(핫패스)에는 AI를 넣지 않는다.**
regression의 가치는 결정적·재현 가능·저비용 실행인데, 같은 입력에 같은
결과가 나와야 "새 fail = 진짜 변화"라는 신호가 성립한다. AI는 (A) 저작
시점, (B) run 후처리, (C) 옵트인 탐색 모드에 배치한다. 현재 `agents/`의
8개 역할이 사실상 이 지도다 — 수동 Claude Code 세션을 플랫폼이 호출하는
파이프라인으로 승격하는 것.

### A. 저작/설계 시점 (AI 핵심 가치 — 자동 파이프라인화)

| # | 작업 | AI 역할 | 현재 대응물 | 런타임 AI? |
|---|---|---|---|---|
| A1 | **Spec diff 영향 분석** | 신규/변경 엔드포인트 해석, 영향받는 시나리오 식별, 재실행 범위 제안 | Spec-Intel agent | ❌ 스케줄된 배치 |
| A2 | **시나리오 초안 생성** | 신규 서비스의 docs + knowledge로 lifecycle JSON 초안 작성 (request body 추론 포함) → **사람 검토 후 머지** | Service agents | ❌ 저작 시점 |
| A3 | **Knowledge 큐레이션** | run 로그에서 validated facts 추출 (ID 필드 경로, 상태머신, 비문서 필수 필드) → formal YAML 인코딩 | Domain-Knowledge agent | ❌ run 후 배치 |
| A4 | **AI-usability 평가** | "AI가 docs만 보고 이 API를 쓸 수 있나" 판정 — 본질적으로 AI가 필요한 유일한 *평가* 축 | AI-Evaluator agent | ❌ spec 변경 시 |
| A5 | conformance 규칙 제안 | 반복 발견되는 결함 패턴 → 새 rule 코드 초안 | Conformance agent | ❌ 비정기 |

### B. Run 후처리 (고가치 — 플랫폼 통합 1순위)

| # | 작업 | AI 역할 | 비고 |
|---|---|---|---|
| B1 | **실패 triage 자동화** | new-fail을 분류: ① 환경/할당량 이슈 ② spec 변경 ③ 테스트(body) 버그 ④ 진짜 regression. known_issues 갱신·재실행·시나리오 수정 PR을 각각 제안 | 현재 `docs/HANDOFF-fail-new-triage.md` 수작업의 자동화 — **가장 먼저 만들 것**. run을 블로킹하지 않는 비동기 후처리 |
| B2 | 리포트 자연어 요약 | run 결과 → 경영/팀 보고용 한 문단 + 주요 변화 하이라이트, 알림에 첨부 | 저비용·즉시 효과 |
| B3 | 테스트 버그 자동수정 PR | triage가 ③으로 판정한 건의 시나리오 JSON 수정 PR 생성 → 사람 리뷰 | B1의 확장, 사람 게이트 필수 |

### C. 런타임 inline (옵트인 — 기본 OFF)

| # | 작업 | AI 역할 | 게이트 |
|---|---|---|---|
| C1 | **탐색 모드 self-healing** | 4xx 에러 메시지를 읽고 request body 수정 후 1회 재시도, 성공 시 fact로 기록 (→ A3로 환류) | 신규 엔드포인트 첫 검증 run에서만 (`SCP_AI_EXPLORE=true`). 정기 regression에선 OFF — 비결정성 오염 방지 |
| C2 | 대시보드 자연어 질의 | "지난주 대비 networking에 뭐가 깨졌어?" 챗 | 읽기 전용, 선택 기능 |

**결론 — "평소 런타임에 AI가 필요한가?"에 대한 답:**
**아니오.** 정기 regression/conformance 실행 자체는 기존 결정적 엔진으로
충분하며 그래야 한다. AI가 *상시* 필요한 곳은 실행 전후다 — spec이
바뀔 때(A1·A2·A4), run이 끝났을 때(B1·B2). 런타임 inline AI(C1)는 신규
커버리지 개척 모드에서만 켜는 옵션이며, 그 산출물도 즉시 결정적 데이터
(validated fact, 수정된 body)로 환원시켜 다음 run부터는 AI 없이 돌게 한다.

---

## 5. 단계별 로드맵

기존 ROADMAP Phase 1(커버리지 100%)은 그대로 선행 조건. 플랫폼 작업은
병행 가능하되 M2부터는 Phase 1 완료가 전제.

### M0 — 기반 정비 (엔진 레벨, 서버 불필요) — DONE (live 검증 대기)
- [x] Suite 정의 도입 — `suites/*.yaml` + `core/suites.py` (render →
      `.github/run-request` 옵션으로 컴파일; dispatch `suite` 입력 / 파일
      `suite=` 라인 양쪽 지원, 명시 라인이 suite 기본값을 override)
- [x] 환경 프로파일 도입 — `environments/*.yaml` + `core/profiles.py`
      (export → `$GITHUB_ENV`/shell; credential은 참조만; `forbid:` 게이트를
      `core/config.py`가 강제 — 운영계 프로파일은 구조적으로 read-only)
- [x] run별 결과 스냅샷 보관 — `core/snapshot.py` (oplog 버킷
      `runs/<run_id>/snapshot/`에 results JSONL + 대시보드 HTML + meta.json,
      meta에 suite/profile/카탈로그 sha256 기록)
- [x] 엔진 보고 훅 — `core/oplog.py`에 `APITEST_PLATFORM_URL` POST 미러
      (fire-and-forget, 기본 비활성 — M1 서버가 수신)

### M1 — Control Plane MVP (실행은 GitHub Actions 그대로) — DONE (배포 대기)
- [x] FastAPI 서버 + SQLite — `controlplane/` (suites/environments는 repo
      파일을 라이브로 읽고, DB는 runs/schedules/events/triage만 보유)
- [x] 수동 실행 UI (suite × 환경 → `workflow_dispatch`) + cron 스케줄러
      (UTC, 30s 폴링 데몬; dispatch 미설정 시 기록만 — 로컬 개발 가능)
- [x] run 히스토리 화면 + per-run 대시보드 서빙 — DB 기록 + oplog 버킷
      index 아카이브 병합, `runs/<id>/snapshot/` 프록시로 과거 대시보드 복원
- [x] 라이브 추적 — oplog 미러(`APITEST_PLATFORM_URL`) 수신
      `/api/ingest/events` → run 상태 자동 전이 + 마일스톤 타임라인
- [x] **AI B1: 실패 triage 후처리** — baseline 외 신규 fail을 Claude
      (claude-opus-4-8, structured output)가 environment/spec_change/
      test_bug/real_regression으로 분류, `agents/regression-agent.md`를
      시스템 프롬프트에 재사용. 수동 버튼 + `PLATFORM_AUTO_TRIAGE` 자동 훅
- [x] B2 요약 알림 — `PLATFORM_NOTIFY_WEBHOOK` (Slack 호환)
- 배포 절차: 서버 기동(`controlplane/README.md`) + repo Variables에
      `APITEST_PLATFORM_URL`/`APITEST_PLATFORM_TOKEN` 설정 + dispatch PAT

### M2 — 관제와 개입
- [ ] 라이브 run 뷰 (보고 훅 수신 → 진행현황 스트림)
- [ ] 리소스 인벤토리 + 단일 리소스 삭제 액션 (reconciler scope 호출)
- [ ] 명령 채널 (engine 체크포인트 폴링): 시나리오 skip · run abort ·
      폴링 강제 종료
- [ ] run 비교 뷰 (A vs B diff)

### M3 — 저작도구 + AI 파이프라인 완성
- [ ] 시나리오/스위트 편집 UI — 검증 + 쓰기 로직 (개발 기간 반영 방식:
      git 커밋 + push → Actions가 클론해서 사용)
- [ ] 의존 그래프 시각화·편집 (dependencies.json ↔ cross-service.yaml)
- [ ] knowledge 브라우저/편집 + 검증상태 필드화
- [ ] AI A1(spec-diff 분석→부분 재실행 제안)·A2(시나리오 초안)·A3(fact
      추출) 파이프라인 + C1 탐색 모드

### M4 — 배포 전환 (마지막: 로컬 파일 모드로 컷오버)
- [ ] 동일 호스트 worker: run 큐 직접 소비 → spec → regression → sweep →
      conformance → dashboard (ROADMAP Phase 3 Step 2의 `runner/` 스크립트)
- [ ] Docker Compose 패키징 (server + worker + repo 볼륨) — AWS/로컬/VM 공통
- [ ] UI 편집 반영을 git push → **호스트 파일 직접 변경**(§3.1)으로 전환
      (검증 로직은 그대로, 마지막 반영 단계만 교체)
- [ ] 디스패치를 workflow_dispatch → worker 큐로 교체, Actions는 보조로 유지
- [ ] 운영 runbook: 신규 호스트 프로비저닝 = compose up 한 번

### 마일스톤별 가치
- M0만으로도: 멀티 환경 매트릭스 + run 히스토리가 생김 (서버 없이).
- M1로: "스케줄에 따라 수행 + 히스토리 리포팅" 요구 충족.
- M2로: "콘솔 로그인 없이 조치" 요구 충족.
- M3로: "저작도구 + AI 상시 파이프라인" 완성.
- M4로: 호스트 불문 단일 패키지 배포 — 플랫폼 완결. **M0~M3 내내 개발·
  협업·실행은 git + GitHub Actions로 원격에서 계속 가능.**

---

## 6. 결정 사항

### 확정 (2026-06-11)

1. **프론트엔드 스택** — FastAPI 서버 렌더 + htmx.
2. **호스팅** — 특정 위치에 묶지 않음 (AWS·로컬 PC·내부 VM 모두 가능) →
   Docker Compose 한 벌로 호스트 불문 동일 배포, SQLite, secret은 호스트
   파일/env 참조.
3. **UI 편집 반영** — 최종 형태는 호스팅 위치의 **파일 직접 변경** (§3.1),
   이때 실행기도 동일 호스트 worker. 단, **플랫폼 개발이 완료될 때까지는
   git + GitHub Actions를 유지** (원격 협업 매체) — 파일 직접 변경 모드로의
   컷오버는 배포 직전 M4에서 수행.

### 미결

4. **multi-tenancy 여부** — 팀/프로젝트 단위 분리가 필요한가? (필요 시
   M1 DB 스키마에 org 컬럼 선반영)
5. **Conformance baseline의 환경별 분리** — 검증계와 운영계의 known_issues
   를 분리 관리할 것인가 (권고: 환경 프로파일에 귀속).
