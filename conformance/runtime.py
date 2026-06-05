"""AXIS 2 — RUNTIME probes (behavior findings).

Ports ``tools/runtime_probes.py`` + ``tools/probe_validation.py`` +
``tools/probe_schema_live.py``. These probes call the live gateway to surface
design/implementation defects. All probes are **non-destructive by default**:

  * ``schema`` / ``notfound`` / ``errors`` / ``pagination`` / ``options`` are
    pure reads (GET / OPTIONS).
  * ``status``, the ``validation`` probe and ``l10n`` send an *empty* ``{}`` body
    to create / update ops that have >=1 required field, so the request fails
    validation (expected 400) *before* anything is created — no billable resource
    is made.
  * ``schema-live`` is the one probe that creates real resources: it is
    double-gated (needs ``SCP_ALLOW_MUTATIONS=true`` AND
    ``SCP_ALLOW_DESTRUCTIVE=true``), reuses the proven CRUD lifecycles, and tears
    every created resource down in reverse on completion and on failure. It
    self-skips (creating nothing) unless both gates are set. See
    :mod:`conformance.schema_live`.
  * mutating verbs stay double-gated behind the env flags below plus the client's
    own ``SCP_ALLOW_MUTATIONS`` gate.

Legacy outputs are dual-written (``reports/runtime_<mode>.json`` +
``reports/csv/runtime_<mode>.csv`` and ``reports/validation_probe.json``) so the
existing dashboard/baseline keep working; in addition every defect is emitted to
the unified results store via :func:`core.results.record_finding`
(``source="runtime"``).

Importing this module performs **no** network I/O. All gateway calls happen only
inside the probe functions, which are invoked from :func:`main` and only after the
env gates pass.
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

from core.results import Finding, record_finding

ROOT = Path(__file__).resolve().parent.parent
DOCS = ROOT / "data" / "api_docs.json"
OUTDIR = ROOT / "reports"
CSVDIR = OUTDIR / "csv"
VALIDATION_OUT = OUTDIR / "validation_probe.json"

NONEXISTENT_ID = "00000000-0000-4000-8000-000000000000"
MALFORMED_ID = "not-a-valid-id"


def _docs():
    return json.loads(DOCS.read_text())


def _emit(endpoint_key: str, rule_id: str, severity: str, detail: str) -> None:
    """Record a runtime finding to the unified results store (best-effort)."""
    record_finding(Finding(endpoint_key=endpoint_key, rule_id=rule_id,
                           severity=severity, detail=detail, source="runtime"))


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


# ---------------------------------------------------------------- schema [GET]
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
        # unified findings
        miss_all = missing or item_missing
        extra_all = extra or item_extra
        if miss_all:
            _emit(k, "schema-missing-field", "red",
                  f"response omits documented required field(s): {miss_all}")
        elif extra_all:
            _emit(k, "schema-undocumented-field", "yellow",
                  f"response has undocumented field(s): {extra_all}")
        time.sleep(0.1)
        if limit and summ["checked"] >= limit:
            break
    _write("schema", summ, rows,
           ["endpoint", "status", "model", "undocumented_fields", "missing_required_fields",
            "item_model", "item_undocumented_fields", "item_missing_required", "note"])


# ------------------------------------------------- status codes [empty body]
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
        if klass == "server_5xx_BUG":
            _emit(k, "5xx-on-bad-input", "red",
                  f"empty body -> {st} (should be 400)")
        time.sleep(0.15)
        n += 1
        if limit and n >= limit:
            break
    _write("status", summ, rows, ["endpoint", "method", "status", "klass", "excerpt"])


# ---------------------------------------------------------------- not-found [GET]
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
        # unified findings (mirror static aggregation semantics)
        name = k.rsplit("/", 1)[-1]
        is_dup = ("checkduplication" in name or "check-duplication" in path
                  or "duplication" in name)
        if not is_dup:
            if s_missing == 200:
                if name.startswith("list"):
                    _emit(k, "notfound-200-list", "yellow",
                          "sub-resource list of a non-existent parent -> 200 (empty), not 404")
                else:
                    _emit(k, "notfound-200", "red",
                          "non-existent id -> 200 (should be 404)")
            elif s_missing in (400, 403):
                _emit(k, "notfound-inconsistent", "yellow",
                      f"non-existent id -> {s_missing} (not 404)")
        time.sleep(0.15)
        n += 1
        if limit and n >= limit:
            break
    _write("notfound", summ, rows,
           ["endpoint", "path", "param", "status_nonexistent_id", "status_malformed_id"])


# ------------------------------------------------ errors/auth (unauth) [GET]
def probe_errors(client, docs, limit, category):
    import requests
    from core.config import Settings
    cfg = Settings()
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
            if r.status_code != 401:
                _emit(f"{cat}/{svc}/{e['name']}", "unauth-non-401", "yellow",
                      f"unauthenticated request -> {r.status_code} (not 401)")
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


# ---------------------------------------------------------------- pagination [GET]
def probe_pagination(client, docs, limit, category):
    rows, summ = [], {"checked": 0, "ignores_size": 0, "no_paging_meta": 0, "non_200": 0}
    n = 0
    PAGING = {"page", "size", "page_size", "limit", "offset", "total", "total_count",
              "count", "next", "next_token", "has_next", "total_pages"}
    for k, e in docs["endpoints"].items():
        if e.get("method") != "GET" or "{" in (e.get("path") or "{"):
            continue
        if not e["name"].startswith("list"):
            continue
        if category and category not in e["category"]:
            continue
        try:
            resp = client.request("GET", e["path"], params={"page": 1, "size": 1},
                                  service=e["service"])
        except Exception as exc:
            rows.append({"endpoint": k, "status": "ERR", "note": str(exc)[:100]})
            continue
        summ["checked"] += 1
        if resp.status != 200 or not isinstance(resp.body, dict):
            summ["non_200"] += 1
            rows.append({"endpoint": k, "status": resp.status})
            time.sleep(0.1)
            continue
        meta = sorted(set(resp.body.keys()) & PAGING)
        arr = [v for v in resp.body.values() if isinstance(v, list)]
        biggest = max((len(a) for a in arr), default=0)
        ignores = biggest > 1                      # asked size=1 but got >1
        if ignores:
            summ["ignores_size"] += 1
        if not meta:
            summ["no_paging_meta"] += 1
        rows.append({"endpoint": k, "status": 200, "returned_items_at_size1": biggest,
                     "respects_size": not ignores, "paging_meta": ",".join(meta)})
        # unified findings
        if ignores:
            _emit(k, "pagination-ignores-size", "yellow",
                  f"requested size=1 but response returned {biggest} items")
        if not meta:
            _emit(k, "pagination-no-meta", "yellow",
                  "list response carries no pagination metadata (page/size/total/next/...)")
        time.sleep(0.1)
        n += 1
        if limit and n >= limit:
            break
    _write("pagination", summ, rows,
           ["endpoint", "status", "returned_items_at_size1", "respects_size", "paging_meta", "note"])


# ---------------------------------------------------------------- OPTIONS/CORS
def probe_options(client, docs, limit, category):
    import requests
    from core.config import Settings
    cfg = Settings()
    rep = {}
    for k, e in docs["endpoints"].items():
        if e.get("method") != "GET" or "{" in (e.get("path") or "{"):
            continue
        rep.setdefault((e["category"], e["service"]), e)
    rows, summ = [], {"services": 0, "options_2xx": 0, "has_allow": 0, "has_cors": 0}
    n = 0
    for (cat, svc), e in sorted(rep.items()):
        if category and category not in cat:
            continue
        try:
            url = cfg.resolve_base_url(svc) + e["path"]
            r = requests.options(url, headers={"Origin": "https://example.com",
                                 "Access-Control-Request-Method": "GET"}, timeout=cfg.timeout)
            allow = r.headers.get("Allow", "")
            acam = r.headers.get("Access-Control-Allow-Methods", "")
            acao = r.headers.get("Access-Control-Allow-Origin", "")
            summ["services"] += 1
            summ["options_2xx"] += int(200 <= r.status_code < 300)
            summ["has_allow"] += int(bool(allow))
            summ["has_cors"] += int(bool(acao or acam))
            rows.append({"service": f"{cat}/{svc}", "path": e["path"], "options_status": r.status_code,
                         "allow": allow, "cors_allow_methods": acam, "cors_allow_origin": acao})
            if not (acao or acam):
                _emit(f"{cat}/{svc}/{e['name']}", "cors-missing", "yellow",
                      f"OPTIONS preflight -> {r.status_code} with no CORS headers "
                      f"(Access-Control-Allow-Origin/Methods)")
        except Exception as exc:
            rows.append({"service": f"{cat}/{svc}", "path": e["path"], "options_status": "ERR",
                         "allow": str(exc)[:80]})
        time.sleep(0.12)
        n += 1
        if limit and n >= limit:
            break
    _write("options", summ, rows,
           ["service", "path", "options_status", "allow", "cors_allow_methods", "cors_allow_origin"])


# ---------------------------------------------------------------- localization
def probe_l10n(client, docs, limit, category):
    HANGUL = re.compile(r"[가-힣]")
    rows, summ = [], {"checked": 0, "localized": 0, "not_localized": 0}
    n = 0
    for k, e in docs["endpoints"].items():
        if e.get("method") != "POST" or "{" in (e.get("path") or "{"):
            continue
        if not any(p["in"] == "body" for p in e.get("parameters", [])):
            continue
        if category and category not in e["category"]:
            continue

        def msg(lang):
            try:
                r = client.request("POST", e["path"], json={}, service=e["service"],
                                   headers={"Accept-Language": lang})
                return r.status, r.raw_text or ""
            except Exception as exc:
                return "ERR", str(exc)

        sk, bk = msg("ko-KR")
        se, be = msg("en-US")
        if sk != 400 and se != 400:
            time.sleep(0.1)
            continue
        ko_has = bool(HANGUL.search(bk))
        en_has = bool(HANGUL.search(be))
        localized = ko_has != en_has              # language actually changes with header
        summ["checked"] += 1
        summ["localized" if localized else "not_localized"] += 1
        rows.append({"endpoint": k, "ko_status": sk, "en_status": se,
                     "ko_has_hangul": ko_has, "en_has_hangul": en_has,
                     "localized": localized, "ko_excerpt": bk.replace("\n", " ")[:120]})
        # unified findings
        if not localized:
            _emit(k, "l10n-not-localized", "yellow",
                  "error message does not change with Accept-Language (ko-KR vs en-US)")
        time.sleep(0.15)
        n += 1
        if limit and n >= limit:
            break
    _write("l10n", summ, rows,
           ["endpoint", "ko_status", "en_status", "ko_has_hangul", "en_has_hangul",
            "localized", "ko_excerpt"])


# ===================================================================
# Validation probe (ported from tools/probe_validation.py)
# empty {} body -> 400; does the error name the offending field?
# ===================================================================
NAMES_FIELD = re.compile(r'[Ff]ield\s*\\*"?[a-z_][a-z0-9_]*'
                         r'|\\*"[a-z_][a-z0-9_.]*\\*"\s*(is|must|required|invalid|should)'
                         r'|"field"\s*:\s*\\*"[a-z_]', re.IGNORECASE)
RULE = re.compile(r"\d+\s*(to|~|-)\s*\d+|characters|digits|should have|should be|"
                  r"must be|\^\[|hyphens|lowercase|uppercase|required|valid Type", re.IGNORECASE)
OPAQUE = re.compile(r"value_error|InvalidInputValue|Invalid input data", re.IGNORECASE)


def classify(status: int, body: str) -> str:
    if status != 400:
        return "other"
    body = body or ""
    if OPAQUE.search(body) and not NAMES_FIELD.search(body):
        return "opaque"
    if NAMES_FIELD.search(body):
        return "names_field"
    if RULE.search(body):
        return "rule_in_prose"
    return "opaque"


def validation_targets(docs, category):
    models = docs["models"]
    out = []
    for k, e in docs["endpoints"].items():
        if e.get("method") != "POST":
            continue
        path = e.get("path") or "{"
        if "{" in path:
            continue
        if category and category not in e["category"]:
            continue
        ref = next((p.get("schema_ref") for p in e.get("parameters", [])
                    if p["in"] == "body" and p.get("schema_ref")), None)
        if not ref:
            continue
        m = models.get(f"{e['category']}/{e['service']}/{ref}")
        if not m or not any(f.get("required") for f in m.get("fields", [])):
            continue
        out.append((k, e))
    return out


def probe_validation(client, docs, limit, category, sleep=0.2):
    from core.http_client import MutationBlocked
    tgts = validation_targets(docs, category)
    if limit:
        tgts = tgts[:limit]

    results, tally = [], {"names_field": 0, "rule_in_prose": 0, "opaque": 0, "other": 0}
    for k, e in tgts:
        try:
            resp = client.request("POST", e["path"], json={}, service=e["service"])
            verdict = classify(resp.status, resp.raw_text)
            results.append({"endpoint": k, "path": e["path"], "status": resp.status,
                            "verdict": verdict, "body": (resp.raw_text or "")[:500]})
            if verdict == "opaque" and resp.status == 400:
                _emit(k, "opaque-validation", "red", "400 names neither field nor rule")
        except MutationBlocked as exc:
            print(f"::error::{exc}")
            return 3
        except Exception as exc:
            results.append({"endpoint": k, "path": e["path"], "verdict": "other",
                            "error": str(exc)})
            verdict = "other"
        tally[verdict] += 1
        time.sleep(sleep)

    VALIDATION_OUT.parent.mkdir(exist_ok=True)
    VALIDATION_OUT.write_text(json.dumps(
        {"summary": tally, "count": len(tgts), "results": results},
        indent=2, ensure_ascii=False))

    op = [r for r in results if r["verdict"] == "opaque"]
    print("## RUNTIME validation probe (empty body -> 400)\n")
    print(f"- probed **{len(tgts)}** create (POST) operations with required fields")
    print(f"- names the offending field (structured): **{tally['names_field']}**")
    print(f"- states the rule in prose only: **{tally['rule_in_prose']}**")
    print(f"- OPAQUE - neither field nor rule: **{tally['opaque']}**")
    print(f"- other (non-400: auth/routing): **{tally['other']}**\n")
    print(f"_wrote {VALIDATION_OUT}_")
    return 0


# ---------------------------------------------------------------- entrypoint
def main() -> int:
    if os.environ.get("SCP_PROBE_RUNTIME") != "true":
        print("Refusing to run: set SCP_PROBE_RUNTIME=true (needs SCP creds; run in CI).")
        return 2

    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--probe",
                    choices=["schema", "status", "notfound", "errors", "validation",
                             "pagination", "options", "l10n", "schema-live", "all"],
                    default="all")
    ap.add_argument("--category", default="")
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--sleep", type=float, default=0.2)
    ap.add_argument("--filter", default="",
                    help="schema-live only: substring filter on lifecycle id")
    args = ap.parse_args()

    from core.config import Settings
    from core.http_client import ApiClient
    cfg = Settings()
    cfg.require_credentials()
    client = ApiClient(cfg)
    docs = _docs()

    # validation is double-gated (mutating verb) like the legacy tool
    def _run_validation(client, docs, limit, category):
        if os.environ.get("SCP_PROBE_VALIDATION") != "true":
            print("Skipping validation probe: set SCP_PROBE_VALIDATION=true "
                  "(+ SCP_ALLOW_MUTATIONS=true).")
            return
        probe_validation(client, docs, limit, category, sleep=args.sleep)

    # schema-live is double-gated (creates + tears down real resources) and lives
    # in the conformance.schema_live helper; it reuses the CRUD lifecycles.
    def _run_schema_live(client, docs, limit, category):
        if not (cfg.allow_mutations and cfg.allow_destructive):
            print("Skipping schema-live probe: set SCP_ALLOW_MUTATIONS=true and "
                  "SCP_ALLOW_DESTRUCTIVE=true (creates + tears down real resources).")
            return
        from conformance.schema_live import probe_schema_live
        probe_schema_live(client, cfg, docs, filter_id=args.filter)

    probes = {"schema": probe_schema, "status": probe_status,
              "notfound": probe_notfound, "errors": probe_errors,
              "validation": _run_validation,
              "pagination": probe_pagination, "options": probe_options,
              "l10n": probe_l10n, "schema-live": _run_schema_live}
    # `all` runs the safe (non-mutating-by-default) probes; the gated probes
    # (validation, schema-live) self-skip inside their runners unless opted in.
    todo = probes if args.probe == "all" else {args.probe: probes[args.probe]}
    for name, fn in todo.items():
        try:
            fn(client, docs, args.limit, args.category)
        except Exception as exc:
            print(f"::error::probe {name} failed: {exc}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
