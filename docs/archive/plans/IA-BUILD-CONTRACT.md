---
status: superseded (이행 완료된 빌드 계약 — 확정 IA 빌드 완료)
for: the IA-build agents (Catalog · Modeling · Reporting) + integration owner
superseded_by: PLATFORM-IA-DIRECTION.md
---

> **⚠️ SUPERSEDED (2026-07-04).** 이 병렬 빌드 계약은 **이행 완료**됐다 — 확정 IA
> (`Catalog · Modeling · Testing · Reporting`)는 controlplane에 구현돼 있다. IA
> 정본은 [`PLATFORM-IA-DIRECTION.md`](PLATFORM-IA-DIRECTION.md) §"✅ 확정 IA
> (2026-06-26)". 아래 규칙 중 지금도 살아있는 원칙(같은 `graph_view` + 렌더러
> 하나 + `overlay()`만 교체)은 그 문서와 코드(`controlplane/static/resource_graph.js`)가
> 담보한다. 아래는 빌드 당시 계약의 역사 기록.

# IA build contract — "그림 하나, 여러 얼굴" parallel build

Confirmed IA: **`Catalog · Modeling · Testing · Reporting`** (면① 작업 콘솔) +
면② 공개 대시보드(별도). This contract lets Catalog/Modeling/Reporting be built
**in parallel** without diverging the shared graph or colliding on shared files.

**Testing(③) is DONE** (reference): `console_api.py` + console2 mounted at
`/testing/console`. Study it as the live example of the overlay contract below.

---

## 1. THE one rule — same graph, different `overlay()`

Every face renders the **same** `composer.graph_view(targets)` data through the
**same** renderer `controlplane/static/resource_graph.js`. A face differs ONLY by
its per-node coloring hook:

```js
ResourceGraph.scene(svgEl, stageEl, data, { overlay: fn, onFocus: fn, ... })
//   data    = composer.graph_view output (frozen shape, below)
//   overlay(node_id) -> { fill, stroke, badge, pulse } | null   // ← the "얼굴"
```

`graph_view` shape (frozen — do NOT change): `{ nodes:[{id, service, provenance,
quota, heavy, options, level, is_target, shared, is_dependent}], edges:[{from,to}],
levels, shared, peak_quota, order, teardown }`.

Each face implements `overlay(id)`:
| 얼굴 | overlay 의미 | 데이터 출처 |
|---|---|---|
| **Modeling** | provenance/완성도 (VALIDATED=초록 · docs=주황 · 미정의/불완전=회색+badge) | `resource_model.load_model()` 의 `provenance`, requires 완성도 |
| **Testing** | 라이브 run-state (running=pulse · pass=초록 · fail=빨강) — *이미 구현됨* | `/api/runs/<id>/events` |
| **Reporting** | 커버리지 (tested=초록 · modeled=주황 · untested=회색) | `reports/results/*.jsonl` (관측), service→resource 집계 |

→ **새 렌더러를 만들지 마라.** `resource_graph.js`(scene)를 재사용하고 `overlay()`만 써라.

## 2. File ownership (disjoint — 충돌 없음)

| Agent | 소유(만들거나 고침) | 절대 건드리지 말 것 |
|---|---|---|
| **Catalog** | `controlplane/catalog_routes.py` (신규 APIRouter, prefix `/catalog`) · `templates/catalog*.html` | app.py · base.html · resource_graph.js |
| **Modeling** | `controlplane/resource_routes.py` (이미 노드편집 폼 보유 — 여기에 "모델 지도" 그래프 뷰 추가) · `templates/resource_*.html` + 신규 model-map 템플릿 | app.py · base.html · resource_graph.js |
| **Reporting** | `controlplane/reporting_routes.py` (신규 APIRouter, prefix `/reporting`*) · `templates/reporting*.html` | app.py · base.html · resource_graph.js |

\* 기존 app.py에 `/reporting` 인라인 라우트가 있으면 **건드리지 말고** 새 라우터는
`/reporting/coverage` 같은 하위 prefix로 — 통합 단계에서 정리.

**공유/금지 파일은 통합 소유자(lead)만 편집**: `app.py`(라우터 include + nav),
`templates/base.html`(nav), `static/resource_graph.js`(렌더러). 에이전트는 자기
라우터를 **만들기만** 하고, lead가 `include_router` + nav 슬롯을 한 번에 배선한다.

## 3. Shared endpoints (재사용, 새로 만들지 말 것)

- `GET /api/model` — 전체 리소스 모델 (console_api, 소유=Testing/lead). Catalog/
  Modeling 의 데이터 원천.
- `POST /api/graph {selection}` — `composer.graph_view` DAG (console_api). 임의
  선택의 합성 DAG. 또는 서버측에서 `composer.graph_view(targets)` 직접 호출 가능.
- `resource_routes` 에 이미 `graph.json`/`graph.js` 있음 — Modeling 은 그걸 재사용.

각 메뉴가 자기 데이터가 필요하면 **자기 prefix 아래** 엔드포인트를 추가하라
(`/catalog/list.json` 등). 계약은 *graph_view 형태 + overlay 훅*이지 단일
엔드포인트가 아니다.

## 4. Per-menu spec

### Catalog (① 재료, READ-ONLY)
- API 인벤토리 1,372: `data/api_catalog.json` 로드 → 검색 + 카테고리▸서비스 그룹 표.
- 각 행 = `METHOD path` (+ validated 뱃지는 `spec.summary`/results 있으면 optional).
- **그래프 없음**(그래프 *이전* 목록). 단 각 리소스/서비스 행에 **`✏️ 레시피 편집 →`**
  링크 = Modeling 의 그 노드 에디터(`/planning/resources/{node_id}`)로 deep-link.
- 순수 RO — 저장/편집 엔드포인트 만들지 말 것.

### Modeling (② 레시피 저작)
- **이미 있는 것 재사용**: `/planning/resources`(목록) + `/planning/resources/{id}`
  (노드 편집 폼: requires·create.endpoint·body·options·capture·verify·delete →
  `resource_model.save_node` → git). 이게 "레시피 저작" 본체다.
- **추가할 것**: "모델 지도" 그래프 뷰 — `composer.graph_view` 전체를 `resource_graph.js`
  scene 으로 렌더, `overlay(id)` = provenance/완성도 색칠, **노드 클릭 → 그 노드의 편집
  폼 열기**(기존 form 라우트로). = "그래프 위에서 레시피 편집".
- onFocus/click 으로 폼을 모달 or 사이드패널로.

### Reporting (④ 평가, READ-ONLY 집계)
- **커버리지 색칠 지도**: `composer.graph_view` + `resource_graph.js`, `overlay(id)`
  = 커버리지 상태(`reports/results/*.jsonl` 관측 집계, service→resource 단위 —
  "API 하나하나" 아님). tested/modeled/untested.
- **2축**: regression(됨?) + conformance(잘 설계됨?) — `core.results` 의 Observation/
  Finding. 기존 `dashdata`/`controlplane.compare`/`reporting` 자산 재사용.
- 과거 run 목록 + 추세(있으면). 면② 공개본으로 링크아웃(별도, 만들지 말 것).
- RO 집계 — 실행/변경 엔드포인트 없음.

## 5. Verify (각 에이전트, 끝내기 전 필수)

in-process TestClient 로 자기 라우터 200 확인 (Testing 가 한 그대로):
```python
from fastapi.testclient import TestClient
# 자기 라우터를 임시 app 에 include 해서 GET 200 + 핵심 needle 확인
```
끝나면 자기 라우터 파일 + 템플릿만 커밋(브랜치 `claude/ecstatic-tesla-fo1g3b`),
app.py/base.html 은 **건드리지 말 것**(머지 충돌). 결과를 lead 에게 한 줄 보고:
{라우터 파일, prefix, 추가 엔드포인트, nav active 키, 검증 통과 여부}.

## 6. Integration (lead, 병렬 종료 후 한 번)
세 라우터 `include_router` + base.html nav 를 `Catalog → Modeling → Testing →
Reporting` 으로 한 커밋에 배선 + Catalog→Modeling ✏️ 링크 + 4얼굴 인프로세스 검증.
