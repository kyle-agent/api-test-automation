---
status: active
for: coverage-service (database/*, data-analytics/*) · coverage-validator · orchestrator
---

# NEWAPI — DBaaS instance-ops 설계 메모 (2026-07-15)

> SPEC-DIFF-20260715 §2⑧/§3의 DBaaS 인스턴스 단위 신규 액션 17개
> (showinstance ×9 · manualbackup ×6 · upgrademajorversion ×2)의 **공통 패턴
> 모델링 메모**. 전부 heavy 클러스터 종속이라 **라이브 검증 0회** (오너 지시:
> 모델만). 근거는 문서 페이지 + 바디 모델 라이브 fetch (대표 3개 —
> postgresqlshowinstance/1.2, postgresqlmanualbackup/1.2,
> mysqlupgrademajorversion/1.2 — 를 정식 파서로 확인, 나머지는 §3 카탈로그
> 시그니처가 경로/메서드 동일함을 확인). 시나리오/야믈 파일은 이 메모 기준으로
> **각 엔진 담당 에이전트가** heavy 캠페인 때 노드를 추가한다 (이번 세션은
> sqlserver 소유권 범위라 database__sqlserver.yaml의 ss-manual-backup만 실제
> 노드로 추가했음 — 그 노드가 아래 패턴의 레퍼런스 구현).

## 1. 대상 엔드포인트 (17)

| 패턴 | 메서드/경로 | 엔진 |
|---|---|---|
| `{engine}showinstance` | `GET /v1/clusters/{cluster_id}/instances/{instance_name}` | cachestore·epas·eventstreams·mariadb·mysql·postgresql·searchengine·sqlserver·vertica (9) |
| `{engine}manualbackup` | `POST /v1/clusters/{cluster_id}/backups/manual` | epas·mariadb·mysql·postgresql·searchengine·sqlserver (6) |
| `{engine}upgrademajorversion` | `PUT /v1/clusters/{cluster_id}/major-version-upgrade` | mariadb·mysql (2) |

전부 **신규 = 기존 커버리지 0, breaking 아님**. 경로가 엔진별로 동일하고 각
엔진 호스트로 라우팅되는 DBaaS 표준 멀티테넌트 패턴 (기존
showcluster/setbackup과 동형).

## 2. showinstance — 파라미터 확정 (정식 파서, postgresql 1.2)

- path 파라미터 2개 모두 REQUIRED: `cluster_id`, **`instance_name`** (id가
  아니라 **이름**임에 주의).
- 응답: 플랫 인스턴스 상세 — `{account_id, cluster_id, cluster_name,
  block_storages[{name,role_type,size_gb,volume_type}], cpu_core, ...}`
  (doc response_example; envelope 래핑 없음).
- **instance_name 공급 경로**: 클러스터 상세 GET `/v1/clusters/{cluster_id}`의
  `$.instance_groups[0].instances[0].name` — postgresql은 detail-lookup 노드
  (pg-instance-group 계열)가 이미 같은 detail을 캡처하므로 **capture 한 줄
  추가**가 최소 변경. sqlserver는 ss-instance-group 노드에
  `instance_name: "$.instance_groups[0].instances[0].name"` 캡처 추가 후
  verify에 `GET .../instances/{instance_name}` 한 스텝.
- **권장 모델링**: 신규 노드가 아니라 **각 엔진의 기존 detail-lookup 노드
  verify에 read 스텝 추가** (expect [200]). 이유: 리소스가 아니라 읽기이므로
  노드를 늘리면 합성 그래프만 비대해짐.
- 라이브 확인 최적 경로: heavy-shared-dbaas 캠페인이 클러스터를 세울 때
  엔진당 1회 GET — 추가 비용 0. eventstreams/vertica/searchengine은
  create-cluster-waiver 게이트라 그 다음 웨이브.

## 3. manualbackup — 즉시 실행 수동 백업

- body **없음** (path `cluster_id`만; doc params=1 확인 — sqlserver/postgresql
  둘 다). 기존 `{engine}setbackup`(백업 **정책**)과 별개.
- 응답: `AsyncResponse {request_id, resource:{id}}` — `resource.id`는 백업
  이력 id 추정 (미확증).
- **선행 조건 미문서**: 백업 정책(setbackup) 미설정 클러스터에서 4xx일
  가능성 — 합성 순서는 `set-backup → manual-backup` 권장.
- **비용/잔존 주의**: 즉시 백업은 스토리지 이력을 남김 —
  `remove-backup-histories`(기존 PUT `.../backup-histories`)로 정리 스텝을
  세트로 넣을 것 (409/빈 이력 관용).
- 409 재시도 필요 (클러스터 진행 중 작업 경합 — restart/sync-state 선례):
  `retry_on_status: [409], retries: 8, retry_interval: 30`.
- 레퍼런스 노드: `database__sqlserver.yaml` → `ss-manual-backup` (2026-07-15).

## 4. upgrademajorversion — mariadb/mysql 전용

- body: `PatchRequest {dbaas_engine REQ string, software_version REQ string}`
  (mysql 1.2 정식 파서 확인). 기존 `upgradekernel`(커널)·`patch`(마이너)와
  별개의 **메이저 버전 업그레이드**.
- `dbaas_engine` 값 후보: 엔진 토큰(`mysql`/`mariadb`) 추정 — engine-versions
  lookup의 필드와 대조 필요 (미확증). `software_version`은 대상 메이저의
  버전 문자열.
- **IRREVERSIBLE + 장시간** — ss-patch 선례대로 `ready` timeout 3600s,
  대상 버전 기본값 없는 required option 게이트 (`target` 미지정 시 합성
  제외). 사전 수동 백업(§3) 선행 권장 — 문서상 백업 요구 여부 미확인.
- 1회용 클러스터에서만 수행 (heavy-shared 공유 클러스터 금지 — 다른
  lifecycle의 버전 가정을 깨뜨림).

## 5. 후속 요건 요약 (라이브 승격 조건)

1. heavy-shared-dbaas 캠페인 1회에 엔진별 showinstance GET 편승 (비용 0,
   9개 중 6개 즉시 가능; eventstreams/vertica/searchengine는 waiver 게이트).
2. manualbackup은 pg 또는 mysql 클러스터 1개에서 set-backup 후 1회 실행 +
   backup-history 정리까지 왕복 확인 (§3 노트 갱신).
3. upgrademajorversion은 구버전 메이저로 생성한 1회용 클러스터 필요 —
   engine-versions에 구 메이저가 노출되는지 먼저 확인 (안 되면
   blocked-engine).
4. sqlserver 계열은 license 게이트 유지 (`gated: license`).
5. 승격 시 이 메모의 해당 절을 지우고 각 엔진 yaml 노드 notes로 이관.
