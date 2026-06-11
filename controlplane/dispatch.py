"""Trigger a test run — development-period executor is GitHub Actions.

The control plane dispatches api-test.yml via the workflow_dispatch REST API
(docs/PLATFORM-PLAN.md §2.4); at the M4 cutover this module is swapped for a
same-host worker queue while the run records and UI stay unchanged.

Config (env):
  PLATFORM_GITHUB_TOKEN     PAT with `actions:write` (or GITHUB_TOKEN)
  PLATFORM_GITHUB_REPO      owner/repo
  PLATFORM_GITHUB_REF       branch to run on (default: main)
  PLATFORM_GITHUB_WORKFLOW  workflow file (default: api-test.yml)
"""
from __future__ import annotations

import os

import requests


def configured() -> bool:
    return bool((os.environ.get("PLATFORM_GITHUB_TOKEN") or os.environ.get("GITHUB_TOKEN"))
                and os.environ.get("PLATFORM_GITHUB_REPO"))


def dispatch_run(suite: str, profile: str = "") -> tuple[bool, str]:
    """Fire workflow_dispatch with the suite × profile inputs. Returns
    (ok, message); an unconfigured dispatcher records the run without firing
    so the UI keeps working in local development."""
    token = os.environ.get("PLATFORM_GITHUB_TOKEN") or os.environ.get("GITHUB_TOKEN")
    repo = os.environ.get("PLATFORM_GITHUB_REPO", "")
    if not (token and repo):
        return False, ("dispatch not configured (PLATFORM_GITHUB_TOKEN / "
                       "PLATFORM_GITHUB_REPO) — run recorded only")
    ref = os.environ.get("PLATFORM_GITHUB_REF", "main")
    workflow = os.environ.get("PLATFORM_GITHUB_WORKFLOW", "api-test.yml")
    inputs = {k: v for k, v in (("suite", suite), ("profile", profile)) if v}
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
