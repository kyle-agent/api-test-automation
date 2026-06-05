#!/usr/bin/env python3
"""RUNTIME probes that surface API design/implementation issues by actually
calling the gateway (needs SCP creds; run in CI). Read-only except `status`,
which sends empty bodies that fail validation before anything is created.

Modes (--probe):
  schema   : GET each parameterless list/show endpoint and diff the JSON against
             the documented response model (undocumented / missing / drifted
             fields, top-level + one level into the main array/object).   [GET]
  status   : send an empty {} body to each create/update op and flag responses
             that are 5xx (should be 4xx) — i.e. server crashes on bad input.  [POST/PUT/PATCH, empty body]
  notfound : GET each single-path-param show endpoint with (a) a valid-format
             but non-existent id and (b) a malformed id; record 400/404/500/200
             consistency.                                                   [GET]
  errors   : per service, call a list endpoint WITHOUT auth and record the
             status + whether the error body is JSON/HTML + its envelope shape
             (error-format & auth-failure consistency).                     [GET]

Outputs: reports/runtime_<mode>.json + reports/csv/runtime_<mode>.csv
Usage (CI): SCP_PROBE_RUNTIME=true SCP_ALLOW_MUTATIONS=true python tools/runtime_probes.py --probe all
"""
from __future__ import annotations

import argparse
import csv
import json
import os
import re
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
DOCS = ROOT / "data" / "api_docs.json"
OUTDIR = ROOT / "reports"
CSVDIR = OUTDIR / "csv"

NONEXISTENT_ID = "00000000-0000-4000-8000-000000000000"
MALFORMED_ID = "not-a-valid-id"


def _docs():
    return json.loads(DOCS.read_text())


def _write(mode, summary, rows, fieldnames):
    OUTDIR.mkdir(exist_ok=True)
    CSVDIR.mkdir(parents=True, exist_ok=True)
    (OUTDIR / f"runtime_{mode}.json").write_text(
        json.dumps({"summary": summary, "results": rows}, indent=2, ensure_ascii=False))
    with (CSVDIR / f"runtime_{mode}.csv").open("w", newline="", encoding="utf-8-sig") as fh:
        w = csv.DictWriter(fh, fieldnames=fieldnames)
        w.writeheader()
        for r in rows:
            w.writerow({k: r.get(k, "") for k in fieldnames})
    print(f"## runtime probe: {mode}\n")
    for k, v in summary.items():
        print(f"- {k}: {v}")
    print(f"\n_wrote reports/runtime_{mode}.json + reports/csv/runtime_{mode}.csv_\n")


# ---------------------------------------------------------------- schema
def probe_schema(client, docs, limit, category):
    models = docs["models"]
    rows = []
    summ = {"checked": 0, "with_undocumented_fields": 0, "with_missing_required": 0, "non_200": 0}
    for k, e in docs["endpoints"].items():
        if e.get("method") != "GET" or "{" in (e.get("path") or "{"):
            continue
        if category and category not in e["category"]:
            continue
        ref = next((r.get("schema_ref") for r in e.get("responses", [])
                    if str(r.get("code", "")).startswith("2") and r.get("schema_ref")), None)
        if not ref:
            continue
        model = models.get(f"{e['category']}/{e['service']}/{ref}")
        if not model:
            continue
        try:
            resp = client.request("GET", e["path"], service=e["service"])
        except Exception as exc:
            rows.append({"endpoint": k, "status": "ERR", "note": str(exc)[:120]})
            continue
        summ["checked"] += 1
        if resp.status != 200 or not isinstance(resp.body, dict):
            summ["non_200"] += 1
            rows.append({"endpoint": k, "status": resp.status, "model": ref,
                         "note": "non-200 or non-object body"})
            time.sleep(0.1)
            continue
        mfields = model.get("fields", [])
        mnames = {f["name"] for f in mfields}
        rkeys = set(resp.body.keys())
        extra = sorted(rkeys - mnames)
        missing = sorted({f["name"] for f in mfields if f.get("required")} - rkeys)
        # one level into the main array/object field (the payload)
        item_extra = item_missing = []
        item_model = ""
        for f in mfields:
            if f.get("schema_ref") and f["name"] in resp.body:
                val = resp.body[f["name"]]
                item = val[0] if isinstance(val, list) and val else (val if isinstance(val, dict) else None)
                im = models.get(f"{e['category']}/{e['service']}/{f['schema_ref']}")
                if item and im:
                    inames = {x["name"] for x in im.get("fields", [])}
                    item_model = f["schema_ref"]
                    item_extra = sorted(set(item.keys()) - inames)
                    item_missing = sorted({x["name"] for x in im.get("fields", []) if x.get("required")} - set(item.keys()))
                    break
        if extra or item_extra:
            summ["with_undocumented_fields"] += 1
        if missing or item_missing:
            summ["with_missing_required"] += 1
        rows.append({"endpoint": k, "status": 200, "model": ref,
                     "undocumented_fields": ",".join(extra),
                     "missing_required_fields": ",".join(missing),
                     "item_model": item_model,
                     "item_undocumented_fields": ",".join(item_extra),
                     "item_missing_required": ",".join(item_missing)})
        time.sleep(0.1)
        if limit and summ["checked"] >= limit:
            break
    _write("schema", summ, rows,
           ["endpoint", "status", "model", "undocumented_fields", "missing_required_fields",
            "item_model", "item_undocumented_fields", "item_missing_required", "note"])


# ---------------------------------------------------------------- status codes
def probe_status(client, docs, limit, category):
    rows, summ = [], {"checked": 0, "client_4xx": 0, "server_5xx_BUG": 0, "other": 0}
    n = 0
    for k, e in docs["endpoints"].items():
        if e.get("method") not in ("POST", "PUT", "PATCH") or "{" in (e.get("path") or "{"):
            continue
        if not any(p["in"] == "body" for p in e.get("parameters", [])):
            continue
        if category and category not in e["category"]:
            continue
        try:
            resp = client.request(e["method"], e["path"], json={}, service=e["service"])
            st = resp.status
        except Exception as exc:
            rows.append({"endpoint": k, "method": e["method"], "status": "ERR", "klass": "error",
                         "excerpt": str(exc)[:160]})
            summ["other"] += 1
            continue
        klass = ("client_4xx" if 400 <= st < 500 else
                 "server_5xx_BUG" if 500 <= st < 600 else "other")
        summ["checked"] += 1
        summ[klass] = summ.get(klass, 0) + 1
        rows.append({"endpoint": k, "method": e["method"], "status": st, "klass": klass,
                     "excerpt": (resp.raw_text or "").replace("\n", " ")[:160]})
        time.sleep(0.15)
        n += 1
        if limit and n >= limit:
            break
    _write("status", summ, rows, ["endpoint", "method", "status", "klass", "excerpt"])


# ---------------------------------------------------------------- not-found
def probe_notfound(client, docs, limit, category):
    rows, summ = [], {"checked": 0, "ok_404": 0, "non_404": 0, "server_5xx": 0}
    n = 0
    for k, e in docs["endpoints"].items():
        path = e.get("path") or ""
        if e.get("method") != "GET":
            continue
        params = re.findall(r"\{([^}]+)\}", path)
        if len(params) != 1:                       # exactly one id to substitute
            continue
        if category and category not in e["category"]:
            continue
        def call(idval):
            p = path.replace("{" + params[0] + "}", idval)
            try:
                return client.request("GET", p, service=e["service"]).status
            except Exception as exc:
                return f"ERR:{str(exc)[:40]}"
        s_missing = call(NONEXISTENT_ID)
        s_malformed = call(MALFORMED_ID)
        summ["checked"] += 1
        if s_missing == 404:
            summ["ok_404"] += 1
        else:
            summ["non_404"] += 1
        if isinstance(s_missing, int) and 500 <= s_missing < 600 or \
           isinstance(s_malformed, int) and 500 <= s_malformed < 600:
            summ["server_5xx"] += 1
        rows.append({"endpoint": k, "path": path, "param": params[0],
                     "status_nonexistent_id": s_missing, "status_malformed_id": s_malformed})
        time.sleep(0.15)
        n += 1
        if limit and n >= limit:
            break
    _write("notfound", summ, rows,
           ["endpoint", "path", "param", "status_nonexistent_id", "status_malformed_id"])


# ---------------------------------------------------------------- errors/auth (unauth)
def probe_errors(client, docs, limit, category):
    import requests
    from core.config import Settings
    cfg = Settings()
    # one representative parameterless GET list endpoint per service
    rep = {}
    for k, e in docs["endpoints"].items():
        if e.get("method") != "GET" or "{" in (e.get("path") or "{"):
            continue
        svc = (e["category"], e["service"])
        if svc not in rep or e["name"].startswith("list"):
            rep[svc] = e
    rows, summ = [], {"services": 0, "unauth_401": 0, "unauth_html": 0, "unauth_other": 0}
    n = 0
    for (cat, svc), e in sorted(rep.items()):
        if category and category not in cat:
            continue
        try:
            base = cfg.resolve_base_url(svc)
        except Exception as exc:
            rows.append({"service": f"{cat}/{svc}", "note": f"host? {exc}"[:120]})
            continue
        url = base + e["path"]
        try:
            r = requests.get(url, headers={"Accept": "application/json", "Accept-Language": "en-US"},
                             timeout=cfg.timeout)
            ct = r.headers.get("Content-Type", "")
            is_html = "html" in ct.lower() or r.text.strip().lower().startswith("<!doctype")
            kind = "json" if r.text.strip().startswith(("{", "[")) else ("html" if is_html else "other")
            keys = ""
            if kind == "json":
                try:
                    j = r.json()
                    keys = ",".join(list(j.keys())[:6]) if isinstance(j, dict) else "[array]"
                except Exception:
                    keys = ""
            summ["services"] += 1
            summ["unauth_401" if r.status_code == 401 else
                 "unauth_html" if kind == "html" else "unauth_other"] += 1
            rows.append({"service": f"{cat}/{svc}", "endpoint": e["name"], "path": e["path"],
                         "unauth_status": r.status_code, "body_kind": kind,
                         "content_type": ct, "envelope_keys": keys,
                         "excerpt": r.text.replace("\n", " ")[:140]})
        except Exception as exc:
            rows.append({"service": f"{cat}/{svc}", "endpoint": e["name"], "path": e["path"],
                         "unauth_status": "ERR", "excerpt": str(exc)[:140]})
        time.sleep(0.15)
        n += 1
        if limit and n >= limit:
            break
    _write("errors", summ, rows,
           ["service", "endpoint", "path", "unauth_status", "body_kind", "content_type",
            "envelope_keys", "excerpt"])


def main() -> int:
    if os.environ.get("SCP_PROBE_RUNTIME") != "true":
        print("Refusing to run: set SCP_PROBE_RUNTIME=true (needs SCP creds; run in CI).")
        return 2
    ap = argparse.ArgumentParser()
    ap.add_argument("--probe", choices=["schema", "status", "notfound", "errors", "all"], default="all")
    ap.add_argument("--category", default="")
    ap.add_argument("--limit", type=int, default=0)
    args = ap.parse_args()

    from core.config import Settings
    from core.http_client import ApiClient
    cfg = Settings()
    cfg.require_credentials()
    client = ApiClient(cfg)
    docs = _docs()

    probes = {"schema": probe_schema, "status": probe_status,
              "notfound": probe_notfound, "errors": probe_errors}
    todo = probes if args.probe == "all" else {args.probe: probes[args.probe]}
    for name, fn in todo.items():
        try:
            fn(client, docs, args.limit, args.category)
        except Exception as exc:
            print(f"::error::probe {name} failed: {exc}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
