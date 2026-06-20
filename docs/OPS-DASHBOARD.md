---
status: active
for: human-ops
---

# Ops dashboard — 영구 oplog 버킷(apitest-oplog-permanent) + 정적 뷰어

> Status: **active** (2026-06-11 도입, 오너 제안). 워크플로 전체 진행현황과
> 과거 런 이력을 GitHub과 독립적으로 보는 운영 대시보드.

## 구조

```
[CI 각 잡] --core/oplog.py--> s3://apitest-oplog-permanent (영구, 절대 미삭제)
                                ├ runs/<run_id>/run.json            런 메타
                                ├ runs/<run_id>/events/<ms>-<단계>.json  진행 이벤트
                                ├ runs/<run_id>/summary.json        종료 요약
                                └ index.json                        전체 런 이력(최신순 ≤200)
[dashboard/ops.html (Pages)] --브라우저에서 직접 GET/LIST--> 버킷
```

- **쓰기**: spec(run-start) → A(smoke/adopt-crud) → B(vpc-crud) → sweep →
  dashboard(finalize: summary + index.json). 이벤트 키가 런·ms·단계별로
  고유해서 A∥B 병렬 잡 간 경합 없음. index.json은 dashboard 잡(런당 단일
  작성자)만 read-modify-write.
- **읽기**: `ops.html`이 30초 폴링 — index.json(이력 표) + ListObjectsV2로
  진행 중 런의 이벤트 타임라인.
- **안전**: oplog는 전부 best-effort (`continue-on-error` + 내부 no-op) —
  버킷/자격/엔드포인트 문제가 테스트 런을 절대 실패시키지 않음. reconciler는
  이 버킷을 모름(이름이 regr*가 아니므로 어떤 매처에도 안 걸림).

## 인증/엔드포인트

- 키: Open API와 **동일한** access/secret (오너 확인 2026-06-11). SDK region은
  kr-west1 → `kr-west` (userguide Amazon S3 활용 가이드).
- 엔드포인트 기본값은 per-service-host 추정
  (`https://objectstorage.<region>.<env>.samsungsdscloud.com`). **첫 런 로그에서
  `[oplog]` 라인을 확인**하고, 틀리면 콘솔 Object Storage 상세의 Public URL을
  repo variable `SCP_OPLOG_S3_ENDPOINT`로 설정.
- 뷰어가 브라우저에서 읽으려면 버킷 public-read + CORS 필요 — `ensure`가
  best-effort로 적용 (`put-bucket-acl public-read`, `put-bucket-cors`). 거부되면
  뷰어 상단에서 엔드포인트(프록시/presigned) 교체 가능.

## 사용

- 뷰어: Pages의 `/ops.html` (dashboard-data 브랜치에 같이 게시). 상단 입력에
  버킷 엔드포인트 저장(localStorage).
- 수동: `python -m core.oplog ensure|emit|finalize` (env: SCP_OPLOG_BUCKET,
  SCP_OPLOG_S3_ENDPOINT, 키는 SCP_* 폴백).

## DEP 맵 (kind→parent 의존성) — 빌드 시 자동 생성

`ops.html`의 리소스 트리는 `const DEP={...}`(kind별 parent + topological depth)를
써서 자식 리소스를 가장 가까운 의존성 아래에 중첩한다. 이 맵은 이제 **대시보드
빌드 시점에 자동 생성**된다 (수동 복붙 폐기, IA.md WS3):

- `dashboard/ops.html`은 **소스 템플릿**(`DEP-MAP` 마커 + last-known 플레이스홀더 맵).
- `python -m dashboard.build`가 리소스 모델(`knowledge/formal/resources`,
  `regression.scenarios.composer.load_model`)에서 맵을 계산해
  (`dashboard.gen_dep_map.dep_map_js`) **마커 사이에 주입**하고 결과를
  `reports/dashboard/ops.html`로 내보낸다. CI publish는 이 **빌드된 사본**을
  게시한다(`reports/dashboard/ops.html` → dashboard-data).
- 따라서 리소스 모델이 바뀌면 다음 빌드에서 맵이 자동 갱신된다 — 더 이상
  `gen_dep_map.py`를 돌려 손으로 붙여넣을 필요가 없다. 모델 파싱은 관용적이라
  (필드 누락 → skip) 빌드를 절대 깨뜨리지 않는다.
- 수동 확인용: `python dashboard/gen_dep_map.py`가 계산된 `const DEP=...`를 출력한다.
