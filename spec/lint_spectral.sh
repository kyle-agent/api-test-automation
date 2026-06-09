#!/usr/bin/env bash
# Lint the locally-generated OpenAPI snapshot (data/openapi/*.json) and write
# a JSON report. Requires: npm i -g @stoplight/spectral-cli
#
# OpenAPI inputs relocated framework/openapi -> data/openapi to match the new
# layout, and the ruleset now lives alongside this script (spec/spectral.yaml).
set -euo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"
ROOT="$(cd "$HERE/.." && pwd)"
cd "$ROOT"
mkdir -p reports
spectral lint "data/openapi/*.json" --ruleset "$HERE/spectral.yaml" --format json \
  > reports/spectral.json 2>/dev/null || true
spectral lint "data/openapi/*.json" --ruleset "$HERE/spectral.yaml" --format pretty \
  2>/dev/null | tail -5 || true
echo "wrote reports/spectral.json"
