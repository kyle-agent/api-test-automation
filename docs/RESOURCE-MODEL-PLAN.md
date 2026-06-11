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
구조의 신규 레이어; validator 확장). 예 — 제안의 001-001 네트워크 묶음:

```yaml
# knowledge/formal/resources/networking__vpc.yaml
version: 1
resources:
  vpc:                                  # 그래프 노드 id (기존 cross-service와 동일 키)
    code: "001-001-a"                   # 사람용 분류 번호 (보고/정렬)
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
    code: "001-001-b"
    requires: [vpc]                     # AND 목록
    create:
      endpoint: "POST /v1/subnets"
      body: {vpc_id: "{vpc.vpc_id}", cidr: "{opt.cidr}", type: GENERAL}
      options:
        cidr: {type: cidr, required: true, pick: sub-block-of, of: vpc.cidr}
    ...

  vpc-peering:
    code: "001-001-i"
    requires:
      - {ref: vpc, count: 2}            # ← 다중성: VPC 2개
    create: { ... approver body {type: CREATE_APPROVE} 등 검증 템플릿 ... }

  private-nat:
    code: "001-001-g"
    requires:
      - one_of: [direct-connect, transit-gateway]   # ← OR-의존

  privatelink-service:
    code: "001-001-k"
    requires:
      - subnet
      - one_of: [load-balancer, {ref: server, use: ip}]  # LB 또는 VM의 IP
    create:
      options:
        security_group: {type: ref, target: security-group, required: false}
        # ↑ 선택적 참조 옵션: 지정 시 합성기가 security-group 노드를 선행 생성

  vpc-endpoint:
    code: "001-001-h"
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

## 3. 플랫폼 통합 (UI/입력 관리)

- **Planning › 자원 모델**: 노드 목록(코드 번호순)·그래프 뷰(기존
  /planning/dependencies 확장 — 노드 클릭 시 의존 폐포 하이라이트).
- **노드 상세**: requires/옵션 표 + "이 자원만 테스트" 버튼 → 분기·옵션
  선택 폼 → 합성 lifecycle 미리보기(생성될 step 순서) → draft 저장 →
  scoped run 트리거 (기존 service/crud_filter 경로 재사용).
- **신규 자원 입력**: 기존 authoring 편집기(검증 게이트 + git 커밋)로 yaml
  추가 — "신규 추가 시에도 입력 관리" 요구 충족. AI A2 파이프라인은
  "카탈로그+docs → task 정의 초안" 생성기로 전환(시나리오 직접 초안보다
  좁고 검증 가능한 산출물).

## 4. 단계별 실행 계획

| 단계 | 내용 | 산출물 |
|---|---|---|
| **R1 모델+역추출** | 스키마 확정·validator 확장, 기존 128 lifecycle에서 body/capture/poll/teardown **역추출**해 networking부터 task화 (제안의 001-001 묶음이 파일럿), cross-service.yaml requires는 모델로 흡수(당분간 양존) | `knowledge/formal/resources/` + 추출기 |
| **R2 합성기** | composer + 동치 검증(합성 결과 vs 수작업 lifecycle diff), "이 자원만 테스트" UI | `composer.py`, Planning UI |
| **R3 전환** | 노드별 라이브 검증 통과 → 수작업 lifecycle을 합성본으로 교체(점진), 전체 회귀 plan 계산(adopt/lane 일반화) | scenarios.json 축소 |
| **R4 C4 변형** | `vary` 옵션 조합 변형 생성 + 커버리지 집계에 C4 축 추가 | 변형 suite |

검증 원칙은 기존과 동일: 합성기는 결정적, AI는 task 정의 *초안*에만, 모든
교체는 scoped 라이브 run 통과 후.
