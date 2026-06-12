# 자원 모델 기반 시나리오 합성 (Resource Task Model) — 설계

> 제안 (owner, 2026-06-11): 자원 타입별로 "최소 의존조건 + 생성 옵션"을
> 구조화해 두면, 의존 그래프가 그려지고 시나리오가 **조합으로 생성**된다 —
> 단독 테스트는 그래프를 따라 선행 자원을 만들고 대상을 테스트한 뒤 역순
> 삭제, 전체 회귀는 task 조합으로 전체 커버리지를 구성. 신규 서비스는 이
> 모델을 입력하는 것으로 추가된다.

## 0. 검토 결론

**채택.** 이 레포의 기존 방향(ROADMAP "formal 데이터 → 엔진 데이터 생성")의
완성형이며, 다음이 핵심 근거다:

1. **이미 절반은 있다** — `cross-service.yaml`의 `requires:` 그래프(41개
   노드), 엔진의 adopt/공유 인프라 메커니즘, 역순 teardown, capture/poll.
   없는 것은 ① 생성 **옵션** 모델 ② **OR-의존**(`direct connect OR transit
   gateway`) ③ **다중성**(vpc peering = vpc×2) ④ 합성기.
2. **엔진 무수정** — 합성기는 자원 모델 → 기존 lifecycle JSON으로 컴파일하는
   *컴파일러*다. 검증된 실행 경로(엔진·sweep·결과 스키마)는 그대로 쓴다.
3. **추출 가능한 검증 데이터가 있다** — 손으로 쓴 128개 lifecycle에 라이브
   검증된 body/capture/poll이 들어 있다. 자원 모델의 초기값은 이걸
   *역추출*해서 채우므로(추측 아님) 부트스트랩 비용이 낮다.
4. **C4(옵션 변형 커버리지)로 가는 유일한 현실적 경로** — 옵션이 데이터로
   모델링되어야 옵션 조합 변형 시나리오를 생성할 수 있다
   (docs/COVERAGE-CRITERIA.md의 종착 목표).

리스크와 완화: 손으로 쓴 lifecycle에는 타입 모델로 일반화 안 되는 인스턴스
지식(예: peering 승인 body `{type: CREATE_APPROVE}`)이 있다 → task 정의가
**검증된 body 템플릿을 그대로 보유**하게 하고(역추출), 합성 결과는 노드별로
기존 수작업 lifecycle과 **동치 비교 + 라이브 검증을 통과한 뒤에만** 교체한다.

## 1. 자원 task 스키마

위치: `knowledge/formal/resources/<category>__<service>.yaml` (formal 3층
구조의 신규 레이어; validator 확장). `code`는 읽을 수 있는
`<cat>-<group>-<resource>` 체계(카테고리 약어 nw/cp/st/db/sec/mg/ct/ai/da,
그룹 키 = 앞 두 세그먼트). 예 — nw-vpc 네트워크 묶음:

```yaml
# knowledge/formal/resources/networking__vpc.yaml
version: 1
resources:
  vpc:                                  # 그래프 노드 id (기존 cross-service와 동일 키)
    code: "nw-vpc-vpc"                  # 사람용 분류 코드 (보고/정렬, <cat>-<group>-<resource>)
    service: networking/vpc
    requires: []                        # 의존 없음
    create:
      endpoint: "POST /v1/vpcs"
      body: {name: "regrvpc{ualpha}", cidr: "{opt.cidr}"}   # 검증된 템플릿
      options:                          # ← 생성 시 선택 가능한 것
        cidr: {type: cidr, required: true, pick: unique-block,
               note: "live VPC들과 비겹침 (domain-constraints.md 블록 할당)"}
    capture: {vpc_id: "$.vpc.id"}
    ready:   {field: "$.vpc.state", until: ACTIVE, timeout: 600}
    delete:  {endpoint: "DELETE /v1/vpcs/{vpc_id}", destructive: true}
    quota: vpc                          # core/budgets 연동
    provenance: VALIDATED               # 역추출 출처 lifecycle/run 기록

  subnet:
    code: "nw-vpc-subnet"
    requires: [vpc]                     # AND 목록
    create:
      endpoint: "POST /v1/subnets"
      body: {vpc_id: "{vpc.vpc_id}", cidr: "{opt.cidr}", type: GENERAL}
      options:
        cidr: {type: cidr, required: true, pick: sub-block-of, of: vpc.cidr}
    ...

  vpc-peering:
    code: "nw-vpc-peering"
    requires:
      - {ref: vpc, count: 2}            # ← 다중성: VPC 2개
    create: { ... approver body {type: CREATE_APPROVE} 등 검증 템플릿 ... }

  private-nat:
    code: "nw-vpc-private-nat"
    requires:
      - one_of: [direct-connect, transit-gateway]   # ← OR-의존

  privatelink-service:
    code: "nw-vpc-privatelink-svc"
    requires:
      - subnet
      - one_of: [load-balancer, {ref: server, use: ip}]  # LB 또는 VM의 IP
    create:
      options:
        security_group: {type: ref, target: security-group, required: false}
        # ↑ 선택적 참조 옵션: 지정 시 합성기가 security-group 노드를 선행 생성

  vpc-endpoint:
    code: "nw-vpc-endpoint"
    requires: [subnet]
    create:
      options:
        target: {type: enum, values: [dns, objectstorage, filestorage, scr],
                 required: true, vary: true}   # vary: C4 변형 생성 대상
```

표현력 요약 — 제안의 모든 케이스를 커버한다:

| 제안 표현 | 스키마 |
|---|---|
| "no dependency / cidr 입력" | `requires: []` + `options.cidr` |
| "subnet: vpc의 하위 cidr" | `pick: sub-block-of, of: vpc.cidr` |
| "direct connect or transit gateway" | `one_of: [...]` |
| "2개의 vpc 필요" | `{ref: vpc, count: 2}` |
| "lb 연결 또는 vm의 ip 연결" | `one_of:` + `use:` (참조의 어떤 산출물을 쓰는지) |
| "security group 선택(선택사항)" | `options.<k>: {type: ref, required: false}` |
| "dns/objectstorage/... 대상 등록" | `options.<k>: {type: enum, vary: true}` |
| "기존 k8s 선택 **또는** 신규 k8s 생성" (aimlops, owner 2026-06-11) | `requires: [{ref: ske-cluster, mode: existing_or_create}]` — 분기 ①: ske-cluster 노드를 선행 생성 후 참조, 분기 ②: 대상의 신규생성형 create body 사용(외부 선행 없음, SKE 폐포의 비용/quota는 계상). 두 분기 모두 C4 변형 대상 |
| "container registry **인증키** 필요" (cloud-ml, owner 2026-06-11) | `requires: [{credential: scr-auth-key}]` — API로 생성 불가한 콘솔 발급 자격은 create step이 아니라 **사전조건 체크**: 합성기는 환경에 해당 credential이 표시돼 있지 않으면 env-skip 처리 (archivestorage 패턴) |

## 2. 합성기 (composer) — 자원 모델 → lifecycle JSON

`regression/scenarios/composer.py`. 입력: 대상 노드 id + 옵션/분기 선택
(미지정 시 기본 분기 = 비용 최소·light 우선). 출력: **기존 엔진이 그대로
실행하는 lifecycle JSON** (id: `gen-<node>[-<variant>]`).

알고리즘 (privatelink-service 단독 테스트 예):

```
1. 의존 폐포 계산: privatelink-service → subnet → vpc,
   one_of[load-balancer|server.ip] → 기본 분기 load-balancer → (lb 체인…)
   options.security_group 선택 시 → security-group 추가
2. 위상 정렬 + capture 배선:
   create-vpc → wait → create-subnet(vpc_id 주입) → wait
   → create-security-group → create-lb-chain(healthcheck→lb …)
   → create-privatelink-service(subnet_id, lb_id, sg_id 주입) → ready 폴링
3. 대상 노드의 read/update 등 검증 step (task 정의의 verify 목록)
4. 역순 teardown (privatelink → lb 체인 → sg → subnet → vpc)
5. adopt 최적화: vpc/subnet은 shared-VPC adopt 표식으로 치환 가능
   (count>1·자기 VPC 변형이 필요한 노드만 self-create) — 기존
   vpc_schedule 분류가 그래프에서 '계산'된다
6. 산출물은 scenarios validator + 할당량 시뮬레이션 통과 후 draft 저장
```

전체 회귀 = **합집합 그래프 1회 계산**: 모든 대상 노드의 의존 폐포를 합치고
공통 접두(vpc/subnet/공유 인프라)는 1회 생성으로 dedup → 오늘 손으로 튜닝한
adopt/직렬 lane 전략이 일반화된 계산 결과가 된다. C4는 `vary: true` 옵션의
값 조합으로 변형 lifecycle을 생성하는 동일 합성기의 모드다.

## 2.5 계층 합성 — 단위 → 서비스 묶음 → 상품 (owner 구체화 2026-06-11)

합성은 세 층위로 동작하며, 산출물은 모두 이름 있는 관리 대상이다:

| 층위 | 입력 | 산출물 (관리 엔티티) |
|---|---|---|
| **단위** | 노드 1개 + 분기/옵션 선택 | `gen-<node>` lifecycle |
| **서비스 묶음** | 그룹(예: nw-vpc 하위 전체) 또는 노드 멀티선택 | `bundle-<group>` lifecycle — 겹치는 선행조건 dedup |
| **상품** | 그룹/노드 혼합 선택 (카테고리 횡단, 예: "VM 상품" = vpc+subnet+sg+keypair+vm+public-ip) | `product-<name>` lifecycle |

**묶음 합성 규칙 (겹침 최소화 + 순서 조정):**

1. 대상 집합의 의존 폐포를 합집합 → 공유 선행자원은 **1회만 생성**
   (vpc 하위 12개 대상이어도 vpc 1·subnet 1).
2. 대상이면서 선행자원인 노드(vpc 자체)는 별도 생성 없이 그 인스턴스에
   verify step만 접목.
3. **구간 스케줄링**: 공유 자원은 첫 의존 대상 직전에 생성, 마지막 의존
   대상 검증 직후에 삭제 — teardown은 전체 역순 1회. (현재 shared-VPC
   adopt + 직렬 lane 전략의 일반화.)
4. `one_of` 분기는 묶음 안에 이미 존재하는 노드를 우선 선택
   (privatelink는 묶음에 lb가 있으면 lb 분기), `count: 2`는 공유분을
   1로 치고 부족분만 추가 생성 (peering = 공유 vpc + 1).
5. 할당량 시뮬레이션으로 peak 동시 자원을 계산해 한도 초과 시 묶음을
   자동 분할(직렬 lane) — 기존 budgets/quota_kinds 재사용.
6. 전체 회귀 suite = 상품/묶음 합성본들의 집합으로 전체 노드 커버리지를
   구성 (커버리지 집계가 "노드 검증 여부"로 직접 매핑됨).

## 3. 플랫폼 통합 (UI/입력 관리) — 폼 기반

사용자 기대 흐름: **서비스별 항목이 보이고 → 항목을 치고 들어가면 전제조건·
옵션값을 입력하는 화면 → 간단히 입력해두면 조합은 플랫폼이 알아서.**

```
Planning › 자원 모델
├─ 그룹 목록: 001 network, 002 compute … (그룹별 노드 수·검증상태 배지)
│   └─ 그룹 화면: 노드 표 (code · id · requires 요약 · 옵션 수 · 검증상태)
│       ├─ [노드 클릭] 상세 입력 화면 (raw YAML이 아닌 폼):
│       │     · 전제조건: 행 추가식 — ref 선택 / one_of 멀티선택 / count
│       │     · 옵션: 이름 + 타입(cidr|enum|ref|string) + required + vary + 기본값
│       │     · 검증된 body 템플릿 (역추출값 표시, 수정 가능)
│       │     · 저장 = 기존 authoring 파이프라인 (validator 게이트 + git 커밋)
│       │     · [이 자원만 테스트] → 분기·옵션 폼 → 합성 미리보기(step 순서표)
│       │       → draft 저장 → scoped run
│       └─ [그룹 전체 테스트] → 묶음 합성 미리보기(생성/검증/삭제 순서 +
│            dedup 표시 + peak 할당량) → run
└─ 상품 시나리오: 노드/그룹 멀티선택 → 이름 부여 저장 → suite처럼 스케줄/실행
```

폼은 내부적으로 동일한 yaml을 생성/수정한다(authoring 파이프라인 공용) —
git이 계속 이력을 갖고, raw 편집기는 고급 사용자용으로 병존.

## 4. 단계별 실행 계획

| 단계 | 내용 | 산출물 |
|---|---|---|
| **R1 모델+역추출** | 스키마 확정·validator 확장, 기존 128 lifecycle에서 body/capture/poll/teardown **역추출**해 networking부터 task화 (nw-vpc 묶음이 파일럿), cross-service.yaml requires는 모델로 흡수(당분간 양존) | `knowledge/formal/resources/` + 추출기 |
| **R2 합성기** | composer + 동치 검증(합성 결과 vs 수작업 lifecycle diff), "이 자원만 테스트" UI | `composer.py`, Planning UI |
| **R3 전환** | 노드별 라이브 검증 통과 → 수작업 lifecycle을 합성본으로 교체(점진), 전체 회귀 plan 계산(adopt/lane 일반화) | scenarios.json 축소 |
| **R4 C4 변형** | `vary` 옵션 조합 변형 생성 + 커버리지 집계에 C4 축 추가 | 변형 suite |

검증 원칙은 기존과 동일: 합성기는 결정적, AI는 task 정의 *초안*에만, 모든
교체는 scoped 라이브 run 통과 후.

**R2 라이브 증명 (2026-06-12)**: 합성 lifecycle `gen-pilot-net-basics`
(vpc/subnet/port/igw/public-ip, 20스텝)가 scoped 라이브 run에서 20/20 통과 —
모델→합성기→엔진 경로 증명. 1차 run에서 delete-vpc 409(자식 비동기 삭제
경합)로 실패한 교훈은 합성기에 반영: 의존자가 있는 노드의 삭제 스텝에
수작업 lifecycle과 동일한 충돌-재시도 시맨틱(409/404 허용 + 409 재시도
40×30s)을 자동 부여한다. 파일럿은 합성 경로의 카나리아로 enabled 유지.
다음: 카테고리 fan-out(R1 확장) → R3 점진 교체.

## 5. 병렬 실행 계획 — 에이전트 맵 + 인터페이스 계약 (2026-06-11)

4개 에이전트가 동시 작업한다. 충돌 방지는 **파일 소유권 분리**, 정합성은
**이 절의 계약**으로 보장한다. 먼저 끝난 에이전트의 확정 사항은
오케스트레이터가 main 머지 + 진행 중 에이전트에 전달(SendMessage)로 동기화.
모든 확정 산출물은 즉시 main 커밋/푸시.

| 에이전트 | 산출물 | 소유 파일 |
|---|---|---|
| **R1 모델·역추출** (진행 중) | 스키마+validator, 추출기, networking 파일럿, read-only 목록 화면 | `knowledge/formal/resources/`, validator, `/planning/resources` 페이지 |
| **R2a 합성기** | `compose()`/`plan()` + 단위·묶음 합성, 자체 fixture 테스트 | `regression/scenarios/composer.py`, `tests/offline/test_composer.py` |
| **R2b 폼 UI·합성 실행** | 노드 폼 편집 + 합성 미리보기 + draft 저장/run 연계 | `controlplane/resource_routes.py`(APIRouter) + `templates/resource_*.html` |
| **R2c AI task-초안** | 미모델 서비스의 task 정의 초안 생성기 (경로 2) | `controlplane/ai_pipelines.py` 확장 + `ai_routes.py` + `ai_taskdraft.html` |

### 계약 (모든 에이전트 공통 준수)

**C1. 자원 모델 파일 레이아웃** — `knowledge/formal/resources/<category>__<service>.yaml`
(§1 스키마), 그룹 정의는 `knowledge/formal/resources/_groups.yaml`
(`groups: {"nw-vpc": {label, category}}`). 로더는 디렉토리 전체 merge.

**C2. 합성기 API** — `regression/scenarios/composer.py`:
```python
load_model(dir="knowledge/formal/resources") -> dict        # {node_id: task}
plan(targets: list[str], choices: dict|None = None,
     options: dict|None = None, model: dict|None = None) -> Plan
  # Plan: {order: [...], dedup: {...}, peak_quota: {...}, branches: {...}}
compose(targets, choices=None, options=None, model=None,
        lifecycle_id: str|None = None) -> dict               # 엔진 lifecycle JSON
```
choices = one_of 분기 선택 {node_id: branch}, options = {node_id: {opt: value}}.
compose 산출물은 scenarios validator를 통과해야 한다 (id 기본값:
`gen-<node>` / `bundle-<id>`). model 인자 주입으로 fixture 테스트 가능.

**C3. UI 라우트** — R1: GET `/planning/resources` (그룹/노드 목록, app 직결).
R2b: APIRouter prefix `/planning/resources` 하위 — GET `/{node_id}` 폼,
POST `/{node_id}/save` (authoring 파이프라인 경유), GET/POST `/compose`
(대상 멀티선택 → plan 미리보기 → compose draft 저장 → run 연계는 기존
`/runs/trigger` 재사용). R2b는 app.py를 건드리지 않는다 — 오케스트레이터가
머지 시 include_router (ai_routes 선례).

**C4. draft 위치** — 합성 lifecycle draft: `drafts/lifecycle-gen-*.json`,
AI task-초안: `drafts/taskdef-<category>__<service>-<ts>.yaml` (사람 검토 후
`knowledge/formal/resources/`로 이동). 자동 enable 금지.

**C5. provenance 규칙** — 역추출(라이브 검증 lifecycle 출처) = `VALIDATED`,
사람 입력/AI 초안 = `docs`. scoped run 통과 시 VALIDATED 승격 (R3).

**C6. fixture** — R2a/R2b/R2c는 R1 산출을 기다리지 않고 §1 스키마 그대로의
자체 fixture(yaml)를 테스트에 내장한다. R1 머지 후 실데이터로 통합 검증은
오케스트레이터 몫.
