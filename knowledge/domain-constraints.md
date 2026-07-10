# SCP account domain constraints (confirmed) — networking & DBaaS

> Confirmed, load-bearing account facts the test harness must obey. Recorded in
> git so every run/agent honours them. Machine-readable mirror lives in
> `regression/scenarios/dependencies.json` (`vpc_schedule`, `quota_kinds`); the
> hard limits also live in `core/budgets.py` (`DEFAULT_LIMITS`).

## Quotas (hard account caps)

| Resource   | Max | Error on exceed                          |
|------------|-----|------------------------------------------|
| **VPC**    | **3** | `scp-network.vpc.exceed-max-count`     |
| private-dns| 3   | `scp-network.private-dns.max-count-exceed` |

- **VPC max = 3** (NOT 5 — corrected). `core/budgets.py:DEFAULT_LIMITS["vpc"]=3`
  and `dependencies.json:vpc_schedule.vpc_limit=3`.
- A run must never hold more than 3 live VPCs at once, *including* lingering
  async-deletes. The shared VPC counts as 1 while alive.
- **Docs discrepancy:** the
  [userguide](https://docs.e.samsungsdscloud.com/userguide/networking/vpc/overview/#constraints)
  says the *default* is **5 VPCs/account** — our account enforces 3 at runtime.
  Trust the VALIDATED 3; the discrepancy itself is a docs/conformance finding.

### Further userguide limits (from docs, not yet runtime-confirmed)

Per the VPC/SG userguide constraint tables (mirrored in
`knowledge/formal/services/`): **6 IP ranges per VPC** (1 default + 5 extra),
**3 subnets per VPC** (default — this is why parallel adopters must ADOPT the
shared subnet, not create their own), **5 VPC peerings/account**, **3 Private
NAT/VPC**, **3 Transit Gateways/account** (5 VPC connections each, same account
only), **100 security groups/account**, **100 rules/SG, 1,000 rules/account**.

## CIDR rules (confirmed)

1. **VPCs must not overlap.** Any two VPCs that exist at the same time (the
   shared VPC + any self-created VPC, and self-created VPCs among themselves)
   MUST have non-overlapping CIDR blocks. Each VPC-creating lifecycle is
   assigned a unique `/20` (see allocation table below); the shared VPC owns
   `10.124.0.0/20`.
2. **A subnet's CIDR must be a sub-range of its VPC's CIDR.** Every subnet is
   carved from inside its parent VPC's `/20` (e.g. the shared subnet
   `10.124.0.0/24` is the first `/24` of the shared VPC `10.124.0.0/20`).

### VPC `/20` allocation (one unique block per VPC-creating lifecycle)

| Lifecycle | VPC CIDR | Class |
|-----------|----------|-------|
| *(shared VPC, engine.provision_shared_vpc)* | `10.124.0.0/20` | shared |
| networking-vpc-subnet | `10.123.0.0/20` | vpc-crud |
| container-ske-cluster-nodepool | `10.125.0.0/20` | adopt (fallback) |
| networking-vpc-internet-gateway | `10.126.0.0/20` | vpc-crud |
| vpc-cidr-secondary | `10.127.0.0/20` (primary) + `10.200.0.0/20` (secondary, same VPC) | vpc-crud |
| vpc-privatelink-service | `10.128.0.0/20` | adopt (fallback) |
| vpc-endpoint | `10.129.0.0/20` | adopt (fallback) |
| vpc-peering | `10.130.0.0/20` (VPC-A) + `10.141.0.0/20` (VPC-B) | vpc-crud |
| vpc-transit-gateway-children | `10.131.0.0/20` (+ unique `/20` per child VPC) | vpc-crud |
| vpc-subnet-vip-nat | `10.132.0.0/20` | vpc-crud |
| database-postgresql-cluster | `10.133.0.0/20` | adopt (fallback) |
| heavy-shared-dbaas | `10.134.0.0/20` | adopt (fallback) |
| compute-virtualserver-full | `10.135.0.0/20` | adopt (fallback) |
| database-mysql-cluster | `10.136.0.0/20` | adopt (fallback) |
| networking-direct-connect-routing | `10.137.0.0/20` | adopt (fallback) |
| networking-loadbalancer-members-nat | `10.138.0.0/20` | adopt (fallback) |
| networking-vpn-gateway-tunnel | `10.139.0.0/20` | adopt (fallback) |
| heavy-shared-networking | `10.140.0.0/20` | vpc-crud (+ private-dns) |
| networking-dns-hosted-zone-private | `10.142.0.0/20` | adopt (fallback) (+ private-dns) |

> "adopt (fallback)" lifecycles ADOPT the shared VPC+subnet at runtime (they
> create no VPC); their own CIDR is used only in the degraded self-create
> fallback when no shared VPC is present. They are still given a unique block so
> even the fallback path never overlaps.

Each lifecycle's subnet(s) are the first `/24`(s) of its VPC `/20`
(e.g. `10.13X.0.0/24`). The shared subnet is `10.124.0.0/24`. ADOPT lifecycles
that pin a fixed host IP re-home it into the shared subnet (`10.124.0.x`,
distinct hosts — see `dependencies.json:vpc_schedule.fixed_ip_map`).

## Parallelism (shared-infra + adopt)

- Provision ONE shared VPC + ONE shared subnet once; ADOPT-class lifecycles
  adopt them and create only their own child resource, so they run in PARALLEL
  (pytest-xdist `-n`). They add no VPCs → no VPC-quota pressure.
- **DBaaS per-engine parallelism:** all DB engine lifecycles (mysql, postgresql,
  heavy-shared-dbaas, and the cachestore/epas/mariadb/sqlserver cluster-subops
  lifecycles) are in the parallel pass, so the DB clusters provision concurrently
  in the ONE shared subnet — this is where the wall-clock win comes from.
- VPC-CRUD lifecycles (self-create a VPC/subnet, peer, or need >1 VPC) run
  SERIALLY. The shared VPC is torn down BEFORE the VPC-CRUD pass so those
  lifecycles get the full 3-VPC budget (e.g. vpc-peering needs 2 at once).

## Cross-run rule

Never let two VPC-mutating runs overlap (they compete for the same 3 VPCs).
Trigger one CRUD run at a time; wait for the prior run's regression job to finish.

## retry_on_status 규칙 — 401은 절대 넣지 말 것 (2026-07-10, run-85b2/377e 실측)

401(인증/인가 실패)은 호출 자체가 거부된 것이라 재시도로 수렴하지 않는다.
DBaaS delete/unset류 27개 스텝이 관성적으로 `retry_on_status: [400, 401, …]`
× 20회 × 60s를 갖고 있었고, run-377e에서 `unset-backup` 2건이 각 1,220초를
태우고 그대로 401-fail — 같은 워커 직렬화와 겹쳐 **makespan을 40분 늘렸다**
(94.2분의 43%). 27곳 전부에서 401 제거(2026-07-10). 재시도가 정당한 것은
상태-전이성 코드뿐: 400(EDITING류 반려)·409(충돌)·429·5xx. 만약 미래에
"401 후 수렴" 패턴이 관측되면 그 서비스 한정으로 근거와 함께 재도입하고
이 항목에 기록할 것.

## 400-as-409 클래스 — 상태-전이 반려가 400으로 오는 서비스들 (2026-07-10, run-0099 실측)

일부 서비스는 "지금 상태에서는 그 연산 불가"(의미상 409)를 **400**으로 반환한다.
이 클래스는 settle 사다리/대기로 수렴한다 — 스키마 수리 대상이 아니다:

- **vpc transit-gateway DELETE**: vpc-connection DELETE 202 직후 TGW DELETE가
  400 "not deletable state" (detach 비동기). 수리: connection 404 gone-폴 후
  삭제 + `retry_on_status: [400, 409]` (generated__light-batch2 gen-private-nat).
- **cloud-function PLE sub-ops**: scf PLE **생성이 함수를 DEPLOYING으로 되돌리고**
  (재배포), 그동안 request/approve/connect가 전부 400
  `function-not-editable-error`. PLE 자신도 CREATING 동안 cancel/delete가 400
  `privatelink-endpoint-invalid-state-error`. 수리: 생성 직후 함수 state settle
  폴 + sub-op 400 사다리 (generated__wave5-appsvc gen-wave5-apigw-privatelink).
  delete-function의 "PL service disabled AND PLE 부재" 전제조건은 이 체인이
  풀려야 충족된다.
- **apigw privatelink-endpoint PUT**: 400 `modify-restricted-state`가 허용 상태
  enum을 명시해 준다 — Requesting/Canceled/Rejected/Active/Disconnected.
  CREATING settle 폴 후 set/approve 진행.

## IAM 트러스트 정책 — Resource가 필수다 (2026-07-10, 400 사다리 3단 해독)

createrole/setroletrustpolicy의 `assume_role_policy_document.Statement[]`는
문서와 달리 **Principal과 Resource 둘 다 필수**: ① v2(root SRN, dict Principal)
→ 400 ValidationError "valid string/valid list" ② v3(Principal.scp 리스트형,
Resource 제거) → 400 "Value error, 'Resource' is required" ③ v4 =
Principal `{"scp": ["srn:e::<acct>:::scp-iam:root"]}` + `Resource: ["*"]`.
에러가 단계마다 다음 필수 필드를 밝혀준 케이스 — 500이 아니라 400이 나오기
시작하면 정형에 근접한 것.

## run-end 리퍼는 게이트를 스스로 켠다 (2026-07-10, run-0099 실측 버그)

콘솔 서버 프로세스는 SCP_ALLOW_MUTATIONS/DESTRUCTIVE env 없이 떠 있을 수 있어
`cleanup/run_scoped.py`가 서버 settings로 클라이언트를 만들면 run-end DELETE가
전부 "blocked"로 무력화된다 (TGW→VPC 잔존의 뿌리). 리퍼는 자기 런의 원장에
기록된 자원만 지우므로(Hard Rule 3) `dataclasses.replace(settings,
allow_mutations=True, allow_destructive=True)`로 게이트를 강제한다 — 회귀 테스트
`tests/offline/test_run_scoped_reap.py::test_reap_forces_gates_on_*`.
