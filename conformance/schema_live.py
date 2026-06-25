"""DATA-BASED schema-drift probe (AXIS 2 runtime, GATED — creates real resources).

Ported from ``tools/probe_schema_live.py``, adapted to the new kernel. Unlike the
other runtime probes (read-only / empty-body), this one CREATES real, billable
resources, so it is **opt-in and non-destructive by default**: it only runs when
the live gateway can be mutated *and* destroyed (teardown needs DELETE), and it
always tears its resources down in reverse order at the end and on failure.

Reuse, not reinvention: it drives the repo's proven CRUD lifecycle definitions
and step helpers from :mod:`regression.scenarios.engine`
(``LIFECYCLES`` + ``_fill`` / ``_fill_obj`` / ``_capture`` / ``_run_step``), so
valid create bodies and capture/cleanup logic are not duplicated here. For every
2xx GET/POST response it diffs the live body against the documented response
model (from ``data/api_docs.json``).

Gating (double-gated, exactly as the source): the caller must have
``SCP_PROBE_RUNTIME=true`` (enforced by :func:`conformance.runtime.main`) AND the
config must report ``allow_mutations`` (``SCP_ALLOW_MUTATIONS=true``) AND
``allow_destructive`` (``SCP_ALLOW_DESTRUCTIVE=true``). If either gate is unset
this probe records nothing and creates nothing.

Dual-write (legacy outputs kept): ``reports/runtime_schema_live.json`` +
``reports/csv/runtime_schema_live.csv`` (same shape as the source). In addition
every drift is emitted to the unified results store via
:func:`core.results.record_finding` (``source="runtime"``).

Importing this module performs **no** network I/O. All gateway calls happen only
inside :func:`probe_schema_live`, invoked from :func:`conformance.runtime.main`
after the env gates pass.
"""
from __future__ import annotations

import csv
import json
import re
import time
from pathlib import Path

from core.results import Finding, record_finding

ROOT = Path(__file__).resolve().parent.parent
DOCS = ROOT / "data" / "api_docs.json"
OUT = ROOT / "reports" / "runtime_schema_live.json"
CSV = ROOT / "reports" / "csv" / "runtime_schema_live.csv"


def _emit(endpoint_key: str, rule_id: str, severity: str, detail: str) -> None:
    record_finding(Finding(endpoint_key=endpoint_key, rule_id=rule_id,
                           severity=severity, detail=detail, source="runtime"))


def _endpoint_index(docs):
    """(method, service) -> list of (regex, endpoint) for path-template matching."""
    idx = {}
    for k, e in docs["endpoints"].items():
        if not e.get("path") or not e.get("method"):
            continue
        rx = re.compile("^" + re.sub(r"\{[^}]+\}", r"[^/]+", e["path"]) + "$")
        idx.setdefault((e["method"], e["service"]), []).append((rx, e))
    return idx


def _model_for(docs, e):
    ref = next((r.get("schema_ref") for r in e.get("responses", [])
                if str(r.get("code", "")).startswith("2") and r.get("schema_ref")), None)
    return docs["models"].get(f"{e['category']}/{e['service']}/{ref}") if ref else None


def _diff(docs, e, body):
    model = _model_for(docs, e)
    if not model or not isinstance(body, dict):
        return None
    mf = model.get("fields", [])
    mn = {f["name"] for f in mf}
    extra = sorted(set(body.keys()) - mn)
    missing = sorted({f["name"] for f in mf if f.get("required")} - set(body.keys()))
    item_model = item_extra = item_missing = ""
    for f in mf:
        if f.get("schema_ref") and f["name"] in body:
            val = body[f["name"]]
            item = val[0] if isinstance(val, list) and val else (val if isinstance(val, dict) else None)
            im = docs["models"].get(f"{e['category']}/{e['service']}/{f['schema_ref']}")
            if item and im:
                inm = {x["name"] for x in im.get("fields", [])}
                item_model = f["schema_ref"]
                item_extra = ",".join(sorted(set(item.keys()) - inm))
                item_missing = ",".join(sorted({x["name"] for x in im.get("fields", []) if x.get("required")} - set(item.keys())))
                break
    return {"model": model["name"], "undocumented_fields": ",".join(extra),
            "missing_required_fields": ",".join(missing), "item_model": item_model,
            "item_undocumented_fields": item_extra, "item_missing_required": item_missing}


def make_schema_diff_hook(docs):
    """Build a thread-safe ``(on_response, finalize)`` pair for schema-drift.

    ``on_response(lifecycle, step, path, service, resp)`` diffs every 2xx GET/POST
    response body against the documented model and emits a Finding per drift (same
    semantics as the read-only :func:`probe_schema`, but on bodies only visible
    AFTER a create). ``finalize(**meta)`` writes the legacy JSON+CSV artifacts and
    returns a summary. Shared by BOTH the serial :func:`probe_schema_live` and the
    DAG-orchestrated heavy run (``tools.dag_run_live --schema-diff``) so the diff
    logic lives in one place. Thread-safe: the DAG executor calls ``on_response``
    from several worker threads concurrently.
    """
    import threading
    idx = _endpoint_index(docs)
    rows: list = []
    drift_rows: list = []
    lock = threading.Lock()

    def on_response(lifecycle, step, path, service, resp):
        if step.get("method") not in ("GET", "POST"):
            return
        if not (200 <= resp.status < 300) or not isinstance(resp.body, dict):
            return
        match = next((e for rx, e in idx.get((step["method"], service), [])
                      if rx.match(path)), None)
        if not match:
            return
        ekey = f"{match['category']}/{match['service']}/{match['name']}"
        d = _diff(docs, match, resp.body)
        if not d:
            return
        row = {"lifecycle": lifecycle["id"], "step": step.get("name", ""),
               "endpoint": ekey, "method": step["method"], **d}
        miss = d["missing_required_fields"] or d["item_missing_required"]
        extra = d["undocumented_fields"] or d["item_undocumented_fields"]
        with lock:
            rows.append(row)
            if miss or extra:
                drift_rows.append(row)
                print(f"  DRIFT {ekey}: extra=[{d['undocumented_fields']}] "
                      f"missing=[{d['missing_required_fields']}] "
                      f"item_extra=[{d['item_undocumented_fields']}]")
                if miss:
                    _emit(ekey, "schema-live-missing-field", "red",
                          f"live (created) response omits documented required "
                          f"field(s): {miss}")
                elif extra:
                    _emit(ekey, "schema-live-undocumented-field", "yellow",
                          f"live (created) response has undocumented field(s): {extra}")

    def finalize(**meta):
        OUT.parent.mkdir(parents=True, exist_ok=True)
        payload = {"checked": len(rows), "with_drift": len(drift_rows),
                   "results": rows}
        payload.update(meta)
        OUT.write_text(json.dumps(payload, indent=2, ensure_ascii=False))
        CSV.parent.mkdir(parents=True, exist_ok=True)
        cols = ["lifecycle", "step", "endpoint", "method", "model",
                "undocumented_fields", "missing_required_fields", "item_model",
                "item_undocumented_fields", "item_missing_required"]
        with CSV.open("w", newline="", encoding="utf-8-sig") as fh:
            w = csv.DictWriter(fh, fieldnames=cols)
            w.writeheader()
            for r in rows:
                w.writerow({c: r.get(c, "") for c in cols})
        print(f"\n## data-based schema drift\n"
              f"- responses checked: {len(rows)}\n"
              f"- responses WITH drift: {len(drift_rows)}")
        print(f"_wrote {OUT} + {CSV}_")
        return {"checked": len(rows), "with_drift": len(drift_rows), "rows": rows}

    return on_response, finalize


def probe_schema_live(client, cfg, docs, *, filter_id: str = ""):
    """Run enabled CRUD lifecycles via the regression ENGINE (SERIAL), diffing every
    2xx GET/POST response body against the documented model, and let the engine tear
    everything down. Returns 0 on completion, 3 if the destructive gate is unset.

    Delegates to :func:`regression.scenarios.engine.run_lifecycle` through the
    ``on_response`` hook from :func:`make_schema_diff_hook` instead of re-implementing
    a step loop, so it inherits the engine's safety machinery: poll-to-ACTIVE before
    use, owner/run/ttl tagging, quota-budget reservation, shared-VPC adoption and
    reverse-order (retry-aware) teardown. That is what makes including HEAVY safe — a
    heavy resource is polled to a deletable state and torn down with retries rather
    than DELETEd while still CREATING (which would strand a billable cluster). The old
    hand-rolled loop had none of this, hence the LIGHT-only restriction.

    For a LARGE heavy run prefer the DAG-orchestrated path
    (``tools.dag_run_live --schema-diff``): it fans the SAME hook across the
    dependency-ordered, VPC-slot-gated PARALLEL scheduler (the platform's existing
    orchestration) instead of this serial loop.

    HEAVY lifecycles are included only when ``cfg.run_heavy`` (SCP_RUN_HEAVY=true);
    otherwise the LIGHT set runs exactly as before. Double-gated: requires
    ``cfg.allow_mutations and cfg.allow_destructive`` (teardown needs DELETE).
    Without both, it creates and records nothing.
    """
    from regression.scenarios.engine import (
        LIFECYCLES, run_lifecycle, provision_shared_vpc)
    from core.registry import ResourceRegistry

    if not (cfg.allow_mutations and cfg.allow_destructive):
        print("::error::schema_live needs SCP_ALLOW_MUTATIONS=true and "
              "SCP_ALLOW_DESTRUCTIVE=true (creates + tears down real resources)")
        return 3

    include_heavy = bool(getattr(cfg, "run_heavy", False))
    lifecycles = [lc for lc in LIFECYCLES
                  if lc.get("enabled")
                  and (include_heavy or not lc.get("heavy"))
                  and filter_id in lc["id"]]

    on_response, finalize = make_schema_diff_hook(docs)

    # One shared registry owner-tracks the shared VPC AND every lifecycle's creates
    # so the cleanup.reconciler backstop can reclaim anything a per-lifecycle teardown
    # missed. Heavy/ADOPT-class lifecycles adopt ONE shared VPC+subnet (vs each
    # consuming a slot against the 5-VPC cap); light-only runs skip it.
    reg = ResourceRegistry()
    shared_ctx, shared_teardown = ({}, lambda: None)
    if include_heavy:
        shared_ctx, shared_teardown = provision_shared_vpc(
            client, cfg, resource_registry=reg)

    n_run = n_passed = 0
    try:
        for lc in lifecycles:
            print(f"\n=== schema-live lifecycle {lc['id']} "
                  f"(heavy={bool(lc.get('heavy'))}) ===")
            try:
                res = run_lifecycle(lc, client, cfg, shared_ctx=shared_ctx,
                                    resource_registry=reg, on_response=on_response)
                n_run += 1
                if res.get("status") == "passed":
                    n_passed += 1
                print(f"  -> {res.get('status')}"
                      f"{': ' + str(res['reason']) if res.get('reason') else ''}")
            except Exception as exc:  # noqa: BLE001 — one bad lifecycle never aborts the probe
                print(f"  lifecycle error [{lc['id']}]: {exc}")
    finally:
        try:
            shared_teardown()
        except Exception as exc:  # noqa: BLE001
            print(f"  shared-VPC teardown error: {exc}")

    finalize(include_heavy=include_heavy, lifecycles_run=n_run,
             lifecycles_passed=n_passed)
    return 0
