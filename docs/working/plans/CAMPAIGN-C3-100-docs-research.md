---
status: DONE (docs-research, read-only) — 2026-07-04
for: 워크스트림 A 후속 에이전트 (HB2/HB6/HB7 실행자) — body 초안 입력
---

# CAMPAIGN-C3-100 — body 미상 7건 docs-research

> 오너 지시 원문(CAMPAIGN-C3-100.md §"SCP docs 조사 필요"): `data-ops
> createdataopsservice(service_workload)` · `data-flow
> createdataflow/createdataflowserviceconsole` · `eventstreams createcluster` ·
> `vpn phase1/phase2 enum` · `virtualserver createimage/importimage` · `backup
> createbackup(FILESYSTEM)` · `dns activateprivatedns`.
>
> **방법론**: 리포 내부 우선 (`data/api_docs.json`의 `endpoints[].request_example`
> + `models[]` 필드 스키마 — SCP 공식 API Reference 페이지를 `spec.scrape_docs`가
> 그대로 추출한 것, 즉 사실상 "공식 문서" 그 자체), `data/api_bodies.json`
> (`spec.extract_bodies`가 렌더된 예시를 재수집), 기존
> `regression/scenarios/lifecycles/*.json` + `knowledge/formal/**/*.yaml`의 과거
> 라이브 시도 기록, git history(과거 세션의 investigation 커밋). 웹은 보조
> (WebSearch/WebFetch) — SCP 공식 문서 웹 크롤은 이 리포의 `data/api_docs.json`
> 자체가 이미 그 결과물이므로 중복 조사는 최소화하고, 리포에 없는 신호(터폴로지
> 힌트 등)만 웹에서 보강했다. **body 창작 없음** — 모든 필드/값은 출처가 있다.
>
> **라이브 호출 없음** (읽기 전용). `regression/scenarios/` 파일은 수정하지
> 않았다 — 아래는 다음 실행 에이전트가 붙여넣을 body 초안이다.

## 요약 (한눈에)

| # | 대상 | 상태 | 핵심 근거 |
|---|---|---|---|
| 1 | data-ops `createdataopsservice.service_workload` | **부분** | 공식 schema 확인(`data-analytics/data-ops/dataopsservicecreaterequest`)이지만 필드 타입이 전부 `object`/`string`(값 도메인 미문서) — 라이브 400 "not valid" 재확인 필요 |
| 2 | data-flow `createdataflow` / `createdataflowserviceconsole` | **확보** | `data/api_docs.json` request_example 전체 body 공식 확보 (계정 3버전 모두: create-flow, create-service) |
| 3 | eventstreams `createcluster` | **부분** | 공식 schema 100% 확보 + 과거 세션의 "ZK-quorum" 가설 body(role_type 토폴로지)가 userguide 신호와 정합하나 **라이브 미검증** |
| 4 | vpn tunnel phase1/phase2 enum | **확보** | 공식 doc request_example(정확한 예시값) — 기존 lifecycle의 guessed 값과 다름(교체 권고) |
| 5 | virtualserver `createimage` / `importimage` | **확보** | 공식 schema+example 전체 확보, `createimage`는 라이브로 스키마 통과까지 확인(2026-06-18); `importimage` 필드명 오류 발견(`source`→`url`) |
| 6 | backup `createbackup` (FILESYSTEM) | **확보(스키마)** / 依존성 불가(라이브) | 공식 schema(enum 전부) 확보 — 다만 Agent-backup 선행 필요(owner waiver 대상), 실 2xx는 waiver 해제 전까지 도달 불가 |
| 7 | dns `activateprivatedns` | **확보** | 공식 doc request_example 완전 확보 (`{"name": "..."}`) |

---

## 1. data-analytics/data-ops — `createdataopsservice` (`service_workload`)

**상태: 부분** (구조 확보, 값 도메인 불가 — waiver 후보 아님, 다음 라이브 시도 필요)

### 확보된 body (공식 schema 기반, 구조 100% 확정)

출처: `data/api_docs.json` → `endpoints["data-analytics/data-ops/createdataopsservice"].request_example`
(SCP API Reference `.../data-ops/apis/createdataopsservice/1.1/` 그대로 추출) +
`models["data-analytics/data-ops/dataopsservicecreaterequest"]`.

```json
{
  "data_ops_id": "{data_ops_id}",
  "data_ops_service_name": "regrdosvc{ualpha}",
  "domain": "regrdosvc{ualpha}",
  "description": "API regression coverage probe",
  "host_alias": {"enabled": false, "host_alias_list": []},
  "node_selector": null,
  "service_workload": {
    "scheduler":  {"cpu": "2000", "memory": "1024", "replica": "1", "version": "2.7.3"},
    "web_server": {"cpu": "2000", "memory": "1024", "replica": "1", "version": "2.7.3"},
    "worker":     {"cpu": "2000", "memory": "1024", "replica": "1", "version": "2.7.3"}
  },
  "storage_class_name": "default",
  "worker_type": "KubernetesExecutor",
  "tags": []
}
```

이는 이미 `regression/scenarios/lifecycles/data-analytics__data-ops.json`의
`create-data-ops-service` 스텝이 보내는 body와 **필드 구성이 거의 동일**하다
(현재 body에 `account`/`node_selector`만 없음 — 둘 다 doc상 `required: false`이므로
누락이 원인은 아니다).

### 왜 "부분"인가

- `models["...dataopsservicecreaterequest"]`의 `service_workload` 필드는
  `"schema": "object"` — 하위 `cpu`/`memory`/`replica`/`version`도 전부
  `"schema": "string"`이며 **enum/pattern/example 값이 전혀 문서화되어 있지
  않다** (Example도 빈 문자열). `worker_type`도 `"schema": "string"` (enum
  아님) — userguide(`knowledge/formal/services/data-analytics__data-ops.yaml`
  `worker-executor-choice`)가 말하는 `Kubernetes|Celery`가 실제 API enum 값과
  1:1인지 미확인 (`KubernetesExecutor`는 유저가이드 표기를 그대로 붙인 추정).
- **라이브 재확인 (2026-06-24, `data-ops-service-and-ops-guarded` lifecycle
  `_note`)**: 위와 동일 구조(cpu/memory/replica/version을 string으로, 또 int로도
  시도)로 여러 조합을 보냈으나 전부 `400 "Input dataOpsServiceWorkload is not
  valid"` — 백엔드가 ID 조회보다 **먼저** body 검증을 하는 것도 확인됨. 즉
  구조는 맞을 가능성이 높은데(스키마와 field명 100% 일치) 값 도메인이 여전히
  거부된다.
- **다음 시도용 리드(미검증, 확보 아님)**: `GET /v1/data-ops/image-versions`
  (`getdataopsimageversionv1`, 공식 응답 스키마 `ImageVersionsResponse` →
  `contents[].version`)가 유효한 Airflow 버전 문자열을 라이브로 알려주는
  discovery 엔드포인트다 — `"2.7.3"`을 하드코딩하는 대신 이 GET으로 실제
  버전을 얻어 `service_workload.*.version`에 주입해볼 것을 권고한다(읽기
  전용이라 안전). data-flow도 동형(`getdataflowimages` →
  `/v1/data-flows/image-versions`).

### 남은 불확실성 / 다음 액션

1. cpu/memory 최소·최대치, replica 허용 범위, version 유효 문자열 — 문서
   미기재. `image-versions` GET 선(先)조회 후 그 값을 그대로 주입해 재시도.
2. `worker_type` 실제 enum 문자열 (`KubernetesExecutor`/`CeleryExecutor`
   추정치가 API 레벨에서 그대로 통하는지 미확인).
3. **waiver 후보 아님** — schema는 확보됐고 discovery 경로(`image-versions`)도
   있으므로, 다음 라이브 배치(HB7)에서 "GET image-versions → 그 값으로 재구성"
   1회 시도를 권고. 그래도 실패하면 그때 waiver 재고.

---

## 2. data-analytics/data-flow — `createdataflow` / `createdataflowserviceconsole`

**상태: 확보** (공식 문서 request_example 전체 body)

출처: `data/api_docs.json` → `endpoints["data-analytics/data-flow/createdataflow"]`
및 `endpoints["data-analytics/data-flow/createdataflowserviceconsole"]`
(`.../data-flow/apis/createdataflow/1.1/`, `.../createdataflowserviceconsole/1.1/`).

### `createdataflow` (POST /v1/data-flows) — 공식 예시 그대로

```json
{
  "account": {"account_id": "", "account_password": ""},
  "cluster_id": "",
  "data_flow_name": "",
  "description": "",
  "domain": "",
  "dsc_domain": "",
  "host_alias_list": [{"hostnames": [""], "ip": ""}],
  "image_id": "",
  "ingress_controller_name": "",
  "instance_id": "",
  "node_selector": "",
  "storage_class_name": "",
  "tags": [{"key": "Key", "value": "Value"}]
}
```

`models["data-analytics/data-flow/dataflowbodycreate"]` required 필드:
`cluster_id`, `data_flow_name`(3-30자), `domain`(3-50자), `host_alias_list`
(array[object], required — 빈 배열 `[]`도 허용되는지는 미확인, doc 예시는
1-원소 배열), `image_id`, `ingress_controller_name`, `storage_class_name`.
optional: `account`, `description`, `dsc_domain`, `instance_id`,
`node_selector`, `tags`.

현재 `regression/scenarios/lifecycles/data-analytics__data-flow.json`의
`create-data-flow` 스텝 body(`data_flow_name`/`cluster_id`/`domain`/
`host_alias_list: []`/`image_id`/`ingress_controller_name`/
`storage_class_name`/`description`/`tags`)는 이미 이 공식 스키마와 필드명
수준에서 일치한다 (account/dsc_domain/instance_id/node_selector 생략 —
전부 optional이라 무해).

### `createdataflowserviceconsole` (POST /v1/data-flow-services) — 공식 예시 그대로

```json
{
  "account": {"account_id": "", "account_password": ""},
  "data_flow_id": "",
  "data_flow_service_name": "",
  "description": "",
  "domain": "",
  "host_alias": {"enabled": "true", "host_alias_list": [{"hostname": "", "ip": ""}]},
  "node_selector": "",
  "service_workload": {
    "nifi":          {"cpu": "2000", "memory": "1024", "replica": "1", "version": "1.27.1"},
    "nifi_registry": {"cpu": "2000", "memory": "1024", "replica": "1", "version": "1.27.1"},
    "zookeeper":     {"cpu": "2000", "memory": "1024", "replica": "3", "version": "3.9.2"}
  },
  "storage_class_name": "",
  "tags": [{"key": "Key", "value": "Value"}]
}
```

이 경우는 §1(data-ops)과 달리 **`service_workload`의 예시값이 문서에 실제
값으로 채워져 있다** (cpu/memory/replica/version이 빈 문자열이 아님 — nifi
1.27.1, zookeeper 3.9.2, replica 3). data-ops 쪽은 동일 위치가 전부 빈
문자열인 것과 대비된다 (모델별 문서 완성도 차이). 현재 lifecycle의
`create-data-flow-service` body가 이미 이 값 그대로 사용 중 — **즉 data-flow는
이미 공식 문서의 실제 예시값을 쓰고 있다**. 남은 미지수는 이 예시값이
백엔드에 그대로 먹히는지(라이브 미검증, 클러스터 종속이라 HB7에서 확인
예정)뿐이며, body 자체의 "미상"은 이 조사로 해소됐다고 판단.

### 남은 불확실성

- `host_alias_list`가 doc 예시처럼 1-원소({"hostnames":[""],"ip":""})여야
  하는지, 빈 배열 `[]`이 허용되는지 미확인 (required 필드이나 빈 배열이
  "존재"로 인정되는지는 스키마상 불명).
- `service_workload`의 예시 수치(cpu/memory/replica/version)가 "예시"인지
  "고정 필수값"인지 불명 — data-ops와 달리 data-flow는 실값이 채워진 이유가
  둘 중 하나: (a) 실제로 유일하게 허용되는 값 조합이거나 (b) 단순히 이
  모델의 문서화 담당자가 예시를 성실히 채웠을 뿐. 라이브 확인 필요.

---

## 3. data-analytics/eventstreams — `createcluster`

**상태: 부분** (공식 schema 100%, 토폴로지 값은 과거 세션의 가설 — 라이브 미검증)

### 공식 schema (필드 100% 확정)

출처: `data/api_docs.json` → `endpoints["data-analytics/eventstreams/eventstreamscreatecluster"]`
(model `EventStreamsClusterCreateRequestV1Dot1`). Required: `init_config_option`
(broker_sasl_id/password, zookeeper_sasl_id/password — sasl id pattern
`^[a-z]+$` 2-20자, `knowledge/formal/services/data-analytics__eventstreams.yaml`
`create-cluster-required-init-config`), `dbaas_engine_version_id`,
`instance_groups`, `instance_name_prefix`(`^[a-z][a-zA-Z0-9\-]*$` 3-13자),
`name`(`^[a-zA-Z]*$` 3-20자), `service_watch_log_collection`, `subnet_id`,
`timezone`. `instance_groups[].role_type` enum:
`{ZOOKEEPER_BROKER, BROKER, ZOOKEEPER, AKHQ, CONSOLE}` (doc 예시의 `ACTIVE`는
다른 엔진(mysql/mariadb 등)용 값이 잘못 섞인 것 — eventstreams 전용 enum이
따로 있다는 것이 이번 재확인 포인트).

### 현재 최선 후보 body (2026-06-19 과거 세션 "ZK-quorum" 가설 + 이번 웹 조사로 보강)

출처: `data/api_bodies.json["data-analytics/eventstreams/eventstreamscreatecluster"]`
(commit `700f72a0`, "eventstreams ZK quorum" investigation fix — 공식 문서
예시가 아니라 **과거 세션이 근거를 갖고 수정한 가설**: 토폴로지를 combined
`ZOOKEEPER_BROKER` 노드 3개로 바꿈).

```json
{
  "name": "regrk",
  "akhq_enabled": false,
  "allowable_ip_addresses": [],
  "dbaas_engine_version_id": "{engine_version_id}",
  "init_config_option": {
    "broker_port": 9091,
    "broker_sasl_id": "regruser",
    "broker_sasl_password": "Regr1ss@2026",
    "zookeeper_port": 2180,
    "zookeeper_sasl_id": "regruser",
    "zookeeper_sasl_password": "Regr1ss@2026"
  },
  "instance_groups": [
    {
      "role_type": "ZOOKEEPER_BROKER",
      "server_type_name": "db1v2m4",
      "block_storage_groups": [{"role_type": "OS", "size_gb": 104, "volume_type": "SSD"}],
      "instances": [
        {"public_ip_id": "", "role_type": "ZOOKEEPER_BROKER"},
        {"public_ip_id": "", "role_type": "ZOOKEEPER_BROKER"},
        {"public_ip_id": "", "role_type": "ZOOKEEPER_BROKER"}
      ]
    }
  ],
  "instance_name_prefix": "regrk",
  "is_combined": true,
  "maintenance_option": {"period_hour": "5", "starting_day_of_week": "MON", "starting_time": "0000"},
  "nat_enabled": false,
  "service_watch_log_collection": true,
  "subnet_id": "{subnet_id}",
  "tags": [],
  "timezone": "Asia/Seoul"
}
```

### 근거/신뢰도

- **필드명·필수여부**: 확실 (공식 model, conf 0.9).
- **토폴로지(3× combined ZOOKEEPER_BROKER, `is_combined: true`)**: 이번
  WebFetch로 SCP userguide 요약(`docs.e.samsungsdscloud.com/userguide/analytics/
  event_streams/overview/` — 페이지 렌더 이슈로 모델의 자체 요약 경유, 원문
  발췌 인용은 불가) 신호: "Zookeeper를 별도 설치하지 않으면 Broker 노드에
  같이 배포된다"(combined 모드 존재 확인) + "3개 이상이 일반적"이라는 취지 —
  이는 700f72a0의 "ZK quorum(3노드)" 가설과 **방향은 일치**하지만 문서가 정확한
  최소 노드 수·조합 규칙을 명시하지 않아 **가설의 확증은 아니다**. conf 0.5.
- **라이브 검증 상태**: `knowledge/formal/resources/data-analytics__eventstreams.yaml`
  (commit `ada47e7d`, 2026-06-12)은 "라이브 시도가 undocumented topology
  value_error로 실패"라고 기록했지만, 이 기록은 700f72a0(2026-06-19)의
  ZK-quorum 수정 **이전** 시점이다 — 즉 **현재 body(위 JSON)는 아직 한 번도
  라이브로 재시도되지 않았다** (`docs/working/plans/CAMPAIGN-C3-100.md`도
  "create body 미검증"으로 분류, 2026-07-04 기준 최신 확인).

### 남은 불확실성 / 다음 액션

1. 이 body를 HB2(eventstreams 실클러스터 슬롯)에서 **가장 먼저** 시도할 것을
   권고 — 과거 세션의 근거 있는 수정이 아직 라이브 테스트를 거치지 않았다.
2. 실패 시 (a) `role_type: BROKER` 별도 그룹 + `role_type: ZOOKEEPER` 별도
   그룹(비-combined) 조합, (b) 홀수 zookeeper 카운트(3/5) 변형을 다음
   순서로 시도.
3. `dbaas_engine_version_id`/`subnet_id`/`server_type_name`은 각각
   `GET /v1/engine-versions`, 세션 공유 VPC subnet, DBaaS server-type 목록에서
   라이브로 채워야 함(플레이스홀더 `{engine_version_id}`/`{subnet_id}` 그대로
   보내면 즉시 400).

---

## 4. networking/vpn — tunnel phase1/phase2 enum

**상태: 확보** (공식 문서 request_example — 기존 lifecycle 값과 다름, 교체 권고)

출처: `data/api_docs.json` → `endpoints["networking/vpn/createvpntunnel"]`
및 `models["networking/vpn/vpnphase1createrequestv1dot1"]` /
`models["networking/vpn/vpnphase2createrequestv1dot1"]`
(`.../vpn/apis/createvpntunnel/1.1/`).

### 공식 문서 예시값 (phase1/phase2, create·set 두 엔드포인트 모두 동일 예시 사용)

```json
{
  "phase1": {
    "dpd_retry_interval": 60,
    "ike_version": 2,
    "peer_gateway_ip": "123.0.0.2",
    "phase1_diffie_hellman_groups": [30, 31, 32],
    "phase1_encryptions": ["des-md5", "chacha20poly1305-prfsha256"],
    "phase1_life_time": 86400,
    "pre_shared_key": "PreSharedKey1"
  },
  "phase2": {
    "perfect_forward_secrecy": "ENABLE",
    "phase2_diffie_hellman_groups": [30, 31, 32],
    "phase2_encryptions": ["null-md5", "aes128gcm", "chacha20poly1305"],
    "phase2_life_time": 43200,
    "remote_subnets": ["10.1.1.0/24", "10.1.2.0/24", "10.1.3.0/24"]
  }
}
```

- `perfect_forward_secrecy`는 문서상 **진짜 enum**: `(ENABLE, DISABLE)` —
  이것만은 확정.
- `phase1_diffie_hellman_groups`/`phase2_diffie_hellman_groups`,
  `phase1_encryptions`/`phase2_encryptions`는 스키마 타입이
  `array[integer]`/`array[string]` (enum 타입 아님) — 문서는 이 정확한 예시
  조합(`[30,31,32]`, `["des-md5","chacha20poly1305-prfsha256"]` 등)만
  "Example"로 제시할 뿐 **전체 허용값 목록은 비공개**. 그래도 이 조합 자체가
  공식 문서가 제시하는 "동작하는 예시"이므로 창작보다 훨씬 근거가 강하다.

### 중요 발견 — 기존 lifecycle 값은 공식 예시와 다르다

`regression/scenarios/lifecycles/networking__vpn.json`의
`networking-vpn-gateway-tunnel` lifecycle은 현재
`"phase1_diffie_hellman_groups": [14]`, `"phase1_encryptions":
["aes256-sha256"]` (표준 IKE DH group 14 + AES256-SHA256 스타일 문자열)을
보내고 있는데, 이는 **공식 문서 예시와 일치하지 않는다** (문서는 `[30,31,32]`
+ `des-md5`/`chacha20poly1305-prfsha256`류 문자열 사용). 현재 값이 어디서
왔는지는 git blame상 불명 — "docs-example guesses"라고 자체 주석에 이미
명시되어 있었다(UNVALIDATED). **권고**: 다음 VPN 실행 배치(HB4)에서 위 공식
예시값으로 교체해 시도할 것 — 최소한 `perfect_forward_secrecy: ENABLE`은
스키마상 확정 enum이므로 그대로 유지.

### 남은 불확실성

- DH group `30/31/32`, encryption 문자열 목록이 계정/리전 전역에서 유효한
  "유일한" 조합인지, 아니면 여러 유효 조합 중 하나의 예시일 뿐인지 불명.
- 라이브 실행 없이는 어느 쪽이 통과하는지 확인 불가 — waiver 후보 아님,
  HB4에서 공식 예시값으로 1회 시도 권고.

---

## 5. compute/virtualserver — `createimage` / `importimage`

**상태: 확보** (공식 schema+example, `createimage`는 라이브 스키마 통과까지 확인됨)

출처: `data/api_docs.json` → `endpoints["compute/virtualserver/createimage"]`,
`endpoints["compute/virtualserver/importimage"]`,
`models["compute/virtualserver/imagecreaterequest"]`,
`models["compute/virtualserver/imageimportrequest"]`
(`.../virtualserver/apis/createimage/1.3/`, `.../apis/importimage/1.3/`).

### `createimage` (POST /v1/images)

```json
{
  "name": "regrimg{ualpha}",
  "os_distro": "ubuntu",
  "disk_format": "qcow2",
  "container_format": "bare",
  "min_disk": 24,
  "min_ram": 0,
  "visibility": "private",
  "url": "https://object-store.{region}.e.samsungsdscloud.com/regression-coverage/nonexistent-{ualpha}.qcow2",
  "tags": []
}
```

`os_distro` enum 확정: `(alma, centos, rhel, rocky, ubuntu, windows, oracle)`.
`visibility` pattern `private|shared` (문서 예시엔 `shared`, 소스 lifecycle은
`private` — 둘 다 유효해 보임). **라이브 재확인 (2026-06-18,
`vs-image-write-coverage` lifecycle `_note`)**: 이 정확한 body로 `POST
/v1/images`를 보냈을 때 더 이상 `ValidationError`가 아니라
`Image.InvalidObjectStorageUrl`(리소스 단계 오류, body 통과 증거)을 받았다 —
즉 **body 스키마 자체는 라이브로 검증됨**, 남은 유일한 변수는 실제 Object
Storage에 업로드된 진짜 `.qcow2` URL (billable/heavy, out of scope here).

### `importimage` (POST /v1/images/{image_id}/import) — 필드명 오류 발견

공식 model `ImageImportRequest`는 필드가 **`url`만** 있다(required, pattern
`.*\.qcow2$`, 최대 255자):

```json
{"url": "https://object-store.kr-west1.s.samsungsdscloud.com/{bucket}/{object}.qcow2"}
```

**현재 `regression/scenarios/lifecycles/compute__virtualserver.json`의
`import-image` 스텝은 `{"source": "regression-coverage-probe"}`를 보내고
있는데 이는 잘못된 필드명이다** (`source`가 아니라 `url`). 이 필드명 자체가
스키마 위반이라 지금은 아마 `ValidationError`(400)로 끝날 것 — createimage와
같은 패턴으로 실제 qcow2 URL 없이도 "필드명 통과 → 리소스 단계 오류"로
바꿔볼 수 있다.

### 남은 불확실성

- 둘 다 실제 2xx를 받으려면 Object Storage에 업로드된 진짜 `.qcow2` 파일이
  필요(heavy/billable, 이번 조사 범위 밖) — 스키마상 미상은 완전히 해소됨.

---

## 6. storage/backup — `createbackup` (FILESYSTEM)

**상태: 확보(스키마)** — 라이브 2xx는 Agent 선행 요건 때문에 현재 owner
waiver 범위(2026-06-10 "agent 없는 백업으로만") 밖에서만 가능, waiver 해제
전까지는 **가능 불가**로 분류

출처: `data/api_docs.json` → `endpoints["storage/backup/createbackup"]`,
`models["storage/backup/backupcreaterequest1dot2"]` (`.../backup/apis/
createbackup/1.2/`). `policy_category` enum `(AGENTLESS, AGENT)`,
`policy_type` enum `(VM_IMAGE, FILESYSTEM)`, `server_category` enum
`(VIRTUAL_SERVER, GPU_SERVER, BAREMETAL_SERVER)`, `retention_period` enum
`(WEEK_2, MONTH_1, MONTH_3, MONTH_6, YEAR_1)` — 전부 문서에 명시된 진짜 enum.

### FILESYSTEM용 body (VM_IMAGE와 다른 필드 조합)

```json
{
  "name": "regrbk{unique}",
  "policy_category": "AGENT",
  "policy_type": "FILESYSTEM",
  "server_category": "BAREMETAL_SERVER",
  "server_uuid": "{backup-target.backup_server_uuid}",
  "server_guid": "{backup-target.backup_server_guid}",
  "is_all_filesystem": false,
  "filesystem_paths": ["/aaa", "/bbb"],
  "schedules": [{"frequency": "DAILY", "start_time": "09:00:00", "type": "FULL"}],
  "retention_period": "WEEK_2",
  "region": "{region}",
  "encrypt_enabled": true,
  "dr_enabled": false,
  "tags": []
}
```

VM_IMAGE 바디(`regression/scenarios/lifecycles/storage__backup-light.json`
`create-backup`)와의 차이: `policy_category: AGENT`(not AGENTLESS),
`policy_type: FILESYSTEM`, `is_all_filesystem`/`filesystem_paths`가 이번엔
**의미 있는 필드**(VM_IMAGE에서는 무의미하지만 문서 예시가 습관적으로
같이 보냄 — `knowledge/formal/resources/storage__backup.yaml`의
"AGENTLESS body에서 제거" 노트 참조). `server_category`는 userguide상
Bare Metal이 FILESYSTEM(Agent형)의 대표 대상이나, 문서 enum은 VIRTUAL_SERVER/
GPU_SERVER도 허용하므로 셋 다 시도 가능.

### 왜 라이브 2xx가 "불가"로 분류되는가

- `knowledge/formal/services/storage__backup.yaml` (`server-prereq`, docs):
  "Agent backup(FILESYSTEM, Bare Metal 포함)은 백업 생성 **전에** Backup
  Agent가 대상 서버에 설치/구성되어 있어야 한다." Agent 설치는 API로
  완결되지 않는 콘솔/게스트-OS 내 절차(`knowledge/formal/resources/
  storage__backup.yaml` `backup-agent` 노드 notes).
- **owner waiver (2026-06-10, "agent 없는 백업으로만")**로 agent 계열 8
  ops(`createbackupagent` 포함)가 명시적으로 WAIVED —
  `data/baselines/coverage_waivers.json` 대상. FILESYSTEM `createbackup`은
  이 waiver 대상 자체는 아니지만 **선행 자원(Agent)이 waiver로 막혀 있어
  실질적으로 도달 불가**.
- 근거: `getbackuptargetlist`(FILESYSTEM 쿼리)는 **라이브 200 확인됨**
  (`data/baselines/known_issues.json` 2026-06-20 확인: "FILESYSTEM policy_type
  returns 200 for all server categories") — 즉 discovery 경로 자체는 살아
  있으나, Agent 미설치 계정에서는 빈 목록을 반환할 것으로 예상되어
  `server_uuid`를 채울 실제 대상이 없다.

### 남은 불확실성 / 권고

1. body 스키마는 확보 완료 — 창작 아님.
2. waiver 해제(Agent 설치 절차를 콘솔/수동으로 1회 수행) 없이는 FILESYSTEM
   createbackup의 실 2xx는 도달 불가 — **waiver 후보로 유지 권고**(owner
   재확인 필요, 2026-06-10 결정과 동일 선상).
3. Agent 없이도 "스키마 통과 확인"(400 ValidationError → 404/409 리소스
   오류로 전환)까지는 가능할 수 있음 — HB3에서 시도해 createimage 사례처럼
   최소한 body 형태 자체는 검증해볼 가치 있음.

---

## 7. networking/dns — `activateprivatedns`

**상태: 확보** (공식 문서 request_example 완전 확보)

출처: `data/api_docs.json` → `endpoints["networking/dns/activateprivatedns"]`
(`.../dns/apis/activateprivatedns/1.3/`), model `PrivateDnsActivateRequest`.

```json
{"name": "regrpdns{ualpha}"}
```

필드는 `name` 하나뿐 (문서 예시 `"private-dns01"`). `POST
/v1/private-dns/activate`는 ID가 아니라 **이름으로** 동작 — 이는
`knowledge/formal/services/networking__dns.yaml`의
`private-dns-account-global` quirk(Private DNS 이름은 계정 전역이며, 한
리전에서 생성 후 다른 리전에서는 "activate"로 동일 이름을 활성화한다)와
정합한다. 동일 body가 이미 `regression/scenarios/scenarios.json`의
비활성화된(disabled, superseded) 레거시 `dns-activate` 스텝에서도
사용됐다(`{"name": "regrpdns{ualpha}"}`) — 다만 그 스텝은 "이 실행 리전에서는
activate가 불필요"라는 이유로 비활성화된 것이지 body가 틀려서가 아니다.

### 남은 불확실성

- 라이브 2xx 확인 없음(disabled 상태로만 존재) — activate는 "동일 이름을
  **다른 리전**에서" 사용할 때만 의미가 있어, 단일 리전 회귀 스위트에서는
  트리거 조건 자체가 드물다(멀티 리전 시나리오 필요). body 자체의 미상은
  해소됐다고 판단.

---

## 부록 — 사용한 리포 내부 소스 지도

- `data/api_docs.json` — `{endpoints:{<cat>/<svc>/<op>: {method, path,
  request_example, response_example, ...}}, models:{<cat>/<svc>/<model>:
  {fields:[{name, required, schema, description}]}}}`. **가장 신뢰도 높은
  출처** — SCP 공식 API Reference 페이지의 "Example HTTP request" 블록을
  그대로 파싱한 것 (spec.scrape_docs). 이번 조사의 1차 근거 대부분이 여기서
  나왔다.
- `data/api_bodies.json` — `spec.extract_bodies`가 렌더된 예시를 재수집한
  덤프. eventstreams 항목은 예외적으로 과거 세션(`700f72a0`)이 수동으로
  값을 채워넣은 **가설(hypothesis) body** — 문서 스크레이핑 결과가 아님을
  주의(§3 참조).
- `regression/scenarios/lifecycles/*.json` — 각 서비스의 현재 coverage-probe
  body와 라이브 시도 결과 `_note`(가장 최근 실측 상태).
- `knowledge/formal/services/*.yaml` + `knowledge/formal/resources/*.yaml` —
  userguide 기반 제약(quota/네이밍/선행조건) + body 조립 근거 및 provenance.
- `data/baselines/known_issues.json` — 백엔드 버그/특이 케이스(baseline).
- 웹: SCP userguide(`docs.e.samsungsdscloud.com/userguide/analytics/
  event_streams/overview/`) 요약 조회 1건(§3, eventstreams 토폴로지 힌트) —
  원문 인용 불가(WebFetch가 렌더 축약본만 반환), 신호로만 사용.
