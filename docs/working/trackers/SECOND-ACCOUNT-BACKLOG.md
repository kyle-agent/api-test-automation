# 2번째 계정 대기 백로그 (owner: "계정 만들고 알려줄께" — 2026-06-13)

교차-계정이 필수라 현재 단일 계정으로는 검증 불가능한 op 목록.
계정이 생기면 이 목록 순서대로 모델 옵션(`peer_account_id` 등)을 배선한다.

## compute/virtualserver

| op | 경로 | 필요한 것 |
|---|---|---|
| acceptvolumetransfer | POST /v1/volume-transfer/{transfer_id}/accept | 수신 계정에서 transfer auth_key로 수락 (auth_key는 create 응답 — capture 예정) |
| createsharingimage | POST /v1/images/{image_id}/share | 공유 대상 계정 |
| createimagemember | POST /v1/images/{image_id}/members | 공유 대상 계정 id |
| showimagemember | GET /v1/images/{image_id}/members/{member_id} | member 생성 선행 |
| updateimagemember | PUT /v1/images/{image_id}/members/{member_id} | member 생성 선행 (수락은 수신 계정에서) |
| deleteimagemember | DELETE /v1/images/{image_id}/members/{member_id} | member 생성 선행 |
| listimagemembers | GET /v1/images/{image_id}/members | 빈 목록 200은 단일 계정으로 가능 (custom-image verify에 이미 배선) — 행이 있는 검증은 2계정 |

## 기타 후보 (발견 시 추가)

- IAM 교차-계정 role assume (sts role_indicator가 타계정 role을 가리키는 경우)
- PostgreSQL `createotherregionreplica`는 2계정이 아니라 **2리전** (kr-east1) — 별도 트랙

## 배선 규칙

1. 모델에 `{credential: second-account}` 또는 `peer_account_id` required 옵션
   (기본값 없음 = 폼 게이트)으로 추가 — sts role_indicator 패턴.
2. 수락(accept)류는 **수신 계정의 자격증명**으로 호출해야 하므로 엔진의
   credential 전환이 필요 — env에 `SCP_PEER_ACCESS_KEY/SECRET` 추가 후
   step 단위 `credentials:` 오버라이드(엔진 확장 필요)로 처리.
