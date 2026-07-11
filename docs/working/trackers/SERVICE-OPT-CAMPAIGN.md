# 서비스별 단독 실행 최적화 캠페인 (2026-07-11 시작)

> 오너 지시: "각 서비스별로 한번에 하나씩 수행해 보면서 의존관계나, API가
> 필요없이 기다리거나 하는 건 없는지, 각 서비스마다의 최적 수행시간은 뭔지 —
> 니가 직접 테스트하고 개선해봐. 전체 서비스를 한번씩은 한다 생각하고."

## 루프 (서비스당)

1. 활성 런 확인 (경합 금지) → 해당 서비스 라이프사이클만 `SCP_CRUD_IDS` 단독 실행
   (`local_run.live_run`; 런 종료 자동 정리 포함, heavy는 해당 서비스 차례에만 opt-in)
2. events 스팬 분석: 스텝별 시간 · 폴 대기 · 재시도 · 스텝 간 공백 분해
   → "필요한 대기(비동기 수렴)" vs "불필요한 대기(고정 sleep/과도 interval/무의미 wait)"
3. 수리 반영 (poll interval/timeout·스텝 순서·그룹 병렬화·레이스 settle)
4. 재실행 검증 → 최적 수행시간을 durations.json에 기록(source: svc-opt) + knowledge 축적
5. 트래커 갱신 → 다음 서비스

## 순서 원칙

경량 컨트롤플레인(reads/CRUD-light) → 네트워킹 → heavy(VM·DB엔진별·SKE·backup).
과금 최소화: heavy는 서비스당 1회, 즉시 teardown.

## 상태 보드

| # | 서비스 | lifecycles | 상태 | 1차 실측 | 개선 | 최적 실측 | 노트 |
|---|---|---|---|---|---|---|---|
| 1 | cloudmonitoring | gen-cm-event-policy, gen-cm-account-resource | ✅ 완료 | 4.5s+5.9s (2 passed) | 불필요 대기 없음 (스텝 공백 0.01s, 폴 없음) | ~6s/개 | 404-관용은 문서화된 fail-fast (undocumented X-ResourceType 필수, Running VM 없으면 404). 개선 기회: VS 라이프사이클 뒤 배치 시 2xx 승격 가능 — 의존관계 노트 |
| 2 | scr | container-scr-registry | ✅ 완료(스킵) | 6.5s (403→skip) | 대기 없음 | ~6.5s | PF-37 entitlement 게이트 — SDS 해소 후 재실측 | | | | PF-37 게이트 — reads만 |
| 3 | baremetal | baremetal-catalog-reads | ✅ 완료 | 8.6s (1 passed) | 대기 없음 | ~8.6s | read-only | | | | |
| 4 | quick-query | gen-quick-query-* ×2 | ✅ 완료 | 4.7s+3.9s | 대기 없음 | ~5s/개 | read | | | | |
| 5 | configinspection | configinspection-read-coverage | ✅ 완료 | 5.9s | 대기 없음 | ~6s | read | | | | |
| 6 | support / quota / pricing / costexplorer | 각 1 (reads) | ✅ 완료 | 8.1/9.6/9.2/6.8s | 대기 없음 | ~7-10s/개 | read 묶음, 4 passed |
| 7 | network-logging | gen-wave4-nlog | ✅ 완료 | 11.4s | 대기 없음 | ~11s | CRUD-light | | | | |
| 8 | multinodegpucluster | gen-gpu-node-image | ✅ 완료 | 11.3s | 대기 없음 | ~11s | | | | | |
| 9 | queueservice | gen-wave3-qfifo, application-queueservice-queue | ✅ 완료 | 17.5s / **153.3s** | **-127s**: 표준 큐 dedup PUT 400은 범주적(FIFO 전용)인데 optional-4xx 사다리가 스텝당 60s 소진 → expect_status에 400 등록 | 17.5s / **26.3s** | 첫 대형 개선 — 사다리는 '승격 가능한 400'에만 | | | | |
| 10 | iam-identity-center | idc-read-coverage | ✅ 완료 | 17.2s | 대기 없음 (404 관용 reads) | ~17s | | | | | |
| 11 | direct-connect | gen-direct-connect | ✅ 완료 | 30.7s (입양 경로) | settle 17.6s는 필요 대기. **입양 재사용으로 프로비저닝 147s 절약 실증** | ~31s | DC 1:1-per-VPC — gen-private-nat 충돌 처방 대기 |
| 12 | iam | iam-group, gen-wave5-iam-bindings | ✅ 완료 | 18.2s/39.7s | 대기 없음 (스텝 최대 4.8s) | 동일 | | | | | |
| 13 | kms | gen-wave2-sec, security-kms-transit-crypto | ✅ 완료 | 22.8s/45.7s | 대기 없음 | 동일 | 예약삭제(PF-09)로 잔존은 자동소멸 | | | | PF-09 예약삭제 |
| 14 | secretsmanager | security-secretsmanager-writes | ✅ 완료 | 54.8s | 대기 없음 (create 18.6s는 서버 실소요) | ~55s | | | | | |
| 15 | apigateway | gen-wave-apigw, gen-wave5-apigw-policy | ✅ 완료 | 13.3s/93.7s | 일시 ConnectionError 1건 → 엔진 전송 재시도 확장 | 동일 | policy쪽 93.7s는 스텝 수(20+) 실소요 | | | | |
| 16 | firewall | gen-wave5-fw | ✅ 완료 | 93.7s | 대기 없음 (IGW 캐리어 대기 포함 실소요) | ~94s | |
| 17 | resourcemanager | 4종 | ✅ 완료 | RG 80.4s→**19.1s** | set-rg tags:[] 400 수리 (-61s) | 19.1/25/17/45.8s | tag-rg 403은 entitlement | | | | |
| 18 | servicewatch | 4종 실측 | ✅ 완료 | loggroup 147.7s→**~22s** | listmetricinfos 바디 수리(**신규 2xx 커버**) + listmetricdata 400=환경적(메트릭 카탈로그 0) 등록 (-130s) | 9~22s | create-group 500 재발 없음; metricdata 2xx는 VM 동반 런에서 | | | | create-group 500 재확인 |
| 19 | cdn / gslb / dns | reads + hosted-zone | ✅ 완료 | 4.5/6.8/**101s** | dns: 892a의 22분은 전부 큐 대기였음(단독 101s). 쿼터 400에 사다리 87s → **엔진 가드 신설**(max-count/quota는 즉시 기록) | dns 클린 ~15s 예상 | private-dns 삭제는 느린 비동기(DELETING 수 분) |
| 20 | certificatemanager | selfsign | ✅ 완료 | 16.5s (재검증) | 캠페인 자체 회귀({today} int화) 적발·즉수정 | ~16.5s | create 201 |
| 21 | networking (vpc/subnet/port/publicip/peering/TGW/endpoint/NAT) | 다수 | 대기 | | | | 슬롯 소비 — 후반 |
| 22 | loadbalancer | light (heavy는 892a 수리 재검증 대기) | ✅ light 완료 | 8.6s | 대기 없음 | ~9s | heavy(members)는 member_state 수리 검증 겸 후속 |
| 23 | filestorage | volume, wave2-fs (replication은 후속) | ✅ 2/3 | 75.1s/56.8s | 느린 스텝 = 정당한 settle 폴 (async 볼륨) | 동일 | replication-schedule 교차리전은 별도 회차 |
| 24 | scf | 4종 | ✅ 완료 | 62/559/496/112s (4 passed) | wave2-scf 트리거read 404수리 **200 검증**. 재검토 후보: ①PLE request/cancel 사다리 ~285s 매번 소진(승격 실측 無) ②update-code 303s (DEPLOYING 대기 구조) | 동일 | PLE 사다리는 설계물 — 트리아지 판단 대상 |
| 25 | backup | gen-heavy-backup | 대기 | | | | heavy |
| 26 | cloud-ml | 2종 | 대기 | | | | SCR 게이트 부분 |
| 27 | virtualserver | 5종 | 대기 | | | | heavy 2 |
| 28 | dbaas 엔진별 (mysql·postgresql·mariadb·epas·cachestore·eventstreams·searchengine·sqlserver·vertica) | 엔진당 1-2 | 대기 | | | | mysql/pg create 500 PF 주의 |
| 29 | ske | 2종 | 대기 | | | | heavy — 최후반 |

(분류는 진행하며 정제 — "?" 버킷 44종은 해당 서비스 차례에 편입)

## 발견/개선 로그

- **프로비저닝 실측 (dns 단독런)**: 공유 VPC+서브넷 준비에 **147초** — 오너 지적('시작이 너무 오래 걸림')의 실측치. 개선 후보 2건 유효: ①프로비저닝 ∥ pytest 기동 겹침(VPC-불요 항목 즉시 출발) ②VPC CREATING 중 subnet POST 수락 여부 실측 후 겹침. 단독런 배치에는 '선행 프로비저닝 재사용'(연속 배치가 같은 공유 VPC 공유)도 유효.
- **운영 규약**: 장기 라이프사이클(dns 22분 등) 배치는 내부 timeout 금지 — 부모만 죽고 정리 미수행 (이번 dns-v2 사고; 스윕으로 회수).
- **자기 회귀 적발 (#20)**: epoch 정수 코어션이 {today} 문자열 필드까지 int화 → cert 400. epoch_* 한정으로 즉시 수정 — 서비스별 즉검증 루프가 당일 회귀를 당일 적발.
- **dns 스킵 관찰 (#19)**: heavy-스킵 확정 선택인데 공유 VPC 프로비저닝(~1분)을 하고 버림 — '선택 전원이 heavy-스킵이면 프로비저닝 생략' 개선 후보.
- **servicewatch (#18)**: listmetricinfos의 dimensions가 JSON '문자열'로 박혀 400이던 것 수리 → **신규 2xx 커버리지 획득**. listmetricdata는 메트릭 카탈로그가 비면(구동 VM 없음) 어떤 쌍도 400 — cm과 같은 VS-의존 클래스. epoch 토큰({epoch_now}/{epoch_1h_ago}) + 정수 보존 치환 엔진 추가.
- **resourcemanager (#17)**: set-rg tags:[] 400 → 실태그 수리 (80→19초).
- **queueservice (#9)**: optional-4xx 재시도 사다리가 '범주적 400'(FIFO 전용 설정을 표준 큐에)에 스텝당 60초 소진 — 문서화된 범주 400은 expect_status로 등록해 사다리 차단 (153→26초). **같은 패턴 전수 점검 후보**: 사다리는 '시간이 지나면 2xx가 될 수 있는 400'에만 태워야 함.
- **엔진 개선 (svc-opt 파생)**: 런 종료 자동 스윕에 자원-생성 게이트 — read-only 런(3~9초)에 계정 전체 스윕(수 분)이 통째로 돌던 낭비 제거 (events 원장 resource-tracked=0이면 스킵; local_run+console2 동일 적용). 캠페인 회전 속도와 오너 콘솔 read 런 종료 시간 모두 단축.
- **cloudmonitoring (#1)**: 대기 낭비 0. getaccountproductlist는 Running VM 의존 — 전체 런에서 VS 뒤로 배치하면 관용404→2xx 승격 가능 (스케줄 의존 힌트).
