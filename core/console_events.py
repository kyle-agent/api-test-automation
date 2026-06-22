"""Optional **local** structured event stream for the console2 live view.

The engine already emits resource events to the ops-log (S3 → ``ops.html``), which
needs cloud egress and is the right channel for CI / production. console2 is a
*local* operator console: a developer runs it on a machine with SCP creds and
wants to watch a run unfold **step by step, in DAG order, "which API is being
tested right now"** — without depending on Object Storage.

This module is that local channel: a tiny append-only JSONL sink, **gated by the
``SCP_CONSOLE_EVENTS`` env var**. When the var is unset (the default, incl. every
CI run and every non-console invocation) ``emit()`` is a single ``os.environ.get``
and returns immediately — **zero behaviour change, zero cost**. When set to a file
path, the engine appends one JSON object per line:

    {"ts": 1.7e9, "kind": "lifecycle-start", "lifecycle": "networking-vpc-subnet", "service": "vpc"}
    {"ts": 1.7e9, "kind": "step-start", "lifecycle": "...", "step": "create-vpc", "method": "POST", "path": "/v1/vpcs"}
    {"ts": 1.7e9, "kind": "step-end",   "lifecycle": "...", "step": "create-vpc", "status": 202, "category": "ok", "elapsed_ms": 812}
    {"ts": 1.7e9, "kind": "lifecycle-end", "lifecycle": "...", "status": "passed"}

``console2_server`` sets the env var to a per-run file, then tails it for the UI.
A simulate run writes the *same* shape from the server (no engine), so the live
view is identical whether the run is real or a dry-run.

Concurrency: writes are a single ``f.write(line)`` in ``O_APPEND`` mode. On Linux
a write below ``PIPE_BUF`` (4096 bytes) is atomic, so the many short lines from
parallel pytest-xdist worker processes interleave cleanly without a lock. A line
that would exceed that is still safe enough for a local view (worst case: one
garbled line the reader skips). Telemetry never raises into the engine.
"""
from __future__ import annotations

import json
import os
import time

ENV = "SCP_CONSOLE_EVENTS"


def enabled() -> bool:
    """True iff a sink path is configured (so callers can skip building payloads)."""
    return bool(os.environ.get(ENV))


def emit(kind: str, **fields) -> None:
    """Append one ``{ts, kind, **fields}`` JSON line to the sink. No-op + cheap
    when ``SCP_CONSOLE_EVENTS`` is unset. Never raises (telemetry must not break
    a run)."""
    path = os.environ.get(ENV)
    if not path:
        return
    try:
        rec = {"ts": round(time.time(), 3), "kind": kind}
        rec.update(fields)
        line = json.dumps(rec, ensure_ascii=False, default=str) + "\n"
        with open(path, "a", encoding="utf-8") as fh:
            fh.write(line)
    except Exception:          # pragma: no cover — telemetry is best-effort only
        pass
