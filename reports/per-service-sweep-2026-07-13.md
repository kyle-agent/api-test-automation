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

**진행: 15/56 완료** — ✅4 · ⚠️9 · ❌2 · (teardown 미복귀 잔존: 0)

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

<!--PROGRESS-END-->
