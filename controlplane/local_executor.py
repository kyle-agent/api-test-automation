"""In-process ``local`` executor (convergence S2).

Runs a selection's **simulate** (later: live) pipeline ON the control-plane host
via ``regression.scenarios.local_run``, streaming the canonical console-event
vocabulary to a per-run JSONL file. :func:`read_events` normalizes that file
through ``core.events_contract`` (S1a) and folds it to per-lifecycle states for the
live DAG overlay.

This is console2's local execution brought into the spine. It has **no web-framework
dependency** — the FastAPI routes in ``app.py`` are thin wrappers over these
functions, so the executor is unit-testable without a running server (and without
fastapi installed). Concurrent runs are isolated by per-run file (no shared global
event sink), so two local simulates never interleave.
"""
from __future__ import annotations

import itertools
import json
import threading
import time
from pathlib import Path

from core import events_contract
from regression.scenarios import local_run

ROOT = Path(__file__).resolve().parent.parent
RUN_DIR = ROOT / "reports" / "controlplane-local"        # gitignored
_RUNS: dict[str, dict] = {}
_LOCK = threading.Lock()
_SEQ = itertools.count(1)        # disambiguates ids when two runs start in the same ms

_PUBLIC_KEYS = ("id", "mode", "status", "lifecycle_ids", "runnable",
                "started", "ended", "error", "rc", "gates")


def _append(evpath: str, evkind: str, **fields) -> None:
    """Append one canonical console-event line (same shape as ``core.console_events``)
    to a run's file. ``evpath``/``evkind`` are deliberately NOT named ``path``/``kind``
    so an event's own ``path`` field can't collide with the positional arg. Append-mode
    write so a concurrent reader/poll never tears it."""
    rec = {"ts": round(time.time(), 3), "kind": evkind, **fields}
    with open(evpath, "a", encoding="utf-8") as fh:
        fh.write(json.dumps(rec, ensure_ascii=False, default=str) + "\n")


def _public(rec: dict) -> dict:
    """Run record minus internals (the thread handle / file path)."""
    return {k: rec.get(k) for k in _PUBLIC_KEYS}


def start_simulate(lifecycle_ids, *, step_delay: float = 0.0, sleep=None) -> dict:
    """Start a SIMULATE run in a daemon thread; return the run record immediately.
    Events stream to the per-run file as the replay advances — poll :func:`read_events`
    for the live view. ``step_delay``>0 paces it watchably (defaults to real sleep)."""
    if sleep is None and step_delay > 0:
        sleep = time.sleep                                # pace a watchable live run
    RUN_DIR.mkdir(parents=True, exist_ok=True)
    run_id = "local-%d-%d" % (int(time.time() * 1000), next(_SEQ))
    evp = str(RUN_DIR / f"{run_id}.jsonl")
    open(evp, "w").close()                                # truncate / create
    rec = {"id": run_id, "mode": "simulate", "status": "running",
           "lifecycle_ids": list(lifecycle_ids), "runnable": [], "events_path": evp,
           "started": time.time(), "ended": None, "error": None}
    with _LOCK:
        _RUNS[run_id] = rec

    def _worker():
        try:
            plan = local_run.build_plan(lifecycle_ids)
            rec["runnable"] = plan["runnable"]
            local_run.simulate_run(
                plan["waves"], plan["preview"],
                lambda kind, **f: _append(evp, kind, **f),
                step_delay=step_delay, sleep=sleep,
                meta={"runnable": plan["runnable"]})
            with _LOCK:
                rec["status"], rec["ended"] = "done", time.time()
        except Exception as exc:                          # surface; never crash the host
            try:
                _append(evp, "run-end", status="error", error=str(exc))
            except Exception:
                pass
            with _LOCK:
                rec["status"], rec["ended"], rec["error"] = "error", time.time(), str(exc)

    t = threading.Thread(target=_worker, daemon=True)
    rec["_thread"] = t
    t.start()
    return _public(rec)


def start_live(lifecycle_ids, *, mutations: bool = True, destructive: bool = True,
               heavy: bool = False, parallel=None) -> dict:
    """Start a LIVE run in a daemon thread — REAL cloud calls (needs SCP creds + egress).
    Runs ``regression.scenarios.local_run.live_run`` (provision→pytest→teardown); the
    engine streams the SAME fine console-events to the per-run file, so the live DAG
    view is identical to simulate. Gates are EXPLICIT (caller opt-in); ``heavy`` stays
    off unless the caller sets it (Hard Rule 1). The raw pytest log goes to a sibling
    ``.log``. Returns the run record immediately; poll :func:`read_events`."""
    RUN_DIR.mkdir(parents=True, exist_ok=True)
    run_id = "local-%d-%d" % (int(time.time() * 1000), next(_SEQ))
    evp = str(RUN_DIR / f"{run_id}.jsonl")
    logp = str(RUN_DIR / f"{run_id}.log")
    open(evp, "w").close()
    rec = {"id": run_id, "mode": "live", "status": "running",
           "lifecycle_ids": list(lifecycle_ids), "runnable": list(lifecycle_ids),
           "events_path": evp, "log_path": logp, "rc": None,
           "gates": {"mutations": mutations, "destructive": destructive, "heavy": heavy},
           "started": time.time(), "ended": None, "error": None}
    with _LOCK:
        _RUNS[run_id] = rec

    def _worker():
        try:
            res = local_run.live_run(lifecycle_ids, evp, logp, mutations=mutations,
                                     destructive=destructive, heavy=heavy, parallel=parallel)
            with _LOCK:
                rec["rc"] = res.get("rc")
                rec["runner_missing"] = res.get("runner_missing")
                rec["status"] = "done" if res.get("rc") == 0 else "fail"
                rec["ended"] = time.time()
        except Exception as exc:                          # surface; never crash the host
            with _LOCK:
                rec["status"], rec["ended"], rec["error"] = "error", time.time(), str(exc)

    t = threading.Thread(target=_worker, daemon=True)
    rec["_thread"] = t
    t.start()
    return _public(rec)


def join(run_id: str, timeout: float = 15.0) -> dict | None:
    """Block until a run's worker finishes — for tests / synchronous callers."""
    rec = _RUNS.get(run_id)
    if not rec:
        return None
    t = rec.get("_thread")
    if t:
        t.join(timeout)
    return _public(rec)


def get(run_id: str) -> dict | None:
    rec = _RUNS.get(run_id)
    return _public(rec) if rec else None


def list_runs() -> list[dict]:
    with _LOCK:
        recs = sorted(_RUNS.values(), key=lambda r: r["started"], reverse=True)
    return [_public(r) for r in recs]


def read_events(run_id: str) -> dict | None:
    """The fine live view: read the per-run console-event file, normalize each line
    through the S1a contract, and fold to per-lifecycle states for the graph overlay.
    Safe to poll mid-run (append-only file). Returns ``None`` for an unknown run."""
    rec = _RUNS.get(run_id)
    if not rec:
        return None
    raw: list[dict] = []
    try:
        with open(rec["events_path"], encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                try:
                    raw.append(json.loads(line))
                except Exception:
                    pass                                  # torn line under concurrent append
    except FileNotFoundError:
        pass
    norm = [ev for r in raw for ev in events_contract.normalize(r, "console")]
    return {"run": _public(rec), "events": norm,
            "states": events_contract.lifecycle_states(norm)}


# --- account-hygiene utilities (force cleanup / verify) ----------------------
def _start_util(mode: str, fn) -> dict:
    """Run a log-producing utility (cleanup/verify) in a daemon thread."""
    RUN_DIR.mkdir(parents=True, exist_ok=True)
    run_id = "local-%d-%d" % (int(time.time() * 1000), next(_SEQ))
    logp = str(RUN_DIR / f"{run_id}.log")
    rec = {"id": run_id, "mode": mode, "status": "running", "lifecycle_ids": [],
           "runnable": [], "log_path": logp, "rc": None, "started": time.time(),
           "ended": None, "error": None}
    with _LOCK:
        _RUNS[run_id] = rec

    def _worker():
        try:
            res = fn(logp)
            with _LOCK:
                rec["rc"] = res.get("rc"); rec["status"] = "done"; rec["ended"] = time.time()
        except Exception as exc:
            with _LOCK:
                rec["status"], rec["ended"], rec["error"] = "error", time.time(), str(exc)

    t = threading.Thread(target=_worker, daemon=True)
    rec["_thread"] = t
    t.start()
    return _public(rec)


def start_cleanup() -> dict:
    """FORCE reconciler sweep (destructive — owner-tagged only). Operator opt-in."""
    return _start_util("cleanup", local_run.cleanup_sweep)


def start_verify() -> dict:
    """Read-only owned-resource inventory (no deletes)."""
    return _start_util("verify", local_run.verify_clean)


def read_log(run_id: str) -> dict | None:
    """The raw log of a run (live pytest / cleanup / verify). Safe to poll mid-run."""
    rec = _RUNS.get(run_id)
    if not rec:
        return None
    lp = rec.get("log_path")
    log = ""
    if lp:
        try:
            with open(lp, encoding="utf-8") as fh:
                log = fh.read()
        except Exception:
            pass
    return {"run": _public(rec), "log": log}
