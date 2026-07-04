---
status: superseded (historical handoff — current state lives in docs/working/CONTEXT.md)
for: all
superseded_by: ../CONTEXT.md
---

# SESSION HANDOFF — 2026-06-17 ~01:00 UTC

다른 세션이 이어받기 위한 현재 상태 스냅샷. (orchestrator 역할 = Coordinator;
자율 루프는 docs/agent-team.md "자율 운영 루프" 참조.)
*(이 위 블록은 2026-06-17 세션 갱신분. 29행 이하 LIVE-PROVEN/모델교훈/백로그는
역사적 참조용 — 여전히 유효하나 날짜는 6-14 기준.)*

## 한 줄 요약
**C1(도달가능) 정적 커버리지 100% 달성 — coverage_gap 1372/1372, GAP 0.** 남은 15개
id-bound GET을 vpc/kms/loggingaudit/cloudmonitoring로 폐쇄. C3(실제 2xx)는 별개
축으로 ~47%, heavy 라이브 런으로 승격 중. 진행 중: **Wave I 헤비 런**(run
27658124753) — Wave H 12-fail 수정(①②③) 재검증 + SCR docker-login 실험.

## git (전부 푸시됨, 트리 clean)
- HEAD == origin/main == origin/claude/work-process-discussion-goub6k == **1bfefc2**.
- 작업 브랜치: `claude/work-process-discussion-goub6k`. 디스패치 시 이 브랜치를
  main으로 fast-forward(`git push origin HEAD:main`) → run-request 트리거.
- 발행: dashboard-data 브랜치(워크플로 clone+rebase). 런 결과: oplog 버킷
  apitest-oplog-permanent (index.json + runs/<id>/).

## 이번 세션(6-17) 완료분 (커밋 dd33ca8..1bfefc2)
- **C1 100%**: cloudmonitoring(EOL, entitlement waiver 유지) / vpc 8 / kms 1 /
  loggingaudit 1 id-bound GET 폐쇄 (dd33ca8, 73d290c).
- **대시보드 수정**: pfs + iam-identity-center를 untestable_services.json에 추가
  (reachability-only 배지 누락 수정, bcd2e5f); ops 대시보드 cluster 그룹핑을
  서비스별로 분리(SKE↔DBaaS 안 섞이게, f76941a).
- **Wave H 12-fail 수정 (9d5b591, 17af1f8)**:
  - ② `core/http_client.py`: POST/PATCH는 timeout/conn 에러 시 **재시도 안 함**
    (느린 SKE create→타임아웃→재시도→409 중복+고아 근본 원인). + 회귀 테스트
    `tests/offline/test_http_retry.py` (6 pass).
  - ① `engine.py`: capture 없는 verify 스텝의 `cidr-already-in-use`(공유 VPC
    멱등 재추가)를 **성공 처리**.
  - ③ 6개 격리 write 스텝(generated__wave2/wave4/wave5-net/wave5-appsvc/
    heavy-dbaas/heavy-vs): asg-schedule start_date→YYYY-MM-DD(2xx), 나머지 5개
    tolerant+optional.

## 지금 진행 중 (IN FLIGHT) — Wave I, run 27658124753 (commit 1bfefc2)
run-request: heavy + mutations + destructive + **docker_probe=true**.
- spec ✅. **VPC-CRUD 잡 ❌** — 단 환경적 실패(내 수정 무관): Wave H sweep이 30분
  time-box로 cancelled→leftover VPC가 5-VPC 상한 점유→`delete-port`가 이미 사라진
  포트에 404 + vpc-peering/heavy-shared-networking가 exceed-max-count로 skip.
- **regression-A 잡(smoke+ADOPT CRUD+docker_probe) 🟡 진행 중** — pre-reclaim이
  leftover 정리 → 여기가 ①②③의 진짜 검증.
- **이어받기 절차**:
  1. run 27658124753의 jobs를 `list_workflow_jobs`로 확인. regression-A의
     "Run ADOPT-class CRUD" 결과(green 기대 — Wave H의 12 fail이 0이어야).
  2. 같은 잡 로그에서 **`SCR-DOCKER-PROBE:` 판정** 읽기 — PUSH-OK면 SCP 키가
     레지스트리 인증 = cloud-ml/SCR scr-auth-key 게이트 제거 가능(모델
     ai-ml__cloud-ml.yaml + container__scr.yaml의 credential requires 해제).
  3. **승격 확인**: Wave H에서 SKE 409로 막혔던 SKE→cloud-ml→aimlops가 이번엔
     생성·승격됐는지. dashboard-data의 verified_endpoints / C3 수치 before(≈47%)
     대비 확인. held 노드 provenance docs→VALIDATED 승격.
  4. sweep 완료(oplog runs/<id>/events/*sweep.json 또는 jobs 상태)까지 기다린 뒤
     다음 run-request 푸시 (시퀀싱 규칙).

## 다음 세션 TODO (우선순위)
1. **Wave I 결과 triage** (위 이어받기 1~3). green이면 held 노드 승격 커밋.
2. **delete-port DELETE→404 관용 (미구현, 제안만 됨)**: engine.py에 "DELETE가
   404=이미 삭제됨=성공" 멱등 규칙 추가. Wave I VPC-CRUD fail의 정당한 수정.
   다음 wave 전에 넣을 것. (Wave I 진행 중이라 이번 세션엔 미적용)
3. **sweep time-box 한계**: Wave H/Wave G에서 sweep이 30분에 잘림 → leftover가
   다음 런 5-VPC 상한 유발. reclaim/sweep 시간 상향 또는 leftover 우선순위 검토.
4. **docker_probe 후속**: PUSH-OK면 cloud-ml 9 + SCR 게이트 해제 → 재모델/검증.
   LOGIN-FAILED면 owner에게 SCR 콘솔 키 요청.
5. **③ R3 항목**: lb-health-check(LB 선행), privatelink(in-CIDR IP 계산),
   custom-image(server 상태), mariadb-archive(RUNNING 재폴링)을 tolerant→2xx로.
6. **C3 천장 ~85%**: heavy 런 반복으로 VPC 의존 376개 승격(앞선 분석 참조).

## run-request 시퀀싱 규칙 (필수)
이전 런(**sweep 잡 포함**) 끝나기 전 .github/run-request 푸시 금지(owner
2026-06-10). 동시 런은 5-VPC 계정 상한에서 충돌. concurrency는 queue(cancel-in-
progress:false)지만 규칙상 대기. 디스패치 = feature를 main으로 ff-push.

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
