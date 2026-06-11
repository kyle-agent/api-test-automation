"""Platform command channel — engine-side polling client (M2, PLATFORM-PLAN §2.5).

The control plane cannot push to a GitHub Actions runner (no inbound network),
so the engine PULLS pending intervention commands from the platform server at
safe checkpoints and acknowledges each one as soon as it acts on it:

  GET  {APITEST_PLATFORM_URL}/api/runs/{run_id}/commands
       -> {"commands": [{"id": 1, "action": "abort_run"},
                        {"id": 2, "action": "skip_scenario", "target": "<lc-id>"},
                        {"id": 3, "action": "stop_polling",  "target": "<lc-id or empty>"}]}
       (the server returns only commands not yet acknowledged)
  POST {APITEST_PLATFORM_URL}/api/commands/{id}/ack   -> {"ok": true}

Same defensive posture as :mod:`core.oplog`: everything is disabled unless
``APITEST_PLATFORM_URL`` is set, every failure silently means "no commands"
(one printed notice on first failure, then quiet), and nothing here may EVER
raise into — or stall — a test run. HTTP polls are throttled to at most one
per :data:`_POLL_INTERVAL` seconds per process (the result is cached between
polls), so calling the predicates at every step boundary / poll iteration is
cheap. stdlib-only (urllib), matching oplog's platform mirror.

Unlike oplog (whose CLI subcommands run in workflow steps that export env via
$GITHUB_ENV mid-job), the engine runs in one pytest process started AFTER the
env is exported — so the enable flag is read once at import, keeping the
disabled hot path to a single boolean check (no clock reads, no env lookups).

Semantics of the predicates (what the engine calls at checkpoints):
  * ``should_abort_run()``  — sticky per process: once an abort command is
    seen (and acked), every later checkpoint in this process keeps returning
    True, so all remaining lifecycles skip even though the server no longer
    re-sends the acked command.
  * ``should_skip(lifecycle_id)`` — True once for the matching command, which
    is consumed (acked + removed from the cache) so a later re-poll cannot
    re-apply it.
  * ``should_stop_polling(lifecycle_id)`` — matches a stop_polling command
    whose target is this lifecycle OR empty (= whatever is polling right now);
    consumed on first True.
"""
from __future__ import annotations

import json
import os
import time

_BASE_URL = os.getenv("APITEST_PLATFORM_URL", "").strip().rstrip("/")
_ENABLED = bool(_BASE_URL)

_POLL_INTERVAL = 10.0   # at most one HTTP poll per process per this many seconds
_TIMEOUT = 3.0          # a slow platform must never stall a step boundary

_NOTICE_SHOWN = False
_last_poll: float | None = None   # monotonic time of the last HTTP poll
_pending: list[dict] = []         # last fetched commands, minus consumed ones
_consumed: set = set()            # ids acted on — never re-applied even if the
                                  # server re-sends them (e.g. the ack got lost)
_abort = False                    # sticky abort flag (see module docstring)


def _run_id() -> str:
    # same resolution as core.oplog._run_id so both channels agree on the run
    return os.getenv("APITEST_RUN_ID") or os.getenv("GITHUB_RUN_ID") or "local"


def _http_json(method: str, url: str):
    """One HTTP round-trip returning parsed JSON, or None on ANY failure."""
    global _NOTICE_SHOWN
    try:
        import urllib.request
        headers = {}
        token = os.getenv("APITEST_PLATFORM_TOKEN", "").strip()
        if token:
            headers["Authorization"] = f"Bearer {token}"
        req = urllib.request.Request(url, headers=headers, method=method)
        with urllib.request.urlopen(req, timeout=_TIMEOUT) as resp:
            return json.loads(resp.read().decode() or "{}")
    except Exception as exc:
        if not _NOTICE_SHOWN:
            print(f"[commands] platform command channel unavailable ({exc}) "
                  f"— continuing without it")
            _NOTICE_SHOWN = True
        return None


def check() -> list[dict]:
    """Return pending (unconsumed) commands, polling the server at most once
    per ``_POLL_INTERVAL`` seconds; between polls the cached list is served."""
    global _last_poll, _pending
    if not _ENABLED:
        return []
    now = time.monotonic()
    if _last_poll is None or now - _last_poll >= _POLL_INTERVAL:
        _last_poll = now
        data = _http_json("GET", f"{_BASE_URL}/api/runs/{_run_id()}/commands")
        if isinstance(data, dict) and isinstance(data.get("commands"), list):
            _pending = [c for c in data["commands"]
                        if isinstance(c, dict) and c.get("id") not in _consumed]
        # on fetch failure the previous cache stands — a stale pending command
        # is still actionable, and the next successful poll refreshes the list
    return list(_pending)


def ack(command_id) -> None:
    """Acknowledge a command (best-effort) so re-polls stop returning it."""
    if not _ENABLED:
        return
    _http_json("POST", f"{_BASE_URL}/api/commands/{command_id}/ack")


def _consume(cmd: dict) -> None:
    """Mark a command acted-on: ack it and drop it from the local cache."""
    cid = cmd.get("id")
    if cid is not None:
        _consumed.add(cid)
        ack(cid)
    try:
        _pending.remove(cmd)
    except ValueError:
        pass


def should_abort_run() -> bool:
    global _abort
    if not _ENABLED:
        return False
    if _abort:
        return True
    for cmd in check():
        if cmd.get("action") == "abort_run":
            _abort = True
            _consume(cmd)
            return True
    return False


def should_skip(lifecycle_id: str) -> bool:
    if not _ENABLED:
        return False
    for cmd in check():
        if (cmd.get("action") == "skip_scenario"
                and cmd.get("target") == lifecycle_id):
            _consume(cmd)
            return True
    return False


def should_stop_polling(lifecycle_id: str = "") -> bool:
    if not _ENABLED:
        return False
    for cmd in check():
        if cmd.get("action") != "stop_polling":
            continue
        target = cmd.get("target") or ""
        if not target or target == lifecycle_id:
            _consume(cmd)
            return True
    return False
