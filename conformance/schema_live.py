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


def probe_schema_live(client, cfg, docs, *, filter_id: str = ""):
    """Run LIGHT enabled CRUD lifecycles, diffing every 2xx GET/POST body against
    the documented model, then tear everything down. Returns 0 on completion, 3
    if the destructive gate is not set (matching the legacy exit codes).

    Double-gated: requires ``cfg.allow_mutations and cfg.allow_destructive``
    (teardown needs DELETE). Without both, it creates and records nothing.
    """
    # reuse the proven lifecycle definitions + step helpers (no reinvention)
    from core.http_client import MutationBlocked
    from regression.scenarios.engine import (
        LIFECYCLES, _fill, _fill_obj, _capture, _run_step)

    if not (cfg.allow_mutations and cfg.allow_destructive):
        print("::error::schema_live needs SCP_ALLOW_MUTATIONS=true and "
              "SCP_ALLOW_DESTRUCTIVE=true (creates + tears down real resources)")
        return 3

    idx = _endpoint_index(docs)
    lifecycles = [lc for lc in LIFECYCLES
                  if lc.get("enabled") and not lc.get("heavy") and filter_id in lc["id"]]

    rows = []
    drift_rows = []
    for lc in lifecycles:
        service = lc.get("service", "").split("/")[-1] or None
        _now = time.gmtime()
        ctx = {"unique": format(int(time.time()), "x"), "region": cfg.region,
               "today": time.strftime("%Y%m%d", _now),
               "today_plus_5y": f"{_now.tm_year + 5}{time.strftime('%m%d', _now)}"}
        cleanups = []
        print(f"\n=== lifecycle {lc['id']} ===")
        try:
            for step in lc["steps"]:
                if step.get("probe_reads"):
                    continue
                svc = step.get("service") or service
                if step.get("wait"):
                    time.sleep(float(step["wait"]))
                path = _fill(step["path"], ctx)
                body = _fill_obj(step.get("json"), ctx)
                try:
                    resp = _run_step(client, step, path, body, svc, ctx)
                except MutationBlocked as exc:
                    print(f"  blocked: {exc}")
                    break
                expected = step.get("expect_status", [200])
                # capture ids for later steps / teardown
                if resp.status in expected:
                    for var, expr in {**step.get("capture", {}), **step.get("capture_soft", {})}.items():
                        v = _capture(resp.body, expr)
                        if v is not None:
                            ctx[var] = str(v)
                    cu = step.get("cleanup")
                    if cu:
                        cleanups.append((cu["method"], _fill(cu["path"], ctx),
                                         cu.get("service") or svc, _fill_obj(cu.get("json"), ctx)))
                # diff GET/POST 2xx responses against the documented model
                if step["method"] in ("GET", "POST") and 200 <= resp.status < 300 and isinstance(resp.body, dict):
                    match = next((e for rx, e in idx.get((step["method"], svc), []) if rx.match(path)), None)
                    if match:
                        ekey = f"{match['category']}/{match['service']}/{match['name']}"
                        d = _diff(docs, match, resp.body)
                        if d:
                            row = {"lifecycle": lc["id"], "step": step["name"],
                                   "endpoint": ekey, "method": step["method"], **d}
                            rows.append(row)
                            miss = d["missing_required_fields"] or d["item_missing_required"]
                            extra = d["undocumented_fields"] or d["item_undocumented_fields"]
                            if miss or extra:
                                drift_rows.append(row)
                                print(f"  DRIFT {ekey}: extra=[{d['undocumented_fields']}] "
                                      f"missing=[{d['missing_required_fields']}] "
                                      f"item_extra=[{d['item_undocumented_fields']}]")
                                # unified findings (same semantics as probe_schema)
                                if miss:
                                    _emit(ekey, "schema-live-missing-field", "red",
                                          f"live (created) response omits documented required "
                                          f"field(s): {miss}")
                                elif extra:
                                    _emit(ekey, "schema-live-undocumented-field", "yellow",
                                          f"live (created) response has undocumented "
                                          f"field(s): {extra}")
        except Exception as exc:
            print(f"  lifecycle error: {exc}")
        finally:
            for method, p, svc, j in reversed(cleanups):
                try:
                    client.request(method, p, json=j, service=svc)
                    print(f"  cleanup {method} {p}")
                except Exception as exc:
                    print(f"  cleanup FAILED {p}: {exc}")

    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps({"checked": len(rows), "with_drift": len(drift_rows),
                               "results": rows}, indent=2, ensure_ascii=False))
    CSV.parent.mkdir(parents=True, exist_ok=True)
    cols = ["lifecycle", "step", "endpoint", "method", "model", "undocumented_fields",
            "missing_required_fields", "item_model", "item_undocumented_fields", "item_missing_required"]
    with CSV.open("w", newline="", encoding="utf-8-sig") as fh:
        w = csv.DictWriter(fh, fieldnames=cols)
        w.writeheader()
        for r in rows:
            w.writerow({c: r.get(c, "") for c in cols})
    print(f"\n## data-based schema drift\n- responses checked: {len(rows)}\n"
          f"- responses WITH drift: {len(drift_rows)}")
    print(f"_wrote {OUT} + {CSV}_")
    return 0
