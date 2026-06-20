#!/usr/bin/env bash
# Live-publish ./dag-run.html to the dashboard-data branch (-> GitHub Pages) every
# INTERVAL seconds while a dag_run_live.py run is in flight, then once more at the
# end. Non-destructive: copies ONLY dag-run.html into a shallow clone of
# dashboard-data (leaves index.html / ops.html / conformance.* untouched) and
# pushes with a fetch+reset retry so a concurrent publish isn't clobbered.
#
# Exits when no `dag_run_live.py` process remains. Usage: tools/publish_dagrun_live.sh [interval_s]
set -uo pipefail
ROOT="$(git rev-parse --show-toplevel)"; cd "$ROOT"
INTERVAL="${1:-40}"
url=$(git remote get-url origin)
work=$(mktemp -d); dd="$work/dd"
git clone --depth=1 --branch dashboard-data "$url" "$dd" >/dev/null 2>&1 || { echo "[pub] clone failed"; exit 1; }
cd "$dd"; git config user.name "Claude"; git config user.email "noreply@anthropic.com"; cd "$ROOT"

publish_once() {
  [ -f "$ROOT/dag-run.html" ] || return 0
  cp "$ROOT/dag-run.html" "$dd/dag-run.html"
  ( cd "$dd"
    git add dag-run.html
    git commit -q -m "dag-run live snapshot $(date -u +%H:%M:%SZ)" 2>/dev/null || exit 0
    for i in 1 2 3; do
      git push "$url" HEAD:dashboard-data >/dev/null 2>&1 && { echo "[pub] pushed $(date -u +%H:%M:%SZ)"; exit 0; }
      git fetch "$url" dashboard-data >/dev/null 2>&1 && git reset --hard FETCH_HEAD >/dev/null 2>&1
      cp "$ROOT/dag-run.html" "$dd/dag-run.html"; git add dag-run.html
      git commit -q -m "dag-run live snapshot $(date -u +%H:%M:%SZ) (retry $i)" 2>/dev/null || true
    done
    echo "[pub] push failed after retries $(date -u +%H:%M:%SZ)" )
}

echo "[pub] live-publishing dag-run.html every ${INTERVAL}s -> dashboard-data"
while pgrep -f "dag_run_live.py" >/dev/null 2>&1; do
  publish_once
  sleep "$INTERVAL"
done
echo "[pub] run ended — final publish"
publish_once
echo "[pub] done"
