#!/usr/bin/env bash
# start.sh — one-shot local bootstrap + console launch.
#
#   ./start.sh                 # venv 준비(없으면 생성) → 의존성 설치(변경 시만) → 존 가드 → 콘솔 실행
#   ./start.sh --setup-only    # venv + 의존성만 준비하고 종료 (그 뒤 `source .venv/bin/activate`)
#   ./start.sh --no-guard      # 존 가드 건너뛰고 콘솔 실행
#   ./start.sh -- <cmd...>     # venv 안에서 임의 명령 실행 (예: ./start.sh -- python -m spec.summary)
#
# 환경변수: PORT(기본 8800) · HOST(기본 0.0.0.0) · PYTHON(기본 python3)
# 콘솔: http://localhost:$PORT/testing/embed  (console2/README.md 참고)
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$ROOT"
PYTHON="${PYTHON:-python3}"
VENV="$ROOT/.venv"
STAMP="$VENV/.requirements.sha256"
PORT="${PORT:-8800}"
HOST="${HOST:-0.0.0.0}"

SETUP_ONLY=0; NO_GUARD=0
while [ $# -gt 0 ]; do
  case "$1" in
    --setup-only) SETUP_ONLY=1; shift ;;
    --no-guard)   NO_GUARD=1; shift ;;
    --)           shift; break ;;
    -h|--help)    sed -n 2,12p "$0"; exit 0 ;;
    *)            echo "unknown option: $1 (see --help)" >&2; exit 2 ;;
  esac
done

# 1) venv — 없으면 생성, 있으면 재사용
if [ ! -x "$VENV/bin/python" ]; then
  echo "[start] creating venv at $VENV ($($PYTHON --version 2>&1))"
  "$PYTHON" -m venv "$VENV"
fi
# shellcheck disable=SC1091
source "$VENV/bin/activate"

# 2) deps — requirements 파일 해시가 바뀐 경우에만 설치 (매번 pip 돌리지 않음)
REQS=(requirements.txt controlplane/requirements.txt)
want="$(cat "${REQS[@]}" | sha256sum | cut -d' ' -f1)"
have="$(cat "$STAMP" 2>/dev/null || true)"
if [ "$want" != "$have" ]; then
  echo "[start] installing dependencies (${REQS[*]})"
  python -m pip install --quiet --upgrade pip
  python -m pip install --quiet -r requirements.txt -r controlplane/requirements.txt
  echo "$want" > "$STAMP"
else
  echo "[start] dependencies up to date"
fi

# 3) 자격증명 — .env 도 없고 환경변수도 없으면 실행 의미가 없으니 멈춤 (.env 내용은 절대 출력하지 않음)
if [ ! -f "$ROOT/.env" ] && [ -z "${SCP_ACCESS_KEY:-}" ]; then
  echo "[start] .env not found — cp .env.example .env 후 SCP_ACCESS_KEY/SCP_SECRET_KEY/SCP_REGION 채우세요" >&2
  [ "$SETUP_ONLY" = 1 ] || exit 1
fi

if [ "$SETUP_ONLY" = 1 ]; then
  echo "[start] setup done — activate with: source .venv/bin/activate"
  exit 0
fi

# 임의 명령 모드
if [ $# -gt 0 ]; then
  exec "$@"
fi

# 4) 존 가드 (read-only 프로브) — stale SCP_ZONE 핀이면 콘솔을 띄우기 전에 알려준다.
#    콘솔 pre-flight·런 시작에서도 다시 검사하므로 여기서는 경고만 (SCP_ZONE_CHECK=false 로 생략).
if [ "$NO_GUARD" = 0 ]; then
  if ! SCP_ALLOW_MUTATIONS=false SCP_ALLOW_DESTRUCTIVE=false python -m regression.scenarios.zone_guard >/dev/null; then
    echo "[start] zone guard BLOCK — .env 의 SCP_ZONE 핀을 확인하세요 (자세한 사유는 위 [zone-guard] 줄)" >&2
    echo "[start] 그래도 콘솔을 띄우려면 ./start.sh --no-guard" >&2
    exit 1
  fi
fi

# 5) 콘솔 (control-plane spine; console2 는 /testing/embed 에 내장)
echo "[start] console → http://localhost:$PORT/testing/embed"
exec uvicorn controlplane.app:app --host "$HOST" --port "$PORT"
