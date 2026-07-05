---
status: ACTIVE campaign (2026-07-04, owner-directed autonomous run)
for: orchestrator + all campaign agents (다른 세션이 이어받을 때 이 문서가 진입점)
---

# CAMPAIGN — C3 100% · 플랫폼 dogfood 개선 · 리포 정비 (3 워크스트림 병렬)

> 오너 지시 (2026-07-04): ① C3 커버리지 100% (수단 자율 — SCP 문서, 축적 지식,
> 서비스별 병렬), ② 테스트는 **플랫폼 자체 기능으로** 실행하며 (VPC cap 전략 필요)
> 발견되는 결함/불편을 플랫폼 개선에 반영 (테스터+개선자 페르소나), ③ 문서·소스
> 정비 — 1차: 일관성 + 미사용 정리, 2차: 내용 정비 (과상세→목적 중심 추상화로 AI
> 자율성 확보, 부족한 곳은 상세 보강). 최대 병렬, 오케스트레이터가 조율, 방법론을
> 문서로 남길 것.

## 조율 규칙 (오케스트레이터 계약 — 세션 불문 유지)

1. **라이브 레인은 하나** — 로컬(플랫폼 콘솔) 또는 CI(chat-heavy) 중 한 시점에 한
   run. 디스패치 게이트: owned==0 + ~5min audit 정적 (+ 이전 run 스윕 종결).
2. **fold 직후 owned==0 재검증** (2026-07-04 규칙 — leak 1일 방치 재발 방지).
3. **VPC cap 5**: shared-adopt 1 + 자체생성 lifecycle 최악 조합 ≤ cap.
   heavy DB/VM 계열은 클러스터/서버 quota도 감안 — 배치당 병렬 4 이하 권장.
4. **증거 원칙**: 2xx만 verified (`tools/derive_verified`) → 자동 승격
   (`tools/promote_validated --apply`, service-스코프). 4xx 도달은 soft — 승격 불가.
5. **계정상 불가는 쫓지 말 것**: gated 34 노드 + 표준 waiver 절차 (아래 D). C3
   100% = "달성가능 전부 2xx + 나머지는 심사된 waiver"로 정의 (분모 조작 금지).
6. **에이전트 파일 규율**: 명시 경로 커밋 (`git add -A` 금지), push 전
   `pull --rebase --autostash`, .github/ 은 오케스트레이터 전용.
7. **워크스트림 간 파일 경계**: A=regression/scenarios/lifecycles/<service별 fragment>
   + knowledge/formal/resources/<service별> + data/baselines. B=console2/ +
   controlplane/ + tools/console2_server. C=docs/ (+ 소스 retire는 목록만 제안,
   삭제 실행은 오케스트레이터 승인 후). knowledge/validated-facts.md 와 CONTEXT.md
   는 append-only 충돌 규칙 (rebase 로 해소).

## 워크스트림 A — C3 100% (커버리지)

- **A0 기준선**: ✅ DONE 2026-07-04 — 아래 §A 레저. 엔드포인트별 분류(344건)는
  `docs/working/plans/CAMPAIGN-C3-100-A0-gaps.json` (A1..An 입력).
- **A1..An 서비스 에이전트**: coverage-service 표준 패턴 (독립 fragment 저작,
  compose→validate→오케스트레이터에 디스패치 블록 제출; 직접 디스패치 금지).
- 실행: L 배치는 플랫폼 콘솔로 (B 가 관찰), H 배치는 chat-heavy 로. fold →
  promote → 레저 갱신 매 배치.

## §A 레저 — A0 실측 기준선 (2026-07-04)

### C3 기준선 (dashboard/build.py 공식: `C3 = ((verified-2xx − excluded_waivers) ∪ reach_covered) / (1372 − excluded_waivers)`)

| 증거 기반 | verified-2xx | C3 | 비고 |
|---|---|---|---|
| repo `data/baselines/verified_endpoints.json` (1518키; lifecycle:step → service-스코프 해석 944건) | 596 | 742/1264 = **58.7%** | derive_verified 누적분만 |
| published `dashboard-data:verified_endpoints.json` (2026-06-26) | 746 | 890/1264 = **70.4%** | 대시보드 누적 |
| **UNION (캠페인 기준선)** | **776** | **920/1264 = 72.78%** | reach_covered 149/153 |

- Waiver 261 = **excluded 108** (blast-radius 24 + entitlement 14 + unsatisfiable 6 + billing 64; 분모 제외) + **reachability 153** (touched=covered; 미터치 4 = cloudcontrol landing-zone 4종 → L 터치 배치).
- **증거 저장소 불일치 (조치 필요)**: dashboard 누적에는 있으나 repo 파일에 없는 검증 키 **180개** (관측 fold 누락), 반대 방향 30개 (6/26 이후 미발행). → 다음 fold 때 양방향 백필 권장 (promote_validated 정확성에 직결).
- 남은 갭 = **344 엔드포인트** (미검증 & 미waive; 미터치 reach-waiver 4 포함).

### 갭 분류 (L=light/console · H=heavy/chat-heavy · W=window/동승 · G=gated/waiver) — 상세: `CAMPAIGN-C3-100-A0-gaps.json`

**총계: L 14 · H 197 · W 16 · G 117 (=344)**

| 서비스 | 갭 | L | H | W | G | 핵심 레버 / 게이트 |
|---|---|---|---|---|---|---|
| networking/vpc | 26 | 1 | 17 | 8 | 0 | TGW/endpoint/peering 라이프사이클; private-nat 7은 DC 동승 |
| database/epas | 23 | 0 | 19 | 0 | 4 | 실클러스터+subops-guarded; 4건 PF-500 |
| compute/virtualserver | 21 | 0 | 21 | 0 | 0 | VS full run + image/interface ops (createimage/importimage body 미검증) |
| container/scr | 20 | 0 | 0 | 0 | 20 | image/tags 19 = docker-push 전용; createregistry quota 1EA 점유 |
| management/iam | 19 | 0 | 11 | 0 | 8 | iam-credentials-heavy (VPC 불요); createrole/accesskeycreate 500 PF |
| data-analytics/eventstreams | 18 | 0 | 18 | 0 | 0 | 실 Kafka 클러스터 (create body 미검증) |
| storage/backup | 17 | 1 | 16 | 0 | 0 | 실 VM+agent 필요 (HB3 동반); createbackup FILESYSTEM 시도 |
| database/mysql | 15 | 0 | 14 | 0 | 1 | 실클러스터+subops |
| database/cachestore | 14 | 0 | 14 | 0 | 0 | 실클러스터+subops (remove-backup-histories 401 quirk) |
| database/mariadb | 14 | 0 | 13 | 0 | 1 | 실클러스터+subops |
| database/postgresql | 13 | 0 | 0 | 0 | 13 | createcluster 500 PF — 전체 cascade 차단 |
| data-analytics/data-flow | 12 | 0 | 10 | 2 | 0 | NiFi heavy; SKE-종속 2건은 HB6 동승; create body 미검증 |
| data-analytics/data-ops | 12 | 0 | 10 | 0 | 2 | Airflow heavy; **createdataopsservice body 미상 (docs 조사)**; 2건 403 |
| management/organization | 12 | 0 | 0 | 0 | 12 | member 계정 (org-master 아님) — entitlement |
| networking/loadbalancer | 11 | 0 | 10 | 1 | 0 | LB members-nat run; cert는 selfsign 사전단계 |
| ai-ml/cloud-ml | 9 | 0 | 0 | 0 | 9 | 제품 미구독 (404 라우팅) — entitlement |
| data-analytics/quick-query | 9 | 0 | 0 | 0 | 9 | create/validate 500 PF — cascade 차단 |
| networking/dns | 9 | 0 | 4 | 0 | 5 | private hosted-zone run; public-domain 5 = 유료 등록 |
| networking/vpn | 8 | 0 | 8 | 0 | 0 | GW+tunnel (VPC+publicip); IKE/IPSec enum 미검증 |
| ai-ml/aimlops-platform | 6 | 0 | 4 | 2 | 0 | gen-heavy-aimlops (~48m); internal 2건 release 설치 후 동승 |
| application-service/apigateway | 6 | 0 | 0 | 0 | 6 | PL entitlement-403 ×5 + PF-23 create-500 |
| compute/scf | 5 | 2 | 0 | 1 | 2 | logs/metrics time 파라미터(L); codefile은 OBS jar 필요(W); PL approve/connect PF |
| financial-management/billingplan | 5 | 0 | 0 | 0 | 5 | 유료 약정 4 + listinstances 500 PF |
| networking/cdn | 5 | 5 | 0 | 0 | 0 | 기존 ACTIVE CDN 대상 writes — 콘솔 L 배치 |
| networking/direct-connect | 5 | 0 | 5 | 0 | 0 | create 시도 (entitlement 미확인 — 실패 시 G 전환) |
| security/configinspection | 5 | 0 | 0 | 0 | 5 | 피검사 계정 auth_key_id 필요 — entitlement |
| devops-tools/devopsservice | 4 | 0 | 0 | 0 | 4 | admin-user-service 미활성 409 — entitlement |
| management/cloudcontrol | 4 | 4 | 0 | 0 | 0 | reachability-waived 미터치 — C2 터치만 (안전: 403 벽) |
| platform/sts | 3 | 0 | 0 | 0 | 3 | createrole PF + SAML 부재로 체인 차단 |
| storage/filestorage | 3 | 0 | 0 | 1 | 2 | setaccessrule은 HB3 VM 동승; replication 2건 DR-측 전용 |
| storage/parallel-filestorage | 3 | 0 | 3 | 0 | 0 | 1TB 볼륨 (billable) |
| security/certificatemanager | 2 | 0 | 0 | 0 | 2 | 실 CA 서명 cert 필요 — unsatisfiable |
| container/ske | 1 | 0 | 0 | 1 | 0 | kubeconfig — HB6 클러스터 동승 |
| quota · servicewatch · kms · secretsmanager · secretvault | 5 | 1 | 0 | 0 | 4 | sw custom-metrics(L); 나머지 구조적 차단 |

### 슬롯 스케줄 (배치 ≤4 lifecycle · VPC cap 5 = shared-adopt 1 + 자체생성 ≤4 · 라이브 레인 1개; est는 durations.json 기반)

| 배치 | 레인 | lifecycles (≤4) | VPC | 커버 | est |
|---|---|---|---|---|---|
| **LB1** | 콘솔(L) | networking-cdn-service · cloudcontrol-landing-zone-guarded(터치) · servicewatch custom-metrics · scoped smoke(scf time-param + backup dup-param) | 0 | L 13 | ~25m |
| **LB2** | 콘솔(L) | vpc-subnet-vip-nat (+publicip 사전생성 → deletesubnetvipnatip) | 1 | L 1 | ~15m |
| **HB1** | chat-heavy | database-mysql-cluster+subops · mariadb cluster+subops | shared 1 | H 27 | ~75m |
| **HB2** | chat-heavy | epas · cachestore · eventstreams (실클러스터 3 + subops; 클러스터 병렬≤4) | shared 1 | H 51 | ~90m |
| **HB3** | chat-heavy | compute-virtualserver-full · vs-image-write-coverage · gen-heavy-backup(VM 동승) | 1+shared | H 53 + W 3 (fs setaccessrule, vpc vipport, scf codefile*) | ~60m |
| **HB4** | chat-heavy | networking-loadbalancer-members-nat · networking-vpn-gateway-tunnel · vpc-transit-gateway-children · vpc-endpoint | 3+shared | H 31 + W 1 (lb cert selfsign) | ~40m |
| **HB5** | chat-heavy | vpc-peering(VPC 2, free≥3 확인) · networking-direct-connect-routing · vpc-private-nat(DC 동승) · networking-dns-hosted-zone-private(+private-dns 1/3) | 3+shared | H 13 + W 7 | ~45m |
| **HB6** | chat-heavy | container-ske-cluster-nodepool · gen-heavy-aimlops | 1+shared | H 4 + W 5 (ske kubeconfig, aimlops internal×2, data-flow SKE-종속×2) | ~75m |
| **HB7** | chat-heavy | data-flow-service-and-flow-guarded · data-ops(**docs 조사 후**) · parallel-filestorage-capacity-restore | shared 1 | H 23 | ~60m |
| **HB8** | chat-heavy | iam-credentials-heavy | 0 | H 11 | ~10m |

합계 벽시간 ≈ L 40m + H 6.5–7.5h (단일 레인 직렬; HB1↔HB2는 DB 클러스터 quota로 병합 금지). L+H+W 전부 2xx 시 C3 ≈ 90.7% → **+ waiver 승인 시 100%** (분모 조작 없음). 선택 특수 레인: **SCR docker-push** (러너 docker + registry 자격 필요) 승인 시 G 19 → H 전환.

### Waiver 제안 (G 117 — 사람 승인 필요; 클래스는 기존 컨벤션)

| 클래스 | 건수 | 대상 |
|---|---|---|
| entitlement (분모 제외) | 39 | organization 12 · cloud-ml 9 · apigateway-PL 5 · configinspection 5 · devopsservice 4 · iam user-policy-binding 2 · data-ops 403 2 |
| unsatisfiable-flow (분모 제외) | 26 | scr docker-push 19(docker 레인 승인 시 철회) · certificatemanager 2 · filestorage DR-측 2 · quota 1 · kms managed-key 1 · secretvault temporarykey 1 |
| billing-prohibitive (분모 제외) | 9 | dns public-domain 5 · billingplan 약정 4 |
| reachability (touched=covered 유지) | 43 | postgresql 13(PF createcluster-500) · quick-query 9(PF) · iam 6(PF 500+cascade) · epas 4(PF) · sts 3(PF-체인) · scf-PL 2(PF) · mysql/mariadb 각 1(PF) · apigw create-PL 1(PF-23) · billingplan listinstances 1(PF) · secretsmanager kms-key 1 · scr createregistry 1(quota) |

주: reachability 제안분은 PF(제품버그) 수리 시 waiver 철회 + 실 2xx 재도전이 원칙. 엔드포인트별 근거는 `CAMPAIGN-C3-100-A0-gaps.json`의 `note` 필드.

### SCP docs 조사 필요 (request body 미상 — 다음 에이전트 입력; **body 창작 금지**)

`data-ops createdataopsservice(service_workload)` · `data-flow createdataflow/createdataflowserviceconsole` · `eventstreams createcluster` · `vpn phase1/phase2 enum` · `virtualserver createimage/importimage` · `backup createbackup(FILESYSTEM)` · `dns activateprivatedns`

## 워크스트림 B — dogfood 테스트 + 플랫폼 개선

- A 의 L 배치를 **콘솔 UI로 실행** (Playwright 페르소나 or API+UI 혼합), 매 run
  마다 마찰 일지 → 트래커(UIUX-AUDIT) 추가 → 소배치 수정 → main 반영 반복.
- 렌즈: default 적절성 · 0클릭 가시성 · 실시간 문제 인지 · 속도 체감(느림이
  설명되는가) · 라벨 오해 소지 (오너 2026-07-04 지정 5렌즈).

## 워크스트림 C — 리포 정비

- **C1 (1차)**: 전수 인벤토리 → (a) 죽은 문서/소스 retire 후보 목록 (증거:
  참조 0 + 최근 미변경 + 기능 대체됨), (b) 문서 간 모순 목록 (정본 우선),
  (c) INDEX/진입점 정합. 삭제는 오케스트레이터 승인 후 별도 커밋.
  - **C1 감사 DONE (2026-07-04)** →
    `docs/working/trackers/REPO-AUDIT-2026-07-04.md`: retire 후보 9묶음
    (핵심: poc/scenario-viz — 단 api-test.yml:1121 발행 스텝이 아직 소비, 교체
    선행 필요 · console_server.py · build_local_demo+local_run.html · 1회성
    publish *.sh · tracked reports/ 산출물 10파일), 모순 14건 중 10건 직접 수정
    (게이트 기본값 표 README/START_HERE/CONTEXT, README 트리거 절, controlplane
    README dispatch 주의, skills README 2건, 구 핸드오프 4+2건 supersede 헤더,
    INDEX 재생성). C2 목표 트리 표 = 감사 문서 §4. **삭제 실행 대기: §2 승인.**
- **C2 (2차)**: 내용 정비 — 과상세 문서를 "목적+계약+포인터"로 추상화 (AI 가
  자율 판단할 여지 확보), 부족한 곳 보강 (예: 운영 runbook 류). 정본 체계:
  PLATFORM-IA-DIRECTION(§확정 IA) > CONTEXT.md(현재 상태) > knowledge/(사실) >
  트래커/플랜(작업).

## 진행 로그 (오케스트레이터가 갱신)

- 2026-07-04: 캠페인 개시. A0 + C1 병렬 가동 (라이브 레인은 페르소나 2차 점유 중).
- 2026-07-04: C1 감사 완료(464b4acf) → 저위험 retire 승인·집행: console_server.py ·
  build_local_demo+local_run.html · 1회성 publish *.sh 4종 · tracked reports/ 산출물
  9파일 untrack. **poc/scenario-viz(155f)는 보류** — api-test.yml:1121 발행 스텝 소비
  + Pages /platform 사용 여부 오너 확인 필요.
- 2026-07-04: A0 후속 — dashboard-data 증거 백필 커밋(1bfe4f0d): repo 기준선에
  +734 키 union(총 2252) + 10노드 VALIDATED 승격(모델 165 VALIDATED / 110 docs /
  34 gated). **C3 재계산 72.78% 불변** — A0 기준선이 이미 dashboard-data 증거를
  union했으므로 백필은 내구성(repo 단독 자급) 확보용, 신규 커버리지 아님.
  gap 344(L14/H197/W16/G117) 유효. docs-research 에이전트(§body 미상 7건) 가동.
- 2026-07-04: docs-research 완료(ef6e061f → `CAMPAIGN-C3-100-docs-research.md`):
  확보 4(data-flow·vpn·vs-image·dns) / 부분 2(data-ops — `GET
  /v1/data-ops/image-versions` 선조회 리드, eventstreams — topology 미검증) /
  차단 1(backup FILESYSTEM — Agent waiver). 적용(90396a7a): vpn phase1/2 공식
  예시값 교체 + importimage `source`→`url`. HB4/HB7에서 실검증 예정.
- 2026-07-04: 페르소나-2 저널 접수 → 판정·신규 마찰 13건을 UIUX-AUDIT §6
  (P2C-1..13)으로 이관. 치명 3(pre-flight 우회 · 재스캔 0건 오보 · 로컬 중단
  부재) 포함 fix-batch 가동 (완료 시 main 반영). C2 완료(10커밋, README 추상화
  ·ARCHITECTURE 병합·supersede 12+건·runbook 보강·INDEX 재생성).
- 2026-07-04 07:28Z: **HB1 디스패치**(f99300c9, run 28699129653) — mysql+mariadb
  cluster-subops-full, parallel=2, shared VPC 1 adopt 확인. live-watcher 감시 중
  (13분 주기, stall/leak/실패폭주 임계). 종료 후: fetch-results 브리지 →
  derive_verified → promote → HB2.
- 2026-07-04 08:10Z **HB1 종결 — 신규 커버 0 (전략 교훈)**: 42m success, 88 obs
  (ok 64 · soft 18 · fail 6). mariadb 풀체인 정상 완주했으나 2xx는 전부 D2–D7
  기검증 키(+0 new); 갭 10키는 **전과 동일 서명으로 재실패**(log-export 3종
  InvalidLogType · set-parameters 500 PF · remove-backup-histories 401 AuthN ·
  patch-minor-version/set-server-type/set-block-storage validation ·
  show-request 400). mysql은 create 500 `ContactAdminForAssistance`(PF, pg-13과
  동류)로 체인 전체 스킵 — untried 12키 잔존. **교훈: H 갭 197 중 78은
  결정적 재실패(재실행 무의미 — 엔드포인트별 body/enum/선조회/pacing 수리 필요,
  일부 PF→waiver), 119는 미시도/404(부모 실존 시 업사이드 — HB 배치 유효).**
  HB2 대상 51 = untried 37 + det 14 → 디스패치 가치 유지. mariadb 잔갭·mysql
  create 재시도는 수리 후 HB1b로.
- 2026-07-04 23:00–23:31Z **HB2 종결 — 신규 커버 0**: run 28722435523 success
  30.5m. cachestore 풀체인 완주(2xx 전부 D6 기검증), **epas는 create 500
  `ContactAdminForAssistance`(PF — mysql·pg-13과 동일 클래스)로 체인 literal-404
  강등**(live-watcher가 "epas 무활동"으로 본 것은 감사이벤트 부재 때문 — 실은
  실행됨). 신규 결정적 서명 4건(cachestore): set-commands maxmemory-policy 값 ·
  switchover value_error · stop/start InvalidState · resize-block-storage
  InvalidBlockStorageRoleType — repair-log 후속 대상. **결론: DB 계열 잔갭은
  PF(waiver 후보: epas·mysql create-500 계열 확대) + 값버그(3f139795 일부 수리)
  뿐 — DB 재실행은 수리분 검증용 HB1b/HB2b로 축소, 슬롯은 비DB 서비스 우선.**
  플랫폼 결함 발견: chat-heavy 아티팩트의 junit-crud.xml이 stale(HB1·HB2 동일
  1063B, 무관 lifecycle 목록) — observations만 정본, junit 생성 경로 수리 백로그.
- 2026-07-04 **HB3 디스패치 준비**: gen-heavy-backup enabled:true 전환(자족형
  VM 클로저 — heavy 게이트 이중 잠금 유지; -restore는 leak-unsafe로 계속 비활성).
  구성: compute-virtualserver-full + vs-image-write-coverage + gen-heavy-backup,
  parallel=3, VPC shared 1 + backup 자체 1 = 2/5.
- 2026-07-05 00:19Z **HB3 종결 — 신규 커버 0 (3연속)**: run 28723287734 success
  ~41m, 110 obs (ok 82 · soft 28 · fail 0), teardown 완전 수렴(잔존 volume 1은
  TTL 보호로 말미 스윕이 스킵 → 오케스트레이터가 IGNORE_TTL 강제 스윕으로 회수).
  진단(job log): ① gen-heavy-backup은 VM 클로저 완주 후
  `create-backup-target 200 → $.contents[0].server_uuid 캡처 실패`로 중단 —
  서버 ACTIVE 직후 backup-target 인벤토리 랙 추정, **조회 settle-poll 필요**;
  ② delete-server 400 `DeleteImpossible` 3번째 재현 — 직후 리컨실러는 삭제
  성공 → 백엔드 하드블록 아닌 **상태 settle 타이밍**, pre-delete 대기 필요;
  ③ create-port 400 fixed_ip 포맷 → port 체인 6키 404 강등; ④ image-update
  400 InvalidVolumeOnMinDiskUpdate; ⑤ create-image 400 InvalidObjectStorageUrl
  (실 qcow2 업로드 시 2xx 전환 가능 — 소액, HB3b 검토). 수리분은 HB3b로.
