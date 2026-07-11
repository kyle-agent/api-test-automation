# v2 로컬 실행 가이드 (별도 디렉터리 · venv)

> 별도 폴더에 clone → venv → 서버 기동 → `localhost:8800`에서 v2 확인.
> 실측 기준(2026-07-10): Python 3.11, controlplane/requirements.txt.

## 1. clone (별도 디렉터리)

```bash
cd ~/work            # 원하는 상위 폴더
git clone https://github.com/kyle-agent/api-test-automation.git scp-v2
cd scp-v2
git checkout claude/v2-redesign-planning-aufboo     # v2 브랜치
```

## 2. venv + 의존성

```bash
python3 -m venv .venv                 # Python 3.11 권장
source .venv/bin/activate             # (Windows: .venv\Scripts\activate)

pip install -U pip
pip install -r controlplane/requirements.txt   # UI 서버 필수 (fastapi/uvicorn/jinja2/boto3 등)

# (선택) 오프라인 테스트를 돌릴 거면:
pip install httpx2                    # starlette TestClient 의존 (reqs에 없음, dev 전용)

# (선택) AI 트리아지·파이프라인 기능까지:
pip install -r controlplane/requirements-ai.txt
```

## 3. 환경 변수 (.env)

```bash
cp .env.example .env
# .env 편집 — 최소 항목:
#   SCP_ACCESS_KEY / SCP_SECRET_KEY   ← 실 API 호출(스모크·LIVE)에 필요
#   SCP_REGION, SCP_ENV, SCP_AUTH_SCHEME
#   SCP_ALLOW_MUTATIONS=false         ← 열람/개발용 안전값(실행 버튼 비활성)
#   SCP_ALLOW_DESTRUCTIVE=false
```

- **화면만 둘러볼 거면 자격증명 없이도 됩니다** — 발행 대시보드(dashboard-data)
  읽기는 `git fetch`로 동작하고, 실 API 호출이 필요한 부분만 비게 됩니다.
- **실제 테스트 구동(스모크/LIVE)까지 하려면** SCP_ACCESS_KEY/SECRET_KEY 필수.
- `.env`는 절대 커밋 금지(gitignore 대상). 값은 로컬에만.

## 4. 발행본 미리 받기 (권장 — 발행 배지/수치가 바로 뜸)

```bash
git fetch origin dashboard-data       # v2가 커버리지·회귀 수치를 여기서 읽음
```

## 5. 서버 기동

```bash
PYTHONPATH=. python3 -m uvicorn controlplane.app:app --host 127.0.0.1 --port 8800
# 브라우저: http://localhost:8800   →  루트가 v2로 자동 랜딩(/ → /v2)
```

- 구 화면이 필요하면: `http://localhost:8800/legacy/home`
- 개발 중 자동 리로드: 끝에 `--reload` 추가.

## 6. 화면 둘러보기 (v2)

| 경로 | 내용 |
|---|---|
| `/v2` | Overview — 현황 헤드라인·KPI·런 타임라인 |
| `/v2/services` | 서비스 목록 → 상세(커버리지 링·엔드포인트·의존 그래프) |
| `/v2/model` | 리소스 모델 (카테고리▸서비스 계층) |
| `/v2/run` | 테스트 계획 (선택 트리 ↔ 조합 DAG ↔ pre-flight) |
| `/v2/runs/{id}` | 런 상세 / 실행 중 라이브 뷰 |
| `/v2/results` | 회귀·트리아지·정합성 |
| 헤더 ⌕ | 전역 검색 (서비스·엔드포인트·런) |

## 7. 실제 테스트 구동 (자격증명 있을 때)

- **읽기 전용 스모크** (자원 변화 없음, 실 API GET):
  ```bash
  SCP_ALLOW_MUTATIONS=false pytest tests/smoke -m smoke -q
  ```
- **v2에서 LIVE 실행**: `/v2/run`에서 서비스 선택 → 조합 DAG 확인 →
  `Review & run` → pre-flight(blast radius) → `Run live ▶`
  (과금 라이프사이클이면 "과금 실행 확인" 체크박스가 잠금 해제).
  → 발사 후 `/v2/runs/local-<id>` 라이브 뷰로 이동.
- **simulate 실행** (클라우드 무접촉, 화면 검증용):
  ```bash
  curl -X POST localhost:8800/api/run -H 'Content-Type: application/json' \
    -d '{"mode":"simulate","services":["networking/loadbalancer"]}'
  ```
  ※ 생성 라이프사이클은 테스트 픽스처(샘플 이미지 등)가 계정에 배선돼 있어야
  실제로 생성됩니다. 미배선 환경에서는 name-mismatch로 skip(실패 아님) — 읽기
  경로는 정상 동작.

## 8. 테스트 (선택)

```bash
PYTHONPATH=. python3 controlplane/v2/tests_offline.py    # v2 오프라인 (httpx2 필요)
PYTHONPATH=. python3 controlplane/tests_offline.py       # 엔진 오프라인
```

## 트러블슈팅

- **`No module named 'httpx2'`** (테스트 시): `pip install httpx2`.
- **포트 점유**: `--port 8801` 등으로 변경.
- **발행 배지가 "발행본 접근 불가"**: `git fetch origin dashboard-data` 실행.
- **`SCP_*` 인증 오류**: `.env`의 키/리전/스킴 확인. 화면만 볼 거면 무시 가능.
- **실행 버튼이 비활성("열람용")**: `SCP_ALLOW_MUTATIONS=true`로 기동해야
  LIVE 실행 버튼이 열립니다(실제 클라우드에 작용하니 주의).
