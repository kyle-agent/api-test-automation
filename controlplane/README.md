# controlplane/ — 플랫폼 제어 평면 서버 (M1)

FastAPI + SQLite + htmx로 구현된 SCP API Regression Test Platform의 control
plane입니다 (docs/PLATFORM-PLAN.md M1). 개발 기간 동안 실행기는 GitHub
Actions(`workflow_dispatch`)이고, M4 컷오버에서 동일 호스트 worker로
교체됩니다 — run 레코드/스케줄/UI는 그대로 유지.

## 기능

| 영역 | 내용 |
|---|---|
| 수동 실행 | suite × 환경 프로파일 선택 → `api-test.yml` dispatch |
| 스케줄 | cron(UTC) × suite × profile — 30초 폴링 데몬이 발화 |
| 라이브 추적 | `core/oplog.py`의 `APITEST_PLATFORM_URL` 미러를 `/api/ingest/events`로 수신 → run 상태/마일스톤 타임라인 |
| Run 히스토리 | DB 기록 + oplog 버킷 `index.json` 아카이브 병합 |
| 스냅샷 복원 | `runs/<id>/snapshot/`(core/snapshot.py)을 프록시해 **과거 run의 대시보드를 그대로 다시 열기** |
| AI triage (B1) | run 종료 후 baseline 외 신규 fail을 Claude가 environment / spec_change / test_bug / real_regression으로 분류 + 조치 제안 |
| 알림 (B2) | triage 요약을 webhook으로 push |

## 실행

```sh
pip install -r requirements.txt -r controlplane/requirements.txt
uvicorn controlplane.app:app --host 0.0.0.0 --port 8800
```

레포 루트에서 실행해야 합니다 — suites/, environments/, data/baselines/를
직접 읽습니다.

## 환경 변수

| 변수 | 용도 |
|---|---|
| `PLATFORM_DB` | SQLite 경로 (기본 `controlplane/data/platform.db`) |
| `PLATFORM_GITHUB_TOKEN` | `actions:write` PAT — 수동/스케줄 실행 dispatch |
| `PLATFORM_GITHUB_REPO` | `owner/repo` |
| `PLATFORM_GITHUB_REF` | dispatch 대상 브랜치 (기본 `main`) |
| `PLATFORM_INGEST_TOKEN` | 설정 시 `/api/ingest/events`에 Bearer 토큰 요구 |
| `SCP_ACCESS_KEY` / `SCP_SECRET_KEY` (+`SCP_REGION`/`SCP_ENV`) | 스냅샷/아카이브 버킷 읽기 (core/oplog.py와 동일 해석) |
| `ANTHROPIC_API_KEY` | AI triage 활성화 |
| `PLATFORM_TRIAGE_MODEL` | 기본 `claude-opus-4-8` |
| `PLATFORM_AUTO_TRIAGE` | `true`면 run 종료(dashboard 마일스톤) 시 자동 triage |
| `PLATFORM_NOTIFY_WEBHOOK` | triage 요약 POST(`{"text": ...}`, Slack 호환) |

## 워크플로우 연동 (라이브 이벤트)

GitHub 레포 Variables에 다음을 추가하면 모든 oplog 이벤트가 이 서버로
미러됩니다 (실패해도 run에는 영향 없음 — fire-and-forget):

```
APITEST_PLATFORM_URL   = https://<이 서버 주소>
APITEST_PLATFORM_TOKEN = <PLATFORM_INGEST_TOKEN과 동일 값>
```

(api-test.yml의 workflow-level `env:`에 두 변수를 노출해야 합니다 — M1
체크리스트 항목.)

## run 매칭 방식

`workflow_dispatch`는 시작한 run id를 돌려주지 않으므로, 디스패치 기록은
`gh_run_id` 없이 생성되고 **첫 ingest 이벤트가 가장 오래된 미결 기록에
FIFO로 바인딩**됩니다(Actions 큐 순서와 일치). 파일 트리거/외부 run은
`external` 레코드로 자동 생성됩니다.

## 보안 주의

UI에는 인증이 없습니다 — VPN/사내망 뒤에서만 운영하고, 외부 노출이
필요하면 리버스 프록시에서 인증을 붙이세요. ingest 엔드포인트는
`PLATFORM_INGEST_TOKEN`으로 보호할 수 있습니다.
