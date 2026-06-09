# Samsung Cloud Platform v2 — API ↔ Terraform Provider 커버리지 갭 상세 리포트

> **대상:** 개발팀
> **분석일:** 2026-06-09
> **목적:** SCP v2 공개 API 대비 Terraform Provider가 제공하지 못하는 기능을 식별하고, 구현 우선순위를 제시한다.

## 분석 기준 데이터

| 항목 | 출처 | 수치 |
|---|---|---|
| API 카탈로그 | `data/api_catalog.json` (docs.e.samsungsdscloud.com/apireference 스크랩) | **1,372 endpoints / 59 services / 13 categories** |
| Terraform Provider | `terraform-provider-samsungcloudplatformv2` (`docs/resources`, `docs/data-sources`) | resource 87 / data-source 168 / **35 services** |

**매핑 방법론**
- API `category/service` ↔ Terraform `docs/resources` 파일 prefix 자동 매칭 후, 명명 규칙 차이(복수형, `virtualserver_*`, `security_group_*` 등)는 수동 보정.
- "관리(생성)가능 리소스" 판정은 API의 `POST/create*` + `DELETE` 존재 여부 기준. 즉 **CRUD가 성립하는 리소스**를 Terraform resource 후보로 봄.
- `GET`만 존재하는 서비스/엔드포인트는 data-source 후보로 분류(별도 표기).
- 액션성 엔드포인트(예: `assume-role`, `kubeconfig` 발급, `encrypt/decrypt`)는 선언형 리소스 대상이 아니므로 참고용으로만 표기.

---

## 1. 총괄 요약

| 구분 | 서비스 수 | API endpoint 수 |
|---|---:|---:|
| API 전체 | 59 | 1,372 |
| Terraform 지원 | 35 | — |
| **Terraform 전혀 미지원** | **24** | **401 (약 29%)** |

갭은 두 종류로 나뉜다.

- **Tier 1 — 서비스 단위 갭:** 서비스 자체가 Provider에 없음 (24개 서비스).
- **Tier 2 — 리소스/기능 단위 갭:** 서비스는 있으나 하위 리소스·운영 기능이 누락 (VirtualServer Auto-Scaling, BareMetal BlockStorage 스냅샷/복제, VPC PrivateLink, DB 리드 레플리카 등).

### 권장 구현 우선순위 (Tier 1)

| 우선순위 | 서비스 | 근거 |
|---|---|---|
| **P1 (높음)** | apigateway, kms, secretsmanager, scr, scf, organization, iam-identity-center | IaC 수요 높음 · 보안/거버넌스 핵심 · 엔드포인트 다수 |
| **P2 (중간)** | cloudcontrol, archivestorage, cdn, queueservice, parallel-filestorage, secretvault | 인프라 리소스이나 수요/범위 중간 |
| **P3 (낮음)** | data-flow, data-ops, quick-query, aimlops-platform, cloud-ml, devopsservice | 특수 워크로드 · 사용자층 제한적 |
| **N/A (조회전용)** | costexplorer, pricing, support, product, sts | 선언형 리소스 부적합 → 필요 시 data-source만 |

---

## 2. Tier 1 — Terraform 전혀 미지원 서비스 (24개)

각 서비스의 관리 대상 리소스(경로 그룹)와 제공 메서드를 정리한다. 제안 리소스명은 `samsungcloudplatformv2_<service>_<resource>` 규칙을 따른다.

### 2.1 P1 — 우선 구현 권장

#### application-service / apigateway — 55 endpoints  *(단일 서비스 최대 갭)*
| 리소스(경로) | 메서드 | CRUD | 제안 Terraform |
|---|---|---|---|
| `apis` | GET/POST/PUT/DELETE (47) | ✅ Full | `_apigateway_api` (resource), `_apigateway_apis` (ds) |
| `privatelink-endpoints` | GET/POST/PUT/DELETE (8) | ✅ Full | `_apigateway_privatelink_endpoint` |

> API 정의·스테이지·리소스·메서드 등 47개 오퍼레이션이 `apis` 하위에 집중. 가장 투자 대비 효과가 큰 미구현 영역.

#### security / kms — 20 endpoints  *(보안 핵심)*
| 리소스(경로) | 메서드 | CRUD | 제안 Terraform |
|---|---|---|---|
| `kms/transit` | GET/POST/PUT/DELETE (9) | ✅ Full | `_kms_key` (resource), `_kms_keys` (ds) |
| `managed-kms/transit` | GET/PUT (3) | 부분 | `_kms_managed_key` |
| `kms/openapi/{encrypt,decrypt,datakey,hmac,rewrap,sign,verify}` | POST (7) | 액션 | (data-source/함수 부적합 — 참고) |

#### security / secretsmanager — 15 endpoints
| 리소스(경로) | 메서드 | CRUD | 제안 Terraform |
|---|---|---|---|
| `secrets` | GET/POST/PUT/DELETE (13) | ✅ Full | `_secretsmanager_secret`, `_secretsmanager_secrets` (ds) |
| `secrets/kms-key` | POST (1) | 액션 | (secret 리소스 속성으로 통합 가능) |
| `secrets/random-password` | POST (1) | 액션 | (참고) |

#### container / scr (Container Registry) — 39 endpoints
| 리소스(경로) | 메서드 | CRUD | 제안 Terraform |
|---|---|---|---|
| `container-registries` | GET/POST/PUT/DELETE (8) | ✅ Full | `_scr_registry`, `_scr_registries` (ds) |
| `repositories` | GET/POST/PUT/DELETE (9) | ✅ Full | `_scr_repository`, `_scr_repositories` (ds) |
| `images` | GET/POST/PUT/DELETE (10) | ✅ Full | `_scr_image` (ds 중심) |
| `tags` | GET/PUT/DELETE (9) | 부분 | `_scr_tag` |
| `*/check-duplication/name`, `connectable-resources` | GET (3) | 조회 | (검증/보조 — 참고) |

#### compute / scf (Serverless Cloud Functions) — 36 endpoints
| 리소스(경로) | 메서드 | CRUD | 제안 Terraform |
|---|---|---|---|
| `cloud-functions` | GET/POST/PUT/DELETE (27) | ✅ Full | `_scf_function`, `_scf_functions` (ds) |
| `triggers`, `triggers/apigateway`, `triggers/cronjob` | GET/POST/PUT/DELETE (6) | ✅ Full | `_scf_trigger` |
| `cloud-functions/{runtimes,sample-codes,check-duplication}` | GET (3) | 조회 | `_scf_runtimes` (ds) |

#### management / organization — 37 endpoints  *(계정/거버넌스)*
| 리소스(경로) | 메서드 | CRUD | 제안 Terraform |
|---|---|---|---|
| `organizations` | GET/POST/PUT/DELETE (6) | ✅ Full | `_organization` |
| `organization-units` | GET/POST/PUT/DELETE (6) | ✅ Full | `_organization_unit` |
| `organization-accounts` | GET/POST/DELETE (5) | ✅ | `_organization_account` |
| `service-control-policies` | GET/POST/PUT/DELETE (5) | ✅ Full | `_organization_service_control_policy` |
| `delegation-policies` | GET/POST/PUT/DELETE (4) | ✅ Full | `_organization_delegation_policy` |
| `assignments/policy-bindings` | POST/DELETE (2) | 바인딩 | `_organization_policy_binding` |
| `invitations`, `account-invitations` 등 | POST/PUT/GET | 액션/조회 | (참고) |

#### management / iam-identity-center (SSO) — 32 endpoints
| 리소스(경로) | 메서드 | CRUD | 제안 Terraform |
|---|---|---|---|
| `instances` | GET/POST/PATCH/DELETE (5) | ✅ Full | `_identitycenter_instance` |
| `users` | GET/POST/PATCH/DELETE (6) | ✅ Full | `_identitycenter_user` |
| `groups` | GET/POST/PATCH/DELETE (9) | ✅ Full | `_identitycenter_group` |
| `permission-sets` | GET/POST/PUT/PATCH/DELETE (8) | ✅ Full | `_identitycenter_permission_set` |
| `account-assignments` | GET/POST/DELETE (4) | ✅ | `_identitycenter_account_assignment` |

### 2.2 P2 — 중간 우선순위

#### management / cloudcontrol (Landing Zone / Guardrail) — 15 endpoints
| 리소스(경로) | 메서드 | CRUD | 제안 |
|---|---|---|---|
| `landing-zones` | GET/POST/PUT/DELETE (4) | ✅ Full | `_cloudcontrol_landing_zone` |
| `baseline-assignments` | GET/POST/PUT/DELETE (4) | ✅ Full | `_cloudcontrol_baseline_assignment` |
| `guardrail-bindings` | POST/DELETE (2) | 바인딩 | `_cloudcontrol_guardrail_binding` |
| `guardrails`, `accounts` 등 | GET/POST | 조회/액션 | (참고) |

#### storage / archivestorage — 25 endpoints
| 리소스(경로) | 메서드 | CRUD | 제안 |
|---|---|---|---|
| `buckets` | GET/POST/PUT/DELETE (14) | ✅ Full | `_archivestorage_bucket`, `_archivestorage_buckets` (ds) |
| `archiving-policies` | GET/POST/PUT (5) | ✅ | `_archivestorage_archiving_policy` |
| `archiving-histories`, `recovery-histories` (+detail/cancel) | GET/PUT (6) | 조회/액션 | `_archivestorage_*_histories` (ds) |

#### networking / cdn — 9 endpoints
| 리소스(경로) | 메서드 | CRUD | 제안 |
|---|---|---|---|
| `cdns` | GET/POST/PUT/DELETE + start/stop (9) | ✅ Full | `_cdn_cdn`, `_cdn_cdns` (ds) |

#### application-service / queueservice — 12 endpoints
| 리소스(경로) | 메서드 | CRUD | 제안 |
|---|---|---|---|
| `queues` | GET/POST/PUT/DELETE (11) | ✅ Full | `_queueservice_queue`, `_queueservice_queues` (ds) |

#### storage / parallel-filestorage — 11 endpoints
| 리소스(경로) | 메서드 | CRUD | 제안 |
|---|---|---|---|
| `volumes` | GET/POST/PUT/DELETE (7) | ✅ Full | `_parallelfilestorage_volume` |
| `snapshots` | GET/POST/PUT/DELETE (4) | ✅ Full | `_parallelfilestorage_snapshot` |

#### security / secretvault — 5 endpoints
| 리소스(경로) | 메서드 | CRUD | 제안 |
|---|---|---|---|
| `secretvault` | GET/POST/PUT (4) | 부분(삭제 없음) | `_secretvault_vault` |
| `temporarykey` | GET (1) | 조회 | (참고) |

### 2.3 P3 — 낮은 우선순위 (특수 워크로드)

| 서비스 | endpoints | 주요 리소스(경로) | CRUD |
|---|---:|---|---|
| data-analytics / data-flow | 17 | `data-flow-services`, `data-flows` (+clusters) | ✅ Full |
| data-analytics / data-ops | 17 | `data-ops-services`, `data-ops` (+clusters) | ✅ Full |
| data-analytics / quick-query | 12 | `quick-query` | ✅ Full |
| ai-ml / aimlops-platform | 12 | `aimlops-platform` (+clusters/images) | ✅ Full |
| ai-ml / cloud-ml | 9 | `cloud-ml` | ✅ Full |
| devops-tools / devopsservice | 6 | `devops-services` | ✅ (create/delete) |

### 2.4 N/A — 조회 전용(선언형 리소스 부적합)

| 서비스 | endpoints | 성격 | 비고 |
|---|---:|---|---|
| financial-management / costexplorer | 3 | GET only (`bills`, `usages`, `payments/monthly`) | data-source만 가능 |
| financial-management / pricing | 3 | GET only (`reports/*`) | data-source만 가능 |
| management / support | 4 | GET only (`inquiries`, `service-requests`) | data-source만 가능 |
| platform / product | 4 | GET only (`products`, `product-categories`) | data-source만 가능 |
| platform / sts | 3 | POST 액션 (`assume-role`, `assume-role-with-saml`, `object-store-authorization`) | provider 인증 흐름에 통합 검토 |

---

## 3. Tier 2 — 지원 서비스 내부의 리소스/기능 갭

서비스는 존재하나 API에 있는 생성 가능 하위 리소스/운영 기능이 Provider에 없는 경우.

### compute / virtualserver  (API 113 / TF resource 5)
TF 제공: `server`, `volume`, `image`, `keypair`, `server_group`. **누락:**
- **Auto-Scaling Group 일체** — `auto-scaling-groups` + policy / schedule / notification
- **Launch Configuration** — `launch-configurations`
- **Server Interface(NIC)** — `servers/{id}/interfaces` (+ static-nat / private-static-nat)
- 이미지 공유/멤버 — `images/{id}/share`, `images/{id}/members`
- 커스텀 이미지 생성 — `servers/{id}/images`
- 메모리 덤프 — `servers/{id}/dump`
- 볼륨 소유권 전송 — `volume-transfer`

### storage / baremetal-blockstorage  (API 41 / TF resource 1) — **비율상 최대 갭**
TF 제공: `baremetal_blockstorage_volume` 만. **누락:**
- 볼륨 스냅샷 — `volumes/{id}/snapshots`, 스냅샷 스케줄, 스냅샷 rate
- 볼륨 복제 — `volumes/{id}/replications`
- 볼륨 복구 — `volumes/{id}/recoveries`
- 볼륨 그룹 — `volume-groups` (+ 그룹 스냅샷/복제/복구/스케줄)
- 볼륨 attachment — `volumes/{id}/attachments`

### networking / vpc  (API 95 / TF resource 22)
- **PrivateLink Endpoint** — `privatelink-endpoints`
- **PrivateLink Service** — `privatelink-services`

### database — cachestore / epas / mariadb / mysql / postgresql / sqlserver
TF 제공: 각 `*_cluster` 리소스만. 클러스터 생성·기본 백업/유지보수/서버타입/블록스토리지 옵션은 생성 시 속성으로 커버되나, 다음 운영 기능은 **별도 리소스가 없어 IaC 관리 불가:**
- **리드 레플리카 / 타 리전 레플리카** — `create-replica`, `create-other-region-replica`
- **레플리카 승격 / Switchover** — `promote-replica`, `switchover`
- **백업으로부터 복원** — `create-restore`
- **파라미터 값(파라미터 그룹) 관리** — `set-parameter-values`
- **로그 export 설정** — `register-log-export-config`
- 아카이브 설정 — `set-archive-config` (일부 엔진)

### management / servicewatch  (API 31)
- Custom Metric — `metrics/custom`, `metrics/custom/meta`
- Custom Log Stream / Event — `log-groups/log-streams/...`

### management / iam  (API 62)
- **SAML Provider** — `saml-providers` (외부 IdP 연동)

### storage / backup  (API 31)
- **Backup Agent** — `backup-agents`

### storage / filestorage  (API 21)
- 온디맨드 스냅샷 — `snapshots` (TF는 `snapshot_schedule`만 제공)

---

## 4. 부록 — 재현 방법

```bash
# 미지원 서비스/리소스 그룹 재집계
cd api-test-automation
python3 - <<'PY'
import json
from collections import Counter
api=json.load(open('data/api_catalog.json'))
print('total', len(api))
print(Counter((d['category'],d['service']) for d in api))
PY
```

- 원천 데이터: `data/api_catalog.json` (각 항목에 `category/service/name/method/http_path/doc_url` 포함)
- Terraform 리소스 목록: `terraform-provider-samsungcloudplatformv2/docs/resources`, `.../data-sources`
