"""Catalog smoke ENGINE (AXIS 1) — the per-endpoint read-only probe.

Ported from ``tests/smoke/test_catalog_smoke.py`` but freed of pytest: the
categorize + call + record logic lives here as plain functions so a scheduler,
a thin pytest entrypoint, or CI can drive it. The categorize logic is kept
byte-for-byte (only 5xx / HMAC-401 are hard fails; everything else is the API
answering correctly given this account's params/permissions/entitlements).

For every catalog endpoint:
  * mutating endpoints are skipped (covered by CRUD scenarios),
  * path-param endpoints are skipped (covered by read_chains / CRUD probes),
  * plain read-only GETs are called for real and categorized ok/soft/fail.

Recording is **dual**: each call appends a :class:`core.results.Observation`
to the unified results store AND a row to the legacy
``reports/smoke_status.tsv`` so the existing dashboard keeps working.
"""
from __future__ import annotations

import json
from pathlib import Path

from core import results
from core.results import Observation
from core.catalog import Endpoint, endpoints

# --- legacy dual-write targets (kept so nothing downstream breaks yet) -------
STATUS_FILE = "reports/smoke_status.tsv"
# Parameter-coverage probe: re-issue each OK GET once with a universally-ignorable
# read-only pagination set, recording to a SEPARATE file so it never inflates the
# smoke count/coverage. Makes the dashboard's "parameter" axis measurable.
PARAM_FILE = "reports/param_status.tsv"
_PARAM_SET = {"page": 0, "size": 1, "limit": 1}
_PARAM_REPR = "page=0&size=1&limit=1"

# A handful of read GETs answer 4xx on a bare call because a query param is
# mandatory (not a permission/entitlement limit). We feed a synthetic value so
# they actually exercise — non-destructive (a duplication check with an unused
# name creates nothing).
_DUP_NAME = "regrprobesmoke"


def load_known_issues(path: str = "known_issues.json") -> dict:
    """Baseline of already-tracked backend failures. A smoke 'fail' whose key is
    listed here is treated as a known issue (still recorded) rather than a NEW
    regression, so the gate stays green unless a new endpoint breaks."""
    p = Path(path)
    if not p.exists():
        return {}
    try:
        return {i["key"]: i for i in json.loads(p.read_text()).get("issues", [])}
    except Exception:
        return {}


def reset_status_files() -> None:
    """Start a smoke session with fresh legacy status logs. Mirrors the smoke
    suite's session-scoped autouse fixture so CRUD probe-reads can append later."""
    for f in (STATUS_FILE, PARAM_FILE):
        p = Path(f)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text("")


def categorize(status: int, text: str) -> tuple[str, str]:
    """Classify a read-only response into ok / soft / fail with a reason.

    Hard-fail only on genuine problems:
      * 401 HmacValidFail   -> our signing is wrong.
      * 5xx                 -> server error.
    Everything else is the API responding correctly to a bare list call given
    this account's params/permissions/entitlements, and is reported, not failed.
    """
    t = (text or "").lower()
    if 200 <= status < 300:
        return results.OK, ""
    if status == 401:
        if "rejected by gateway" in t or "catalog has not target" in t:
            return results.SOFT, "service not entitled for this account/region"
        return results.FAIL, "HmacValidFail — signing/credentials wrong"
    if status >= 500:
        return results.FAIL, "server error"
    if status == 403:
        return results.SOFT, "no permission for this key"
    if status == 404:
        return results.SOFT, "not provisioned / not found"
    return results.SOFT, "needs required query params / data"  # 400/409/422/etc.


def _record(endpoint: Endpoint, status: int, category: str,
            elapsed_ms: float | None = None, *, source: str = "smoke",
            note: str = "") -> None:
    """Dual-write: the unified Observation store AND the legacy smoke TSV."""
    results.record(Observation(
        endpoint_key=endpoint.key, method=endpoint.method or "GET",
        path=endpoint.http_path or "", status=status, category=category,
        elapsed_ms=elapsed_ms, source=source, note=note))
    ems = "" if elapsed_ms is None else f"{elapsed_ms:.0f}"
    try:
        with open(STATUS_FILE, "a") as fh:
            fh.write(f"{status}\t{category}\t{endpoint.key}\t{endpoint.method}"
                     f"\t{endpoint.http_path}\t{ems}\n")
    except OSError:
        pass


def _record_param(endpoint: Endpoint, status: int, category: str) -> None:
    """Append a parameter-coverage result (legacy file; 6th col = param set)."""
    try:
        with open(PARAM_FILE, "a") as fh:
            fh.write(f"{status}\t{category}\t{endpoint.key}\t{endpoint.method}"
                     f"\t{endpoint.http_path}\t{_PARAM_REPR}\n")
    except OSError:
        pass


def _required_param_candidates(endpoint: Endpoint) -> list[dict]:
    p = endpoint.http_path or ""
    if p.endswith("/check-duplication") or p.endswith("/check-duplication/name"):
        return [{"name": _DUP_NAME}, {"productName": _DUP_NAME}, {"resourceName": _DUP_NAME}]
    if p.endswith("/parameters"):
        svc = endpoint.service  # e.g. mysql / postgresql / eventstreams
        return [{"dbType": svc}, {"engine": svc}, {"engineName": svc},
                {"dbEngine": svc}, {"engineVersion": "1"}, {"version": "1"}]
    return []


def select_endpoints(category: str | None = None,
                     service: str | None = None) -> list[Endpoint]:
    """Catalog selection used by the smoke engine (resolved paths only)."""
    return endpoints(category=category, service=service, resolved_only=True)


def smoke_endpoint(endpoint: Endpoint, client, *,
                   known_issues: dict | None = None) -> dict:
    """Probe one endpoint and record the result. Returns a small result dict
    ``{status, category, reason, skipped, known_issue, ...}``.

    Returns ``skipped=True`` (without recording) for endpoints the read-only
    smoke can't meaningfully call (mutating / path-param). The caller decides
    how to surface a 'fail' category — this engine never raises on a fail.
    """
    if endpoint.is_mutating:
        return {"skipped": True, "reason": "mutating endpoint — covered by CRUD scenarios"}
    if endpoint.has_path_params:
        return {"skipped": True, "reason": f"needs a real resource id: {endpoint.http_path}"}

    candidates = _required_param_candidates(endpoint)
    resp = category = reason = None
    for params in (candidates or [None]):
        try:
            resp = client.get(endpoint.http_path, service=endpoint.service, params=params)
        except Exception as exc:
            # Transient/host failure — record a fail (status 0) so the count
            # stays honest and the dashboard surfaces unreachable services.
            _record(endpoint, 0, results.FAIL, note=f"unreachable: {exc}")
            return {"skipped": False, "status": 0, "category": results.FAIL,
                    "reason": f"unreachable: {exc}", "known_issue": False}
        category, reason = categorize(resp.status, resp.raw_text)
        if category == results.OK:
            break

    _record(endpoint, resp.status, category, getattr(resp, "elapsed_ms", None),
            note=reason)

    # Parameter-coverage probe (read-only, record-only) for plain OK lists.
    if resp.ok and not candidates:
        try:
            presp = client.get(endpoint.http_path, service=endpoint.service, params=_PARAM_SET)
            pcat, _ = categorize(presp.status, presp.raw_text)
            _record_param(endpoint, presp.status, pcat)
        except Exception:
            _record_param(endpoint, 0, results.FAIL)

    known = bool(category == results.FAIL and (known_issues or {}).get(endpoint.key))
    return {"skipped": False, "status": resp.status, "category": category,
            "reason": reason, "known_issue": known,
            "elapsed_ms": getattr(resp, "elapsed_ms", None)}


def run_smoke(client, *, category: str | None = None, service: str | None = None,
              reset: bool = True) -> dict:
    """Drive the whole smoke pass. Returns a summary
    ``{total, ok, soft, fail, skipped, known_issues, failures: [...]}``.

    Never raises on an endpoint failure: the engine records everything; a thin
    pytest entrypoint can assert on the returned ``failures`` (minus known)."""
    if reset:
        reset_status_files()
    known = load_known_issues()
    summary = {"total": 0, results.OK: 0, results.SOFT: 0, results.FAIL: 0,
               "skipped": 0, "known_issues": 0, "failures": []}
    for ep in select_endpoints(category=category, service=service):
        res = smoke_endpoint(ep, client, known_issues=known)
        if res.get("skipped"):
            summary["skipped"] += 1
            continue
        summary["total"] += 1
        summary[res["category"]] = summary.get(res["category"], 0) + 1
        if res["category"] == results.FAIL:
            if res["known_issue"]:
                summary["known_issues"] += 1
            else:
                summary["failures"].append(
                    {"key": ep.key, "method": ep.method, "path": ep.http_path,
                     "status": res["status"], "reason": res["reason"]})
    return summary
