# SCP API 품질 컨포먼스 리포트 — 2026-08-20

> **스코프**: 카탈로그 전 엔드포인트 **1,416개** (2026-08-20 fresh 재수집 기준 1,417 발견 / 1,416 해석; 테스트 가능 여부와 무관하게 전수 판정).
> **방법**: AXIS 2 정적 분석(`conformance.static`, 스펙/문서/모델 규칙) + 동적 분석(런타임 프로브: schema/status/notfound/pagination/options/l10n/validation + 실측 런 6954·19f2·3e67의 라이브 확인 결함 28건, req-id 증거 동봉).
> **데이터**: `data/conformance.json` (이 리포트와 같은 커밋에서 재생성).

## 1. 요약 판정

| 판정 | 엔드포인트 | 비율 |
|---|---:|---:|
| 🟢 GREEN (결함 없음) | **919** | 64.9% |
| 🟡 YELLOW (개선 필요) | **466** | 32.9% |
| 🔴 RED (심각 — 우선 수정) | **31** | 2.2% |

발견 총 **554건** = 정적 375 + 런타임(동적) 179. 별도로 **시스템 전반(systemic) 결함 6종**이 사실상 전 API에 걸쳐 있음(아래 §2 — 엔드포인트 판정에 중복 계상하지 않음).

## 2. 시스템 전반 결함 (플랫폼 공통 — 최우선 구조 개선 대상)

| # | 결함 | 영향 범위 | 내용 |
|---|---|---|---|
| 15 | **error-schema-undocumented** | 1,414 EP | 4xx/5xx 응답 스키마가 문서에 전무. 실측 에러 엔벨로프가 ≥3종(표준 errors[], Spring 기본, WAF HTML) 혼재 |
| 17 | model-fields-no-description | 463 모델 필드 | 모델 필드 설명 공란 |
| 40 | accept-language-ignored | 124 EP | Accept-Language 무시, 에러 메시지 영어 고정 |
| 13 | path-collisions | 78 경로 | 서비스 간 동일 method+path 재사용 (네임스페이스 없음) |
| 36 | **unauth-404** | 58 서비스 | 미인증 요청이 401이 아니라 404+Spring 엔벨로프 |
| 39 | no-cors | 58 서비스 | OPTIONS→403, Allow/CORS 헤더 없음 |

## 3. RED 31 — 클래스별 전체 목록

### A. 클라이언트 상태에 500 (`runtime.500-on-client-state`) — 9개
클라이언트가 유발한 상태(4xx로 안내해야 할 상황)에 500/ContactAdminForAssistance를 반환. 자동화(재시도 판단)를 직접 방해하는 최상위 결함.

- container/ske/**createnodepool** (500 → 콘솔-미러 바디로만 회피 가능)
- database/{mysql,mariadb,epas,postgresql}/**registerlogexportconfig** (4개)
- database/postgresql/**setparametervalues**
- application-service/apigateway/**approveprivatelinkendpoint** (run 3e67 재확인: 승인 PUT 500)
- data-analytics/quick-query/**validatequickqueryresources**
- container/scr/**showregistry** (생성 직후 show 500)

### B. 생성계 파라미터 불투명 (`undiscoverable-params` + `opaque-validation`) — 12개
필수 바디 파라미터의 값 출처가 문서 어디에도 없고(발견 불가), 잘못 주면 검증 메시지도 불투명. **모든 DBaaS 계열 createcluster가 공통**:

- database/{postgresql,mysql,mariadb,epas,sqlserver,cachestore}/**createcluster** (6)
- data-analytics/{searchengine,eventstreams,vertica}/**createcluster** (3)
- management/iam-identity-center/**creategroup**, **createpermissionset** (2)
- management/cloudmonitoring/**puteventpolicy** (1)

### C. subnet read-plane 버전 드리프트 (`networking.subnet-read-plane-version-drift`) — 2개
- networking/vpc/**showsubnet**, **listsubnets** — v1.3으로 만든 PRIVATE subnet이 read-plane(v1.2 enum)에서 **API-비가시 유령**이 됨 (show 404 "Invalid Type", list ?type=PRIVATE 400). 실측 라이브 증명 완료.

### D. 이미지 공유 비가역 체인 (compute/virtualserver) — 3개
- **createsharingimage**: 202에 빈 바디(no-success-schema 중복) + 수락/거절 API 부재로 **취소 불가** + 임시 볼륨(104GB) 고아화 — 4중 결함
- **deleteimage**: 전송 중 삭제 무방비 (delete-during-transfer-unguarded)

### E. 잘못된 입력에 5xx (`5xx-on-bad-input`) — 2개
- storage/baremetal-blockstorage/**createvolume**, **createvolumegroup**

### F. 404여야 할 곳에 200 (`notfound-200`) — 2개
- management/servicewatch/**showloggroup**, **showeventrule** — 없는 리소스 조회가 200

### G. 스키마-실응답 불일치 — 2개
- data-analytics/data-ops/**getdataopsimageversionv1** (schema-missing-field)
- management/cloudmonitoring/**getmetricperfdatalist** (method-verb + B계열 중복)

## 4. YELLOW 주요 결함 클래스 사전 (빈도순)

| 클래스 | 건수 | src | 의미 / 개선 방향 |
|---|---:|---|---|
| undiscoverable-params | **291** | 정적 | 필수 파라미터 값의 출처(생산 endpoint)가 문서에 없음 — AI/자동화 소비성 최대 장벽. 상위: virtualserver 25, vpc 22, kms 13 |
| notfound-inconsistent | **73** | 동적 | 부재 리소스에 404 대신 400/200 등 비일관 응답 (DBaaS requests/{id}=400, 삭제된 PLE 상세=403 등). 상위: scf 12, apigateway 11 |
| no-success-schema | **55** | 정적 | 2xx 응답 스키마 미문서화. 상위: virtualserver 18, scr 17 |
| pagination | **54** | 동적 | size/page 미준수·무시·비표준 (vpc 11, iam 6) |
| param-naming | 16 | 정적 | 명명 비일관 (camel/snake 혼재 등) |
| opaque-validation | 13 | 동적 | 검증 실패 메시지가 원인 필드를 특정하지 않음 |
| method-verb | 9 | 정적 | HTTP 메서드-동사 불일치 (예: 조회가 POST) |
| docs.async-settle-undocumented | 6 | 실측 | create/set 202 후 EDITING/CREATING 중 mutate 400인데 settle 필요가 미문서 (vpce, PLS, vpn gw/tunnel) |
| schema-undocumented-field | 4 | 동적 | 실응답에 문서 밖 필드 |
| deprecated | 4 | 정적 | deprecated 표기 후 대체 안내 없음 |
| versioning.doc-version-not-supported | 3 | 실측 | 문서 명시 버전을 서버가 406 거절 |
| 기타(단건 8종) | 8 | 실측 | WAF 417 비-JSON 엔벨로프, 빈 컬렉션 404, wrong-403, 버전 시맨틱 미문서 등 |

**주의(자기모순 에러 사례, 컨포먼스 등재 예정)**: run 3e67에서 scf create가 문서 예시 그대로의 `Node.js:20`을 400 invalid-runtime으로 거절하면서 에러 메시지에 여전히 'Node.js:20'을 유효 예시로 인용 — 유효 런타임 셋 변경이 문서/에러 메시지에 미반영.

## 5. 서비스 워스트 랭킹 (red 우선, yellow 비율 차순위)

| 서비스 | EP | 🔴 | 🟡 | findings |
|---|---:|---:|---:|---:|
| database/postgresql | 49 | 3 | 8 | 13 |
| management/servicewatch | 37 | 2 | 20 | 23 |
| management/iam-identity-center | 32 | 2 | 15 | 21 |
| compute/virtualserver | 113 | 2 | 44 | 58 |
| networking/vpc | 95 | 2 | 36 | 39 |
| management/cloudmonitoring | 18 | 2 | 4 | 9 |
| database/mysql · mariadb · epas | 48~49 | 각2 | 8~9 | 12~13 |
| storage/baremetal-blockstorage | 41 | 2 | 4 | 8 |
| data-analytics/data-ops | 17 | 1 | 10 | 14 |
| container/scr | 39 | 1 | 19 | 23 |
| container/ske | 25 | 1 | 10 | 15 |
| application-service/apigateway | 55 | 1 | 22 | 26 |

카테고리 롤업: database(272EP)가 red 11로 최다, management(250EP) red 6 + yellow 89(비율 최악급), data-analytics red 5. security/ai-ml/financial-management/platform/devops-tools는 red 0.

## 6. 개선 우선순위 제안 (플랫폼팀 전달용)

- **P0 — 계약 위반(동작)**: §3-A 500-on-client-state 9건(4xx로 정정), §3-C subnet read-plane 버전 정합(유령 리소스), §3-D 이미지 공유 취소 API 신설+빈 202 바디, §3-E 5xx-on-bad-input, §3-F notfound-200. *모두 req-id 증거 보유.*
- **P1 — 계약 위반(문서/스키마)**: systemic #15 에러 스키마 표준화(엔벨로프 단일화 포함 — WAF 417 HTML 제거), no-success-schema 55건, DBaaS createcluster 계열 파라미터 카탈로그화(§3-B), async-settle 문서화 6건, 버전 정합 3건.
- **P2 — 일관성/소비성**: undiscoverable-params 291건(파라미터 생산자 명시 — AI 소비성), notfound-inconsistent 73건(404 통일), pagination 54건, unauth-404→401, Accept-Language, CORS.

## 7. 각주

- 판정·건수는 이 리포트 커밋의 `data/conformance.json`과 1:1. 시스템 전반 결함은 엔드포인트 판정에 중복 계상하지 않음.
- 동적 근거는 결과 스토어(findings) + oplog(runs/<rid>/artifact) req-id로 재추적 가능. 베이스라인(`data/baselines/conformance_baseline.json`)은 이 리포트에서 변경하지 않음.
