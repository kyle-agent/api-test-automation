"""Read-only GET coverage booster via list->show chaining (AXIS 1 engine).

Ported from ``tests/smoke/test_read_chains.py``, freed of pytest. The catalog
smoke can only call GETs WITHOUT path params; every ``show{X}/{id}`` is skipped
because it needs a real id. Many single-path-param GETs, though, take an id
freely derivable from a sibling *list*: ``/v1/server-types/{server_type_id}``
pairs with ``/v1/server-types``. This module exercises those reads with ZERO
resource creation:

  * 1-param chains: list the parent collection, take the first item's id, call
    the show endpoint.
  * 2-param chains: derive param1 from the parent list, then param2 from the
    sub-list (with param1 filled), then call the two-param show.

Parent lists are cached per (service, path) so a service with many show
endpoints under one collection lists only once.

Read-only and record-only (exactly like the CRUD ``probe_reads`` step): every
call appends a :class:`core.results.Observation` (source ``read_chain``) AND a
row to the legacy ``reports/smoke_status.tsv`` so coverage is preserved. A
derived read can never by itself turn the regression gate red.
"""
from __future__ import annotations

import re

from core import results
from core.results import Observation
from core.catalog import endpoints

# None = all services. Pass a list to scope (e.g. ["virtualserver"]).
SMOKE_TSV = "reports/smoke_status.tsv"
_PLACEHOLDER = re.compile(r"\{([^}]+)\}")


def categorize(status: int, text: str) -> str:
    """Same ok/soft/fail split as the smoke suite (only 5xx / HMAC-401 are hard
    fails; everything else is the API answering correctly given this account)."""
    t = (text or "").lower()
    if 200 <= status < 300:
        return results.OK
    if status == 401:
        return results.SOFT if ("rejected by gateway" in t
                                or "catalog has not target" in t) else results.FAIL
    if status >= 500:
        return results.FAIL
    return results.SOFT  # 400/403/404/409/422 — needs params/permission/provisioning


def _record(status, category, key, method, path, elapsed_ms=None):
    """Dual-write: unified Observation store AND legacy smoke TSV."""
    results.record(Observation(
        endpoint_key=key, method=method, path=path, status=status,
        category=category, elapsed_ms=elapsed_ms, source="read_chain"))
    import os
    ems = "" if elapsed_ms is None else f"{elapsed_ms:.0f}"
    try:
        os.makedirs("reports", exist_ok=True)
        with open(SMOKE_TSV, "a") as fh:
            fh.write(f"{status}\t{category}\t{key}\t{method}\t{path}\t{ems}\n")
    except OSError:
        pass


def _list_items(body):
    """Best-effort extract a list of resource dicts from a list response. SCP
    list bodies vary: a bare array, or a dict wrapping the array under a key
    (contents/items/<plural>/data). Return the first list-of-dicts found."""
    if isinstance(body, list):
        return [it for it in body if isinstance(it, dict)]
    if isinstance(body, dict):
        for v in body.values():
            if isinstance(v, list) and v and isinstance(v[0], dict):
                return v
    return []


def _id_from(item: dict, param: str):
    """Pull the value a path param wants out of a list item. Try the exact param
    name, then a generic 'id', then 'name', then the param with common suffixes
    stripped."""
    for k in (param, "id", "name", param.replace("_id", ""), param.replace("_name", ""),
              param.replace("_id", "_name")):
        if k and isinstance(item.get(k), (str, int)) and str(item.get(k)).strip():
            return str(item[k])
    return None


def single_param_chains(services: list[str] | None = None):
    """Every GET with exactly one path param, paired with its parent list = the
    path prefix before the first '{'. Scoped by `services` (None = all)."""
    out = []
    svc_filter = set(services) if services else None
    for e in endpoints(method="GET", resolved_only=True):
        if svc_filter and e.service not in svc_filter:
            continue
        params = _PLACEHOLDER.findall(e.http_path)
        if len(params) != 1:
            continue
        prefix = e.http_path.split("{", 1)[0].rstrip("/")
        if not prefix:
            continue
        out.append((e, params[0], prefix))
    return out


def two_param_chains(services: list[str] | None = None):
    """Every GET with exactly two path params, paired with the two lists that
    feed them: the parent collection for param1 (prefix before the first '{'),
    and the sub-collection template for param2 (up to the second '{', still
    holding {param1}). Scoped by `services`."""
    out = []
    svc_filter = set(services) if services else None
    for e in endpoints(method="GET", resolved_only=True):
        if svc_filter and e.service not in svc_filter:
            continue
        params = _PLACEHOLDER.findall(e.http_path)
        if len(params) != 2:
            continue
        prefix1 = e.http_path.split("{", 1)[0].rstrip("/")
        second_open = e.http_path.index("{", e.http_path.index("}"))
        sublist_tmpl = e.http_path[:second_open].rstrip("/")
        if not prefix1 or not sublist_tmpl:
            continue
        out.append((e, params[0], prefix1, params[1], sublist_tmpl))
    return out


def _derive_id(client, cache, service, list_path, param):
    """List `list_path` (cached) and return (id, None) or (None, skip_reason)."""
    ck = (service, list_path)
    if ck not in cache:
        try:
            cache[ck] = client.get(list_path, service=service)
        except Exception as exc:
            cache[ck] = exc
    lst = cache[ck]
    if isinstance(lst, Exception):
        return None, f"list {list_path} unreachable: {lst}"
    if not lst.ok:
        return None, f"list {list_path} -> {lst.status}; no id to derive"
    rid = next((_id_from(it, param) for it in _list_items(lst.body)
                if _id_from(it, param)), None)
    if rid is None:
        return None, f"no {param} available from {list_path} (empty/no id field)"
    return rid, None


def run_chain(endpoint, param, list_path, client, cache) -> dict:
    """1-param chain: derive the id from the sibling list, exercise the show GET.
    Record-only; returns ``{recorded, category?, skipped?, reason?}``."""
    rid, why = _derive_id(client, cache, endpoint.service, list_path, param)
    if rid is None:
        return {"recorded": False, "skipped": True, "reason": why}
    path = endpoint.http_path.replace("{%s}" % param, rid)
    try:
        resp = client.get(path, service=endpoint.service)
    except Exception as exc:
        _record(0, results.FAIL, endpoint.key, "GET", endpoint.http_path)
        return {"recorded": True, "skipped": True, "reason": f"{path} unreachable: {exc}"}
    cat = categorize(resp.status, getattr(resp, "raw_text", ""))
    _record(resp.status, cat, endpoint.key, "GET", endpoint.http_path,
            getattr(resp, "elapsed_ms", None))
    return {"recorded": True, "skipped": False, "category": cat, "status": resp.status}


def run_chain_2p(endpoint, p1, list1, p2, sublist_tmpl, client, cache) -> dict:
    """2-param chain: derive param1 from the parent list, param2 from the
    sub-list (param1 filled), then exercise the two-param show GET. Record-only."""
    id1, why = _derive_id(client, cache, endpoint.service, list1, p1)
    if id1 is None:
        return {"recorded": False, "skipped": True, "reason": why}
    sublist = sublist_tmpl.replace("{%s}" % p1, id1)
    id2, why = _derive_id(client, cache, endpoint.service, sublist, p2)
    if id2 is None:
        return {"recorded": False, "skipped": True, "reason": why}
    path = endpoint.http_path.replace("{%s}" % p1, id1).replace("{%s}" % p2, id2)
    try:
        resp = client.get(path, service=endpoint.service)
    except Exception as exc:
        _record(0, results.FAIL, endpoint.key, "GET", endpoint.http_path)
        return {"recorded": True, "skipped": True, "reason": f"{path} unreachable: {exc}"}
    cat = categorize(resp.status, getattr(resp, "raw_text", ""))
    _record(resp.status, cat, endpoint.key, "GET", endpoint.http_path,
            getattr(resp, "elapsed_ms", None))
    return {"recorded": True, "skipped": False, "category": cat, "status": resp.status}


def run_read_chains(client, *, services: list[str] | None = None) -> dict:
    """Drive both chain families. Shares one session list-cache. Returns a summary
    ``{chains_1p, chains_2p, recorded, skipped, ok, soft, fail}``."""
    cache: dict = {}
    summary = {"chains_1p": 0, "chains_2p": 0, "recorded": 0, "skipped": 0,
               results.OK: 0, results.SOFT: 0, results.FAIL: 0}
    for ep, param, list_path in single_param_chains(services):
        summary["chains_1p"] += 1
        res = run_chain(ep, param, list_path, client, cache)
        _tally(summary, res)
    for ep, p1, list1, p2, sublist in two_param_chains(services):
        summary["chains_2p"] += 1
        res = run_chain_2p(ep, p1, list1, p2, sublist, client, cache)
        _tally(summary, res)
    return summary


def _tally(summary: dict, res: dict) -> None:
    if res.get("skipped") and not res.get("recorded"):
        summary["skipped"] += 1
    if res.get("category"):
        summary["recorded"] += 1
        summary[res["category"]] = summary.get(res["category"], 0) + 1
