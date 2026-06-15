# 후속 할 일 (FOLLOWUP) — 자원 그래프 UI

> 진행방향은 [`PLATFORM-PLAN.md`](PLATFORM-PLAN.md), 아이디어 검토는
> [`DIRECTION.md`](DIRECTION.md). 이 문서는 **main 반영 후 남은 검증·작업**만 모은 체크리스트.
> 착륙 완료: P0(graph 계약) · P1(정적 Catalog + 편집폼 그래프) · P2(정적 Plan + compose 그래프 + CI publish)
> · P4(Report 수행시간) · P3a(정적 Run 구조). 대시보드↔카탈로그 링크.

## A. 당장 — GitHub Actions 운영 중 확인 (서버 불필요)
- [ ] 대시보드 Pages 상단 "자원 모델: 카탈로그·Plan·Run·Report" 링크 노출
      (https://kyle-agent.github.io/api-test-automation/ → /catalog/)
- [ ] **실제 런(run-request/dispatch)** 후 publish 스텝이 `/catalog/`를 재생성하는지
      (일반 push는 validate.yml만 → Pages 안 바뀜)
- [ ] **CRUD 런(`SCP_RUN_CRUD=true`)** 후 `report.html`의 노드별 수행시간이
      "untested" → 실측 elapsed_ms로 채워지는지 (create=POST는 CRUD에서만 측정됨)
- [ ] 워크플로의 main 트리거 규칙(`0ff42c7`)과 catalog export 라인이 함께 정상 동작

## B. 설치본(self-host) 단계 — control plane 라이브 + 오프라인 테스트
- [ ] `pip install pytest pyyaml && pytest tests/offline/test_composer.py tests/offline/test_graph_export.py -q`
      (9건: graph_view/focus_view/dependents + build_report)
- [ ] `uvicorn controlplane.app:app --port 8800` 후:
  - [ ] `/planning/resources/graph` 데모 렌더
  - [ ] `/planning/resources/graph.json?focus=vpc` JSON 응답
  - [ ] `/planning/resources/<id>` 편집폼에 focus 의존 그래프 + 클릭 이동
  - [ ] `/planning/resources/compose` 대상 선택 시 합성 폐포 그래프
  - [ ] 노드 편집→저장이 authoring(검증→git commit) 경로로 반영

## C. 남은 구현 (별도 트랙)
- [ ] **3a-live**: `GET /runs/{id}/graph` — 진행 중 run의 plan 그래프에 oplog 이벤트로
      상태 색칠(생성중/완료/실패). 데이터원은 `ops.html`과 동일(oplog 버킷).
      정적 `run.html`은 구조/추정까지만 함.
- [ ] **P3b**: 엔진 레벨 병렬 executor — composer가 barrier-group(level) 메타 emit +
      레벨 내 동시·레벨 간 배리어 실행. **quota/budget 동시성 예약**(`core/budgets`)과
      teardown 역배리어가 관건. xdist A∥B·shared-VPC adopt 위에 점진 도입. (req4 "실제 병렬")
- [ ] **렌더러 업그레이드(옵션)**: 인터랙티브 화면 Cytoscape.js(줌/팬/dagre 레이아웃),
      정적 export 서버 dagre→SVG. 둘 다 같은 `graph.json` 소비. 현재는 무빌드 SVG(`graph.js`).
- [ ] **모델 갭**: one_of 1건/count 3건뿐 → 분기·다중성 의존 확충(④ 화면 가치↑) 또는
      검증율 0% 카테고리(ai-ml/data-analytics/financial/devops) 합성 런으로 docs→VALIDATED 승격.
- [ ] **통합/부채상환**: `dependencies.html`·`ops.html`·`gen_dep_map.py`를
      `graph.js`+`graph.json`으로 수렴(그래프 렌더러 1개로 통일).

## 참고 — 핵심 파일
- 단일 진실: `regression/scenarios/composer.py` (`plan/graph_view/focus_view/dependents`)
- 라이브 라우트/렌더러: `controlplane/resource_routes.py`, `controlplane/static/graph.js`,
  `controlplane/templates/{resource_graph,resource_form,resource_compose}.html`
- 정적 export: `controlplane/graph_export.py` (`catalog/plan/run/report.html` + CI publish 스텝)
- 탐색/목업: `poc/scenario-viz/` (6개 아이디어 + 통합 워크스루)
