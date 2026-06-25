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
from typing import Any, Callable, Iterable, Mapping, Sequence


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
