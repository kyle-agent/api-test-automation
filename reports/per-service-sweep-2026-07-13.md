# 서비스 단위 순차 통합테스트 — 2026-07-13 밤 (23:00 KST 시작)

**목표**: 서비스를 하나씩 선택 → 의존 폐포 포함 라이브 실행(생성/실행/teardown) → 전 API 호출 확인 → teardown 더블체크(누수 0) → 400/결함 기록.
**게이트**: `SCP_DAG_RUNNER=true SCP_ALLOW_MUTATIONS=true SCP_ALLOW_DESTRUCTIVE=true SCP_RUN_HEAVY=true` (heavy/과금 전부 포함).
**에러 정책**: 오늘밤은 **기록만**, 개선은 아침에.
**Baseline (시작 전)**: owned survivors = **1** (servicewatch `/v1/log-groups` 1건 — 상시 auto-created, 우리 스윕 대상 아님).

범례: ✅ 정상(create/exec/teardown ok, 누수 없음) · ⚠️ 부분(일부 fail/400 있으나 teardown ok) · ❌ 실패(run fail/hang 또는 누수)

---

## 진행 요약 (서비스별 한 줄)

| # | 서비스 | dry-run(폐포/LC) | 라이브 결과 | teardown(survivors) | 400/결함 | 판정 |
|---|--------|------------------|-------------|---------------------|----------|------|
| 1 | networking/vpc | 15 / 10 | passed=9, skipped=1(VPC 5개 cap 초과 skip) | 32→1(reconcile로 baseline 복귀) | 4xx=22(대부분 xcov 프로브 placeholder), fail-step=11. 실결함 후보: privatelink-service IP↔subnet CIDR 미포함 400, network-logging 중복 409 | ⚠️ |

### 사전 incident (기록)
- 첫 VPC 라이브 런이 Bash 2분 포그라운드 한도에 걸려 teardown 전에 kill됨 → 자원 44건 누수(subnet/vpc/transit-gateway/publicip/filestorage volume).
- 대응: reconciler 3패스 + async 삭제 대기 → **baseline(=1)로 완전 복귀 확인**. 이후 모든 라이브 런은 백그라운드(timeout 40분)로 전환.
- 교훈(개선 후보): 라이브 런은 반드시 detached 실행. verify_clean survivors 카운트가 패스마다 표기 단위가 달라 혼동(44→12→1) → 카운트 일관성 개선 여지.

## 진행 현황 (자동 갱신)

<!--PROGRESS-BEGIN-->

**진행: 37/56 완료** — ✅16 · ⚠️19 · ❌2 · (teardown 미복귀 잔존: 0)

| # | 서비스 | 폐포/LC | 라이브(passed/skip) | 4xx;5xx;fail | teardown surv;recon | 판정 |
|---|--------|---------|---------------------|--------------|---------------------|------|
| 1 | networking/vpc | 15/10 | passed=9, skipped=1 | 4xx=22;5xx=0;failstep=11 | surv=1;recon=yes | ⚠️ERRS |
| 2 | networking/security-group | 2/1 | passed=1 | 4xx=0;5xx=0;failstep=0 | surv=1;recon=no | ✅OK |
| 3 | networking/firewall | 4/3 | passed=3 | 4xx=2;5xx=0;failstep=1 | surv=1;recon=yes | ⚠️ERRS |
| 4 | compute/virtualserver | 25/11 | passed=11 | 4xx=14;5xx=0;failstep=7 | surv=1;recon=yes | ⚠️ERRS |
| 5 | compute/baremetal | 3/2 | passed=2 | 4xx=0;5xx=0;failstep=0 | surv=1;recon=yes | ✅OK |
| 6 | compute/scf | 12/6 | passed=6 | 4xx=10;5xx=0;failstep=2 | surv=1;recon=yes | ⚠️ERRS |
| 7 | compute/multinodegpucluster | 6/2 | passed=2 | 4xx=0;5xx=0;failstep=0 | surv=1;recon=yes | ✅OK |
| 8 | container/scr | 4/3 | passed=2, skipped=1 | 4xx=0;5xx=0;failstep=0 | surv=1;recon=no | ✅OK |
| 9 | container/ske | 12/6 | - | 4xx=2;5xx=0;failstep=1 | surv=1;recon=yes | ❌TIMEOUT |
| 10 | storage/filestorage | 4/2 | passed=2 | 4xx=2;5xx=0;failstep=1 | surv=1;recon=no | ⚠️ERRS |
| 11 | storage/parallel-filestorage | 2/1 | passed=1 | 4xx=2;5xx=1;failstep=2 | surv=1;recon=no | ⚠️ERRS |
| 12 | storage/backup | 13/6 | passed=6 | 4xx=2;5xx=1;failstep=2 | surv=1;recon=yes | ⚠️ERRS |
| 13 | storage/archivestorage | 2/2 | passed=2 | 4xx=23;5xx=0;failstep=14 | surv=1;recon=no | ⚠️ERRS |
| 14 | storage/baremetal-blockstorage | 4/2 | passed=2 | 4xx=7;5xx=0;failstep=5 | surv=1;recon=no | ⚠️ERRS |
| 15 | database/mysql | 24/4 | - | 4xx=1;5xx=10;failstep=6 | surv=1;recon=yes | ❌TIMEOUT |
| 16 | networking/loadbalancer | 11/5 | failed=1, passed=4 | 4xx=19;5xx=0;failstep=7 | surv=1;recon=yes | ⚠️RC1 |
| 17 | networking/dns | 2/1 | passed=1 | 4xx=0;5xx=0;failstep=0 | surv=1;recon=yes | ✅OK |
| 18 | networking/gslb | 1/1 | passed=1 | 4xx=0;5xx=0;failstep=0 | surv=1;recon=no | ✅OK |
| 19 | networking/cdn | 1/1 | passed=1 | 4xx=0;5xx=0;failstep=0 | surv=1;recon=no | ✅OK |
| 20 | networking/vpn | 4/3 | passed=3 | 4xx=0;5xx=0;failstep=0 | surv=1;recon=yes | ✅OK |
| 21 | networking/direct-connect | 2/2 | passed=2 | 4xx=2;5xx=0;failstep=1 | surv=1;recon=yes | ⚠️ERRS |
| 22 | security/kms | 1/1 | passed=1 | 4xx=0;5xx=0;failstep=0 | surv=1;recon=yes | ✅OK |
| 23 | security/secretsmanager | 2/2 | passed=2 | 4xx=0;5xx=0;failstep=0 | surv=1;recon=yes | ✅OK |
| 24 | security/secretvault | 1/1 | passed=1 | 4xx=0;5xx=0;failstep=0 | surv=1;recon=no | ✅OK |
| 25 | security/certificatemanager | 2/2 | passed=2 | 4xx=0;5xx=0;failstep=0 | surv=1;recon=no | ✅OK |
| 26 | security/configinspection | 1/1 | passed=1 | 4xx=0;5xx=0;failstep=0 | surv=1;recon=no | ✅OK |
| 27 | management/iam | 12/8 | passed=8 | 4xx=0;5xx=2;failstep=2 | surv=1;recon=no | ⚠️ERRS |
| 28 | management/iam-identity-center | 5/5 | passed=5 | 4xx=0;5xx=0;failstep=0 | surv=1;recon=no | ✅OK |
| 29 | management/organization | 6/6 | passed=6 | 4xx=0;5xx=0;failstep=0 | surv=1;recon=no | ✅OK |
| 30 | management/resourcemanager | 2/1 | passed=1 | 4xx=2;5xx=0;failstep=1 | surv=1;recon=yes | ⚠️ERRS |
| 31 | management/cloudcontrol | 2/2 | passed=2 | 4xx=2;5xx=0;failstep=1 | surv=1;recon=yes | ⚠️ERRS |
| 32 | management/cloudmonitoring | 4/1 | passed=1 | 4xx=2;5xx=0;failstep=1 | surv=1;recon=no | ⚠️ERRS |
| 33 | management/servicewatch | 8/5 | passed=5 | 4xx=2;5xx=0;failstep=1 | surv=1;recon=yes | ⚠️ERRS |
| 34 | management/loggingaudit | 1/1 | passed=1 | 4xx=2;5xx=0;failstep=1 | surv=1;recon=yes | ⚠️ERRS |
| 35 | management/network-logging | 1/1 | passed=1 | 4xx=2;5xx=0;failstep=1 | surv=1;recon=no | ⚠️ERRS |
| 36 | management/quota | 2/1 | passed=1 | 4xx=0;5xx=0;failstep=0 | surv=1;recon=no | ✅OK |
| 37 | management/support | 2/1 | passed=1 | 4xx=2;5xx=0;failstep=1 | surv=1;recon=no | ⚠️ERRS |

<!--PROGRESS-END-->

## audit log 대조 — 우리 GET "준비됨" 시각 vs 백엔드 실제 create/delete end (사용자 요청)
방법: `GET /v1/logs`(loggingaudit) event_type `{자원}.create/delete/update.start/end` 실제 시각 vs `observations.jsonl` readiness GET 시각(자원타입+생성구간 매칭).

**백엔드 실제 소요(오늘밤)**: create median 119s / p90 260s / **max private-dns ~1160s(19분)**, ske cluster 688s, vpc-peering 692s. delete median 20s / max subnet 506s(8분).

**조기진행(우리가 백엔드 create.end보다 먼저 진행) 6건 / 정상대기 36건:**
- private-dns ×2: 백엔드 1043~1160s 생성인데 우리 폴링 ~100s 후 진행 → ~16~18분 먼저. 폴링 예산 부족.
- subnet ×4: 백엔드 134~253s 생성인데 우리 20~62s 후 진행 → 72~201s 먼저. provisioner의 `NOT waiting ACTIVE (adopt-time gate)` 설계 여파.
- 개선안: (a) subnet adopt-time gate가 ACTIVE까지 대기하도록, (b) private-dns 폴링 예산을 실제(~20분)에 맞춰 확대 or 의존 스텝을 optional 유지, (c) observations에 concrete resource_id 기록해 정밀 대조 가능케.

---

## 최종 요약 (사용자 요청으로 06:48 KST 중단 — 37/56 실행)

**실행 37 · 판정**: ✅정상 16 · ⚠️부분(4xx 있으나 teardown OK) 19 · ❌timeout 2 (ske, mysql)
**teardown**: 완료된 37개 서비스 **전부 surv=1(baseline)로 복귀 — 지속 누수 0**. (중단된 data-flow의 in-flight 자원 41건은 종료 후 reconcile로 별도 정리)

### ❌/주의 서비스
- **container/ske** ❌TIMEOUT — cluster/nodepool `upgrade-wait`(예산 3600s)가 40분 cap 초과. teardown은 reconcile로 회복.
- **database/mysql** ❌TIMEOUT — cluster 삭제 `wait-gone` hang(로그 25분 동결) + **5xx=10**. reconcile로 42건 회복.
- **networking/loadbalancer** ⚠️RC1 — `gen-heavy-lb-members` 실패(멤버 VM 미발견)로 되돌리기 teardown, 누수 25건 reconcile.

### 미실행 (19개 — 중단 시점 이후)
- data-analytics: **data-flow(중단)**, data-ops, eventstreams, quick-query, searchengine, vertica
- ai-ml: cloud-ml, aimlops-platform
- application-service: apigateway, queueservice · devops-tools: devopsservice
- financial-management: billingplan, budget · platform: sts
- **DB(후순위 지연분)**: mariadb, postgresql, epas, sqlserver, cachestore

### 핵심 발견 (기록 — 개선은 승인 후)
1. **DBaaS teardown hang (systematic)** — mysql·mariadb 클러스터 삭제 `wait-gone`이 무출력으로 hang → 40분 timeout. reconcile는 회복. (mysql 5xx=10 동반)
2. **ske/데이터클러스터 upgrade-wait > per-run cap** — cluster/nodepool 업그레이드 대기 예산(3600s)이 실용적 런 타임아웃 초과 → bounded 런 완주 불가. data-flow도 ske 폐포 포함으로 동일 위험.
3. **private-dns 매우 느림** — 백엔드 실제 create ~17~19분(audit 확인). VPC에서 private-dns가 삭제 막음(reconciler 매핑 필요), dns 서비스 28분 소요의 원인.
4. **audit 대조 — 조기진행 6건** — subnet(adopt-time gate가 ACTIVE 대기 skip, 백엔드 2~4분 생성 중 20~60초 후 진행 ×4), private-dns(폴링 예산 ~100s vs 실제 ~19분 ×2). 나머지 36건은 정상 대기.
5. **xcov 커버리지 프로브의 4xx 다수** — 대부분 placeholder-driven(의도된 커버리지 기록), 실결함과 구분 필요. 실결함 후보: privatelink-service IP↔subnet CIDR 400, network-logging 중복 409, mysql backup 401, LB listener ValidationError 400.

### 성능 병목 (사용자 문의 답)
- 느린 서비스의 공통점 = **공유 VPC/subnet 프로비저닝 + subnet ACTIVE 대기**(서비스 격리라 매번 반복). 빠른 서비스(18초~6분)는 이 단계가 없음.
- 추가로 **서비스별 verify_clean(전체 계정 스캔 ~1~2분) + 누수 시 reconcile(최대 3패스)** 세금.
- **개선안**: (a) 공유 VPC를 서비스 간 재사용해 재프로비저닝 제거, (b) DBaaS/ske teardown wait에 실질 타임아웃+진행로그, (c) subnet adopt-time gate가 ACTIVE까지 대기, (d) private-dns 폴링예산 확대 또는 optional 유지, (e) observations에 concrete resource_id 기록(정밀 대조용), (f) reconciler에 private-dns 삭제 매핑 추가.

### 최종 자원 정리 (06:57 KST 확인)
사용자 중단 후 in-flight data-flow 자원 41건 reconcile → **owned survivors = 1**(baseline 복귀). 남은 1건은 `servicewatch /v1/log-groups`(상시 auto-created, 오늘밤 생성분 아님). **오늘밤 테스트로 생성된 자원은 전부 삭제 확인.** 실행 중 sweep 프로세스 없음.

---

## 자원별 실제 소요시간 누적 (audit log 기반)
- 수집 도구: `python -m tools.audit_timing_collector collect --run-label <label>` (sweep 종료 시 자동 실행).
- 누적 저장: `data/audit_timings.jsonl` (실행마다 append, (resource_id,action) dedupe). report용: `... report`.
- 이번 실행(2026-07-13-per-service-sweep) 수집: 919 자원-작업 레코드.

## 자원별 실제 소요시간 (audit log 누적, 919건 / runs: 2026-07-13-per-service-sweep)

| action | resource_type | n | median | p90 | max |
|--------|---------------|---|--------|-----|-----|
| archive-config.modify | mysql | 1 | 38s | 38s | 38s |
| archive-config.modify | mariadb | 1 | 27s | 27s | 27s |
| archive-config.sync | mysql | 1 | 29s | 29s | 29s |
| backup-config.modify | mariadb | 1 | 41s | 41s | 41s |
| backup-config.modify | mysql | 1 | 41s | 41s | 41s |
| block-storage.create | mysql | 1 | 118s | 118s | 118s |
| block-storage.resize | mysql | 1 | 73s | 73s | 73s |
| create | private-dns | 3 | 1096s | 1160s | 1160s |
| create | vpc-peering | 1 | 692s | 692s | 692s |
| create | cluster | 2 | 628s | 688s | 688s |
| create | mysql | 1 | 555s | 555s | 555s |
| create | mariadb | 1 | 534s | 534s | 534s |
| create | subnet | 50 | 235s | 259s | 319s |
| create | transit-gateway | 4 | 120s | 121s | 121s |
| create | virtual-server | 9 | 91s | 162s | 162s |
| create | loadbalancer | 1 | 51s | 51s | 51s |
| create | cdn | 1 | 49s | 49s | 49s |
| create | vpn-tunnel | 1 | 33s | 33s | 33s |
| create | privatelink-endpoint | 1 | 27s | 27s | 27s |
| create | privatelink-service | 1 | 25s | 25s | 25s |
| create | snapshot | 1 | 22s | 22s | 22s |
| create | vpc-endpoint | 1 | 22s | 22s | 22s |
| create | internet-gateway | 7 | 19s | 23s | 23s |
| create | nat-gateway | 2 | 16s | 17s | 17s |
| create | private-nat | 1 | 15s | 15s | 15s |
| create | gslb | 1 | 13s | 13s | 13s |
| create | api | 3 | 13s | 128s | 128s |
| create | vpn-gateway | 1 | 10s | 10s | 10s |
| create | volume | 15 | 6s | 14s | 18s |
| create | cloud-function | 3 | 6s | 7s | 7s |
| create | direct-connect | 4 | 6s | 7s | 7s |
| create | secret | 1 | 3s | 3s | 3s |
| create | kms | 7 | 1s | 1s | 1s |
| create | lb-listener | 1 | 1s | 1s | 1s |
| create | certificate | 1 | 0s | 0s | 0s |
| create | lb-health-check | 1 | 0s | 0s | 0s |
| create | lb-server-group | 1 | 0s | 0s | 0s |
| delete | vpc-peering | 1 | 470s | 470s | 470s |
| delete | nodepool | 2 | 382s | 384s | 384s |
| delete | private-dns | 3 | 254s | 256s | 256s |
| delete | mariadb | 1 | 201s | 201s | 201s |
| delete | mysql | 1 | 184s | 184s | 184s |
| delete | subnet | 50 | 135s | 273s | 506s |
| delete | privatelink-endpoint | 2 | 58s | 59s | 59s |
| delete | privatelink-service | 1 | 58s | 58s | 58s |
| delete | transit-gateway | 4 | 56s | 66s | 66s |
| delete | loadbalancer | 1 | 28s | 28s | 28s |
| delete | vpc-endpoint | 1 | 20s | 20s | 20s |
| delete | vpn-tunnel | 1 | 18s | 18s | 18s |
| delete | internet-gateway | 7 | 17s | 22s | 22s |
| delete | gslb | 1 | 17s | 17s | 17s |
| delete | nat-gateway | 2 | 13s | 13s | 13s |
| delete | virtual-server | 9 | 12s | 26s | 26s |
| delete | private-nat | 1 | 10s | 10s | 10s |
| delete | direct-connect | 4 | 6s | 12s | 12s |
| delete | lb-server-group | 1 | 5s | 5s | 5s |
| delete | api | 1 | 4s | 4s | 4s |
| delete | log-group | 10 | 4s | 5s | 5s |
| delete | volume | 18 | 3s | 5s | 16s |
| delete | policy | 4 | 2s | 2s | 2s |
| delete | lb-listener | 1 | 2s | 2s | 2s |
| delete | cloud-function | 3 | 2s | 3s | 3s |
| delete | certificate | 1 | 1s | 1s | 1s |
| delete | vpn-gateway | 1 | 1s | 1s | 1s |
| delete | group | 3 | 0s | 1s | 1s |
| delete | lb-health-check | 1 | 0s | 0s | 0s |
| delete | snapshot | 1 | 0s | 0s | 0s |
| endpoint.create | privatelink-service | 1 | 24s | 24s | 24s |
| endpoint.delete | privatelink-service | 1 | 58s | 58s | 58s |
| healthcheck.update | gslb | 1 | 21s | 21s | 21s |
| interface.change | virtual-server | 3 | 9s | 10s | 10s |
| log-event.delete-bulk | log-group | 1 | 3s | 3s | 3s |
| maintenance.delete | mysql | 1 | 5s | 5s | 5s |
| maintenance.delete | mariadb | 1 | 4s | 4s | 4s |
| maintenance.modify | mysql | 1 | 6s | 6s | 6s |
| maintenance.modify | mariadb | 1 | 4s | 4s | 4s |
| modify | cloud-function | 2 | 25s | 45s | 45s |
| password.update | user | 1 | 0s | 0s | 0s |
| power_off | virtual-server | 3 | 10s | 11s | 11s |
| power_on | virtual-server | 3 | 26s | 27s | 27s |
| reboot | virtual-server | 3 | 22s | 23s | 23s |
| relationship.create | volume | 1 | 2s | 2s | 2s |
| remove-member | group | 1 | 0s | 0s | 0s |
| resize | volume | 3 | 1s | 1s | 1s |
| resources.status.update | gslb | 1 | 1s | 1s | 1s |
| resources.update | gslb | 1 | 21s | 21s | 21s |
| restart | mysql | 1 | 108s | 108s | 108s |
| revert | volume | 1 | 3s | 3s | 3s |
| rule.create | vpc-peering | 1 | 38s | 38s | 38s |
| rule.delete | vpc-peering | 1 | 83s | 83s | 83s |
| set | volume | 1 | 0s | 0s | 0s |
| set.qos | volume | 10 | 24s | 79s | 79s |
| stop | mysql | 1 | 173s | 173s | 173s |
| sync | mysql | 1 | 17s | 17s | 17s |
| transfer.create | volume | 1 | 1s | 1s | 1s |
| update | private-dns | 1 | 13s | 13s | 13s |
| update | vpn-tunnel | 1 | 12s | 12s | 12s |
| update | volume | 4 | 4s | 9s | 9s |
| update | group | 2 | 0s | 0s | 0s |
| update | policy | 1 | 0s | 0s | 0s |
| volume.attach | virtual-server | 3 | 36s | 79s | 79s |
| volume.detach | virtual-server | 3 | 9s | 12s | 12s |
| vpc-connection.create | transit-gateway | 1 | 551s | 551s | 551s |
| vpc-connection.delete | transit-gateway | 1 | 279s | 279s | 279s |
