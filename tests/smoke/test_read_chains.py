"""Read-only GET coverage booster via list->show chaining.

The catalog smoke suite (test_catalog_smoke.py) can only call GETs WITHOUT path
params; every ``show{X}/{id}`` endpoint is skipped because it needs a real
resource id. Those ids are normally only supplied by a CRUD lifecycle — which,
for heavy services, may be environment-blocked (e.g. VPC quota).

Many single-path-param GETs, though, take an id that is freely derivable from a
sibling *list* in the same service: ``/v1/server-types/{server_type_id}`` pairs
with ``/v1/server-types``. This module exercises those reads with zero resource
creation: list the parent collection, take the first item's id, and call the
show endpoint. Catalog-backed collections (server-types, volume-types, images)
yield coverage on every run; account-dependent ones (snapshots, keypairs, …)
are covered opportunistically when the account happens to hold such a resource,
and are skipped (not failed) when the list is empty.

Read-only and record-only, exactly like the CRUD ``probe_reads`` step: results
land in the same reports/smoke_status.tsv the dashboard reads, so they count
toward read coverage, and a probe never turns the suite red on its own.

Scope is intentionally limited to the services in ``_CHAIN_SERVICES``; extend
that list to widen coverage.
"""
from __future__ import annotations

import re

import pytest

from framework.catalog import endpoints

pytestmark = pytest.mark.smoke

_CHAIN_SERVICES = ["virtualserver"]
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


def _record_smoke(status, category, key, method, path):
    import os
    try:
        os.makedirs("reports", exist_ok=True)
        with open(_SMOKE_TSV, "a") as fh:
            fh.write(f"{status}\t{category}\t{key}\t{method}\t{path}\n")
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
    name, then a generic 'id', then 'name' (keypairs key on name)."""
    for k in (param, "id", "name", param.replace("_id", ""), param.replace("_name", "")):
        if k and isinstance(item.get(k), (str, int)) and str(item.get(k)).strip():
            return str(item[k])
    return None


def _single_param_get_chains():
    """virtualserver (and any _CHAIN_SERVICES) GETs with exactly one path param,
    paired with the parent list = path prefix before the first '{'."""
    out = []
    for svc in _CHAIN_SERVICES:
        for e in endpoints(service=svc, method="GET", resolved_only=True):
            params = _PLACEHOLDER.findall(e.http_path)
            if len(params) != 1:
                continue
            prefix = e.http_path.split("{", 1)[0].rstrip("/")
            if not prefix:
                continue
            out.append((e, params[0], prefix))
    return out


_CHAINS = _single_param_get_chains()


@pytest.mark.parametrize(
    "endpoint,param,list_path",
    _CHAINS,
    ids=[e.key for e, _, _ in _CHAINS] or ["none"],
)
def test_read_chain(endpoint, param, list_path, client):
    """Derive the path-param id from the sibling list and exercise the show GET."""
    try:
        lst = client.get(list_path, service=endpoint.service)
    except Exception as exc:  # parent list unreachable — nothing to derive from
        pytest.skip(f"parent list {list_path} unreachable: {exc}")
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
    _record_smoke(resp.status, cat, endpoint.key, "GET", endpoint.http_path)
    # Record-only (matches CRUD probe_reads): a 4xx/5xx on a derived read is
    # surfaced on the dashboard, not asserted here, so bonus coverage can never
    # by itself turn the regression check red.
