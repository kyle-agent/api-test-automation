---
status: CONFIRMED (오너 확정 — 2026-06-26 · IA = Catalog · Modeling · Testing · Reporting · **2026-07-07 개정: Catalog→우측 유틸 링크, Modeling이 흡수 — §개정 참조**)
for: owner + platform
---

# 플랫폼 방향성 — 쉽게 정리

> 기능을 더 붙이기 전에, 이 플랫폼이 **"누가 · 무엇을 · 어떻게"** 흘러가는지 쉽게
> 정리하고, 지금 구현과 뭐가 다른지 본 뒤 방향을 잡는 문서. (용어·약어 최대한 배제)

## 한 줄 요약 (정정)

이 방향("**한 그래프를 단계마다 다른 얼굴로**")은 **새 발명이 아니라, 이미 결정되고
절반쯤 구현된** `poc/scenario-viz/PLATFORM-PLAN.md`다(owner 2026-06-15 결정). 진짜 문제는
같은 일을 **두 앱(controlplane + console2)이 따로** 하고 있어 흐름이 쪼개져 보이는 것.
→ **새로 그리지 말고, 이미 있는 둘을 한 흐름으로 수렴**하고(면 1), **공개 커버리지
대시보드는 별도 유지**(면 2)하면 된다.

---

## ✅ 확정 IA (2026-06-26) — `Catalog · Modeling · Testing · Reporting`

> 아래 "쉽게 정리"(§1~§6, 2026-06-25 탐색)를 오너와 끝까지 좁혀 **확정**한 최종 정보구조.
> 본문은 여기에 이르는 배경·근거이며, **네이밍·배치는 이 절이 정본**이다.

**면 2개**: 면① = 일하는 콘솔 하나(이 4메뉴 = controlplane 척추) · 면② = 공개 커버리지
대시보드(별도 발행 · Reporting에서 링크아웃).

**면① 상단 네비 = 4단계 흐름 (좌→우 = ①→④):**

```
Catalog          Modeling              Testing                          Reporting
(① 재료)         (② 레시피 저작)        (③ 요리)                          (④ 평가)
──────────       ──────────────        ─────────────────────────        ──────────
API 1,372    ─✏️→  노드편집 = 모델 지도    ┌ Test Planning  (구성·큐)        커버리지
읽기전용(RO)       click 노드 = 레시피     └ Test Execution (실행·런타임·중단)  색칠지도+2축+추세
                                          = console2 통째 (서브탭)           └→ 면② 공개본
```

| 메뉴 | 단계 | 무엇 | 그래프 얼굴 | RO/RW | 코드(오늘) |
|---|---|---|---|---|---|
| **Catalog** | ① 재료 | API 인벤토리 1,372 · 검색·서비스 그룹 | (그래프 *전* — 목록) | **RO** | `data/api_catalog` · catalog 뷰 |
| **Modeling** | ② 레시피 저작 | 리소스 노드 저작: 의존·생성바인딩·옵션·캡처·검증·삭제 | **모델 지도 · 편집** | RW | `resource_form`+`resource_model`+`authoring` → `knowledge/formal/resources/*.yaml` |
| **Testing** | ③ 요리 | `Test Planning`(선택→합성DAG→큐) ∣ `Test Execution`(실행·실시간·중단·런 리포트) | **합성 + 라이브** | RW | **console2 흡수** → `console_api.py` + console2 프론트 |
| **Reporting** | ④ 평가 | 커버리지 색칠지도(서비스→리소스) · 2축(regression+conformance) · 추세 · 공개본 링크 | **색칠** | RO | `reporting` / `dashboard` |
| **+Knowledge** | 참조 (5번째 항목 — 단계 흐름 밖) | 지식 파일 브라우저: `knowledge/*.md` · `knowledge/formal/*` · suites · environments | (그래프 없음 — 목록) | **RO** | `/knowledge` — *2026-07-03 owner 확정 보강* |

**핵심 원칙 — "그림 하나, 여러 얼굴":** 중심 위젯은 `composer.graph_view` + scene 렌더러
**하나**. Modeling(모델지도·편집) · Testing(합성+라이브) · Reporting(색칠) 셋이 **같은
그래프에 데이터만 갈아끼움**. Catalog만 그래프 *이전*(목록). (S1b 렌더러 통일이 그 토대.)

**확정 시 풀린 쟁점(근거):**
1. **B(레시피 저작) ≠ C(시나리오 합성)** — 저작(B: `*.yaml` git·가끔·깊게·담당자)과 합성
   (C: 휘발·매실행·빠르게·운영자)은 *다른 일*. → **B = Modeling**(영속 설계), **C = Testing의
   Test Planning**(실행 준비). 혼선은 console2 "구성"(=C)을 한때 Planning이라 부른 데서 왔고,
   C를 Testing 제자리로 보내 해소.
2. **이름 "Modeling"(≠ Planning)** — (a) "Planning"을 Testing 하위 *Test Planning* 으로 쓰므로
   상단 재사용 시 충돌, (b) 레시피 저작은 "계획"이 아니라 *모델링*(코드의 `resource_model`과
   일치). 표준 QA의 *Test Planning(설계) / Test Execution(실행)* 은 Testing **하위**에 그대로.
3. **Catalog는 순수 RO** — `api_catalog`는 자동추출 → 손편집은 재추출 때 소실 → 편집 레이어
   (리소스 yaml)는 물리적으로 분리. 단 "보다가 바로 고치기"는 Catalog 행의 **`✏️ 레시피 편집 →`**
   링크가 **Modeling 에디터**를 여는 것으로 제공(입구만, 편집은 Modeling).
4. **console2는 안 쪼갬** — 구성+실행을 Testing **서브탭**으로 통째 흡수(큐가 두 서브탭의
   이음매). 별도 상단메뉴로 쪼개면 console2의 한 흐름·큐 상태가 깨짐.

**네이밍 규칙:** Catalog = 명사(참조), Modeling·Testing·Reporting = 동명사(활동 영역).

**수렴 매핑(앱 3개 → 메뉴 4개):** controlplane Catalog → **Catalog** · controlplane
Model(`resource_form`) → **Modeling** · console2(구성 + 실행&리포트) → **Testing** ·
dashboard + reporting → **Reporting**(+ 면② 공개본).

### 개정 (2026-07-07 오너 결정) — Catalog를 유틸 링크로 강등, Modeling이 흡수

> 위 2026-06-26 확정 본문은 역사로 그대로 두고, 이 절이 네비 배치의 **최신 정본**이다.

- **상단 네비 = 3단계**: `Modeling → Testing → Reporting (+Knowledge · 🤖AI)`.
  **Catalog는 최상위 네비 단계에서 내려와** 우측 유틸 영역(📊 대시보드 옆)의
  **📖 카탈로그** 링크가 된다. `/catalog` 라우트·딥링크는 전부 유지(제거 없음) —
  전체 인벤토리 **참조 화면**으로 남는다.
- **Modeling이 서비스별 엔드포인트를 인라인으로 품는다**: Modeling 표의 각 서비스
  그룹 행에 **"API N (모델됨 M · 미모델 K)"** 집계 + 엔드포인트 드로어(htmx lazy
  파셜 `/planning/resources/map/endpoints?service=…`) — 모델됨 칩은 그 노드 편집
  딥링크. 매핑이 애매한 엔드포인트(리터럴 vs 자리표시자 호환 케이스)는 미모델로
  과대계상하지 않고 **미매핑** 버킷으로 분리(분류 규칙 정본:
  `controlplane/resource_routes.py` "카탈로그 인라인" 주석).
- **근거**: Catalog는 데이터로는 필수(커버리지 **분모** · "이 서비스에서 뭐가
  미모델인가" 질의 · conformance 단위)지만 **워크플로 단계로는 약하다** — 사용자는
  "목록을 훑자"가 아니라 "무엇을 모델링/검증하자"에서 시작하므로, 그 데이터를
  Modeling의 작업 맥락 안으로 옮기고 단계는 셋으로 줄인다.
- 홈 파이프라인 스트립도 4칸 → 3칸 (Modeling 칸 부제 "전체 {N} API의 테스트 모델
  저작"으로 분모 표기 유지).

---

## 1. 이 플랫폼이 하는 일

SCP 클라우드의 수많은 API가 **실제로 잘 작동하는지 자동으로 테스트**하고, **얼마나
테스트했는지(커버리지)**를 한눈에 보여준다.

## 2. 일이 흘러가야 하는 순서 — 4단계 (요리 비유)

| 단계 | 하는 일 | 누가 | 지금 어디에 있나 |
|------|---------|------|------------------|
| ① **재료 목록** 📋 | 어떤 API가 있는지 전부 목록화 | AI가 문서에서 추출 | controlplane **Catalog** + 정적 `catalog.html` |
| ② **레시피 작성** 📝 | 서비스마다 의존관계·생성순서·실행조건 정리 | **AI 초안 + 담당자 마무리** | controlplane **Model** (`resource_form`+`authoring.py`) = "저작화면" |
| ③ **요리(실행)** 🔥 | 레시피 골라 실제 실행 | 테스트 운영자 | controlplane **Compose→Validate→Run** + **console2**(더 폴리시된 실행) |
| ④ **평가** 📊 | 결과+커버리지 표시 | 결과 보는 사람(다수) | **dashboard**(공개·별도) + console2 **R1~R4**(런별 리포트) |

> **즉 4단계는 이미 controlplane의 IA로 들어와 있다**: 상단 네비 `Overview · Plan · Run ·
> Report · Knowledge`, Plan 안에 선형 스테퍼 **Catalog → Model → Compose → Validate**.
> 사장님이 물은 "기존 저작화면" = 이 중 **Model 단계**(노드별 폼 + validator 통과 시 git 커밋).

## 2-1. ②단계 깊이 보기 — "레시피"는 지금 어디에, 커버리지 결과물은 어디 담기나

> 오너 질문: "어떤 순서·어떤 제약으로 테스트해야 커버리지가 오르는지를 저작하는데,
> 이게 지금 어디에 정의돼 있냐?"

**레시피가 적히는 곳 = `knowledge/formal/resources/<서비스>.yaml`** (서비스마다 1개, 총 60개).
한 리소스 = 이런 블록(networking/vpc 의 subnet 예시, 주석은 설명):

```yaml
subnet:
  requires: [vpc]                      # ← 순서/의존: 서브넷 전에 VPC 먼저
  create:
    endpoint: "POST /v1/subnets"       # ← 어떤 API로 만드는지
    body: { cidr: "{opt.cidr}", vpc_id: "{vpc.vpc_id}", type: GENERAL }
    options:
      cidr: { pick: sub-block-of, of: vpc.cidr }   # ← 제약: VPC 대역의 하위 /24
  ready: { field: "$.subnet.state", until: ACTIVE } # ← 준비될 때까지 대기 조건
  verify:
    - { endpoint: "GET /v1/subnets/{subnet_id}/vips" }  # ← 추가로 커버하는 API
  delete: { endpoint: "DELETE /v1/subnets/{subnet_id}" }  # ← 역순 삭제
  provenance: VALIDATED                # ← "실제 2xx로 검증됨" 표시
```

`requires`=순서, `options/body/ready/verify`=제약·조건. 이게 **"레시피"**이고, composer가
실행형(lifecycle)으로 자동 변환. UI에선 controlplane **Model 단계**에서 이 파일을 편집(저작)한다.

**커버리지 높인 "결과물"은 두 군데에 담긴다:**
1. **실행 기록** → `reports/results/observations.jsonl` (각 API 2xx 여부+`elapsed_ms`) →
   집계되어 **대시보드 커버리지 %**.
2. **노드 단위 증거** → 위 yaml의 `provenance`가 **`docs`(문서 추정) → `VALIDATED`(실제 2xx
   검증)**로 승급. = "이 리소스는 커버됐다".

> **(2026-07-03 보강 — gated 정본 명기)** provenance 정본 = **`VALIDATED | docs`**,
> 여기에 **부가 상태 `gated: <reason>`** (예: `docs` + `gated: license`). 계정
> 게이트(라이선스 · entitlement-403 · org-master · credential)로 **이 계정에선
> 검증 자체가 불가능**한 노드 표시이며, `gated`는 provenance를 바꾸지 않는다
> (노드는 `docs` 유지) — UI/작업 큐에서 "할 일(docs)"과 "할 수 없음(gated)"을
> 분리하는 근거. 표기 규약 정본: `knowledge/formal/FORMAT.md` §Account-gate marker.

**AI가 높이고, 막히면 사람이 저작 — 이 과정도 이미 정의돼 있다:**
- AI(`coverage-service` 에이전트)가 위 yaml을 채우고/고쳐 커버리지를 올린다.
- 막히면(도메인 지식 부족·콘솔 전용 단계·권한/라이선스·제품 버그 등) → **에스컬레이션 사다리
  + 사람 호출 6기준(STOP-6)**(`docs/agent-team.md`)으로 사람에게 넘어온다 → 사람이 같은 yaml +
  `knowledge/validated-facts.md`에 순서·제약을 적는다.

## 3. 지금 실제 상태 — 방향은 맞고, 절반 와 있는데, 두 앱으로 쪼개짐

흐름(4단계)은 이미 controlplane에 있다. 문제는 **같은 일을 두 앱이 따로** 한다는 것:

- **controlplane** = 플랫폼 **척추**. 저작(②) + Plan 스테퍼(①②③) + 디스패치/실행 + 라이브
  추적(oplog) + AI 어시스트 + 그래프 레이어(`graph.json`/`graph.js`) + 정적 export. **많이 만들어짐.**
- **console2** = **실행·리포트만 더 잘** 만든 **별도 앱**(static SPA). 선택→Plan→실행→리포트 4스테이지,
  레이어드-DAG 캔버스(줌/스윔레인), simulate/live, R1 진행·R2 리소스·R3 API·R4 로그. **UX가 폴리시됨.**
- **dashboard** = 커버리지·conformance **공개 정적 페이지**(Pages) + `ops.html` 라이브 ops.
- **poc/scenario-viz** = 위 "한 그래프 + 오버레이" 아이디어를 **검증·구현한 곳**.
  `PLATFORM-PLAN.md`에 **P0~P4 구현 완료**로 기록(composer.graph_view/focus_view/dependents,
  공용 graph.js, 정적 catalog/plan/run/report).

**그래서 쪼개져 보이는 진짜 이유:** 폴리시된 실행 UX(console2)와 저작/카탈로그(controlplane)가
**서로 다른 앱**에 있고, 그래프 렌더러도 둘(console2의 `viz.js` vs controlplane의 `graph.js`),
커버리지는 또 별도(dashboard). **흐름이 없어서가 아니라, 한 곳에 안 모여서.**

## 4. 방향 — 새로 그리지 말고 "수렴" (이미 PLATFORM-PLAN의 결론)

`PLATFORM-PLAN.md`가 이미 명시한 원칙 그대로:
> "**그래프 컴포넌트 1개로 통일** · **4개 화면 = 같은 그래프 + 오버레이만 교체**
> (model=provenance / run-status=oplog / result=observations)."

이를 끝까지 수렴하면:

- **면 1 = 일하는 콘솔 하나** (담당자·운영자): controlplane의 **4단계 스테퍼를 셸로** 삼고,
  그 위에 **console2의 폴리시된 실행/리포트 UX를 흡수** — 한 **그래프 엔진**에서 단계마다
  오버레이만 바꾼다. (**살릴 console2 컨셉:** 레이어드-DAG 줌/스윔레인, simulate→live 2단,
  R1~R4 런별 리포트, 큐에 넣고 실행.)
- **면 2 = 공개 커버리지 대시보드** (다수 열람): **별도 페이지로 유지**(지금 dashboard 그대로).

## 5. 추천 + 남은 선택

**추천: 수렴 대상 = controlplane(척추)**. 저작·디스패치·라이브추적·정적export·4단계 스테퍼가
이미 여기 있으니, **console2의 강점(실행/리포트 UX)을 이 안으로 흡수**하고 그래프 렌더러를
하나로 통일한다. (사장님이 "어떤 식이어도 좋다" 하셨으니, 반대로 console2를 키워 저작/디스패치를
얹는 것도 가능 — 다만 척추를 두 번 짓는 비용이 큼.) **공개 대시보드 별도 유지는 확정.**

## 6. 다음 단계

이 **수렴 방향**에 합의되면, 다음은 "재설계"가 아니라 **합치는 작업의 갭을 구체화**하는 것:
① 두 앱의 중복(실행/리포트·그래프 렌더러) 정리 순서 ② console2 컨셉을 controlplane에 얹는
구체 항목 ③ 4단계가 한 흐름으로 보이게 하는 네비. 잘게 쪼개 진행한다.
