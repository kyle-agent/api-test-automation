#!/usr/bin/env python3
"""Sweep leftover regression resources (named regr*) created by CRUD lifecycles.

A partially-failed lifecycle can leave resources behind. This deletes anything
named with the suite's prefixes, in dependency order (servers -> keypairs/sg ->
subnets -> vpcs -> resource-groups). Best-effort; requires SCP_ALLOW_DESTRUCTIVE.
Run it before the CRUD lifecycles (and any time, to clean up).
"""
from __future__ import annotations

from framework.client import ApiClient, MutationBlocked
from framework.config import settings

# (service, list_path, name_prefix, delete_path_template, id_field)
SWEEPS = [
    ("virtualserver", "/v1/servers", "regrsrv", "/v1/servers/{}", "id"),
    ("virtualserver", "/v1/keypairs", "regrkey", "/v1/keypairs/{}", "name"),
    ("security-group", "/v1/security-groups", "regrsg", "/v1/security-groups/{}", "id"),
    ("vpc", "/v1/subnets", "regrsub", "/v1/subnets/{}", "id"),
    ("vpc", "/v1/vpcs", "regrvpc", "/v1/vpcs/{}", "id"),
    ("resourcemanager", "/v1/resource-groups", "regr-rg", "/v1/resource-groups/{}", "id"),
]


def _items(body):
    """Return the first list-of-objects found in a list response body."""
    if isinstance(body, dict):
        for v in body.values():
            if isinstance(v, list) and (not v or isinstance(v[0], dict)):
                return v
    return body if isinstance(body, list) else []


def main() -> int:
    settings.require_credentials()
    client = ApiClient(settings)
    total = 0
    for service, list_path, prefix, del_tmpl, idf in SWEEPS:
        try:
            resp = client.get(list_path, service=service)
        except Exception as exc:
            print(f"[{service}] list {list_path} skipped: {exc}")
            continue
        if not resp.ok:
            print(f"[{service}] list {list_path} -> {resp.status} (skip)")
            continue
        leftovers = [it for it in _items(resp.body)
                     if isinstance(it, dict) and str(it.get("name", "")).startswith(prefix)]
        for it in leftovers:
            ident = it.get(idf)
            if not ident:
                continue
            try:
                d = client.delete(del_tmpl.format(ident))
                print(f"[{service}] deleted {it.get('name')} ({ident}) -> {d.status}")
                total += 1
            except MutationBlocked as exc:
                print(f"[{service}] would delete {it.get('name')} ({ident}) — {exc}")
            except Exception as exc:
                print(f"[{service}] delete {ident} failed: {exc}")
    print(f"sweep done: {total} resource(s) deleted")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
