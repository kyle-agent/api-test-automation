#!/usr/bin/env bash
# record_run.sh — 테스트 명령 1개를 가상 X 디스플레이의 터미널(xterm)에서 실행하면서
# 화면을 MP4로 녹화한다 (Xvfb + xterm + ffmpeg x11grab, 추가 패키지 없음 — 이 세 개는
# Claude Code 클라우드 이미지와 대부분의 Linux 데스크톱에 기본 설치).
#
#   tools/record_run.sh [-o OUT.mp4] [-g WxH] [-f FPS] [-t TAIL_SEC] -- <command...>
#
# 예) 읽기 전용 스모크 런 녹화 (실 API 호출, 자원 변경 없음):
#   SCP_ALLOW_MUTATIONS=false tools/record_run.sh -o reports/smoke.mp4 \
#       -- python -m pytest tests/smoke -m smoke -q
#
# 자격/안전 게이트는 전부 호출자 env를 그대로 상속한다 — 이 스크립트는 어떤 SCP_*
# 플래그도 켜거나 끄지 않는다 (Hard Rule 1). 터미널에 찍히는 출력이 그대로 녹화되므로
# 비밀값을 echo하는 명령은 넣지 말 것 (Hard Rule 2).
set -euo pipefail

OUT="reports/run-$(date +%Y%m%d-%H%M%S).mp4"
GEOM="1280x720"
FPS=10
TAIL=3
while [ $# -gt 0 ]; do
  case "$1" in
    -o) OUT="$2"; shift 2 ;;
    -g) GEOM="$2"; shift 2 ;;
    -f) FPS="$2"; shift 2 ;;
    -t) TAIL="$2"; shift 2 ;;
    --) shift; break ;;
    *) echo "unknown option: $1" >&2; exit 2 ;;
  esac
done
[ $# -gt 0 ] || { echo "usage: $0 [-o out.mp4] [-g WxH] [-f fps] [-t tail_sec] -- <command...>" >&2; exit 2; }
for t in Xvfb xterm ffmpeg; do
  command -v "$t" >/dev/null || { echo "missing: $t (apt install xvfb xterm ffmpeg)" >&2; exit 3; }
done
mkdir -p "$(dirname "$OUT")"

# 사용 중이지 않은 디스플레이 번호 선택
DISP=99
while [ -e "/tmp/.X11-unix/X$DISP" ]; do DISP=$((DISP + 1)); done
export DISPLAY=":$DISP"

Xvfb "$DISPLAY" -screen 0 "${GEOM}x24" -nolisten tcp >/dev/null 2>&1 &
XVFB_PID=$!
cleanup() {
  kill "$FF_PID" 2>/dev/null || true
  wait "$FF_PID" 2>/dev/null || true
  kill "$XVFB_PID" 2>/dev/null || true
}
trap cleanup EXIT
# Xvfb 준비 대기
for _ in $(seq 1 50); do [ -e "/tmp/.X11-unix/X$DISP" ] && break; sleep 0.1; done

# 터미널 출력 인코딩 (· 같은 기호가 깨지지 않도록 UTF-8 고정)
export LANG="${LANG:-C.UTF-8}" LC_ALL="${LC_ALL:-C.UTF-8}"

# 녹화를 먼저 켜고(x11grab 기동 ~1초) 그 다음 터미널을 띄워 시작 구간이 잘리지 않게 한다.
ffmpeg -loglevel error -y -f x11grab -draw_mouse 0 -video_size "$GEOM" -framerate "$FPS" \
       -i "$DISPLAY" -c:v libx264 -preset veryfast -pix_fmt yuv420p "$OUT" &
FF_PID=$!
sleep 1.5

# 터미널: 명령을 실행하고 종료 코드를 표시한 뒤 TAIL초 머물다 닫힌다.
CMD_STR=$(printf '%q ' "$@")
SHOWN="$*"
W=${GEOM%x*}; H=${GEOM#*x}
COLS=$(( W / 9 )); ROWS=$(( H / 18 ))
xterm -u8 -geometry "${COLS}x${ROWS}+0+0" -fa Monospace -fs 11 -bg black -fg white \
      -title "api-test-automation" -e bash -c \
      "printf '\$ %s\n\n' $(printf '%q' "$SHOWN"); $CMD_STR; rc=\$?; printf '\n[exit %s]\n' \"\$rc\"; sleep $TAIL" &
XTERM_PID=$!

wait "$XTERM_PID" || true
kill -INT "$FF_PID" 2>/dev/null || true
wait "$FF_PID" 2>/dev/null || true
echo "recorded -> $OUT"
