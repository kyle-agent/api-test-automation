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
