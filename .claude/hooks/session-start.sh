#!/bin/bash
# SessionStart hook — make a fresh Claude-on-the-web container test-ready.
# Idempotent, non-interactive. Web-only (skips on local machines).
#
# Why this exists (discovered 2026-06-19): a fresh remote container starts with
#   - Python deps NOT installed (requests/pytest/boto3 missing) -> conftest import fails
#   - a SHALLOW clone (history truncated -> git ancestry/merge-base lie)
#   - a STALE local `main` ref (points at the clone-time commit, not origin/main)
# This hook fixes the deps (the thing that blocks tests) and refreshes git refs.
# The pytest-interpreter gotcha (always `python -m pytest`, never bare `pytest`)
# and the rest of the bootstrap contract live in START_HERE.md.
set -euo pipefail

# Only run in the Claude-on-the-web remote env; no-op on local machines.
if [ "${CLAUDE_CODE_REMOTE:-}" != "true" ]; then
  echo "[session-start] not remote (CLAUDE_CODE_REMOTE != true) — skipping"
  exit 0
fi

cd "${CLAUDE_PROJECT_DIR:-$(git rev-parse --show-toplevel)}"

# 1) Python deps — install with the SAME interpreter the tests use (python -m pip).
#    Bare `pip`/`pytest` resolve to a different env that lacks these deps.
echo "[session-start] installing Python deps (python -m pip) ..."
python -m pip install -q -r requirements.txt

# 2) Git hygiene — unshallow + refresh refs so history/ancestry checks are honest,
#    and re-point the stale local `main` at the real origin/main. Best-effort:
#    never fail the session on a git/network hiccup.
echo "[session-start] refreshing git (unshallow + align main) ..."
if [ "$(git rev-parse --is-shallow-repository 2>/dev/null || echo false)" = "true" ]; then
  git fetch --unshallow --quiet origin 2>/dev/null || git fetch --quiet origin 2>/dev/null || true
else
  git fetch --quiet origin 2>/dev/null || true
fi
# Align local main to origin/main without touching the checked-out branch.
if git show-ref --verify --quiet refs/remotes/origin/main; then
  cur="$(git rev-parse --abbrev-ref HEAD 2>/dev/null || echo)"
  if [ "$cur" != "main" ]; then
    git branch -f main origin/main 2>/dev/null || true
  fi
fi

# 3) Sanity: confirm the test interpreter can import the kernel.
python -c "import requests, yaml, boto3; import core" \
  && echo "[session-start] OK — deps + kernel import verified" \
  || { echo "[session-start] WARN — kernel import failed (check requirements)"; }

echo "[session-start] done"
