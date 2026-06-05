"""Read-only GET coverage booster via list->show chaining (all services).

The catalog smoke suite (test_catalog_smoke.py) can only call GETs WITHOUT path
params; every ``show{X}/{id}`` endpoint is skipped because it needs a real
resource id. Those ids are normally only supplied by a CRUD lifecycle — which,
for heavy services, may be environment-blocked (e.g. VPC quota).

Many single-path-param GETs, though, take an id that is freely derivable from a
sibling *list* in the same service: ``/v1/server-types/{server_type_id}`` pairs
with ``/v1/server-types``. This module exercises those reads with zero resource
creation, across EVERY service: list the parent collection, take the first
item's id, and call the show endpoint. Catalog-backed collections (server-types,
volume-types, images, regions, …) yield coverage on every run; account-dependent
ones are covered opportunistically when the account holds such a resource, and
are skipped (not failed) when the list is empty.

Parent lists are cached per (service, path) for the session so a service with
many show endpoints under one collection only lists once.

Read-only and record-only, exactly like the CRUD ``probe_reads`` step: results
land in the same reports/smoke_status.tsv the dashboard reads, so they count
toward read coverage, and a probe never turns the suite red on its own.
"""
from __future__ import annotations

import re

import pytest

from framework.catalog import endpoints

pytestmark = pytest.mark.smoke

# None = all services. Set to a list to scope (e.g. ["virtualserver"]).
_CHAIN_SERVICES = None
_SMOKE_TSV = "reports/smoke_status.tsv"
_PLACEHOLDER = re.compile(r"\{([^}]+)\}")


def _categorize(status: int, text: str) -> str:
    """Same ok/soft/fail split as the smoke suite (only 5xx / HMAC-401 are hard
    fails; everything else is the API answering correctly given this account)."""
    t = (text or "").lower()
    if 200 <= status < 300:
        return "ok"
    if status == 401:
        return "soft" if ("rejected by gateway" in t or "catalog has not target" in t) else "fail"
    if status >= 500:
        return "fail"
    return "soft"  # 400/403/404/409/422 — needs params/permission/provisioning


def _record_smoke(status, category, key, method, path, elapsed_ms=None):
    import os
    ems = "" if elapsed_ms is None else f"{elapsed_ms:.0f}"
    try:
        os.makedirs("reports", exist_ok=True)
        with open(_SMOKE_TSV, "a") as fh:
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
    name, then a generic 'id', then 'name' (keypairs key on name), then the param
    with common suffixes stripped."""
    for k in (param, "id", "name", param.replace("_id", ""), param.replace("_name", ""),
              param.replace("_id", "_name")):
        if k and isinstance(item.get(k), (str, int)) and str(item.get(k)).strip():
            return str(item[k])
    return None


def _single_param_get_chains():
    """Every GET with exactly one path param, paired with its parent list = the
    path prefix before the first '{'. Scoped by _CHAIN_SERVICES (None = all)."""
    out = []
    svc_filter = set(_CHAIN_SERVICES) if _CHAIN_SERVICES else None
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


def _two_param_get_chains():
    """Every GET with exactly two path params, paired with the two lists that feed
    them: the parent collection for param1 (prefix before the first '{'), and the
    sub-collection template for param2 (everything up to the second '{', still
    holding {param1}). E.g. /v1/apis/{api_id}/resources/{resource_id} ->
    list1=/v1/apis, sublist=/v1/apis/{api_id}/resources. Scoped by _CHAIN_SERVICES."""
    out = []
    svc_filter = set(_CHAIN_SERVICES) if _CHAIN_SERVICES else None
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


_CHAINS = _single_param_get_chains()
_CHAINS_2P = _two_param_get_chains()


@pytest.fixture(scope="session")
def _list_cache():
    """Cache parent-list responses for the session: many show endpoints share one
    collection, so we list each (service, path) at most once."""
    return {}


@pytest.mark.parametrize(
    "endpoint,param,list_path",
    _CHAINS,
    ids=[e.key for e, _, _ in _CHAINS] or ["none"],
)
def test_read_chain(endpoint, param, list_path, client, _list_cache):
    """Derive the path-param id from the sibling list and exercise the show GET."""
    cache_key = (endpoint.service, list_path)
    if cache_key not in _list_cache:
        try:
            _list_cache[cache_key] = client.get(list_path, service=endpoint.service)
        except Exception as exc:
            _list_cache[cache_key] = exc
    lst = _list_cache[cache_key]
    if isinstance(lst, Exception):
        pytest.skip(f"parent list {list_path} unreachable: {lst}")
    if not lst.ok:
        pytest.skip(f"parent list {list_path} -> {lst.status}; no id to derive")

    items = _list_items(lst.body)
    rid = next((_id_from(it, param) for it in items if _id_from(it, param)), None)
    if rid is None:
        pytest.skip(f"no {param} available from {list_path} (empty/no id field)")

    path = endpoint.http_path.replace("{%s}" % param, rid)
    try:
        resp = client.get(path, service=endpoint.service)
    except Exception as exc:
        _record_smoke(0, "fail", endpoint.key, "GET", endpoint.http_path)
        pytest.skip(f"{path} unreachable: {exc}")  # record-only; never break the gate

    cat = _categorize(resp.status, getattr(resp, "raw_text", ""))
    _record_smoke(resp.status, cat, endpoint.key, "GET", endpoint.http_path,
                  getattr(resp, "elapsed_ms", None))
    # Record-only (matches CRUD probe_reads): a 4xx/5xx on a derived read is
    # surfaced on the dashboard, not asserted here, so bonus coverage can never
    # by itself turn the regression check red.


def _derive_id(client, cache, service, list_path, param):
    """List `list_path` (cached) and return the first usable `param` id, or a
    (None, skip_reason) pair. Mirrors the 1-param derivation."""
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


@pytest.mark.parametrize(
    "endpoint,p1,list1,p2,sublist_tmpl",
    _CHAINS_2P,
    ids=[e.key for e, *_ in _CHAINS_2P] or ["none"],
)
def test_read_chain_2p(endpoint, p1, list1, p2, sublist_tmpl, client, _list_cache):
    """Two-level derive: param1 from the parent list, then param2 from the
    sub-list (with param1 filled), then exercise the two-param show GET. Covers
    nested reads (e.g. /v1/apis/{api_id}/resources/{resource_id}) whenever both
    parent and child resources exist, with zero resource creation."""
    id1, why = _derive_id(client, _list_cache, endpoint.service, list1, p1)
    if id1 is None:
        pytest.skip(why)
    sublist = sublist_tmpl.replace("{%s}" % p1, id1)
    id2, why = _derive_id(client, _list_cache, endpoint.service, sublist, p2)
    if id2 is None:
        pytest.skip(why)

    path = endpoint.http_path.replace("{%s}" % p1, id1).replace("{%s}" % p2, id2)
    try:
        resp = client.get(path, service=endpoint.service)
    except Exception as exc:
        _record_smoke(0, "fail", endpoint.key, "GET", endpoint.http_path)
        pytest.skip(f"{path} unreachable: {exc}")  # record-only; never break the gate

    cat = _categorize(resp.status, getattr(resp, "raw_text", ""))
    _record_smoke(resp.status, cat, endpoint.key, "GET", endpoint.http_path,
                  getattr(resp, "elapsed_ms", None))
    # Record-only, exactly like test_read_chain above.
