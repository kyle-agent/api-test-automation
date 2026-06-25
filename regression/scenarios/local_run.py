"""Shared local-run pipeline for the `local` PLATFORM_EXECUTOR (convergence S2).

The original local execution console (`tools/console2_server.py`) runs a selection
locally — **simulate** (replay the dag_planner plan to the live-event stream, no
cloud) or **live** (provision shared VPC → `pytest tests/crud` → teardown). The
convergence brings that same capability into the control plane as a third executor
(`local`, alongside `actions`/`worker`). To avoid two copies of the run loop, the
reusable *logic* lives here and both callers wire it to their own run-record /
event sink.

This module owns the **simulate replay** as a pure, deterministic function:
:func:`simulate_run` walks the plan and calls an injected ``emit(kind, **fields)``
sink using the canonical console-event vocabulary (see ``core.console_events`` /
``core.events_contract``). No globals, no I/O, no cloud — ``sleep``/``new_id`` are
injectable so tests run instantly and reproducibly. The caller supplies the plan
(from ``dag_planner``) and an ``emit`` that appends to its event stream
(``core.console_events.emit`` for a real run, a list for a test).

The **live** pipeline (provision→pytest→teardown) is the next slice; it stays in
``console2_server`` until the control-plane executor is wired, then moves here too.
"""
from __future__ import annotations

import itertools
import os
import re
import subprocess
import sys
from pathlib import Path
from typing import Any, Callable, Iterable, Mapping, Sequence

_ROOT = Path(__file__).resolve().parents[2]      # repo root (regression/scenarios/..)


def resource_type(path: str) -> str:
    """Coarse resource type from a step path — the first non-version, non-template
    segment. ``/v1/vpcs/{vpc_id}`` → ``vpcs`` · ``/v1/queues`` → ``queues``. Plural
    (the raw collection segment); mirrors ``console2_server._sim_resource_type`` so
    the simulate resource view labels identically."""
    for seg in (path or "").strip("/").split("/"):
        if not seg or seg.startswith("{"):
            continue
        if seg.startswith("v") and len(seg) > 1 and seg[1].isdigit():   # v1, v2, v1.1, v2025-01
            continue
        return seg
    return "resource"


def simulate_run(
    waves: Sequence[Mapping[str, Any]],
    preview: Mapping[str, Mapping[str, Any]],
    emit: Callable[..., None],
    *,
    step_delay: float = 0.0,
    beat: float = 0.0,
    sleep: Callable[[float], None] | None = None,
    new_id: Callable[[], str] | None = None,
    meta: Mapping[str, Any] | None = None,
) -> None:
    """Replay a dag_planner plan to the canonical console-event vocabulary — a DRY
    RUN with **no cloud calls**. Walks the waves in DAG order and, within each wave,
    each lifecycle's HTTP steps, so a live view shows the real creation order + API
    sequence. Emits synthetic ``resource-tracked`` / ``resource-deleted`` (ids
    prefixed ``sim-``) on create/delete steps so a resource view renders too.

    Args:
        waves:   ``[{kind, lifecycles:[lid], vpc_slots}]`` — ``dag_planner`` plan waves.
        preview: ``{lid: {service, heavy, steps:[{name, method, path, kind}]}}`` —
                 each lifecycle's HTTP step list (steps without ``method`` are skipped).
        emit:    sink called ``emit(kind, **fields)`` for every event (the ONLY output).
        step_delay/beat: optional pacing (seconds) so a live view is watchable; 0 = instant.
        sleep:   injectable sleeper (default no-op) — pass ``time.sleep`` for real pacing.
        new_id:  injectable synthetic-id generator (default a deterministic counter).
        meta:    extra fields merged into the ``run-meta`` event (e.g. ``{"runnable": [...]}``).
    """
    sleep = sleep or (lambda _s: None)
    if new_id is None:
        _ctr = itertools.count(1)
        new_id = lambda: "sim-%08x" % next(_ctr)   # noqa: E731 — tiny local default

    waves = list(waves or [])
    emit("run-meta", mode="simulate", waves=len(waves), **(dict(meta) if meta else {}))
    for wi, w in enumerate(waves):
        emit("wave-start", wave=wi, wave_kind=w.get("kind", ""),
             lifecycles=list(w.get("lifecycles", [])), vpc_slots=w.get("vpc_slots", 0))
        for lid in w.get("lifecycles", []):
            pv = preview.get(lid) or {"steps": [], "service": "", "heavy": False}
            steps = [s for s in pv.get("steps", []) if s.get("method")]   # HTTP steps only
            emit("lifecycle-start", lifecycle=lid, service=pv.get("service", ""),
                 heavy=pv.get("heavy", False), n_steps=len(steps), wave=wi)
            for s in steps:
                emit("step-start", lifecycle=lid, step=s["name"],
                     method=s["method"], path=s["path"])
                sleep(step_delay)
                emit("step-end", lifecycle=lid, step=s["name"],
                     method=s["method"], path=s["path"],
                     status=200, category="ok", elapsed_ms=int(step_delay * 1000))
                if s.get("kind") == "create":
                    emit("resource-tracked", lifecycle=lid,
                         resource_type=resource_type(s["path"]),
                         resource_id=new_id(), path=s["path"])
                    sleep(beat)
                elif s.get("kind") == "delete":
                    emit("resource-deleted", lifecycle=lid,
                         resource_type=resource_type(s["path"]), path=s["path"])
                    sleep(beat)
            emit("lifecycle-end", lifecycle=lid, status="passed")
    emit("run-end", status="done")


def _step_kind(step: Mapping[str, Any]) -> str:
    """Coarse create/delete classification for the synthetic simulate resources, by
    HTTP method (POST→create, DELETE→delete); a wait/ready step is neither. Simpler
    than console2's predicate table — it only affects which steps emit a synthetic
    ``resource-tracked`` / ``-deleted`` in a dry run."""
    name = (step.get("name") or "").lower()
    if any(w in name for w in ("wait", "ready", "active")):
        return "wait"
    return {"POST": "create", "DELETE": "delete"}.get((step.get("method") or "").upper(), "step")


def build_plan(lifecycle_ids: Sequence[str]) -> dict:
    """Build the simulate inputs for a selection using ENGINE modules only — the same
    ``dag_planner`` schedule + per-lifecycle step preview console2's ``_plan`` produces,
    so the control-plane ``local`` executor is self-contained (no console2 import).

    Returns ``{waves, preview, runnable, skipped_disabled, leaf_set}`` — hand
    ``plan["waves"], plan["preview"]`` straight to :func:`simulate_run`.
    """
    from regression.scenarios import dag_planner, validate_dag
    from regression.scenarios.loader import load_lifecycles
    deps = validate_dag._load_deps()
    all_lcs = validate_dag._load_lifecycles()
    enabled = {lc["id"] for lc in all_lcs if lc.get("enabled")}
    requested = list(lifecycle_ids or [])
    runnable = [lid for lid in requested if lid in enabled]
    # leaf set = the runnable subset of the SELECTION; None (= all enabled) ONLY when
    # nothing was selected — never plan the whole platform for an all-disabled selection.
    leaf_set = runnable if requested else None
    p = dag_planner.plan(leaf_set=leaf_set, deps=deps, lifecycles=all_lcs)
    lcs, _ = load_lifecycles(with_sources=True)
    by_id = {lc["id"]: lc for lc in lcs}
    preview: dict[str, dict] = {}
    for lid in p.leaf_set:
        lc = by_id.get(lid, {})
        steps = [{"name": s.get("name", ""), "method": s.get("method"),
                  "path": s.get("path"), "kind": _step_kind(s)} for s in lc.get("steps", [])]
        preview[lid] = {"service": lc.get("service", ""), "heavy": bool(lc.get("heavy")),
                        "n_steps": len(steps), "steps": steps}
    return {"waves": p.to_dict()["waves"], "preview": preview, "runnable": runnable,
            "skipped_disabled": sorted(set(requested) - enabled), "leaf_set": list(p.leaf_set)}


# --------------------------------------------------------------------------- #
# live pipeline — the REAL run path (provision shared VPC -> pytest tests/crud ->
# precise teardown). Extracted from console2_server._run_worker so the control-plane
# `local` executor doesn't import the console2 dev server. Safety gates are EXPLICIT
# args (the caller's opt-in) — never defaulted on "to make a test pass" (Hard Rule 1).
# The engine emits fine console-events to events_path during pytest, so the live view
# is identical to simulate. Needs SCP creds + egress (real cloud calls).
# --------------------------------------------------------------------------- #
def provision_shared(env: dict, f) -> dict:
    """Provision ONE session-shared VPC+subnet so adopter lifecycles don't skip under
    ``-n``. Best-effort: on failure adopters self-skip and self-creators still run.
    ``f`` is the run's open log file. Returns the ``SCP_SHARED_*`` env to merge into
    the pytest env."""
    f.write("\n=== provision shared VPC (adopters need this under -n) ===\n")
    f.flush()
    out = subprocess.run([sys.executable, "-m", "regression.scenarios.shared_infra", "--provision"],
                         cwd=str(_ROOT), env=env, stdout=subprocess.PIPE,
                         stderr=subprocess.STDOUT, text=True)
    f.write(out.stdout or "")
    shared: dict = {}
    for line in (out.stdout or "").splitlines():
        if line.startswith("SCP_SHARED_") and "=" in line:
            k, _, v = line.partition("=")
            if v.strip():
                shared[k.strip()] = v.strip()
    if shared.get("SCP_SHARED_VPC_ID"):
        shared["SCP_VPC_SHARED_RESERVED"] = "1"
        f.write(f"\n[provision] shared VPC ready: {shared['SCP_SHARED_VPC_ID']}\n")
    else:
        f.write("\n[provision] no shared VPC id — adopters will skip (self-creators still run)\n")
    f.flush()
    return shared


def teardown_shared(env: dict, shared: dict, f) -> None:
    """Delete the session shared VPC precisely by id (no name-guessing — Hard Rule 3)."""
    if not shared.get("SCP_SHARED_VPC_ID"):
        return
    f.write("\n=== teardown shared VPC (precise, by id) ===\n")
    f.flush()
    subprocess.run([sys.executable, "-m", "regression.scenarios.shared_infra", "--teardown"],
                   cwd=str(_ROOT), env={**env, **shared}, stdout=f, stderr=subprocess.STDOUT)


def pytest_did_not_run(rc: int, pytest_out: str) -> bool:
    """True when the pytest runner itself never executed (e.g. pytest not installed) —
    so there are no results to trust AND nothing was created (skip teardown/sweep)."""
    low = (pytest_out or "").lower()
    if "no module named pytest" in low or "no module named 'pytest'" in low:
        return True
    has_outcome = bool(re.search(r"\d+\s+(passed|failed|skipped|error|xfailed|deselected)",
                                 pytest_out or ""))
    return rc in (3, 4) and not has_outcome


def live_run(lifecycle_ids, events_path: str, log_path: str, *, mutations: bool,
             destructive: bool, heavy: bool, parallel: int | None = None) -> dict:
    """REAL run: provision shared VPC (heavy only) → ``pytest tests/crud -m crud`` with
    ``SCP_CRUD_IDS`` + ``SCP_CONSOLE_EVENTS`` + the EXPLICIT safety gates → precise
    teardown. Per-run cleanup is teardown-scoped (the lifecycle deletes what it created);
    the account-wide reconciler sweep stays the manual 강제 클린업 (it can't scope to one
    run). Returns ``{rc, runner_missing}``; everything else surfaces in ``log_path``."""
    ids = list(lifecycle_ids)
    env = {**os.environ, "PYTHONPATH": str(_ROOT),
           "SCP_CRUD_IDS": ",".join(ids), "SCP_CONSOLE_EVENTS": events_path,
           "SCP_ALLOW_MUTATIONS": "true" if mutations else "false",
           "SCP_ALLOW_DESTRUCTIVE": "true" if destructive else "false",
           "SCP_RUN_HEAVY": "true" if heavy else "false"}
    n = str(parallel or max(1, min(6, len(ids) or 2)))
    with open(log_path, "w", encoding="utf-8") as f:
        f.write(f"# local live run  lifecycle_ids={ids}\n"
                f"# gates: mutations={mutations} destructive={destructive} heavy={heavy}  parallel={n}\n")
        f.flush()
        shared = provision_shared(env, f) if heavy else {}
        f.write("\n=== pytest ===\n")
        f.flush()
        pos = f.tell()
        rc = subprocess.run(
            [sys.executable, "-m", "pytest", "tests/crud", "-m", "crud",
             "-n", n, "-o", "addopts=", "-q"],
            cwd=str(_ROOT), env={**env, **shared}, stdout=f, stderr=subprocess.STDOUT).returncode
        f.flush()
        try:
            with open(log_path, encoding="utf-8") as rf:
                rf.seek(pos)
                pytest_out = rf.read()
        except Exception:
            pytest_out = ""
        runner_missing = pytest_did_not_run(rc, pytest_out)
        if runner_missing:
            f.write("\n⚠ pytest runner missing — no tests ran; skipping teardown/sweep "
                    "(nothing was created).\n")
        else:
            teardown_shared(env, shared, f)
            f.write("\n=== per-run cleanup: teardown-scoped ===\n"
                    "  this run's resources were deleted by the lifecycle teardown above.\n"
                    "  account-wide reaping = the manual 강제 클린업 (POST /api/cleanup).\n")
        f.flush()
    return {"rc": rc, "runner_missing": runner_missing}


def cleanup_sweep(log_path: str) -> dict:
    """FORCE account-wide reconciler sweep — delete ALL owner-tagged resources,
    ignoring TTL (DESTRUCTIVE). The explicit opt-in is the operator's button click;
    only OUR owner tag is reaped (Hard Rule 3 — no name-guessing). Writes ``log_path``."""
    env = {**os.environ, "PYTHONPATH": str(_ROOT), "SCP_ALLOW_MUTATIONS": "true",
           "SCP_ALLOW_DESTRUCTIVE": "true", "SCP_SWEEP_IGNORE_TTL": "true"}
    with open(log_path, "w", encoding="utf-8") as f:
        f.write("# FORCE cleanup — reconciler sweep (owner-tagged only, ignore TTL)\n\n")
        f.flush()
        rc = subprocess.run([sys.executable, "-m", "cleanup.reconciler"],
                            cwd=str(_ROOT), env=env, stdout=f, stderr=subprocess.STDOUT).returncode
    return {"rc": rc}


def verify_clean(log_path: str) -> dict:
    """Read-only owned-resource inventory (no deletes) — counts survivors. Writes log."""
    env = {**os.environ, "PYTHONPATH": str(_ROOT), "SCP_ALLOW_DESTRUCTIVE": "false"}
    with open(log_path, "w", encoding="utf-8") as f:
        f.write("# verify clean — owned inventory (read-only, no deletes)\n\n")
        f.flush()
        rc = subprocess.run([sys.executable, "-m", "cleanup.verify_clean"],
                            cwd=str(_ROOT), env=env, stdout=f, stderr=subprocess.STDOUT).returncode
    return {"rc": rc}
