# 서비스별 커버리지 갭 리포트 (병렬 agent 분석, 2026-06-13)

> 산출 방법: origin/dashboard-data의 verified_endpoints.json(라이브 2xx 증명 집합)
> 대비 data/api_catalog.json 전체 ops. 모델 노드 notes의 블로커와 교차 확인.
> postgresql/virtualserver/ske/scr/조회전용 6종은 본문 Q&A로 별도 진행되어 제외.
> 분류: A 자동 진행 가능 · B 무겁지만 가능(heavy/과금) · C owner 확인 필요 · D 차단/제외(waiver)


## 그룹 1 — 네트워킹 (agent 분석 2026-06-13)

### 요약: 209 ops 중 98 검증(47%)

| 서비스 | 전체 | 검증 | 남음 | 분류 |
|---|---|---|---|---|
| security-group | 9 | 8 | 1 | A — list 필수쿼리 (이미 수정, rev 대기) |
| loadbalancer | 34 | 20 | 14 | B — member/NAT/setter류 (heavy 체인 보강) |
| vpc | 95 | 49 | 46 | B/C — endpoint·private-nat·TGW 자식·privatelink·peering 승인 |
| dns | 22 | 13 | 9 | B/C — public-domain 계열 + setter |
| gslb | 10 | 2 | 8 | C — 합성본 라이브 미증명 (다음 윈도우 투입 가능) |
| vpn | 10 | 2 | 8 | C — 터널 협상은 실 peer 필요 (생성/삭제는 placeholder로 가능) |
| cdn | 9 | 1 | 8 | C — placeholder origin으로 생성 시도 가능 |
| firewall | 8 | 2 | 6 | D→B — API create 없음(암묵 생성): carrier에 firewall_enabled:true로 열림 |
| direct-connect | 8 | 1 | 7 | D — 물리 회선 의심 (확인 필요) |
| network-logging | 4 | 0 | 4 | 준비 완료 — logsink+모델 재작성, wave4 투입 |

### 키 블로커/결정
1. **firewall**: POST /v1/firewalls가 없음 — IGW/TGW/DC/LB 생성 시 `firewall_enabled: true`로 암묵 생성 → carrier 모델에 옵션 추가 + firewall_id 캡처가 해법 (R3 작업, owner 결정 불필요)
2. **private-nat**: direct-connect가 막히면 **transit-gateway 경로로 검증** (TGW는 create 검증됨)
3. **vpc-endpoint**: resource_key가 실 FS 볼륨 id여야 함 — filestorage-volume 노드와 배선하면 해결 (작업 가능)
4. **direct-connect**: API 생성이 물리 회선 없이 되는지 — **owner 확인 필요**
5. **vpn 터널 협상**: 실 peer IP/PSK 없으면 협상 실패 예상 — 생성/삭제만 검증하고 협상은 제외할지 **owner 확인**



## 그룹 2 — DBaaS 형제 (agent 분석 2026-06-13, PG 제외)

### 요약: 281 ops 중 73 검증(26%) · 남음 208 [자동 A:54 / PG패턴 B:50 / 확인 C:90 / 차단 D:14]

| 서비스 | 전체 | 검증 | 남음 | 특이점 |
|---|---|---|---|---|
| mysql | 45 | 16 | 29 | PG 패턴 그대로 전이 가능 |
| mariadb | 46 | 13 | 33 | delete ~90분 느림 (윈도우 계획 주의) |
| epas | 47 | 13 | 34 | create 라이브 2xx 미증명 — 1순위 검증 대상 |
| sqlserver | 38 | 5 | 33 | **라이선스 credential 필요** — 게이트 |
| cachestore | 32 | 13 | 19 | Sentinel replica_count doc(1-2) vs live(0) 충돌 기록 |
| eventstreams | 24 | 5 | 19 | Kafka 토폴로지 role_type 조합 미문서 → create 미증명 |
| searchengine | 26 | 4 | 22 | admin credential/password 정책 미문서 |
| vertica | 23 | 4 | 19 | 위와 동일 + 라이선스 가능성 |

### PG 재사용 가능 (mysql/mariadb/epas): create body 구조·replica 체인·restore/patch/parameter/backup setter 전부 동형. 엔진별 발산: mysql utf8mb4/case_sensitive, mariadb utf8/slow-delete, epas encoding/locale.

### owner 질문 (이 그룹 Top)
1. **sqlserver 라이선스**: 콘솔에서 라이선스 등록이 선행인가? 테스트 계정에 등록 가능?
2. **eventstreams**: 콘솔에서 클러스터 만들 때 broker/zookeeper 구성을 어떻게 고르는지 (role_type 조합)
3. **searchengine/vertica**: 콘솔 생성 시 admin 계정/패스워드 정책 + 라이선스 여부
4. **cachestore**: Sentinel 구성 시 replica 수를 콘솔에서 어떻게 받는지 (0 허용?)
5. engine 구버전 (patch용): mysql/mariadb/epas 각각 콘솔 버전 목록



## 그룹 3 — 스토리지 · 관리 · AI/데이터 (agent 분석 2026-06-13)

### 요약표

| 서비스 | 전체 | 검증 | 남음 | 분류 |
|---|---|---|---|---|
| storage/backup | 31 | 3 | 20 | C — 실 서버 필요 |
| storage/filestorage | 21 | 8 | 13 | B — 복제/스케줄 잔여 |
| storage/archivestorage | 25 | 0 | 0 | D — 전용 인증키 영구 제외 (waiver) |
| storage/baremetal-blockstorage | 41 | 2 | 0 | D — 물리/과금 (waiver) |
| storage/parallel-filestorage | 11 | 1 | 3 | D — owner 읽기 전용 제외 |
| mg/cloudcontrol (Landing Zone) | 15 | 0 | 15 | C |
| mg/iam | 62 | 25 | 37 | B |
| mg/iam-identity-center | 32 | 1 | 0 | D — SSO 미활성 (waiver) |
| mg/loggingaudit | 10 | 1 | 9 | B — logsink 배선 완료, trail 체인 가능 |
| mg/resourcemanager | 27 | 13 | 14 | B |
| mg/servicewatch | 31 | 16 | 15 | B |
| mg/cloudmonitoring | 18 | 4 | 0 | D — entitlement (waiver) |
| ai-ml/cloud-ml | 9 | 0 | 9 | C — SCR 인증키 게이트 |
| ai-ml/aimlops-platform | 12 | 2 | 10 | C |
| da/data-flow | 17 | 3 | 14 | C — engine/server-type lookup 필요 |
| da/data-ops | 17 | 3 | 14 | C |
| da/quick-query | 12 | 1 | 11 | C |
| ap/apigateway | 55 | 38 | 17 | A — privatelink/resource-policy 확장 |
| ap/queueservice | 12 | 9 | 3 | A — qfifo 합성 완료 (rev7 검증 중) |
| cp/scf | 36 | 25 | 11 | A — apigw 트리거/privatelink |
| devops-tools/devopsservice | 6 | 1 | 5 | C — create 체인 미증명 |
| cp/baremetal · multinodegpucluster | 32 | 5 | 0 | D — 물리/과금 (waiver) |

### owner 질문 (이 그룹 Top)
1. **backup (20 ops)**: 백업은 실 서버에 붙는다 — heavy 윈도우의 합성 VM에 backup-policy를 걸어 검증하는 흐름 승인? (agent 설치가 선행이면 VM 안에서의 절차 확인 필요)
2. **loggingaudit**: trail마다 개별 버킷? 공유 버킷(apitest-logsink) 1개로 여러 trail 가능?
3. **data-flow/data-ops/quick-query/eventstreams**: 콘솔에서 생성 시 어떤 선행/입력이 필요한지 (engine version, server type 류) — 콘솔 한 번 훑어주시면 lookup 노드로 배선
4. **cloud-ml**: SCR 인증키 게이트 — 프로브 결과 대기 중 (public endpoint 시도)
5. **aimlops-platform**: 플랫폼 생성이 과금/시간이 큰지 (콘솔 기준)



## 종합 — owner 확인 대기 질문 (우선순위)

1. **sqlserver 라이선스**: 콘솔에서 라이선스 등록 선행 여부 / 테스트 계정 등록 가능 여부 (33 ops 게이트)
2. **eventstreams**: 콘솔 클러스터 생성 시 broker/zookeeper(role_type) 구성 선택 방법 (19 ops)
3. **searchengine/vertica**: 콘솔 생성 시 admin 계정·패스워드 정책 + 라이선스 여부 (41 ops)
4. **direct-connect**: 물리 회선 없이 API 생성 가능 여부 (7 ops + private-nat 우회로 결정)
5. **backup**: heavy 윈도우의 합성 VM에 backup-policy를 거는 흐름 승인 (20 ops; agent 설치 선행 여부 포함)
6. **vpn**: 실 peer 없이 생성/삭제만 검증하는 범위 승인 (협상 실패는 예상된 결과로 기록)
7. **data-flow/data-ops/quick-query**: 콘솔 생성 흐름 한 번 훑어봐 주시면 lookup 배선 (39 ops)
8. **loggingaudit**: trail 여러 개가 버킷 1개(apitest-logsink) 공유 가능한지
9. **DBaaS 엔진 구버전**: mysql/mariadb/epas 콘솔 버전 목록 (patch/upgrade용 — PG는 17.6/16.10 확인됨)
10. **aimlops-platform**: 생성 과금/시간 규모

## 이미 결정·진행 중 (owner 액션 불필요)

- firewall: carrier(IGW/TGW/LB) 모델에 firewall_enabled 옵션 + firewall_id 캡처 (R3 작업)
- private-nat: TGW 경로로 우회 검증
- vpc-endpoint: filestorage-volume 실 id 배선
- IAM 37 ops / apigw 17 / scf 11 / LB 14: 자동 웨이브 후보
- DBaaS A-카테고리 54 ops: setter/액션 일괄 verify 추가
- PG replica/restore/patch: 모델링 agent 작업 중 (승인 완료분)
