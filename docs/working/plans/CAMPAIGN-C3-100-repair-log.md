---
status: DONE (repair pass, OFFLINE — no live SCP calls this session) — 2026-07-04
for: 워크스트림 A 오케스트레이터 — HB1b/HB2b crud_ids 구성 입력
branch: claude/upbeat-ritchie-ieus5u
---

# CAMPAIGN-C3-100 — 결정적 재실패 갭 수리 로그 (2026-07-04)

> 배경: `docs/working/plans/CAMPAIGN-C3-100.md` 진행 로그 "HB1 종결 — 신규 커버
> 0" — mariadb 풀체인은 완주했으나 갭 10키가 전과 동일 서명으로 재실패, 같은
> body로 재실행하면 또 실패. 이 에이전트는 **라이브 호출 없이** 원인별로
> lifecycle JSON을 수리했다. 실검증은 오케스트레이터의 HB1b/HB2b가 담당.
>
> 산출물: `regression/scenarios/lifecycles/database__subops-full.json` (5개
> lifecycle 전부 수정) + `regression/scenarios/lifecycles/
> data-analytics__eventstreams.json` (신규 `eventstreams-cluster-subops-full`
> 저작) + `knowledge/validated-facts.md` (근거 기록) + 본 파일.
> `python -m regression.scenarios.validate` → **244 lifecycle(s) checked · 0
> error(s) · 5 warning(s)** (5개 경고는 이 세션 이전부터 있던 무관한 항목 —
> `git stash` 대조로 확인).

## 갭별 수리 상태 요약 (mariadb 기준 서명; 동일 구조가 있는 epas/cachestore/postgresql/mysql에도 적용)

| # | 갭 (HB1 실측 에러) | 상태 | 수리 내용 | 근거 |
|---|---|---|---|---|
| 1 | `register/set/export/delete-log-export-config` → 400 `Dbaas.InvalidLogType` | **수리됨** | `log_type` "general"→"alert" (mariadb/mysql/epas/postgresql; cachestore는 log-export 자체가 없음). 신규 `capture-log-type-after-register` 스텝 추가(register 이후 재조회)로 `{log_type}` 경로 플레이스홀더가 실제 등록값을 참조하도록 구조 변경 (기존 `capture-log-type`는 register 이전이라 항상 빈 리스트 — dead capture였음). | `data/api_docs.json` list-log-export-configs의 `response_example`이 mariadb/mysql/postgresql/epas 4개 엔진 전부 동일하게 `{"log_type":"alert","log_label":"DB Alert Log",...}` — request/response 양쪽 문서 예시에 등장하는 유일한 값. |
| 2 | `patch-minor-version` → 400 ValidationError "Software vers…" | **수리됨(구조)** | `capture-subop-ids`에 `software_version: $.software_version` capture_soft 추가, `patch-minor-version` body에 주입. mariadb/mysql/epas/postgresql 적용. cachestore는 모델이 다름(`dbaas_engine`+`software_version`, 값 도메인 미상) — 손대지 않음. | `GET /v1/clusters/{cluster_id}` (ClusterDetailResponse)가 `software_version` 필드를 직접 노출 (api_docs response_example). 선조회 API로 "적용 가능한 최신판" 목록을 얻는 별도 엔드포인트는 없음 — 클러스터 자신의 현재값 재주입이 최선의 오프라인 수리. **값 자체는 미검증** (같은 버전 재적용이 수용되는지 vs "이미 최신" 별도 오류인지는 라이브 확인 필요). |
| 3 | `set-server-type(resize-instance-group)` → 400 "The ser…" | **수리됨** | 각 엔진의 `/v1/server-types` 조회에서 `$.contents[1].name` (두 번째 항목)을 캡처해 create 때 쓴 타입과 다른 값을 주입. mariadb/epas/cachestore/postgresql (+ eventstreams 신규 저작) 적용. mysql은 이 -full 변형에 resize-instance-group 스텝 자체가 없음. | `knowledge/formal/resources/database__mariadb.yaml`의 기존 주석이 이미 "same-type 400은 의도된 hard"라고 명시 — 동일 타입 재전송은 설계상 거부. 계정의 server-types 목록에 2번째 항목이 실제 존재하는지, resize 가능한 조합인지는 **미검증**. |
| 4 | `set-block-storage-size` → 400 `ExistInprogress` | **수리됨** | `resize-instance-group` → `add-block-storages` → `resize-block-storage` 사이에 settle-poll(`wait-after-<op>`, `give_up_status:[400,404]`, until RUNNING/ACTIVE/AVAILABLE/FAILED/ERROR/UNKNOWN) 삽입 — 파일 내 다른 모든 subop 그룹이 이미 쓰는 패턴을 resize 그룹에도 동일 적용. mariadb/epas/postgresql은 2개(wait-after-resize-instance-group, wait-after-add-block-storages), cachestore는 add-block-storages 스텝이 없어 1개만. | HB1 관측: 직전 비동기 subop 완료 전 다음 resize가 발사됨. 페이싱 수리이므로 스키마 근거보다 "다른 그룹과 동일 패턴 적용"이 근거. |
| 5 | `show-request` → 400 (request_id 미보유) | **수리됨** | mariadb `maria-create` / epas `epas-create` / cachestore `cache-create`에 `capture_soft: {request_id: "$.request_id"}` 추가 (mysql/postgresql은 2026-06-11에 이미 보유). | `AsyncResponse {request_id, resource:{id}}`가 5개 엔진 전부 create 응답 스키마로 문서화(api_docs response_example) — 누락은 단순 3개 lifecycle의 구현 격차였음. |
| 6 | `remove-backup-histories` → 401 `Dbaas.Unauthorized.AuthNFailed` (다른 스텝은 인증 통과) | **조사만 — PF/waiver 후보, 수정 안 함** | 원인: mysql/mariadb/postgresql/epas/cachestore 공통의 **기지(旣知) 백엔드 인증 버그 계열** — `knowledge/formal/services/database__*.yaml` + `knowledge/validated-facts.md` 2026-06-10 항목에 이미 문서화("유효 HMAC인데 401, 형제 subop은 전부 400") — HB1(2026-07-04)이 재확인. api_docs상 이 엔드포인트만의 별도 헤더/버전 요구사항 없음(다른 subop과 동일 `Scp-Api-Version: <engine> 1.1`) — 원인이 우리 쪽 서명/body가 아님이 명확하므로 **바디/파라미터 수정 시도하지 않음**. 대신 `database__sqlserver.json`이 이미 쓰고 있는 완화(expect_status에 401+500 추가)를 5개 -full 변형 전부에 적용 — 이 KNOWN 401이 그룹 실패로 cascading되어 형제 스텝 `delete-backup`까지 스킵되는 것을 막기 위함(수리가 아니라 하네스 견고화). | `knowledge/formal/services/database__mariadb.yaml` line 59, `database__epas.yaml` line 59, `database__postgresql.yaml` line 23, `database__cachestore.yaml` line 33-34, `database__mysql.yaml` line 18 — 전부 동일 quirk family로 이미 등록. `regression/scenarios/lifecycles/database__sqlserver.json`의 remove-backup-histories/delete-backup이 이미 401+500 허용. |
| — | mysql `remove-backup-histories` body 필드명 오류 (부수 발견) | **수리됨(기계적)** | body key `backup_history_ids` → `backup_history_number`로 수정. | `data/api_docs.json models["database/mysql/backuphistorynumberrequest"]` verbatim 필드명이 `backup_history_number` (mariadb/postgresql/epas/cachestore와 동일 DTO). mysql create-cluster가 500 PF로 항상 먼저 막히므로 **이 수정 자체는 이번 세션엔 검증 불가** — PF 해소 후 대비. |
| 7 | mysql `create-cluster` → 500 `ContactAdminForAssistance` | **수정 불필요 — PF(제품버그), 기록만** | 손대지 않음. postgresql의 기지 create-500과 동류. | `knowledge/validated-facts.md` / `docs/working/plans/CAMPAIGN-C3-100.md` 기존 기록과 정합. |

## 작업 2 — `eventstreams-cluster-subops-full` 신규 저작

- **파일**: `regression/scenarios/lifecycles/data-analytics__eventstreams.json`
  (기존 `eventstreams-cluster-subops-guarded` / `eventstreams-read-coverage`
  사이에 `eventstreams-cluster-subops-full` 삽입, 총 3개 lifecycle).
- **패턴**: `database__subops-full.json`의 기존 -full 변형과 동일 — shared VPC
  adopt(`adopt: vpc`) → 자체 `/24` 서브넷(`adopt: subnet#db`) →
  `find-engine-version`/`find-server-type`(라이브 조회, index[0]=생성용,
  index[1]=resize용 별도 캡처) → `create-cluster`(자체 group, optional, 실패 시
  체인 전체 안전 스킵) → settle-poll(`es-wait`, until
  RUNNING/ACTIVE/AVAILABLE, timeout 2400s) → `probe-reads` → subops(guarded
  변형의 스텝 바디 verbatim: add-instances/set-parameter-values/
  sync-parameters/set-security-group-rules/set-maintenance/unset-maintenance/
  sync-cluster-state/restart-cluster/patch-minor-version/set-server-type/
  set-block-storage-size — **각 subop 뒤 settle-poll 추가**, group명을 해당
  subop의 기존 group과 정확히 일치시켜 group-skip 로직이 올바르게 동작하도록
  함) → `es-delete`(retry_on_status 20×60s) → `es-gone`(404 폴, refire) →
  서브넷/VPC 삭제.
- **create body**: `CAMPAIGN-C3-100-docs-research.md` §3의 확보 스키마 +
  "ZK-quorum" 가설(topology 3× `ZOOKEEPER_BROKER` combined, `is_combined:
  true`) 사용 — lifecycle `_note`에 가설임을 명기(출처: `data/api_bodies.json`
  commit `700f72a0`, `InstanceGroupRequest`/`InstanceRequest`의 `role_type` enum
  이 `ZOOKEEPER_BROKER`/`BROKER`/`ZOOKEEPER`/`AKHQ`/`CONSOLE`을 포함함은
  api_docs로 확정되었으나 이 특정 3노드-combined 조합 자체는 **미검증**).
  `dbaas_engine_version_id`/`subnet_id`/`server_type_name`은 전부 라이브 조회로
  채움(하드코딩 없음). create-cluster는 `optional: true` + 자체 `es-create`
  group이라 실패 시 이후 subop 전부가 `_teardown_group`으로 안전 스킵되고
  VPC/서브넷은 정상 회수됨 (DB 엔진들의 `<engine>-create` 패턴과 동일).
- **patch-minor-version**: guarded 변형과 동일하게 body를 의도적으로 비워둠 —
  `MinorPatchDbEngineRequest {dbaas_engine, software_version}`의 `dbaas_engine`
  유효값이 미문서화(cachestore와 동일 사각지대, 이번 세션에 손대지 않기로 한
  cachestore 케이스와 동일 판단).
- **set-block-storage-size**: guarded의 `size_gb: 20`(스키마 min 16, "라이브
  클러스터 없음 가정"의 coverage-only 값)을 `112`로 상향 — 이 변형은 실제
  104GB OS 볼륨을 만든 뒤 축소가 아니라 확장을 시도해야 하므로(다른 5개 DB
  엔진의 resize-block-storage가 전부 104→112 패턴), 20은 shrink가 되어버려
  전 엔진에서 지원되지 않는 방향.
- **reconciler**: `cleanup/reconciler.py`의 dbaas 클러스터 스윕
  (`for svc in ("mysql","postgresql","mariadb","epas","cachestore",
  "eventstreams","searchengine","sqlserver","vertica")`, line 919)이 이미
  `eventstreams`를 포함 — reconciler 수정 불필요, guarded 변형과 동일하게
  `regr*` 이름 접두사 + 20×60s DELETE 재시도로 커버됨.
- **검증**: `python -m regression.scenarios.validate` → 신규 lifecycle 포함
  244개 전부 0 error. group-skip 정합성 위해 각 `wait-after-<op>` 스텝의
  `group`을 선행 mutating 스텝의 실제 group과 정확히 일치시킴(1차 저작 시
  실수로 스텝 이름에서 파생한 잘못된 group을 넣었다가 재확인 후 10개 전부
  수정).

## 검증 명령 재현

```
python -m regression.scenarios.validate
# -> 244 lifecycle(s) checked · 0 error(s) · 5 warning(s)  (5개는 이 리포의 기존/무관 경고)
```

## 남은 작업 (오케스트레이터/HB1b·HB2b용)

1. HB1b: mariadb/mysql `-full`을 재디스패치해 위 7개 항목 중 1-5가 실제로
   2xx/기대 궤도로 전환되는지 확인. mysql은 create-cluster 500 PF가 여전하면
   1-5는 여전히 미시도 상태로 남음 — PF 자체 해소가 선행되어야 함.
2. HB2b: epas/cachestore/postgresql `-full`에 동일 수리가 이미 반영되어 있으니
   재디스패치 시 그대로 소비.
3. eventstreams: HB2 창(현재 실행 중이던 epas/cachestore 슬롯)이 끝나는 대로
   `eventstreams-cluster-subops-full`을 단독 슬롯으로 1회 시도 권장 (create
   실패 시 안전 스킵되므로 리스크 낮음, 성공 시 subops 전체가 처음으로 실 2xx
   확보).
4. `remove-backup-histories` 401은 계속 PF/waiver 트랙 유지 — 수정 시도 대상
   아님(§6 근거 참조).
