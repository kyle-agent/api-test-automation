#!/usr/bin/env bash
# Publish the loggingaudit live-flow view to GitHub Pages (dashboard-data
# branch -> kyle-agent.github.io/api-test-automation/live.html). Because the CI
# auto-trigger is off, hand-driven / heavy runs would otherwise have no live
# page; this re-harvests the loggingaudit window, re-renders audit.live_view
# --mode flow, and pushes ONLY live.html to dashboard-data. Designed to be run
# once or in a loop (REFRESH cadence) so the page tracks a heavy run in near
# real time. The page itself carries <meta refresh> so an open browser reloads.
#
# Usage:
#   tools/publish_live.sh                     # one shot, window = since START
#   START=2026-06-18T22:30:00Z tools/publish_live.sh
#   LOOP=1 EVERY=90 tools/publish_live.sh     # re-publish every 90s until killed
set -uo pipefail
ROOT="$(git rev-parse --show-toplevel)"; cd "$ROOT"
START="${START:-$(date -u -d '90 minutes ago' +%Y-%m-%dT%H:%M:%SZ 2>/dev/null || date -u +%Y-%m-%dT%H:%M:%SZ)}"
REFRESH="${REFRESH:-60}"
EVERY="${EVERY:-90}"
url=$(git remote get-url origin)
dd=$(mktemp -d)/dd
git clone --depth=1 --branch dashboard-data "$url" "$dd" >/dev/null 2>&1 || { echo "[live] clone failed"; exit 1; }
( cd "$dd" && git config user.name Claude && git config user.email noreply@anthropic.com )

publish_once() {
  echo "[live] harvest+render (start=$START) ..."
  timeout 280 python -m audit.live_view --start "$START" --mode flow --refresh "$REFRESH" \
    --out reports/audit/live_flow.html 2>&1 | tail -2
  cp reports/audit/live_flow.html "$dd/live.html"
  ( cd "$dd"
    git pull --rebase -q origin dashboard-data 2>/dev/null || true
    cp "$ROOT/reports/audit/live_flow.html" live.html
    git add live.html
    git commit -q -m "live flow @ $(date -u +%H:%MZ) (heavy run)" 2>/dev/null || { echo "[live] no change"; return 0; }
    for i in 1 2 3 4; do
      git push -q "$url" HEAD:dashboard-data 2>/dev/null && { echo "[live] PUBLISHED -> live.html"; return 0; }
      git pull --rebase -q "$url" dashboard-data 2>/dev/null || true; sleep 2
    done
    echo "[live] push failed"; )
}

publish_once
if [ "${LOOP:-0}" = "1" ]; then
  while true; do sleep "$EVERY"; publish_once; done
fi
