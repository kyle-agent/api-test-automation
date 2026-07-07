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

---

# §HB3b — compute-virtualserver-full / gen-heavy-backup 수리 (2026-07-05, OFFLINE)

> 배경: `docs/working/plans/CAMPAIGN-C3-100.md` 진행 로그 "HB3 종결 — 신규 커버
> 0 (3연속)" — run 28723287734 (success ~41m, 110 obs: ok 82 · soft 28 · fail
> 0)의 job-log 진단 5건을 lifecycle 수리로 옮긴 세션. **라이브 SCP 호출 없음**
> (HB4 heavy run이 레인 점유 중이었으므로 오프라인 전용). 실검증은 다음 HEAVY
> 디스패치가 담당.
>
> 산출물: `regression/scenarios/lifecycles/generated__heavy-backup.json`
> (create-backup-target 스텝) + `regression/scenarios/scenarios.json`
> (compute-virtualserver-full의 create-port/image-update/delete-server 3스텝)
> + `knowledge/services.md` (storage/backup, compute/virtualserver 섹션에
> 근거 기록) + 본 섹션. `python -m regression.scenarios.validate` → **244
> lifecycle(s) checked · 0 error(s) · 5 warning(s)** (5개 경고는 이 세션
> 이전부터 있던 무관 항목 — 전임 HB1 repair 세션과 동일한 5건, 본 세션에서
> 변경한 3개 파일과 무관함을 diff로 확인).

## 수리 4건

| # | 갭 (HB3 job-log 진단) | 상태 | 수리 내용 | 근거 |
|---|---|---|---|---|
| 1 | `gen-heavy-backup` create-backup-target 200이지만 `$.contents[0].server_uuid` 캡처 실패 → 엔진 assert로 lifecycle 중단 | **수리됨** | 같은 스텝을 poll로 전환: `field:"$.count"`, `until:[1]`, `timeout:300s`, `interval:15s`, `give_up_status:[400,401,403,404,500]` (구조적 오류는 즉시 반환, 등록-지연만 재시도). `server_uuid` 자체는 값이 예측 불가능해 엔진의 등가-비교 poll(`field`+`until`)로는 폴링할 수 없음(엔진에 "필드 존재까지" 폴 옵션 없음, `regression/scenarios/engine.py:_run_step` 확인) — 값을 예측 가능한 `count`(0→1 전이)로 대체. 쿼리 자체가 `server_name=regrsrv{unique}`로 이미 자기 서버만 필터링하므로 `count=1`이면 `contents[0]`이 곧 자기 서버 — 별도 이름 필터는 불필요·불가능(엔진 jsonpath는 배열 인덱스 리터럴만 지원, 조건 필터 없음) 하다는 전제를 스텝 `_note`에 명기. | `data/api_docs.json` `storage/backup/getbackuptargetlist`의 `response_example`이 `{"contents":[{...}],"count":1}` — top-level `count` 필드가 문서로 확정. `regression/scenarios/engine.py` `_run_step`(약 L579-632)이 poll을 `until_status` 또는 `field`+`until`(등가 리스트) 두 가지로만 지원함을 코드로 확인(존재-여부 poll 없음). |
| 2 | `compute-virtualserver-full` delete-server 400 `InvalidVirtualServerState.DeleteImpossible` 3회 재현 (직후 리컨실러 삭제는 성공) | **수리됨** | delete-server 직전에 `wait-server-settled` 스텝 신설: 고정 `wait:20`(초) + `$.server.state`를 `[ACTIVE,STOPPED,SHUTOFF,RUNNING]`까지 최대 120s/15s 간격 poll(`give_up_status:[404]`). 또한 delete-server 자체에 `retry_on_status:[400,409]`(6×20s) 추가 — 2차 방어선. | `cleanup/reconciler.py`의 서버 삭제 경로(`_delete`+`_wait_gone`, L817-822)를 확인 — 특별한 상태-체크 없이 그냥 재삭제 시도하고, **경과 시간**만으로 다음 라운드에 성공함. 즉 하드 블록이 아니라 타이밍 이슈. `$.server.state` 필드가 전이 중간상태(task_state)를 노출한다는 문서 근거는 없어(응답 예시 스키마 확인, `showvirtualserver`) 고정 대기가 핵심 수리, poll은 보강용으로 명기. |
| 3 | `compute-virtualserver-full` create-port 400 `scp-network.port.fixed_ip.format-error` (port 체인 6키 404 강등) | **수리됨** | body에서 `fixed_ip_address` 키를 완전히 제거(빈 문자열 `""` 대신 생략). | `data/api_docs.json` `networking/vpc/portcreaterequest` 모델은 `fixed_ip_address`를 `any of [string, null]`(옵션, default `""`)로 문서화하고 API 문서의 request_example도 실제로 `"fixed_ip_address":""`를 보내지만, HB3 라이브 에러 메시지 `"The requested Fixed IP() is invalid IP format."`(빈 괄호 — 즉 빈 문자열이 그대로 전달됐다는 증거)가 **문서 예시 자체를 백엔드가 거부**함을 확정. 필드를 생략(자동 할당)하는 쪽으로 수리. |
| 4 | `compute-virtualserver-full` image-update 400 `Image.InvalidVolumeOnMinDiskUpdate` | **수리됨** | `min_disk: 100` → `min_disk: 104`(부팅 볼륨 크기와 동일). | 같은 파일의 `create-server` 스텝이 부팅 볼륨을 `size:104`로 고정 생성하는 리터럴이 이미 존재 — 별도 캡처 없이 동일 상수로 주입(문서상 안전값보다 실제 생성값과 일치가 더 확실). `image-update`는 이미지가 참조하는 소스 볼륨 크기 이상을 요구한다는 에러명(`InvalidVolumeOnMinDiskUpdate`)에서 직접 추론, 100 < 104가 원인. |

## 조사 5 — create-image / import-image 2xx 전환 (실행 계획만, 업로드/라이브 검증 없음)

**현재 상태**: `vs-image-write-coverage`(`regression/scenarios/lifecycles/compute__virtualserver.json`)의
`create-image`/`import-image` 스텝은 **의도적으로 존재하지 않는 qcow2 URL**을
보내 `Image.InvalidObjectStorageUrl`(createimage) / `ValidationError`
(importimage, url 정규식 `.*\.qcow2$`/255자 위반 시)를 유발하도록 저작되어
있음 — heavy/billable이라 스코프 밖으로 명시(`_note` 참조). 즉 body
스키마(`os_distro`, `url` 필드명 등)는 이미 확정되어 있고, 남은 변수는
**실존하는 qcow2 URL 하나**뿐.

**(a) 버킷 위치/권한**: `core/oplog.py`가 이미 관리하는 영구 버킷
`apitest-oplog-permanent`(sweep 어떤 매처에도 불일치, `ensure_bucket()`으로
생성/CORS/ACL 설정)가 후보. `SCP_REGION`(기본 `kr-west1`) 환경변수로 지어지므로
VM 라이프사이클이 쓰는 `{region}` placeholder와 **같은 리전으로 맞추는 것은
설정상 가능**하나, importimage 문서 설명("Object Storage bucket ... must be
in the same zone as the server you are creating")의 "zone"이 리전과 동일
개념인지 별도 AZ 단위인지는 미확정. 계정 접근키가 오브젝트 스토리지 API와
동일하다는 것은 `knowledge/validated-facts.md` 2026-06-11 검증 항목으로 이미
확정됨(`SCP Object Storage S3 (oplog 버킷에서 검증)`).

**URL 포맷 불일치(중요 리스크)**: 같은 리포 안에 이미 **서로 다른 두 도메인
표기**가 존재함 — (i) `create-image` 스텝은
`object-store.{region}.e.samsungsdscloud.com/regression-coverage/...`
(env=`e`, `core/oplog.py`의 API-엔드포인트 추정 규칙과 동일 패턴), (ii)
`import-image` 스텝 및 `data/api_docs.json`의 API 문서 예시 자체는
`object-store.kr-west1.s.samsungsdscloud.com/<account_id>/<bucket>/<obj>`
(env=`s`, 리전 하드코딩, 슬래시 구분). 그런데
`knowledge/validated-facts.md`(2026-06-11, oplog 버킷 라이브 검증)는 **익명/공개
경로는 RGW 테넌트 문법 `/<account_id>:<bucket>/<key>`**(콜론 구분, 슬래시
구분 시 `NotFoundBucketNameInPath`)라고 명시 — 즉 API 문서의 request_example
URL 형태(슬래시 구분)를 그대로 신뢰하면 이미지 서비스가 그 URL을 못 읽을
가능성이 있음. **어느 쪽이 실제로 이미지 서비스가 내부적으로 fetch하는
경로인지는 라이브로 확인된 바 없음** — 이 불일치 자체가 이번 조사의 핵심
발견.

**(b) qcow2 매직바이트 초소형 파일**: `qemu-img create -f qcow2 <path> 1M`
(또는 그보다 작은 사이즈)로 로컬에서(라이브 SCP 호출과 무관) 유효한 qcow2
헤더 + 희소(sparse) 컨텐츠를 가진 수백 바이트~수 KB급 파일 생성 가능 —
매직바이트(`QFI\xfb`)와 기본 헤더 파싱은 통과할 개연성이 높음. 다만 백엔드가
헤더 이상(가상 디스크 크기, 클러스터 테이블 등)까지 깊게 검증하는지는
미확인 — "포맷 통과 → 곧 2xx"라고 확정할 근거 없음.

**(c) 다운스트림 async 실패의 leak 가능성**: `importimage`는 `202 Accepted`
(빈 바디, 완전 비동기) — 성공/실패는 이후 `GET /v1/images/{id}`로만 드러남.
`createimage`의 문서 response_example은 `status:"active"`인 성공 케이스만
보여줄 뿐, 실제 처리가 동기인지 비동기인지는 그 예시만으로 단정 불가.
이미지가 실패 상태(예: error/killed 유사)로 전이될 경우 `DELETE
/v1/images/{id}`가 그 상태에서도 수락되는지는 **이 리포의 어떤 lifecycle도
아직 실측한 적 없음** — 이것이 leak 리스크의 핵심이며, 라이브 세션에서
가장 먼저 확인해야 할 항목.

**실행 계획 (다음 라이브 세션 — 이번 세션은 조사만, 업로드/호출 없음)**:
1. `python -m core.oplog ensure` — 버킷 존재/리전 확인 (VM 라이프사이클의
   `{region}`과 일치하는지 로그로 확인).
2. 로컬에서 `qemu-img create -f qcow2 /tmp/regr-min.qcow2 1M` (또는 동급)로
   최소 qcow2 생성 — 라이브 호출 아님, 순수 로컬 파일 생성.
3. `core/oplog.py`가 이미 쓰는 `put_object(..., ACL="public-read")` 패턴을
   재사용해 `apitest-oplog-permanent`의 임시 키(예:
   `images/regr-min-{unique}.qcow2`)에 업로드 — **오브젝트 단위 public-read
   ACL 필수**(버킷 ACL만으로는 GET 불가, 이미 검증된 사실).
4. **createimage/importimage를 호출하기 전에** 후보 URL 두 형태(콜론-테넌트
   vs 슬래시) 각각에 대해 **순수 HTTPS GET**(anonymous curl, SCP API 호출
   아님)으로 어느 쪽이 실제로 200을 반환하는지 먼저 확인 — 잘못된 형태로
   이미지 API를 호출해 async job을 낭비/오염시키는 것을 피함.
5. 올바른 URL이 확정되면 `vs-image-write-coverage`의 create-image(또는
   import-image)를 그 URL로 1회 라이브 시도 → 성공 시 `GET
   /v1/images/{image_id}`를 폴링해 최종 상태(active vs error류)와 그 상태에서
   `DELETE`가 수락되는지 확인 → 확인되면 lifecycle의 synthetic URL을 실
   URL(캡처 방식 또는 고정값)로 교체하고 `optional`/`expect_status`를 2xx
   전용으로 좁힘. 실패 시(async error) 이미지가 삭제 가능한 상태로
   남는지까지 확인한 뒤 teardown.
6. 임시 오브젝트는 검증 후 `delete_object`로 회수(영구 버킷이므로 회수는
   agent 책임, sweep 대상 아님).

**결론**: (a)(b)(c) 모두 "가능성 있음"이나 **URL 도메인/경로 문법 불일치가
확인 전 최대 리스크** — 잘못된 형태로 실 이미지 서비스를 호출하면 낭비뿐
아니라 상태 불명 이미지가 남을 수 있음. 이번 세션은 업로드/라이브 호출을
하지 않았으므로 이 결론은 **문서 대조 기반 추론**이며 다음 라이브 세션의
1차 작업으로 위 실행계획 1-4(HTTPS 형태 확인까지)를 먼저 마치고 나서
5-6(실제 이미지 API 호출)으로 넘어갈 것을 권고.

## 검증 명령 재현

```
python -m regression.scenarios.validate
# -> 244 lifecycle(s) checked · 0 error(s) · 5 warning(s)  (5개는 이 리포의 기존/무관 경고, diff로 확인됨)
```

## 남은 작업 (다음 HEAVY 디스패치용)

1. `compute-virtualserver-full` 재디스패치로 위 2·3·4번 수리가 실제 2xx로
   전환되는지 확인 (port 체인 6키 신규 커버 기대).
2. `gen-heavy-backup` 재디스패치로 1번 수리(backup-target count-poll)가
   `create-backup-policy` 이후 전체 verify/manual-backup 체인을 살리는지
   확인.
3. `create-image`/`import-image` 2xx 전환은 위 실행계획 1-4(URL 형태 확인)를
   업로드 없이 먼저 마친 뒤에만 5-6(라이브 이미지 API 호출)로 진행 — 순서
   준수.

---

# §HB4b — networking/vpn · loadbalancer · vpc(TGW/vpc-endpoint) 수리 (2026-07-06, OFFLINE)

> 배경: `docs/working/plans/CAMPAIGN-C3-100.md` 진행 로그 "HB4 종결 — 신규 커버
> 0 (4연속), 단 전 서명 진단 완료" — run 28738115294 (97 obs: ok 30 · soft 67)의
> `reports/results/hb4/observations-gw*.jsonl` note 필드 오류 전문을 lifecycle
> 수리로 옮긴 세션. **라이브 SCP 호출 없음**(HB3b heavy run이 레인 점유 중이라
> 오프라인 전용). 실검증은 다음 HEAVY 디스패치(가칭 HB4c)가 담당.
>
> 산출물: `regression/scenarios/lifecycles/networking__vpn.json` +
> `regression/scenarios/lifecycles/networking__loadbalancer.json` +
> `regression/scenarios/lifecycles/networking__vpc.json` (vpc-transit-gateway-children
> + vpc-endpoint) + `knowledge/services.md`(networking/vpn·vpc·loadbalancer 섹션에
> 근거 기록, loadbalancer 섹션 신규) + 본 섹션. `python -m regression.scenarios.validate`
> → **244 lifecycle(s) checked · 0 error(s) · 5 warning(s)** (5개는 이 리포의
> 기존/무관 경고 — HB1/HB3b 세션과 동일 항목, 이번 세션에서 수정한 3개 파일과
> 무관함을 diff로 확인).

## 수리 6건

| # | 갭 (HB4 job-log/observations 진단) | 상태 | 수리 내용 | 근거 |
|---|---|---|---|---|
| 1 | `networking-vpn-gateway-tunnel:create-vpn-gateway` → 400 `ValidationError ["Field required"]` (필드명 미표기) — 체인 8키 전부 soft 강등 | **수리됨** | `data/api_docs.json` models `networking/vpn/vpngatewaycreaterequest`의 `ip_address` 필드가 `required:true`인데 현행 body에 없었음(`ip_id`/`ip_type`/`name`/`tags`/`vpc_id`만 있었음). `create-publicip` 스텝에 `$.publicip.ip_address` capture 추가(`publicip_ip_address`) → `create-vpn-gateway` body에 `ip_address: "{publicip_ip_address}"` 주입(같은 publicip의 실 IP를 게이트웨이 자신의 IP로 사용 — ip_id/ip_type과 짝을 이루는 필드이므로 자연스러운 짝). | `data/api_docs.json` `models['networking/vpn/vpngatewaycreaterequest']` 필드 목록에서 `ip_address`(`required:true`, `schema:string`)를 직접 확인 — request_example도 `ip_id`/`ip_type`과 나란히 `ip_address:"123.0.0.1"`을 보냄. `networking/vpc/createpublicip`의 response_example이 `publicip.ip_address`(예: `"192.167.0.5"`)를 노출함을 확인해 캡처 경로 확정. **라이브 미검증** — 값 자체(같은 IP를 gateway/publicip 양쪽에 재사용하는 것이 accepted인지)는 다음 라이브 세션 확인 대상. |
| 2 | `networking-loadbalancer-members-nat:lb-healthcheck-create` → 400 `SubnetNotAssociatedWithLoadBalancer`(subnet_id 9ab0704d…, LB 없는 subnet) — servergroup/members/listener 캐스케이드 | **수리됨(순서)** | 라이브 에러 본문이 "Please ensure a Load Balancer exists within the subnet before attempting again"임을 확인 — subnet_id 자체는 LB-create와 동일한 `{subnet_id}`(이 lifecycle의 create-subnet 캡처값, 필요 시 세션 공유 subnet 채택)라서 "잘못된 참조"가 아니라 **순서 문제**였음: 2026-06 수정에서 lb-servergroup-create가 PRE-CREATED health check를 요구해 lb-healthcheck-create를 lb-create보다 앞으로 옮겼는데, 그 결과 health check 생성 시점에 이 subnet에 LB가 아직 없어 매번 400. 해법: lb-create + lb-wait를 먼저(LB가 subnet에 안착) → lb-healthcheck-create(이제 subnet에 LB 존재) → lb-servergroup-create(health check 이미 존재) 순서로 재배치 — 두 제약을 동시 충족. | HB4 observations: `lb-healthcheck-create`의 note가 정확히 "the chosen subnet does not contain a Load Balancer (subnet_id: '9ab0704d...')"라고 명시(대상 subnet 실체 확인). 기존 파일 자체의 이전 `_note`(2026-06 수정 이력)가 "server-group이 PRE-CREATED health check를 요구"한다고 이미 기록해 두 제약의 존재를 교차 확인. **라이브 미검증**. |
| 3 | `networking-loadbalancer-members-nat:static-nat-create` → 400 `igw-required-for-static-nat`("No Internet Gateway (IGW) found in the VPC") | **수리됨** | `networking__vpc.json`의 기존 검증된 `create-internet-gateway-for-nat`/`wait-igw-for-nat-active`/`set-internet-gateway-for-nat` 3단 패턴(2026-06-23 CONFIRMED: NAT gateway도 IGW attach 필요)을 그대로 LB의 static-nat 앞에 이식 — `create-igw-for-static-nat`(POST, group `staticnat`, optional, capture_soft `igw_id`) → `wait-igw-for-static-nat-active`(poll `$.internet_gateway.state` until ACTIVE) → `set-igw-for-static-nat`(PUT) → 기존 `static-nat-create`. Teardown: `delete-igw-for-static-nat`을 `static-nat-delete` 뒤·`delete-subnet`/`delete-vpc` 앞에 추가(수명주기 균형 — 생성했으면 삭제), 409/400 retry 8×15s. 전부 group `staticnat` + optional이라 공유 VPC상 다른 동시 어댑터와의 IGW 충돌(409/quota)이 있어도 이 NAT 패밀리만 스킵. | `networking__vpc.json`의 동일 3단 패턴이 이미 "CONFIRMED 2026-06-23: scp-network.nat-gateway.internet-gateway-not-associated"로 라이브 검증되어 있음 — LB static-nat의 에러 코드(`igw-required-for-static-nat`)도 "IGW가 VPC에 없다"는 동일 계열 사전조건이므로 검증된 패턴 이식이 가장 안전한 근거. **패턴은 검증됨, 이 특정 적용(LB 컨텍스트)은 라이브 미검증**. |
| 4 | `private-static-nat-create` → 403 `PrivateNatIpForbidden` | **수정 안 함 — entitlement, waiver 후보로 기록만** | 손대지 않음. body 문제가 아니라 "You do not have permission to access the private NAT IP resource" — 계정 entitlement 벽. `expect_status`에 이미 403 포함되어 reach-coverage는 유지됨. `_note`에 waiver 후보임을 명기. | HB4 observations 그대로: 어떤 body를 보내도 바뀌지 않을 계정 단위 권한 오류(과제 지시 #4 준수 — 수정 금지). |
| 5 | `vpc-transit-gateway-children`의 10개 child write 전부 400 `transit-gateway.not-active-state`("... (CREATING)") — delete도 `invalid-state`("... not deletable state(Active, Error)") | **수리됨** | `create-transit-gateway` 뒤에 `wait-tgw-active`(poll `$.transit_gateway.state` until ACTIVE, give_up_status 400/403/404, timeout 300s/15s) 추가 — 모든 10개 child write가 이 폴 뒤에 실행되므로 캐스케이드 전체 해소 기대. `delete-transit-gateway` 직전에도 `wait-tgw-active-before-delete`(동일 패턴) 추가 — 선행 child(vpc-connection 등)가 TGW를 다시 비-ACTIVE로 전이시킬 수 있어 재확인. `delete-transit-gateway`의 `retry_on_status`에 400도 추가(409뿐이었음). | HB4 observations의 모든 child create/set/delete 에러 본문이 예외 없이 "Transit Gateway state is not Active.:(CREATING)" 또는 "... not deletable state(Active, Error)" — 순수 타이밍 문제임을 직접 증거. `data/api_docs.json`의 `createtransitgateway`/`showtransitgateway` response_example에서 `$.transit_gateway.state` 필드 확인(둘 다 `"state":"ACTIVE"` 예시). 패턴은 `database__subops-full.json`의 기존 settle-poll(`field`+`until`+`give_up_status`) 및 `networking__vpc.json` 자체의 `wait-igw-for-nat-active`와 동일 구조. **라이브 미검증**. |
| 6 | `vpc-endpoint:create-vpc-endpoint` → 400 `subnet-not-found`("VPC Endpoint Type Subnet not found. subnet_id:9ab0704d…") | **수리됨(부분) + 조사 기록** | subnet `type`을 `GENERAL` → `VPC_ENDPOINT`로 변경, 해당 create-subnet 스텝의 `"adopt":"subnet"`도 제거(세션 공유 subnet은 GENERAL이라 채택하면 동일 오류 재현 — 매 실행 전용 subnet 자체 생성으로 되돌림). **resource_key는 손대지 않음** — `knowledge/formal/resources/networking__vpc.yaml`의 vpc-endpoint 노드(provenance: docs, 아직 미검증)에 resource_key가 실제 대상 서비스 자원 id(FS면 실 filestorage volume_id)여야 한다고 이미 기록되어 있으나, 이는 cross-service(storage/filestorage) 자원 생성이 필요한 별도 축이라 이번 세션(과제 지시 #6 "확실치 않으면 조사 결과만") 스코프 밖 — `generated__wave5-net.json`의 비활성 `gen-wave5-vpce`가 이미 실 FS volume 배선을 모델링했으나 그 자체도 라이브 미검증(`_disabled_reason` IB-013)이므로 참고만 하고 손대지 않음. | subnet type enum이 `data/api_docs.json` `models['networking/vpc/subnetcreaterequest']`에서 `enum (GENERAL, LOCAL, VPC_ENDPOINT)`로 확정. **VPC_ENDPOINT-타입 subnet 자체는 이미 VALIDATED**(`knowledge/formal/resources/networking__vpc.yaml` endpoint-subnet 노드, "live-validated run 27583285457 (2026-06-15)" 기록) — 이 lifecycle이 그 기지 사실을 아직 반영하지 않고 있었던 것. resource_key 요건은 같은 yaml의 vpc-endpoint 노드 주석("subnet_id MUST be a VPC_ENDPOINT-type subnet")과 `_disabled_reason`(IB-013)에서 확인. |

## docs fetch 시도 실패 (기록)

`createvpcendpoint`의 API 문서 페이지(`https://docs.e.samsungsdscloud.com/apireference/networking/vpc/apis/createvpcendpoint/1.2/`)를 WebFetch로 재확인 시도(subnet 요건에 대한 문서 본문 서술 유무 확인 목적) — **2회 모두 HTTP 503**(프록시/사이트 일시 장애로 추정, SCP API 호출 아님이므로 offline 제약과 무관). 대신 이미 리포에 축적된 `data/api_docs.json` 모델 필드 + `knowledge/formal/resources/networking__vpc.yaml`의 기존 VALIDATED 근거로 충분히 확정 가능해 문서 재조회 없이 수리 진행. 다음 세션에서 여유가 있으면 재시도해 문서 서술과 대조 권장.

## 검증 명령 재현

```
python -m regression.scenarios.validate
# -> 244 lifecycle(s) checked · 0 error(s) · 5 warning(s)  (5개는 이 리포의 기존/무관 경고, diff로 확인됨)
```

## 남은 작업 (다음 HEAVY 디스패치용, 가칭 HB4c)

1. `networking-vpn-gateway-tunnel` 재디스패치 — create-vpn-gateway가 2xx로
   전환되는지, 이어서 공식 phase1/phase2 터널 값(90396a7a)이 실제로 도달·검증
   되는지 확인.
2. `networking-loadbalancer-members-nat` 재디스패치 — 순서 변경 후
   lb-healthcheck-create/lb-servergroup-create가 2xx로 전환되는지, IGW 3단
   패턴이 static-nat-create를 2xx로 바꾸는지 확인. private-static-nat-create의
   403은 계속 waiver 트랙(수정 시도 대상 아님).
3. `vpc-transit-gateway-children` 재디스패치 — settle-poll이 10개 child +
   set/delete를 실제로 2xx(또는 각 child 고유의 다음 단계 에러)로 바꾸는지 확인.
4. `vpc-endpoint` 재디스패치 — subnet type 수정 후 에러 서명이 subnet-not-found
   에서 resource_key 관련 4xx(또는 2xx)로 실제로 바뀌는지 관찰. resource_key
   축(실 FS volume 배선)은 이번 세션 스코프 밖 — 관찰 결과에 따라 별도 세션에서
   `gen-wave5-vpce` 패턴 이식 여부 결정.

---

# §HB3b-2 — compute-virtualserver-full 잔여 3서명 + boot volume 스윕 정책 (2026-07-06, OFFLINE)

> 배경: `docs/working/plans/CAMPAIGN-C3-100.md` 진행 로그 "HB3b 종결 — 수리
> 루프 첫 실수확: verified store +13 (2252→2265)" — run 28766151214 (success
> 44m, 108 obs: ok 97 · soft 11 · fail 0)에서 HB3b(2026-07-05) 수리 3건은
> 실제로 검증되었으나(create-port/map-sg/delete-port 2xx, delete-server
> 무400, backup-target poll 통과) 잔여 3서명 + 스윕 정책 1건이 새로 확인된
> 세션. **라이브 SCP 호출 없음**(HB4b heavy run이 레인 점유 중이라 오프라인
> 전용). 실검증은 다음 HEAVY 디스패치가 담당.
>
> 산출물: `regression/scenarios/scenarios.json` (compute-virtualserver-full의
> image-update / create-port+attach-port-to-server(+신규 create-port-subnet/
> wait-port-subnet) / 신규 delete-boot-volume 4곳) + `knowledge/services.md`
> (storage/backup 섹션 REVISED 항목 + compute/virtualserver 섹션 4건 추가) +
> 본 섹션. `generated__heavy-backup.json`은 **의도적으로 변경하지 않음**(항목
> 1 참조 — 조사 결과 파라미터 문제가 아님이 확인되어 수리 대상이 아님).
> `python -m regression.scenarios.validate` → **244 lifecycle(s) checked · 0
> error(s) · 5 warning(s)** (5개 경고는 이전 HB1/HB3b/HB4b 세션과 동일한
> 무관 항목 — diff로 확인).

## 항목별 상태

| # | 갭 (HB3b job-log 진단, run 28766151214) | 상태 | 수리/조사 내용 | 근거 |
|---|---|---|---|---|
| 1 | `gen-heavy-backup` create-backup-target: 서버 ACTIVE 후에도 `{"contents":[],"count":0}` 지속 — HB3b의 `$.count until 1` 폴이 300s 전체 소진(observations의 `wait-server`→`create-backup-target` 타임스탬프 간격 ≈303s vs 스텝 자체 `elapsed_ms`≈828ms로 폴 소진 확정) | **수정 안 함 — 조사 결과 blocked (product-bug + owner-waiver 이중 차단), 가설 창작 금지 지시 준수** | `knowledge/formal/resources/storage__backup.yaml` line 40에 이미 기록된 사실을 이번에 이 증상과 연결: `policy_type=FILESYSTEM`은 **Agent형** 백업 카테고리 — 서버에 Backup Agent가 설치/구성되어야만 대상 목록에 나타남("Agent backups require prior agent creation and configuration on target servers"). agent 계열 8 ops는 owner waiver(2026-06-10 "agent 없는 백업으로만")라 이 계정/런은 절대 agent를 설치하지 않으므로 `contents:[]`는 **정상 응답**이지 타이밍/파라미터 버그가 아님. 우리가 원하는 agentless 경로의 올바른 쿼리는 `policy_type=VM_IMAGE`이지만 이건 별도의 기지(旣知) 제품버그(500 `ContactAdminForAssistance`, `data/baselines/known_issues.json`, 2026-06-20 확정)로 막혀있음. **결론: 현재 이 계정 상태로는 어떤 쿼리 파라미터 조합도 agentless 목표에 맞는 non-empty 응답을 줄 수 없다** — 새 근거(VM_IMAGE 500 해소 또는 agent waiver 해제) 없이는 재시도 금지. `$.count until 1` 폴은 그대로 둠(무해 — VM_IMAGE 버그가 언젠가 풀리면 `policy_type`만 바꿔도 그대로 작동할 self-healing 코드이므로) 단 이게 실제 수리가 아님을 `knowledge/services.md`에 명기. | `data/api_docs.json` storage/backup/getbackuptargetlist 쿼리 파라미터 전수 확인(server_name/server_category/policy_type/region/page/size — 우리가 이미 전부 정확히 사용 중, 추가 파라미터 없음). `knowledge/formal/resources/storage__backup.yaml` L10-18, L40. `data/baselines/known_issues.json` `storage/backup/getbackuptargetlist`. `reports/results/hb3b/observations-gw0.jsonl` 타임스탬프 대조로 폴 소진 확정. |
| 2 | `compute-virtualserver-full` image-update 400 `Image with volumes cannot update min disk`(HB3b의 min_disk:104 수리가 무효 — 값이 아니라 **범주적** 거부, 다른 메시지) | **수리됨** | `min_disk` 키를 body에서 완전히 제거. `data/api_docs.json` `compute/virtualserver/imagesetrequest`(PUT body 모델)의 필드는 `min_disk`/`min_ram`/`protected`/`visibility` 4개뿐 — `description` 필드 자체가 모델에 없음(과제 지시가 예시로 든 필드가 이 모델엔 부재). 안전한 대체로 `visibility:"private"`(문서 enum `private\|shared`, 기존값 유지라 부작용 없음)를 min_ram/protected와 함께 전송. | `data/api_docs.json` models `compute/virtualserver/imagesetrequest` 필드 전수(4개, description 없음 확인). run 28766151214 job-log 에러 문자열 자체("Image with volumes cannot update min disk")가 범주적 거부임을 직접 증거(HB3b의 값-일치 가설을 반증). |
| 3 | `compute-virtualserver-full` attach-port 400 `VirtualServer.CreateInterface.Duplicated`(서버가 이미 `{subnet_id}`에 인터페이스 보유 — create-port/attach-port-to-server 둘 다 서버 자신의 subnet을 참조하고 있었음) | **수리됨** | `vs-port` 그룹에 신규 `create-port-subnet`(같은 VPC 안 `10.135.3.0/24`, 자체 cleanup) + `wait-port-subnet` 스텝 추가, `create-port`/`attach-port-to-server`의 `subnet_id`를 `{subnet_id}`→`{port_subnet_id}`로 교체. 이 lifecycle엔 DB엔진 `-full` 계열의 `adopt: subnet#db` 같은 공유 2번째 subnet이 없음을 먼저 확인(grep으로 전 파일 대조) — 그래서 그룹 전용 신규 subnet을 저작(그룹 실패 시 `_teardown_group`이 이 subnet도 즉시 회수하도록 cleanup 등록). | `regression/scenarios/scenarios.json` 내 기존 `adopt: subnet#db` 사용처 전수 확인(database `-full` 계열/eventstreams만 해당, compute-virtualserver-full엔 없음). `create-server`의 `networks[].subnet_id`가 `{subnet_id}`와 동일함을 코드로 대조 — 곧 서버 자신의 boot NIC가 이미 그 subnet에 있다는 직접 증거. |
| 4 | boot volume이 `delete-server` 이후에도 말미 sweep을 2연속 생존(수동 `SCP_SWEEP_IGNORE_TTL=true` 회수 반복) | **수리됨(lifecycle 명시 삭제 스텝 추가) — (b) 워크플로 TTL 변경은 검토 후 기각, 문서 권고만** | (a) 채택: `wait-server-gone`(서버 404 확인) 직후 신규 `delete-boot-volume` 스텝 추가, 기존에 있었지만 아무 데도 쓰이지 않던 dead capture `boot_vol_id`(`capture-server-volume`의 `$.volumes[0].id`)를 사용. `optional:true` + `expect_status`에 400/404 포함(카스케이드가 이미 회수했거나 detach 중이어도 lifecycle을 절대 실패시키지 않음). (b) 기각: 말미 sweep을 무조건 `SCP_SWEEP_IGNORE_TTL=true`로 돌리는 안은 **검토 후 채택하지 않음** — `cleanup/reconciler.py _is_deletable`의 TTL은 정확히 "동시에 실행 중인 다른 에이전트의 아직 살아있는 자원"을 보호하는 장치(owner-tag는 있되 만료 전인 리소스는 스킵)이므로, 이걸 통째로 끄면 동시 실행 중인 다른 캠페인 에이전트의 리소스를 오삭제할 위험이 생김(VPC budget이 동시 5개 에이전트에 걸쳐 공유된다는 세션 브리프 규칙과 직접 충돌). `.github/` 워크플로 자체는 이번 세션에서 손대지 않음(지시 준수) — 대신 이 문서에 "sweep을 IGNORE_TTL로 바꾸지 말 것"이라는 권고만 남김. | `cleanup/reconciler.py` `_is_deletable`(L98-131) 코드 확인: `has_tag` 분기의 own-run 예외(`RUN_KEY` 일치 시 TTL 무시)가 있지만, 말미 sweep이 원본 테스트 job과 별도 job/run-id 컨텍스트로 돈다면 `APITEST_RUN_ID` 불일치로 이 예외가 적용되지 않을 수 있음(정확한 원인은 라이브 job 구조 확인 필요 — 오프라인이라 재현 못 함, 그래서 근본원인 대신 "가장 안전한 최소 수정"인 명시적 delete를 택함). `create-server`의 `volumes[0].delete_on_termination:true`가 실제로는 sweep 시점까지 항상 완료를 보장하지 않는다는 것이 이번 관측(2연속 생존)의 직접 증거. |

## 검증 명령 재현

```
python -m regression.scenarios.validate
# -> 244 lifecycle(s) checked · 0 error(s) · 5 warning(s)  (5개는 이전 세션과 동일한 기존/무관 경고, diff로 확인됨)
```

## 남은 작업 (다음 HEAVY 디스패치용)

1. `compute-virtualserver-full` 재디스패치 — 항목 2(image-update)·3(attach-port
   +port-subnet)·4(delete-boot-volume) 3건이 실제로 2xx/생존-0 으로 전환되는지
   확인. 특히 4번은 말미 sweep 로그에서 IGNORE_TTL 수동 개입이 더 이상
   필요 없는지 관찰.
2. `gen-heavy-backup`은 **재디스패치해도 이번 세션 수정분이 없으므로** 현재
   상태(FILESYSTEM 빈 목록 + VM_IMAGE 500) 그대로일 것으로 예상 — 항목 1의
   이중 차단(product-bug + owner-waiver) 중 하나가 해소되기 전까지는 신규
   커버 기대하지 않음. 다음 세션이 재조사할 경우 "agent waiver 해제 후 실제
   agent 설치가 API로 완결 가능한지"(현재 문서상 "게스트 OS 안에서 설치
   파일 실행/구성 필요"로 API 완결 불가로 기록됨, `knowledge/formal/
   resources/storage__backup.yaml` L35) 재확인부터 시작할 것.
3. sweep의 TTL 정책 자체를 바꿀 필요가 있다고 판단되면(예: 항목 4의 lifecycle
   수정 후에도 다른 리소스 종류에서 유사 증상 재발), `.github/` 워크플로
   수정은 오케스트레이터/owner 승인을 받아 별도 세션에서 진행 — 이번 세션은
   권고만 남김(위 표 항목 4 참조).

---

# §HB4b-2 — networking/vpn·vpc(TGW)·loadbalancer 재수리 + reconciler TGW settle 갭 (2026-07-07, OFFLINE)

> 배경: `docs/working/plans/CAMPAIGN-C3-100.md` 진행 로그 "HB4b 종결" — run
> 28827996068 (109 obs: ok 58 · soft 51)의
> `reports/results/hb4b/observations-gw*.jsonl` note 필드 오류 전문 + `ts` 타임스탬프
> 간격을 원인 확정 증거로 사용. **라이브 SCP 호출 없음**(HB5 heavy run이 레인
> 점유 중이라 오프라인 전용). 실검증은 다음 HEAVY 디스패치가 담당. 오너 지시
> "오류난 건은 원인확인 필요" 준수 — 각 건 아래 표의 "증거" 열이 관측
> 원본/문서 근거, 근거 없이 수리한 항목 없음.
>
> 산출물: `regression/scenarios/lifecycles/networking__vpn.json` (IGW 3단 +
> teardown) · `regression/scenarios/lifecycles/networking__vpc.json` (TGW
> vpc-connection capture-key 수정 3건 + 신규 settle-poll) ·
> `regression/scenarios/lifecycles/networking__loadbalancer.json`
> (static-nat용 실 public IP 발급) · `cleanup/reconciler.py` (+
> `tests/offline/test_reconciler_convergence.py` 신규 테스트 2건) ·
> `knowledge/services.md`(networking/vpn·vpc·loadbalancer 섹션) ·
> `knowledge/validated-facts.md`(reconciler TGW settle 갭) · 본 섹션.
> `python -m regression.scenarios.validate` → **244 lifecycle(s) checked · 0
> error(s) · 5 warning(s)** (5개 경고는 이전 HB1/HB3b/HB4b 세션과 동일한
> 무관 항목 — 이번 세션에서 수정한 4개 파일과 무관함을 diff로 확인).
> `python -m pytest tests/offline` → 449 passed + 3 failed(모두 이번 변경과
> **무관한 기존 실패** — `git stash`로 변경 전 상태에서도 동일하게 실패함을
> 확인: `test_docs_index.py::test_index_is_up_to_date`,
> `test_validate_dag.py::test_real_graph_is_a_complete_dag`,
> `test_validate_dag.py::test_main_check_returns_zero_on_complete_graph` — DAG
> 정합성/문서 인덱스 이슈로 이번 네트워킹 lifecycle 변경과 무관, 손대지 않음).

## 원인확인 + 조치 표

| # | 증상 | 원인(증거) | 조치 |
|---|---|---|---|
| 1 | `networking-vpn-gateway-tunnel:create-vpn-gateway` 404 `scp-network.vpn-gateway.internet-gateway-not-found` | 관측 note 전문: `"Cannot found the Internet Gateway on VPC(58da5a4d...)."` — VPC에 IGW가 없다는 요건이 에러 코드/문구로 직접 확정(추정 아님). `data/api_docs.json`에 IGW 1개/VPC 카디널리티 제한 서술은 없음; 반면 같은 shared VPC에 이미 IGW를 붙이는 **검증 전례 2건**(networking/vpc의 create-internet-gateway-for-nat, 2026-06-23 라이브 CONFIRMED; networking/loadbalancer의 create-igw-for-static-nat, HB4b 오프라인 수리)이 하드 충돌 보고 없이 존재 — 신규 리스크 등급이 아니라고 판단, **자체 VPC 전환은 기각**(VPC 예산 cap 5 낭비, 커버리지 이득 없음). | `networking__vpn.json`에 `create-igw-for-vpn`/`wait-igw-for-vpn-active`/`set-igw-for-vpn` 3단(create-igw-for-static-nat 패턴 이식) + `delete-igw-for-vpn` teardown 추가. group `vpn` + optional + 4xx/409 허용 유지 — 충돌 발생 시 이 family만 soft-fail, 다른 동시 lifecycle의 IGW를 침범하지 않음. **라이브 미검증.** |
| 2a | `vpc-transit-gateway-children`의 firewall/routing-rule/uplink-routing-rule/set 400 `not-active-state:(EDITING)` — `create-tgw-vpc-connection` **성공(202) 직후** 발생 | 관측 타임스탬프 대조로 확정: `wait-tgw-active` 200(ts …894) → `create-tgw-vpc-connection` 202(ts …897, +3s) → GET 3개(읽기 전용, 문제없음) → `create-tgw-firewall` 400(ts …904, connection 생성 후 겨우 ~7s). TGW가 이미 ACTIVE였다가 connection 생성 자체가 다시 EDITING으로 되돌린다는 것을 시간차로 직접 증거(추정 아님) — HB4b 오너 가설("vpc-connection 후 재전이")과 일치. | `create-tgw-vpc-connection` 뒤에 `wait-tgw-active-after-connection`(기존 `wait-tgw-active`와 동일 field/until/give_up_status poll) 신규 삽입, 이후 모든 child create 앞에 위치. |
| 2b | `delete-tgw-vpc-connection` 400 `not-in-vpc-connection` — 에러 본문에 **미치환 리터럴 `{vpc_connection_id}`**가 그대로 노출 | 오너 지시는 "2a와 동일 처리(TGW non-active)"였으나, 에러 문구의 리터럴 placeholder 자체가 **capture_soft 실패**(TGW 상태와 무관)를 직접 증거함 — 가설을 관측으로 반증하고 실제 원인을 재확정. `data/api_docs.json` `createtransitgatewayvpcconnection`의 `response_example`은 `transit_gateway_vpc_connection.id`로 감싸는데(문서 확인), lifecycle의 `capture_soft`는 `$.vpc_connection.id`를 사용 중이었음 — 키 자체가 틀림. | `capture_soft`를 `$.transit_gateway_vpc_connection.id`로 정정. 같은 패턴으로 `create-tgw-routing-rule`/`create-tgw-uplink-routing-rule`의 `capture_soft`도 문서 대조 결과 동일하게 틀려 있어(`$.routing_rule.id` → 실제는 `$.transit_gateway_rule.id`, 양쪽 endpoint 모두 `data/api_docs.json` response_example로 확인) 동일 세션에서 정정. |
| 3 | `networking-loadbalancer-members-nat:members-add` 403 `scp-loadbalancer.members.InvalidVmInMember` — `"object_id: '', ip: '10.124.0.31'"` | 관측 note 전문이 실 VM object_id 요건을 직접 명시(빈 문자열이 거부됨). 이 lifecycle은 VM을 만들지 않음 — cross-service(compute/virtualserver) 선행 자원이 필요. | **수정 안 함 — 조사만, 판단 근거는 아래.** (a) cross-lifecycle capture 불가 확인: repo 전체에서 `"adopt"` 값은 `vpc`/`subnet`/`subnet#db` 3종뿐(grep 전수 확인) — `shared_infra.py`의 아웃오브밴드 프로비저닝(`.github/workflows/api-test.yml`이 pytest 시작 전에 실행, `SCP_SHARED_VPC_ID`류를 export)과 동일한 방식으로 VM을 공유하려면 엔진 변경 + `.github/` 변경이 모두 필요(이번 세션 금지 대상). (b) LB lifecycle 안에 자체 VM 클로저를 넣는 안은 기술적으로 가능하나, `compute-virtualserver-full` 자체가 port/volume/image 엣지케이스로 오프라인 수리 3라운드(HB3/HB3b/HB3b-2)를 거쳤을 만큼 그 create-server 바디 자체가 불안정 이력이 있어, 이미 34-엔드포인트급인 LB lifecycle에 동일 취약 클래스를 이식하는 비용(실 컴퓨트 과금 + 수분 대기 + 오프라인 미검증 리스크) 대비 이득(members 4종만)이 낮다고 판단. **권고**: (i) `shared_infra.py`+워크플로에 `SCP_SHARED_VM_ID`류를 추가해 `"adopt":"server"`를 엔진 1급 기능으로 만들거나, (ii) 다음 heavy 배치에서 이 LB lifecycle을 `compute-virtualserver-full`과 같은 배치에 넣고 그 run의 `server_id` 캡처가 라이브로 증명된 뒤 별도 소규모 오프라인 세션에서 `"adopt":"server"`를 이식 — 둘 중 오너 승인 필요, 이번 세션은 미실행. |
| 4 | `networking-loadbalancer-members-nat:static-nat-create` 404 `scp-loadbalancer.loadbalancers.PublicIpNotFound` — `"Public IP '' is not found."` | 관측 note가 빈 문자열 public IP를 직접 명시. IGW 수리(HB4b)는 여전히 필요하지만 충분조건이 아니었음 — `data/api_docs.json` `staticnatcreaterequestdetail` 모델은 `publicip_id`가 유일 필드이자 `required:true`인데 body가 의도적으로 `""`(당시 실 IP 확보 수단이 없어 설계된 상태, lifecycle 자체 주석에 명시)를 보내고 있었음. | `create-publicip-for-static-nat`(`type:IGW`, networking/vpc의 검증된 create-publicip-for-nat/for-vip 패턴 이식) 신규 추가 → `$.publicip.id`를 `static-nat-create`의 `publicip_id`로 주입, teardown에 `delete-publicip-for-static-nat` 추가(static-nat-delete 뒤, delete-igw-for-static-nat와 함께). |
| 5 | HB4b 말미 스윕이 TGW+VPC 쌍을 못 거둠(수 시간 뒤 수동 재스윕은 성공) | `cleanup/reconciler.py` 코드 확인으로 원인 확정(라이브 재현 불가, 코드 추적만): TGW 자신의 DELETE는 `state`가 `ACTIVE`/`ERROR`일 때만 수락됨(에러 문구 "not deletable state(Active, Error)")인데, 2026-07-03에 추가된 `_is_async_deleting`/`_ASYNC_DELETING_STATES`는 `DELETING`류(철거중) 상태만 in-progress로 집계하고 `CREATING`/`EDITING`(항목 2a처럼 vpc-connection 생성/삭제가 유발하는 재전이)는 집계 대상이 아니었음 — 즉 TGW의 connection이 이미 걷혔는데 TGW 자신이 잠깐 EDITING인 라운드에서 `_vpc_409_holder`도 더 이상 보호하지 않으면 genuine=0/inprog=0으로 수렴("stop") 판정 → 다음 라운드가 없었으면 실제로 남을 수 있는 코드 경로가 확인됨. 이는 워크플로 파라미터(`SCP_SWEEP_NOWAIT` 자체) 문제가 아니라 **리컨실러 TGW 경로의 상태-집계 누락**이라 최소 수정이 자연스러움(.github 불변). | `_is_tgw_settling(item)`(허용 상태 `{active, error}`, `_ASYNC_DELETING_STATES`는 제외) 신규 헬퍼 추가 — TGW 자신의 DELETE 직전에 검사해 참이면 DELETE를 시도하지 않고 `_INPROGRESS_THIS_ROUND`를 증가시켜 다음 라운드를 보장(기존 `_is_async_deleting`과 동일 취급). 오프라인 테스트 2건 추가(`test_is_tgw_settling_predicate`, `test_editing_tgw_delete_skipped_and_counts_in_progress`) — `python -m pytest tests/offline/test_reconciler_convergence.py` 30 passed(신규 2건 포함). |

## 검증 명령 재현

```
python -m regression.scenarios.validate
# -> 244 lifecycle(s) checked · 0 error(s) · 5 warning(s)  (5개는 이전 세션과 동일한 무관 경고)

python -m pytest tests/offline/test_reconciler_convergence.py tests/offline/test_reconciler_vpc_prefix.py
# -> 30 passed

python -m pytest tests/offline
# -> 449 passed, 3 failed (모두 변경 전에도 동일하게 실패하는 기존 이슈 — git stash로 확인)
```

## 남은 작업 (다음 HEAVY 디스패치용, 가칭 HB4c/HB5 이후)

1. `networking-vpn-gateway-tunnel` 재디스패치 — IGW 3단 추가 후 create-vpn-gateway가
   2xx로 전환되는지, 공식 phase1/2 터널 값까지 도달하는지 확인. 동시에 다른
   IGW-필요 lifecycle(NAT gateway, LB static-nat)과 같은 배치일 때 409 충돌
   신호가 있는지 관찰(항목 1의 "충돌 없음" 판단 재검증).
2. `vpc-transit-gateway-children` 재디스패치 — `wait-tgw-active-after-connection`
   추가 후 firewall/routing-rule/uplink-routing-rule/set/delete가 실제 2xx로
   전환되는지, capture-key 수정(vpc_connection_id/routing_rule_id/
   uplink_routing_rule_id) 후 delete 계열이 실 id로 동작하는지 확인.
3. `networking-loadbalancer-members-nat` 재디스패치 — static-nat-create가 실
   public IP로 2xx 전환되는지 확인. members-add는 항목 3의 권고(i)/(ii) 중
   오너가 택한 방향으로 후속.
4. 다음 말미 스윕에서 TGW+VPC 잔존이 재발하는지 관찰 — 재발하면 항목 5의
   `_is_tgw_settling` 수정이 충분한지, 아니면 TGW settle 자체가 라운드 예산
   (`SCP_SWEEP_ROUNDS`/`SCP_SWEEP_INPROGRESS_SLEEP_S`)보다 오래 걸리는 케이스가
   있는지(그 경우는 워크플로 파라미터 조정 권고 — `.github/` 승인 필요) 구분.
