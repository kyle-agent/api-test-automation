---
status: draft (오너 논의용 — 2026-06-25, 실제 코드 확인 후 정정)
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
