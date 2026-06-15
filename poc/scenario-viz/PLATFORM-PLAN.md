# 실제 플랫폼 반영 계획 — 합성 시나리오 그래프 UI

> PoC(`poc/scenario-viz/`)에서 검증한 5개 아이디어를 controlplane에 반영하는 단계 계획.
> 핵심: **새로 만들기보다 이미 있는 것을 재사용**한다. 편집/생성·합성·타이밍 데이터는
> 대부분 플랫폼에 이미 있고, 빠진 것은 **그래프 시각화 레이어**와 **레벨 병렬 실행**뿐이다.

## 설계 원칙 (3)

1. **composer.py = 단일 진실 소스.** UI는 `composer.plan()/compose()`가 만든 데이터를
   *그리기만* 한다. PoC의 `viz.js`(closure/topo를 JS로 재구현)는 대역일 뿐 —
   플랫폼에선 로직을 JS로 복제하지 않는다.
2. **그래프 컴포넌트 1개로 통일.** 지금 산발적인 3개(`controlplane/templates/dependencies.html`
   손-SVG, `dashboard/ops.html` 트리, `dashboard/gen_dep_map.py`)를 하나로 수렴한다.
3. **4개 화면 = 같은 그래프 + 오버레이만 교체.** model(provenance) / run-status(oplog) /
   result(observations) 레이어만 갈아끼운다. (PoC `06-ia-walkthrough.html`가 증명)

## 이미 있는 것(재사용) vs 새로 만들 것

| 요구 | 이미 있는 것 | 새로 만들 것 |
|---|---|---|
| 1 서비스별 의존/옵션 보기 | `resource_routes.resource_list/_grouped`, `resource_form.html` | 초점 **의존 그래프**(upstream+dependents) |
| 2 수정/생성 | `POST /{node_id}/save → authoring.propose_edit`(validator+git), `_groups`/form | 그래프 즉시 미리보기, `dependents()` |
| 3 선택→실행 / 조합→공통의존 | `resource_compose.html`, `compose_run`(plan/draft), `crud_filter` 실행 | 표→**그래프**, 원클릭 "바로 실행" |
| 4 Run 단계별/병렬 | `ops.html`+oplog(라이브 자원 트리), milestone, M2 command | 그래프 상태 오버레이; **레벨 병렬 실행(엔진)** |
| 5 Report 수행시간 | `observations.jsonl` **elapsed_ms**, oplog 타임스탬프, `dashboard/build.py` | 단계별 시간표 + 결과 색칠 그래프 |

## 단계별 계획

### Phase 0 — 그래프 기반공사 (선행, 1 스프린트)
- **신규 엔드포인트** `GET /planning/resources/graph.json?targets=&choices=&focus=`
  → `composer.load_model()`+`composer.plan()`을 호출해
  `{nodes:[{id,service,provenance,quota,heavy,opts,level}], edges:[{from,to,kind}],
  shared(dedup), peak_quota, levels}` 반환. `dependents()` 헬퍼 추가(composer 또는 ai_pipelines).
- **신규 공용 렌더러** `controlplane/static/graph.js` — `graph.json` + overlay를 받아 그린다.
  렌더 스택 결정 필요(아래).
- 산출물: 한 컴포넌트로 nodeSet+레이어를 그리는 계약 확정.

### Phase 1 — Catalog (req 1·2) — 기존 자원 화면 확장
- `resource_list.html`: 카테고리→서비스 드릴다운(`_grouped` 재사용) + 검색.
- `resource_form.html`: 노드 상세에 **초점 그래프**(이 노드의 upstream closure + 직접 dependents,
  좌=의존/우=피의존). `graph.json?focus=<node>`.
- 편집/생성은 **이미 동작**(`/{node_id}/save`, validator-gated, git commit) — 그래프 라이브
  미리보기와 "+ 새 노드"만 붙인다.
- 버튼: "이 서비스로 바로 실행", "Plan에 추가".

### Phase 2 — Plan (req 3) — `resource_compose.html`에 그래프
- 기존 plan-rows 표 옆/대신 **그래프**(closure 하이라이트, dedup gold, 🔀 분기 = 기존 `choices`
  select와 양방향 연동).
- 단독 1개 → **원클릭 실행**: `compose`→draft 저장→`/runs/trigger crud_filter=gen-<node>`를
  한 버튼으로(지금은 저장 후 별도 폼). 조합은 공통 의존 dedup된 bundle로.

### Phase 3 — Run (req 4) — 두 파트로 분리
- **(3a) 시각화 [작음, 먼저]**: `GET /runs/{id}/graph` — 진행 중 run의 plan 그래프에
  oplog 이벤트로 상태 색칠(`ops.html`과 같은 데이터원). 세로 띠 = composer depth(level) →
  "레벨 병렬 잠재력"을 가시화.
- **(3b) 실제 병렬 실행 [큼, 별도 트랙]**: `composer`가 **barrier-group(level) 메타**를 emit →
  엔진에 **레벨 병렬 executor**(레벨 내 동시, 레벨 간 배리어). 현재 xdist A∥B·shared-VPC adopt
  위에 점진 도입. **quota/budget 동시성 안전**(`core/budgets` 예약)과 teardown 역배리어가 관건.
  → 위험·비용이 크므로 **시각화(3a) 먼저, 실행(3b)은 PoC로 따로 검증 후**.

### Phase 4 — Report (req 5) — dashboard/reporting 확장
- `observations.jsonl.elapsed_ms` + oplog 타임스탬프로 **단계별 수행시간 표** + 그래프
  **결과 색칠**(pass/fail/docs→VALIDATED 승격) + **wall-clock(병렬) vs 순차** + 임계(단계별 최장).
- `dashboard/build.py`에 섹션 추가 → `/platform/` static export에도 반영(데이터가 이미 있어 가장 쉬움).

### 부채 상환(병행)
- `dependencies.html`·`ops.html`·`gen_dep_map.py`를 `graph.js`+`graph.json`으로 수렴.

## 결정 필요 (2)

1. **렌더 스택** — (a) Cytoscape.js(클라이언트, dagre 레이아웃·줌/팬·인터랙션) vs
   (b) 서버측 dagre→SVG(정적 export 호환·무JS). **추천: 둘 다 같은 `graph.json`을 먹되,
   인터랙티브 화면=Cytoscape, `/platform/` 정적 export=서버 SVG.**
2. **Run 실제 병렬(3b)을 이번 범위에 포함?** — 추천: **아니오. 3a 시각화까지만.**
   3b는 엔진 변경이라 별도 분기에서 PoC.

## 결정 (owner, 2026-06-15)
- **렌더 스택 = 둘 다.** 인터랙티브 화면 Cytoscape.js, `/platform/` 정적 export 서버 SVG,
  같은 `graph.json` 공유. (P0는 우선 무-CDN SVG 렌더러로 착륙 — Cytoscape 업그레이드는 후속)
- **Run 병렬 = 3a 시각화만.** 실제 레벨 병렬 executor(3b)는 별도 트랙(이번 범위 밖).
- **다음 = P0 기반공사.**

## P0 상태 — 구현됨 (2026-06-15)
- `regression/scenarios/composer.py`: `graph_view()` · `focus_view()` · `dependents()`
  (순수 함수, 네트워크 없음; `__all__` 노출).
- `controlplane/resource_routes.py`: `GET /planning/resources/graph.json`
  (`?focus=` / `?targets=&choices=`), `/graph.js`(파일 서빙, 정적 마운트 불필요),
  `/graph`(데모 페이지) — 모두 `/{node_id}` 앞에 선언.
- `controlplane/static/graph.js`: 공용 SVG 렌더러(레벨 띠·dedup·focus 색칠, 무빌드/오프라인).
- `controlplane/templates/resource_graph.html`: 데모(`/planning/resources/graph`),
  `resource_list.html`에 진입 링크.
- `tests/offline/test_composer.py`: graph_view/focus_view/dependents 회귀 5건.
- 검증: 실제 270노드 모델 + 픽스처에서 동작 확인(이 환경엔 fastapi/pytest 미설치라 라이브
  구동 대신 함수·문법·어서션 검증). 라이브 확인은 control plane 기동 환경에서 `/planning/resources/graph`.

## P1 상태 — 읽기=정적 / 쓰기=FastAPI 로 분리 (2026-06-15)
owner 통찰 반영: **정의/수정만 FastAPI, 보기(Catalog)·Report는 정적**으로 충분.
- **읽기(정적)** `controlplane/graph_export.py` — `python -m controlplane.graph_export <out>`:
  composer.load_model + **노드별 `focus_view` 미리계산** → `catalog.js`(270 focus, 0 err) +
  `graph.js` + 자기완결 `catalog.html`(카테고리→서비스 드릴다운 + focus 그래프 + 읽기전용 상세).
  서버 없이 GitHub Pages에서 열람. Report는 기존 dashboard가 이미 정적이라 그대로 사용.
- **쓰기(FastAPI)** 기존 `resource_form.html`(+`/{node_id}/save` authoring 파이프라인)에
  **focus 그래프**(`/graph.json?focus=`)만 얹음 — 편집하며 의존을 본다.
- 정적 Catalog는 dashboard-data `/catalog/`로 배포(Pages). CI 통합은 1줄
  (`python -m controlplane.graph_export <pub>/catalog`)로 후속.

## P2 상태 — 정적 Plan 미리보기 + 라이브 그래프 + CI 자동화 (2026-06-15)
- **읽기(정적)** `graph_export.py`가 `plan.html`도 생성 — 여러 타깃 선택 → 클라이언트가
  모델 의존으로 **합집합 폐포 + dedup + level + peak quota** 미리보기(graph.js 렌더).
  "control plane에서 실행 →" 버튼이 `/planning/resources/compose?targets=…`로 연결. catalog↔plan 네비.
- **쓰기(FastAPI)** `resource_compose.html`에 **합성 폐포 그래프** 추가(`/graph.json?targets=&choices=`)
  — 기존 plan 표/실행 폼은 그대로, 그래프만 얹음.
- **CI 자동화**: 워크플로 dashboard publish 스텝에
  `python -m controlplane.graph_export "$dd/catalog"` 추가(composer-pure, 실패해도 publish 무영향)
  → 매 런마다 `/catalog/`(catalog.html + plan.html) 자동 갱신.

## 권장 순서 / 규모
`P0(기반) → P1(Catalog) → P2(Plan) → P4(Report, 데이터 보유로 쉬움) → P3a(Run viz)`
각 ~1 스프린트. **P3b(엔진 병렬)만 별도 트랙.** 전 과정 C4 안전(draft-only, 자동 enable 금지) 유지.
