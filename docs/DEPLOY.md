# DEPLOY — 호스트 불문 단일 패키지 배포 runbook (M4)

> docs/PLATFORM-PLAN.md §1 원칙 5 · §3 · M4. AWS EC2든 로컬 PC든 사내 VM이든
> **같은 Docker Compose 한 벌**(server + worker + 공유 repo 볼륨)로 뜬다.
> 엔진/시나리오/knowledge는 무수정 — 실행기만 GitHub Actions ↔ 동일 호스트
> worker 사이에서 전환된다 (`PLATFORM_EXECUTOR`).

## 구성

```
┌─ docker compose ──────────────────────────────────────────────┐
│  server  uvicorn controlplane.app  :8800  (UI·API·스케줄러)     │
│  worker  python -m runner.worker          (run 큐 소비·실행)    │
│     └── 둘 다 ./ 를 /app 으로 bind-mount — repo working copy가  │
│         곧 공유 상태: 선언 데이터(suites/·environments/·         │
│         scenarios·knowledge) + SQLite(controlplane/data/) +     │
│         리포트/스냅샷                                            │
└────────────────────────────────────────────────────────────────┘
```

run의 흐름 (worker 실행기): UI/스케줄러가 run 레코드 생성(status
`dispatched`, gh_run_id 없음 — **이 레코드가 곧 큐**) → worker가 ~15초마다
폴링, 가장 오래된 레코드를 `local-<unix_ts>` id로 claim(UPDATE … WHERE
gh_run_id IS NULL — 단일 승자 보장) → api-test.yml과 동일한 스테이지 시퀀스
실행(validate → smoke → [adopt-CRUD → vpc-CRUD] → sweep → [conformance] →
dashboard → snapshot) → 스테이지별 마일스톤을 DB에 직접 기록(라이브 UI는
HTTP 없이도 동작).

## 신규 호스트 프로비저닝 (= compose up 한 번)

```sh
# 1. docker 설치 (예: Ubuntu)
curl -fsSL https://get.docker.com | sh

# 2. repo 클론 (또는 기존 호스트에서 디렉토리 복사 — 아래 '호스트 이전')
git clone <repo-url> apitest && cd apitest

# 3. 환경 파일 작성 — 모든 변수가 템플릿에 주석으로 문서화되어 있음
cp .env.platform.example .env.platform
$EDITOR .env.platform        # SCP_ACCESS_KEY / SCP_SECRET_KEY / SCP_PROJECT_ID 필수

# 4. 기동
docker compose up -d --build

# 확인
docker compose ps
docker compose logs -f worker     # "[worker] queue ... (poll 15s)"
open http://<host>:8800           # UI — Testing 탭에서 suite × profile 실행
```

## 업그레이드

```sh
git pull
docker compose up -d --build      # 이미지 재빌드 + 재시작
```

코드가 bind-mount이므로 파이썬 변경만이라면 `docker compose restart`로도
충분하다 — `--build`는 의존성(requirements)이 바뀐 경우용.

## 호스트 이전 (= 디렉토리 이동)

플랫폼의 전체 상태(repo working copy + SQLite DB `controlplane/data/` +
리포트/스냅샷)가 이 디렉토리 하나에 있다 (§1 원칙 5):

```sh
docker compose down               # 옛 호스트
tar czf apitest.tgz apitest/      # 디렉토리 통째로
# 새 호스트에서: 풀고 → docker 설치 → docker compose up -d --build
```

`.env.platform`(secret)이 함께 이동하는지만 확인하면 끝. S3 oplog/스냅샷
버킷은 외부에 있으므로 영향 없음.

## 편집 반영 (§3.1 파일 모드 — 컷오버 완료 상태)

UI 저작(`/planning/edit`)은 `controlplane/authoring.py`의 검증 → 원자적
쓰기 → **로컬 git 커밋** 한 경로만 탄다. 개발 기간의 동작이 그대로 배포
파일 모드다 — worker가 **같은 working copy**를 읽으므로 저장이 통과되면
**다음 run에 즉시 반영**된다 (push 불필요; `PLATFORM_GIT_PUSH=true`로 백업
push를 켤 수 있고, git은 이력·1-click 되돌리기 용도). run 로그/스냅샷
meta에는 실행 시점 working copy 상태가 남는다.

## 실행기 전환 (worker ↔ GitHub Actions)

compose 번들은 `docker-compose.yml`의 `environment:`로
`PLATFORM_EXECUTOR=worker`를 강제한다. **Actions 실행기로 되돌리려면**:

1. `docker-compose.yml`의 server/worker 양쪽에서 `PLATFORM_EXECUTOR: worker`
   줄 삭제 (또는 `actions`로 변경)
2. `.env.platform`에 `PLATFORM_GITHUB_TOKEN`(actions:write PAT) +
   `PLATFORM_GITHUB_REPO=owner/repo` (+필요시 `PLATFORM_GITHUB_REF`) 설정
3. `docker compose up -d` — 이후 수동/스케줄 실행은 다시 workflow_dispatch로
   나간다. worker 컨테이너는 큐가 비므로 켜두어도 무해하고
   (`docker compose stop worker`로 꺼도 됨), run 레코드/히스토리/UI는 두
   모드에서 동일하다.

라이브 이벤트: Actions 모드에서는 repo Variables의 `APITEST_PLATFORM_URL`이
이 서버의 **외부에서 접근 가능한** 주소를 가리켜야 미러가 들어온다. worker
모드에서는 마일스톤이 DB 직접 기록이라 서버 주소 없이도 라이브 UI가 동작한다
(`APITEST_PLATFORM_URL=http://server:8800`은 리소스 이벤트 미러용).

## 운영 메모

- **보안**: UI에는 인증이 없다 — 사내망/VPN 뒤에서만 운영하고, 외부 노출이
  필요하면 리버스 프록시(예: nginx + basic auth/SSO)에서 인증을 붙인다.
  엔진용 엔드포인트(ingest·명령 채널)는 `PLATFORM_INGEST_TOKEN`으로 보호.
- **로그**: 스테이지별 전체 로그는 `reports/worker/<run_id>-<stage>.log`,
  요약은 `docker compose logs worker`.
- **동시성**: worker 1대 = 전 run 직렬 (Actions concurrency 그룹과 동일한
  효과). claim이 단일 승자 보장이므로 worker 컨테이너를 늘리면 run 단위
  병렬이 되지만, 같은 계정/리전 프로파일이라면 VPC 할당량(계정 cap 5)을
  공유하므로 권장하지 않는다 (PLATFORM-PLAN §2.4의 할당량 인지 큐는 후속).
- **Actions와 병행**: worker 모드 중에도 `.github/run-request` push /
  workflow_dispatch로 Actions run을 직접 돌릴 수 있다 (보조 실행기) — 그
  run들은 ingest 미러를 통해 같은 히스토리에 합류한다.
- **worker가 하지 않는 것**: PR 코멘트, GitHub Pages(dashboard-data 브랜치)
  발행, `refresh_catalog`, heavy.txt 셀프트리거, schema-live probe. 대시보드는
  호스트의 `dashboard/index.html` + run 스냅샷(S3 구성 시)으로 제공된다.
