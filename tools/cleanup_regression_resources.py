#!/usr/bin/env python3
"""Sweep leftover regression resources (named regr*) left by failed lifecycles.

Deletes in dependency order and waits for async deletion so parents (VPCs) can
be removed after their children (subnets). Best-effort but thorough: retries
VPC deletes that 409 on lingering subnets. Requires SCP_ALLOW_DESTRUCTIVE.
"""
from __future__ import annotations

import time

from framework.client import ApiClient, MutationBlocked
from framework.config import settings


def _items(body):
    if isinstance(body, dict):
        for v in body.values():
            if isinstance(v, list) and (not v or isinstance(v[0], dict)):
                return v
    return body if isinstance(body, list) else []


def _name_of(it):
    for k in ("name", "volume_name", "registry_name"):
        if it.get(k):
            return str(it[k])
    return ""


def _list(client, service, path, prefix):
    try:
        r = client.get(path, service=service)
    except Exception as exc:
        print(f"  list {path} error: {exc}"); return []
    if not r.ok:
        print(f"  list {path} -> {r.status}"); return []
    return [it for it in _items(r.body)
            if isinstance(it, dict) and _name_of(it).startswith(prefix)]


def _delete(client, service, path):
    try:
        r = client.delete(path, service=service)
        return r.status
    except MutationBlocked as exc:
        print(f"  blocked: {exc}"); return None
    except Exception as exc:
        print(f"  delete {path} error: {exc}"); return None


def _wait_gone(client, service, path, timeout=150, interval=10):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            if client.get(path, service=service).status == 404:
                return True
        except Exception:
            return True
        time.sleep(interval)
    return False


def main() -> int:
    settings.require_credentials()
    c = ApiClient(settings)
    deleted = 0

    # 1. servers (virtualserver) — delete then wait gone (frees subnet/sg)
    for it in _list(c, "virtualserver", "/v1/servers", "regrsrv"):
        if _delete(c, "virtualserver", f"/v1/servers/{it['id']}"):
            deleted += 1; _wait_gone(c, "virtualserver", f"/v1/servers/{it['id']}", 300, 15)
    # 2. keypairs + security-groups (independent)
    for it in _list(c, "virtualserver", "/v1/keypairs", "regrkey"):
        if _delete(c, "virtualserver", f"/v1/keypairs/{it.get('name')}"):
            deleted += 1
    for it in _list(c, "security-group", "/v1/security-groups", "regrsg"):
        if _delete(c, "security-group", f"/v1/security-groups/{it['id']}"):
            deleted += 1
    # 3. subnets — delete all, then wait each is gone
    subnet_ids = []
    for it in _list(c, "vpc", "/v1/subnets", "regrsub"):
        if _delete(c, "vpc", f"/v1/subnets/{it['id']}"):
            deleted += 1; subnet_ids.append(it["id"])
    for sid in subnet_ids:
        _wait_gone(c, "vpc", f"/v1/subnets/{sid}")
    # 4. vpcs — retry on 409 (lingering child), deleting any stray subnets first
    for it in _list(c, "vpc", "/v1/vpcs", "regrvpc"):
        vid = it["id"]
        for attempt in range(6):
            st = _delete(c, "vpc", f"/v1/vpcs/{vid}")
            print(f"  delete vpc {it['name']} ({vid}) -> {st}")
            if st in (200, 202, 204):
                deleted += 1; break
            if st == 409:  # children remain — delete this vpc's regr* subnets, retry
                for sn in _items(c.get("/v1/subnets", service="vpc").body):
                    if (isinstance(sn, dict) and sn.get("id")
                            and str(sn.get("vpc_id")) == vid
                            and str(sn.get("name", "")).startswith("regrsub")):
                        _delete(c, "vpc", f"/v1/subnets/{sn['id']}")
                        _wait_gone(c, "vpc", f"/v1/subnets/{sn['id']}", 120, 10)
                time.sleep(10)
                continue
            break
    # 5. resource-groups
    for it in _list(c, "resourcemanager", "/v1/resource-groups", "regr-rg"):
        if _delete(c, "resourcemanager", f"/v1/resource-groups/{it['id']}"):
            deleted += 1
    # 6. container registries (scr) — delete may flaky-500, so retry
    for it in _list(c, "scr", "/v1/container-registries", "regrscr"):
        rid = it.get("id")
        for _ in range(4):
            st = _delete(c, "scr", f"/v1/container-registries/{rid}")
            print(f"  delete registry {_name_of(it)} ({rid}) -> {st}")
            if st in (200, 202, 204):
                deleted += 1; break
            if st == 500:
                time.sleep(15); continue
            break
    # 7. filestorage volumes
    for it in _list(c, "filestorage", "/v1/volumes", "regrfs"):
        vid = it.get("volume_id") or it.get("id")
        if vid and _delete(c, "filestorage", f"/v1/volumes/{vid}"):
            deleted += 1
    # 8. ske clusters (regrske) — delete their nodepools first, then the cluster
    for it in _list(c, "ske", "/v1/clusters", "regrske"):
        cid = it.get("id")
        try:
            nps = _items(c.get(f"/v1/clusters/{cid}/nodepools", service="ske").body)
        except Exception:
            nps = []
        for np in nps:
            npid = np.get("id") if isinstance(np, dict) else None
            if npid:
                _delete(c, "ske", f"/v1/nodepools/{npid}")
                _wait_gone(c, "ske", f"/v1/nodepools/{npid}", 600, 30)
        for _ in range(8):
            st = _delete(c, "ske", f"/v1/clusters/{cid}")
            print(f"  delete cluster {_name_of(it)} ({cid}) -> {st}")
            if st in (200, 202, 204):
                deleted += 1; break
            if st in (409, 500):
                time.sleep(30); continue
            break
    print(f"sweep done: {deleted} resource(s) deleted")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
