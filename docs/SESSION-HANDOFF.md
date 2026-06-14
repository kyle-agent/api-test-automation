# SESSION HANDOFF — 2026-06-14 08:20 UTC

다른 세션이 이어받기 위한 현재 상태 스냅샷. (orchestrator 역할 = Coordinator;
자율 루프는 agents/orchestrator.md "자율 운영 루프" 참조.)

## 한 줄 요약
SCP API 회귀 자동화 플랫폼을 agent가 자율로 구축 중. M6 플랫폼 8티켓 완료,
task 245노드/59서비스, **C3 50.67%** (세션 시작 44.79), DBaaS replica 4형제
(PG/epas/mysql/mariadb) LIVE-PROVEN. 진행 중: **SKE upgrade rev3**.

## git
- HEAD == origin/main (워킹트리 깨끗, 미푸시 없음). 모든 완료분 커밋·푸시됨.
- 발행: dashboard-data 브랜치(워크플로가 clone+rebase로 발행 — T7/IB-001 수정됨).
- 런 결과: oplog 버킷 apitest-oplog-permanent (index.json + runs/<id>/).

## 지금 진행 중 (IN FLIGHT)
- **SKE upgrade rev3** (run-request e41745f, gen-heavy-ske-upgrade):
  v1.33.5 클러스터+노드풀 → v1.34.3 컨트롤플레인 업그레이드 → 노드롤 →
  kubeconfig×2. baseline dashboard-data = 3bcf2a3 (이 sha에서 바뀌면 발행됨).
  - rev1/rev2는 첫 lookup create-ske-image에서 빠르게 실패(필수 쿼리
    누락) → rev3에서 `scp_original_image_type=k8s&size=20&page=0`로 수정.
  - **이어받기**: dashboard-data sha가 3bcf2a3에서 바뀌면 발행 완료. 
    `failed_only` 로그로 gen-heavy-ske-upgrade 결과 확인 → 그린이면
    ske-cluster-upgrade/ske-nodepool-upgrade 노드 provenance를 docs→VALIDATED
    승격, 실패면 본문으로 모델 수정 후 sweep 대기 → 재디스패치.
  - run-request 시퀀싱 규칙: 이전 런(sweep 포함) 끝나기 전 새 run-request
    푸시 금지. sweep 완료는 oplog runs/<id>/events/*sweep.json 으로 확인.

## LIVE-PROVEN (이번 세션)
- DBaaS replica family ×4: PG/epas/mysql/mariadb (create→sync-replica-state→
  reset-replica→promote→teardown). epas는 클러스터 create 자체가 첫 증명.
- DBaaS cluster spine + 설정 setter(sg-rules/archive/backup/maintenance/sync).
- VS netops 확장: server post-create ops(lock/unlock/dump/password[200,400]),
  volume attach/detach, custom-image, static-NAT(IGW 선행), server-interface.
- queue FIFO+dedup, servicewatch(alert 제외), resourcemanager SRN 태그 패밀리,
  iam bindings, apigw policy/privatelink(부분), wave3 read-only 5종.

## 핵심 모델 교훈 (replica 패턴 — 4파일 공통 적용됨)
1. DBaaS setsecuritygrouprules: 빈 본문 거부 → add_ip_addresses:["10.10.10.10/32"]
2. replica create: 클러스터 RUNNING 후 settling 필요 → retry_on_status [400,409] x12x60
3. replica block storage role_type: 클러스터=OS, replica=DATA
4. state-민감 verify(archive/maint): RUNNING 복귀까지 retry [400,409] (컴포저가
   이제 verify entry의 retry 통과 — composer.py)
5. replica 체인은 클러스터를 **타겟이 아닌 prereq로** 합성(교란 setter verify 회피)

## 비활성 체인 (사유는 IMPROVEMENT-BACKLOG IB-00x)
- restore ×4 (백업 스케줄 대기), upgrade DBaaS ×3 (owner 엔진 구버전 확인 대기),
  cloudml(SCR 인증키-PF16 결론), backup(IB-014 lookup poll-until-capture),
  privnat(IB-012 TGW connectable), vpce(IB-013 endpoint-type subnet),
  iam-role(PF-20 500), iam-saml(IB-010 multipart), swatch-alert(IB-009 중첩 capture),
  devops/mgmisc/net-endpoint/cmep(구 차단).

## 다음 후보 (Coordinator 판단)
1. SKE upgrade rev3 결과 triage (최우선, 진행 중).
2. PG upgrade 활성화 가능: owner가 PG 16.10→17.6 확인함 → gen-heavy-pg-upgrade
   enable 시도 (현재 disabled). DBaaS upgrade(mysql/mariadb/epas)는 엔진
   구버전 owner 확인 대기.
3. M6 후속 티켓: T3b(run-request compose= 디스패치 문법), T3c(peak_quota 자동분할).
4. 수작업 레거시 은퇴: tools/retirement.py 매트릭스 재실행 → 그린된 합성본이
   덮는 수작업 lifecycle 2단계 은퇴(enabled:false → 삭제). green 기준집합은
   data/baselines/green_lifecycles.json.

## owner 확인 대기 (질문)
- DBaaS mysql/mariadb/epas 엔진 구버전 유무 (upgrade 체인 활성용)
- sqlserver 라이선스, 2계정(SECOND-ACCOUNT-BACKLOG), IAM saml multipart 결정,
  custom-metric OTLP namespace 라우팅 키, backup restore 백업 스케줄 시점.

## 운영 메모
- 로그가 크면 mcp__github__get_job_logs는 파일로 저장됨 → python slice/grep.
- failed_only=true + tail 작게 → 실패 잡 id만 빠르게, 그 다음 전체 로그 grep.
- 모니터: dashboard-data sha 변화 감지로 발행 포착. 타임아웃 시 재장전.
