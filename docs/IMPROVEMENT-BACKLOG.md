# IMPROVEMENT-BACKLOG — Planner가 유지하는 개선 계획

- 소유: Planner tier (agents/orchestrator.md "자율 운영 루프" 참조).
- 갱신: 매 윈도우 머지 후 + 일 1회 스윕. 입력 = PRODUCT-FINDINGS.md,
  SERVICE-GAP-REPORTS.md, tools/retirement.py 매트릭스, dashboard/history.jsonl.
- 형식: `id | area | problem | proposed-fix | size | status`.
  - **id**: `IB-NNN`, append-only, 재사용 금지.
  - **area**: publish · debt · visualize · coverage · onboarding · platform.
  - **size**: S/M/L.
  - **status**: open · in-progress(ticket Tn) · done · waived.

## 멀티-윈도우 운영 레인 (2026-06-15, owner)

owner가 3개 Claude Code 창을 병렬로 운영: ① 기존 테스트 창 ② UI 개선 창 ③ 이
창(멀티에이전트 자율 루프). 창 간 충돌 방지 계약 — **이 창(③)의 행동 규칙**:

- **브랜치**: 이 창은 `claude/work-process-discussion-goub6k`에만 커밋을 쌓는다.
  당분간 main 통합은 보류(테스트 창이 main을 소유하며 run-request를 push 중).
- **라이브 run/디스패치는 테스트 창 전용.** 이 창은 `.github/run-request` 등 어떤
  run-트리거도 push하지 않는다(CI는 main의 run-request push에만 트리거됨 → 피처
  브랜치 push는 안전).
- **건드리지 않는 파일**: `agents/CONTEXT.md`(특히 "Current state"/run 결과 =
  테스트 창 소유), `dashboard/`·`controlplane/`(= UI 창 소유).
- **이 창의 상태 기록처**: 이 파일(BACKLOG) + `agents/coordination/ledger.json`
  자기 행(行)만. 그 외 공유 인덱스는 통합 담당이 정리.

## 백로그

| id | area | problem | proposed-fix | size | status |
|----|------|---------|--------------|------|--------|
| IB-001 | publish | dashboard-data force-push race(확정): dashboard `git push -f`가 conformance rebase-push를 덮어 드롭, concurrency 그룹 없음 | dashboard publish를 clone+rebase로 통일(M6-DESIGN §D.2 1안) + concurrency 안전벨트 | M | open (ticket T7) |
| IB-002 | debt | 태그 네임스페이스 부재 — 동시 실행 시 sweep(cleanup.reconciler)이 다른 실행 자원을 교차 삭제할 위험 | run_id별 owner+run 태그를 reconciler 필터에 강제, 동시 실행은 태그 prefix로 격리 | M | open |
| IB-003 | debt | monitor 재무장 toil — 윈도우마다 발행 감시를 수동 재설정 | controlplane `/schedules` 토글 자동화 또는 CI 후크로 재무장 | S | open |
| IB-004 | debt | 96건 "create without delete" R1 경고(lookup 노드 노이즈) | lookup 노드를 분류해 R1에서 경고 억제(또는 lookup: true 플래그) | S | done (51 lookup 노드 `lookup:true`; validate.py 가드 "lookup인데 create가 GET 아니면 ERROR"; 경고 118→67, ERROR 0) |
| IB-005 | visualize | gen_dep_map.py 출력을 ops.html DEP-MAP 마커 사이에 수동 붙여넣기 — drift 위험 | 발행 빌드 step에서 gen_dep_map.py 출력 자동 주입 | S | open |
| IB-006 | coverage | restore/upgrade 체인 게이트(위험/과금) — 다수 비활성 | owner 승인 게이트 + heavy/destructive 분리 배치로 단계 활성화 | M | open |
| IB-007 | debt | second-account backlog(docs/SECOND-ACCOUNT-BACKLOG.md) 미결 | 별도 계정 credential 발급 후 peak_quota 분할을 계정 차원으로 확장 | L | open (owner credential 대기) |
| IB-008 | coverage | SERVICE-GAP-REPORTS의 C 분류(gslb/vpn/cdn/direct-connect 라이브 미증명) | 분류별 다음 윈도우 라이브 투입(placeholder 가능분 우선) | M | open |
| IB-009 | coverage | alert 노드: dimension 필수인데 sw-metric-catalog lookup이 중첩 dimension(key/value)을 못 잡음 → gen-wave5-swatch-alert 비활성 | composer/engine에 중첩 배열 capture(`$.namespaces[0]...dimensions[0]`) 지원 추가 후 활성 | M | open |
| IB-010 | coverage | iam-saml 체인: SAML provider는 multipart/form-data 필요, 엔진은 JSON 전용 → 비활성 | 엔진에 multipart 지원 추가 vs 4 saml ops waive (owner 질문) | M | open (owner 결정 대기) |
| IB-011 | onboarding | custom-metric OTLP ingest namespace 라우팅 키 미상(SWT_CUSTOM_NAMESPACE 400) | 콘솔 agent 설정 확인 후 routing attribute 확정 (owner 질문) | S | open (owner 확인 대기) |
| IB-012 | coverage | gen-wave5-privnat 비활성: create-private-nat가 Connectable 상태의 TGW를 요구(scp-network.private-nat.connectable-transit-gateway-not-found, run 27466988779) — TGW에 VPC attachment/connection이 있어야 Connectable이 됨 | transit-gateway VPC-connection prerequisite 노드 모델링 후 private-nat 체인에 배선 → 재활성 | M | done-modeled (tgw-vpc-connection 노드 추가, LIST-poll readiness, private-nat TGW 분기를 use:transit_gateway_id로 배선; 체인 recompose. **enabled:false 유지 — 라이브 재검증은 run-dispatch 창**) |
| IB-013 | coverage | gen-wave5-vpce 비활성: create-vpc-endpoint가 전용 endpoint-type subnet을 요구(scp-network.vpc-endpoint.subnet-not-found, run 27466988779) — FS-volume resource_key 배선으로는 부족 | 올바른 type/role로 생성한 endpoint-type-subnet prerequisite 노드 모델링 후 vpc-endpoint에 배선 → 재활성 | M | done-modeled (endpoint-subnet 노드 type=VPC_ENDPOINT 추가, vpc-endpoint.subnet_id + endpoint_ip_address 재배선; 체인 recompose. **enabled:false 유지 — 라이브 재검증은 run-dispatch 창**) |

| IB-014 | debt | lookup 노드가 즉시-빈 리스트를 받으면 hard capture 실패(backup-target: 서버 ACTIVE 직후 목록 미반영, run 27483004836) — 엔진에 'capture 충족까지 GET 재폴링' 능력 부재 | engine에 lookup poll-until-capture(또는 ready 후 capture) 지원 추가 → gen-heavy-backup 재활성 | M | open |
| IB-015 | loop | 종료조건(Stop-when) 부재 — 실패 체인의 재시도 한계가 즉흥적(SKE rev3/PG rev4), 무한 두드림·매번 사람판단 위험 | 에스컬레이션 사다리(L0→L3) + 윈도우당 3 rev 한계 + 무진전 감지 + 사람-필요 6기준 명문화(orchestrator.md). 후속: 한계 수치를 composer/dispatch에 자동화(rev 카운터·history.jsonl diff 게이트) | M | in-progress (정책 codified) |
| IB-016 | loop | 직렬 운영 toil — run 결과를 기다리는 동안 도메인 작업이 멈춰 처리량 저하 | 3-레인 병렬 파이프라인(A 결과대기·B 가이드/도메인·C 합성/준비) 명문화(orchestrator.md), 공유자원 read-before-claim. 후속: 레인 B 상시가동을 세션 부트스트랩에 기본 포함 | M | in-progress (정책 codified) |
| IB-017 | coverage | sqlserver Always On Secondary(add-secondary) + Enterprise 전용 경로가 **SQL Server License Key**를 요구 — userguide에 자체 발급 절차 없음(archivestorage 전용키 미발급 선례와 동급) | owner가 라이선스 키 발급 → `ss-add-secondary` credential 주입 후 HA/secondary 체인 합성·검증. 그 전까지 해당 노드 gated | M | open (owner credential 대기) |
| IB-018 | coverage | analytics data-flow(NiFi)/data-ops(Airflow)/quick-query(Trino)는 DBaaS가 아니라 **SKE k8s 엔진 위에 설치** — 기존 create body가 dbaas instance_groups 모양(coverage probe artifact)이라 docs-vs-reality 불일치, 실제 2xx 미검증. quick-query는 추가로 DSC domain 실값 필요, data-flow/ops는 account id/pw 필요 | api_bodies.json에 실제 SKE-엔진 body 작성(ske-cluster+filestorage prereq 배선) → heavy 윈도우 라이브 검증; DSC domain/account 값은 owner 도메인 지식 주입 | L | open (UNPROVEN body + 일부 owner 도메인값) |
| IB-019 | debt | resource 모델은 정정됐으나 **소스 lifecycle JSON이 옛 발명 body 유지** — `regression/scenarios/lifecycles/financial-management__billingplan.json`이 존재하지 않는 필드(product_offering 등) + 잘못된 capture `$.contents[0].id` 사용(devopsservice도 유사 패턴 점검 필요) | 정정된 resource 모델(PlannedComputeCreateRequest)로 lifecycle JSON 재합성/수정 + capture 정정 → 모델↔lifecycle drift 해소 | S | done (billingplan: PlannedComputeCreateRequest/ChangeRequest/CancellationFeeRequest로 정정, capture `$.planned_computes[0].id`; devopsservice: DevOpsServiceCreateRequest{tenant_name,tenant_code,members}, capture `$.devops_services[0].id`. 둘 다 UNPROVEN docs-derived, write_gap 0) |
| IB-020 | coverage | data-analytics 5개 서비스(data-flow·data-ops·quick-query·searchengine·eventstreams)가 LIST capture에 `$.contents[0].id`를 가정 — resource yaml은 UNPROVEN 명시(7일+ 라이브 2xx 무증명, validated-facts.md 누락). Watcher drift-detector 2026-06-15 | data-flow 또는 data-ops 1개를 라이브 윈도우에 투입 → 실 LIST envelope 확정 후 5개 서비스 일괄 패치 → validated-facts 등록 | M | open |
| IB-021 | coverage | data-flow/data-ops resource yaml ESCALATE 미해결 — 서비스는 SKE-on-k8s(NiFi/Airflow)인데 create body가 dbaas 모양(instance_groups/dbaas_engine_version_id) 그대로. Watcher drift-detector | owner가 NiFi/Airflow 실 userguide ingest → SKE-엔진 body로 교체; IB-018와 통합 가능 | L | open (owner domain-knowledge) |
| IB-022 | debt | post-IB-004 audit — 24개 resource yaml 노드가 inbound `requires` 없고 source lifecycle도 없음(lookup 의도? orphan?). Watcher drift-detector | 24개 일괄 audit: (a) intended lookup → `lookup:true` + note, (b) orphaned → 삭제, (c) missing source → 배선. 결과를 INGESTION.md 또는 RESOURCE-MODEL-PLAN.md에 박음 | S | open |
| IB-023 | debt | 일부 disabled lifecycle의 `_disabled_reason`이 이미 DONE-MODELED 됐는데도 enabled:false 유지(IB-012/013, cm-event-policy 등) — 어떤 게 라이브 ready인지 인벤토리 부재. Watcher drift-detector | `docs/LIVE-READINESS-GATES.md` 생성: 각 disabled lifecycle에 fix_status·ready_for_live·next_window 컬럼 | S | open |
| IB-024 | coverage | IB-020와 동질 — analytics 5개 서비스의 LIST envelope이 validated-facts.md "Captured-id shapes" 표에 부재 | IB-020 라이브 윈도우에서 함께 검증·등록 | S | open (IB-020와 묶음) |
| IB-025 | debt | `dashboard/history.jsonl` 부재 — loop_cycle.py의 fail_new TREND 패널이 영구 공란, 추세 분석 불가. Track 1 platform-improver 2026-06-15 | dashboard build/publish 파이프에서 history.jsonl seed/링크; 라이브 run 후 누적 | S | open |
| IB-026 | debt | `tools/new_service.py`가 capture 봉투를 항상 `$.id`로 가정(line 175-180) — devops/analytics(`$.contents[0].id` 등)에서 깨지는데 scaffold-time 검증 없음. Track4 debt-finder | scaffold가 api_docs.json 응답 샘플을 보고 envelope-relative capture 제안 + validator가 LIST capture를 api_docs schema와 대조(non-blocking warn) | M | open |
| IB-027 | platform | **CI concurrency 충돌 위험(P0)** — `api-test.yml` concurrency group이 `${{github.run_id}}`를 포함해, 같은 ref에 run-request가 동시에 push되면 그룹이 갈려 중복 동시 run(2배 과금) 가능. 주석에 사례 기록됨(run 27520649710/650231). Track4 debt-finder | group을 `scp-api-test-${{github.ref}}`로 축소 + `cancel-in-progress` 정책 결정, 또는 feature 브랜치 run-request push 차단 게이트. **run 인프라 변경 → owner 결정 필요** | M | open (owner 결정) |
| IB-028 | debt | scaffold capture 가정과 같은 뿌리(IB-026) — 새 서비스 ~20%(analytics 5+financial+devops)가 post-scaffold 수동 교정 필요 | IB-026과 묶어 처리(validator 규칙 + scaffold envelope 힌트) | M | open (IB-026와 묶음) |
| IB-029 | debt | (debt-finder가 제안했으나 **이번 라운드 Track1이 해소**) disabled lifecycle 준비상태가 BACKLOG/`_disabled_reason`/RESOURCE-MODEL-PLAN에 흩어짐 | `docs/LIVE-READINESS-GATES.md` 생성 — 26 disabled lifecycle inventory(fix_status·ready_for_live·blocking_IB). **done(IB-023과 동일 산출)** | S | done |
| IB-030 | debt | disabled lifecycle governance — 'blocked' vs 'waiting-for-testing' 구분이 prose(RESOURCE-MODEL-PLAN §6)에만, 머신리더블 플래그 부재. Track4 debt-finder | scenarios 스키마에 `_status` enum{blocked,done-modeled,gated-ready,timing-gated,triage} 추가 + validator 강제 → 대시보드 'readiness gauge' 가능. LIVE-READINESS-GATES.md(IB-029)의 데이터화 후속 | M | **done** (Agent C: 26개 lifecycle `_status` 태깅 + scenarios.validate 비차단 경고/요약; base scenarios.json 5건은 미분류라 후속). 잔여: validator 강제(error화)·대시보드 gauge는 별도 |
| IB-031 | debt | **진짜 모델 중복 requires 6건** — sibling dep을 통해 전이로 도달되고 body에서도 안 쓰이는 dead-weight `requires`: `vpc`@server(subnet으로 도달, body subnet_id만), `vpc`@privatelink-service, `server`@backup-policy(backup-target 경유), `filestorage-volume`@data-flow-service·data-ops-service(ske-cluster 경유), `keypair`@ske-nodepool(ske-cluster 경유). Track4 전수 스캔(67노드/78엣지 중 graph-only 72 + true 6). owner도 server→vpc 직접 지적 | 해당 6개 노드 yaml에서 redundant `requires` 항목 삭제(합성 closure 불변 — 전이 경로가 여전히 끌어옴). offline-gate-only | S | **done** (Agent A: 6건 모두 body 미참조 확인 false-positive 0, R1 0err/81warn 불변, closure 불변) |
| IB-032 | visualize | 의존 그래프에 transitive reduction 부재 — N→d 엣지가 다른 직접 dep d'를 통해 도달 가능해도 그대로 그려, 노드가 조상 직속처럼 보임(owner: quick-query/server가 vpc 직속). 67노드 영향 | focus-graph 렌더러(graph.js + graph_export.py)에 transitive reduction: 표시 엣지에서 중복 엣지 숨김(requires/모델 불변), 토글 기본 ON. **done(Track1 Platform: 272 그래프 고아 0, pytest 89 passed)** | M | done |
| IB-033 | debt | 수작성 lifecycle ↔ 합성(gen-*) **중복/교체** — Track F 스캔: 189 lifecycle 중 수작성 128개, 합성 61개. 합성 노드가 덮는 수작성을 점진 retire하면 라이브 run 시간·중복 절감(최대 중복: heavy-shared-networking ↔ gen-heavy-lb-members 25ep). | **검증 게이트 필수**: F가 "VALIDATED 71개"로 분류했으나 모델 전체 91/272만 VALIDATED라 **과대평가 위험** → ① Watcher가 각 후보의 합성 노드가 *실제 VALIDATED+enabled*인지 독립 확인 ② 해당 gen-*가 최근 라이브 green인지 확인 ③ scoped 라이브 run으로 커버리지 무손실 확인 후에만 id 제거(비가역). 단계적(phase). | L | **open (검증-게이트 발동 — retire 보류 확정)**. ⚠️**Watcher(M) 재검증 결과 F의 "71개"는 과대평가**: F가 docs 노드 71개를 VALIDATED로 착각(실제 VALIDATED ~56 / docs 71). 샘플 검증 — mysql/pg cluster-subops의 합성 등가물(gen-heavy-*-replica)은 수작성의 36~40%만 커버하고 나머지 노드는 docs(미검증). **즉시 안전 retire = ~5-10개**(라이브-green 확인된 경량 체인만). heavy DBaaS 라이브 + 1:1 매핑 + 노드 VALIDATED 확인 후 phase 진행 |
| IB-034 | coverage | Cloud Monitoring **EOL 2026-09**(→ServiceWatch 이관, userguide 확인) 인데 `cloudmonitoring-event-policy` lifecycle은 still `enabled:true` — 일몰 서비스에 투자 지속. wave 중복(gen-wave-mgmisc·gen-wave2-cmep)은 이미 STALE/cruft. Track N | event-policy `enabled:false` + `_disabled_reason`에 EOL 명시; wave 중복 2개 삭제/archive; LIVE-READINESS-GATES에 EOL-gated 반영. **coverage 축소라 deliberate** | S | open |
| IB-035 | debt | Direct Connect 선행 Security Group이 cross_constraints엔 문서화됐으나 `cross-service.yaml: direct-connect.requires=[vpc]`에 미반영. Track N | requires에 security-group 추가(1줄; SG는 시나리오에서 이미 생성, 리스크 낮음). composer closure 변화라 게이트 확인 | S | open |
| IB-036 | debt | **private-dns quota 불일치(HIGH)** — `cross-service.yaml` limit:3 인데 userguide/validated-facts는 **1/account**(2nd create 4xx). Track N. ⚠️docs-vs-docs | limit 3→1. **단 docs 충돌이라 라이브 1회로 실제 cap 확인 후 변경**(맹목 변경 시 유효 create skip 위험) | S | open (라이브 확인 선행) |
| IB-037 | debt | **direct-connect quota 미모델(HIGH)** — docs 5/account + 1:1/VPC인데 `dependencies.json` quota_kinds에 없음 → shared-VPC adopt 시 quota 게이트 부재, 2nd create 409 false-regression. Track N | cross-service.yaml에 direct-connect quota(limit:5) + dependencies.json quota_kinds/budget_paths(`/v1/direct-connects`) 배선 | M | open |
| IB-038 | debt | firewall rule quota(EXSMALL=5 기본) service yaml엔 있으나 budget/quotas-and-budgets.md 미기재. Track N | quotas-and-budgets.md에 5-rule 기본 한도 NOTE(현 single-rule 시나리오는 안전, monitored) | S | open |
| IB-039 | debt | hosted-zone quota(20/account·100 records/zone) 미모델 — 스케일 시 한도. Track N | cross-service.yaml에 hosted-zone quota(limit:20) 추가(현재 저위험) | S | open |
| IB-040 | debt | planned-compute body **console↔API 불일치 의심** — userguide 콘솔 create 폼은 5필드(target/OS/server_type/term/**quantity**)인데 `PlannedComputeCreateRequest` 모델엔 quantity 없음(Track P, billingplan overview 200). | 라이브 2xx 또는 apiref 재확인으로 quantity 필요 여부 검증 → 필요 시 body에 추가. docs 호스트 안정 후 | S | open (검증 필요) |

## 진행 중 티켓 (M6-DESIGN §F)

- 배치1(병렬): T1 new_service.py · T2 expand_targets/targets.py · T4 plan-manifest emit · T6 Planner cadence(이 문서 + orchestrator.md) ✅
- 후속: T3 compose_service/group/theme · T3b run-request compose= · T3c peak_quota 자동분할 · T5 ops.html 오버레이 · T7 발행 race(IB-001) · T8 /platform/ IA 통합
