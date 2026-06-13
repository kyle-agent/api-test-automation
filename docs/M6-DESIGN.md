# M6 설계 — 자율 운영 가능한 SCP API 회귀 테스트 플랫폼

> 작성: Planner agent · 2026-06-13 · 의사결정 문서 (구현 명세 아님)
>
> **목표.** SCP API 회귀 테스트를 *지속적으로 자동화*하되, **플랫폼을 만드는 일
> 자체를 agent(Planner→Coordinator→Executors)가 자율 수행**하고, 사람은 도메인
> 지식 제공과 게이트 승인만 한다. M6은 이 자율 루프를 "닫는" 6개 작업으로 나뉜다.
>
> **설계 원칙 (이 문서 전체에 적용).** 새로 만들지 않는다. 모든 결정은 이미 존재
> 하는 메커니즘에 묶고, 파일/함수를 명시한다. 빈 곳을 메우는 얇은 어댑터만 추가
> 한다. 근거:
> - 조합 컴파일러 `regression/scenarios/composer.py` (`load_model`/`plan`/`compose`)
> - 지식 모델 `knowledge/formal/resources/<cat>__<svc>.yaml` (245 task / 59 svc) + `_groups.yaml`
> - R1 게이트 `knowledge/formal/validate.py`, 시나리오 게이트 `regression/scenarios/validate.py`
> - CI `/.github/workflows/api-test.yml` (spec→regression A∥B→sweep∥conformance→dashboard)
> - 컨트롤플레인 `controlplane/app.py` + `controlplane/static_export.py` (212 Pages)
> - 자원 이벤트 `core/oplog.py` (`emit_resource`→S3 `runs/<id>/res/*.json`)
> - 대시보드 `dashboard/build.py` + `dashboard/ops.html` + `dashboard/gen_dep_map.py`

---

## 0. 현재 상태 한눈에 (근거 인벤토리)

| 영역 | 이미 있음 | 빠진 것 (= M6 작업) |
|---|---|---|
| 지식 모델 | resources/*.yaml 245노드, validate.py R1 | 신규 서비스 온보딩 키트, `tools/new_service.py` |
| 조합 실행 | composer.plan/compose, dependency closure+dedup+branch-cost+peak_quota | target= 문법, `compose_service/group/theme`, CI 소비 경로 |
| 가시화 | ops.html (S3 res-event 트리/간트), gen_dep_map.py | plan-manifest(plan.json), 의도 vs 실제 오버레이 |
| 관제 | controlplane 라우트(`/planning /testing /reporting`), static_export 212p | `/platform/` IA 통합, **발행 race 수정** |
| 자율 루프 | orchestrator.md(Assess→Plan→Delegate→Integrate→Record), ledger.json, retirement.py | Planner 주기/입력 정의, IMPROVEMENT-BACKLOG.md 포맷 |

확인된 위험(§G에서 backlog seed로 정리): **dashboard-data 발행 force-push race**(확정),
실행 간 태그 네임스페이스 부재(sweep 교차 삭제 위험), monitor 재무장 toil,
validator의 96건 "create without delete" 경고(lookup 노드), ops.html에 DEP-MAP 수동
붙여넣기, restore/upgrade 체인 게이트, second-account backlog.

---

## A. M6a — 신규 서비스 온보딩 키트

### A.1 결정: YAML-우선(YAML-first)

신규 서비스 owner가 작성하는 **유일한 입력은 한 개의 YAML 파일**
`knowledge/formal/resources/<cat>__<svc>.yaml` 이다. 웹의 자원 작성 폼
(`controlplane/resource_routes.py`의 `/planning/resources/{node_id}` 폼)은
GitHub Pages 정적 발행본에서 **읽기 전용**(static_export가 TestClient로 렌더한
HTML, 서버 전용 버튼은 배너로 비활성)이므로, owner가 Pages에서 직접 노드를 만들
수 없다. 따라서 권위 있는 진입점은 *git에 커밋되는 YAML*이며 폼은 뷰어로 격하한다.

### A.2 owner가 쓰는 최소 YAML (가상 서비스 `cache/redis-cache` 예시)

`load_model()`은 파일 안의 `resources:` 딕셔너리를 읽고(`composer.py:151`), 파일명
`<cat>__<svc>.yaml`로 서비스를 식별한다. 노드 스키마는 composer 모듈 docstring
(`composer.py:17-89`)과 R1 검증 키 집합이 정의한다. 최소 필수는 `service` +
`create.endpoint` + `capture` + `delete.endpoint`(teardown 가능하게) + `provenance`.

```yaml
# knowledge/formal/resources/cache__redis-cache.yaml
resources:
  redis-cache:
    code: "ch-redis-cache"          # <cat>-<grp>-<resource>; _groups.yaml의 ch-redis 그룹
    service: cache/redis-cache       # category/service. step service = 마지막 세그먼트
    requires:
      - vpc                          # 의존 노드(plan이 closure에 자동 포함)
      - {ref: subnet, count: 1}
    create:
      endpoint: "POST /v1/redis-caches"
      body:
        name: "regrcache{unique}"    # {unique}=엔진 builtin (passthrough)
        vpc_id: "{vpc.vpc_id}"       # 필요 노드의 primary capture
        subnet_id: "{subnet.subnet_id}"
        node_type: "{opt.node_type}"
        engine_version: "{opt.engine_version}"
      options:
        node_type:    {type: enum, values: [cache.t3.small, cache.r5.large], required: true}
        engine_version: {type: string, default: "7.0"}
    capture:
      redis_id: "$.cache.id"         # 첫 키 = primary capture
    ready:
      field: "$.cache.state"
      until: ACTIVE
      timeout: 600
      interval: 10
      endpoint: "GET /v1/redis-caches/{redis_id}"
    verify:
      - name: set-params
        endpoint: "PUT /v1/redis-caches/{redis_id}/parameters"
        json: {maxmemory_policy: "allkeys-lru"}
        expect_status: [200, 202]
    delete: {endpoint: "DELETE /v1/redis-caches/{redis_id}", destructive: true}
    quota: redis-cache               # peak_quota 집계 키 (core/budgets 또는 dependencies.json에 존재해야)
    provenance: docs                 # docs(미증명) → 라이브 2xx 확인 후 VALIDATED로 승격
```

이 한 파일이 들어오면 `compose(["redis-cache"])`가 vpc/subnet closure를 자동으로
끌어와(`plan._expand` 재귀, `composer.py:304`) create→ready(poll)→verify→teardown
(역순 interval 스케줄, `composer.py:879`) 전체 라이프사이클 JSON을 컴파일한다.

### A.3 `tools/new_service.py` 명세 (스캐폴더)

**모델은 `tools/retirement.py`** — 동일 패턴: `data/api_catalog.json`을
`_catalog_index()`로 인덱싱하고(`retirement.py:27`, `_norm()`로 경로 정규화),
argparse CLI, `python -m tools.new_service` 진입, stdout/파일로 산출. retirement가
"커버 매트릭스"를 찍듯 new_service는 "노드 stub"을 찍는다.

- **입력**: `--service <cat>/<svc>` (필수), `--out` (기본
  `knowledge/formal/resources/<cat>__<svc>.yaml`).
- **endpoint 후보 추출**: `data/api_catalog.json`(flat list,
  `{key,category,service,method,http_path,...}`)을 `service`로 필터. POST=create
  후보, DELETE=delete 후보, GET(단건 경로 = `{id}` 포함)=ready/read 후보,
  PUT/PATCH/POST(하위 경로)=verify 후보로 휴리스틱 분류.
- **DTO 필드 스켈레톤**: `data/api_docs.json`의 `models`(2,306개,
  `{fields:[...], category, service}`)에서 해당 service의 create 요청 모델을 찾아
  `body:` 키를 `required` 필드 위주로 자동 채우고 값은 `"{opt.<field>}"` 또는
  `"<TODO>"` placeholder로.
- **capture 추측**: create 응답 모델의 `id`류 필드 → `capture: {<svc>_id: "$.<root>.id"}`.
  ready 경로의 `state`/`status` 필드 → `ready.field` 추측. (추측이므로 주석에 근거 표기.)
- **provenance**: 모든 추출 노드는 `provenance: docs`(미확인) 스탬프 — INGESTION.md의
  문서→VALIDATED 승급 규칙(`knowledge/formal/INGESTION.md`)을 그대로 따른다.
- **출력**: 컴파일 가능하지만 *비활성 초안*인 stub YAML 한 파일. owner는 quota 키,
  enum 값, verify 본문만 손본다.

> 자율성 관점: new_service.py는 Executor agent가 도는 도구다. owner는 "이 서비스
> 추가해줘"라고 도메인 지식(어떤 quota, 어떤 prereq)만 주고, agent가 stub을 생성→
> 손질→검증→PR한다.

### A.4 검증 게이트

stub은 곧장 R1 게이트를 통과해야 한다:
```bash
python knowledge/formal/validate.py     # R1: ref/one_of 타겟 해소, 토큰→capture/option 검증,
                                         #     quota 키 존재, _groups.yaml 형식
```
신규 노드가 `create`만 있고 `delete.endpoint`가 없으면 **"create without
delete — composed lifecycles will have no teardown"** 경고(현재 96건과 같은 류)가
뜬다. lookup/조회전용이면 의도된 것이므로 `capture_soft: true`로 명시(다른 노드에
먹일 수 없음, `composer.py:737` 가드)하거나 §G의 분류 정책으로 억제한다.

### A.5 "YAML 착륙 → 첫 예약 실행" 경로 (플랫폼팀 작업 0)

1. owner/agent가 `cache__redis-cache.yaml` 커밋 (브랜치 또는 PR).
2. `.github/workflows/validate.yml`이 R1 게이트 실행(머지 전 차단).
3. 머지되면 `load_model()`이 다음 실행부터 노드를 자동 인지(파일 글롭, 코드 변경 0).
4. 첫 실행에서 §B의 `compose_service("redis-cache")` 가 closure 포함 번들을 컴파일,
   `enabled:false` 초안으로 저장(`compose()`가 `enabled:False` 고정,
   `composer.py:923`) → Coordinator/owner가 라이브 2xx 확인 후 활성화.
5. ledger.json의 service 행 status가 `authored`→`integrated`→`live-validated`로 진행.

플랫폼팀(=사람)이 손대는 곳: **R1 게이트 통과 승인과 credential 발급뿐.** 코드 변경 없음.

---

## B. M6b/M6c — 조합·연쇄 실행

### B.1 target 문법 (composer 위에 얹는 얇은 해석기)

`compose()`/`plan()`은 **노드 id 리스트**만 받는다(`composer.py:719`). 사람이/agent가
쓰기 좋은 셀렉터를 노드 리스트로 푸는 `expand_targets(spec) -> list[node_id]`를
`regression/scenarios/` 신규 모듈(예: `targets.py`)에 둔다. 문법:

| 셀렉터 | 푸는 규칙 | 근거 |
|---|---|---|
| `service:<cat>/<svc>` | 해당 service의 모든 노드 | `load_model()`의 `task["service"]` 일치 |
| `group:<code>` | `<cat>-<grp>-*` code 접두 일치 노드 | 노드 `code` 필드 + `_groups.yaml` |
| `theme:read-only` | `delete`/`create`가 없는 lookup 노드 (capture_soft 포함) | 노드 shape |
| `theme:crud` | create+delete 둘 다 있는 노드 | 노드 shape |
| `theme:heavy` | `heavy: true` 노드 | `task.get("heavy")` (`composer.py:226`) |
| `theme:vary` | option에 `vary: true`가 있는 노드 | create.options[*].vary |
| `all` | 모델 전체 노드 | `load_model()` 키 전체 |

`expand_targets`는 **셀렉터→노드 리스트만** 책임지고, 의존 closure·dedup·순서·
branch 선택은 전부 `plan()`이 한다(이미 구현). 즉 신규 코드는 필터 한 겹뿐.

### B.2 `compose_service(svc)` = 서비스 전체를 번들 타겟으로

```python
def compose_service(svc, **kw):
    targets = expand_targets(f"service:{svc}")     # 그 서비스의 모든 노드
    return composer.compose(sorted(targets), **kw) # closure는 plan이 자동 포함
```
한 서비스의 모든 노드를 동시에 타겟으로 주면 `plan()`이 공유 prereq(vpc/subnet 등)를
**한 번만** 만들고(dedup, `plan()` 반환 `dedup`, `composer.py:432`), verify는 타겟
노드의 공유 인스턴스에만 그래프트(`composer.py:842`)된다. `compose_group`/`compose_theme`도
동일 한 줄. — **새 컴파일 로직 0.**

### B.3 run-request / workflow_dispatch 소비 경로

CI는 이미 `.github/run-request`의 `KEY=VALUE`(마지막 줄 우선)와 workflow_dispatch
입력을 spec job의 "Resolve run options" 단계에서 `req_*` 출력으로 파싱한다
(`api-test.yml`, spec job). M6은 키 **하나**를 추가:

- `compose=<selector>` (예: `compose=service:cache/redis-cache`,
  `compose=group:ch-redis`, `compose=theme:crud`).
- spec job이 `req_compose`를 출력 → 새 step이 `expand_targets`+`compose`를 호출,
  결과 라이프사이클을 `regression/scenarios/lifecycles/gen-*.json` 초안으로 저장
  (resource_routes의 `save_lifecycle_draft` 경로와 동일).
- workflow_dispatch에도 `compose` 입력 추가(기존 `crud_filter`/`category`/`service`
  옆에). 둘 다 같은 `expand_targets`로 수렴.

### B.4 peak_quota pre-flight 자동 분할 (VPC>5)

`plan()`은 이미 `peak_quota`(quota 종류별 동시 인스턴스 수)를 반환한다
(`composer.py:438-443`). 디스패치 직전 pre-flight:

```
p = plan(targets)
if p["peak_quota"].get("vpc", 0) > 5:    # 계정 cap 5 (dependencies.json vpc_schedule)
    # 타겟을 vpc-cost로 정렬해 2개 이상 배치로 쪼개 순차 디스패치
```
이는 현재 CI의 A∥B 분할과 **동형**이다: `shared_infra --print-filters`가
`dependencies.json`의 `vpc_schedule`(adopt vs vpc-crud)로 pytest `-k`를 갈라 VPC
점유를 cap 5 안에 유지하는 것(`regression/scenarios/shared_infra.py`)을, 조합 경로
에서는 `peak_quota`로 미리 계산해 배치를 나눈다. **같은 cap, 같은 데이터 소스.**

### B.5 디스패치 모드: 현행 Actions vs M4 서버

- **현행(M6 기본)**: GitHub Actions. `compose`가 초안 JSON을 만들고, 기존 regression
  job이 `loader.load_lifecycles()`로 픽업해 pytest 케이스로 실행. 추가 인프라 0.
- **M4 서버 모드(옵션)**: `controlplane/app.py`의 `/runs/trigger`가 서버에서 직접
  디스패치. 같은 `expand_targets`+`compose`+`peak_quota` 분할 로직을 재사용. 차이는
  *실행 장소*뿐 — 컴파일/분할 코드는 공유.

---

## C. M6d — 실행 중 연쇄 가시화

### C.1 plan-manifest 아티팩트 (의도된 체인)

현재 ops.html은 **실제 일어난** res-event(`core/oplog.py:emit_resource` →
S3 `runs/<id>/res/*.json`)만 본다. **의도한** 체인이 없어 "다음에 뭐가 와야 하는지"를
모른다. 해결: 각 조합 라이프사이클의 `plan()` 결과를 직렬화해 **실행 시작 시점**에
`runs/<id>/plan.json`으로 업로드.

- 위치: oplog의 S3 키 규약과 평행 — `runs/{run_id}/plan.json` (run_id 출처:
  `APITEST_RUN_ID`→`GITHUB_RUN_ID`→`local`, `core/oplog.py`와 동일 함수 재사용).
- 내용: `plan()` 반환 그대로 — `order`, `teardown`, `dedup`, `peak_quota`,
  `branches`, `instances`, `credentials`. 직렬화 가능한 dict이므로 추가 가공 0.
- 발행 시점: regression job의 run-start milestone 단계(이미 존재)에서 `emit`.

### C.2 ops.html 오버레이 (의도 vs 실제)

`dashboard/ops.html`은 외부 라이브러리 없이 vanilla JS로 res-event를
`foldResources()`→nested div 트리 + 라이프사이클 간트로 그린다. DEP-MAP은
`gen_dep_map.py`가 찍은 `const DEP = {depth, parent}`를 DEP-MAP 마커 사이에 붙여
부모-자식 중첩에 쓴다.

오버레이 추가(같은 fetch 패턴 재사용):
1. `runs/<id>/res/*.json` 옆에서 `runs/<id>/plan.json`도 `fetch()`.
2. `plan.order`의 각 인스턴스를 **회색 placeholder 노드**로 미리 그림(의도된 체인).
3. 실제 `created` 이벤트가 도착하면 해당 노드를 점등(`.st-alive`), `lifecycle-end`
   status로 `.st-done`/`.st-fail` 전환 — 기존 status class 재사용.
4. **현재 스텝 하이라이트**: `plan.order` 중 아직 `created` 안 된 첫 노드를 "다음"으로
   강조. teardown 단계는 `plan.teardown` 순서로 동일 처리.
5. `plan.dedup`/`plan.branches`는 툴팁으로(이 prereq를 누가 공유하는지, 어떤 one_of
   분기를 탔는지).

순효과: 운영자가 실행 중에 "VPC 만들고→subnet 2개→redis 대기 중, 다음은 verify"를
선·후행과 함께 본다. **새 그래프 엔진 0**, plan.json fetch + 노드 사전 배치만 추가.

---

## D. M6e — 관제 통합

### D.1 결정: `/platform/` 단일 IA — Overview · Plan · Run · Report

현재 `controlplane/templates/base.html`의 nav가 이미 **Overview → Plan → Run →
Report** 4-탭 셸과 공용 디자인 토큰(CSS 변수 `--bg/--surface/--green/--amber/--red`,
시스템 폰트, card/table/tag/badge 컴포넌트)을 가진다. 라우트는 `/planning`,
`/testing`, `/reporting`으로 흩어져 있고 대시보드는 별도 발행 타깃이다. M6e는
**라우트를 `/platform/` 아래 4탭으로 정렬**하고 발행 파이프라인을 하나로 합친다.

| 탭 | 합치는 것 (이동/병합/삭제) | 근거 |
|---|---|---|
| **Overview** | 기존 `/`(runs/schedules/coverage snapshot) 유지, `/platform/` 진입점으로 | `controlplane/app.py` `/` |
| **Plan** | `/planning/*` (scenarios, dependencies, knowledge, resources compose 폼) 그대로 이동 | resource_routes.py |
| **Run** | `/testing` + **ops 라이브 뷰 임베드** (`dashboard/ops.html`을 탭 안으로) | ops.html, oplog |
| **Report** | `/reporting` + **coverage index/drilldown 임베드** (`dashboard/build.py` 산출) | build.py, reporting |

- **이동(move)**: `/planning/*`→`/platform/plan/*`, `/testing`→`/platform/run`,
  `/reporting`→`/platform/report`. 라우트 prefix만 변경, 핸들러 로직 유지.
- **병합(merge)**: ops.html(현재 독립 HTML, S3 직접 fetch)을 Run 탭 iframe/패널로,
  build.py 산출(coverage index + per-service drilldown + untestable gray-out from
  `data/baselines/untestable_services.json`)을 Report 탭 패널로. 둘 다 base.html
  셸 안에서 같은 nav/디자인 토큰을 공유.
- **삭제(delete)**: static_export의 dead-route 처리(`/runs`,`/ai`,`/planning/edit`를
  `href="#"`로)와 중복되는 별도 발행본 진입점들 — 단일 셸로 흡수.

### D.2 발행 race 수정 (확정 위험 → SINGLE pipeline)

**현재 (확정 race).** 두 job이 같은 `dashboard-data` 브랜치에 쓴다:
- conformance job: `git clone --branch dashboard-data || git init` → commit →
  **rebase 재시도 루프 5회**(충돌 인지형, `api-test.yml` conformance publish step).
- dashboard job: `git init` (fresh) → commit → **`git push -f`**(force,
  last-writer-wins, `api-test.yml` dashboard publish step).
- `concurrency` 그룹 없음. dashboard가 conformance 뒤에 force-push하면 conformance
  데이터를 **덮어쓴다**(=드롭). conformance는 spec 미변경 시 skip되므로 고아 데이터도 발생.

**제안 (둘 중 택1, 1안 권장).**

1. **dashboard job도 clone+rebase로 통일** (최소 변경, 권장). dashboard publish
   step을 conformance와 같은 패턴으로: `git clone --branch dashboard-data ||
   git init` → 자기 소유 파일만 `cp`(HTML/history/services는 dashboard 소유,
   conformance.json류는 conformance 소유) → commit → push 실패 시 fetch+rebase
   재시도. 두 job이 **disjoint 파일 집합**을 쓰므로 rebase 충돌 없음. force-push 제거.
2. **전용 publisher job + concurrency 그룹.** regression/conformance/dashboard는
   각자 데이터만 아티팩트로 올리고, 마지막 단일 `publish` job이 모두 모아 한 번만
   push. `concurrency: group: dashboard-data, cancel-in-progress: false`로 직렬화.

> 권장: **1안**. 변경 표면이 작고(`git init`+`-f`→`clone`+rebase 루프, 이미
> conformance에 검증된 코드 복사), 두 발행자가 disjoint 파일을 쓴다는 사실이
> 충돌-프리를 보장. concurrency 그룹은 1안 위에 안전벨트로 추가 가능.

### D.3 단일 발행

static_export.py(212 Pages, TestClient 렌더)와 dashboard/build.py(coverage HTML)는
**같은 `dashboard-data` 브랜치의 다른 하위 경로**로 나간다(static→`platform/`,
dashboard→루트+`services/`). D.2 수정 후 둘은 단일 발행 파이프라인의 disjoint
영역이 되고, base.html 셸을 공유하므로 `/platform/`에서 일관된 nav로 보인다.

---

## E. M6f — 자율 운영 루프

### E.1 루프 형태 (이미 있는 역할 위에 codify)

`agents/orchestrator.md`가 이미 **Assess→Plan→Delegate→Integrate→Record** 사이클과
가드레일(게이트 완화 금지, teardown 스킵 금지)을 정의하고, `ledger.json`이
status 라이프사이클(`todo→claimed→authored→integrated→live-validated`)을 추적한다.
M6f는 이를 **Planner/Coordinator/Executors 3역할**로 명문화한다.

```
Planner   ──(IMPROVEMENT-BACKLOG.md 갱신)──▶ Coordinator ──(delegate)──▶ Executors
   ▲ 입력: 트렌드/리포트                          │ worktree-merge 프로토콜        │ 격리 worktree 작업
   └────────── 사람: 게이트 승인/credential ───────┴───── cp-merge + commit ◀──────┘
```

### E.2 Planner — 주기와 입력/출력

- **주기**: ① 머지 윈도우마다(한 묶음 머지 직후) 리뷰, ② 매일 1회 sweep 리뷰.
- **입력**(전부 기존 산출물):
  - `docs/PRODUCT-FINDINGS.md` — 제품/API 결함 큐레이션 원장.
  - `docs/SERVICE-GAP-REPORTS.md` — 서비스별 커버리지 갭(검증/남음/분류 A·B·C·D).
  - retirement 매트릭스 — `python -m tools.retirement` 출력(RETIRE/NEAR).
  - 커버리지 트렌드 — `dashboard/history.jsonl`(run당 1행: `cov_op/cov_get/cov_c3/
    fail_new/reachable_pct/gap_write/gap_getid/crud_ran`)의 시계열.
  - `data/baselines/untestable_services.json` — 제외 서비스(회색) 목록.
- **출력**: `docs/IMPROVEMENT-BACKLOG.md` 갱신(아래 포맷). 행 추가/상태 전이만.

### E.3 Coordinator — worktree-merge 프로토콜 (이미 사용 중)

저장소에 `.claude/worktrees/agent-*/`가 다수 존재 — Executor가 **격리된 git
worktree**에서 작업하는 현행 메커니즘이다. 프로토콜:
1. Coordinator가 backlog 행을 Executor에 위임(`ledger.json` status `todo→claimed`).
2. Executor가 자기 worktree에서 작업, **3개 게이트로 자기검증**:
   - `python knowledge/formal/validate.py` (R1 지식 게이트)
   - `python regression/scenarios/validate.py` (시나리오 게이트)
   - `pytest tests/offline` (오프라인 단위 — composer 등, 네트워크 없음)
3. Executor가 결과 보고(커버리지/게이트 통과 여부, status `authored`).
4. Coordinator가 cp-merge(worktree 변경을 메인 체크아웃으로 복사-병합) + commit
   (orchestrator.md의 Integrate→Record 단계), status `integrated`.
5. 라이브 2xx 확인 후 `live-validated`.

### E.4 사람 접점 = 게이트/승인/credential **만**

사람이 하는 일은 (a) R1/시나리오 게이트가 막은 변경의 승인, (b) credential 발급
(`plan()`이 반환하는 `credentials` 전제조건, `composer.py:937`), (c) 도메인 지식
주입(어떤 quota/prereq). 그 외 생성·검증·병합·발행은 agent 루프가 닫는다.

### E.5 `docs/IMPROVEMENT-BACKLOG.md` 포맷

Planner가 만들고 갱신하는 단일 backlog. 마크다운 표, 한 행 = 한 개선 항목:

```markdown
# IMPROVEMENT-BACKLOG.md — Planner가 관리하는 자율 개선 큐 (append + 상태전이)

| id | area | problem | proposed-fix | size | status |
|----|------|---------|--------------|------|--------|
| IB-001 | publish | dashboard-data force-push race로 conformance 데이터 드롭 | dashboard publish를 clone+rebase로 통일(§D.2 1안) | M | todo |
| IB-002 | onboarding | 신규 서비스 stub 수작업 | tools/new_service.py 스캐폴더(§A.3) | M | claimed |
| IB-003 | coverage | gslb 합성본 라이브 미증명(8 ops) | 다음 윈도우 라이브 투입 | S | todo |
```

- `id`: `IB-NNN` 단조 증가.
- `area`: `onboarding|compose|visualize|platform|publish|coverage|loop|debt` 등.
- `problem`: 한 줄 현상 + 가능하면 근거 파일.
- `proposed-fix`: 묶을 메커니즘/섹션 참조.
- `size`: `S|M|L` (Executor 1단위 기준).
- `status`: ledger와 동기 — `todo|claimed|authored|integrated|live-validated|wontfix`.

---

## F. 실행 티켓 (S/M/L · 의존 순서 · files-touched · acceptance gate)

acceptance gate 약어: **R1**=`python knowledge/formal/validate.py`,
**SC**=`python regression/scenarios/validate.py`, **OFF**=`pytest tests/offline`.

### 첫 병렬 배치 (충돌 없는 파일 — 동시 착수 가능)

서로 다른 파일을 건드리므로 worktree 병렬 실행 안전:

| # | 티켓 | size | files-touched | acceptance |
|---|------|------|---------------|------------|
| T1 | **tools/new_service.py** 스캐폴더 (M6a) | M | `tools/new_service.py`(신규), 읽기: `data/api_catalog.json`,`data/api_docs.json` | OFF(새 단위테스트), R1(생성 stub가 통과) |
| T2 | **expand_targets + target= 문법** (M6b/c) | M | `regression/scenarios/targets.py`(신규) | OFF(`tests/offline/test_targets.py`) |
| T4 | **plan-manifest emit** (M6d) | S | `core/oplog.py`(`emit_plan`/`runs/<id>/plan.json`), regression job run-start step | OFF |
| T6 | **Planner cadence + IMPROVEMENT-BACKLOG.md scaffold** (M6f) | S | `docs/IMPROVEMENT-BACKLOG.md`(신규), `agents/orchestrator.md`(역할 명문화) | (문서) |

### 후속 (배치1 의존)

| # | 티켓 | size | dep | files-touched | acceptance |
|---|------|------|-----|---------------|------------|
| T3 | **compose_service/group/theme** | S | T2 | `regression/scenarios/composer.py`(또는 `targets.py`에 헬퍼) | OFF |
| T3b | **run-request `compose=` + dispatch 입력** | M | T2,T3 | `.github/workflows/api-test.yml`(spec job parse + 새 step), `controlplane/app.py`(`/runs/trigger`) | (CI 드라이런) |
| T3c | **peak_quota pre-flight 자동분할** | M | T2 | `regression/scenarios/targets.py` 또는 디스패처 | OFF(>5 vpc 분할 테스트) |
| T5 | **ops.html 의도 vs 실제 오버레이** | M | T4 | `dashboard/ops.html` | (수동 — plan.json fetch 렌더 확인) |
| T7 | **발행 race 수정** (M6e §D.2 1안) | M | — | `.github/workflows/api-test.yml`(dashboard publish step) | (CI: 동시 push 충돌-프리 확인) |
| T8 | **`/platform/` IA 통합** | L | T7 | `controlplane/app.py`(route prefix), `controlplane/templates/*.html`, `controlplane/static_export.py`(PAGES/링크 재작성), ops.html/build.py 임베드 | static_export 212p 렌더 통과, OFF |

> 의존 순서 요지: T2가 T3/T3b/T3c의 선행. T4가 T5의 선행. T7(race)는 독립이며
> T8(IA 통합) 선행(발행이 깨지지 않은 상태에서 IA 이동). T7은 단일 파일이라 배치1과도
> 병렬 가능하나, dashboard publish step 한 곳에 집중되므로 별도 배치로 둠.

---

## G. 개선 backlog seed (지금 보이는 위험/부채)

§E.5 포맷으로 즉시 채울 시드. (Planner 첫 실행 입력.)

| id | area | problem | proposed-fix | size |
|----|------|---------|--------------|------|
| IB-001 | publish | **dashboard-data force-push race**(확정): dashboard `git push -f`가 conformance rebase-push를 덮어 드롭. `concurrency` 그룹 없음 | dashboard publish를 clone+rebase로 통일(§D.2 1안), disjoint 파일 보장 + concurrency 안전벨트 | M |
| IB-002 | debt | **태그 네임스페이스 부재** — 동시 실행 시 sweep(`cleanup.reconciler`)이 다른 실행의 자원을 교차 삭제할 위험. 현재 run 태그 스코프뿐 | run_id별 owner+run 태그를 reconciler 필터에 강제, 동시 실행은 태그 prefix로 격리 | M |
| IB-003 | debt | **monitor 재무장 toil** — 실행마다 모니터/스케줄 수동 재설정 | 스케줄 재무장을 controlplane `/schedules` 토글 자동화 또는 CI 후크로 | S |
| IB-004 | debt | **96건 "create without delete" 경고**(lookup 노드) — R1 노이즈 | lookup은 `capture_soft:true` 명시로 분류하거나, validator에 lookup 분류 후 경고 억제(R1 규칙 보강) | S |
| IB-005 | visualize | **gen_dep_map.py 수동 붙여넣기** — DEP-MAP 마커 사이에 손으로 붙임, drift 위험 | 발행 시 gen_dep_map.py 출력을 ops.html에 자동 주입(빌드 step) | S |
| IB-006 | coverage | **restore/upgrade 체인 게이트** — 위험/과금 체인 비활성 | owner 승인 게이트 + heavy/destructive 분리 배치로 단계 활성화 | M |
| IB-007 | debt | **second-account backlog** — `docs/SECOND-ACCOUNT-BACKLOG.md`의 미결 항목(VPC cap, 격리 계정) | 별도 계정 credential 발급 후 peak_quota 분할을 계정 차원으로 확장 | L |
| IB-008 | coverage | SERVICE-GAP-REPORTS의 C 분류(gslb/vpn/cdn/direct-connect 라이브 미증명) | 분류별 다음 윈도우 라이브 투입(placeholder 가능분 우선) | M |

---

## 부록 — 핵심 파일/함수 색인

- 조합: `regression/scenarios/composer.py` — `load_model`(:134), `plan`(:273,
  반환 `order/teardown/dedup/peak_quota/branches/credentials/instances`),
  `compose`(:719), `_validate_composed`(:947), capture_soft 가드(:737).
- 셀렉터(신규): `regression/scenarios/targets.py` — `expand_targets`.
- 신규 서비스(신규): `tools/new_service.py` (모델: `tools/retirement.py`).
- 지식: `knowledge/formal/resources/<cat>__<svc>.yaml`, `_groups.yaml`,
  `knowledge/formal/validate.py`(R1), `knowledge/formal/INGESTION.md`(docs→VALIDATED).
- CI: `.github/workflows/api-test.yml`(spec/regression A/regression-vpc-crud B/sweep/
  conformance/dashboard), `validate.yml`(R1 PR 게이트),
  `regression/scenarios/shared_infra.py`(`--print-filters`, VPC cap 5),
  `regression/scenarios/dependencies.json`(`vpc_schedule`).
- 가시화/oplog: `core/oplog.py`(`emit_resource`, run_id, S3 `runs/<id>/res/*.json`,
  `/api/ingest/events` 미러), `dashboard/ops.html`, `dashboard/gen_dep_map.py`,
  plan-manifest `runs/<id>/plan.json`(신규).
- 관제: `controlplane/app.py`(라우트), `controlplane/resource_routes.py`(compose 폼,
  Pages 읽기전용), `controlplane/static_export.py`(212 Pages, TestClient),
  `controlplane/templates/base.html`(Overview/Plan/Run/Report 셸), `dashboard/build.py`
  (coverage index/drilldown, `untestable_services.json` 회색), `dashboard/history.jsonl`(트렌드).
- 자율 루프: `agents/orchestrator.md`, `agents/coordination/ledger.json`(status 전이),
  `tools/retirement.py`(RETIRE/NEAR 매트릭스), `docs/PRODUCT-FINDINGS.md`,
  `docs/SERVICE-GAP-REPORTS.md`, `docs/IMPROVEMENT-BACKLOG.md`(신규).
