"""AI failure triage (PLATFORM-PLAN §4-B1) + summary notification (B2).

Post-run, asynchronous, never in the test hot path: reads the run's archived
observations from the snapshot, separates NEW fails from the tracked baseline
(data/baselines/known_issues.json), and asks Claude to classify each new fail
into the four buckets the manual HANDOFF triage used:

  environment      quota / WAF / transient infra — rerun or ignore
  spec_change      the API itself changed — catalog/scenario needs updating
  test_bug         our request body / scenario / capture is wrong
  real_regression  the API actually broke — report upstream

The result (one-paragraph summary + per-endpoint classification) is stored in
the DB, shown on the run page, and optionally pushed to a webhook.

Config (env):
  ANTHROPIC_API_KEY         enables triage (absent -> disabled, no error)
  PLATFORM_TRIAGE_MODEL     default claude-opus-4-8
  PLATFORM_AUTO_TRIAGE      'true' -> triage fires automatically when a run finishes
  PLATFORM_NOTIFY_WEBHOOK   optional URL; POSTs {"text": summary} after triage
"""
from __future__ import annotations

import json
import os
from pathlib import Path

from controlplane import db, snapshots

ROOT = Path(__file__).resolve().parent.parent
DEFAULT_MODEL = "claude-opus-4-8"
MAX_FAILS = 80          # cap the prompt; beyond this the run has bigger problems
CATEGORIES = ("environment", "spec_change", "test_bug", "real_regression", "unknown")

OUTPUT_SCHEMA = {
    "type": "object",
    "properties": {
        "summary": {
            "type": "string",
            "description": "한국어 한 단락 요약: 새 실패의 전반적 양상과 가장 시급한 조치."},
        "classifications": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "endpoint_key": {"type": "string"},
                    "category": {"type": "string", "enum": list(CATEGORIES)},
                    "reason": {"type": "string"},
                    "suggested_action": {"type": "string"},
                },
                "required": ["endpoint_key", "category", "reason", "suggested_action"],
                "additionalProperties": False,
            },
        },
    },
    "required": ["summary", "classifications"],
    "additionalProperties": False,
}


def enabled() -> bool:
    return bool(os.environ.get("ANTHROPIC_API_KEY"))


def _known_issue_keys() -> set[str]:
    try:
        data = json.loads((ROOT / "data" / "baselines" / "known_issues.json").read_text())
        return {i.get("key", "") for i in data.get("issues", [])}
    except (OSError, ValueError):
        return set()


def new_fails(gh_run_id: str) -> list[dict]:
    """The run's fail-category observations not muted by the baseline."""
    known = _known_issue_keys()
    fails = []
    for obs in snapshots.observations(gh_run_id):
        if obs.get("category") != "fail":
            continue
        if obs.get("endpoint_key") in known:
            continue
        fails.append(obs)
    return fails


def _system_prompt() -> str:
    base = (
        "You are the failure-triage agent of the SCP API Regression Test Platform. "
        "You receive the NEW failures (already filtered against the known-issues "
        "baseline) of one regression run against Samsung Cloud Platform's Open APIs, "
        "and classify each into: environment (quota caps such as the validated 5-VPC "
        "limit, WAF blocks, transient infra, auth/HMAC hiccups), spec_change (the API "
        "surface changed; the catalog or scenario must be updated), test_bug (our "
        "request body, placeholder capture, polling or ordering is wrong), or "
        "real_regression (the API itself broke and must be reported upstream). "
        "Use 'unknown' only when the evidence truly cannot distinguish. Be specific "
        "in suggested_action (which file/scenario to fix, what to re-run, what to "
        "report). Write reason/suggested_action/summary in Korean.")
    # reuse the regression agent's role definition for domain conventions
    try:
        agent_md = (ROOT / "agents" / "regression-agent.md").read_text()
        base += "\n\n--- regression agent role (repo conventions) ---\n" + agent_md[:4000]
    except OSError:
        pass
    return base


def run_triage(gh_run_id: str) -> dict | None:
    """Classify the run's new fails with Claude and store the result.

    Returns the stored result dict, or None when triage is disabled / there is
    nothing to triage / the snapshot is unavailable."""
    if not enabled():
        return None
    fails = new_fails(gh_run_id)
    meta = snapshots.meta(gh_run_id) or {}
    if not fails:
        result = {"summary": "새로운 실패 없음 — baseline 외 fail이 발견되지 않았습니다.",
                  "classifications": []}
        db.set_triage(gh_run_id, "none-needed", result["summary"], json.dumps(result))
        return result

    lines = []
    for obs in fails[:MAX_FAILS]:
        lines.append(json.dumps({
            "endpoint_key": obs.get("endpoint_key"),
            "method": obs.get("method"), "path": obs.get("path"),
            "status": obs.get("status"), "source": obs.get("source"),
            "detail": str(obs.get("detail", ""))[:300],
        }, ensure_ascii=False))
    user = (f"Run {gh_run_id} (suite={meta.get('suite', '?')}, "
            f"profile={meta.get('profile', '?')}, region={meta.get('region', '?')}, "
            f"catalog_sha={str(meta.get('catalog', {}).get('sha256', ''))[:12]}).\n"
            f"NEW failures ({len(fails)} total, showing {min(len(fails), MAX_FAILS)}):\n"
            + "\n".join(lines))

    import anthropic
    client = anthropic.Anthropic()
    model = os.environ.get("PLATFORM_TRIAGE_MODEL", DEFAULT_MODEL)
    response = client.messages.create(
        model=model,
        max_tokens=16000,
        thinking={"type": "adaptive"},
        system=_system_prompt(),
        output_config={"format": {"type": "json_schema", "schema": OUTPUT_SCHEMA}},
        messages=[{"role": "user", "content": user}],
    )
    if response.stop_reason == "refusal":
        db.set_triage(gh_run_id, model, "triage refused by safety classifier", "{}")
        return None
    text = next(b.text for b in response.content if b.type == "text")
    result = json.loads(text)
    db.set_triage(gh_run_id, model, result.get("summary", ""),
                  json.dumps(result, ensure_ascii=False))
    notify(f"[SCP API Test] run {gh_run_id} triage — 신규 실패 {len(fails)}건\n"
           + result.get("summary", ""))
    return result


def auto_triage(gh_run_id: str) -> None:
    """Fire-and-forget post-run hook (PLATFORM_AUTO_TRIAGE=true)."""
    if os.environ.get("PLATFORM_AUTO_TRIAGE", "").strip().lower() != "true":
        return
    import threading

    def _job():
        try:
            run_triage(gh_run_id)
        except Exception as exc:
            print(f"[triage] run {gh_run_id} failed: {exc}")

    threading.Thread(target=_job, name=f"triage-{gh_run_id}", daemon=True).start()


def notify(text: str) -> None:
    """Best-effort webhook notification (B2). Slack-compatible {'text': ...}."""
    url = os.environ.get("PLATFORM_NOTIFY_WEBHOOK", "").strip()
    if not url:
        return
    try:
        import requests
        requests.post(url, json={"text": text[:3500]}, timeout=10)
    except Exception as exc:
        print(f"[notify] webhook failed: {exc}")
