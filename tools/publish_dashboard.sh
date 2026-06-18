#!/usr/bin/env bash
# Hand-driven dashboard publish — replicates the CI "Build + publish dashboard"
# job for runs executed from the Claude remote env (CI auto-trigger is disabled,
# so hand-driven coverage would otherwise never reach the published Pages
# dashboard). CUMULATIVE: pulls the prior verified-set / history / per-endpoint
# status from the dashboard-data branch, merges THIS session's
# reports/results/observations.jsonl, then pushes the rebuilt dashboard back to
# dashboard-data (-> GitHub Pages). Non-destructive: only overwrites the files
# the dashboard owns; uses a fetch+rebase retry loop so a concurrent push isn't
# clobbered. Mirrors .github/workflows/api-test.yml "Build + publish dashboard".
#
# Usage:  tools/publish_dashboard.sh
set -uo pipefail
ROOT="$(git rev-parse --show-toplevel)"; cd "$ROOT"
SHA=$(git rev-parse --short HEAD); BRANCH=$(git rev-parse --abbrev-ref HEAD)
mkdir -p dashboard reports data data/baselines

# This script pulls the dashboard-data CUMULATIVE state + build outputs into
# tracked paths (data/*, dashboard/*) so build.py can merge them. Those belong on
# dashboard-data, NOT on the source branch — restore the working tree on exit so
# a hand-driven publish never leaves the repo dirty.
cleanup() {
  cd "$ROOT" 2>/dev/null || return
  git checkout -- data/baselines/verified_endpoints.json data/conformance.json dashboard/ops.html 2>/dev/null || true
  rm -f dashboard/endpoint_status.json dashboard/verified_endpoints.json \
        data/endpoint_status.json data/verified_endpoints.json 2>/dev/null || true
}
trap cleanup EXIT

echo "[publish] pulling cumulative state from origin/dashboard-data ..."
git fetch origin dashboard-data --depth=1 >/dev/null 2>&1 || true
git show origin/dashboard-data:history.jsonl            > dashboard/history.jsonl        2>/dev/null || true
git show origin/dashboard-data:verified_endpoints.json  > data/verified_endpoints.json   2>/dev/null || true
[ -s data/verified_endpoints.json ] || rm -f data/verified_endpoints.json
git show origin/dashboard-data:endpoint_status.json     > data/endpoint_status.json      2>/dev/null || true
[ -s data/endpoint_status.json ] || rm -f data/endpoint_status.json
git show origin/dashboard-data:conformance.json         > data/conformance.json          2>/dev/null || true

echo "[publish] building dashboard (cumulative merge) ..."
python -m dashboard.build --run-type "hand-driven" --sha "$SHA" --branch "$BRANCH" \
  --out dashboard/index.html || { echo "[publish] build FAILED"; exit 1; }

echo "[publish] deriving + accumulating 2xx evidence (IB-041) ..."
git show origin/dashboard-data:verified_endpoints_evidence.json > data/baselines/verified_endpoints.json 2>/dev/null || true
[ -s data/baselines/verified_endpoints.json ] || rm -f data/baselines/verified_endpoints.json
python -m tools.derive_verified --observations reports/results/observations.jsonl \
  --out data/baselines/verified_endpoints.json || true

echo "[publish] pushing to dashboard-data ..."
url=$(git remote get-url origin)
pub=$(mktemp -d); dd="$pub/dd"
git clone --depth=1 --branch dashboard-data "$url" "$dd" 2>/dev/null || { mkdir -p "$dd"; ( cd "$dd" && git init -q -b dashboard-data ); }
# copy ONLY the files the dashboard owns (no rm -rf of the branch — conformance
# job's files etc. are left untouched).
cp dashboard/index.html              "$dd"/ 2>/dev/null || true
cp dashboard/history.jsonl           "$dd"/ 2>/dev/null || true
rm -rf "$dd"/services; cp -r dashboard/services "$dd"/services 2>/dev/null || true
cp dashboard/verified_endpoints.json "$dd"/ 2>/dev/null || true
cp dashboard/endpoint_status.json    "$dd"/ 2>/dev/null || true
cp data/baselines/verified_endpoints.json "$dd"/verified_endpoints_evidence.json 2>/dev/null || true
cp reports/dashboard/ops.html        "$dd"/ops.html 2>/dev/null || cp dashboard/ops.html "$dd"/ 2>/dev/null || true
touch "$dd/.nojekyll"
cd "$dd"
git config user.name "Claude"; git config user.email "noreply@anthropic.com"
git add -A
git commit -q -m "dashboard @ $SHA (hand-driven publish from $BRANCH)" || { echo "[publish] no change to publish"; exit 0; }
for i in 1 2 3 4 5; do
  if git push "$url" HEAD:dashboard-data 2>/dev/null; then echo "[publish] PUBLISHED to dashboard-data (-> Pages)"; exit 0; fi
  echo "[publish] push race, fetch+rebase ($i) ..."
  git fetch "$url" dashboard-data 2>/dev/null && git rebase FETCH_HEAD 2>/dev/null || git rebase --abort 2>/dev/null || true
  sleep 3
done
echo "[publish] PUSH FAILED after retries"; exit 1
