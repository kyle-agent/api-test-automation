#!/usr/bin/env bash
# Publish the EXEC live view (our execution log = reports/results/observations.jsonl)
# to dashboard-data -> live.html. ZERO loggingaudit API calls: renders
# audit.live_view --from obs --mode exec, so it works DURING a gateway storm (when
# the audit harvest 503s) and avoids the per-page harvest call cost. Loops every
# EVERY seconds while a dag_run_live run is in flight, then publishes once more.
#
# Usage:  EVERY=60 tools/publish_live_obs.sh
set -uo pipefail
ROOT="$(git rev-parse --show-toplevel)"; cd "$ROOT"
EVERY="${EVERY:-60}"
url=$(git remote get-url origin)
dd=$(mktemp -d)/dd
git clone --depth=1 --branch dashboard-data "$url" "$dd" >/dev/null 2>&1 || { echo "[live-obs] clone failed"; exit 1; }
( cd "$dd" && git config user.name Claude && git config user.email noreply@anthropic.com )

publish_once() {
  timeout 120 python -m audit.live_view --from obs --obs reports/results/observations.jsonl \
    --mode exec --refresh "$EVERY" --live-state --out reports/audit/live_obs.html 2>&1 | tail -1
  [ -f reports/audit/live_obs.html ] || return 0
  ( cd "$dd"
    git pull --rebase -q origin dashboard-data 2>/dev/null || true
    cp "$ROOT/reports/audit/live_obs.html" live.html
    git add live.html
    git commit -q -m "live exec (obs, 0 audit calls) $(date -u +%H:%M:%SZ)" 2>/dev/null || return 0
    for i in 1 2 3; do
      git push -q "$url" HEAD:dashboard-data 2>/dev/null && { echo "[live-obs] pushed $(date -u +%H:%M:%SZ)"; return 0; }
      git pull --rebase -q "$url" dashboard-data 2>/dev/null || true; sleep 2
    done
    echo "[live-obs] push failed" )
}

publish_once
while pgrep -f "dag_run_live.py" >/dev/null 2>&1; do sleep "$EVERY"; publish_once; done
echo "[live-obs] run ended — final publish"; publish_once
