# controlplane/ — 플랫폼 제어 평면 서버 (M1+M2+M3+M4)

FastAPI + SQLite + htmx로 구현된 SCP API Regression Test Platform의 control
plane입니다 (docs/PLATFORM-PLAN.md M1~M4). 실행기는
`PLATFORM_EXECUTOR`로 전환합니다 (controlplane/dispatch.py):

- `actions` (기본, 개발 기간) — `api-test.yml`을 `workflow_dispatch`로 트리거
- `worker` (배포 모드) — run 레코드(status `dispatched`)가 곧 큐이고, 동일
  호스트의 `runner/worker.py`가 claim해 같은 스테이지 시퀀스를 실행

run 레코드/스케줄/UI는 두 모드에서 동일합니다. Docker Compose 배포 번들
(server + worker + 공유 repo 볼륨)과 운영 runbook은 `docs/DEPLOY.md` 참고.

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
| 개입 명령 (M2) | run 상세 페이지에서 run abort / 시나리오 skip / 폴링 강제 종료 명령을 쌓으면 **엔진이 step 경계마다 폴링**해 수행 (아래 명령 채널 API) |
| 리소스 인벤토리 (M2) | `/testing/resources` — ingest된 resource 이벤트를 res_id별로 접어 live/gone 상태 표시, run 필터 |
| 단일 리소스 삭제 (M2) | live 행의 삭제 버튼 — `cleanup/reconciler.py`의 `_delete` + kind별 DELETE 매핑 재사용, `SCP_ALLOW_DESTRUCTIVE=true` 필수, 시도는 `platform-delete` resource 이벤트로 기록 |
| Run 비교 (M2) | `/reporting/compare?a=&b=` — 두 run의 스냅샷 observations를 endpoint_key+method로 조인해 새로 깨짐/고쳐짐/계속 실패/분류 변화 diff |
| multi-tenancy 기반 (M2) | runs/schedules에 `tenant` 컬럼 (§6 확정 4 — UI 선택자는 M3) |
| 저작 편집기 (M3) | Planning의 스위트·환경 프로파일·시나리오(lifecycle)·knowledge 파일을 `/planning/edit`에서 raw 편집 — 검증만(htmx 인라인 오류) / 검증+저장. 아래 "편집 모델" 참고 |
| 의존 그래프 뷰 (M3) | `/planning/dependencies` — `dependencies.json`의 vpc_schedule(공유 adopt VPC vs 직렬 vpc-crud, lanes, quota)과 `knowledge/formal/cross-service.yaml`의 requires 그래프를 서버 렌더 SVG/박스로 시각화 (read-only, 편집은 원본 파일 편집기 링크) |
| 할당량 시뮬레이션 (M3) | scenario/dependencies 파일 저장 시 vpc_schedule이 함의하는 peak 동시 VPC(공유 adopt 1 + 직렬 vpc-crud 최악)를 계산, `core/budgets.py` 한도 초과면 **경고** (차단하지 않음) |

## 편집 모델 (M3 — §3.1 개발 기간 반영 방식)

UI 저장은 `controlplane/authoring.py`의 `propose_edit()` 한 경로만 탑니다:

1. 경로 게이트 — `suites/`·`environments/`·`knowledge/`·`regression/scenarios/`
   아래의 yaml/json/md만 편집 가능 (엔진 `.py`는 불가)
2. 파싱 (suffix별 YAML/JSON) → 임시 파일 기록 → `os.replace`로 원자적 적용
3. 해당 파일군 validator를 **적용된 상태**에서 실행 — `core.suites` /
   `core.profiles` / `python -m regression.scenarios.validate` /
   `knowledge/formal/validate.py`. 실패 시 원본을 byte-identical 복원
4. 통과 시 `git add` + 로컬 커밋 `authoring: <path> via platform UI`
   (식별자 미설정/서명 불가 호스트는 `-c user.name=… -c commit.gpgsign=false`
   폴백; git 실패는 경고로만 보고 — 파일 반영은 유지)
5. 원격 push는 `PLATFORM_GIT_PUSH=true`일 때만 — **기본 off, 운영자가 수동
   push** (Actions가 클론해 읽는 개발 기간 모델)

M4 컷오버는 5단계만 바꿉니다 — 검증→쓰기→커밋(1~4)은 두 모드 공용.

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
| `PLATFORM_EXECUTOR` | `actions`(기본) \| `worker` — M4 실행기 전환 (worker면 dispatch 없이 큐잉만) |
| `PLATFORM_GITHUB_TOKEN` | `actions:write` PAT — 수동/스케줄 실행 dispatch (actions 실행기) |
| `PLATFORM_GITHUB_REPO` | `owner/repo` |
| `PLATFORM_GITHUB_REF` | dispatch 대상 브랜치 (기본 `main`) |
| `PLATFORM_INGEST_TOKEN` | 설정 시 `/api/ingest/events`에 Bearer 토큰 요구 |
| `SCP_ACCESS_KEY` / `SCP_SECRET_KEY` (+`SCP_REGION`/`SCP_ENV`) | 스냅샷/아카이브 버킷 읽기 (core/oplog.py와 동일 해석) |
| `SCP_ALLOW_DESTRUCTIVE` | `true`면 인벤토리의 단일 리소스 삭제 활성 (미설정 시 버튼 비활성 + 경고) |
| `ANTHROPIC_API_KEY` | AI triage 활성화 |
| `PLATFORM_TRIAGE_MODEL` | 기본 `claude-opus-4-8` |
| `PLATFORM_AUTO_TRIAGE` | `true`면 run 종료(dashboard 마일스톤) 시 자동 triage |
| `PLATFORM_GIT_PUSH` | `true`면 편집 저장(로컬 커밋) 후 `git push`까지 수행 — 기본 off (운영자 수동 push) |
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

## 명령 채널 API (M2 — 엔진 폴링 계약)

엔진은 step 경계마다 다음을 폴링/ack합니다. `PLATFORM_INGEST_TOKEN` 설정 시
두 엔드포인트 모두 ingest와 동일한 `Authorization: Bearer` 토큰을 요구합니다.

```
GET  /api/runs/<gh_run_id>/commands
     → {"commands": [{"id": 1, "action": "abort_run"|"skip_scenario"|"stop_polling",
                      "target": "<str>"}]}     # pending(미 ack)만
POST /api/commands/<id>/ack
     → {"ok": true}                            # 멱등 (재 ack도 ok), 미존재 id는 404
```

## 오프라인 테스트

```sh
PYTHONPATH=. python3 controlplane/tests_offline.py
PYTHONPATH=. python3 controlplane/tests_ai_offline.py
PYTHONPATH=. python3 runner/tests_offline.py      # M4 worker/실행기 전환
```

네트워크/버킷/credential 없이 명령 채널 · 인벤토리 폴딩 · 비교 뷰 · tenant
스키마 · 저작 파이프라인(검증 거부/복원·임시 git repo 커밋·할당량 시뮬레이션
경고) · 편집기/의존 그래프 렌더를 검증합니다 (DB는 임시 파일).

## run 매칭 방식

`workflow_dispatch`는 시작한 run id를 돌려주지 않으므로, 디스패치 기록은
`gh_run_id` 없이 생성되고 **첫 ingest 이벤트가 가장 오래된 미결 기록에
FIFO로 바인딩**됩니다(Actions 큐 순서와 일치). 파일 트리거/외부 run은
`external` 레코드로 자동 생성됩니다.

worker 실행기(M4)에서는 미결 dispatched 기록이 곧 **로컬 worker의 큐**라서
ingest가 FIFO 바인딩을 하지 않습니다 — worker가 claim 시점에 `local-<ts>`
id를 직접 부여하고, 병행 Actions run은 항상 `external` 레코드를 받습니다.

## 보안 주의

UI에는 인증이 없습니다 — VPN/사내망 뒤에서만 운영하고, 외부 노출이
필요하면 리버스 프록시에서 인증을 붙이세요. 엔진용 엔드포인트(ingest ·
명령 폴링/ack)는 `PLATFORM_INGEST_TOKEN`으로 보호할 수 있습니다.
