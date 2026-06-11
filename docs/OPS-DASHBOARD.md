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
