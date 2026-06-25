"""Canonical live-event contract — one vocabulary the live-DAG view renders,
regardless of which telemetry channel produced the event.

The engine has two telemetry channels **by design** (different transports — not a
mistake to be merged into one):

  - ``core.console_events`` — LOCAL fine-grained JSONL, one line per lifecycle
    step / API call, in DAG order. Gated by ``SCP_CONSOLE_EVENTS``; written by the
    engine during a local run (and by the simulate worker). This is the "which API
    is being tested *right now*" stream that the local console paints.
  - ``core.oplog`` — CLOUD channel (S3 bucket + ``/api/ingest/events`` mirror to
    the control plane). Coarser: run **milestones** (``emit``) plus per-resource
    create/delete events (``emit_resource``). This is the CI / production stream
    the control plane already ingests (today it only folds milestones).

The two were shaped independently, so a single consumer cannot render both without
an adapter:

  * ``kind`` is overloaded — console_events uses it as the EVENT type
    (``step-start`` …); an oplog resource event uses it as the RESOURCE type
    (``vpcs``) and puts the event verb in ``action``.
  * ``ts`` differs — console_events is a float epoch; oplog is an ISO-8601 string.
  * granularity differs — step-level vs milestone / resource-level.

**This module is the seam (S1a).** The console_events vocabulary IS the canonical
shape (so the hot local path needs ~no transform); :func:`normalize_oplog` maps the
cloud channel into it, and :func:`lifecycle_states` folds a normalized stream into
the per-lifecycle live state the graph overlay paints. No engine telemetry is
changed — this is a pure consumer-side adapter, so it is safe and offline-testable.
"""
from __future__ import annotations

import calendar
import time
from typing import Any, Iterable

# --- canonical ``kind`` vocabulary -------------------------------------------
# console_events is the reference; the cloud channel normalizes INTO this.
# ``milestone`` has no fine-grained twin — it is the coarse run-level marker that
# only the cloud channel emits.
RUN_META = "run-meta"
WAVE_START = "wave-start"
LIFECYCLE_START = "lifecycle-start"
STEP_START = "step-start"
STEP_END = "step-end"
RESOURCE_TRACKED = "resource-tracked"
RESOURCE_DELETED = "resource-deleted"
LIFECYCLE_END = "lifecycle-end"
RUN_END = "run-end"
MILESTONE = "milestone"

CANONICAL_KINDS = frozenset({
    RUN_META, WAVE_START, LIFECYCLE_START, STEP_START, STEP_END,
    RESOURCE_TRACKED, RESOURCE_DELETED, LIFECYCLE_END, RUN_END, MILESTONE,
})

# --- per-lifecycle live state the graph overlay paints -----------------------
QUEUED = "queued"        # implicit default (lifecycle not yet seen)
RUNNING = "running"
DONE = "done"
FAIL = "fail"

_PASS_WORDS = {"passed", "pass", "ok", "done", "success", "succeeded"}


def to_epoch(ts: Any) -> float:
    """Normalize a timestamp to float epoch **seconds**.

    Accepts a float/int epoch (console_events) or an ISO-8601 ``…Z`` UTC string
    (oplog, e.g. ``2026-06-25T11:02:03Z``). Returns ``0.0`` on anything unparseable
    so a junk timestamp never breaks a live view.
    """
    if isinstance(ts, bool):                      # bool is an int subclass — reject
        return 0.0
    if isinstance(ts, (int, float)):
        return float(ts)
    if isinstance(ts, str) and ts:
        try:
            return float(calendar.timegm(time.strptime(ts, "%Y-%m-%dT%H:%M:%SZ")))
        except Exception:
            try:
                return float(ts)                  # tolerate a stringified epoch
            except Exception:
                return 0.0
    return 0.0


def normalize_console(ev: dict) -> dict:
    """A console_events line is already canonical — return a copy with ``ts``
    coerced to float and a ``kind`` guaranteed. Never mutates the input."""
    out = dict(ev or {})
    out["ts"] = to_epoch(out.get("ts", 0.0))
    out.setdefault("kind", "")
    return out


def normalize_oplog(payload: dict) -> list[dict]:
    """Map one control-plane mirror payload (the ``/api/ingest/events`` body,
    i.e. ``{kind: 'milestone'|'resources', run_id, ...}``) into zero or more
    canonical events.

      * ``milestone``  -> one ``{kind:'milestone', stage, status, detail, job,
        run_id, ts}``.
      * ``resources``  -> one canonical resource event per batched item, with the
        overloaded ``kind`` (the resource TYPE) renamed to ``resource_kind`` and
        the verb in ``action`` mapped to ``resource-deleted`` / ``resource-tracked``.
        The raw ``action`` is preserved so nothing is lost.
    """
    if not isinstance(payload, dict):
        return []
    env = payload.get("kind")
    run_id = payload.get("run_id") or payload.get("gh_run_id") or ""
    if env == "milestone":
        return [{
            "kind": MILESTONE,
            "ts": to_epoch(payload.get("ts")),
            "run_id": run_id,
            "stage": payload.get("stage", ""),
            "status": payload.get("status", ""),
            "detail": payload.get("detail", ""),
            "job": payload.get("job", ""),
        }]
    if env == "resources":
        out: list[dict] = []
        for e in (payload.get("events") or []):
            if not isinstance(e, dict):
                continue
            action = str(e.get("action", "") or "")
            kind = RESOURCE_DELETED if action.startswith("delete") else RESOURCE_TRACKED
            raw_ts = e.get("ts")
            # oplog resource events carry BOTH ts (ISO seconds) and t (epoch ms);
            # prefer the ISO seconds, fall back to t/1000 — never treat ms as s.
            ts = to_epoch(raw_ts) if raw_ts else (float(e.get("t") or 0) / 1000.0)
            out.append({
                "kind": kind,
                "ts": ts,
                "run_id": run_id,
                "action": action,
                "resource_kind": e.get("kind", ""),   # the overloaded field, renamed
                "res_id": e.get("res_id", ""),
                "service": e.get("service", ""),
                "name": e.get("name", ""),
                "lifecycle": e.get("lifecycle", ""),
                "status": e.get("status", ""),
                "parent": e.get("parent", ""),
            })
        return out
    return []


def normalize(raw: dict, source: str) -> list[dict]:
    """Dispatch to the right normalizer. ``source='console'`` -> one canonical
    event; ``source='oplog'`` -> the mapped list; unknown source -> ``[]``."""
    if source == "console":
        return [normalize_console(raw)]
    if source == "oplog":
        return normalize_oplog(raw)
    return []


def lifecycle_states(events: Iterable[dict]) -> dict:
    """Fold a stream of **canonical** events into ``{lifecycle_id: state}`` for the
    graph overlay. Order-sensitive (replay in arrival order); ``FAIL`` is sticky so
    one bad step is not overwritten by a later "running":

      lifecycle-start                          -> running
      step-end (category error/fail)           -> fail (sticky)
      step-end (any other classified step)     -> running — category beats raw status,
                                                  so a "soft" 404 (GET-after-delete) is NOT a fail
      step-end (no category, raw status >= 400) -> fail (fallback only)
      lifecycle-end (passed-ish status)        -> done (unless already fail)
      lifecycle-end (any other status)         -> fail

    A lifecycle never seen stays absent (the UI renders that as ``queued``).
    """
    st: dict[str, str] = {}
    for ev in events or []:
        if not isinstance(ev, dict):
            continue
        k = ev.get("kind")
        lc = ev.get("lifecycle")
        if not lc:
            continue
        if k == LIFECYCLE_START:
            if st.get(lc) != FAIL:
                st[lc] = RUNNING
        elif k == STEP_END:
            cat = str(ev.get("category", "")).lower()
            status = ev.get("status")
            if cat in ("error", "fail", "failed"):
                st[lc] = FAIL
            elif cat:
                # a classified non-error step (ok / soft / warn …) never fails the
                # lifecycle — the engine's category is authoritative over the raw
                # status, so a "soft" 404 (GET-after-delete) is expected, not a fail
                if st.get(lc) != FAIL:
                    st[lc] = RUNNING
            elif isinstance(status, int) and not isinstance(status, bool) and status >= 400:
                st[lc] = FAIL                      # no category to trust → raw status
            elif st.get(lc) != FAIL:
                st[lc] = RUNNING
        elif k == LIFECYCLE_END:
            if str(ev.get("status", "")).lower() in _PASS_WORDS:
                if st.get(lc) != FAIL:
                    st[lc] = DONE
            else:
                st[lc] = FAIL
    return st
