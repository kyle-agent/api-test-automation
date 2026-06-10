# Parallel execution plan — staged foundations + per-VPC lanes (DRAFT)

> Status: **draft, awaiting review.** 테스트 시간을 줄이기 위한 단계형 병렬
> 실행 설계. 승인되면 engine/shared_infra/dependencies.json을 이 모델로
> 확장한다. 현재 구현(공유 VPC 1개 + adopt 병렬 / vpc-crud 직렬)의 일반화.

## Idea (요청 요지)

1. 시작하자마자 **선행조건이 없는 것 + 나중에 다른 서비스가 쓰는 것**을 전부
   병렬로 미리 만든다 (VPC 여러 개, filestorage, bucket, keypair…).
2. 그 다음 VPC 하위 자원(subnet 등)을 VPC별로 병렬로 만든다.
3. 그 다음 **VPC마다 전담 용도(lane)** 를 줘서 lane끼리 병렬로 테스트한다
   (DB lane / networking lane / LB+VM lane …).

## Hard constraints the plan must respect (validated knowledge)

| Constraint | Value | Effect on the plan |
|---|---|---|
| VPC account cap | **5 VALIDATED** (run 27306490231의 라이브 에러 'The number(5) of VPCs ... exceeded'; 종전 '3 VALIDATED'는 오류) | lanes = cap − 1 = 4 (1 slot은 VPC-CRUD 테스트용 예약) |
| Subnets per VPC | 3 (docs) | lane당 subnet ≤ 3; DB 병렬은 한 subnet 안에서 이미 동작 확인됨 |
| VPC CIDR 비중첩 | VALIDATED | 기존 /20 할당표 재사용 (`knowledge/domain-constraints.md`) |
| security-group | 계정/리전 스코프 — **VPC 불필요** (VALIDATED) | Stage 0으로 이동 (요청에서는 Stage 1이었지만 더 일찍 가능) |
| 동시 mutating run 금지 | VALIDATED | 이 전체 계획이 ONE run 안에서 돌아야 함 (lane은 run 내부 병렬) |

## Stages

### Stage 0 — Foundations (모두 병렬; 선행조건 없음 / 재사용 자원)

```
vpc × (cap-1)            ← lane 전용; CIDR 할당표의 고유 /20씩
security-group × 2-3     ← 계정 스코프 (VPC 불필요)
keypair                  ← zero-cost, 즉시
filestorage volume × 2   ← SKE용 1 + VM-mount 콤보용 1
objectstorage bucket × 2 ← (현재 standalone lifecycle 확인 필요)
scr registry · kms key · secret · certificate · queue · resource-group
block-volume (+snapshot) ← virtualserver standalone
```

이 스테이지의 모든 생성은 registry에 태깅되고, 마지막 teardown 스테이지가
역순으로 회수한다. 각 항목 자체가 해당 서비스의 create 커버리지 측정이기도 함.

### Stage 1 — VPC children (VPC별 병렬)

각 lane VPC 아래: subnet ≥1 (CIDR은 부모 /20의 /24들), 필요한 lane만
internet-gateway / port. lookups(image, server-type, k8s-version)도 여기서
한 번만 수행해 lane에 전달.

### Stage 2 — Lanes (lane 간 병렬, lane 내부도 가능한 만큼 병렬)

| Lane (VPC) | What runs | Notes |
|---|---|---|
| **VPC-A · database** | mysql · postgresql · mariadb · epas · sqlserver · cachestore 클러스터 병렬 생성→테스트→삭제 | 한 subnet 안 DBaaS 병렬은 현 shared-subnet 설계로 이미 검증됨 |
| **VPC-B · networking** | LB(+listener/member) · VPN gw/tunnel · direct-connect · NAT/VIP · endpoint · privatelink · private-dns | fixed-IP는 lane subnet 안에서 비충돌 할당 (fixed_ip_map 확장) |
| **VPC-C · compute** | VM full (keypair/SG/볼륨은 Stage 0 것 adopt) → LB+VM attach 콤보 · SKE cluster+nodepool (Stage 0 filestorage adopt) | heavy 직렬 2개가 아니라 VM과 SKE를 lane 내 병렬로 |
| **(예약 슬롯) · VPC-CRUD 직렬** | networking-vpc-subnet · igw · cidr-secondary · **peering(VPC 2개 필요)** · transit-gateway | peering은 예약 슬롯+lane 하나가 비워진 뒤 또는 cap=5 확인 후 |

> **느린 프로비저너 격리 (VALIDATED 2026-06-10):** private-dns는 생성/삭제가
> **수 시간** 걸릴 수 있다 (run #39이 여기서 정체). private-dns가 포함된
> 플로우는 **자기 lane/VPC에 격리**하고, 생성은 run 시작 직후·삭제는 마지막에
> 배치하며, 넉넉한 poll timeout을 준다. 다른 lifecycle이 그 뒤에 직렬로 묶이지
> 않게 한다 (`flows.yaml: slow-provisioners-isolated`).

### Stage 3 — Teardown (역순)

lane 내부 자원 → Stage 1 children → Stage 0 foundations. registry-driven,
409/500 retry, 자식 404 확인 후 부모 삭제 (기존 flow_rules 그대로).

## Why this is faster

현재: heavy lifecycle들이 사실상 직렬 (VM ~17m + SKE ~27m + DB들 + …) → 합계.
계획: wall-clock ≈ Stage0(분) + Stage1(분) + **max(lane 시간)** (~30m) +
teardown. heavy들이 lane에 분산-중첩되므로 합계가 아닌 최대값으로 떨어짐.

## What already exists vs what to build

**Exists:** adopt 메커니즘(`{"adopt":"vpc"}` + env id 주입) · xdist 병렬 ·
lane 정의(`dependencies.json:vpc_schedule.lanes`) · -k 파티션 생성기
(`shared_infra --print-filters`) · CIDR 할당표 · registry/budget.

**To build (승인 후):**
1. `shared_infra`를 **다중 풀**로 일반화: lane별 VPC+subnet 프로비저닝,
   `SCP_SHARED_VPC_ID__DB` 식 네임드 env, `adopt: "vpc#db"` 문법.
2. Stage 0 **foundations provisioner**: 선행조건 없는 재사용 자원 일괄 생성
   + env로 id 전달 (keypair/SG/filestorage/bucket…), lifecycle들이 adopt.
3. 워크플로우: lane을 pytest-xdist 그룹(또는 matrix job)으로 매핑, Stage
   경계는 단계별 스텝으로.
4. `Budget.sync()`를 Stage 0 시작 전에 실제 list 호출로 동기화 (durable_fix
   항목 1 — 이미 dependencies.json에 기록된 과제).
5. **quota API로 VPC cap 실값 확인** → lanes 수 결정 (3→2 lanes, 5→4 lanes).

## Open questions (검토 시 답해주세요)

1. VPC cap이 3으로 확인되면 lane 2개(db / networking+compute 통합)로
   시작할지, networking과 compute를 시간대로 나눌지.
2. object storage bucket lifecycle이 현재 없으면 Stage 0에 신규 작성 필요 —
   우선순위에 포함할지.
3. lane 실패 격리: lane 하나가 깨져도 다른 lane은 계속 (group/optional을
   lane 단위로 확장) — 동의하는지.
