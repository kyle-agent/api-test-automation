# SCP API 품질 결함 리포트 (서비스 개발팀 전달용) — 2026-08-20

**검증 방법**: 공식 API Reference(docs.e.samsungsdscloud.com/apireference) 전 엔드포인트 **1,416개** 정적 분석 + 실제 API 호출 검증(동적 프로브 8종 2026-08-20 전면 재실시, kr-west1). 실측 결함은 응답의 `global_request_id`를 함께 기재하므로 서버 로그에서 해당 호출을 직접 추적할 수 있습니다. 별첨 CSV(`API-품질-결함목록-2026-08-20.csv`)에 결함 전체가 로데이터와 함께 있습니다.

## 1. 요약

| 판정 | 엔드포인트 수 | 비율 |
|---|---:|---:|
| 결함 없음 | 914 | 64.5% |
| 개선 필요 (YELLOW) | 471 | 33.3% |
| 심각 (RED) | 31 | 2.2% |

개별 결함 총 **563건**. 아래 §2의 플랫폼 공통 결함 6종은 별도 집계(개별 판정에 중복 계상하지 않음).

### 2026-08-20 동적 재검증 diff (6월 첫 측정 대비)

| 항목 | 해소 | 신규 | 잔존 |
|---|---:|---:|---:|
| 잘못된 입력에 500 (5xx-on-bad-input) | **2** | 0 | 0 |
| 부재 리소스 404 비일관 | 0 | 2 | 88 |
| size 무시(pagination) | 2 | 5 | 52 |

- **해소 확인**: baremetal-blockstorage createvolume/createvolumegroup — 6월 500 → 현재 400 + 누락 필드명 나열(모범 응답). 플랫폼 수정 확인.
- 신규 non-404: `ai-ml/aimlops-platform/getaimlopsplatformnodelistv1`, `ai-ml/aimlops-platform/getaimlopsplatformstorageclasseslistv1` · 신규 size무시: `container/scr/listconnectableresources`, `financial-management/billingplan/listplannedcomputeservertypes`, `management/resourcemanager/listtagkeys`, `management/resourcemanager/listtagvalues`, `management/servicewatch/listalerts`

### 메서드별 분포

| 메서드 | 전체 API | 결함 | RED | 결함/100API |
|---|---:|---:|---:|---:|
| GET | 546 | 165 | 6 | 30.2 |
| POST | 393 | 275 | 22 | 70.0 |
| PUT | 257 | 108 | 2 | 42.0 |
| DELETE | 211 | 6 | 1 | 2.8 |
| PATCH | 9 | 9 | 0 | 100.0 |

GET 결함의 81%는 실호출에서만 드러나는 계열(404 비일관·pagination) — 문서 기준으론 깨끗해 보여도 자동화 클라이언트에는 실장애물. DELETE가 가장 깨끗(2.8/100).

### 결함 구분

**계약 위반** 201건 · **문서 결손** 360건 · **동작버그(기능 결함 — 버그 트래커 전달 권장)** 2건. 각 결함 행에 구분 표기, CSV에 cls 컬럼.

### 상품군별 통계

| 상품군 | API 수 | RED | YELLOW | 결함 건수 |
|---|---:|---:|---:|---:|
| database | 272 | 11 | 51 | 72 |
| management | 250 | 6 | 94 | 108 |
| data-analytics | 123 | 5 | 35 | 49 |
| networking | 216 | 2 | 74 | 81 |
| compute | 181 | 2 | 73 | 88 |
| container | 64 | 2 | 30 | 39 |
| financial-management | 21 | 2 | 11 | 17 |
| application-service | 67 | 1 | 26 | 30 |
| storage | 131 | 0 | 33 | 33 |
| security | 57 | 0 | 30 | 30 |
| ai-ml | 21 | 0 | 8 | 10 |
| platform | 7 | 0 | 5 | 5 |
| devops-tools | 6 | 0 | 1 | 1 |

### 상품별 통계 (결함 보유 상품 전체)

| 상품 | API 수 | RED | YELLOW | 결함 건수 |
|---|---:|---:|---:|---:|
| database/postgresql | 49 | 3 | 8 | 13 |
| compute/virtualserver | 113 | 2 | 43 | 57 |
| networking/vpc | 95 | 2 | 36 | 39 |
| management/servicewatch | 37 | 2 | 23 | 26 |
| management/iam-identity-center | 32 | 2 | 15 | 21 |
| database/mariadb | 49 | 2 | 9 | 13 |
| database/mysql | 48 | 2 | 9 | 13 |
| database/epas | 49 | 2 | 8 | 12 |
| management/cloudmonitoring | 18 | 2 | 4 | 9 |
| financial-management/billingplan | 10 | 2 | 4 | 8 |
| application-service/apigateway | 55 | 1 | 22 | 26 |
| container/scr | 39 | 1 | 20 | 24 |
| container/ske | 25 | 1 | 10 | 15 |
| data-analytics/data-ops | 17 | 1 | 10 | 14 |
| database/sqlserver | 44 | 1 | 10 | 12 |
| database/cachestore | 33 | 1 | 7 | 9 |
| data-analytics/searchengine | 28 | 1 | 6 | 8 |
| data-analytics/vertica | 24 | 1 | 5 | 7 |
| data-analytics/eventstreams | 25 | 1 | 4 | 6 |
| data-analytics/quick-query | 12 | 1 | 4 | 5 |
| compute/scf | 36 | 0 | 20 | 21 |
| management/iam | 62 | 0 | 21 | 21 |
| security/kms | 23 | 0 | 15 | 15 |
| management/organization | 37 | 0 | 11 | 11 |
| networking/cdn | 9 | 0 | 8 | 11 |
| data-analytics/data-flow | 17 | 0 | 6 | 9 |
| storage/archivestorage | 26 | 0 | 8 | 8 |
| ai-ml/aimlops-platform | 12 | 0 | 6 | 8 |
| management/resourcemanager | 27 | 0 | 8 | 8 |
| storage/filestorage | 22 | 0 | 8 | 8 |
| storage/backup | 31 | 0 | 7 | 7 |
| networking/dns | 22 | 0 | 7 | 7 |
| management/cloudcontrol | 15 | 0 | 7 | 7 |
| storage/baremetal-blockstorage | 41 | 0 | 6 | 6 |
| networking/vpn | 10 | 0 | 6 | 6 |
| security/secretsmanager | 14 | 0 | 6 | 6 |
| compute/baremetal | 16 | 0 | 6 | 6 |
| networking/security-group | 17 | 0 | 5 | 5 |
| networking/gslb | 10 | 0 | 5 | 5 |
| storage/parallel-filestorage | 11 | 0 | 4 | 4 |
| networking/direct-connect | 8 | 0 | 4 | 4 |
| application-service/queueservice | 12 | 0 | 4 | 4 |
| compute/multinodegpucluster | 16 | 0 | 4 | 4 |
| financial-management/costexplorer | 3 | 0 | 2 | 4 |
| security/certificatemanager | 7 | 0 | 4 | 4 |
| financial-management/budget | 5 | 0 | 3 | 3 |
| networking/firewall | 8 | 0 | 2 | 3 |
| platform/sts | 3 | 0 | 3 | 3 |
| security/secretvault | 5 | 0 | 3 | 3 |
| management/loggingaudit | 10 | 0 | 2 | 2 |
| ai-ml/cloud-ml | 9 | 0 | 2 | 2 |
| management/quota | 4 | 0 | 2 | 2 |
| financial-management/pricing | 3 | 0 | 2 | 2 |
| platform/product | 4 | 0 | 2 | 2 |
| security/configinspection | 8 | 0 | 2 | 2 |
| networking/loadbalancer | 37 | 0 | 1 | 1 |
| management/network-logging | 4 | 0 | 1 | 1 |
| devops-tools/devopsservice | 6 | 0 | 1 | 1 |

## 2. 플랫폼 공통 결함 (전 서비스급 영향)

| 결함 | 범위 | 무엇이 문제인가 | 기대 동작 |
|---|---|---|---|
| `error-schema-undocumented` | 1414 EP | 4xx/5xx 에러 응답 스키마가 문서에 없고, 실제 에러 엔벨로프가 3종 혼재 — 표준 errors[](서비스) · Spring 기본(공통 게이트웨이의 인증 실패 경로) · HTML(엣지 WAF). §5.5 실응답 원문 참조 | 표준 errors[] 스키마 문서화 + 게이트웨이/엣지 포함 엔벨로프 단일화 |
| `unauth-404` | 58 서비스 | 공통 API 게이트웨이(Spring Cloud Gateway)가 인증 실패를 게이트웨이 기본 포맷으로 반환 — 404 + Spring 기본 엔벨로프({timestamp,path,status,error,requestId}). 58개 서비스 전원 동일 + requestId 프리픽스 서비스 간 재사용으로 게이트웨이 소행 확정. 개별 서비스가 아닌 게이트웨이 수정 대상 | 게이트웨이 에러 핸들러에서 401 + 표준 errors[] 엔벨로프로 변환 |
| `no-cors` | 58 서비스 | OPTIONS 요청 403, Allow/CORS 헤더 없음 | OPTIONS/CORS 표준 응답 |
| `accept-language-ignored` | 124 EP | Accept-Language 헤더 무시 — 에러 메시지 영어 고정 | 요청 언어 반영 |
| `path-collisions` | 78 경로 | 서로 다른 서비스가 동일 method+path 재사용 (네임스페이스 없음) | 경로 네임스페이스 분리 |
| `model-fields-no-description` | 463 필드 | 모델 필드 설명이 공란 | 필드 설명 작성 |

## 3. 결함 유형 정의 (건수 순)

| 유형 | 건수 | 무엇이 문제인가 | 기대 동작 |
|---|---:|---|---|
| `undiscoverable-params` | 291 | 필수 파라미터의 값 형식·제약·출처가 API Reference에 없음 | 필수 필드별 타입·제약·유효값(또는 값을 얻는 조회 API)을 문서에 명시 |
| `notfound-inconsistent` | 75 | 형식이 유효한 존재하지-않는 id 조회에 404가 아닌 400/403 반환 — 클라이언트가 "형식 오류/권한 문제/부재"를 구분할 수 없음. 참고: 같은 플랫폼에서 192개 API는 404를 반환하므로 (존재여부 은닉 등) 의도된 정책이 아니라 비일관 | 부재 리소스는 404로 통일 (또는 은닉 정책이라면 플랫폼 전체 일관 + 문서화) |
| `pagination` | 57 | 목록 조회에 ?size=1을 보내도 전량 반환(size 무시 — 실측: listservertypes 121개, listbillingitemids 276개), 또는 페이징 메타(count/total) 부재로 클라이언트가 순회 종료 시점을 알 수 없음 | size개만 반환 + count/page/size 메타 제공. 의도적 전량 반환 API라면 size/page 파라미터를 문서에서 제거 |
| `no-success-schema` | 55 | 성공(2xx) 응답 스키마가 문서에 없음 | 2xx 응답 바디 스키마 문서화 |
| `param-naming` | 16 | 경로 파라미터 명명이 표준과 다름 | 리소스명을 포함한 파라미터명 사용(예: {alert_id}) |
| `opaque-validation` | 13 | 잘못된 입력에 대한 400 응답이 원인 필드와 위반 규칙을 특정하지 않음 | 에러 응답에 필드명과 위반 내용을 포함 |
| `runtime.500-on-client-state` | 9 | 클라이언트가 유발한 상태·입력에 500 반환 | 4xx + 원인/해소 방법 안내 (500은 서버 결함 신호로만) |
| `method-verb` | 9 | 엔드포인트 이름의 동사와 HTTP 메서드 불일치 | 동사-메서드 정합(조회=GET, 생성=POST 등) |
| `schema-undocumented-field` | 8 | 실제 응답에 문서에 없는 필드 존재 | 응답 스키마 문서 갱신 |
| `docs.async-settle-undocumented` | 6 | 생성/변경 202 후 상태가 안정(ACTIVE)되기 전의 set/delete를 400으로 거절 — 400은 "요청 자체가 잘못됨" 신호라 클라이언트가 "기다렸다 재시도" 판단을 할 수 없음. 같은 상태-충돌에 플랫폼 내 다른 서비스는 이미 409를 반환(SCR creating-cannot-delete 409, VIP connected-ports 409 — 실측 원문 §5.5 인접) | 일시-상태 거절은 409로 통일(플랫폼 자체 선례 준용). 보조로 "202=접수, ACTIVE 후 변경 가능" 문서화 |
| `deprecated` | 4 | DEPRECATED 표기만 있고 대체 API 안내 없음 | 대체 엔드포인트와 제거 일정 명시 |
| `versioning.doc-version-not-supported` | 3 | 문서에 명시된 API 버전을 서버가 406으로 거절 | 문서-서버 버전 정합 |
| `notfound-200-list` | 2 | 존재하지 않는 부모의 하위 목록 조회가 200(빈 목록) 반환 | 부모 부재 시 404 |
| `errors.detail-python-repr` | 2 | errors[].detail에 파이썬 리스트의 repr 문자열을 그대로 직렬화해 반환 — 플랫폼 표준(JSON 배열, 당일 131개 API 실측)과 달리 필드 단위 파싱 불가. 즉시 수정 대상 | detail을 JSON 배열로 반환 |
| `notfound-200` | 2 | 존재하지 않는 리소스 조회가 200 반환 | 부재 리소스는 404 |
| `networking.subnet-read-plane-version-drift` | 2 | 생성(v1.3)은 되는 PRIVATE 타입 서브넷이 조회 계열(v1.2 enum)에서 보이지 않음 — API로 존재를 확인할 수 없는 리소스 발생 | 조회 계열 enum을 생성 계열과 동일 버전으로 정합 |
| `status.wrong_code_403` | 1 | 입력 검증 오류에 403 반환 (권한 문제로 오인 유발) | 입력 오류는 400 |
| `compute.image-sharing-202-empty-body` | 1 | 공유 시작 202 응답 바디가 비어 있어 진행 추적 수단(공유 ID)이 없음 | 202 응답에 추적 가능한 식별자 반환 |
| `compute.image-sharing-orphan-volume-no-cleanup` | 1 | 공유 과정에서 생성된 임시 볼륨이 공유 중단 시 삭제 불가능(400 반복) 상태로 잔존 | 공유 레코드 소멸 시 파생 임시 볼륨 정리 경로 제공 |
| `docs.image-share-cancellation-undocumented` | 1 | 공유의 수락/거절/취소가 별도 엔드포인트(updateimagemember)에 있음이 해당 문서에 없음 | 공유 문서에 상대 엔드포인트 상호 참조 |
| `compute.image-sharing-delete-during-transfer-unguarded` | 1 | 공유 전송 중인 원본 이미지 삭제가 차단 없이 성공(204) → 파생 임시 볼륨 영구 고아화 | 전송 중 원본 삭제를 409로 차단하거나 파생 자원 연쇄 정리 |
| `errors.rate-limit-non-json` | 1 | 유량 제한 시 JSON 에러 규격이 아닌 HTML 차단 페이지(417) 반환 | 엣지 레벨에서도 표준 JSON 에러 엔벨로프 유지 |
| `schema-missing-field` | 1 | 문서상 필수 응답 필드가 실제 응답에 없음 | 문서-실응답 정합 |
| `docs.version-semantics-undocumented` | 1 | 버전에 따라 응답 시맨틱이 다른데(1.1=202+빈 바디) 문서는 1.0 동작만 기술 | 버전별 응답 차이 문서화 |
| `runtime.empty-collection-404` | 1 | 빈 컬렉션 조회가 404 반환 | 빈 컬렉션은 200 + 빈 배열 |

## 4. 심각(RED) 31개 API — 개별 상세

### application-service/apigateway — `PUT /v1/privatelink-endpoints/{privatelink_endpoint_id}/approval` (approveprivatelinkendpoint)
- **[문서] 문제**: 필수 파라미터의 값 형식·제약·출처가 API Reference에 없음
  - 근거: required fields with no documented constraint: api_id
  - 기대 동작: 필수 필드별 타입·제약·유효값(또는 값을 얻는 조회 API)을 문서에 명시
- **[계약] 문제**: 클라이언트가 유발한 상태·입력에 500 반환
  - 근거: PUT approve on a PrivateLink Endpoint whose request/cancel already AUTO-approved it (state already APPROVED, so REQUESTED/REJECTED are never reached) -> 500 ContactAdminForAssistance instead of 400 invalid-state. req-e619b286..., 실측 2026-07-16.
  - 기대 동작: 4xx + 원인/해소 방법 안내 (500은 서버 결함 신호로만)

### compute/virtualserver — `POST /v1/images/{image_id}/share` (createsharingimage)
- **[문서] 문제**: 성공(2xx) 응답 스키마가 문서에 없음
  - 근거: POST 2xx documents no schema
  - 기대 동작: 2xx 응답 바디 스키마 문서화
- **[계약] 문제**: 공유 시작 202 응답 바디가 비어 있어 진행 추적 수단(공유 ID)이 없음
  - 근거: POST /v1/images/{id}/share -> 202 with an EMPTY body {} — no tracking handle (share/task id) is returned, so a caller can't correlate the async op with its outcome except by polling the target account's pending-images or watching temp-volume side effects. 실측 타임라인: 18:03:36Z createimage 202 -> 18:03:37Z createsharingimage 202 (req_body {"account_id": ...}) -> 18:04:35Z platform spawns a hex-named untagged 104GB temp volume in the recipient account -> 18:05:53Z deleteimage(original) 204 ACCEPTED while the share is still in flight -> the share record vanishes from BOTH accounts' API-visible state (pending-images count 0 either side) but the temp volume's VolumeForSharingImageDelete flag persists, permanently rejecting DELETE ("try again later") with no API-visible owner left to reconcile against.
  - 기대 동작: 202 응답에 추적 가능한 식별자 반환
- **[동작버그] 문제**: 공유 과정에서 생성된 임시 볼륨이 공유 중단 시 삭제 불가능(400 반복) 상태로 잔존
  - 근거: The hex-named 104GB temp volume createsharingimage spawns in the recipient account has no API-reachable cleanup path once its share record vanishes (source deleted mid-transfer, or recipient never acts): DELETE permanently 400s (VolumeForSharingImageDelete, "try again later") with no owning share left in either account's API state to reconcile against — an unrecoverable billable orphan via the API plane, confirmed via cross-account API diff 2026-07-16 (old vs new account both 0 pending-images / 0 private images, volume still stuck). 실측 타임라인: 18:03:36Z createimage 202 -> 18:03:37Z createsharingimage 202 (req_body {"account_id": ...}) -> 18:04:35Z platform spawns a hex-named untagged 104GB temp volume in the recipient account -> 18:05:53Z deleteimage(original) 204 ACCEPTED while the share is still in flight -> the share record vanishes from BOTH accounts' API-visible state (pending-images count 0 either side) but the temp volume's VolumeForSharingImageDelete flag persists, permanently rejecting DELETE ("try again later") with no API-visible owner left to reconcile against.
  - 기대 동작: 공유 레코드 소멸 시 파생 임시 볼륨 정리 경로 제공
- **[문서] 문제**: 공유의 수락/거절/취소가 별도 엔드포인트(updateimagemember)에 있음이 해당 문서에 없음
  - 근거: createsharingimage's own doc page never mentions that the accept/reject/cancel counterpart lives on a DIFFERENT endpoint family — PUT /v1/images/{image_id}/members/{member_id} (updateimagemember, body {"status": pending|accepted|rejected}) — not a sibling of the share endpoint itself. An AI/agent reading only the share endpoint's docs has no discoverable path to cancel or unwind a share. (Corrected 2026-07-16 after an earlier read mistakenly concluded no accept/reject/cancel API existed at all — it does, just undocumented as a counterpart of share.)
  - 기대 동작: 공유 문서에 상대 엔드포인트 상호 참조

### compute/virtualserver — `DELETE /v1/images/{image_id}` (deleteimage)
- **[동작버그] 문제**: 공유 전송 중인 원본 이미지 삭제가 차단 없이 성공(204) → 파생 임시 볼륨 영구 고아화
  - 근거: DELETE on the source image of an in-flight createsharingimage share succeeds (204) with no guard rejecting it — deleting the source ~2m16s into a still-pending share orphans the derived temp volume permanently (see compute.image-sharing-orphan-volume-no-cleanup). 실측 타임라인: 18:03:36Z createimage 202 -> 18:03:37Z createsharingimage 202 (req_body {"account_id": ...}) -> 18:04:35Z platform spawns a hex-named untagged 104GB temp volume in the recipient account -> 18:05:53Z deleteimage(original) 204 ACCEPTED while the share is still in flight -> the share record vanishes from BOTH accounts' API-visible state (pending-images count 0 either side) but the temp volume's VolumeForSharingImageDelete flag persists, permanently rejecting DELETE ("try again later") with no API-visible owner left to reconcile against.
  - 기대 동작: 전송 중 원본 삭제를 409로 차단하거나 파생 자원 연쇄 정리

### container/scr — `GET /v1/container-registries/{registry_id}` (showregistry)
- **[계약] 문제**: 클라이언트가 유발한 상태·입력에 500 반환
  - 근거: GET on a registry mid-CREATING -> 500 ContactAdminForAssistance instead of 409/425 (a racing client-visible state, not a true not-found). req-90138294..., live 실측-07-16. Workaround: 500 retry ladder 15s x 10 until ACTIVE.
  - 기대 동작: 4xx + 원인/해소 방법 안내 (500은 서버 결함 신호로만)

### container/ske — `POST /v1/nodepools` (createnodepool)
- **[문서] 문제**: 필수 파라미터의 값 형식·제약·출처가 API Reference에 없음
  - 근거: required fields with no documented constraint: cluster_id, image_os, image_os_version, keypair_name, kubernetes_version, name, server_type_id, volume_type_name
  - 기대 동작: 필수 필드별 타입·제약·유효값(또는 값을 얻는 조회 API)을 문서에 명시
- **[계약] 문제**: 클라이언트가 유발한 상태·입력에 500 반환
  - 근거: POST /v1/nodepools -> 500 ContactAdminForAssistance (16.7s) when `zone` is omitted on a single-AZ account (nodepoolcreaterequestv1dot5 added an optional `zone`; unmatched default-zone placement 500s server-side instead of 400 asking for zone). Cluster itself reaches RUNNING fine; failure isolated to nodepool create. req-87752221..., single-service live rerun 2026-07-16.
  - 기대 동작: 4xx + 원인/해소 방법 안내 (500은 서버 결함 신호로만)

### data-analytics/data-ops — `GET /v1/data-ops/image-versions` (getdataopsimageversionv1)
- **[계약] 문제**: 문서상 필수 응답 필드가 실제 응답에 없음
  - 근거: response omits documented required field(s): image_attr
  - 기대 동작: 문서-실응답 정합

### data-analytics/eventstreams — `POST /v1/clusters` (eventstreamscreatecluster)
- **[문서] 문제**: 필수 파라미터의 값 형식·제약·출처가 API Reference에 없음
  - 근거: required fields with no documented constraint: dbaas_engine_version_id, instance_name_prefix, name, subnet_id, timezone
  - 기대 동작: 필수 필드별 타입·제약·유효값(또는 값을 얻는 조회 API)을 문서에 명시
- **[계약] 문제**: 잘못된 입력에 대한 400 응답이 원인 필드와 위반 규칙을 특정하지 않음
  - 근거: 400 names neither field nor rule
  - 기대 동작: 에러 응답에 필드명과 위반 내용을 포함

### data-analytics/quick-query — `POST /v1/quick-query/validate-resources` (validatequickqueryresources)
- **[계약] 문제**: 클라이언트가 유발한 상태·입력에 500 반환
  - 근거: POST /v1/quick-query/validate-resources -> 500 ContactAdminForAssistance when the account has no Quick Query instance (service itself is reachable — image-versions 200 same run) instead of 400/404. Reconfirmed 실측 2026-07-16; already masked as a <자원명>-axis known_issue (data/baselines/known_issues.json) but not previously reflected as an AXIS-2 design finding.
  - 기대 동작: 4xx + 원인/해소 방법 안내 (500은 서버 결함 신호로만)

### data-analytics/searchengine — `POST /v1/clusters` (searchenginecreatecluster)
- **[문서] 문제**: 필수 파라미터의 값 형식·제약·출처가 API Reference에 없음
  - 근거: required fields with no documented constraint: dbaas_engine_version_id, instance_name_prefix, name, subnet_id, timezone
  - 기대 동작: 필수 필드별 타입·제약·유효값(또는 값을 얻는 조회 API)을 문서에 명시
- **[계약] 문제**: 잘못된 입력에 대한 400 응답이 원인 필드와 위반 규칙을 특정하지 않음
  - 근거: 400 names neither field nor rule
  - 기대 동작: 에러 응답에 필드명과 위반 내용을 포함

### data-analytics/vertica — `POST /v1/clusters` (verticacreatecluster)
- **[문서] 문제**: 필수 파라미터의 값 형식·제약·출처가 API Reference에 없음
  - 근거: required fields with no documented constraint: dbaas_engine_version_id, instance_name_prefix, name, subnet_id, timezone
  - 기대 동작: 필수 필드별 타입·제약·유효값(또는 값을 얻는 조회 API)을 문서에 명시
- **[계약] 문제**: 잘못된 입력에 대한 400 응답이 원인 필드와 위반 규칙을 특정하지 않음
  - 근거: 400 names neither field nor rule
  - 기대 동작: 에러 응답에 필드명과 위반 내용을 포함

### database/cachestore — `POST /v1/clusters` (cachestorecreatecluster)
- **[문서] 문제**: 필수 파라미터의 값 형식·제약·출처가 API Reference에 없음
  - 근거: required fields with no documented constraint: dbaas_engine_version_id, instance_name_prefix, name, subnet_id, timezone
  - 기대 동작: 필수 필드별 타입·제약·유효값(또는 값을 얻는 조회 API)을 문서에 명시
- **[계약] 문제**: 잘못된 입력에 대한 400 응답이 원인 필드와 위반 규칙을 특정하지 않음
  - 근거: 400 names neither field nor rule
  - 기대 동작: 에러 응답에 필드명과 위반 내용을 포함

### database/epas — `POST /v1/clusters` (epascreatecluster)
- **[문서] 문제**: 필수 파라미터의 값 형식·제약·출처가 API Reference에 없음
  - 근거: required fields with no documented constraint: dbaas_engine_version_id, instance_name_prefix, name, subnet_id, timezone
  - 기대 동작: 필수 필드별 타입·제약·유효값(또는 값을 얻는 조회 API)을 문서에 명시
- **[계약] 문제**: 잘못된 입력에 대한 400 응답이 원인 필드와 위반 규칙을 특정하지 않음
  - 근거: 400 names neither field nor rule
  - 기대 동작: 에러 응답에 필드명과 위반 내용을 포함

### database/epas — `POST /v1/clusters/{cluster_id}/log-export-configs` (epasregisterlogexportconfig)
- **[문서] 문제**: 필수 파라미터의 값 형식·제약·출처가 API Reference에 없음
  - 근거: required fields with no documented constraint: access_key, bucket_name, log_type, schedule_day_of_month, schedule_frequency_type, schedule_hour, secret_key
  - 기대 동작: 필수 필드별 타입·제약·유효값(또는 값을 얻는 조회 API)을 문서에 명시
- **[계약] 문제**: 클라이언트가 유발한 상태·입력에 500 반환
  - 근거: Same access_key="" -> 500 ContactAdminForAssistance class as database/postgresql/postgresqlregisterlogexportconfig (실측).
  - 기대 동작: 4xx + 원인/해소 방법 안내 (500은 서버 결함 신호로만)

### database/mariadb — `POST /v1/clusters` (mariadbcreatecluster)
- **[문서] 문제**: 필수 파라미터의 값 형식·제약·출처가 API Reference에 없음
  - 근거: required fields with no documented constraint: dbaas_engine_version_id, instance_name_prefix, name, subnet_id, timezone
  - 기대 동작: 필수 필드별 타입·제약·유효값(또는 값을 얻는 조회 API)을 문서에 명시
- **[계약] 문제**: 잘못된 입력에 대한 400 응답이 원인 필드와 위반 규칙을 특정하지 않음
  - 근거: 400 names neither field nor rule
  - 기대 동작: 에러 응답에 필드명과 위반 내용을 포함

### database/mariadb — `POST /v1/clusters/{cluster_id}/log-export-configs` (mariadbregisterlogexportconfig)
- **[문서] 문제**: 필수 파라미터의 값 형식·제약·출처가 API Reference에 없음
  - 근거: required fields with no documented constraint: access_key, bucket_name, log_type, schedule_day_of_month, schedule_frequency_type, schedule_hour, secret_key
  - 기대 동작: 필수 필드별 타입·제약·유효값(또는 값을 얻는 조회 API)을 문서에 명시
- **[계약] 문제**: 클라이언트가 유발한 상태·입력에 500 반환
  - 근거: Same access_key="" -> 500 ContactAdminForAssistance class as database/postgresql/postgresqlregisterlogexportconfig (실측).
  - 기대 동작: 4xx + 원인/해소 방법 안내 (500은 서버 결함 신호로만)

### database/mysql — `POST /v1/clusters` (mysqlcreatecluster)
- **[문서] 문제**: 필수 파라미터의 값 형식·제약·출처가 API Reference에 없음
  - 근거: required fields with no documented constraint: dbaas_engine_version_id, instance_name_prefix, name, subnet_id, timezone
  - 기대 동작: 필수 필드별 타입·제약·유효값(또는 값을 얻는 조회 API)을 문서에 명시
- **[계약] 문제**: 잘못된 입력에 대한 400 응답이 원인 필드와 위반 규칙을 특정하지 않음
  - 근거: 400 names neither field nor rule
  - 기대 동작: 에러 응답에 필드명과 위반 내용을 포함

### database/mysql — `POST /v1/clusters/{cluster_id}/log-export-configs` (mysqlregisterlogexportconfig)
- **[문서] 문제**: 필수 파라미터의 값 형식·제약·출처가 API Reference에 없음
  - 근거: required fields with no documented constraint: access_key, bucket_name, log_type, schedule_day_of_month, schedule_frequency_type, schedule_hour, secret_key
  - 기대 동작: 필수 필드별 타입·제약·유효값(또는 값을 얻는 조회 API)을 문서에 명시
- **[계약] 문제**: 클라이언트가 유발한 상태·입력에 500 반환
  - 근거: Same access_key="" -> 500 ContactAdminForAssistance class as database/postgresql/postgresqlregisterlogexportconfig (실측).
  - 기대 동작: 4xx + 원인/해소 방법 안내 (500은 서버 결함 신호로만)

### database/postgresql — `POST /v1/clusters` (postgresqlcreatecluster)
- **[문서] 문제**: 필수 파라미터의 값 형식·제약·출처가 API Reference에 없음
  - 근거: required fields with no documented constraint: dbaas_engine_version_id, instance_name_prefix, name, subnet_id, timezone
  - 기대 동작: 필수 필드별 타입·제약·유효값(또는 값을 얻는 조회 API)을 문서에 명시
- **[계약] 문제**: 잘못된 입력에 대한 400 응답이 원인 필드와 위반 규칙을 특정하지 않음
  - 근거: 400 names neither field nor rule
  - 기대 동작: 에러 응답에 필드명과 위반 내용을 포함

### database/postgresql — `POST /v1/clusters/{cluster_id}/log-export-configs` (postgresqlregisterlogexportconfig)
- **[문서] 문제**: 필수 파라미터의 값 형식·제약·출처가 API Reference에 없음
  - 근거: required fields with no documented constraint: access_key, bucket_name, log_type, schedule_day_of_month, schedule_frequency_type, schedule_hour, secret_key
  - 기대 동작: 필수 필드별 타입·제약·유효값(또는 값을 얻는 조회 API)을 문서에 명시
- **[계약] 문제**: 클라이언트가 유발한 상태·입력에 500 반환
  - 근거: POST log-export-config with access_key="" -> 500 ContactAdminForAssistance instead of 400 (an empty required credential should fail input validation, not the backend). Same class reproduced across the mariadb/mysql/epas siblings (see sibling findings on this rule_id). Heavy n6 DBaaS runs; already a <자원명>-axis known_issue but not previously an AXIS-2 finding. Reconfirmed 실측 2026-07-16.
  - 기대 동작: 4xx + 원인/해소 방법 안내 (500은 서버 결함 신호로만)

### database/postgresql — `PUT /v1/clusters/{cluster_id}/parameters` (postgresqlsetparametervalues)
- **[계약] 문제**: 클라이언트가 유발한 상태·입력에 500 반환
  - 근거: PUT parameters no-op echo (re-submitting the current applied_value for a template-string parameter, e.g. "{1/8 of server total memory}") -> 500 ContactAdminForAssistance instead of 200/400. req-ef12a36a..., 실측 campaign A (2026-07-16). Workaround: scenario only echoes literal-valued params (e.g. max_connections).
  - 기대 동작: 4xx + 원인/해소 방법 안내 (500은 서버 결함 신호로만)

### database/sqlserver — `POST /v1/clusters` (sqlservercreatecluster)
- **[문서] 문제**: 필수 파라미터의 값 형식·제약·출처가 API Reference에 없음
  - 근거: required fields with no documented constraint: dbaas_engine_version_id, instance_name_prefix, name, subnet_id, timezone
  - 기대 동작: 필수 필드별 타입·제약·유효값(또는 값을 얻는 조회 API)을 문서에 명시
- **[계약] 문제**: 잘못된 입력에 대한 400 응답이 원인 필드와 위반 규칙을 특정하지 않음
  - 근거: 400 names neither field nor rule
  - 기대 동작: 에러 응답에 필드명과 위반 내용을 포함

### financial-management/billingplan — `POST /v1/planned-computes` (createplannedcomputes)
- **[문서] 문제**: 필수 파라미터의 값 형식·제약·출처가 API Reference에 없음
  - 근거: required fields with no documented constraint: server_type, service_id
  - 기대 동작: 필수 필드별 타입·제약·유효값(또는 값을 얻는 조회 API)을 문서에 명시
- **[계약] 문제**: errors[].detail에 파이썬 리스트의 repr 문자열을 그대로 직렬화해 반환 — 플랫폼 표준(JSON 배열, 당일 131개 API 실측)과 달리 필드 단위 파싱 불가. 즉시 수정 대상
  - 근거: errors[].detail is a stringified Python list — observed "['Field required', 'Field required', 'Field required', 'Field required']" (empty-body 400, global_request_id req-b6280fd2-7848-…, 2026-08-20). Platform-standard detail is a JSON array (131 endpoints measured same day); a repr string cannot be parsed field-wise. Immediate-fix per owner triage.
  - 기대 동작: detail을 JSON 배열로 반환

### financial-management/billingplan — `POST /v1/planned-computes/cancellation-fee` (showcancellationfee)
- **[계약] 문제**: 엔드포인트 이름의 동사와 HTTP 메서드 불일치
  - 근거: read-verb name but not GET (POST /v1/planned-computes/cancellation-fee)
  - 기대 동작: 동사-메서드 정합(조회=GET, 생성=POST 등)
- **[계약] 문제**: errors[].detail에 파이썬 리스트의 repr 문자열을 그대로 직렬화해 반환 — 플랫폼 표준(JSON 배열, 당일 131개 API 실측)과 달리 필드 단위 파싱 불가. 즉시 수정 대상
  - 근거: errors[].detail is a stringified Python list — observed "['Field required']" (empty-body 400, global_request_id req-210f6bdc-3f2a-4932-9585-5e8c8167773e, 2026-08-20). Same repr-leak shape as createplannedcomputes. Immediate-fix per owner triage.
  - 기대 동작: detail을 JSON 배열로 반환

### management/cloudmonitoring — `POST /v1/cloudmonitorings/product/v2/metric-data` (getmetricperfdatalist)
- **[계약] 문제**: 엔드포인트 이름의 동사와 HTTP 메서드 불일치
  - 근거: read-verb name but not GET (POST /v1/cloudmonitorings/product/v2/metric-data)
  - 기대 동작: 동사-메서드 정합(조회=GET, 생성=POST 등)
- **[문서] 문제**: 필수 파라미터의 값 형식·제약·출처가 API Reference에 없음
  - 근거: required fields with no documented constraint: queryEndDt
  - 기대 동작: 필수 필드별 타입·제약·유효값(또는 값을 얻는 조회 API)을 문서에 명시
- **[계약] 문제**: 잘못된 입력에 대한 400 응답이 원인 필드와 위반 규칙을 특정하지 않음
  - 근거: 400 names neither field nor rule
  - 기대 동작: 에러 응답에 필드명과 위반 내용을 포함

### management/cloudmonitoring — `POST /v1/cloudmonitorings/event/v2/event-policies` (puteventpolicy)
- **[문서] 문제**: 필수 파라미터의 값 형식·제약·출처가 API Reference에 없음
  - 근거: required fields with no documented constraint: productResourceId
  - 기대 동작: 필수 필드별 타입·제약·유효값(또는 값을 얻는 조회 API)을 문서에 명시
- **[계약] 문제**: 잘못된 입력에 대한 400 응답이 원인 필드와 위반 규칙을 특정하지 않음
  - 근거: 400 names neither field nor rule
  - 기대 동작: 에러 응답에 필드명과 위반 내용을 포함

### management/iam-identity-center — `POST /v1/groups` (creategroup)
- **[문서] 문제**: 필수 파라미터의 값 형식·제약·출처가 API Reference에 없음
  - 근거: required fields with no documented constraint: instance_id, name
  - 기대 동작: 필수 필드별 타입·제약·유효값(또는 값을 얻는 조회 API)을 문서에 명시
- **[계약] 문제**: 잘못된 입력에 대한 400 응답이 원인 필드와 위반 규칙을 특정하지 않음
  - 근거: 400 names neither field nor rule
  - 기대 동작: 에러 응답에 필드명과 위반 내용을 포함

### management/iam-identity-center — `POST /v1/permission-sets` (createpermissionset)
- **[문서] 문제**: 필수 파라미터의 값 형식·제약·출처가 API Reference에 없음
  - 근거: required fields with no documented constraint: instance_id, name
  - 기대 동작: 필수 필드별 타입·제약·유효값(또는 값을 얻는 조회 API)을 문서에 명시
- **[계약] 문제**: 잘못된 입력에 대한 400 응답이 원인 필드와 위반 규칙을 특정하지 않음
  - 근거: 400 names neither field nor rule
  - 기대 동작: 에러 응답에 필드명과 위반 내용을 포함

### management/servicewatch — `GET /v1/event-rules/{event_rule_id}` (showeventrule)
- **[계약] 문제**: 존재하지 않는 리소스 조회가 200 반환
  - 근거: non-existent id -> 200 (should be 404)
  - 기대 동작: 부재 리소스는 404

### management/servicewatch — `GET /v1/log-groups/{log_group_id}` (showloggroup)
- **[계약] 문제**: 존재하지 않는 리소스 조회가 200 반환
  - 근거: non-existent id -> 200 (should be 404)
  - 기대 동작: 부재 리소스는 404

### networking/vpc — `GET /v1/subnets` (listsubnets)
- **[계약] 문제**: 목록 조회에 ?size=1을 보내도 전량 반환(size 무시 — 실측: listservertypes 121개, listbillingitemids 276개), 또는 페이징 메타(count/total) 부재로 클라이언트가 순회 종료 시점을 알 수 없음
  - 근거: ignores size=1 (returned 2)
  - 기대 동작: size개만 반환 + count/page/size 메타 제공. 의도적 전량 반환 API라면 size/page 파라미터를 문서에서 제거
- **[계약] 문제**: 생성(v1.3)은 되는 PRIVATE 타입 서브넷이 조회 계열(v1.2 enum)에서 보이지 않음 — API로 존재를 확인할 수 없는 리소스 발생
  - 근거: GET listsubnets?type=PRIVATE -> 400 "Input should be 'GENERAL', 'LOCAL' or 'VPC_ENDPOINT'" (PRIVATE rejected as a filter value) even though createsubnet documents and accepts PRIVATE. PRIVATE subnet enum was added in createsubnet v1.3 (PUBLIC/PRIVATE/LOCAL/VPC_ENDPOINT) and create/delete accept it (202), but the READ plane (show/list) still validates against the old v1.2 enum (GENERAL/LOCAL/VPC_ENDPOINT) -> a PRIVATE-typed subnet is a live, billable resource with no GET path (create/delete plane version != read plane version). Live-probed req-65a36c09..., 실측 2026-07-16; scenario workaround is a 30s blind settle instead of show-poll.
  - 기대 동작: 조회 계열 enum을 생성 계열과 동일 버전으로 정합

### networking/vpc — `GET /v1/subnets/{subnet_id}` (showsubnet)
- **[계약] 문제**: 생성(v1.3)은 되는 PRIVATE 타입 서브넷이 조회 계열(v1.2 enum)에서 보이지 않음 — API로 존재를 확인할 수 없는 리소스 발생
  - 근거: GET showsubnet on a PRIVATE-typed subnet -> 404 "Not found with ID With Invalid Type" even though the subnet exists and DELETE on the same id succeeds (202). PRIVATE subnet enum was added in createsubnet v1.3 (PUBLIC/PRIVATE/LOCAL/VPC_ENDPOINT) and create/delete accept it (202), but the READ plane (show/list) still validates against the old v1.2 enum (GENERAL/LOCAL/VPC_ENDPOINT) -> a PRIVATE-typed subnet is a live, billable resource with no GET path (create/delete plane version != read plane version). Live-probed req-65a36c09..., 실측 2026-07-16; scenario workaround is a 30s blind settle instead of show-poll.
  - 기대 동작: 조회 계열 enum을 생성 계열과 동일 버전으로 정합

## 5. 실측 로데이터 (요청/응답 원문)

### compute/scf — 유효 런타임 셋과 문서·에러 메시지 불일치
문서 예시와 동일한 runtime 값("Node.js:20")을 400으로 거절하면서, 에러 메시지에는 그 값을 유효 예시로 인용. 유효 런타임 변경이 문서와 에러 메시지에 반영되지 않음.

```
POST /v1/cloud-functions
요청: {"name": "regrscf99c369a3", "runtime": "Node.js:20", "content": "exports.handler = async () => ({ statusCode: 200, body: 'regr' });"}
응답: HTTP 400
{"errors":[{"code":"scp-cloud-function.invalid-runtime","detail":"Invalid runtime. Runtime should be in the format '{runtime}:{version}', such as 'Node.js:20'.","global_request_id":"req-7331d2ba-1698-49a0-9761-fd73cecfc196","links":[],"related_resources":[],"request_id":"req-c1f3d8f6-0a4e-4d4c-8e3c-9646d7ed1e2b","response":null,"status":400,"title":"InvalidRuntimeError"}]}

```

### security/kms — 정상 요청에 500
문서 스펙대로의 키 생성 요청에 500 "Vault error". 클라이언트가 조치할 방법이 없는 응답.

```
POST /v1/kms/transit
요청: {"auto_rotate": "Y", "description": "API regression suite (crypto)", "key_type": "advanced", "name": "regrkmsc99c59c33", "purpose": "rsa-2048", "rotate_cycle": 7}
응답: HTTP 500
{"errors":[{"code":"scp-security.kms.vault-error","detail":"Vault error","global_request_id":"req-2d747a7b-549e-4635-860d-bc6f861b5b95","links":[],"related_resources":[],"request_id":"req-ccad9686-5efc-461a-93c5-20930475624a","response":null,"status":500,"title":"KmsVaultError"}]}

```

### application-service/apigateway — 승인 처리에 500
PrivateLink Endpoint 승인 PUT이 500 ContactAdminForAssistance. 동일 계열(클라이언트 상태에 500)이 9개 API에서 재현됨.

```
PUT /v1/privatelink-endpoints/{ple_id}/approval
요청: {"api_id": "e7b61e90255b4ee9a4362c71", "type": "APPROVE"}
응답: HTTP 500
{"errors":[{"code":"ContactAdminForAssistance","detail":"There was a problem processing your request.\nContact us through Support Center > Contact Us.","global_request_id":"req-07496a45-a460-42de-bb75-bc37b9eb2cae","links":[],"related_resources":[],"request_id":"req-dc07979c-f024-4eb9-b147-35eb61e6d7f8","response":null,"status":500,"title":"ContactAdminForAssistance"}]}

```

## 5.5 에러 엔벨로프 실태 (실응답 원문)

한 클라이언트가 SCP 에러를 처리하려면 현재 최소 3가지 형태를 알아야 합니다. 전부 2026-08-20 실측 원문입니다.

### ① 미인증 경로 — 프레임워크(Spring) 기본 엔벨로프, 58개 서비스 전원 (예외 0)
무서명 요청에 표준 errors[]가 아닌 프레임워크 기본 바디 + 401이 아닌 404. **58개 서비스 전원이 동일 형태이고, requestId 프리픽스가 서비스 간 재사용됨**(예: 1d493ca5-가 apigateway·queueservice에서, a395fa31-이 scr·data-ops에서 동일) — 개별 서비스가 아니라 **공통 API 게이트웨이(Spring Cloud Gateway 계열)의 기본 에러 핸들러**가 내는 응답으로 판단됩니다. 수정 주체 = 게이트웨이 1곳: 인증 실패를 401 + 표준 errors[] 엔벨로프로 변환.

```
{"timestamp":"2026-08-20T10:11:36.800+00:00","path":"/v1/queues","status":404,"error":"Not Found","requestId":"1d493ca5-2809043"}
```
대상: ai-ml/aimlops-platform, ai-ml/cloud-ml, application-service/apigateway, application-service/queueservice, compute/baremetal, compute/multinodegpucluster, compute/scf, compute/virtualserver, container/scr, container/ske, data-analytics/data-flow, data-analytics/data-ops, data-analytics/eventstreams, data-analytics/quick-query, data-analytics/searchengine, data-analytics/vertica, database/cachestore, database/epas, database/mariadb, database/mysql, database/postgresql, database/sqlserver, devops-tools/devopsservice, financial-management/billingplan, financial-management/budget, financial-management/costexplorer, financial-management/pricing, management/cloudcontrol, management/cloudmonitoring, management/iam, management/iam-identity-center, management/loggingaudit, management/network-logging, management/organization, management/quota, management/resourcemanager, management/servicewatch, management/support, networking/cdn, networking/direct-connect, networking/dns, networking/firewall, networking/gslb, networking/loadbalancer, networking/security-group, networking/vpc, networking/vpn, platform/product, security/certificatemanager, security/configinspection, security/kms, security/secretsmanager, security/secretvault, storage/archivestorage, storage/backup, storage/baremetal-blockstorage, storage/filestorage, storage/parallel-filestorage

### ② 정상 인증 4xx — 표준 errors[] 엔벨로프 (이 형태가 정답이나, 문서에 스키마 없음)
```
{"errors":[{"code":"ValidationError","detail":["Field required"],"global_request_id":"req-fb079650-e947-4ca0-84ee-8c1b5591bf76","links":[],"related_resources":[
```
필드: code / detail / global_request_id / links / related_resources / request_id / status / title — **이 스키마를 API Reference에 공식 문서화하는 것이 개선의 출발점.**

### ③ 같은 표준 엔벨로프 안에서도 detail 타입 혼재
- JSON 배열(표준): **131건** — `"detail":["Field required"]`
- 문자열: **4건** — `financial-management/billingplan/createplannedcomputes`, `financial-management/billingplan/showcancellationfee`, `financial-management/budget/createaccountbudget`, `management/iam/accesskeysendtemporaryotp`

**즉시 수정 대상 — billingplan 2건은 파이썬 리스트의 repr을 문자열로 직렬화해 반환** (서버 내부 표현 누출, 필드 단위 파싱 불가):

```
{"errors":[{"code":"ValidationError","detail":"['Field required', 'Field required', 'Field required', 'Field required']","global_request_id":"req-b6280fd2-7848-
```
→ RED `errors.detail-python-repr` 로 등재: createplannedcomputes, showcancellationfee. (budget/iam의 문자열 detail 2건은 자연어 문장이라 타입 혼재 항목으로만 분류)

### ④ 엣지(WAF) 유량 차단 — HTML 페이지 (특정 API 아님, 부록성)
단시간 다수 요청 시 플랫폼 앞단 WAF가 417 + HTML "Request Rejected"(F5 계열) 반환 — 관측 지점은 SCR 생성이었으나 엣지 공통 동작. 차단 시에도 JSON 엔벨로프 유지가 바람직하나 WAF 관행상 논쟁적.

## 6. 전체 결함 목록 (YELLOW 포함, 상품군→상품별)

### ai-ml

#### ai-ml/aimlops-platform — 8건

| API | 심각도 | 문제 | 근거 |
|---|---|---|---|
| `GET /v1/aimlops-platform/clusters/{cluster_id}/check-version`<br>(checkaimlopsplatformversionv1) | YELLOW | 형식이 유효한 존재하지-않는 id 조회에 404가 아닌 400/403 반환 — 클라이언트가 "형식 오류/권한 문제/부재"를 구분할 수 없음. 참고: 같은 플랫폼에서 192개 API는 404를 반환하므로 (존재여부 은닉 등) 의도된 정책이 아니라 비일관 | non-existent id -> 400 (not 404) |
| `GET /v1/aimlops-platform/internal/clusters/{cluster_id}/nodes`<br>(getaimlopsplatformnodelistv1) | YELLOW | DEPRECATED 표기만 있고 대체 API 안내 없음 | DEPRECATED endpoint |
| `GET /v1/aimlops-platform/internal/clusters/{cluster_id}/nodes`<br>(getaimlopsplatformnodelistv1) | YELLOW | 형식이 유효한 존재하지-않는 id 조회에 404가 아닌 400/403 반환 — 클라이언트가 "형식 오류/권한 문제/부재"를 구분할 수 없음. 참고: 같은 플랫폼에서 192개 API는 404를 반환하므로 (존재여부 은닉 등) 의도된 정책이 아니라 비일관 | non-existent id -> 400 (not 404) |
| `GET /v1/aimlops-platform/internal/clusters/{cluster_id}/storageclasses`<br>(getaimlopsplatformstorageclasseslistv1) | YELLOW | DEPRECATED 표기만 있고 대체 API 안내 없음 | DEPRECATED endpoint |
| `GET /v1/aimlops-platform/internal/clusters/{cluster_id}/storageclasses`<br>(getaimlopsplatformstorageclasseslistv1) | YELLOW | 형식이 유효한 존재하지-않는 id 조회에 404가 아닌 400/403 반환 — 클라이언트가 "형식 오류/권한 문제/부재"를 구분할 수 없음. 참고: 같은 플랫폼에서 192개 API는 404를 반환하므로 (존재여부 은닉 등) 의도된 정책이 아니라 비일관 | non-existent id -> 400 (not 404) |
| `GET /v1/aimlops-platform/{release_id}`<br>(getaimlopsplatformv1) | YELLOW | 형식이 유효한 존재하지-않는 id 조회에 404가 아닌 400/403 반환 — 클라이언트가 "형식 오류/권한 문제/부재"를 구분할 수 없음. 참고: 같은 플랫폼에서 192개 API는 404를 반환하므로 (존재여부 은닉 등) 의도된 정책이 아니라 비일관 | non-existent id -> 400 (not 404) |
| `GET /v1/aimlops-platform/clusters/{cluster_id}/validate-namespaces`<br>(validateclusternamespaceforaimlopsplatformv1) | YELLOW | 형식이 유효한 존재하지-않는 id 조회에 404가 아닌 400/403 반환 — 클라이언트가 "형식 오류/권한 문제/부재"를 구분할 수 없음. 참고: 같은 플랫폼에서 192개 API는 404를 반환하므로 (존재여부 은닉 등) 의도된 정책이 아니라 비일관 | non-existent id -> 400 (not 404) |
| `GET /v1/aimlops-platform/clusters/{cluster_id}/validate-resources`<br>(validateclusterresourcesizeforaimlopsplatformv1) | YELLOW | 형식이 유효한 존재하지-않는 id 조회에 404가 아닌 400/403 반환 — 클라이언트가 "형식 오류/권한 문제/부재"를 구분할 수 없음. 참고: 같은 플랫폼에서 192개 API는 404를 반환하므로 (존재여부 은닉 등) 의도된 정책이 아니라 비일관 | non-existent id -> 400 (not 404) |

#### ai-ml/cloud-ml — 2건

| API | 심각도 | 문제 | 근거 |
|---|---|---|---|
| `POST /v1/cloud-ml`<br>(createcloudml) | YELLOW | 필수 파라미터의 값 형식·제약·출처가 API Reference에 없음 | required fields with no documented constraint: cloud_ml_name, cluster_id, custom_registry_access_key, custom_registry_access_secret_key, custom_registry_host, domain_name, endpoint_type, image_id |
| `PUT /v1/cloud-ml/{cloud_ml_id}`<br>(updatecloudml) | YELLOW | 필수 파라미터의 값 형식·제약·출처가 API Reference에 없음 | required fields with no documented constraint: description |

### application-service

#### application-service/apigateway — 26건

| API | 심각도 | 문제 | 근거 |
|---|---|---|---|
| `PUT /v1/privatelink-endpoints/{privatelink_endpoint_id}/approval`<br>(approveprivatelinkendpoint) | RED | 클라이언트가 유발한 상태·입력에 500 반환 | PUT approve on a PrivateLink Endpoint whose request/cancel already AUTO-approved it (state already APPROVED, so REQUESTED/REJECTED are never reached) -> 500 ContactAdminForAssistance instead of 400 invalid-state. req-e619b286..., 실측 2026-07-16. |
| `PUT /v1/privatelink-endpoints/{privatelink_endpoint_id}/approval`<br>(approveprivatelinkendpoint) | YELLOW | 필수 파라미터의 값 형식·제약·출처가 API Reference에 없음 | required fields with no documented constraint: api_id |
| `PUT /v1/privatelink-endpoints/{privatelink_endpoint_id}/connection`<br>(connectprivatelinkendpoint) | YELLOW | 필수 파라미터의 값 형식·제약·출처가 API Reference에 없음 | required fields with no documented constraint: api_id |
| `POST /v1/apis/{api_id}/access-controls`<br>(createaccesscontrols) | YELLOW | 필수 파라미터의 값 형식·제약·출처가 API Reference에 없음 | required fields with no documented constraint: name |
| `POST /v1/apis`<br>(createapi) | YELLOW | 필수 파라미터의 값 형식·제약·출처가 API Reference에 없음 | required fields with no documented constraint: name |
| `POST /v1/apis`<br>(createapi) | YELLOW | 실제 응답에 문서에 없는 필드 존재 | response has undocumented field(s): endpoint_type |
| `POST /v1/apis/{api_id}/usage-plans/{usage_plan_id}/api-keys`<br>(createapikey) | YELLOW | 필수 파라미터의 값 형식·제약·출처가 API Reference에 없음 | required fields with no documented constraint: name |
| `POST /v1/apis/{api_id}/auths`<br>(createauth) | YELLOW | 필수 파라미터의 값 형식·제약·출처가 API Reference에 없음 | required fields with no documented constraint: name |
| `POST /v1/privatelink-endpoints`<br>(createprivatelinkendpoint) | YELLOW | 필수 파라미터의 값 형식·제약·출처가 API Reference에 없음 | required fields with no documented constraint: name, privatelink_service_id |
| `POST /v1/apis/{api_id}/resources/{parent_id}`<br>(createresource) | YELLOW | 필수 파라미터의 값 형식·제약·출처가 API Reference에 없음 | required fields with no documented constraint: path_part |
| `POST /v1/apis/{api_id}/stages`<br>(createstage) | YELLOW | 필수 파라미터의 값 형식·제약·출처가 API Reference에 없음 | required fields with no documented constraint: deployment_id, stage_name |
| `POST /v1/apis/{api_id}/usage-plans`<br>(createusageplan) | YELLOW | 필수 파라미터의 값 형식·제약·출처가 API Reference에 없음 | required fields with no documented constraint: name |
| `GET /v1/apis/{api_id}/access-controls`<br>(listaccesscontrols) | YELLOW | 형식이 유효한 존재하지-않는 id 조회에 404가 아닌 400/403 반환 — 클라이언트가 "형식 오류/권한 문제/부재"를 구분할 수 없음. 참고: 같은 플랫폼에서 192개 API는 404를 반환하므로 (존재여부 은닉 등) 의도된 정책이 아니라 비일관 | non-existent id -> 403 (not 404) |
| `GET /v1/apis/{api_id}/deployments`<br>(listapideployments) | YELLOW | 형식이 유효한 존재하지-않는 id 조회에 404가 아닌 400/403 반환 — 클라이언트가 "형식 오류/권한 문제/부재"를 구분할 수 없음. 참고: 같은 플랫폼에서 192개 API는 404를 반환하므로 (존재여부 은닉 등) 의도된 정책이 아니라 비일관 | non-existent id -> 403 (not 404) |
| `GET /v1/apis`<br>(listapis) | YELLOW | 실제 응답에 문서에 없는 필드 존재 | response has undocumented field(s): endpoint_type |
| `GET /v1/apis/{api_id}/auths`<br>(listauths) | YELLOW | 형식이 유효한 존재하지-않는 id 조회에 404가 아닌 400/403 반환 — 클라이언트가 "형식 오류/권한 문제/부재"를 구분할 수 없음. 참고: 같은 플랫폼에서 192개 API는 404를 반환하므로 (존재여부 은닉 등) 의도된 정책이 아니라 비일관 | non-existent id -> 403 (not 404) |
| `GET /v1/apis/{api_id}/connected-endpoints`<br>(listconnectedprivatelinkendpoints) | YELLOW | 형식이 유효한 존재하지-않는 id 조회에 404가 아닌 400/403 반환 — 클라이언트가 "형식 오류/권한 문제/부재"를 구분할 수 없음. 참고: 같은 플랫폼에서 192개 API는 404를 반환하므로 (존재여부 은닉 등) 의도된 정책이 아니라 비일관 | non-existent id -> 403 (not 404) |
| `GET /v1/apis/{api_id}/reports`<br>(listreports) | YELLOW | 형식이 유효한 존재하지-않는 id 조회에 404가 아닌 400/403 반환 — 클라이언트가 "형식 오류/권한 문제/부재"를 구분할 수 없음. 참고: 같은 플랫폼에서 192개 API는 404를 반환하므로 (존재여부 은닉 등) 의도된 정책이 아니라 비일관 | non-existent id -> 403 (not 404) |
| `GET /v1/apis/{api_id}/resources`<br>(listresources) | YELLOW | 형식이 유효한 존재하지-않는 id 조회에 404가 아닌 400/403 반환 — 클라이언트가 "형식 오류/권한 문제/부재"를 구분할 수 없음. 참고: 같은 플랫폼에서 192개 API는 404를 반환하므로 (존재여부 은닉 등) 의도된 정책이 아니라 비일관 | non-existent id -> 403 (not 404) |
| `GET /v1/apis/{api_id}/stages`<br>(liststages) | YELLOW | 형식이 유효한 존재하지-않는 id 조회에 404가 아닌 400/403 반환 — 클라이언트가 "형식 오류/권한 문제/부재"를 구분할 수 없음. 참고: 같은 플랫폼에서 192개 API는 404를 반환하므로 (존재여부 은닉 등) 의도된 정책이 아니라 비일관 | non-existent id -> 403 (not 404) |
| `GET /v1/apis/{api_id}/usage-plans`<br>(listusageplans) | YELLOW | 형식이 유효한 존재하지-않는 id 조회에 404가 아닌 400/403 반환 — 클라이언트가 "형식 오류/권한 문제/부재"를 구분할 수 없음. 참고: 같은 플랫폼에서 192개 API는 404를 반환하므로 (존재여부 은닉 등) 의도된 정책이 아니라 비일관 | non-existent id -> 403 (not 404) |
| `PUT /v1/apis/{api_id}/stages/{stage_name}/deployment`<br>(setstageactivedeployment) | YELLOW | 필수 파라미터의 값 형식·제약·출처가 API Reference에 없음 | required fields with no documented constraint: deployment_id |
| `GET /v1/apis/{api_id}`<br>(showapi) | YELLOW | 형식이 유효한 존재하지-않는 id 조회에 404가 아닌 400/403 반환 — 클라이언트가 "형식 오류/권한 문제/부재"를 구분할 수 없음. 참고: 같은 플랫폼에서 192개 API는 404를 반환하므로 (존재여부 은닉 등) 의도된 정책이 아니라 비일관 | non-existent id -> 403 (not 404) |
| `GET /v1/apis/{api_id}`<br>(showapi) | YELLOW | 실제 응답에 문서에 없는 필드 존재 | response has undocumented field(s): endpoint_type,privatelink_service_id |
| `GET /v1/privatelink-endpoints/{privatelink_endpoint_id}`<br>(showprivatelinkendpoint) | YELLOW | 형식이 유효한 존재하지-않는 id 조회에 404가 아닌 400/403 반환 — 클라이언트가 "형식 오류/권한 문제/부재"를 구분할 수 없음. 참고: 같은 플랫폼에서 192개 API는 404를 반환하므로 (존재여부 은닉 등) 의도된 정책이 아니라 비일관 | non-existent id -> 403 (not 404) |
| `GET /v1/apis/{api_id}/resource-policies`<br>(showresourcepolicy) | YELLOW | 형식이 유효한 존재하지-않는 id 조회에 404가 아닌 400/403 반환 — 클라이언트가 "형식 오류/권한 문제/부재"를 구분할 수 없음. 참고: 같은 플랫폼에서 192개 API는 404를 반환하므로 (존재여부 은닉 등) 의도된 정책이 아니라 비일관 | non-existent id -> 403 (not 404) |

#### application-service/queueservice — 4건

| API | 심각도 | 문제 | 근거 |
|---|---|---|---|
| `POST /v1/queues`<br>(createqueue) | YELLOW | 필수 파라미터의 값 형식·제약·출처가 API Reference에 없음 | required fields with no documented constraint: name |
| `GET /v1/queues/{queue_id}/attributes`<br>(getqueueattributes) | YELLOW | 형식이 유효한 존재하지-않는 id 조회에 404가 아닌 400/403 반환 — 클라이언트가 "형식 오류/권한 문제/부재"를 구분할 수 없음. 참고: 같은 플랫폼에서 192개 API는 404를 반환하므로 (존재여부 은닉 등) 의도된 정책이 아니라 비일관 | non-existent id -> 400 (not 404) |
| `GET /v1/queues/{queue_id}`<br>(showqueue) | YELLOW | 형식이 유효한 존재하지-않는 id 조회에 404가 아닌 400/403 반환 — 클라이언트가 "형식 오류/권한 문제/부재"를 구분할 수 없음. 참고: 같은 플랫폼에서 192개 API는 404를 반환하므로 (존재여부 은닉 등) 의도된 정책이 아니라 비일관 | non-existent id -> 400 (not 404) |
| `PUT /v1/queues/{queue_id}/description`<br>(updatequeuedescription) | YELLOW | 필수 파라미터의 값 형식·제약·출처가 API Reference에 없음 | required fields with no documented constraint: description |

### compute

#### compute/baremetal — 6건

| API | 심각도 | 문제 | 근거 |
|---|---|---|---|
| `POST /v1/baremetals/{baremetal_id}/private-nat-ips`<br>(assignbaremetalprivatenatip) | YELLOW | 필수 파라미터의 값 형식·제약·출처가 API Reference에 없음 | required fields with no documented constraint: private_nat_id, private_nat_ip_id |
| `POST /v1/baremetals/{baremetal_id}/public-nat-ips`<br>(assignbaremetalpublicnatip) | YELLOW | 필수 파라미터의 값 형식·제약·출처가 API Reference에 없음 | required fields with no documented constraint: public_ip_address_id |
| `PUT /v1/baremetals/local-subnet/{baremetal_id}/attach`<br>(attachlocalsubnetbaremetal) | YELLOW | 필수 파라미터의 값 형식·제약·출처가 API Reference에 없음 | required fields with no documented constraint: local_subnet_id |
| `POST /v1/baremetals`<br>(createbaremetals) | YELLOW | 필수 파라미터의 값 형식·제약·출처가 API Reference에 없음 | required fields with no documented constraint: region_id, image_id, os_user_id, os_user_password, vpc_id, subnet_id |
| `PUT /v1/baremetals/local-subnet/{baremetal_id}/detach`<br>(detachlocalsubnetbaremetal) | YELLOW | 필수 파라미터의 값 형식·제약·출처가 API Reference에 없음 | required fields with no documented constraint: local_subnet_id, local_subnet_ip |
| `GET /v1/bm_products`<br>(listbaremetalproducts) | YELLOW | 목록 조회에 ?size=1을 보내도 전량 반환(size 무시 — 실측: listservertypes 121개, listbillingitemids 276개), 또는 페이징 메타(count/total) 부재로 클라이언트가 순회 종료 시점을 알 수 없음 | ignores size=1 (returned 16) |

#### compute/multinodegpucluster — 4건

| API | 심각도 | 문제 | 근거 |
|---|---|---|---|
| `POST /v1/gpu-nodes/{gpu_node_id}/public-nat-ip`<br>(assigngpunodepublicnatip) | YELLOW | 필수 파라미터의 값 형식·제약·출처가 API Reference에 없음 | required fields with no documented constraint: public_ip_address_id |
| `POST /v1/gpu-nodes`<br>(creategpunodes) | YELLOW | 필수 파라미터의 값 형식·제약·출처가 API Reference에 없음 | required fields with no documented constraint: region_id, image_id, os_user_id, os_user_password, vpc_id, subnet_id |
| `GET /v1/gpu-nodes/products`<br>(listgpunodeproducts) | YELLOW | 목록 조회에 ?size=1을 보내도 전량 반환(size 무시 — 실측: listservertypes 121개, listbillingitemids 276개), 또는 페이징 메타(count/total) 부재로 클라이언트가 순회 종료 시점을 알 수 없음 | ignores size=1 (returned 2) |
| `POST /v1/cluster-fabrics/modify-members`<br>(modifyclusterfabricmembers) | YELLOW | 필수 파라미터의 값 형식·제약·출처가 API Reference에 없음 | required fields with no documented constraint: after_cluster_fabric_id, before_cluster_fabric_id |

#### compute/scf — 21건

| API | 심각도 | 문제 | 근거 |
|---|---|---|---|
| `POST /v1/cloud-functions`<br>(createcloudfunction) | YELLOW | 필수 파라미터의 값 형식·제약·출처가 API Reference에 없음 | required fields with no documented constraint: content, name, runtime |
| `POST /v1/triggers/apigateway`<br>(createcloudfunctionapigatewaytrigger) | YELLOW | 필수 파라미터의 값 형식·제약·출처가 API Reference에 없음 | required fields with no documented constraint: apigateway_api_id, apigateway_stage_name, cloud_function_id |
| `POST /v1/triggers/cronjob`<br>(createcloudfunctioncronjobtrigger) | YELLOW | 필수 파라미터의 값 형식·제약·출처가 API Reference에 없음 | required fields with no documented constraint: cloud_function_id, schedule, timezone |
| `GET /v1/cloud-functions/{cloud_function_id}/configurations/environment-variables`<br>(listenvironmentvariables) | YELLOW | 형식이 유효한 존재하지-않는 id 조회에 404가 아닌 400/403 반환 — 클라이언트가 "형식 오류/권한 문제/부재"를 구분할 수 없음. 참고: 같은 플랫폼에서 192개 API는 404를 반환하므로 (존재여부 은닉 등) 의도된 정책이 아니라 비일관 | non-existent id -> 403 (not 404) |
| `GET /v1/cloud-functions/{cloud_function_id}/configurations/privatelink-endpoints`<br>(listprivatelinkendpoint) | YELLOW | 형식이 유효한 존재하지-않는 id 조회에 404가 아닌 400/403 반환 — 클라이언트가 "형식 오류/권한 문제/부재"를 구분할 수 없음. 참고: 같은 플랫폼에서 192개 API는 404를 반환하므로 (존재여부 은닉 등) 의도된 정책이 아니라 비일관 | non-existent id -> 403 (not 404) |
| `GET /v1/cloud-functions/runtimes`<br>(listruntimes) | YELLOW | 목록 조회에 ?size=1을 보내도 전량 반환(size 무시 — 실측: listservertypes 121개, listbillingitemids 276개), 또는 페이징 메타(count/total) 부재로 클라이언트가 순회 종료 시점을 알 수 없음 | ignores size=1 (returned 9) |
| `GET /v1/cloud-functions/sample-codes`<br>(listsamplecodes) | YELLOW | 목록 조회에 ?size=1을 보내도 전량 반환(size 무시 — 실측: listservertypes 121개, listbillingitemids 276개), 또는 페이징 메타(count/total) 부재로 클라이언트가 순회 종료 시점을 알 수 없음 | ignores size=1 (returned 39) |
| `PUT /v1/cloud-functions/{cloud_function_id}/codes/file`<br>(setcloudfunctioncodefile) | YELLOW | 필수 파라미터의 값 형식·제약·출처가 API Reference에 없음 | required fields with no documented constraint: class_name, method_name |
| `GET /v1/cloud-functions/{cloud_function_id}`<br>(showcloudfunction) | YELLOW | 형식이 유효한 존재하지-않는 id 조회에 404가 아닌 400/403 반환 — 클라이언트가 "형식 오류/권한 문제/부재"를 구분할 수 없음. 참고: 같은 플랫폼에서 192개 API는 404를 반환하므로 (존재여부 은닉 등) 의도된 정책이 아니라 비일관 | non-existent id -> 403 (not 404) |
| `GET /v1/cloud-functions/{cloud_function_id}/codes`<br>(showcloudfunctioncode) | YELLOW | 형식이 유효한 존재하지-않는 id 조회에 404가 아닌 400/403 반환 — 클라이언트가 "형식 오류/권한 문제/부재"를 구분할 수 없음. 참고: 같은 플랫폼에서 192개 API는 404를 반환하므로 (존재여부 은닉 등) 의도된 정책이 아니라 비일관 | non-existent id -> 403 (not 404) |
| `GET /v1/cloud-functions/{cloud_function_id}/configurations`<br>(showcloudfunctionconfiguration) | YELLOW | 형식이 유효한 존재하지-않는 id 조회에 404가 아닌 400/403 반환 — 클라이언트가 "형식 오류/권한 문제/부재"를 구분할 수 없음. 참고: 같은 플랫폼에서 192개 API는 404를 반환하므로 (존재여부 은닉 등) 의도된 정책이 아니라 비일관 | non-existent id -> 403 (not 404) |
| `GET /v1/cloud-functions/{cloud_function_id}/logs`<br>(showcloudfunctionlogs) | YELLOW | 형식이 유효한 존재하지-않는 id 조회에 404가 아닌 400/403 반환 — 클라이언트가 "형식 오류/권한 문제/부재"를 구분할 수 없음. 참고: 같은 플랫폼에서 192개 API는 404를 반환하므로 (존재여부 은닉 등) 의도된 정책이 아니라 비일관 | non-existent id -> 403 (not 404) |
| `GET /v1/cloud-functions/{cloud_function_id}/metrics`<br>(showcloudfunctionmetrics) | YELLOW | 형식이 유효한 존재하지-않는 id 조회에 404가 아닌 400/403 반환 — 클라이언트가 "형식 오류/권한 문제/부재"를 구분할 수 없음. 참고: 같은 플랫폼에서 192개 API는 404를 반환하므로 (존재여부 은닉 등) 의도된 정책이 아니라 비일관 | non-existent id -> 403 (not 404) |
| `GET /v1/cloud-functions/{cloud_function_id}/metrics`<br>(showcloudfunctionmetrics) | YELLOW | 문서에 명시된 API 버전을 서버가 406으로 거절 | docs-derived pin 'scf metrics 1.3' -> 406 NoSuchVersion against the product pin (1.4); served via the no-pin fallback (latest current). Confirmed 실측 2026-07-16. |
| `GET /v1/triggers/{trigger_id}`<br>(showcloudfunctiontrigger) | YELLOW | 형식이 유효한 존재하지-않는 id 조회에 404가 아닌 400/403 반환 — 클라이언트가 "형식 오류/권한 문제/부재"를 구분할 수 없음. 참고: 같은 플랫폼에서 192개 API는 404를 반환하므로 (존재여부 은닉 등) 의도된 정책이 아니라 비일관 | non-existent id -> 400 (not 404) |
| `GET /v1/cloud-functions/{cloud_function_id}/configurations/config`<br>(showgeneralconfig) | YELLOW | 형식이 유효한 존재하지-않는 id 조회에 404가 아닌 400/403 반환 — 클라이언트가 "형식 오류/권한 문제/부재"를 구분할 수 없음. 참고: 같은 플랫폼에서 192개 API는 404를 반환하므로 (존재여부 은닉 등) 의도된 정책이 아니라 비일관 | non-existent id -> 403 (not 404) |
| `GET /v1/cloud-functions/{cloud_function_id}/configurations/privatelink-services`<br>(showprivatelinkservice) | YELLOW | 형식이 유효한 존재하지-않는 id 조회에 404가 아닌 400/403 반환 — 클라이언트가 "형식 오류/권한 문제/부재"를 구분할 수 없음. 참고: 같은 플랫폼에서 192개 API는 404를 반환하므로 (존재여부 은닉 등) 의도된 정책이 아니라 비일관 | non-existent id -> 403 (not 404) |
| `GET /v1/cloud-functions/{cloud_function_id}/configurations/resource-policies`<br>(showresourcepolicy) | YELLOW | 형식이 유효한 존재하지-않는 id 조회에 404가 아닌 400/403 반환 — 클라이언트가 "형식 오류/권한 문제/부재"를 구분할 수 없음. 참고: 같은 플랫폼에서 192개 API는 404를 반환하므로 (존재여부 은닉 등) 의도된 정책이 아니라 비일관 | non-existent id -> 403 (not 404) |
| `GET /v1/cloud-functions/{cloud_function_id}/configurations/url`<br>(showurlconfig) | YELLOW | 형식이 유효한 존재하지-않는 id 조회에 404가 아닌 400/403 반환 — 클라이언트가 "형식 오류/권한 문제/부재"를 구분할 수 없음. 참고: 같은 플랫폼에서 192개 API는 404를 반환하므로 (존재여부 은닉 등) 의도된 정책이 아니라 비일관 | non-existent id -> 403 (not 404) |
| `PUT /v1/cloud-functions/{cloud_function_id}/codes`<br>(updatecloudfunctioncode) | YELLOW | 필수 파라미터의 값 형식·제약·출처가 API Reference에 없음 | required fields with no documented constraint: content |
| `PUT /v1/triggers/cronjob/{trigger_id}`<br>(updatecloudfunctioncronjobtrigger) | YELLOW | 필수 파라미터의 값 형식·제약·출처가 API Reference에 없음 | required fields with no documented constraint: cloud_function_id, schedule, timezone |

#### compute/virtualserver — 57건

| API | 심각도 | 문제 | 근거 |
|---|---|---|---|
| `POST /v1/images/{image_id}/share`<br>(createsharingimage) | RED | 공유 과정에서 생성된 임시 볼륨이 공유 중단 시 삭제 불가능(400 반복) 상태로 잔존 | The hex-named 104GB temp volume createsharingimage spawns in the recipient account has no API-reachable cleanup path once its share record vanishes (source deleted mid-transfer, or recipient never acts): DELETE permanently 400s (VolumeForSharingImageDelete, "t |
| `DELETE /v1/images/{image_id}`<br>(deleteimage) | RED | 공유 전송 중인 원본 이미지 삭제가 차단 없이 성공(204) → 파생 임시 볼륨 영구 고아화 | DELETE on the source image of an in-flight createsharingimage share succeeds (204) with no guard rejecting it — deleting the source ~2m16s into a still-pending share orphans the derived temp volume permanently (see compute.image-sharing-orphan-volume-no-cleanu |
| `POST /v1/volume-transfer/{transfer_id}/accept`<br>(acceptvolumetransfer) | YELLOW | 필수 파라미터의 값 형식·제약·출처가 API Reference에 없음 | required fields with no documented constraint: auth_key |
| `POST /v1/servers/{server_id}/security-groups`<br>(attachvirtualserversecuritygroup) | YELLOW | 성공(2xx) 응답 스키마가 문서에 없음 | POST 2xx documents no schema |
| `POST /v1/servers/{server_id}/security-groups`<br>(attachvirtualserversecuritygroup) | YELLOW | DEPRECATED 표기만 있고 대체 API 안내 없음 | DEPRECATED endpoint |
| `POST /v1/servers/{server_id}/security-groups`<br>(attachvirtualserversecuritygroup) | YELLOW | 필수 파라미터의 값 형식·제약·출처가 API Reference에 없음 | required fields with no documented constraint: security_group_id |
| `POST /v1/volumes/{volume_id}/servers`<br>(attachvolumetovirtualserver) | YELLOW | 필수 파라미터의 값 형식·제약·출처가 API Reference에 없음 | required fields with no documented constraint: server_id |
| `POST /v1/auto-scaling-groups`<br>(createautoscalinggroup) | YELLOW | 필수 파라미터의 값 형식·제약·출처가 API Reference에 없음 | required fields with no documented constraint: launch_configuration_id, name, placement_strategy, server_name_prefix |
| `POST /v1/auto-scaling-groups/{auto_scaling_group_id}/policies`<br>(createautoscalinggrouppolicy) | YELLOW | 필수 파라미터의 값 형식·제약·출처가 API Reference에 없음 | required fields with no documented constraint: comparison_operator, metric_method, metric_type, name, scale_method, scale_type |
| `POST /v1/auto-scaling-groups/{auto_scaling_group_id}/schedules`<br>(createautoscalinggroupschedule) | YELLOW | 필수 파라미터의 값 형식·제약·출처가 API Reference에 없음 | required fields with no documented constraint: name, timezone |
| `POST /v1/images`<br>(createimage) | YELLOW | 필수 파라미터의 값 형식·제약·출처가 API Reference에 없음 | required fields with no documented constraint: name |
| `POST /v1/images/{image_id}/members`<br>(createimagemember) | YELLOW | 필수 파라미터의 값 형식·제약·출처가 API Reference에 없음 | required fields with no documented constraint: member_id |
| `POST /v1/keypairs`<br>(createkeypair) | YELLOW | 필수 파라미터의 값 형식·제약·출처가 API Reference에 없음 | required fields with no documented constraint: name |
| `POST /v1/launch-configurations`<br>(createlaunchconfiguration) | YELLOW | 필수 파라미터의 값 형식·제약·출처가 API Reference에 없음 | required fields with no documented constraint: image_id, keypair_name, name, server_type_id |
| `POST /v1/server-groups`<br>(createservergroup) | YELLOW | 필수 파라미터의 값 형식·제약·출처가 API Reference에 없음 | required fields with no documented constraint: name, policy |
| `POST /v1/servers/{server_id}/interfaces/{port_id}/static-nats`<br>(createserverinterfacenat) | YELLOW | 성공(2xx) 응답 스키마가 문서에 없음 | POST 2xx documents no schema |
| `POST /v1/servers/{server_id}/interfaces/{port_id}/static-nats`<br>(createserverinterfacenat) | YELLOW | 필수 파라미터의 값 형식·제약·출처가 API Reference에 없음 | required fields with no documented constraint: publicip_id |
| `POST /v1/servers/{server_id}/interfaces/{port_id}/private-static-nats`<br>(createserverinterfaceprivatenat) | YELLOW | 성공(2xx) 응답 스키마가 문서에 없음 | POST 2xx documents no schema |
| `POST /v1/servers/{server_id}/interfaces/{port_id}/private-static-nats`<br>(createserverinterfaceprivatenat) | YELLOW | 필수 파라미터의 값 형식·제약·출처가 API Reference에 없음 | required fields with no documented constraint: private_nat_id, private_nat_ip_id |
| `POST /v1/servers/{server_id}/interfaces/{port_id}/private-static-nats`<br>(createserverinterfaceprivatenat) | YELLOW | 입력 검증 오류에 403 반환 (권한 문제로 오인 유발) | A validation error on private-static-nat create -> 403 instead of 400 (a client-input problem misclassified as an authorization failure — the two are not interchangeable for a caller that branches on status family). 실측 2026-07-16. |
| `POST /v1/servers/{server_id}/volumes`<br>(createservervolume) | YELLOW | 필수 파라미터의 값 형식·제약·출처가 API Reference에 없음 | required fields with no documented constraint: volume_id |
| `POST /v1/images/{image_id}/share`<br>(createsharingimage) | YELLOW | 성공(2xx) 응답 스키마가 문서에 없음 | POST 2xx documents no schema |
| `POST /v1/images/{image_id}/share`<br>(createsharingimage) | YELLOW | 공유 시작 202 응답 바디가 비어 있어 진행 추적 수단(공유 ID)이 없음 | POST /v1/images/{id}/share -> 202 with an EMPTY body {} — no tracking handle (share/task id) is returned, so a caller can't correlate the async op with its outcome except by polling the target account's pending-images or watching temp-volume side effects. 실측 타 |
| `POST /v1/images/{image_id}/share`<br>(createsharingimage) | YELLOW | 공유의 수락/거절/취소가 별도 엔드포인트(updateimagemember)에 있음이 해당 문서에 없음 | createsharingimage's own doc page never mentions that the accept/reject/cancel counterpart lives on a DIFFERENT endpoint family — PUT /v1/images/{image_id}/members/{member_id} (updateimagemember, body {"status": pending\|accepted\|rejected}) — not a sibling of t |
| `POST /v1/snapshots`<br>(createsnapshot) | YELLOW | 필수 파라미터의 값 형식·제약·출처가 API Reference에 없음 | required fields with no documented constraint: name, volume_id |
| `POST /v1/servers`<br>(createvirtualserver) | YELLOW | 필수 파라미터의 값 형식·제약·출처가 API Reference에 없음 | required fields with no documented constraint: image_id, keypair_name, name, server_type_id, zone |
| `POST /v1/servers/{server_id}/images`<br>(createvirtualservercustomimage) | YELLOW | 필수 파라미터의 값 형식·제약·출처가 API Reference에 없음 | required fields with no documented constraint: image_name |
| `POST /v1/servers/{server_id}/dump`<br>(createvirtualserverdump) | YELLOW | 성공(2xx) 응답 스키마가 문서에 없음 | POST 2xx documents no schema |
| `POST /v1/volumes`<br>(createvolume) | YELLOW | 필수 파라미터의 값 형식·제약·출처가 API Reference에 없음 | required fields with no documented constraint: name, zone |
| `POST /v1/volume-transfer`<br>(createvolumetransfer) | YELLOW | 필수 파라미터의 값 형식·제약·출처가 API Reference에 없음 | required fields with no documented constraint: volume_id |
| `DELETE /v1/servers/{server_id}/security-groups/{security_group_id}`<br>(detachvirtualserversecuritygroup) | YELLOW | DEPRECATED 표기만 있고 대체 API 안내 없음 | DEPRECATED endpoint |
| `POST /v1/images/{image_id}/import`<br>(importimage) | YELLOW | 성공(2xx) 응답 스키마가 문서에 없음 | POST 2xx documents no schema |
| `GET /v1/auto-scaling-groups/{auto_scaling_group_id}/notifications`<br>(listautoscalinggroupnotifications) | YELLOW | 존재하지 않는 부모의 하위 목록 조회가 200(빈 목록) 반환 | sub-resource list of a non-existent parent -> 200 (empty), not 404 |
| `GET /v1/images`<br>(listimages) | YELLOW | 목록 조회에 ?size=1을 보내도 전량 반환(size 무시 — 실측: listservertypes 121개, listbillingitemids 276개), 또는 페이징 메타(count/total) 부재로 클라이언트가 순회 종료 시점을 알 수 없음 | ignores size=1 (returned 20) |
| `GET /v1/servers/{server_id}/ips`<br>(listserverips) | YELLOW | 문서에 명시된 API 버전을 서버가 406으로 거절 | docs-derived pin 'virtualserver /v1/servers/{id}/ips 1.3' -> 406 NoSuchVersion against the product pin; served via the no-pin fallback. Confirmed 실측 2026-07-16. |
| `GET /v1/server-types`<br>(listservertypes) | YELLOW | 목록 조회에 ?size=1을 보내도 전량 반환(size 무시 — 실측: listservertypes 121개, listbillingitemids 276개), 또는 페이징 메타(count/total) 부재로 클라이언트가 순회 종료 시점을 알 수 없음 | ignores size=1 (returned 121) |
| `GET /v1/volume-types`<br>(listvolumetypes) | YELLOW | 목록 조회에 ?size=1을 보내도 전량 반환(size 무시 — 실측: listservertypes 121개, listbillingitemids 276개), 또는 페이징 메타(count/total) 부재로 클라이언트가 순회 종료 시점을 알 수 없음 | ignores size=1 (returned 4) |
| `POST /v1/servers/{server_id}/lock`<br>(lockvirtualserver) | YELLOW | 성공(2xx) 응답 스키마가 문서에 없음 | POST 2xx documents no schema |
| `POST /v1/servers/{server_id}/reboot`<br>(rebootvirtualserver) | YELLOW | 성공(2xx) 응답 스키마가 문서에 없음 | POST 2xx documents no schema |
| `POST /v1/servers/{server_id}/rebuild`<br>(rebuildvirtualserver) | YELLOW | 성공(2xx) 응답 스키마가 문서에 없음 | POST 2xx documents no schema |
| `POST /v1/volumes/{volume_id}/revert`<br>(revertvolumetosnapshot) | YELLOW | 성공(2xx) 응답 스키마가 문서에 없음 | POST 2xx documents no schema |
| `POST /v1/volumes/{volume_id}/revert`<br>(revertvolumetosnapshot) | YELLOW | 필수 파라미터의 값 형식·제약·출처가 API Reference에 없음 | required fields with no documented constraint: snapshot_id |
| `POST /v1/servers/{server_id}/server-type`<br>(setvirtualservertype) | YELLOW | 성공(2xx) 응답 스키마가 문서에 없음 | POST 2xx documents no schema |
| `POST /v1/servers/{server_id}/server-type`<br>(setvirtualservertype) | YELLOW | 필수 파라미터의 값 형식·제약·출처가 API Reference에 없음 | required fields with no documented constraint: server_type |
| `PUT /v1/volumes/{volume_id}/qos`<br>(setvolumeqos) | YELLOW | 성공(2xx) 응답 스키마가 문서에 없음 | PUT 2xx documents no schema |
| `POST /v1/servers/{server_id}/password`<br>(showvirtualserverpassword) | YELLOW | 엔드포인트 이름의 동사와 HTTP 메서드 불일치 | read-verb name but not GET (POST /v1/servers/{server_id}/password) |
| `GET /v1/volumes/quota-sets`<br>(showvolumequotaset) | YELLOW | 실제 응답에 문서에 없는 필드 존재 | response has undocumented field(s): snapshots_SSD,snapshots_SSD_KMS,snapshots_SSD_MultiAttach,snapshots_SSD_Provisioned,snapshots_hdd0,snapshots_ssd0,usages_SSD,usages_SSD_KMS,usages_SSD_MultiAttach,usages_SSD_Provisioned,usages_hdd0,usages_ssd0,volumes_SSD,vo |
| `POST /v1/servers/{server_id}/start`<br>(startvirtualserver) | YELLOW | 성공(2xx) 응답 스키마가 문서에 없음 | POST 2xx documents no schema |
| `POST /v1/servers/{server_id}/stop`<br>(stopvirtualserver) | YELLOW | 성공(2xx) 응답 스키마가 문서에 없음 | POST 2xx documents no schema |
| `POST /v1/servers/{server_id}/unlock`<br>(unlockvirtualserver) | YELLOW | 성공(2xx) 응답 스키마가 문서에 없음 | POST 2xx documents no schema |
| `PUT /v1/auto-scaling-groups/{auto_scaling_group_id}/lb-server-groups`<br>(updateautoscalinggrouplbservergroups) | YELLOW | 성공(2xx) 응답 스키마가 문서에 없음 | PUT 2xx documents no schema |
| `PUT /v1/servers/{server_id}/interfaces/{port_id}`<br>(updateserverinterface) | YELLOW | 성공(2xx) 응답 스키마가 문서에 없음 | PUT 2xx documents no schema |
| `PUT /v1/servers/{server_id}/interfaces/{port_id}`<br>(updateserverinterface) | YELLOW | 필수 파라미터의 값 형식·제약·출처가 API Reference에 없음 | required fields with no documented constraint: fixed_ip_address |
| `PUT /v1/servers/{server_id}/volumes/{volume_id}`<br>(updateservervolume) | YELLOW | 성공(2xx) 응답 스키마가 문서에 없음 | PUT 2xx documents no schema |
| `PUT /v1/servers/{server_id}/volumes/{volume_id}`<br>(updateservervolume) | YELLOW | 필수 파라미터의 값 형식·제약·출처가 API Reference에 없음 | required fields with no documented constraint: volume_id |
| `PUT /v1/snapshots/{snapshot_id}`<br>(updatesnapshot) | YELLOW | 필수 파라미터의 값 형식·제약·출처가 API Reference에 없음 | required fields with no documented constraint: name |
| `PUT /v1/servers/{server_id}`<br>(updatevirtualserver) | YELLOW | 필수 파라미터의 값 형식·제약·출처가 API Reference에 없음 | required fields with no documented constraint: name |

### container

#### container/scr — 24건

| API | 심각도 | 문제 | 근거 |
|---|---|---|---|
| `GET /v1/container-registries/{registry_id}`<br>(showregistry) | RED | 클라이언트가 유발한 상태·입력에 500 반환 | GET on a registry mid-CREATING -> 500 ContactAdminForAssistance instead of 409/425 (a racing client-visible state, not a true not-found). req-90138294..., live 실측-07-16. Workaround: 500 retry ladder 15s x 10 until ACTIVE. |
| `PUT /v1/tagses/{tags_id}/check-vulnerability`<br>(checktagsvulnerability) | YELLOW | 성공(2xx) 응답 스키마가 문서에 없음 | PUT 2xx documents no schema |
| `POST /v1/container-registries`<br>(createregistry) | YELLOW | 필수 파라미터의 값 형식·제약·출처가 API Reference에 없음 | required fields with no documented constraint: name |
| `POST /v1/container-registries`<br>(createregistry) | YELLOW | 유량 제한 시 JSON 에러 규격이 아닌 HTML 차단 페이지(417) 반환 | 단시간 다수 요청(약 60초 내 80건) 상황에서 the SCP edge WAF answers with 417 + an HTML 'Request Rejected' block page (Support ID 3232170405160507975, F5-style) instead of the platform's JSON error envelope — breaks the 'errors are always JSON' contract an AI/programmatic con |
| `POST /v1/repositories`<br>(createrepository) | YELLOW | 필수 파라미터의 값 형식·제약·출처가 API Reference에 없음 | required fields with no documented constraint: description, name, registry_id |
| `GET /v1/tagses/{tags_id}/download/manifest`<br>(downloadmanifest) | YELLOW | 성공(2xx) 응답 스키마가 문서에 없음 | GET 2xx documents no schema |
| `GET /v1/container-registries/connectable-resources`<br>(listconnectableresources) | YELLOW | 목록 조회에 ?size=1을 보내도 전량 반환(size 무시 — 실측: listservertypes 121개, listbillingitemids 276개), 또는 페이징 메타(count/total) 부재로 클라이언트가 순회 종료 시점을 알 수 없음 | ignores size=1 (returned 2) |
| `POST /v1/images/{image_id}/lifecycle-policy/preview`<br>(runimagelifecyclepolicypreview) | YELLOW | 성공(2xx) 응답 스키마가 문서에 없음 | POST 2xx documents no schema |
| `PUT /v1/images/{image_id}/description`<br>(updateimagedescription) | YELLOW | 성공(2xx) 응답 스키마가 문서에 없음 | PUT 2xx documents no schema |
| `PUT /v1/images/{image_id}/description`<br>(updateimagedescription) | YELLOW | 필수 파라미터의 값 형식·제약·출처가 API Reference에 없음 | required fields with no documented constraint: description |
| `PUT /v1/images/{image_id}/lifecycle-policy`<br>(updateimagelifecyclepolicy) | YELLOW | 성공(2xx) 응답 스키마가 문서에 없음 | PUT 2xx documents no schema |
| `PUT /v1/images/{image_id}/lock-policy`<br>(updateimagelockpolicy) | YELLOW | 성공(2xx) 응답 스키마가 문서에 없음 | PUT 2xx documents no schema |
| `PUT /v1/images/{image_id}/pull-policy`<br>(updateimagepullpolicy) | YELLOW | 성공(2xx) 응답 스키마가 문서에 없음 | PUT 2xx documents no schema |
| `PUT /v1/images/{image_id}/scan-policy`<br>(updateimagescanpolicy) | YELLOW | 성공(2xx) 응답 스키마가 문서에 없음 | PUT 2xx documents no schema |
| `PUT /v1/container-registries/{registry_id}/private-acl`<br>(updateprivateacl) | YELLOW | 성공(2xx) 응답 스키마가 문서에 없음 | PUT 2xx documents no schema |
| `PUT /v1/container-registries/{registry_id}/public-acl`<br>(updatepublicacl) | YELLOW | 성공(2xx) 응답 스키마가 문서에 없음 | PUT 2xx documents no schema |
| `PUT /v1/container-registries/{registry_id}/enable-public-endpoint`<br>(updatepublicendpointenabled) | YELLOW | 성공(2xx) 응답 스키마가 문서에 없음 | PUT 2xx documents no schema |
| `PUT /v1/repositories/{repository_id}/description`<br>(updaterepositorydescription) | YELLOW | 성공(2xx) 응답 스키마가 문서에 없음 | PUT 2xx documents no schema |
| `PUT /v1/repositories/{repository_id}/description`<br>(updaterepositorydescription) | YELLOW | 필수 파라미터의 값 형식·제약·출처가 API Reference에 없음 | required fields with no documented constraint: description |
| `PUT /v1/repositories/{repository_id}/lifecycle-policy`<br>(updaterepositorylifecyclepolicy) | YELLOW | 성공(2xx) 응답 스키마가 문서에 없음 | PUT 2xx documents no schema |
| `PUT /v1/repositories/{repository_id}/lock-policy`<br>(updaterepositorylockpolicy) | YELLOW | 성공(2xx) 응답 스키마가 문서에 없음 | PUT 2xx documents no schema |
| `PUT /v1/repositories/{repository_id}/pull-policy`<br>(updaterepositorypullpolicy) | YELLOW | 성공(2xx) 응답 스키마가 문서에 없음 | PUT 2xx documents no schema |
| `PUT /v1/repositories/{repository_id}/scan-policy`<br>(updaterepositoryscanpolicy) | YELLOW | 성공(2xx) 응답 스키마가 문서에 없음 | PUT 2xx documents no schema |
| `PUT /v1/tagses/{tags_id}/lock-policy`<br>(updatetagslockpolicy) | YELLOW | 성공(2xx) 응답 스키마가 문서에 없음 | PUT 2xx documents no schema |

#### container/ske — 15건

| API | 심각도 | 문제 | 근거 |
|---|---|---|---|
| `POST /v1/nodepools`<br>(createnodepool) | RED | 클라이언트가 유발한 상태·입력에 500 반환 | POST /v1/nodepools -> 500 ContactAdminForAssistance (16.7s) when `zone` is omitted on a single-AZ account (nodepoolcreaterequestv1dot5 added an optional `zone`; unmatched default-zone placement 500s server-side instead of 400 asking for zone). Cluster itself r |
| `POST /v1/clusters`<br>(createcluster) | YELLOW | 필수 파라미터의 값 형식·제약·출처가 API Reference에 없음 | required fields with no documented constraint: kubernetes_version, name, subnet_id, volume_id, vpc_id |
| `GET /v1/clusters/{cluster_id}/kubeconfig`<br>(createclusterkubeconfig) | YELLOW | 엔드포인트 이름의 동사와 HTTP 메서드 불일치 | create-verb name but not POST (GET /v1/clusters/{cluster_id}/kubeconfig) |
| `GET /v1/clusters/{cluster_id}/kubeconfig`<br>(createclusterkubeconfig) | YELLOW | 성공(2xx) 응답 스키마가 문서에 없음 | GET 2xx documents no schema |
| `GET /v1/clusters/{cluster_id}/kubeconfig`<br>(createclusterkubeconfig) | YELLOW | 형식이 유효한 존재하지-않는 id 조회에 404가 아닌 400/403 반환 — 클라이언트가 "형식 오류/권한 문제/부재"를 구분할 수 없음. 참고: 같은 플랫폼에서 192개 API는 404를 반환하므로 (존재여부 은닉 등) 의도된 정책이 아니라 비일관 | non-existent id -> 400 (not 404) |
| `POST /v1/nodepools`<br>(createnodepool) | YELLOW | 필수 파라미터의 값 형식·제약·출처가 API Reference에 없음 | required fields with no documented constraint: cluster_id, image_os, image_os_version, keypair_name, kubernetes_version, name, server_type_id, volume_type_name |
| `GET /v1/kubernetes-versions`<br>(listkubernetesversions) | YELLOW | 목록 조회에 ?size=1을 보내도 전량 반환(size 무시 — 실측: listservertypes 121개, listbillingitemids 276개), 또는 페이징 메타(count/total) 부재로 클라이언트가 순회 종료 시점을 알 수 없음 | ignores size=1 (returned 7) |
| `GET /v1/clusters/{cluster_id}/nodepools`<br>(listnodepools) | YELLOW | 형식이 유효한 존재하지-않는 id 조회에 404가 아닌 400/403 반환 — 클라이언트가 "형식 오류/권한 문제/부재"를 구분할 수 없음. 참고: 같은 플랫폼에서 192개 API는 404를 반환하므로 (존재여부 은닉 등) 의도된 정책이 아니라 비일관 | non-existent id -> 400 (not 404) |
| `PUT /v1/clusters/{cluster_id}/public-access-control`<br>(setclusterpublicaccesscontrol) | YELLOW | 필수 파라미터의 값 형식·제약·출처가 API Reference에 없음 | required fields with no documented constraint: public_endpoint_access_control_ip |
| `PUT /v1/clusters/{cluster_id}/upgrade`<br>(setclusterupgrade) | YELLOW | 필수 파라미터의 값 형식·제약·출처가 API Reference에 없음 | required fields with no documented constraint: kubernetes_version |
| `PUT /v1/nodepools/{nodepool_id}/preferred-ips`<br>(setnodepoolpreferredips) | YELLOW | 필수 파라미터의 값 형식·제약·출처가 API Reference에 없음 | required fields with no documented constraint: preferred_ips |
| `PUT /v1/nodepools/{nodepool_id}/upgrade`<br>(setnodepoolupgrade) | YELLOW | 필수 파라미터의 값 형식·제약·출처가 API Reference에 없음 | required fields with no documented constraint: os_version |
| `GET /v1/clusters/{cluster_id}`<br>(showcluster) | YELLOW | 형식이 유효한 존재하지-않는 id 조회에 404가 아닌 400/403 반환 — 클라이언트가 "형식 오류/권한 문제/부재"를 구분할 수 없음. 참고: 같은 플랫폼에서 192개 API는 404를 반환하므로 (존재여부 은닉 등) 의도된 정책이 아니라 비일관 | non-existent id -> 400 (not 404) |
| `GET /v1/clusters/{cluster_id}/user-kubeconfig`<br>(showclusteruserkubeconfig) | YELLOW | 성공(2xx) 응답 스키마가 문서에 없음 | GET 2xx documents no schema |
| `GET /v1/clusters/{cluster_id}/user-kubeconfig`<br>(showclusteruserkubeconfig) | YELLOW | 형식이 유효한 존재하지-않는 id 조회에 404가 아닌 400/403 반환 — 클라이언트가 "형식 오류/권한 문제/부재"를 구분할 수 없음. 참고: 같은 플랫폼에서 192개 API는 404를 반환하므로 (존재여부 은닉 등) 의도된 정책이 아니라 비일관 | non-existent id -> 400 (not 404) |

### data-analytics

#### data-analytics/data-flow — 9건

| API | 심각도 | 문제 | 근거 |
|---|---|---|---|
| `POST /v1/data-flows`<br>(createdataflow) | YELLOW | 성공(2xx) 응답 스키마가 문서에 없음 | POST 2xx documents no schema |
| `POST /v1/data-flows`<br>(createdataflow) | YELLOW | 필수 파라미터의 값 형식·제약·출처가 API Reference에 없음 | required fields with no documented constraint: cluster_id, data_flow_name, domain, image_id, storage_class_name |
| `POST /v1/data-flow-services`<br>(createdataflowserviceconsole) | YELLOW | 성공(2xx) 응답 스키마가 문서에 없음 | POST 2xx documents no schema |
| `POST /v1/data-flow-services`<br>(createdataflowserviceconsole) | YELLOW | 필수 파라미터의 값 형식·제약·출처가 API Reference에 없음 | required fields with no documented constraint: data_flow_id, data_flow_service_name, domain, storage_class_name |
| `GET /v1/data-flows/{data_flow_id}`<br>(getdataflow) | YELLOW | 형식이 유효한 존재하지-않는 id 조회에 404가 아닌 400/403 반환 — 클라이언트가 "형식 오류/권한 문제/부재"를 구분할 수 없음. 참고: 같은 플랫폼에서 192개 API는 404를 반환하므로 (존재여부 은닉 등) 의도된 정책이 아니라 비일관 | non-existent id -> 400 (not 404) |
| `GET /v1/data-flow-services/{data_flow_service_id}`<br>(getdataflowserviceconsole) | YELLOW | 형식이 유효한 존재하지-않는 id 조회에 404가 아닌 400/403 반환 — 클라이언트가 "형식 오류/권한 문제/부재"를 구분할 수 없음. 참고: 같은 플랫폼에서 192개 API는 404를 반환하므로 (존재여부 은닉 등) 의도된 정책이 아니라 비일관 | non-existent id -> 400 (not 404) |
| `GET /v1/data-flow-services/data-flows/{data_flow_id}/sub-versions`<br>(getdataflowservicesubversions) | YELLOW | 형식이 유효한 존재하지-않는 id 조회에 404가 아닌 400/403 반환 — 클라이언트가 "형식 오류/권한 문제/부재"를 구분할 수 없음. 참고: 같은 플랫폼에서 192개 API는 404를 반환하므로 (존재여부 은닉 등) 의도된 정책이 아니라 비일관 | non-existent id -> 400 (not 404) |
| `GET /v1/data-flows/clusters/{cluster_id}/ingress-controllers`<br>(ingresscluster) | YELLOW | 성공(2xx) 응답 스키마가 문서에 없음 | GET 2xx documents no schema |
| `GET /v1/data-flows/clusters/{cluster_id}/ingress-controllers`<br>(ingresscluster) | YELLOW | 형식이 유효한 존재하지-않는 id 조회에 404가 아닌 400/403 반환 — 클라이언트가 "형식 오류/권한 문제/부재"를 구분할 수 없음. 참고: 같은 플랫폼에서 192개 API는 404를 반환하므로 (존재여부 은닉 등) 의도된 정책이 아니라 비일관 | non-existent id -> 400 (not 404) |

#### data-analytics/data-ops — 14건

| API | 심각도 | 문제 | 근거 |
|---|---|---|---|
| `GET /v1/data-ops/image-versions`<br>(getdataopsimageversionv1) | RED | 문서상 필수 응답 필드가 실제 응답에 없음 | response omits documented required field(s): image_attr |
| `GET /v1/data-ops-services/{data_ops_service_name}/check-duplication`<br>(checkduplicationcontroller) | YELLOW | 성공(2xx) 응답 스키마가 문서에 없음 | GET 2xx documents no schema |
| `GET /v1/data-ops/{data_ops_name}/check-duplication`<br>(checkduplicationcontrollerv1) | YELLOW | 성공(2xx) 응답 스키마가 문서에 없음 | GET 2xx documents no schema |
| `POST /v1/data-ops`<br>(createdataops) | YELLOW | 성공(2xx) 응답 스키마가 문서에 없음 | POST 2xx documents no schema |
| `POST /v1/data-ops`<br>(createdataops) | YELLOW | 필수 파라미터의 값 형식·제약·출처가 API Reference에 없음 | required fields with no documented constraint: cluster_id, data_ops_name, domain, image_id, storage_class_name |
| `POST /v1/data-ops-services`<br>(createdataopsservice) | YELLOW | 성공(2xx) 응답 스키마가 문서에 없음 | POST 2xx documents no schema |
| `POST /v1/data-ops-services`<br>(createdataopsservice) | YELLOW | 필수 파라미터의 값 형식·제약·출처가 API Reference에 없음 | required fields with no documented constraint: data_ops_id, data_ops_service_name, domain, storage_class_name, worker_type |
| `GET /v1/data-ops/{data_ops_id}`<br>(getdataopsdetail) | YELLOW | 형식이 유효한 존재하지-않는 id 조회에 404가 아닌 400/403 반환 — 클라이언트가 "형식 오류/권한 문제/부재"를 구분할 수 없음. 참고: 같은 플랫폼에서 192개 API는 404를 반환하므로 (존재여부 은닉 등) 의도된 정책이 아니라 비일관 | non-existent id -> 400 (not 404) |
| `GET /v1/data-ops-services/{data_ops_service_id}`<br>(getdataopsservice) | YELLOW | 형식이 유효한 존재하지-않는 id 조회에 404가 아닌 400/403 반환 — 클라이언트가 "형식 오류/권한 문제/부재"를 구분할 수 없음. 참고: 같은 플랫폼에서 192개 API는 404를 반환하므로 (존재여부 은닉 등) 의도된 정책이 아니라 비일관 | non-existent id -> 400 (not 404) |
| `POST /v1/data-ops-services/clusters/{cluster_id}/validate-resources`<br>(getdataopsservicevalidateresourcescreation) | YELLOW | 엔드포인트 이름의 동사와 HTTP 메서드 불일치 | read-verb name but not GET (POST /v1/data-ops-services/clusters/{cluster_id}/validate-resources) |
| `POST /v1/data-ops-services/{data_ops_service_id}/validate-resources`<br>(getdataopsservicevalidateresourcesupdate) | YELLOW | 엔드포인트 이름의 동사와 HTTP 메서드 불일치 | read-verb name but not GET (POST /v1/data-ops-services/{data_ops_service_id}/validate-resources) |
| `GET /v1/data-ops-services/data-ops/{data_ops_id}/sub-versions`<br>(getdataopssubversion) | YELLOW | 형식이 유효한 존재하지-않는 id 조회에 404가 아닌 400/403 반환 — 클라이언트가 "형식 오류/권한 문제/부재"를 구분할 수 없음. 참고: 같은 플랫폼에서 192개 API는 404를 반환하므로 (존재여부 은닉 등) 의도된 정책이 아니라 비일관 | non-existent id -> 400 (not 404) |
| `GET /v1/data-ops/clusters/{cluster_id}/ingress-controllers`<br>(getingresscontrollerlistv1) | YELLOW | 성공(2xx) 응답 스키마가 문서에 없음 | GET 2xx documents no schema |
| `GET /v1/data-ops/clusters/{cluster_id}/ingress-controllers`<br>(getingresscontrollerlistv1) | YELLOW | 형식이 유효한 존재하지-않는 id 조회에 404가 아닌 400/403 반환 — 클라이언트가 "형식 오류/권한 문제/부재"를 구분할 수 없음. 참고: 같은 플랫폼에서 192개 API는 404를 반환하므로 (존재여부 은닉 등) 의도된 정책이 아니라 비일관 | non-existent id -> 400 (not 404) |

#### data-analytics/eventstreams — 6건

| API | 심각도 | 문제 | 근거 |
|---|---|---|---|
| `POST /v1/clusters`<br>(eventstreamscreatecluster) | RED | 잘못된 입력에 대한 400 응답이 원인 필드와 위반 규칙을 특정하지 않음 | 400 names neither field nor rule |
| `POST /v1/clusters`<br>(eventstreamscreatecluster) | YELLOW | 필수 파라미터의 값 형식·제약·출처가 API Reference에 없음 | required fields with no documented constraint: dbaas_engine_version_id, instance_name_prefix, name, subnet_id, timezone |
| `PUT /v1/clusters/{cluster_id}/patch`<br>(eventstreamspatchminorversion) | YELLOW | 필수 파라미터의 값 형식·제약·출처가 API Reference에 없음 | required fields with no documented constraint: dbaas_engine, software_version |
| `POST /v1/clusters/{cluster_id}/maintenance`<br>(eventstreamssetmaintenance) | YELLOW | 필수 파라미터의 값 형식·제약·출처가 API Reference에 없음 | required fields with no documented constraint: start_time, term_hour |
| `POST /v1/instance-groups/{instance_group_id}/resize`<br>(eventstreamssetservertype) | YELLOW | 필수 파라미터의 값 형식·제약·출처가 API Reference에 없음 | required fields with no documented constraint: server_type_name |
| `GET /v1/requests/{request_id}`<br>(eventstreamsshowrequest) | YELLOW | 형식이 유효한 존재하지-않는 id 조회에 404가 아닌 400/403 반환 — 클라이언트가 "형식 오류/권한 문제/부재"를 구분할 수 없음. 참고: 같은 플랫폼에서 192개 API는 404를 반환하므로 (존재여부 은닉 등) 의도된 정책이 아니라 비일관 | non-existent id -> 400 (not 404) |

#### data-analytics/quick-query — 5건

| API | 심각도 | 문제 | 근거 |
|---|---|---|---|
| `POST /v1/quick-query/validate-resources`<br>(validatequickqueryresources) | RED | 클라이언트가 유발한 상태·입력에 500 반환 | POST /v1/quick-query/validate-resources -> 500 ContactAdminForAssistance when the account has no Quick Query instance (service itself is reachable — image-versions 200 same run) instead of 400/404. Reconfirmed 실측 2026-07-16; already masked as a <자원명>-axis know |
| `PUT /v1/quick-query/{quick_query_id}/description`<br>(updatequickquerydescription) | YELLOW | 필수 파라미터의 값 형식·제약·출처가 API Reference에 없음 | required fields with no documented constraint: description |
| `PUT /v1/quick-query/{quick_query_id}/domain`<br>(updatequickquerydomain) | YELLOW | 필수 파라미터의 값 형식·제약·출처가 API Reference에 없음 | required fields with no documented constraint: domain |
| `PUT /v1/quick-query/{quick_query_id}/dsc-domain`<br>(updatequickquerydscdomain) | YELLOW | 필수 파라미터의 값 형식·제약·출처가 API Reference에 없음 | required fields with no documented constraint: dsc_domain |
| `PUT /v1/quick-query/{quick_query_id}/engine`<br>(updatequickqueryengine) | YELLOW | 필수 파라미터의 값 형식·제약·출처가 API Reference에 없음 | required fields with no documented constraint: cpu, memory, replica |

#### data-analytics/searchengine — 8건

| API | 심각도 | 문제 | 근거 |
|---|---|---|---|
| `POST /v1/clusters`<br>(searchenginecreatecluster) | RED | 잘못된 입력에 대한 400 응답이 원인 필드와 위반 규칙을 특정하지 않음 | 400 names neither field nor rule |
| `POST /v1/clusters`<br>(searchenginecreatecluster) | YELLOW | 필수 파라미터의 값 형식·제약·출처가 API Reference에 없음 | required fields with no documented constraint: dbaas_engine_version_id, instance_name_prefix, name, subnet_id, timezone |
| `POST /v1/clusters/{cluster_id}/restore`<br>(searchenginecreaterestore) | YELLOW | 필수 파라미터의 값 형식·제약·출처가 API Reference에 없음 | required fields with no documented constraint: backup_history_number, instance_name_prefix, name |
| `PUT /v1/clusters/{cluster_id}/patch`<br>(searchenginepatchminorversion) | YELLOW | 필수 파라미터의 값 형식·제약·출처가 API Reference에 없음 | required fields with no documented constraint: dbaas_engine, software_version |
| `POST /v1/clusters/{cluster_id}/backups`<br>(searchenginesetbackup) | YELLOW | 필수 파라미터의 값 형식·제약·출처가 API Reference에 없음 | required fields with no documented constraint: retention_period_day, starting_time_hour |
| `POST /v1/clusters/{cluster_id}/maintenance`<br>(searchenginesetmaintenance) | YELLOW | 필수 파라미터의 값 형식·제약·출처가 API Reference에 없음 | required fields with no documented constraint: start_time, term_hour |
| `POST /v1/instance-groups/{instance_group_id}/resize`<br>(searchenginesetservertype) | YELLOW | 필수 파라미터의 값 형식·제약·출처가 API Reference에 없음 | required fields with no documented constraint: server_type_name |
| `GET /v1/requests/{request_id}`<br>(searchengineshowrequest) | YELLOW | 형식이 유효한 존재하지-않는 id 조회에 404가 아닌 400/403 반환 — 클라이언트가 "형식 오류/권한 문제/부재"를 구분할 수 없음. 참고: 같은 플랫폼에서 192개 API는 404를 반환하므로 (존재여부 은닉 등) 의도된 정책이 아니라 비일관 | non-existent id -> 400 (not 404) |

#### data-analytics/vertica — 7건

| API | 심각도 | 문제 | 근거 |
|---|---|---|---|
| `POST /v1/clusters`<br>(verticacreatecluster) | RED | 잘못된 입력에 대한 400 응답이 원인 필드와 위반 규칙을 특정하지 않음 | 400 names neither field nor rule |
| `POST /v1/clusters`<br>(verticacreatecluster) | YELLOW | 필수 파라미터의 값 형식·제약·출처가 API Reference에 없음 | required fields with no documented constraint: dbaas_engine_version_id, instance_name_prefix, name, subnet_id, timezone |
| `POST /v1/clusters/{cluster_id}/restore`<br>(verticacreaterestore) | YELLOW | 필수 파라미터의 값 형식·제약·출처가 API Reference에 없음 | required fields with no documented constraint: backup_history_number, instance_name_prefix, name |
| `POST /v1/clusters/{cluster_id}/backups`<br>(verticasetbackup) | YELLOW | 필수 파라미터의 값 형식·제약·출처가 API Reference에 없음 | required fields with no documented constraint: retention_period_day, starting_time_hour |
| `POST /v1/clusters/{cluster_id}/maintenance`<br>(verticasetmaintenance) | YELLOW | 필수 파라미터의 값 형식·제약·출처가 API Reference에 없음 | required fields with no documented constraint: start_time, term_hour |
| `POST /v1/instance-groups/{instance_group_id}/resize`<br>(verticasetservertype) | YELLOW | 필수 파라미터의 값 형식·제약·출처가 API Reference에 없음 | required fields with no documented constraint: server_type_name |
| `GET /v1/requests/{request_id}`<br>(verticashowrequest) | YELLOW | 형식이 유효한 존재하지-않는 id 조회에 404가 아닌 400/403 반환 — 클라이언트가 "형식 오류/권한 문제/부재"를 구분할 수 없음. 참고: 같은 플랫폼에서 192개 API는 404를 반환하므로 (존재여부 은닉 등) 의도된 정책이 아니라 비일관 | non-existent id -> 400 (not 404) |

### database

#### database/cachestore — 9건

| API | 심각도 | 문제 | 근거 |
|---|---|---|---|
| `POST /v1/clusters`<br>(cachestorecreatecluster) | RED | 잘못된 입력에 대한 400 응답이 원인 필드와 위반 규칙을 특정하지 않음 | 400 names neither field nor rule |
| `POST /v1/clusters`<br>(cachestorecreatecluster) | YELLOW | 필수 파라미터의 값 형식·제약·출처가 API Reference에 없음 | required fields with no documented constraint: dbaas_engine_version_id, instance_name_prefix, name, subnet_id, timezone |
| `POST /v1/clusters/{cluster_id}/restore`<br>(cachestorecreaterestore) | YELLOW | 필수 파라미터의 값 형식·제약·출처가 API Reference에 없음 | required fields with no documented constraint: instance_name_prefix, name, server_type_name |
| `PUT /v1/clusters/{cluster_id}/patch`<br>(cachestorepatchminorversion) | YELLOW | 필수 파라미터의 값 형식·제약·출처가 API Reference에 없음 | required fields with no documented constraint: dbaas_engine, software_version |
| `POST /v1/clusters/{cluster_id}/backups`<br>(cachestoresetbackup) | YELLOW | 필수 파라미터의 값 형식·제약·출처가 API Reference에 없음 | required fields with no documented constraint: retention_period_day, starting_time_hour |
| `POST /v1/clusters/{cluster_id}/maintenance`<br>(cachestoresetmaintenance) | YELLOW | 필수 파라미터의 값 형식·제약·출처가 API Reference에 없음 | required fields with no documented constraint: start_time, term_hour |
| `POST /v1/instance-groups/{instance_group_id}/resize`<br>(cachestoresetservertype) | YELLOW | 필수 파라미터의 값 형식·제약·출처가 API Reference에 없음 | required fields with no documented constraint: server_type_name |
| `GET /v1/requests/{request_id}`<br>(cachestoreshowrequest) | YELLOW | 형식이 유효한 존재하지-않는 id 조회에 404가 아닌 400/403 반환 — 클라이언트가 "형식 오류/권한 문제/부재"를 구분할 수 없음. 참고: 같은 플랫폼에서 192개 API는 404를 반환하므로 (존재여부 은닉 등) 의도된 정책이 아니라 비일관 | non-existent id -> 400 (not 404) |
| `POST /v1/clusters/{cluster_id}/switchover`<br>(cachestoreswitchovercluster) | YELLOW | 필수 파라미터의 값 형식·제약·출처가 API Reference에 없음 | required fields with no documented constraint: switch_host_name |

#### database/epas — 12건

| API | 심각도 | 문제 | 근거 |
|---|---|---|---|
| `POST /v1/clusters`<br>(epascreatecluster) | RED | 잘못된 입력에 대한 400 응답이 원인 필드와 위반 규칙을 특정하지 않음 | 400 names neither field nor rule |
| `POST /v1/clusters/{cluster_id}/log-export-configs`<br>(epasregisterlogexportconfig) | RED | 클라이언트가 유발한 상태·입력에 500 반환 | Same access_key="" -> 500 ContactAdminForAssistance class as database/postgresql/postgresqlregisterlogexportconfig (실측). |
| `POST /v1/clusters`<br>(epascreatecluster) | YELLOW | 필수 파라미터의 값 형식·제약·출처가 API Reference에 없음 | required fields with no documented constraint: dbaas_engine_version_id, instance_name_prefix, name, subnet_id, timezone |
| `POST /v1/clusters/{cluster_id}/replicas/other-region`<br>(epascreateotherregionreplica) | YELLOW | 필수 파라미터의 값 형식·제약·출처가 API Reference에 없음 | required fields with no documented constraint: region, subnet_id |
| `POST /v1/clusters/{cluster_id}/restore`<br>(epascreaterestore) | YELLOW | 필수 파라미터의 값 형식·제약·출처가 API Reference에 없음 | required fields with no documented constraint: instance_name_prefix, name, server_type_name |
| `PUT /v1/clusters/{cluster_id}/patch`<br>(epaspatchminorversion) | YELLOW | 필수 파라미터의 값 형식·제약·출처가 API Reference에 없음 | required fields with no documented constraint: software_version |
| `POST /v1/clusters/{cluster_id}/log-export-configs`<br>(epasregisterlogexportconfig) | YELLOW | 필수 파라미터의 값 형식·제약·출처가 API Reference에 없음 | required fields with no documented constraint: access_key, bucket_name, log_type, schedule_day_of_month, schedule_frequency_type, schedule_hour, secret_key |
| `POST /v1/clusters/{cluster_id}/backups`<br>(epassetbackup) | YELLOW | 필수 파라미터의 값 형식·제약·출처가 API Reference에 없음 | required fields with no documented constraint: retention_period_day, starting_time_hour |
| `PUT /v1/clusters/{cluster_id}/log-export-configs/{log_type}`<br>(epassetlogexportconfig) | YELLOW | 필수 파라미터의 값 형식·제약·출처가 API Reference에 없음 | required fields with no documented constraint: access_key, schedule_day_of_month, schedule_frequency_type, schedule_hour, secret_key |
| `POST /v1/clusters/{cluster_id}/maintenance`<br>(epassetmaintenance) | YELLOW | 필수 파라미터의 값 형식·제약·출처가 API Reference에 없음 | required fields with no documented constraint: start_time, term_hour |
| `POST /v1/instance-groups/{instance_group_id}/resize`<br>(epassetservertype) | YELLOW | 필수 파라미터의 값 형식·제약·출처가 API Reference에 없음 | required fields with no documented constraint: server_type_name |
| `GET /v1/requests/{request_id}`<br>(epasshowrequest) | YELLOW | 형식이 유효한 존재하지-않는 id 조회에 404가 아닌 400/403 반환 — 클라이언트가 "형식 오류/권한 문제/부재"를 구분할 수 없음. 참고: 같은 플랫폼에서 192개 API는 404를 반환하므로 (존재여부 은닉 등) 의도된 정책이 아니라 비일관 | non-existent id -> 400 (not 404) |

#### database/mariadb — 13건

| API | 심각도 | 문제 | 근거 |
|---|---|---|---|
| `POST /v1/clusters`<br>(mariadbcreatecluster) | RED | 잘못된 입력에 대한 400 응답이 원인 필드와 위반 규칙을 특정하지 않음 | 400 names neither field nor rule |
| `POST /v1/clusters/{cluster_id}/log-export-configs`<br>(mariadbregisterlogexportconfig) | RED | 클라이언트가 유발한 상태·입력에 500 반환 | Same access_key="" -> 500 ContactAdminForAssistance class as database/postgresql/postgresqlregisterlogexportconfig (실측). |
| `POST /v1/clusters`<br>(mariadbcreatecluster) | YELLOW | 필수 파라미터의 값 형식·제약·출처가 API Reference에 없음 | required fields with no documented constraint: dbaas_engine_version_id, instance_name_prefix, name, subnet_id, timezone |
| `POST /v1/clusters/{cluster_id}/replicas/other-region`<br>(mariadbcreateotherregionreplica) | YELLOW | 필수 파라미터의 값 형식·제약·출처가 API Reference에 없음 | required fields with no documented constraint: region, subnet_id |
| `POST /v1/clusters/{cluster_id}/restore`<br>(mariadbcreaterestore) | YELLOW | 필수 파라미터의 값 형식·제약·출처가 API Reference에 없음 | required fields with no documented constraint: instance_name_prefix, name, server_type_name |
| `PUT /v1/clusters/{cluster_id}/patch`<br>(mariadbpatchminorversion) | YELLOW | 필수 파라미터의 값 형식·제약·출처가 API Reference에 없음 | required fields with no documented constraint: software_version |
| `POST /v1/clusters/{cluster_id}/log-export-configs`<br>(mariadbregisterlogexportconfig) | YELLOW | 필수 파라미터의 값 형식·제약·출처가 API Reference에 없음 | required fields with no documented constraint: access_key, bucket_name, log_type, schedule_day_of_month, schedule_frequency_type, schedule_hour, secret_key |
| `POST /v1/clusters/{cluster_id}/backups`<br>(mariadbsetbackup) | YELLOW | 필수 파라미터의 값 형식·제약·출처가 API Reference에 없음 | required fields with no documented constraint: retention_period_day, starting_time_hour |
| `PUT /v1/clusters/{cluster_id}/log-export-configs/{log_type}`<br>(mariadbsetlogexportconfig) | YELLOW | 필수 파라미터의 값 형식·제약·출처가 API Reference에 없음 | required fields with no documented constraint: access_key, schedule_day_of_month, schedule_frequency_type, schedule_hour, secret_key |
| `POST /v1/clusters/{cluster_id}/maintenance`<br>(mariadbsetmaintenance) | YELLOW | 필수 파라미터의 값 형식·제약·출처가 API Reference에 없음 | required fields with no documented constraint: start_time, term_hour |
| `POST /v1/instance-groups/{instance_group_id}/resize`<br>(mariadbsetservertype) | YELLOW | 필수 파라미터의 값 형식·제약·출처가 API Reference에 없음 | required fields with no documented constraint: server_type_name |
| `GET /v1/requests/{request_id}`<br>(mariadbshowrequest) | YELLOW | 형식이 유효한 존재하지-않는 id 조회에 404가 아닌 400/403 반환 — 클라이언트가 "형식 오류/권한 문제/부재"를 구분할 수 없음. 참고: 같은 플랫폼에서 192개 API는 404를 반환하므로 (존재여부 은닉 등) 의도된 정책이 아니라 비일관 | non-existent id -> 400 (not 404) |
| `PUT /v1/clusters/{cluster_id}/major-version-upgrade`<br>(mariadbupgrademajorversion) | YELLOW | 필수 파라미터의 값 형식·제약·출처가 API Reference에 없음 | required fields with no documented constraint: dbaas_engine, software_version |

#### database/mysql — 13건

| API | 심각도 | 문제 | 근거 |
|---|---|---|---|
| `POST /v1/clusters`<br>(mysqlcreatecluster) | RED | 잘못된 입력에 대한 400 응답이 원인 필드와 위반 규칙을 특정하지 않음 | 400 names neither field nor rule |
| `POST /v1/clusters/{cluster_id}/log-export-configs`<br>(mysqlregisterlogexportconfig) | RED | 클라이언트가 유발한 상태·입력에 500 반환 | Same access_key="" -> 500 ContactAdminForAssistance class as database/postgresql/postgresqlregisterlogexportconfig (실측). |
| `POST /v1/clusters`<br>(mysqlcreatecluster) | YELLOW | 필수 파라미터의 값 형식·제약·출처가 API Reference에 없음 | required fields with no documented constraint: dbaas_engine_version_id, instance_name_prefix, name, subnet_id, timezone |
| `POST /v1/clusters/{cluster_id}/replicas/other-region`<br>(mysqlcreateotherregionreplica) | YELLOW | 필수 파라미터의 값 형식·제약·출처가 API Reference에 없음 | required fields with no documented constraint: region, subnet_id |
| `POST /v1/clusters/{cluster_id}/restore`<br>(mysqlcreaterestore) | YELLOW | 필수 파라미터의 값 형식·제약·출처가 API Reference에 없음 | required fields with no documented constraint: instance_name_prefix, name, server_type_name |
| `PUT /v1/clusters/{cluster_id}/patch`<br>(mysqlpatchminorversion) | YELLOW | 필수 파라미터의 값 형식·제약·출처가 API Reference에 없음 | required fields with no documented constraint: software_version |
| `POST /v1/clusters/{cluster_id}/log-export-configs`<br>(mysqlregisterlogexportconfig) | YELLOW | 필수 파라미터의 값 형식·제약·출처가 API Reference에 없음 | required fields with no documented constraint: access_key, bucket_name, log_type, schedule_day_of_month, schedule_frequency_type, schedule_hour, secret_key |
| `POST /v1/clusters/{cluster_id}/backups`<br>(mysqlsetbackup) | YELLOW | 필수 파라미터의 값 형식·제약·출처가 API Reference에 없음 | required fields with no documented constraint: retention_period_day, starting_time_hour |
| `PUT /v1/clusters/{cluster_id}/log-export-configs/{log_type}`<br>(mysqlsetlogexportconfig) | YELLOW | 필수 파라미터의 값 형식·제약·출처가 API Reference에 없음 | required fields with no documented constraint: access_key, schedule_day_of_month, schedule_frequency_type, schedule_hour, secret_key |
| `POST /v1/clusters/{cluster_id}/maintenance`<br>(mysqlsetmaintenance) | YELLOW | 필수 파라미터의 값 형식·제약·출처가 API Reference에 없음 | required fields with no documented constraint: start_time, term_hour |
| `POST /v1/instance-groups/{instance_group_id}/resize`<br>(mysqlsetservertype) | YELLOW | 필수 파라미터의 값 형식·제약·출처가 API Reference에 없음 | required fields with no documented constraint: server_type_name |
| `GET /v1/requests/{request_id}`<br>(mysqlshowrequest) | YELLOW | 형식이 유효한 존재하지-않는 id 조회에 404가 아닌 400/403 반환 — 클라이언트가 "형식 오류/권한 문제/부재"를 구분할 수 없음. 참고: 같은 플랫폼에서 192개 API는 404를 반환하므로 (존재여부 은닉 등) 의도된 정책이 아니라 비일관 | non-existent id -> 400 (not 404) |
| `PUT /v1/clusters/{cluster_id}/major-version-upgrade`<br>(mysqlupgrademajorversion) | YELLOW | 필수 파라미터의 값 형식·제약·출처가 API Reference에 없음 | required fields with no documented constraint: dbaas_engine, software_version |

#### database/postgresql — 13건

| API | 심각도 | 문제 | 근거 |
|---|---|---|---|
| `POST /v1/clusters`<br>(postgresqlcreatecluster) | RED | 잘못된 입력에 대한 400 응답이 원인 필드와 위반 규칙을 특정하지 않음 | 400 names neither field nor rule |
| `POST /v1/clusters/{cluster_id}/log-export-configs`<br>(postgresqlregisterlogexportconfig) | RED | 클라이언트가 유발한 상태·입력에 500 반환 | POST log-export-config with access_key="" -> 500 ContactAdminForAssistance instead of 400 (an empty required credential should fail input validation, not the backend). Same class reproduced across the mariadb/mysql/epas siblings (see sibling findings on this r |
| `PUT /v1/clusters/{cluster_id}/parameters`<br>(postgresqlsetparametervalues) | RED | 클라이언트가 유발한 상태·입력에 500 반환 | PUT parameters no-op echo (re-submitting the current applied_value for a template-string parameter, e.g. "{1/8 of server total memory}") -> 500 ContactAdminForAssistance instead of 200/400. req-ef12a36a..., 실측 campaign A (2026-07-16). Workaround: scenario only |
| `POST /v1/clusters`<br>(postgresqlcreatecluster) | YELLOW | 필수 파라미터의 값 형식·제약·출처가 API Reference에 없음 | required fields with no documented constraint: dbaas_engine_version_id, instance_name_prefix, name, subnet_id, timezone |
| `POST /v1/clusters/{cluster_id}/replicas/other-region`<br>(postgresqlcreateotherregionreplica) | YELLOW | 필수 파라미터의 값 형식·제약·출처가 API Reference에 없음 | required fields with no documented constraint: region, subnet_id |
| `POST /v1/clusters/{cluster_id}/restore`<br>(postgresqlcreaterestore) | YELLOW | 필수 파라미터의 값 형식·제약·출처가 API Reference에 없음 | required fields with no documented constraint: instance_name_prefix, name, server_type_name |
| `PUT /v1/clusters/{cluster_id}/patch`<br>(postgresqlpatchminorversion) | YELLOW | 필수 파라미터의 값 형식·제약·출처가 API Reference에 없음 | required fields with no documented constraint: software_version |
| `POST /v1/clusters/{cluster_id}/log-export-configs`<br>(postgresqlregisterlogexportconfig) | YELLOW | 필수 파라미터의 값 형식·제약·출처가 API Reference에 없음 | required fields with no documented constraint: access_key, bucket_name, log_type, schedule_day_of_month, schedule_frequency_type, schedule_hour, secret_key |
| `POST /v1/clusters/{cluster_id}/backups`<br>(postgresqlsetbackup) | YELLOW | 필수 파라미터의 값 형식·제약·출처가 API Reference에 없음 | required fields with no documented constraint: retention_period_day, starting_time_hour |
| `PUT /v1/clusters/{cluster_id}/log-export-configs/{log_type}`<br>(postgresqlsetlogexportconfig) | YELLOW | 필수 파라미터의 값 형식·제약·출처가 API Reference에 없음 | required fields with no documented constraint: access_key, schedule_day_of_month, schedule_frequency_type, schedule_hour, secret_key |
| `POST /v1/clusters/{cluster_id}/maintenance`<br>(postgresqlsetmaintenance) | YELLOW | 필수 파라미터의 값 형식·제약·출처가 API Reference에 없음 | required fields with no documented constraint: start_time, term_hour |
| `POST /v1/instance-groups/{instance_group_id}/resize`<br>(postgresqlsetservertype) | YELLOW | 필수 파라미터의 값 형식·제약·출처가 API Reference에 없음 | required fields with no documented constraint: server_type_name |
| `GET /v1/requests/{request_id}`<br>(postgresqlshowrequest) | YELLOW | 형식이 유효한 존재하지-않는 id 조회에 404가 아닌 400/403 반환 — 클라이언트가 "형식 오류/권한 문제/부재"를 구분할 수 없음. 참고: 같은 플랫폼에서 192개 API는 404를 반환하므로 (존재여부 은닉 등) 의도된 정책이 아니라 비일관 | non-existent id -> 400 (not 404) |

#### database/sqlserver — 12건

| API | 심각도 | 문제 | 근거 |
|---|---|---|---|
| `POST /v1/clusters`<br>(sqlservercreatecluster) | RED | 잘못된 입력에 대한 400 응답이 원인 필드와 위반 규칙을 특정하지 않음 | 400 names neither field nor rule |
| `POST /v1/clusters/{cluster_id}/dr-secondaries`<br>(sqlserveradddrsecondary) | YELLOW | 필수 파라미터의 값 형식·제약·출처가 API Reference에 없음 | required fields with no documented constraint: ad_user_id, ad_user_password, license, name, region, subnet_id |
| `POST /v1/clusters/{cluster_id}/add-secondary`<br>(sqlserveraddsecondary) | YELLOW | 필수 파라미터의 값 형식·제약·출처가 API Reference에 없음 | required fields with no documented constraint: license, name |
| `POST /v1/clusters`<br>(sqlservercreatecluster) | YELLOW | 필수 파라미터의 값 형식·제약·출처가 API Reference에 없음 | required fields with no documented constraint: dbaas_engine_version_id, instance_name_prefix, name, subnet_id, timezone |
| `POST /v1/clusters/{cluster_id}/restore`<br>(sqlservercreaterestore) | YELLOW | 필수 파라미터의 값 형식·제약·출처가 API Reference에 없음 | required fields with no documented constraint: instance_name_prefix, name, server_type_name |
| `PUT /v1/clusters/{cluster_id}/patch`<br>(sqlserverpatchminorversion) | YELLOW | 필수 파라미터의 값 형식·제약·출처가 API Reference에 없음 | required fields with no documented constraint: software_version |
| `POST /v1/clusters/{cluster_id}/log-export-configs`<br>(sqlserverregisterlogexportconfig) | YELLOW | 필수 파라미터의 값 형식·제약·출처가 API Reference에 없음 | required fields with no documented constraint: access_key, bucket_name, log_type, schedule_day_of_month, schedule_frequency_type, schedule_hour, secret_key |
| `POST /v1/clusters/{cluster_id}/backups`<br>(sqlserversetbackup) | YELLOW | 필수 파라미터의 값 형식·제약·출처가 API Reference에 없음 | required fields with no documented constraint: retention_period_day, starting_time_hour |
| `PUT /v1/clusters/{cluster_id}/log-export-configs/{log_type}`<br>(sqlserversetlogexportconfig) | YELLOW | 필수 파라미터의 값 형식·제약·출처가 API Reference에 없음 | required fields with no documented constraint: access_key, schedule_day_of_month, schedule_frequency_type, schedule_hour, secret_key |
| `POST /v1/clusters/{cluster_id}/maintenance`<br>(sqlserversetmaintenance) | YELLOW | 필수 파라미터의 값 형식·제약·출처가 API Reference에 없음 | required fields with no documented constraint: start_time, term_hour |
| `POST /v1/instance-groups/{instance_group_id}/resize`<br>(sqlserversetservertype) | YELLOW | 필수 파라미터의 값 형식·제약·출처가 API Reference에 없음 | required fields with no documented constraint: server_type_name |
| `GET /v1/requests/{request_id}`<br>(sqlservershowrequest) | YELLOW | 형식이 유효한 존재하지-않는 id 조회에 404가 아닌 400/403 반환 — 클라이언트가 "형식 오류/권한 문제/부재"를 구분할 수 없음. 참고: 같은 플랫폼에서 192개 API는 404를 반환하므로 (존재여부 은닉 등) 의도된 정책이 아니라 비일관 | non-existent id -> 400 (not 404) |

### devops-tools

#### devops-tools/devopsservice — 1건

| API | 심각도 | 문제 | 근거 |
|---|---|---|---|
| `POST /v1/devops-services`<br>(createdevopsservice) | YELLOW | 필수 파라미터의 값 형식·제약·출처가 API Reference에 없음 | required fields with no documented constraint: tenant_code, tenant_name |

### financial-management

#### financial-management/billingplan — 8건

| API | 심각도 | 문제 | 근거 |
|---|---|---|---|
| `POST /v1/planned-computes`<br>(createplannedcomputes) | RED | errors[].detail에 파이썬 리스트의 repr 문자열을 그대로 직렬화해 반환 — 플랫폼 표준(JSON 배열, 당일 131개 API 실측)과 달리 필드 단위 파싱 불가. 즉시 수정 대상 | errors[].detail is a stringified Python list — observed "['Field required', 'Field required', 'Field required', 'Field required']" (empty-body 400, global_request_id req-b6280fd2-7848-…, 2026-08-20). Platform-standard detail is a JSON array (131 endpoints meas |
| `POST /v1/planned-computes/cancellation-fee`<br>(showcancellationfee) | RED | errors[].detail에 파이썬 리스트의 repr 문자열을 그대로 직렬화해 반환 — 플랫폼 표준(JSON 배열, 당일 131개 API 실측)과 달리 필드 단위 파싱 불가. 즉시 수정 대상 | errors[].detail is a stringified Python list — observed "['Field required']" (empty-body 400, global_request_id req-210f6bdc-3f2a-4932-9585-5e8c8167773e, 2026-08-20). Same repr-leak shape as createplannedcomputes. Immediate-fix per owner triage. |
| `POST /v1/planned-computes`<br>(createplannedcomputes) | YELLOW | 필수 파라미터의 값 형식·제약·출처가 API Reference에 없음 | required fields with no documented constraint: server_type, service_id |
| `GET /v1/planned-computes/contract-types`<br>(listcontracttypes) | YELLOW | 목록 조회에 ?size=1을 보내도 전량 반환(size 무시 — 실측: listservertypes 121개, listbillingitemids 276개), 또는 페이징 메타(count/total) 부재로 클라이언트가 순회 종료 시점을 알 수 없음 | ignores size=1 (returned 4) |
| `GET /v1/planned-computes/os-types`<br>(listostypes) | YELLOW | 목록 조회에 ?size=1을 보내도 전량 반환(size 무시 — 실측: listservertypes 121개, listbillingitemids 276개), 또는 페이징 메타(count/total) 부재로 클라이언트가 순회 종료 시점을 알 수 없음 | ignores size=1 (returned 4) |
| `GET /v1/planned-computes/server-types`<br>(listplannedcomputeservertypes) | YELLOW | 목록 조회에 ?size=1을 보내도 전량 반환(size 무시 — 실측: listservertypes 121개, listbillingitemids 276개), 또는 페이징 메타(count/total) 부재로 클라이언트가 순회 종료 시점을 알 수 없음 | ignores size=1 (returned 387) |
| `GET /v1/planned-computes/service-types`<br>(listplannedcomputeservicetypes) | YELLOW | 목록 조회에 ?size=1을 보내도 전량 반환(size 무시 — 실측: listservertypes 121개, listbillingitemids 276개), 또는 페이징 메타(count/total) 부재로 클라이언트가 순회 종료 시점을 알 수 없음 | ignores size=1 (returned 13) |
| `POST /v1/planned-computes/cancellation-fee`<br>(showcancellationfee) | YELLOW | 엔드포인트 이름의 동사와 HTTP 메서드 불일치 | read-verb name but not GET (POST /v1/planned-computes/cancellation-fee) |

#### financial-management/budget — 3건

| API | 심각도 | 문제 | 근거 |
|---|---|---|---|
| `POST /v1/budgets/account`<br>(createaccountbudget) | YELLOW | 필수 파라미터의 값 형식·제약·출처가 API Reference에 없음 | required fields with no documented constraint: name, start_month, unit |
| `GET /v1/budgets/account`<br>(listaccountbudgets) | YELLOW | 목록 조회에 ?size=1을 보내도 전량 반환(size 무시 — 실측: listservertypes 121개, listbillingitemids 276개), 또는 페이징 메타(count/total) 부재로 클라이언트가 순회 종료 시점을 알 수 없음 | ignores size=1 (returned 4) |
| `PUT /v1/budgets/account/{budget_id}`<br>(setaccountbudget) | YELLOW | 필수 파라미터의 값 형식·제약·출처가 API Reference에 없음 | required fields with no documented constraint: name, start_month, unit |

#### financial-management/costexplorer — 4건

| API | 심각도 | 문제 | 근거 |
|---|---|---|---|
| `GET /v1/bills`<br>(listbills) | YELLOW | 실제 응답에 문서에 없는 필드 존재 | response has undocumented field(s): usage_amt |
| `GET /v1/bills`<br>(listbills) | YELLOW | 목록 조회에 ?size=1을 보내도 전량 반환(size 무시 — 실측: listservertypes 121개, listbillingitemids 276개), 또는 페이징 메타(count/total) 부재로 클라이언트가 순회 종료 시점을 알 수 없음 | ignores size=1 (returned 20) |
| `GET /v1/usages`<br>(listusages) | YELLOW | 실제 응답에 문서에 없는 필드 존재 | response has undocumented field(s): end_time,metrics,start_time,used_time |
| `GET /v1/usages`<br>(listusages) | YELLOW | 목록 조회에 ?size=1을 보내도 전량 반환(size 무시 — 실측: listservertypes 121개, listbillingitemids 276개), 또는 페이징 메타(count/total) 부재로 클라이언트가 순회 종료 시점을 알 수 없음 | ignores size=1 (returned 20) |

#### financial-management/pricing — 2건

| API | 심각도 | 문제 | 근거 |
|---|---|---|---|
| `GET /v1/reports/billing-item-ids`<br>(listbillingitemids) | YELLOW | 목록 조회에 ?size=1을 보내도 전량 반환(size 무시 — 실측: listservertypes 121개, listbillingitemids 276개), 또는 페이징 메타(count/total) 부재로 클라이언트가 순회 종료 시점을 알 수 없음 | ignores size=1 (returned 276) |
| `GET /v1/reports/offerings`<br>(listoffering) | YELLOW | 목록 조회에 ?size=1을 보내도 전량 반환(size 무시 — 실측: listservertypes 121개, listbillingitemids 276개), 또는 페이징 메타(count/total) 부재로 클라이언트가 순회 종료 시점을 알 수 없음 | ignores size=1 (returned 4) |

### management

#### management/cloudcontrol — 7건

| API | 심각도 | 문제 | 근거 |
|---|---|---|---|
| `POST /v1/baseline-assignments/{assignment_id}`<br>(addbaselineassignment) | YELLOW | 필수 파라미터의 값 형식·제약·출처가 API Reference에 없음 | required fields with no documented constraint: landing_zone_id |
| `POST /v1/accounts`<br>(createaccountfactoryaccount) | YELLOW | 필수 파라미터의 값 형식·제약·출처가 API Reference에 없음 | required fields with no documented constraint: landing_zone_id, name, parent_unit_id |
| `POST /v1/landing-zones`<br>(createlandingzone) | YELLOW | 필수 파라미터의 값 형식·제약·출처가 API Reference에 없음 | required fields with no documented constraint: additional_ou_name, audit_account_name, audit_login_id, basic_ou_name, log_archive_account_name, log_archive_login_id |
| `PUT /v1/landing-zones/{landing_zone_id}`<br>(setlandingzone) | YELLOW | 필수 파라미터의 값 형식·제약·출처가 API Reference에 없음 | required fields with no documented constraint: detective_guardrail_status |
| `GET /v1/guardrails/{guardrail_id}`<br>(showguardrail) | YELLOW | 형식이 유효한 존재하지-않는 id 조회에 404가 아닌 400/403 반환 — 클라이언트가 "형식 오류/권한 문제/부재"를 구분할 수 없음. 참고: 같은 플랫폼에서 192개 API는 404를 반환하므로 (존재여부 은닉 등) 의도된 정책이 아니라 비일관 | non-existent id -> 403 (not 404) |
| `GET /v1/landing-zones/{landing_zone_id}`<br>(showlandingzone) | YELLOW | 형식이 유효한 존재하지-않는 id 조회에 404가 아닌 400/403 반환 — 클라이언트가 "형식 오류/권한 문제/부재"를 구분할 수 없음. 참고: 같은 플랫폼에서 192개 API는 404를 반환하므로 (존재여부 은닉 등) 의도된 정책이 아니라 비일관 | non-existent id -> 403 (not 404) |
| `PUT /v1/baseline-assignments/{assignment_id}`<br>(updatebaselineassignment) | YELLOW | 필수 파라미터의 값 형식·제약·출처가 API Reference에 없음 | required fields with no documented constraint: landing_zone_id |

#### management/cloudmonitoring — 9건

| API | 심각도 | 문제 | 근거 |
|---|---|---|---|
| `POST /v1/cloudmonitorings/product/v2/metric-data`<br>(getmetricperfdatalist) | RED | 잘못된 입력에 대한 400 응답이 원인 필드와 위반 규칙을 특정하지 않음 | 400 names neither field nor rule |
| `POST /v1/cloudmonitorings/event/v2/event-policies`<br>(puteventpolicy) | RED | 잘못된 입력에 대한 400 응답이 원인 필드와 위반 규칙을 특정하지 않음 | 400 names neither field nor rule |
| `GET /v1/cloudmonitorings/product/v2/addrbooks/{addrbookId}/members`<br>(getadressbookmemberlist) | YELLOW | 형식이 유효한 존재하지-않는 id 조회에 404가 아닌 400/403 반환 — 클라이언트가 "형식 오류/권한 문제/부재"를 구분할 수 없음. 참고: 같은 플랫폼에서 192개 API는 404를 반환하므로 (존재여부 은닉 등) 의도된 정책이 아니라 비일관 | non-existent id -> 400 (not 404) |
| `GET /v1/cloudmonitorings/event/v2/event-policies/{eventPolicyId}`<br>(geteventpolicydetail) | YELLOW | 형식이 유효한 존재하지-않는 id 조회에 404가 아닌 400/403 반환 — 클라이언트가 "형식 오류/권한 문제/부재"를 구분할 수 없음. 참고: 같은 플랫폼에서 192개 API는 404를 반환하므로 (존재여부 은닉 등) 의도된 정책이 아니라 비일관 | non-existent id -> 400 (not 404) |
| `GET /v1/cloudmonitorings/event/v2/event-policies/{eventPolicyId}/histories`<br>(geteventpolicyhistories) | YELLOW | 형식이 유효한 존재하지-않는 id 조회에 404가 아닌 400/403 반환 — 클라이언트가 "형식 오류/권한 문제/부재"를 구분할 수 없음. 참고: 같은 플랫폼에서 192개 API는 404를 반환하므로 (존재여부 은닉 등) 의도된 정책이 아니라 비일관 | non-existent id -> 400 (not 404) |
| `GET /v1/cloudmonitorings/event/v2/event-policies/{eventPolicyId}/notifications`<br>(geteventpolicynotification) | YELLOW | 형식이 유효한 존재하지-않는 id 조회에 404가 아닌 400/403 반환 — 클라이언트가 "형식 오류/권한 문제/부재"를 구분할 수 없음. 참고: 같은 플랫폼에서 192개 API는 404를 반환하므로 (존재여부 은닉 등) 의도된 정책이 아니라 비일관 | non-existent id -> 400 (not 404) |
| `POST /v1/cloudmonitorings/product/v2/metric-data`<br>(getmetricperfdatalist) | YELLOW | 엔드포인트 이름의 동사와 HTTP 메서드 불일치 | read-verb name but not GET (POST /v1/cloudmonitorings/product/v2/metric-data) |
| `POST /v1/cloudmonitorings/product/v2/metric-data`<br>(getmetricperfdatalist) | YELLOW | 필수 파라미터의 값 형식·제약·출처가 API Reference에 없음 | required fields with no documented constraint: queryEndDt |
| `POST /v1/cloudmonitorings/event/v2/event-policies`<br>(puteventpolicy) | YELLOW | 필수 파라미터의 값 형식·제약·출처가 API Reference에 없음 | required fields with no documented constraint: productResourceId |

#### management/iam — 21건

| API | 심각도 | 문제 | 근거 |
|---|---|---|---|
| `POST /v1/access-keys/send-otp`<br>(accesskeysendtemporaryotp) | YELLOW | 성공(2xx) 응답 스키마가 문서에 없음 | POST 2xx documents no schema |
| `POST /v1/groups/{group_id}/members`<br>(addgroupmember) | YELLOW | 필수 파라미터의 값 형식·제약·출처가 API Reference에 없음 | required fields with no documented constraint: user_id |
| `POST /v1/resource-policies/{srn}/statements`<br>(addpermission) | YELLOW | 필수 파라미터의 값 형식·제약·출처가 API Reference에 없음 | required fields with no documented constraint: Effect |
| `POST /v1/groups`<br>(creategroup) | YELLOW | 필수 파라미터의 값 형식·제약·출처가 API Reference에 없음 | required fields with no documented constraint: description, name |
| `POST /v1/accounts/{account_id}/users`<br>(createiamuser) | YELLOW | 필수 파라미터의 값 형식·제약·출처가 API Reference에 없음 | required fields with no documented constraint: password, user_name |
| `POST /v1/policies`<br>(createpolicy) | YELLOW | 필수 파라미터의 값 형식·제약·출처가 API Reference에 없음 | required fields with no documented constraint: policy_name |
| `POST /v1/roles`<br>(createrole) | YELLOW | 필수 파라미터의 값 형식·제약·출처가 API Reference에 없음 | required fields with no documented constraint: name |
| `POST /v1/saml-providers`<br>(createsamlprovider) | YELLOW | 필수 파라미터의 값 형식·제약·출처가 API Reference에 없음 | required fields with no documented constraint: federation_type, saml_provider_name |
| `GET /v1/endpoints`<br>(listendpoints) | YELLOW | 목록 조회에 ?size=1을 보내도 전량 반환(size 무시 — 실측: listservertypes 121개, listbillingitemids 276개), 또는 페이징 메타(count/total) 부재로 클라이언트가 순회 종료 시점을 알 수 없음 | ignores size=1 (returned 180) |
| `GET /v1/groups`<br>(listgroup) | YELLOW | 목록 조회에 ?size=1을 보내도 전량 반환(size 무시 — 실측: listservertypes 121개, listbillingitemids 276개), 또는 페이징 메타(count/total) 부재로 클라이언트가 순회 종료 시점을 알 수 없음 | ignores size=1 (returned 3) |
| `GET /v1/accounts/{account_id}/users`<br>(listiamuser) | YELLOW | 형식이 유효한 존재하지-않는 id 조회에 404가 아닌 400/403 반환 — 클라이언트가 "형식 오류/권한 문제/부재"를 구분할 수 없음. 참고: 같은 플랫폼에서 192개 API는 404를 반환하므로 (존재여부 은닉 등) 의도된 정책이 아니라 비일관 | non-existent id -> 403 (not 404) |
| `GET /v1/policies`<br>(listpolicy) | YELLOW | 목록 조회에 ?size=1을 보내도 전량 반환(size 무시 — 실측: listservertypes 121개, listbillingitemids 276개), 또는 페이징 메타(count/total) 부재로 클라이언트가 순회 종료 시점을 알 수 없음 | ignores size=1 (returned 3) |
| `GET /v1/roles`<br>(listrole) | YELLOW | 목록 조회에 ?size=1을 보내도 전량 반환(size 무시 — 실측: listservertypes 121개, listbillingitemids 276개), 또는 페이징 메타(count/total) 부재로 클라이언트가 순회 종료 시점을 알 수 없음 | ignores size=1 (returned 3) |
| `GET /v1/saml-providers`<br>(listsamlprovider) | YELLOW | 목록 조회에 ?size=1을 보내도 전량 반환(size 무시 — 실측: listservertypes 121개, listbillingitemids 276개), 또는 페이징 메타(count/total) 부재로 클라이언트가 순회 종료 시점을 알 수 없음 | ignores size=1 (returned 2) |
| `GET /v1/service-accounts`<br>(listserviceaccount) | YELLOW | 목록 조회에 ?size=1을 보내도 전량 반환(size 무시 — 실측: listservertypes 121개, listbillingitemids 276개), 또는 페이징 메타(count/total) 부재로 클라이언트가 순회 종료 시점을 알 수 없음 | ignores size=1 (returned 9) |
| `GET /v1/users/{user_id}/policy-bindings`<br>(listuserpolicybindings) | YELLOW | 존재하지 않는 부모의 하위 목록 조회가 200(빈 목록) 반환 | sub-resource list of a non-existent parent -> 200 (empty), not 404 |
| `PUT /v1/groups/{group_id}`<br>(setgroup) | YELLOW | 필수 파라미터의 값 형식·제약·출처가 API Reference에 없음 | required fields with no documented constraint: description, name |
| `PUT /v1/resource-policies/{srn}/statements/{sid}`<br>(setpermission) | YELLOW | 필수 파라미터의 값 형식·제약·출처가 API Reference에 없음 | required fields with no documented constraint: Effect |
| `PUT /v1/policies/{policy_id}/bindings`<br>(setpolicygroupbinding) | YELLOW | 필수 파라미터의 값 형식·제약·출처가 API Reference에 없음 | required fields with no documented constraint: identity_type |
| `GET /v1/resource-policies/{srn}`<br>(showresourcepolicy) | YELLOW | 형식이 유효한 존재하지-않는 id 조회에 404가 아닌 400/403 반환 — 클라이언트가 "형식 오류/권한 문제/부재"를 구분할 수 없음. 참고: 같은 플랫폼에서 192개 API는 404를 반환하므로 (존재여부 은닉 등) 의도된 정책이 아니라 비일관 | non-existent id -> 400 (not 404) |
| `PUT /v1/accounts/{account_id}/users/{user_id}/password`<br>(updateiamuserpassword) | YELLOW | 필수 파라미터의 값 형식·제약·출처가 API Reference에 없음 | required fields with no documented constraint: password |

#### management/iam-identity-center — 21건

| API | 심각도 | 문제 | 근거 |
|---|---|---|---|
| `POST /v1/groups`<br>(creategroup) | RED | 잘못된 입력에 대한 400 응답이 원인 필드와 위반 규칙을 특정하지 않음 | 400 names neither field nor rule |
| `POST /v1/permission-sets`<br>(createpermissionset) | RED | 잘못된 입력에 대한 400 응답이 원인 필드와 위반 규칙을 특정하지 않음 | 400 names neither field nor rule |
| `POST /v1/account-assignments`<br>(createaccountassignment) | YELLOW | 필수 파라미터의 값 형식·제약·출처가 API Reference에 없음 | required fields with no documented constraint: instance_id |
| `POST /v1/groups/{group_id}/users`<br>(createbulkgroupusers) | YELLOW | 필수 파라미터의 값 형식·제약·출처가 API Reference에 없음 | required fields with no documented constraint: instance_id |
| `POST /v1/groups`<br>(creategroup) | YELLOW | 필수 파라미터의 값 형식·제약·출처가 API Reference에 없음 | required fields with no documented constraint: instance_id, name |
| `POST /v1/instances`<br>(createinstance) | YELLOW | 필수 파라미터의 값 형식·제약·출처가 API Reference에 없음 | required fields with no documented constraint: name |
| `POST /v1/permission-sets`<br>(createpermissionset) | YELLOW | 필수 파라미터의 값 형식·제약·출처가 API Reference에 없음 | required fields with no documented constraint: instance_id, name |
| `POST /v1/users`<br>(createuser) | YELLOW | 필수 파라미터의 값 형식·제약·출처가 API Reference에 없음 | required fields with no documented constraint: instance_id, name, user_id |
| `DELETE /v1/users/{user_uuid}`<br>(deleteuser) | YELLOW | 경로 파라미터 명명이 표준과 다름 | {user_uuid} vs {*_id} in /v1/users/{user_uuid} |
| `GET /v1/groups/{group_id}/users`<br>(listgroupusers) | YELLOW | 형식이 유효한 존재하지-않는 id 조회에 404가 아닌 400/403 반환 — 클라이언트가 "형식 오류/권한 문제/부재"를 구분할 수 없음. 참고: 같은 플랫폼에서 192개 API는 404를 반환하므로 (존재여부 은닉 등) 의도된 정책이 아니라 비일관 | non-existent id -> 400 (not 404) |
| `GET /v1/instances`<br>(listinstances) | YELLOW | 목록 조회에 ?size=1을 보내도 전량 반환(size 무시 — 실측: listservertypes 121개, listbillingitemids 276개), 또는 페이징 메타(count/total) 부재로 클라이언트가 순회 종료 시점을 알 수 없음 | ignores size=1 (returned 2) |
| `GET /v1/permission-sets/{permission_set_id}/policies`<br>(listpermissionsetpolicies) | YELLOW | 형식이 유효한 존재하지-않는 id 조회에 404가 아닌 400/403 반환 — 클라이언트가 "형식 오류/권한 문제/부재"를 구분할 수 없음. 참고: 같은 플랫폼에서 192개 API는 404를 반환하므로 (존재여부 은닉 등) 의도된 정책이 아니라 비일관 | non-existent id -> 400 (not 404) |
| `PATCH /v1/groups/{group_id}`<br>(setgroup) | YELLOW | 필수 파라미터의 값 형식·제약·출처가 API Reference에 없음 | required fields with no documented constraint: instance_id |
| `PATCH /v1/permission-sets/{permission_set_id}`<br>(setpermissionset) | YELLOW | 필수 파라미터의 값 형식·제약·출처가 API Reference에 없음 | required fields with no documented constraint: instance_id |
| `PUT /v1/permission-sets/{permission_set_id}/policies`<br>(setpermissionsetpolicies) | YELLOW | 필수 파라미터의 값 형식·제약·출처가 API Reference에 없음 | required fields with no documented constraint: instance_id |
| `PATCH /v1/users/{user_uuid}`<br>(setuser) | YELLOW | 경로 파라미터 명명이 표준과 다름 | {user_uuid} vs {*_id} in /v1/users/{user_uuid} |
| `PATCH /v1/users/{user_uuid}`<br>(setuser) | YELLOW | 필수 파라미터의 값 형식·제약·출처가 API Reference에 없음 | required fields with no documented constraint: instance_id |
| `GET /v1/groups/{group_id}`<br>(showgroup) | YELLOW | 형식이 유효한 존재하지-않는 id 조회에 404가 아닌 400/403 반환 — 클라이언트가 "형식 오류/권한 문제/부재"를 구분할 수 없음. 참고: 같은 플랫폼에서 192개 API는 404를 반환하므로 (존재여부 은닉 등) 의도된 정책이 아니라 비일관 | non-existent id -> 400 (not 404) |
| `GET /v1/permission-sets/{permission_set_id}`<br>(showpermissionset) | YELLOW | 형식이 유효한 존재하지-않는 id 조회에 404가 아닌 400/403 반환 — 클라이언트가 "형식 오류/권한 문제/부재"를 구분할 수 없음. 참고: 같은 플랫폼에서 192개 API는 404를 반환하므로 (존재여부 은닉 등) 의도된 정책이 아니라 비일관 | non-existent id -> 400 (not 404) |
| `GET /v1/users/{user_uuid}`<br>(showuser) | YELLOW | 경로 파라미터 명명이 표준과 다름 | {user_uuid} vs {*_id} in /v1/users/{user_uuid} |
| `GET /v1/users/{user_uuid}`<br>(showuser) | YELLOW | 형식이 유효한 존재하지-않는 id 조회에 404가 아닌 400/403 반환 — 클라이언트가 "형식 오류/권한 문제/부재"를 구분할 수 없음. 참고: 같은 플랫폼에서 192개 API는 404를 반환하므로 (존재여부 은닉 등) 의도된 정책이 아니라 비일관 | non-existent id -> 400 (not 404) |

#### management/loggingaudit — 2건

| API | 심각도 | 문제 | 근거 |
|---|---|---|---|
| `POST /v1/trails`<br>(createtrail) | YELLOW | 필수 파라미터의 값 형식·제약·출처가 API Reference에 없음 | required fields with no documented constraint: account_id, bucket_name, bucket_region, trail_name |
| `POST /v1/logs/download`<br>(downloadlogs) | YELLOW | 필수 파라미터의 값 형식·제약·출처가 API Reference에 없음 | required fields with no documented constraint: end_at, time_zone_info |

#### management/network-logging — 1건

| API | 심각도 | 문제 | 근거 |
|---|---|---|---|
| `POST /v1/network-logging/storages`<br>(createnetworkloggingstorage) | YELLOW | 필수 파라미터의 값 형식·제약·출처가 API Reference에 없음 | required fields with no documented constraint: bucket_name |

#### management/organization — 11건

| API | 심각도 | 문제 | 근거 |
|---|---|---|---|
| `POST /v1/organization-accounts`<br>(createaccount) | YELLOW | 필수 파라미터의 값 형식·제약·출처가 API Reference에 없음 | required fields with no documented constraint: login_id, name, organization_id, role_name |
| `POST /v1/organizations`<br>(createorganization) | YELLOW | 필수 파라미터의 값 형식·제약·출처가 API Reference에 없음 | required fields with no documented constraint: name |
| `POST /v1/organization-units`<br>(createorganizationunit) | YELLOW | 필수 파라미터의 값 형식·제약·출처가 API Reference에 없음 | required fields with no documented constraint: name |
| `POST /v1/service-control-policies`<br>(createservicecontrolpolicy) | YELLOW | 필수 파라미터의 값 형식·제약·출처가 API Reference에 없음 | required fields with no documented constraint: description, name, organization_id |
| `GET /v1/organizations`<br>(listorganizations) | YELLOW | 목록 조회에 ?size=1을 보내도 전량 반환(size 무시 — 실측: listservertypes 121개, listbillingitemids 276개), 또는 페이징 메타(count/total) 부재로 클라이언트가 순회 종료 시점을 알 수 없음 | ignores size=1 (returned 2) |
| `GET /v1/organization-units/{unit_id}/parents`<br>(listparents) | YELLOW | 형식이 유효한 존재하지-않는 id 조회에 404가 아닌 400/403 반환 — 클라이언트가 "형식 오류/권한 문제/부재"를 구분할 수 없음. 참고: 같은 플랫폼에서 192개 API는 404를 반환하므로 (존재여부 은닉 등) 의도된 정책이 아니라 비일관 | non-existent id -> 403 (not 404) |
| `PUT /v1/organization-accounts/parent`<br>(moveaccount) | YELLOW | 필수 파라미터의 값 형식·제약·출처가 API Reference에 없음 | required fields with no documented constraint: parent_unit_id |
| `PUT /v1/service-control-policies/{policy_id}`<br>(setservicecontrolpolicy) | YELLOW | 필수 파라미터의 값 형식·제약·출처가 API Reference에 없음 | required fields with no documented constraint: organization_id |
| `GET /v1/organization-accounts/{account_id}`<br>(showaccount) | YELLOW | 형식이 유효한 존재하지-않는 id 조회에 404가 아닌 400/403 반환 — 클라이언트가 "형식 오류/권한 문제/부재"를 구분할 수 없음. 참고: 같은 플랫폼에서 192개 API는 404를 반환하므로 (존재여부 은닉 등) 의도된 정책이 아니라 비일관 | non-existent id -> 403 (not 404) |
| `GET /v1/organizations/{organization_id}`<br>(showorganization) | YELLOW | 형식이 유효한 존재하지-않는 id 조회에 404가 아닌 400/403 반환 — 클라이언트가 "형식 오류/권한 문제/부재"를 구분할 수 없음. 참고: 같은 플랫폼에서 192개 API는 404를 반환하므로 (존재여부 은닉 등) 의도된 정책이 아니라 비일관 | non-existent id -> 403 (not 404) |
| `GET /v1/service-control-policies/{policy_id}`<br>(showservicecontrolpolicy) | YELLOW | 형식이 유효한 존재하지-않는 id 조회에 404가 아닌 400/403 반환 — 클라이언트가 "형식 오류/권한 문제/부재"를 구분할 수 없음. 참고: 같은 플랫폼에서 192개 API는 404를 반환하므로 (존재여부 은닉 등) 의도된 정책이 아니라 비일관 | non-existent id -> 403 (not 404) |

#### management/quota — 2건

| API | 심각도 | 문제 | 근거 |
|---|---|---|---|
| `GET /v1/account-quotas`<br>(listaccountquota) | YELLOW | 목록 조회에 ?size=1을 보내도 전량 반환(size 무시 — 실측: listservertypes 121개, listbillingitemids 276개), 또는 페이징 메타(count/total) 부재로 클라이언트가 순회 종료 시점을 알 수 없음 | ignores size=1 (returned 2) |
| `GET /v1/quota-requests`<br>(listquotarequests) | YELLOW | 목록 조회에 ?size=1을 보내도 전량 반환(size 무시 — 실측: listservertypes 121개, listbillingitemids 276개), 또는 페이징 메타(count/total) 부재로 클라이언트가 순회 종료 시점을 알 수 없음 | ignores size=1 (returned 2) |

#### management/resourcemanager — 8건

| API | 심각도 | 문제 | 근거 |
|---|---|---|---|
| `POST /v1/resource-groups`<br>(createresourcegroup) | YELLOW | 필수 파라미터의 값 형식·제약·출처가 API Reference에 없음 | required fields with no documented constraint: description, name |
| `GET /v1/tags/{srn}`<br>(listresourcetags) | YELLOW | 형식이 유효한 존재하지-않는 id 조회에 404가 아닌 400/403 반환 — 클라이언트가 "형식 오류/권한 문제/부재"를 구분할 수 없음. 참고: 같은 플랫폼에서 192개 API는 404를 반환하므로 (존재여부 은닉 등) 의도된 정책이 아니라 비일관 | non-existent id -> 400 (not 404) |
| `GET /v1/tags/keys`<br>(listtagkeys) | YELLOW | 목록 조회에 ?size=1을 보내도 전량 반환(size 무시 — 실측: listservertypes 121개, listbillingitemids 276개), 또는 페이징 메타(count/total) 부재로 클라이언트가 순회 종료 시점을 알 수 없음 | ignores size=1 (returned 4) |
| `GET /v1/tags/values`<br>(listtagvalues) | YELLOW | 목록 조회에 ?size=1을 보내도 전량 반환(size 무시 — 실측: listservertypes 121개, listbillingitemids 276개), 또는 페이징 메타(count/total) 부재로 클라이언트가 순회 종료 시점을 알 수 없음 | ignores size=1 (returned 87) |
| `PUT /v1/resource-groups/{resource_group_id}`<br>(setresourcegroup) | YELLOW | 필수 파라미터의 값 형식·제약·출처가 API Reference에 없음 | required fields with no documented constraint: description |
| `GET /v1/resources/{srn}`<br>(showresource) | YELLOW | 형식이 유효한 존재하지-않는 id 조회에 404가 아닌 400/403 반환 — 클라이언트가 "형식 오류/권한 문제/부재"를 구분할 수 없음. 참고: 같은 플랫폼에서 192개 API는 404를 반환하므로 (존재여부 은닉 등) 의도된 정책이 아니라 비일관 | non-existent id -> 400 (not 404) |
| `PUT /v1/tags/{region}/{service}/{resource_type}/{resource_identifier}/{key}`<br>(updatecomponentstagvalue) | YELLOW | 필수 파라미터의 값 형식·제약·출처가 API Reference에 없음 | required fields with no documented constraint: value |
| `PUT /v1/tags/{srn}/{key}`<br>(updateresourcetagvalue) | YELLOW | 필수 파라미터의 값 형식·제약·출처가 API Reference에 없음 | required fields with no documented constraint: value |

#### management/servicewatch — 26건

| API | 심각도 | 문제 | 근거 |
|---|---|---|---|
| `GET /v1/event-rules/{event_rule_id}`<br>(showeventrule) | RED | 존재하지 않는 리소스 조회가 200 반환 | non-existent id -> 200 (should be 404) |
| `GET /v1/log-groups/{log_group_id}`<br>(showloggroup) | RED | 존재하지 않는 리소스 조회가 200 반환 | non-existent id -> 200 (should be 404) |
| `POST /v1/alerts`<br>(createalert) | YELLOW | 필수 파라미터의 값 형식·제약·출처가 API Reference에 없음 | required fields with no documented constraint: metric_id, name, namespace_id |
| `POST /v1/log-groups/log-streams/collect/custom`<br>(createcustomlogstream) | YELLOW | 필수 파라미터의 값 형식·제약·출처가 API Reference에 없음 | required fields with no documented constraint: log_group_name, log_stream_name |
| `POST /v1/metrics/custom/meta`<br>(createcustommetricmetas) | YELLOW | 필수 파라미터의 값 형식·제약·출처가 API Reference에 없음 | required fields with no documented constraint: namespace |
| `POST /v1/metrics/custom`<br>(createcustommetrics) | YELLOW | 성공(2xx) 응답 스키마가 문서에 없음 | POST 2xx documents no schema |
| `POST /v1/dashboards`<br>(createdashboard) | YELLOW | 필수 파라미터의 값 형식·제약·출처가 API Reference에 없음 | required fields with no documented constraint: name |
| `POST /v1/event-rules`<br>(createeventrule) | YELLOW | 필수 파라미터의 값 형식·제약·출처가 API Reference에 없음 | required fields with no documented constraint: name, service_id |
| `POST /v1/log-groups`<br>(createloggroup) | YELLOW | 필수 파라미터의 값 형식·제약·출처가 API Reference에 없음 | required fields with no documented constraint: name |
| `POST /v1/log-groups/export-tasks`<br>(createloggroupexporttask) | YELLOW | 필수 파라미터의 값 형식·제약·출처가 API Reference에 없음 | required fields with no documented constraint: bucket_name, end_at, log_group_id, start_at |
| `POST /v1/log-groups/{log_group_id}/log-streams`<br>(createloggrouplogstream) | YELLOW | 필수 파라미터의 값 형식·제약·출처가 API Reference에 없음 | required fields with no documented constraint: name |
| `POST /v1/metrics/data/download/image`<br>(downloadmetricdataimage) | YELLOW | 성공(2xx) 응답 스키마가 문서에 없음 | POST 2xx documents no schema |
| `GET /v1/alerts/{id}/notifications`<br>(listalertnotifications) | YELLOW | 경로 파라미터 명명이 표준과 다름 | bare {id} in /v1/alerts/{id}/notifications |
| `GET /v1/alerts`<br>(listalerts) | YELLOW | 목록 조회에 ?size=1을 보내도 전량 반환(size 무시 — 실측: listservertypes 121개, listbillingitemids 276개), 또는 페이징 메타(count/total) 부재로 클라이언트가 순회 종료 시점을 알 수 없음 | ignores size=1 (returned 2) |
| `GET /v1/dashboards`<br>(listdashboards) | YELLOW | 실제 응답에 문서에 없는 필드 존재 | response has undocumented field(s): namespace_code,service_code |
| `GET /v1/log-groups`<br>(listloggroups) | YELLOW | 목록 조회에 ?size=1을 보내도 전량 반환(size 무시 — 실측: listservertypes 121개, listbillingitemids 276개), 또는 페이징 메타(count/total) 부재로 클라이언트가 순회 종료 시점을 알 수 없음 | ignores size=1 (returned 2) |
| `POST /v1/metrics/data`<br>(listmetricdata) | YELLOW | 엔드포인트 이름의 동사와 HTTP 메서드 불일치 | read-verb name but not GET (POST /v1/metrics/data) |
| `POST /v1/metrics`<br>(listmetricinfos) | YELLOW | 엔드포인트 이름의 동사와 HTTP 메서드 불일치 | read-verb name but not GET (POST /v1/metrics) |
| `PATCH /v1/alerts/{id}`<br>(setalert) | YELLOW | 경로 파라미터 명명이 표준과 다름 | bare {id} in /v1/alerts/{id} |
| `PATCH /v1/alerts/{id}`<br>(setalert) | YELLOW | 필수 파라미터의 값 형식·제약·출처가 API Reference에 없음 | required fields with no documented constraint: metric_id, namespace_id |
| `PATCH /v1/alerts/{id}/activated`<br>(setalertactivated) | YELLOW | 경로 파라미터 명명이 표준과 다름 | bare {id} in /v1/alerts/{id}/activated |
| `PATCH /v1/alerts/{id}/description`<br>(setalertdescription) | YELLOW | 경로 파라미터 명명이 표준과 다름 | bare {id} in /v1/alerts/{id}/description |
| `PUT /v1/alerts/{id}/notifications`<br>(setalertnotifications) | YELLOW | 경로 파라미터 명명이 표준과 다름 | bare {id} in /v1/alerts/{id}/notifications |
| `PATCH /v1/event-rules/{event_rule_id}`<br>(seteventrule) | YELLOW | 필수 파라미터의 값 형식·제약·출처가 API Reference에 없음 | required fields with no documented constraint: service_id |
| `GET /v1/alerts/{id}`<br>(showalert) | YELLOW | 경로 파라미터 명명이 표준과 다름 | bare {id} in /v1/alerts/{id} |
| `GET /v1/alerts/level-counts-by-service`<br>(showalertlevelcountsbyservice) | YELLOW | 실제 응답에 문서에 없는 필드 존재 | response has undocumented field(s): namespace_code |

### networking

#### networking/cdn — 11건

| API | 심각도 | 문제 | 근거 |
|---|---|---|---|
| `POST /v1/cdns`<br>(createcdnservice) | YELLOW | 필수 파라미터의 값 형식·제약·출처가 API Reference에 없음 | required fields with no documented constraint: caching_option, cdn_origin_hostname, cdn_origin_protocol, cdn_service_domain_prefix, content_policy, name |
| `DELETE /v1/cdns/{id}`<br>(deletecdnservice) | YELLOW | 경로 파라미터 명명이 표준과 다름 | bare {id} in /v1/cdns/{id} |
| `GET /v1/cdns/{id}`<br>(detailcdnservice) | YELLOW | 경로 파라미터 명명이 표준과 다름 | bare {id} in /v1/cdns/{id} |
| `POST /v1/cdns/{id}/purge`<br>(purgecdnservice) | YELLOW | 경로 파라미터 명명이 표준과 다름 | bare {id} in /v1/cdns/{id}/purge |
| `POST /v1/cdns/{id}/purge`<br>(purgecdnservice) | YELLOW | 필수 파라미터의 값 형식·제약·출처가 API Reference에 없음 | required fields with no documented constraint: target_content, target_url |
| `POST /v1/cdns/{id}/start`<br>(startcdnservice) | YELLOW | 경로 파라미터 명명이 표준과 다름 | bare {id} in /v1/cdns/{id}/start |
| `POST /v1/cdns/{id}/stop`<br>(stopcdnservice) | YELLOW | 경로 파라미터 명명이 표준과 다름 | bare {id} in /v1/cdns/{id}/stop |
| `PUT /v1/cdns/{id}`<br>(updatecdnservice) | YELLOW | 경로 파라미터 명명이 표준과 다름 | bare {id} in /v1/cdns/{id} |
| `PUT /v1/cdns/{id}`<br>(updatecdnservice) | YELLOW | 필수 파라미터의 값 형식·제약·출처가 API Reference에 없음 | required fields with no documented constraint: caching_option, cdn_origin_hostname, cdn_origin_protocol, content_policy |
| `PUT /v1/cdns/{id}/description`<br>(updatedescriptionofcdnservice) | YELLOW | 경로 파라미터 명명이 표준과 다름 | bare {id} in /v1/cdns/{id}/description |
| `PUT /v1/cdns/{id}/description`<br>(updatedescriptionofcdnservice) | YELLOW | 필수 파라미터의 값 형식·제약·출처가 API Reference에 없음 | required fields with no documented constraint: description |

#### networking/direct-connect — 4건

| API | 심각도 | 문제 | 근거 |
|---|---|---|---|
| `POST /v1/direct-connects`<br>(createdirectconnect) | YELLOW | 필수 파라미터의 값 형식·제약·출처가 API Reference에 없음 | required fields with no documented constraint: name, vpc_id |
| `POST /v1/direct-connects/{direct_connect_id}/routing-rules`<br>(createroutingrule) | YELLOW | 필수 파라미터의 값 형식·제약·출처가 API Reference에 없음 | required fields with no documented constraint: destination_cidr |
| `GET /v1/direct-connects`<br>(listdirectconnects) | YELLOW | 목록 조회에 ?size=1을 보내도 전량 반환(size 무시 — 실측: listservertypes 121개, listbillingitemids 276개), 또는 페이징 메타(count/total) 부재로 클라이언트가 순회 종료 시점을 알 수 없음 | ignores size=1 (returned 2) |
| `PUT /v1/direct-connects/{direct_connect_id}`<br>(setdirectconnect) | YELLOW | 필수 파라미터의 값 형식·제약·출처가 API Reference에 없음 | required fields with no documented constraint: description |

#### networking/dns — 7건

| API | 심각도 | 문제 | 근거 |
|---|---|---|---|
| `POST /v1/private-dns/activate`<br>(activateprivatedns) | YELLOW | 필수 파라미터의 값 형식·제약·출처가 API Reference에 없음 | required fields with no documented constraint: name |
| `POST /v1/hosted-zones`<br>(createhostedzone) | YELLOW | 필수 파라미터의 값 형식·제약·출처가 API Reference에 없음 | required fields with no documented constraint: name |
| `POST /v1/hosted-zones/{hosted_zone_id}/records`<br>(createhostedzonerecord) | YELLOW | 필수 파라미터의 값 형식·제약·출처가 API Reference에 없음 | required fields with no documented constraint: name, type |
| `POST /v1/private-dns`<br>(createprivatedns) | YELLOW | 필수 파라미터의 값 형식·제약·출처가 API Reference에 없음 | required fields with no documented constraint: name |
| `POST /v1/public-domain-names`<br>(createpublicdomainname) | YELLOW | 필수 파라미터의 값 형식·제약·출처가 API Reference에 없음 | required fields with no documented constraint: address_type, domestic_first_address_en, domestic_first_address_ko, domestic_second_address_en, domestic_second_address_ko, name, overseas_first_address, overseas_second_address |
| `GET /v1/private-dns`<br>(listprivatedns) | YELLOW | 목록 조회에 ?size=1을 보내도 전량 반환(size 무시 — 실측: listservertypes 121개, listbillingitemids 276개), 또는 페이징 메타(count/total) 부재로 클라이언트가 순회 종료 시점을 알 수 없음 | ignores size=1 (returned 2) |
| `PUT /v1/public-domain-names/{public_domain_id}/information`<br>(setpublicdomainnamewhoisinfo) | YELLOW | 필수 파라미터의 값 형식·제약·출처가 API Reference에 없음 | required fields with no documented constraint: address_type, domestic_first_address_en, domestic_first_address_ko, domestic_second_address_en, domestic_second_address_ko, overseas_first_address, overseas_second_address, overseas_third_address |

#### networking/firewall — 3건

| API | 심각도 | 문제 | 근거 |
|---|---|---|---|
| `POST /v1/firewalls/rules`<br>(createfirewallrule) | YELLOW | 필수 파라미터의 값 형식·제약·출처가 API Reference에 없음 | required fields with no documented constraint: firewall_id |
| `POST /v1/firewalls/rules`<br>(createfirewallrule) | YELLOW | 버전에 따라 응답 시맨틱이 다른데(1.1=202+빈 바디) 문서는 1.0 동작만 기술 | Endpoint-pinned version 'firewall 1.1' -> 202 + an EMPTY body {} (no rule id capturable from the response); the doc page only describes the 1.0/201-with-full-body semantics. A caller pinned (or defaulted) to 1.1 who doesn't know to poll listfirewallrules inste |
| `GET /v1/firewalls`<br>(listfirewalls) | YELLOW | 목록 조회에 ?size=1을 보내도 전량 반환(size 무시 — 실측: listservertypes 121개, listbillingitemids 276개), 또는 페이징 메타(count/total) 부재로 클라이언트가 순회 종료 시점을 알 수 없음 | ignores size=1 (returned 2) |

#### networking/gslb — 5건

| API | 심각도 | 문제 | 근거 |
|---|---|---|---|
| `POST /v1/gslbs`<br>(creategslb) | YELLOW | 필수 파라미터의 값 형식·제약·출처가 API Reference에 없음 | required fields with no documented constraint: name |
| `GET /v1/gslbs`<br>(listgslbs) | YELLOW | 목록 조회에 ?size=1을 보내도 전량 반환(size 무시 — 실측: listservertypes 121개, listbillingitemids 276개), 또는 페이징 메타(count/total) 부재로 클라이언트가 순회 종료 시점을 알 수 없음 | ignores size=1 (returned 3) |
| `GET /v1/gslbs/routing-control`<br>(listgslbsregionalroutingcontrol) | YELLOW | 목록 조회에 ?size=1을 보내도 전량 반환(size 무시 — 실측: listservertypes 121개, listbillingitemids 276개), 또는 페이징 메타(count/total) 부재로 클라이언트가 순회 종료 시점을 알 수 없음 | ignores size=1 (returned 3) |
| `PUT /v1/gslbs/{gslb_id}/health-check`<br>(setgslbhealthcheck) | YELLOW | 필수 파라미터의 값 형식·제약·출처가 API Reference에 없음 | required fields with no documented constraint: protocol |
| `PUT /v1/gslbs/{gslb_id}/routing-control`<br>(setgslbregionalroutingcontrol) | YELLOW | 필수 파라미터의 값 형식·제약·출처가 API Reference에 없음 | required fields with no documented constraint: region |

#### networking/loadbalancer — 1건

| API | 심각도 | 문제 | 근거 |
|---|---|---|---|
| `GET /v1/loadbalancers/{loadbalancer_id}/static-nats`<br>(showloadbalancerpublicnatip) | YELLOW | 빈 컬렉션 조회가 404 반환 | GET .../{loadbalancer_id}/static-nats on a load balancer with zero NAT IPs attached -> 404 instead of 200 [] (empty collection represented as not-found rather than an empty successful list). 실측 2026-07-16. |

#### networking/security-group — 5건

| API | 심각도 | 문제 | 근거 |
|---|---|---|---|
| `POST /v1/address-groups`<br>(createaddressgroup) | YELLOW | 필수 파라미터의 값 형식·제약·출처가 API Reference에 없음 | required fields with no documented constraint: name |
| `POST /v1/security-groups`<br>(createsecuritygroup) | YELLOW | 필수 파라미터의 값 형식·제약·출처가 API Reference에 없음 | required fields with no documented constraint: name |
| `POST /v1/security-group-rules`<br>(createsecuritygrouprule) | YELLOW | 필수 파라미터의 값 형식·제약·출처가 API Reference에 없음 | required fields with no documented constraint: direction, security_group_id |
| `PUT /v1/address-groups/{address_group_id}`<br>(setaddressgroup) | YELLOW | 성공(2xx) 응답 스키마가 문서에 없음 | PUT 2xx documents no schema |
| `PUT /v1/security-groups/{security_group_id}`<br>(setsecuritygroup) | YELLOW | 성공(2xx) 응답 스키마가 문서에 없음 | PUT 2xx documents no schema |

#### networking/vpc — 39건

| API | 심각도 | 문제 | 근거 |
|---|---|---|---|
| `GET /v1/subnets`<br>(listsubnets) | RED | 생성(v1.3)은 되는 PRIVATE 타입 서브넷이 조회 계열(v1.2 enum)에서 보이지 않음 — API로 존재를 확인할 수 없는 리소스 발생 | GET listsubnets?type=PRIVATE -> 400 "Input should be 'GENERAL', 'LOCAL' or 'VPC_ENDPOINT'" (PRIVATE rejected as a filter value) even though createsubnet documents and accepts PRIVATE. PRIVATE subnet enum was added in createsubnet v1.3 (PUBLIC/PRIVATE/LOCAL/VPC |
| `GET /v1/subnets/{subnet_id}`<br>(showsubnet) | RED | 생성(v1.3)은 되는 PRIVATE 타입 서브넷이 조회 계열(v1.2 enum)에서 보이지 않음 — API로 존재를 확인할 수 없는 리소스 발생 | GET showsubnet on a PRIVATE-typed subnet -> 404 "Not found with ID With Invalid Type" even though the subnet exists and DELETE on the same id succeeds (202). PRIVATE subnet enum was added in createsubnet v1.3 (PUBLIC/PRIVATE/LOCAL/VPC_ENDPOINT) and create/dele |
| `POST /v1/vpcs/{vpc_id}/cidrs`<br>(addvpccidr) | YELLOW | 필수 파라미터의 값 형식·제약·출처가 API Reference에 없음 | required fields with no documented constraint: cidr |
| `POST /v1/internet-gateways`<br>(createinternetgateway) | YELLOW | 필수 파라미터의 값 형식·제약·출처가 API Reference에 없음 | required fields with no documented constraint: vpc_id |
| `POST /v1/nat-gateways`<br>(createnatgateway) | YELLOW | 필수 파라미터의 값 형식·제약·출처가 API Reference에 없음 | required fields with no documented constraint: subnet_id |
| `POST /v1/ports`<br>(createport) | YELLOW | 필수 파라미터의 값 형식·제약·출처가 API Reference에 없음 | required fields with no documented constraint: name, subnet_id |
| `POST /v1/privatelink-endpoints`<br>(createprivatelinkendpoint) | YELLOW | 필수 파라미터의 값 형식·제약·출처가 API Reference에 없음 | required fields with no documented constraint: endpoint_ip_address, name, subnet_id |
| `POST /v1/privatelink-services`<br>(createprivatelinkservice) | YELLOW | 필수 파라미터의 값 형식·제약·출처가 API Reference에 없음 | required fields with no documented constraint: name, service_ip_address, subnet_id |
| `POST /v1/private-nats`<br>(createprivatenat) | YELLOW | 필수 파라미터의 값 형식·제약·출처가 API Reference에 없음 | required fields with no documented constraint: cidr, name, service_resource_id |
| `POST /v1/private-nats/{private_nat_id}/private-nat-ips`<br>(createprivatenatip) | YELLOW | 필수 파라미터의 값 형식·제약·출처가 API Reference에 없음 | required fields with no documented constraint: ip_address |
| `POST /v1/publicips`<br>(createpublicip) | YELLOW | 필수 파라미터의 값 형식·제약·출처가 API Reference에 없음 | required fields with no documented constraint: zone |
| `POST /v1/subnets`<br>(createsubnet) | YELLOW | 필수 파라미터의 값 형식·제약·출처가 API Reference에 없음 | required fields with no documented constraint: cidr, name, vpc_id |
| `POST /v1/subnets/{subnet_id}/vips/{vip_id}/static-nat-ips`<br>(createsubnetvipnatip) | YELLOW | 필수 파라미터의 값 형식·제약·출처가 API Reference에 없음 | required fields with no documented constraint: nat_type, publicip_id |
| `POST /v1/subnets/{subnet_id}/vips/{vip_id}/connected-ports`<br>(createsubnetvipport) | YELLOW | 필수 파라미터의 값 형식·제약·출처가 API Reference에 없음 | required fields with no documented constraint: port_id |
| `POST /v1/transit-gateways`<br>(createtransitgateway) | YELLOW | 필수 파라미터의 값 형식·제약·출처가 API Reference에 없음 | required fields with no documented constraint: name |
| `POST /v1/transit-gateways/{transit_gateway_id}/routing-rules`<br>(createtransitgatewayrule) | YELLOW | 필수 파라미터의 값 형식·제약·출처가 API Reference에 없음 | required fields with no documented constraint: destination_cidr, tgw_connection_vpc_id |
| `POST /v1/transit-gateways/{transit_gateway_id}/uplink-routing-rules`<br>(createtransitgatewayuplinkrule) | YELLOW | 필수 파라미터의 값 형식·제약·출처가 API Reference에 없음 | required fields with no documented constraint: destination_cidr |
| `POST /v1/transit-gateways/{transit_gateway_id}/vpc-connections`<br>(createtransitgatewayvpcconnection) | YELLOW | 필수 파라미터의 값 형식·제약·출처가 API Reference에 없음 | required fields with no documented constraint: vpc_id |
| `POST /v1/vpcs`<br>(createvpc) | YELLOW | 필수 파라미터의 값 형식·제약·출처가 API Reference에 없음 | required fields with no documented constraint: cidr, name |
| `POST /v1/vpc-endpoints`<br>(createvpcendpoint) | YELLOW | 필수 파라미터의 값 형식·제약·출처가 API Reference에 없음 | required fields with no documented constraint: endpoint_ip_address, name, resource_info, resource_key, subnet_id, vpc_id |
| `POST /v1/vpc-peerings`<br>(createvpcpeering) | YELLOW | 필수 파라미터의 값 형식·제약·출처가 API Reference에 없음 | required fields with no documented constraint: approver_vpc_account_id, approver_vpc_id, name, requester_vpc_id |
| `POST /v1/vpc-peerings/{vpc_peering_id}/routing-rules`<br>(createvpcpeeringrule) | YELLOW | 필수 파라미터의 값 형식·제약·출처가 API Reference에 없음 | required fields with no documented constraint: destination_cidr |
| `DELETE /v1/privatelink-services/{privatelink_service_id}`<br>(deleteprivatelinkservice) | YELLOW | 생성/변경 202 후 상태가 안정(ACTIVE)되기 전의 set/delete를 400으로 거절 — 400은 "요청 자체가 잘못됨" 신호라 클라이언트가 "기다렸다 재시도" 판단을 할 수 없음. 같은 상태-충돌에 플랫폼 내 다른 서비스는 이미 409를 반환(SCR creating-cannot-delete 409, VIP connected-ports 409 — 실측 원문 §5.5 인접) | DELETE while the resource is still CREATING (async 202 from the preceding create/set) -> 400 invalid-state; the doc page never states that a caller must poll to ACTIVE before mutating — 자동화 클라이언트 following only the endpoint doc hits an undocumented race. 실측 20 |
| `DELETE /v1/vpc-endpoints/{vpc_endpoint_id}`<br>(deletevpcendpoint) | YELLOW | 생성/변경 202 후 상태가 안정(ACTIVE)되기 전의 set/delete를 400으로 거절 — 400은 "요청 자체가 잘못됨" 신호라 클라이언트가 "기다렸다 재시도" 판단을 할 수 없음. 같은 상태-충돌에 플랫폼 내 다른 서비스는 이미 409를 반환(SCR creating-cannot-delete 409, VIP connected-ports 409 — 실측 원문 §5.5 인접) | DELETE while the resource is still CREATING (async 202 from the preceding create/set) -> 400 invalid-state; the doc page never states that a caller must poll to ACTIVE before mutating — 자동화 클라이언트 following only the endpoint doc hits an undocumented race. 실측 20 |
| `GET /v1/internet-gateways`<br>(listinternetgateways) | YELLOW | 목록 조회에 ?size=1을 보내도 전량 반환(size 무시 — 실측: listservertypes 121개, listbillingitemids 276개), 또는 페이징 메타(count/total) 부재로 클라이언트가 순회 종료 시점을 알 수 없음 | ignores size=1 (returned 2) |
| `GET /v1/nat-gateways`<br>(listnatgateways) | YELLOW | 목록 조회에 ?size=1을 보내도 전량 반환(size 무시 — 실측: listservertypes 121개, listbillingitemids 276개), 또는 페이징 메타(count/total) 부재로 클라이언트가 순회 종료 시점을 알 수 없음 | ignores size=1 (returned 2) |
| `GET /v1/privatelink-endpoints`<br>(listprivatelinkendpoints) | YELLOW | 목록 조회에 ?size=1을 보내도 전량 반환(size 무시 — 실측: listservertypes 121개, listbillingitemids 276개), 또는 페이징 메타(count/total) 부재로 클라이언트가 순회 종료 시점을 알 수 없음 | ignores size=1 (returned 2) |
| `GET /v1/privatelink-services`<br>(listprivatelinkservices) | YELLOW | 목록 조회에 ?size=1을 보내도 전량 반환(size 무시 — 실측: listservertypes 121개, listbillingitemids 276개), 또는 페이징 메타(count/total) 부재로 클라이언트가 순회 종료 시점을 알 수 없음 | ignores size=1 (returned 2) |
| `GET /v1/private-nats`<br>(listprivatenats) | YELLOW | 목록 조회에 ?size=1을 보내도 전량 반환(size 무시 — 실측: listservertypes 121개, listbillingitemids 276개), 또는 페이징 메타(count/total) 부재로 클라이언트가 순회 종료 시점을 알 수 없음 | ignores size=1 (returned 2) |
| `GET /v1/publicips`<br>(listpublicip) | YELLOW | 목록 조회에 ?size=1을 보내도 전량 반환(size 무시 — 실측: listservertypes 121개, listbillingitemids 276개), 또는 페이징 메타(count/total) 부재로 클라이언트가 순회 종료 시점을 알 수 없음 | ignores size=1 (returned 2) |
| `GET /v1/subnets`<br>(listsubnets) | YELLOW | 목록 조회에 ?size=1을 보내도 전량 반환(size 무시 — 실측: listservertypes 121개, listbillingitemids 276개), 또는 페이징 메타(count/total) 부재로 클라이언트가 순회 종료 시점을 알 수 없음 | ignores size=1 (returned 2) |
| `GET /v1/transit-gateways`<br>(listtransitgateways) | YELLOW | 목록 조회에 ?size=1을 보내도 전량 반환(size 무시 — 실측: listservertypes 121개, listbillingitemids 276개), 또는 페이징 메타(count/total) 부재로 클라이언트가 순회 종료 시점을 알 수 없음 | ignores size=1 (returned 2) |
| `GET /v1/vpc-endpoints`<br>(listvpcendpoints) | YELLOW | 목록 조회에 ?size=1을 보내도 전량 반환(size 무시 — 실측: listservertypes 121개, listbillingitemids 276개), 또는 페이징 메타(count/total) 부재로 클라이언트가 순회 종료 시점을 알 수 없음 | ignores size=1 (returned 2) |
| `GET /v1/vpc-peerings`<br>(listvpcpeerings) | YELLOW | 목록 조회에 ?size=1을 보내도 전량 반환(size 무시 — 실측: listservertypes 121개, listbillingitemids 276개), 또는 페이징 메타(count/total) 부재로 클라이언트가 순회 종료 시점을 알 수 없음 | ignores size=1 (returned 2) |
| `GET /v1/vpcs`<br>(listvpcs) | YELLOW | 목록 조회에 ?size=1을 보내도 전량 반환(size 무시 — 실측: listservertypes 121개, listbillingitemids 276개), 또는 페이징 메타(count/total) 부재로 클라이언트가 순회 종료 시점을 알 수 없음 | ignores size=1 (returned 2) |
| `PUT /v1/privatelink-services/{privatelink_service_id}`<br>(setprivatelinkservice) | YELLOW | 생성/변경 202 후 상태가 안정(ACTIVE)되기 전의 set/delete를 400으로 거절 — 400은 "요청 자체가 잘못됨" 신호라 클라이언트가 "기다렸다 재시도" 판단을 할 수 없음. 같은 상태-충돌에 플랫폼 내 다른 서비스는 이미 409를 반환(SCR creating-cannot-delete 409, VIP connected-ports 409 — 실측 원문 §5.5 인접) | PUT (set) while the resource is still CREATING (async 202 from the preceding create/set) -> 400 invalid-state; the doc page never states that a caller must poll to ACTIVE before mutating — 자동화 클라이언트 following only the endpoint doc hits an undocumented race. 실측 |
| `PUT /v1/publicips/{publicip_id}`<br>(setpublicip) | YELLOW | 필수 파라미터의 값 형식·제약·출처가 API Reference에 없음 | required fields with no documented constraint: description |
| `PUT /v1/subnets/{subnet_id}/vips/{vip_id}`<br>(setsubnetvip) | YELLOW | 필수 파라미터의 값 형식·제약·출처가 API Reference에 없음 | required fields with no documented constraint: description |
| `PUT /v1/vpc-endpoints/{vpc_endpoint_id}`<br>(setvpcendpoint) | YELLOW | 생성/변경 202 후 상태가 안정(ACTIVE)되기 전의 set/delete를 400으로 거절 — 400은 "요청 자체가 잘못됨" 신호라 클라이언트가 "기다렸다 재시도" 판단을 할 수 없음. 같은 상태-충돌에 플랫폼 내 다른 서비스는 이미 409를 반환(SCR creating-cannot-delete 409, VIP connected-ports 409 — 실측 원문 §5.5 인접) | PUT (set) while the resource is still CREATING (async 202 from the preceding create/set) -> 400 invalid-state; the doc page never states that a caller must poll to ACTIVE before mutating — 자동화 클라이언트 following only the endpoint doc hits an undocumented race. 실측 |

#### networking/vpn — 6건

| API | 심각도 | 문제 | 근거 |
|---|---|---|---|
| `POST /v1/vpn-gateways`<br>(createvpngateway) | YELLOW | 필수 파라미터의 값 형식·제약·출처가 API Reference에 없음 | required fields with no documented constraint: ip_address, ip_id, ip_type, name, vpc_id |
| `POST /v1/vpn-tunnels`<br>(createvpntunnel) | YELLOW | 필수 파라미터의 값 형식·제약·출처가 API Reference에 없음 | required fields with no documented constraint: name, vpn_gateway_id |
| `GET /v1/vpn-gateways`<br>(listvpngateways) | YELLOW | 목록 조회에 ?size=1을 보내도 전량 반환(size 무시 — 실측: listservertypes 121개, listbillingitemids 276개), 또는 페이징 메타(count/total) 부재로 클라이언트가 순회 종료 시점을 알 수 없음 | ignores size=1 (returned 2) |
| `GET /v1/vpn-tunnels`<br>(listvpntunnels) | YELLOW | 목록 조회에 ?size=1을 보내도 전량 반환(size 무시 — 실측: listservertypes 121개, listbillingitemids 276개), 또는 페이징 메타(count/total) 부재로 클라이언트가 순회 종료 시점을 알 수 없음 | ignores size=1 (returned 2) |
| `PUT /v1/vpn-gateways/{vpn_gateway_id}`<br>(setvpngateway) | YELLOW | 생성/변경 202 후 상태가 안정(ACTIVE)되기 전의 set/delete를 400으로 거절 — 400은 "요청 자체가 잘못됨" 신호라 클라이언트가 "기다렸다 재시도" 판단을 할 수 없음. 같은 상태-충돌에 플랫폼 내 다른 서비스는 이미 409를 반환(SCR creating-cannot-delete 409, VIP connected-ports 409 — 실측 원문 §5.5 인접) | PUT (set) while the resource is still EDITING (async 202 from the preceding create/set) -> 400 invalid-state; the doc page never states that a caller must poll to ACTIVE before mutating — 자동화 클라이언트 following only the endpoint doc hits an undocumented race. 실측/ |
| `PUT /v1/vpn-tunnels/{vpn_tunnel_id}`<br>(setvpntunnel) | YELLOW | 생성/변경 202 후 상태가 안정(ACTIVE)되기 전의 set/delete를 400으로 거절 — 400은 "요청 자체가 잘못됨" 신호라 클라이언트가 "기다렸다 재시도" 판단을 할 수 없음. 같은 상태-충돌에 플랫폼 내 다른 서비스는 이미 409를 반환(SCR creating-cannot-delete 409, VIP connected-ports 409 — 실측 원문 §5.5 인접) | PUT (set) while the resource is still EDITING (async 202 from the preceding create/set) -> 400 invalid-state; the doc page never states that a caller must poll to ACTIVE before mutating — 자동화 클라이언트 following only the endpoint doc hits an undocumented race. 실측/ |

### platform

#### platform/product — 2건

| API | 심각도 | 문제 | 근거 |
|---|---|---|---|
| `GET /v1/product-categories`<br>(listproductcategories) | YELLOW | 목록 조회에 ?size=1을 보내도 전량 반환(size 무시 — 실측: listservertypes 121개, listbillingitemids 276개), 또는 페이징 메타(count/total) 부재로 클라이언트가 순회 종료 시점을 알 수 없음 | ignores size=1 (returned 14) |
| `GET /v1/products`<br>(listproducts) | YELLOW | 목록 조회에 ?size=1을 보내도 전량 반환(size 무시 — 실측: listservertypes 121개, listbillingitemids 276개), 또는 페이징 메타(count/total) 부재로 클라이언트가 순회 종료 시점을 알 수 없음 | ignores size=1 (returned 20) |

#### platform/sts — 3건

| API | 심각도 | 문제 | 근거 |
|---|---|---|---|
| `POST /v1/assume-role`<br>(assumerole) | YELLOW | 필수 파라미터의 값 형식·제약·출처가 API Reference에 없음 | required fields with no documented constraint: role_indicator, role_session_name |
| `POST /v1/assume-role-with-saml`<br>(assumerolewithsaml) | YELLOW | 필수 파라미터의 값 형식·제약·출처가 API Reference에 없음 | required fields with no documented constraint: principal_indicator, role_indicator, saml_assertion |
| `POST /v1/object-store-authorization`<br>(objectstoreauthorization) | YELLOW | 필수 파라미터의 값 형식·제약·출처가 API Reference에 없음 | required fields with no documented constraint: method, url, x_amz_content_sha256, x_amz_date |

### security

#### security/certificatemanager — 4건

| API | 심각도 | 문제 | 근거 |
|---|---|---|---|
| `POST /v1/certificatemanager/check-duplication`<br>(checknameduplication) | YELLOW | 필수 파라미터의 값 형식·제약·출처가 API Reference에 없음 | required fields with no documented constraint: name |
| `POST /v1/certificatemanager`<br>(createcertificate) | YELLOW | 필수 파라미터의 값 형식·제약·출처가 API Reference에 없음 | required fields with no documented constraint: cert_body, name, private_key, region, timezone |
| `POST /v1/certificatemanager/self-sign`<br>(selfsigncert) | YELLOW | 필수 파라미터의 값 형식·제약·출처가 API Reference에 없음 | required fields with no documented constraint: cn, name, not_after_dt, organization, region, timezone |
| `POST /v1/certificatemanager/check-validation`<br>(validatecertificate) | YELLOW | 필수 파라미터의 값 형식·제약·출처가 API Reference에 없음 | required fields with no documented constraint: cert_body, private_key |

#### security/configinspection — 2건

| API | 심각도 | 문제 | 근거 |
|---|---|---|---|
| `POST /v1/configinspection/diagnosis/save`<br>(creatediagnosisobject) | YELLOW | 필수 파라미터의 값 형식·제약·출처가 API Reference에 없음 | required fields with no documented constraint: account_id, check_list_version_id, csp_type, diagnosis_account_id, diagnosis_check_type, diagnosis_id, diagnosis_name, diagnosis_type |
| `POST /v1/configinspection/diagnosis/request`<br>(diagnosisrequest) | YELLOW | 필수 파라미터의 값 형식·제약·출처가 API Reference에 없음 | required fields with no documented constraint: access_key, diagnosis_id, secret_key, tenant_id |

#### security/kms — 15건

| API | 심각도 | 문제 | 근거 |
|---|---|---|---|
| `PUT /v1/kms/transit/{key_id}/rotate-info`<br>(changerotateinfo) | YELLOW | 필수 파라미터의 값 형식·제약·출처가 API Reference에 없음 | required fields with no documented constraint: auto_rotate |
| `PUT /v1/kms/transit/{key_id}/state`<br>(changestatekey) | YELLOW | 필수 파라미터의 값 형식·제약·출처가 API Reference에 없음 | required fields with no documented constraint: state |
| `POST /v1/kms/transit`<br>(createkey) | YELLOW | 필수 파라미터의 값 형식·제약·출처가 API Reference에 없음 | required fields with no documented constraint: auto_rotate, description, name, purpose |
| `POST /v1/kms/openapi/datakey/{key_id}`<br>(datakey) | YELLOW | 필수 파라미터의 값 형식·제약·출처가 API Reference에 없음 | required fields with no documented constraint: key_type |
| `POST /v1/kms/openapi/decrypt/{key_id}`<br>(decryptdata) | YELLOW | 필수 파라미터의 값 형식·제약·출처가 API Reference에 없음 | required fields with no documented constraint: ciphertext |
| `POST /v1/kms/openapi/encrypt/{key_id}`<br>(encryptdata) | YELLOW | 필수 파라미터의 값 형식·제약·출처가 API Reference에 없음 | required fields with no documented constraint: plaintext |
| `POST /v1/kms/openapi/hmac/{key_id}`<br>(hmac) | YELLOW | 필수 파라미터의 값 형식·제약·출처가 API Reference에 없음 | required fields with no documented constraint: input |
| `PUT /v1/kms/transit/{key_id}/acl-cidr`<br>(kmssetaclcidr) | YELLOW | 필수 파라미터의 값 형식·제약·출처가 API Reference에 없음 | required fields with no documented constraint: acl_cidr |
| `GET /v1/kms/transit`<br>(listkeys) | YELLOW | 목록 조회에 ?size=1을 보내도 전량 반환(size 무시 — 실측: listservertypes 121개, listbillingitemids 276개), 또는 페이징 메타(count/total) 부재로 클라이언트가 순회 종료 시점을 알 수 없음 | ignores size=1 (returned 2) |
| `GET /v1/managed-kms/transit`<br>(listmanagedkeys) | YELLOW | 목록 조회에 ?size=1을 보내도 전량 반환(size 무시 — 실측: listservertypes 121개, listbillingitemids 276개), 또는 페이징 메타(count/total) 부재로 클라이언트가 순회 종료 시점을 알 수 없음 | ignores size=1 (returned 2) |
| `POST /v1/kms/openapi/rewrap/{key_id}`<br>(rewrapdata) | YELLOW | 필수 파라미터의 값 형식·제약·출처가 API Reference에 없음 | required fields with no documented constraint: ciphertext |
| `POST /v1/kms/openapi/sign/{key_id}`<br>(signdata) | YELLOW | 필수 파라미터의 값 형식·제약·출처가 API Reference에 없음 | required fields with no documented constraint: input |
| `PUT /v1/kms/transit/{key_id}/description`<br>(updatedescription) | YELLOW | 필수 파라미터의 값 형식·제약·출처가 API Reference에 없음 | required fields with no documented constraint: description |
| `PUT /v1/managed-kms/transit/{key_id}/description`<br>(updatemanagedkeydescription) | YELLOW | 필수 파라미터의 값 형식·제약·출처가 API Reference에 없음 | required fields with no documented constraint: description |
| `POST /v1/kms/openapi/verify/{key_id}`<br>(verifydata) | YELLOW | 필수 파라미터의 값 형식·제약·출처가 API Reference에 없음 | required fields with no documented constraint: input, signature |

#### security/secretsmanager — 6건

| API | 심각도 | 문제 | 근거 |
|---|---|---|---|
| `POST /v1/secrets`<br>(createsecretsmanager) | YELLOW | 필수 파라미터의 값 형식·제약·출처가 API Reference에 없음 | required fields with no documented constraint: kms_id, name |
| `GET /v1/secrets`<br>(listsecretsmanager) | YELLOW | 목록 조회에 ?size=1을 보내도 전량 반환(size 무시 — 실측: listservertypes 121개, listbillingitemids 276개), 또는 페이징 메타(count/total) 부재로 클라이언트가 순회 종료 시점을 알 수 없음 | ignores size=1 (returned 2) |
| `PUT /v1/secrets/{secret_id}/kmsid`<br>(setkmsid) | YELLOW | 필수 파라미터의 값 형식·제약·출처가 API Reference에 없음 | required fields with no documented constraint: kms_id |
| `PUT /v1/secrets/{secret_id}/description`<br>(setsecretdescription) | YELLOW | 필수 파라미터의 값 형식·제약·출처가 API Reference에 없음 | required fields with no documented constraint: description |
| `PUT /v1/secrets/{secret_id}/label`<br>(setsecretsmanagerlabel) | YELLOW | 성공(2xx) 응답 스키마가 문서에 없음 | PUT 2xx documents no schema |
| `POST /v1/secrets/{secret_id}/values`<br>(showsecretsmanagersecretvalue) | YELLOW | 엔드포인트 이름의 동사와 HTTP 메서드 불일치 | read-verb name but not GET (POST /v1/secrets/{secret_id}/values) |

#### security/secretvault — 3건

| API | 심각도 | 문제 | 근거 |
|---|---|---|---|
| `POST /v1/secretvault`<br>(createsecretvault) | YELLOW | 필수 파라미터의 값 형식·제약·출처가 API Reference에 없음 | required fields with no documented constraint: access_key_id, acl_cidr, name |
| `GET /v1/temporarykey/{secret_vault_id}`<br>(gettemporarykey) | YELLOW | 형식이 유효한 존재하지-않는 id 조회에 404가 아닌 400/403 반환 — 클라이언트가 "형식 오류/권한 문제/부재"를 구분할 수 없음. 참고: 같은 플랫폼에서 192개 API는 404를 반환하므로 (존재여부 은닉 등) 의도된 정책이 아니라 비일관 | non-existent id -> 400 (not 404) |
| `GET /v1/secretvault`<br>(listsecretvault) | YELLOW | 목록 조회에 ?size=1을 보내도 전량 반환(size 무시 — 실측: listservertypes 121개, listbillingitemids 276개), 또는 페이징 메타(count/total) 부재로 클라이언트가 순회 종료 시점을 알 수 없음 | ignores size=1 (returned 2) |

### storage

#### storage/archivestorage — 8건

| API | 심각도 | 문제 | 근거 |
|---|---|---|---|
| `PUT /v1/archiving-histories/cancel-archiving`<br>(cancelarchiving) | YELLOW | 성공(2xx) 응답 스키마가 문서에 없음 | PUT 2xx documents no schema |
| `PUT /v1/recovery-histories/cancel-recovery`<br>(cancelrecovery) | YELLOW | 성공(2xx) 응답 스키마가 문서에 없음 | PUT 2xx documents no schema |
| `POST /v1/archiving-policies`<br>(createarchivingpolicy) | YELLOW | 필수 파라미터의 값 형식·제약·출처가 API Reference에 없음 | required fields with no documented constraint: archiving_source_bucket_name, object_lifecycle, object_path |
| `POST /v1/buckets`<br>(createbucket) | YELLOW | 필수 파라미터의 값 형식·제약·출처가 API Reference에 없음 | required fields with no documented constraint: name |
| `PUT /v1/buckets/{bucket_id}/recover-objects`<br>(recoverobjects) | YELLOW | 필수 파라미터의 값 형식·제약·출처가 API Reference에 없음 | required fields with no documented constraint: recovery_target_bucket_name |
| `PUT /v1/buckets/{bucket_id}/recover-object-versions`<br>(recoverobjectversions) | YELLOW | 필수 파라미터의 값 형식·제약·출처가 API Reference에 없음 | required fields with no documented constraint: recovery_target_bucket_name, source_object_path, source_object_version |
| `PUT /v1/archiving-policies/{archiving_policy_id}`<br>(setarchivingpolicy) | YELLOW | 필수 파라미터의 값 형식·제약·출처가 API Reference에 없음 | required fields with no documented constraint: object_lifecycle |
| `PUT /v1/archiving-policies/{archiving_policy_id}/state`<br>(setarchivingpolicystate) | YELLOW | 필수 파라미터의 값 형식·제약·출처가 API Reference에 없음 | required fields with no documented constraint: state |

#### storage/backup — 7건

| API | 심각도 | 문제 | 근거 |
|---|---|---|---|
| `POST /v1/backups`<br>(createbackup) | YELLOW | 필수 파라미터의 값 형식·제약·출처가 API Reference에 없음 | required fields with no documented constraint: name, server_uuid |
| `POST /v1/backup-agents`<br>(createbackupagent) | YELLOW | 필수 파라미터의 값 형식·제약·출처가 API Reference에 없음 | required fields with no documented constraint: server_uuid |
| `GET /v1/backups/region-relationship`<br>(listbackupregionrelationship) | YELLOW | 목록 조회에 ?size=1을 보내도 전량 반환(size 무시 — 실측: listservertypes 121개, listbillingitemids 276개), 또는 페이징 메타(count/total) 부재로 클라이언트가 순회 종료 시점을 알 수 없음 | ignores size=1 (returned 2) |
| `GET /v1/backups/{backup_id}/restore/restorable-subnets`<br>(listbackuprestoresubnets) | YELLOW | 형식이 유효한 존재하지-않는 id 조회에 404가 아닌 400/403 반환 — 클라이언트가 "형식 오류/권한 문제/부재"를 구분할 수 없음. 참고: 같은 플랫폼에서 192개 API는 404를 반환하므로 (존재여부 은닉 등) 의도된 정책이 아니라 비일관 | non-existent id -> 400 (not 404) |
| `GET /v1/backups/{backup_id}/filesystem-path`<br>(listfilesystempath) | YELLOW | 형식이 유효한 존재하지-않는 id 조회에 404가 아닌 400/403 반환 — 클라이언트가 "형식 오류/권한 문제/부재"를 구분할 수 없음. 참고: 같은 플랫폼에서 192개 API는 404를 반환하므로 (존재여부 은닉 등) 의도된 정책이 아니라 비일관 | non-existent id -> 400 (not 404) |
| `POST /v1/backups/{backup_id}/restore-agent-backup`<br>(restoreagentbackup) | YELLOW | 필수 파라미터의 값 형식·제약·출처가 API Reference에 없음 | required fields with no documented constraint: restore_server_uuid, restore_target_id |
| `POST /v1/backups/{backup_id}/restore`<br>(restorebackup) | YELLOW | 필수 파라미터의 값 형식·제약·출처가 API Reference에 없음 | required fields with no documented constraint: restore_server_name, restore_target_id, server_type_id |

#### storage/baremetal-blockstorage — 6건

| API | 심각도 | 문제 | 근거 |
|---|---|---|---|
| `POST /v1/volumes`<br>(createvolume) | YELLOW | 필수 파라미터의 값 형식·제약·출처가 API Reference에 없음 | required fields with no documented constraint: name, zone |
| `POST /v1/volume-groups`<br>(createvolumegroup) | YELLOW | 필수 파라미터의 값 형식·제약·출처가 API Reference에 없음 | required fields with no documented constraint: name, zone |
| `POST /v1/volume-groups/{volume_group_id}/recoveries`<br>(createvolumegrouprecovery) | YELLOW | 필수 파라미터의 값 형식·제약·출처가 API Reference에 없음 | required fields with no documented constraint: block_storage_name_prefix, snapshot_id |
| `POST /v1/volume-groups/{volume_group_id}/replications`<br>(createvolumegroupreplication) | YELLOW | 필수 파라미터의 값 형식·제약·출처가 API Reference에 없음 | required fields with no documented constraint: name, region, replication_volume_name_prefix, zone |
| `POST /v1/volumes/{volume_id}/recoveries`<br>(createvolumerecovery) | YELLOW | 필수 파라미터의 값 형식·제약·출처가 API Reference에 없음 | required fields with no documented constraint: name, snapshot_id |
| `POST /v1/volumes/{volume_id}/replications`<br>(createvolumereplication) | YELLOW | 필수 파라미터의 값 형식·제약·출처가 API Reference에 없음 | required fields with no documented constraint: name, region, zone |

#### storage/filestorage — 8건

| API | 심각도 | 문제 | 근거 |
|---|---|---|---|
| `POST /v1/snapshots`<br>(createsnapshot) | YELLOW | 필수 파라미터의 값 형식·제약·출처가 API Reference에 없음 | required fields with no documented constraint: volume_id |
| `POST /v1/snapshot-schedules`<br>(createsnapshotschedule) | YELLOW | 필수 파라미터의 값 형식·제약·출처가 API Reference에 없음 | required fields with no documented constraint: volume_id |
| `POST /v1/volumes`<br>(createvolume) | YELLOW | 필수 파라미터의 값 형식·제약·출처가 API Reference에 없음 | required fields with no documented constraint: name, protocol, type_name, zone |
| `POST /v1/replications`<br>(createvolumereplication) | YELLOW | 필수 파라미터의 값 형식·제약·출처가 API Reference에 없음 | required fields with no documented constraint: name, replication_frequency, zone, volume_id, replication_type |
| `GET /v1/replications/regions`<br>(listvolumereplicationregion) | YELLOW | 문서에 명시된 API 버전을 서버가 406으로 거절 | docs-derived pin 'filestorage /v1/replications/regions 1.1' -> 406 NoSuchVersion against the product pin; served via the no-pin fallback. Confirmed 실측 2026-07-16. |
| `PUT /v1/volumes/{volume_id}/access-rules`<br>(setaccessrule) | YELLOW | 필수 파라미터의 값 형식·제약·출처가 API Reference에 없음 | required fields with no documented constraint: object_id, object_type, action |
| `PUT /v1/replications/{replication_id}`<br>(setvolumereplication) | YELLOW | 필수 파라미터의 값 형식·제약·출처가 API Reference에 없음 | required fields with no documented constraint: replication_update_type |
| `GET /v1/replications/{replication_id}`<br>(showvolumereplication) | YELLOW | 형식이 유효한 존재하지-않는 id 조회에 404가 아닌 400/403 반환 — 클라이언트가 "형식 오류/권한 문제/부재"를 구분할 수 없음. 참고: 같은 플랫폼에서 192개 API는 404를 반환하므로 (존재여부 은닉 등) 의도된 정책이 아니라 비일관 | non-existent id -> 400 (not 404) |

#### storage/parallel-filestorage — 4건

| API | 심각도 | 문제 | 근거 |
|---|---|---|---|
| `POST /v1/snapshots`<br>(createsnapshot) | YELLOW | 필수 파라미터의 값 형식·제약·출처가 API Reference에 없음 | required fields with no documented constraint: volume_id |
| `POST /v1/volumes`<br>(createvolume) | YELLOW | 필수 파라미터의 값 형식·제약·출처가 API Reference에 없음 | required fields with no documented constraint: name, zone |
| `PUT /v1/snapshots/{snapshot_id}/restore`<br>(restoresnapshot) | YELLOW | 성공(2xx) 응답 스키마가 문서에 없음 | PUT 2xx documents no schema |
| `PUT /v1/volumes/{volume_id}/capacity`<br>(setvolumecapacity) | YELLOW | 성공(2xx) 응답 스키마가 문서에 없음 | PUT 2xx documents no schema |
