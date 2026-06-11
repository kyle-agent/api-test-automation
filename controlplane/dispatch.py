"""Trigger a test run — switchable executor (M4, docs/PLATFORM-PLAN.md §3).

PLATFORM_EXECUTOR selects who consumes the run record:

  actions (default)  development period — dispatch api-test.yml via the
                     workflow_dispatch REST API (§2.4). Unchanged behaviour.
  worker             deployment mode — GitHub is skipped entirely; the run
                     record itself (status 'dispatched', gh_run_id NULL) IS
                     the queue, and the same-host runner/worker.py claims and
                     executes it (ROADMAP Phase 3 Step 2).

Run records, schedules and the UI are identical in both modes — only this
dispatch implementation differs.

Config (env):
  PLATFORM_EXECUTOR         actions (default) | worker
  PLATFORM_GITHUB_TOKEN     PAT with `actions:write` (or GITHUB_TOKEN)
  PLATFORM_GITHUB_REPO      owner/repo
  PLATFORM_GITHUB_REF       branch to run on (default: main)
  PLATFORM_GITHUB_WORKFLOW  workflow file (default: api-test.yml)
"""
from __future__ import annotations

import os

import requests


def executor() -> str:
    """'actions' (default) or 'worker' — unknown values fall back to actions
    so a typo can never silently swallow runs."""
    mode = os.environ.get("PLATFORM_EXECUTOR", "").strip().lower()
    return "worker" if mode == "worker" else "actions"


def configured() -> bool:
    if executor() == "worker":
        return True  # the queue is the local DB — nothing external to configure
    return bool((os.environ.get("PLATFORM_GITHUB_TOKEN") or os.environ.get("GITHUB_TOKEN"))
                and os.environ.get("PLATFORM_GITHUB_REPO"))


def dispatch_run(suite: str, profile: str = "", service: str = "",
                 crud_filter: str = "") -> tuple[bool, str]:
    """Fire workflow_dispatch with the suite × profile (+ optional service /
    crud_filter narrowing — "이 서비스만 실행"). Returns (ok, message); an
    unconfigured dispatcher records the run without firing so the UI keeps
    working in local development.

    In worker mode there is nothing to fire: the caller's run record (status
    'dispatched', no gh_run_id) is the queue the same-host worker polls — the
    narrowing options travel in the run record's detail (KEY=VALUE lines the
    worker merges over the suite expansion)."""
    if executor() == "worker":
        return True, "queued for local worker"
    token = os.environ.get("PLATFORM_GITHUB_TOKEN") or os.environ.get("GITHUB_TOKEN")
    repo = os.environ.get("PLATFORM_GITHUB_REPO", "")
    if not (token and repo):
        return False, ("dispatch not configured (PLATFORM_GITHUB_TOKEN / "
                       "PLATFORM_GITHUB_REPO) — run recorded only")
    ref = os.environ.get("PLATFORM_GITHUB_REF", "main")
    workflow = os.environ.get("PLATFORM_GITHUB_WORKFLOW", "api-test.yml")
    inputs = {k: v for k, v in (("suite", suite), ("profile", profile),
                                ("service", service), ("crud_filter", crud_filter)) if v}
    try:
        resp = requests.post(
            f"https://api.github.com/repos/{repo}/actions/workflows/{workflow}/dispatches",
            json={"ref": ref, "inputs": inputs},
            headers={"Authorization": f"Bearer {token}",
                     "Accept": "application/vnd.github+json"},
            timeout=15)
    except requests.RequestException as exc:
        return False, f"dispatch failed: {exc}"
    if resp.status_code == 204:
        return True, f"dispatched {workflow}@{ref} (suite={suite or '-'} profile={profile or '-'})"
    return False, f"dispatch failed: HTTP {resp.status_code} {resp.text[:200]}"
