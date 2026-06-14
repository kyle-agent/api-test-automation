# 합성 시나리오 시각화 — 방향성 검토 노트

> PoC 디렉토리 `poc/scenario-viz/`. 다른 세션 작업과 격리하기 위한 별도 영역이며,
> 엔진/컨트롤플레인 코드는 건드리지 않는다(읽기 전용 추출 + 정적 HTML만).

## 무엇을 보여주는가
`knowledge/formal/resources/*.yaml`(270 노드, 91 VALIDATED / 179 docs)를
`build_data.py`로 `data/model.js`에 추출하고, "단위 서비스의 의존성 합성"을
5가지 UI로 그린다. 모든 페이지는 외부 의존성 없이 `file://`로 열린다.

| # | 파일 | 아이디어 | 핵심 |
|---|------|---------|------|
| ① | `01-compose-canvas.html` | 인터랙티브 합성 캔버스 | 대상 선택→폐포 그래프+plan+dedup+quota (플래그십) |
| ② | `02-layered-dag.html` | 레이어드 DAG+스윔레인 | 위상깊이=생성순서, 밴드=카테고리, teardown 오버레이 |
| ③ | `03-closure-overlap.html` | 폐포 오버랩(UpSet) | 공유 인프라 dedup 절감 시각화 |
| ④ | `04-branch-toggle.html` | OR-의존 분기 토글 | one_of/count의 합성 비용 비교 |
| ⑤ | `05-sequence-gantt.html` | 시퀀스/실행 간트 | async ready 대기·heavy 소요 타임라인 |
| 참고 | `references.html` | 레퍼런스 갤러리 | Terraform/CFN Composer/Airflow/Dagster/n8n + 라이브러리 + 외부 링크 |

## 검토할 결정들 (owner 입력 대기)

1. **어디에 붙일까** — 현재 컨트롤플레인엔 이미 ① 손-SVG 의존 그래프
   (`controlplane/templates/dependencies.html`) ② 합성 폼(`resource_compose.html`)이
   있다. 이 PoC의 ①/②를 그 두 화면에 흡수시킬지, 새 `/planning/graph` 탭을 만들지.
2. **렌더 스택** — 무빌드 철학 유지 시 **Cytoscape.js(클라이언트) 또는 dagre 서버측
   좌표→SVG(정적 export 호환)** 가 후보. 지금 PoC는 손-레이아웃이라 노드 많아지면
   한계. (references ③ 참고)
3. **실행 상태 오버레이** — Airflow/Dagster처럼 최근 런의 pass/fail·검증시각을
   그래프에 색칠할지(= ops 뷰 데이터와 결합).
4. **모델 갭 우선순위** — one_of 1건/count 3건뿐이라 ④의 가치가 아직 작다.
   분기·다중성 의존을 더 모델링할지, 아니면 docs→VALIDATED 승격(검증율 34%)을
   먼저 칠지.

## 다음 단계 후보
- [ ] owner가 5개 중 1~2개를 "본 채택" 선정 → 컨트롤플레인 라우트로 승격
- [ ] Cytoscape.js PoC로 ①을 자동 레이아웃/줌·팬 버전 재구현
- [ ] `dashboard.build`/ops와 결합해 실행 상태 색칠
- [ ] 검증율 0% 카테고리(ai-ml/data-analytics/financial/devops) 합성 런으로 승격

## 재생성
```bash
python3 poc/scenario-viz/build_data.py   # 모델 변경 후 data/model.js 갱신
# 열기: 브라우저로 poc/scenario-viz/index.html
```
