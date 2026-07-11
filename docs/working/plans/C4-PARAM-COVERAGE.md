# C4 파라미터 커버리지 계획 (owner directive 2026-07-11)

> **오너 지시 (2026-07-11, 콘솔 협업 세션):** "개별 api가 동작한다를 넘어서,
> 특정 파라미터에 대해서 동작한다도 필요해. 모든 파라미터를 할 수는 없지만 —
> 예를 들면 VM의 여러 이미지들에 대해서 기본 생성·수정·삭제 등을 테스트하는 식."
> 이후 시나리오에 반영할 것.

## 자리매김

- `docs/COVERAGE-CRITERIA.md`의 **C4 (심화 Deepened)** 슬롯 그 자체 —
  "beyond one 2xx: status-code coverage, parameter combinations,
  response-schema validation (≈ TCL4–6)". 지금까지 미구현·post-100% 로드맵.
- 선행 백로그와 병합: **이미지 파라미터화** (owner 2026-07-08 — "레시피/런
  단위로 특정 이미지 지정 테스트, Test Plan에서 이미지 pick, 배포판별 매트릭스").
- C3(검증) 캠페인과 공존: C4는 **이미 VALIDATED된 라이프사이클**에만 얹는다
  (미검증 키에 변형을 얹으면 실패 원인이 파라미터인지 기본 경로인지 분리 불가).

## 설계 스케치 (구현 전 오너 컨펌 필요 항목 표시)

### 1. 변형(variant) 축의 선정 원칙

전 파라미터 조합은 조합 폭발 — **"실사용자가 실제로 바꾸는 축"만** 골라 1축씩:

| 우선 | 대상 | 변형 축 | 근거 |
|---|---|---|---|
| P1 | compute-virtualserver-full | **image** (배포판 매트릭스: ubuntu 22/24 · RHEL 8/9 · Windows · GPU) | 오너 예시 그대로. password(PF-17)의 Windows 의존 같은 **이미지-조건부 동작**을 이미 실측했음 — 이미지축이 실제 분기를 만든다 |
| P1 | vs 볼륨 계열 | volume_type (SSD / SSD_Provisioned / HDD?) · size 경계값 | multiple_of·min/max 제약 실측 경험 (run-923a) |
| P2 | DBaaS create | engine version 매트릭스 ([0] 최신 / [1] 직전 — patch-minor 2xx 전략과 겸용) | patch "Unpatchable(자기버전)" 확정 → 구버전 create가 유일한 2xx 경로 |
| P2 | 스토리지 | filestorage protocol (NFS/CIFS), 스냅샷 schedule 변형 | |
| P3 | 네트워킹 | subnet type (GENERAL/LB/…), publicip 용도별 | |

### 2. 엔진 메커니즘 (제안)

- 라이프사이클에 `variants` 키 신설: 라이프사이클을 N회 실행하되 지정 스텝의
  지정 필드만 치환. 예:
  ```json
  "variants": {
    "axis": "image",
    "select": {"step": "create-image", "capture": "image_id"},
    "values_from": {"path": "/v1/images?status=active&visibility=public",
                     "list": "$.images", "distinct_by": "os_distro",
                     "max": 5},
    "apply_to": ["create-server"]
  }
  ```
  — 이미지 목록에서 배포판별 대표 1개씩(최대 N) 뽑아 create-server를 반복.
- 결과 기록: observations에 `variant` 필드 추가 (`endpoint_key`는 동일 —
  C3 집계 불변; C4 대시보드가 variant 차원을 별도 집계).
- **비용 가드**: variant 반복은 heavy 자원(VM/DB)을 곱한다 —
  variant 수 × 예상시간을 pre-flight에 노출하고 명시 선택시에만 실행
  (기본 런에는 대표 1 variant = 현행과 동일).

### 3. 관측 예시가 준 힌트 (2026-07-11 스윕 로그)

`/v1/images: 20 listed / 0 deletable — skipped: UBUNTU 24.04 GPU …(name-mismatch)`
— 스윕 매처가 public 이미지들을 name-mismatch로 건너뛰는 건 정상(우리 소유
아님)이지만, 같은 20개 목록이 곧 **이미지 매트릭스의 모집단**이다. C4 1단계는
이 목록을 `distinct_by: os_distro`로 접어 5±개 대표를 뽑는 것으로 시작.

### 4. 단계 (오너 컨펌 후 착수)

1. **P0 — 계약 문서**: variants 스키마 확정 + 비용 가드 + 대시보드 C4 표시
   방식 (이 문서 갱신, 오너 리뷰).
2. **P1 — 엔진 variants 지원** (+ offline 테스트) — 이미지축 1개로 개통.
3. **P1 — compute-virtualserver-full 이미지 매트릭스** 첫 라이브 (heavy 창,
   명시 선택): 배포판 5종 × create/show/update/delete + password(Windows 케이스
   재검) — PF-17 waiver 판단 자료 겸용.
4. **P2 — DBaaS 버전축** (patch-minor 2xx 전략과 한 몸).
5. 이후 축은 실측 수요(파라미터-조건부 400 발견)가 생길 때마다 추가 —
   "모든 파라미터"가 아니라 **분기를 만드는 파라미터**만.

## 상태

- 2026-07-11: 오너 지시 접수, 계획 문서 작성. **구현 착수 전 — §2 스키마와
  §1 우선순위 오너 컨펌 대기.** C3 100% 캠페인(현행)과 병행하지 않고,
  서비스별 C3 수렴 후 해당 서비스부터 C4 진입이 기본 순서.
