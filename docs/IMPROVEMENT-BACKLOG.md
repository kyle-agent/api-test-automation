# IMPROVEMENT-BACKLOG — Planner가 유지하는 개선 계획

- 소유: Planner tier (agents/orchestrator.md "자율 운영 루프" 참조).
- 갱신: 매 윈도우 머지 후 + 일 1회 스윕. 입력 = PRODUCT-FINDINGS.md,
  SERVICE-GAP-REPORTS.md, tools/retirement.py 매트릭스, dashboard/history.jsonl.
- 형식: `id | area | problem | proposed-fix | size | status`.
  - **id**: `IB-NNN`, append-only, 재사용 금지.
  - **area**: publish · debt · visualize · coverage · onboarding · platform.
  - **size**: S/M/L.
  - **status**: open · in-progress(ticket Tn) · done · waived.

## 백로그

| id | area | problem | proposed-fix | size | status |
|----|------|---------|--------------|------|--------|
| IB-001 | publish | dashboard-data force-push race(확정): dashboard `git push -f`가 conformance rebase-push를 덮어 드롭, concurrency 그룹 없음 | dashboard publish를 clone+rebase로 통일(M6-DESIGN §D.2 1안) + concurrency 안전벨트 | M | open (ticket T7) |
| IB-002 | debt | 태그 네임스페이스 부재 — 동시 실행 시 sweep(cleanup.reconciler)이 다른 실행 자원을 교차 삭제할 위험 | run_id별 owner+run 태그를 reconciler 필터에 강제, 동시 실행은 태그 prefix로 격리 | M | open |
| IB-003 | debt | monitor 재무장 toil — 윈도우마다 발행 감시를 수동 재설정 | controlplane `/schedules` 토글 자동화 또는 CI 후크로 재무장 | S | open |
| IB-004 | debt | 96건 "create without delete" R1 경고(lookup 노드 노이즈) | lookup 노드를 분류해 R1에서 경고 억제(또는 lookup: true 플래그) | S | open |
| IB-005 | visualize | gen_dep_map.py 출력을 ops.html DEP-MAP 마커 사이에 수동 붙여넣기 — drift 위험 | 발행 빌드 step에서 gen_dep_map.py 출력 자동 주입 | S | open |
| IB-006 | coverage | restore/upgrade 체인 게이트(위험/과금) — 다수 비활성 | owner 승인 게이트 + heavy/destructive 분리 배치로 단계 활성화 | M | open |
| IB-007 | debt | second-account backlog(docs/SECOND-ACCOUNT-BACKLOG.md) 미결 | 별도 계정 credential 발급 후 peak_quota 분할을 계정 차원으로 확장 | L | open (owner credential 대기) |
| IB-008 | coverage | SERVICE-GAP-REPORTS의 C 분류(gslb/vpn/cdn/direct-connect 라이브 미증명) | 분류별 다음 윈도우 라이브 투입(placeholder 가능분 우선) | M | open |
| IB-009 | coverage | alert 노드: dimension 필수인데 sw-metric-catalog lookup이 중첩 dimension(key/value)을 못 잡음 → gen-wave5-swatch-alert 비활성 | composer/engine에 중첩 배열 capture(`$.namespaces[0]...dimensions[0]`) 지원 추가 후 활성 | M | open |
| IB-010 | coverage | iam-saml 체인: SAML provider는 multipart/form-data 필요, 엔진은 JSON 전용 → 비활성 | 엔진에 multipart 지원 추가 vs 4 saml ops waive (owner 질문) | M | open (owner 결정 대기) |
| IB-011 | onboarding | custom-metric OTLP ingest namespace 라우팅 키 미상(SWT_CUSTOM_NAMESPACE 400) | 콘솔 agent 설정 확인 후 routing attribute 확정 (owner 질문) | S | open (owner 확인 대기) |
| IB-012 | coverage | gen-wave5-privnat 비활성: create-private-nat가 Connectable 상태의 TGW를 요구(scp-network.private-nat.connectable-transit-gateway-not-found, run 27466988779) — TGW에 VPC attachment/connection이 있어야 Connectable이 됨 | transit-gateway VPC-connection prerequisite 노드 모델링 후 private-nat 체인에 배선 → 재활성 | M | open |
| IB-013 | coverage | gen-wave5-vpce 비활성: create-vpc-endpoint가 전용 endpoint-type subnet을 요구(scp-network.vpc-endpoint.subnet-not-found, run 27466988779) — FS-volume resource_key 배선으로는 부족 | 올바른 type/role로 생성한 endpoint-type-subnet prerequisite 노드 모델링 후 vpc-endpoint에 배선 → 재활성 | M | open |

| IB-014 | debt | lookup 노드가 즉시-빈 리스트를 받으면 hard capture 실패(backup-target: 서버 ACTIVE 직후 목록 미반영, run 27483004836) — 엔진에 'capture 충족까지 GET 재폴링' 능력 부재 | engine에 lookup poll-until-capture(또는 ready 후 capture) 지원 추가 → gen-heavy-backup 재활성 | M | open |
| IB-015 | loop | 종료조건(Stop-when) 부재 — 실패 체인의 재시도 한계가 즉흥적(SKE rev3/PG rev4), 무한 두드림·매번 사람판단 위험 | 에스컬레이션 사다리(L0→L3) + 윈도우당 3 rev 한계 + 무진전 감지 + 사람-필요 6기준 명문화(orchestrator.md). 후속: 한계 수치를 composer/dispatch에 자동화(rev 카운터·history.jsonl diff 게이트) | M | in-progress (정책 codified) |
| IB-016 | loop | 직렬 운영 toil — run 결과를 기다리는 동안 도메인 작업이 멈춰 처리량 저하 | 3-레인 병렬 파이프라인(A 결과대기·B 가이드/도메인·C 합성/준비) 명문화(orchestrator.md), 공유자원 read-before-claim. 후속: 레인 B 상시가동을 세션 부트스트랩에 기본 포함 | M | in-progress (정책 codified) |

## 진행 중 티켓 (M6-DESIGN §F)

- 배치1(병렬): T1 new_service.py · T2 expand_targets/targets.py · T4 plan-manifest emit · T6 Planner cadence(이 문서 + orchestrator.md) ✅
- 후속: T3 compose_service/group/theme · T3b run-request compose= · T3c peak_quota 자동분할 · T5 ops.html 오버레이 · T7 발행 race(IB-001) · T8 /platform/ IA 통합
