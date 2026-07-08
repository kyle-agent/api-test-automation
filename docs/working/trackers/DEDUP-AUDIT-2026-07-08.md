---
status: active (2026-07-08 오프라인 정적 감사 — owner 결정 대기)
for: owner
method: deterministic offline (console2 selection semantics + lifecycle surface diff)
---

# DEDUP Audit — 단위 서비스 선택 시 lifecycle 최소중복 전수 확인 (2026-07-08)

**Owner 요청**: "단위 서비스를 선택했을 때 그 서비스에서 실행될 lifecycle들이 최소
중복인지 전 서비스에 대해 확인." **선례**: `gen-wave4-asg` — endpoint 표면이
`heavy-asg-full-coverage`에 완전 포함(유일 커버 `showlaunchconfiguration` 1건만 이관
후 은퇴, commit `a4697b11`). 그 모양의 잔존 케이스를 전 서비스에서 찾았다.

## Executive summary (3줄)

1. **59개 서비스 중 34개 감사**(모델 기준 서비스 선택 → 2개 이상 lifecycle이 실행되는 서비스; 나머지 25개는 lifecycle ≤1로 중복 불가), 그중 **28개 서비스에서 58개 중복 쌍** 발견 — DUPLICATE 9 / SUBSUMED 28 / SUBSUMED-1(유일 1건) 16 / HIGH-OVERLAP 5.
2. 권고 분류: **즉시 은퇴 가능 19개** lifecycle(유일 endpoint 0, DUPLICATE 9쌍은 payload까지 바이트 동일), **이관-후-은퇴 8개**(각 1 endpoint만 이관 — ASG 선례와 같은 모양), **통합 검토 7건**(billable 이중 프로비저닝 포함: postgresql 클러스터 2기, LB 스택 2~3기, SCR registry 2기(quota=1 충돌), DBaaS shared-vs-subops).
3. Intra-lifecycle 반복(동일 endpoint ≥4회, poll 제외)은 **0건**; 깨끗한 서비스 6개(맨 아래 목록).

**감사 기준(재현 방법)**: 서비스별 실행 집합 = `console2_server._resolve_lifecycle_ids({'services':[svc]})`
(enabled ∧ role=verify 만 — 은퇴된 gen-wave4-asg는 이미 제외됨). lifecycle 표면 =
step들의 `(METHOD, norm_path)` 집합(`tools/derive_verified.norm_path`: query 제거,
`{...}` 세그먼트 → `*`). **판정은 공용 인프라 제외 표면(ex)** — `/v1/vpcs`,
`/v1/subnets`, `/v1/keypairs`, `/v1/security-groups` 제외(단, 그 prefix를 **자기
API로 소유한** 서비스(networking/vpc·security-group, compute/virtualserver)에선
제외하지 않음). full/ex 두 숫자 모두 표기. **한계**: norm은 query·header·body를
지우므로 FIFO(.fifo)·X-ResourceType 헤더·payload 변형은 표면상 같아 보인다 —
그런 케이스는 아래에서 개별 검증해 "keep-distinct"로 분리했다.

**은퇴 절차(기존 정책 재사용)**: `tools/retirement.py`의 2단계 — ① `enabled:false`
+ `_replaced_by` 주석(한 윈도우 커버리지/fail_new 무영향 확인) ② 다음 정리 커밋에서
물리 삭제. 유지 측이 LIVE-GREEN인지 확인 후 실행(`data/baselines/green_lifecycles.json`).
node가 가리키는 lifecycle(`nodes` 표기)을 은퇴할 땐 node 재지정 동반.

표기: `[H/l]` heavy/light · `ex a/b∩c` = 인프라 제외 표면 |A|/|B|, 교집합 c ·
`mutEx` = A측 비-GET step 수(중복 실행 비용 감).

---

## A. 즉시 은퇴 (유일 endpoint 0 — 이관 불필요)

### financial-management/costexplorer — 2 lifecycles
- `gen-cost-reads` 3 · `gen-wave3-cost` 3 (둘 다 read-only)
- **DUPLICATE** [ll] `gen-wave3-cost` == `gen-cost-reads` — ex 3/3∩3 J=1.0 (full 동일). step·payload 동일.
- 권고: **retire `gen-wave3-cost` → `gen-cost-reads`** (현행 composer targets 산출물 유지).

### financial-management/pricing — 2 lifecycles
- `gen-pricing-reads` 3 · `gen-wave3-pricing` 3 (read-only)
- **DUPLICATE** [ll] `gen-wave3-pricing` == `gen-pricing-reads` — ex 3/3∩3 J=1.0.
- 권고: **retire `gen-wave3-pricing` → `gen-pricing-reads`**.

### platform/product — 2 lifecycles
- `product-catalog-readonly` 4 · `gen-wave3-product` 4 (read-only)
- **DUPLICATE** [ll] `gen-wave3-product` == `product-catalog-readonly` — ex 4/4∩4 J=1.0.
- 권고: **retire `gen-wave3-product` → `product-catalog-readonly`** (분석 노트가 붙은 정본 유지; `knowledge/formal/resources/platform__product.yaml` 참조 갱신 확인).

### management/quota — 3 lifecycles
- `quota-read-coverage` 4 · `gen-wave3-quota` 4 · `gen-quota-request` 2 (전부 read-only — quota는 mutating endpoint가 없는 서비스)
- **DUPLICATE** [ll] `gen-wave3-quota` == `quota-read-coverage` — ex 4/4∩4 J=1.0.
- **SUBSUMED** [ll] `gen-quota-request`(2) ⊂ 양쪽(4) — 유일 0.
- 권고: **retire `gen-wave3-quota`·`gen-quota-request` → `quota-read-coverage`** (읽기 정본 1개로 수렴).

### management/network-logging — 2 lifecycles
- `gen-wave4-nlog` 4 (mut 2) · `gen-network-logging-storage` 4 (mut 2)
- **DUPLICATE** [ll] `gen-network-logging-storage` == `gen-wave4-nlog` — ex 4/4∩4 J=1.0, payload까지 동일. 선택 시 storage POST/DELETE **왕복 2회**.
- 권고: **retire `gen-network-logging-storage` → `gen-wave4-nlog`** (ensure-logsink 운영 노트가 있는 쪽 유지).

### management/servicewatch — 12 lifecycles (waveA1 4개가 wave5 4개와 바이트 동일)
- 관련 roster: `gen-dashboard` 4 == `gen-wave5-swatch-dashboard` 4 · `gen-sw-custom-log-collect` 6 == `gen-wave5-swatch-custom-log` 6 · `gen-sw-custom-metric-meta` 3 == `gen-wave5-swatch-custom-metric` 3 · `gen-sw-metric-catalog` 3 == `gen-wave5-swatch-metric` 3
- **DUPLICATE ×4** [ll] — 전 쌍 ex J=1.0, steps/payload 동일(spot-check 확인). 선택 시 dashboard/log/metric 쓰기 **12 mutation step이 그대로 2회** 실행.
- 권고: **retire waveA1 4개(`gen-dashboard`,`gen-sw-custom-log-collect`,`gen-sw-custom-metric-meta`,`gen-sw-metric-catalog`) → wave5 짝** (owner 2026-06-13 wave5가 최신).

### management/support — 3 lifecycles (read-only)
- `gen-wave3-support` 6 · `gen-support-inquiry` 3 · `gen-support-service-request` 3
- **SUBSUMED ×2** [ll]: inquiry(3) ⊂ wave3(6), service-request(3) ⊂ wave3(6) — 합집합이 wave3와 **정확히 일치**(known-id GET 포함, 검증됨).
- 권고: **retire `gen-support-inquiry`·`gen-support-service-request` → `gen-wave3-support`** (3→1, 손실 0).

### application-service/queueservice — 3 lifecycles
- `application-queueservice-queue` 11 (mut 8; nodes: queue, queue-fifo) · `gen-wave3-qfifo` 5 (mut 4) · `gen-wave2-queue` 3 (mut 2)
- **SUBSUMED** [ll] `gen-wave2-queue`(3) ⊂ 두 쪽 모두 — 유일 0. Standard 큐 create/attributes/delete만 — 선택 시 **큐 생성 왕복 1회 순증**.
- **SUBSUMED** [ll] `gen-wave3-qfifo`(5) ⊂ `application-queueservice-queue`(11) — 표면상 포함이나 **payload가 FIFO**(`.fifo` 이름, dedup/scope는 FIFO-only per userguide).
- 권고: **retire `gen-wave2-queue`**; `gen-wave3-qfifo`는 **keep-distinct**(FIFO 의미론 — norm 표면으론 안 보이는 변형).

### networking/security-group — 3 lifecycles
- `networking-security-group` 7 (SG+rule CRUD, 필터 list에 `security_group_id={sg_id}` 쿼리 이미 포함 — 확인함) · `gen-wave4-sgrule` 6 · `networking-security-group-update` 5
- **SUBSUMED** [ll] `gen-wave4-sgrule`(6) ⊂ `networking-security-group`(7) — ex ∩6 J=0.857, 유일 0. **wave4 쿼리-변형 가치까지 이미 정본에 있음** → ASG 선례와 동일한 wave4 모양.
- 권고: **retire `gen-wave4-sgrule` → `networking-security-group`** (SG+rule 생성 왕복 1회 절약). (update는 §B 참조.)

### data-analytics/quick-query — 4 lifecycles
- **SUBSUMED** [ll] `gen-quick-query-list`(1, read-only) ⊂ `quick-query-read-coverage`(3).
- 권고: **retire `gen-quick-query-list`** (손실 0, 우선순위 낮음).

### management/resourcemanager — 5 lifecycles
- `resourcemanager-tag-lifecycle` 23 (mut 다수, RG 생성) · `gen-wave4-rmtags` 15 · `gen-resource-group-bulk` 3 (mut 2) · `gen-wave2-rg` 3 (mut 2)
- **SUBSUMED ×3** [ll]: `gen-resource-group-bulk`(3) ⊂ rmtags(15)·tag-lifecycle(23); `gen-wave2-rg`(3) ⊂ rmtags(15) — 유일 0. 현재 서비스 선택 시 **RG 생성 왕복이 4회**.
- 권고: **retire `gen-resource-group-bulk`·`gen-wave2-rg` → `resourcemanager-tag-lifecycle`**. `gen-wave4-rmtags` ↔ tag-lifecycle는 J=0.462 near-miss지만 rmtags는 403 권한경계(PF) 프로브 문서 가치 — keep-distinct.

### networking/vpc 소속 2건 (§D의 family 검토와 별개로 즉시 가능)
- **SUBSUMED** [Hl] `vpc-internet-gateway`(7) ⊂ `networking-vpc-internet-gateway`(8) ⊂ `gen-pilot-net-basics`(20); ⊂ `vpc-nat-gateway`(15)·`vpc-subnet-vip-nat`(23)도 성립 — **4중 커버**. `gen-pilot-net-basics` 노트에 이미 "vpc-cidr-secondary/-internet-gateway ... retire after this goes green" 계획 존재.
- 권고: **retire `vpc-internet-gateway`** (유일 0, 기존 계획 실행); **retire `networking-vpc-internet-gateway` → `gen-pilot-net-basics`** (유일 0이나 `internet-gateway` node 재지정 필요).

## B. 이관-후-은퇴 (유일 endpoint 1개 — showlaunchconfiguration 선례와 동형)

| service | A ⊂1 B (retire A) | ex 수치 | 이관할 유일 endpoint | 절약 |
|---|---|---|---|---|
| application-service/apigateway | `application-apigateway-api-resource` → `apigateway-api-write-coverage` | 9/34∩8, mutEx 6 | `GET v1/apis` (listapis) | API+resource+deployment 생성 왕복 1회 (write-coverage가 자체 API를 또 만듦). nodes: apigw-api·root-resource·resource 재지정 |
| management/iam | `gen-wave2-iam` → `gen-wave5-iam-bindings` | 7/9∩6, mutEx 4 | `GET v1/groups/*/members` | group+policy 생성 왕복 1회 (wave5도 self-created principals 사용) |
| security/kms | `security-kms-key` → `security-kms-transit-crypto` | 5/16∩4, mutEx 3 | `GET v1/kms/transit` (list) | 키 생성/삭제예약 왕복 1회 (transit-crypto가 이미 키 2개 생성). node kms-key 재지정 |
| security/secretsmanager | `security-secretsmanager-secret` → `security-secretsmanager-writes` | 12/16∩11, mutEx 9 | `GET v1/secrets` (list) | **KMS키+secret 이중 프로비저닝 제거** (양쪽 다 kms_id 체인). node secret 재지정 |
| networking/security-group | `networking-security-group-update` → `networking-security-group` | 5/7∩4, mutEx 3 | `PUT v1/security-groups/*` (setsecuritygroup — body `{description, loggable}`만 유효, KEY FACTS 노트째 이관) | SG 생성 왕복 1회 |
| networking/vpc | `vpc-cidr-secondary` → `gen-pilot-net-basics` | 5/20∩4, mutEx 4 | `PUT v1/vpcs/*` | 기존 은퇴 계획 실행분 |
| networking/vpc | `vpc-port` → `gen-pilot-net-basics`(또는 `heavy-shared-networking`) | 10/20∩9, mutEx 7 | `PUT v1/ports/*` | port 생성 왕복 1회 |
| networking/vpc | `vpc-nat-gateway` → `heavy-shared-networking` | 15/48∩14, mutEx 11 | `PUT v1/internet-gateways/*` | NATGW+publicip 왕복 1회 (heavy 배치 한정) |

각 건 권고: **migrate-then-retire** — 유일 endpoint를 유지측에 step으로 이식(strict 200)
후 `enabled:false + _replaced_by`. ASG 때와 동일 절차.

## C. 통합 검토 (HIGH-OVERLAP — 병합/차별화 owner 결정 필요)

### database/postgresql — 최대 비용 건
- roster: `database-postgresql-cluster` [H] 30ex/36full (mutEx 18; nodes 5개) · `postgresql-cluster-subops-full` [H] 34ex/40full · `postgresql-read-coverage` [l] 4
- **HIGH-OVERLAP** [HH] cluster ↔ subops-full: ex 30/34∩23 J=0.561 (full 36/40∩29). subops-full은 의도적 SELF-SUFFICIENT(생성 body를 cluster에서 verbatim 복사) — 단일 서비스 선택 시 **billable PG 클러스터 2기** 프로비저닝.
- cluster 유일 7(archive/backup-histories/replicas/engine-properties/parameter-groups/parameters/requests GET들) · subops-full 유일 11(log-export, switchover, kernel-upgrade, patch 등 쓰기).
- 권고: **통합 검토** — cluster의 유일 GET 7개를 subops-full에 흡수하고 단일-서비스 scope에선 subops-full만 남기는 안(5개 node 재지정 필요) vs 현행 유지(중복 클러스터 감수). `postgresql-read-coverage`(GET-only, 기존 클러스터 soft-capture)는 light 커버리지 — keep-distinct.

### networking/loadbalancer — LB 스택 2~3기
- roster: `gen-heavy-lb-members` [H] 32ex (wave5-net) · `networking-loadbalancer-members-nat` [H] 27ex (nodes: lb-member·lb-member-bulk·lb-static-nat) · `heavy-shared-networking` [H] 39ex (LB 체인 포함) · `gen-lb-members-light` [l] 10ex · `networking-loadbalancer-reads` [l] 5
- **HIGH-OVERLAP** [HH] `gen-heavy-lb-members` ↔ `members-nat`: ex 32/27∩21 J=0.553 — 각자 **billable LB 스택을 따로 세움**; heavy-shared-networking까지 겹치면(J=0.365 near-miss) 선택 한 번에 LB 최대 3기.
- members-nat 유일 6: `POST/DELETE v1/loadbalancers/*/private-static-nats`, `GET v1/loadbalancers/certificates(/*)`, `GET v1/internet-gateways`, `PUT v1/internet-gateways/*`. gen-heavy 유일 11: hc/listener/server-group/member show·PUT류.
- 권고: **통합 검토** — members-nat 유일 6개를 `gen-heavy-lb-members`에 병합 후 members-nat 은퇴(node 3개 재지정), heavy-shared-networking의 LB 체인과 3자 정리 포함. `gen-lb-members-light`(⊂ gen-heavy 10/32)는 light-run 커버리지 — keep-distinct.

### container/scr — registry quota=1 충돌
- roster: `container-scr-registry` 17ex (mutEx 12; nodes: container-registry·scr-repository) · `gen-wave2-scr` 15ex · `scr-repo-borrow-coverage` 11ex · `gen-scr-endpoint` 등
- **HIGH-OVERLAP** [ll] registry ↔ `gen-wave2-scr`: ex 17/15∩13 J=0.684 — **둘 다 registry+repo를 생성**하는데 registry quota=1 → 선택 시 두 번째 create는 충돌/실패 소지.
- wave2-scr 유일 2: `GET v1/repositories/*`, `GET v1/repositories/*/images`. registry 유일 4: 목록 + endpoint/acl PUT 3종.
- 권고: **wave2-scr 유일 GET 2개를 `container-scr-registry`에 병합 후 retire `gen-wave2-scr`**. `scr-repo-borrow-coverage`(11 ⊂ 17)는 **keep-distinct** — quota 슬롯이 이미 점유된 환경에서의 유일 실행 경로(borrow 전략, Hard Rule 6의 skip-not-fail 관점).

### compute/virtualserver — 볼륨 3중 생성
- 관련: `compute-virtualserver-volume-snapshot` 7ex (nodes: block-volume·volume-snapshot) · `gen-wave2-volume` 9ex · `gen-wave-vslight` 9ex — 선택 시 **볼륨 create가 3회**.
- **SUBSUMED-1** volume-snapshot → gen-wave2-volume (7/9∩6, 유일 `GET v1/snapshots/*`); **HIGH-OVERLAP** vslight ↔ wave2-volume (9/9∩6 J=0.5; vslight 유일=server-groups CRUD, wave2 유일=snapshots CRUD+transfer).
- 권고: **통합 검토** — 방향 제안: wave2-volume의 snapshot-list/transfer 유일분을 **named `compute-virtualserver-volume-snapshot`에 흡수 후 `gen-wave2-volume` 은퇴**(node 유지 방향), vslight는 server-group 전담으로 볼륨 step 제거.

### networking/vpc — `vpc-endpoint` 짝
- **HIGH-OVERLAP** [lH] `gen-vpc-endpoint`(12ex, target으로 volume 생성 — 유일 3이 전부 v1/volumes 배관) ↔ `vpc-endpoint`(12ex, UNPROVEN; 유일 3: `GET v1/vpc-endpoints`, `GET .../connectable-resources`, `PUT v1/vpc-endpoints/*`).
- 권고: **통합 검토** — named의 유일 3개를 gen 쪽에 병합하고 한쪽 은퇴(gen이 LIVE 경로면 gen 유지).

### application-service/apigateway — 3-way near-miss 군집 (참고)
- `apigateway-api-write-coverage` ↔ `gen-wave-apigw` J=0.41 · ↔ `gen-wave5-apigw-policy` J=0.477 · wave ↔ wave5 J=0.444 — 임계 미만이나 **API 생성 왕복이 lifecycle마다 반복**. §B의 base 은퇴와 함께 apigw family 1회 정리 권장.

### database/cachestore · epas · mariadb — shared-dbaas vs subops-full (구조 결정)
- 각 서비스에서 **SUBSUMED** [HH] `heavy-shared-dbaas`(서비스내 ex 5) ⊂ `*-cluster-subops-full`(24~33). subops-full은 의도적 self-sufficient(“bodies copied VERBATIM from heavy-shared-dbaas”).
- 비용: 단일 DB 서비스 선택 → 해당 엔진 클러스터 **2기** + heavy-shared-dbaas는 **다른 두 엔진 클러스터까지** 프로비저닝(cachestore 선택이 epas·mariadb 클러스터를 만듦).
- 권고: **keep-distinct-but-review** — heavy-shared-dbaas는 7개 node의 provenance 앵커라 은퇴 불가; 대신 (a) 단일-서비스 scope 확장에서 shared-dbaas 제외 또는 (b) 엔진별 분할을 owner 결정 항목으로 올림.

## D. keep-distinct 판정 (표면 포함이지만 은퇴 부적합 — 사유 명시)

- ai-ml/cloud-ml: `gen-cloudml-image`(GET 1) ⊂ `gen-cloudml-chain`(17,[H]) — **light 선택의 유일 커버리지**(chain은 heavy 게이트 뒤). 중복 비용 GET 1회 ≈ 0.
- database/mysql·postgresql(+cachestore·epas·mariadb read-coverage류): `*-read-coverage`(GET-only, 기존 클러스터 soft-capture) ⊂ heavy 클러스터 lifecycle — **light 커버리지 경로**, 중복 비용 GET 수회.
- networking/direct-connect: `gen-direct-connect`(3ex, DX 생성) ⊂ `networking-direct-connect-routing`(7ex,[H]) — routing은 heavy 게이트 뒤라 **light DX 커버리지가 사라짐**; heavy-run에서만 DX 왕복 2회 중복. owner가 heavy-이관 감수 시 retire 가능.
- storage/filestorage: `filestorage-volume`(5ex, 유일 `GET v1/volumes`) ⊂1 `filestorage-replication-schedule`(17ex,[H]) — 동일 사유(light 게이트). 부수 관찰: filestorage-volume도 billable NFS 볼륨을 만들면서 heavy 미표기.
- management/cloudmonitoring: `gen-cm-account-resource`(GET 1) ⊂ `cloudmonitoring-readonly-shows`(10) — 단, 유일 가치가 **`X-ResourceType: INSTANCE` 헤더 변형**(norm에 안 보임; readonly-shows는 VM/Object Storage만). INSTANCE 헤더 step을 readonly-shows에 이식하면 그때 은퇴 가능(이관-후-은퇴, read-only라 저순위).
- application-service/queueservice `gen-wave3-qfifo`, container/scr `scr-repo-borrow-coverage`, networking/loadbalancer `gen-lb-members-light`, management/resourcemanager `gen-wave4-rmtags` — 각 §A/§C에 사유 기재.

## E. Intra-lifecycle 반복

동일 `(METHOD, norm_path)`를 4회 이상 호출하는 lifecycle **없음** (poll step 제외 기준).

## F. No findings — 깨끗한 서비스 (감사 34개 중 6개)

`compute/scf` · `container/ske` · `data-analytics/eventstreams` · `networking/vpn` · `storage/backup` · `storage/baremetal-blockstorage`

감사 제외(선택 시 lifecycle ≤1 — 중복 자체가 불가, 25개):
ai-ml/aimlops-platform, compute/baremetal, compute/multinodegpucluster, data-analytics/data-flow,
data-analytics/data-ops, data-analytics/searchengine, data-analytics/vertica, database/sqlserver,
devops-tools/devopsservice, financial-management/billingplan, financial-management/budget,
management/cloudcontrol, management/iam-identity-center, management/loggingaudit, management/organization,
networking/cdn, networking/dns, networking/firewall, networking/gslb, platform/sts,
security/certificatemanager, security/configinspection, security/secretvault, storage/archivestorage,
storage/parallel-filestorage

---
*레시피 무수정 — 본 문서는 결정 자료. 재현: 본문 상단 감사 기준 참조 (모델·loader·norm_path 전부 repo 내 코드로만 계산, 네트워크 없음).*
