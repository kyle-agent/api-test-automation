"""Registry-driven reconciler — support concern C (guaranteed teardown).

Sweeps the account for resources that are *ours* (owner tag or legacy name
prefix) AND safe to delete (run finished / TTL expired), then removes them in
dependency order.

Ownership check (two-tier):
  1. TAG-BASED (preferred): ``core.registry.is_owned(item)`` returns True when
     the resource carries ``owner=apitest``.  This is the source-of-truth for
     all resources created by the new framework paths.
  2. PREFIX FALLBACK: legacy resources (created before tag support was added)
     are identified by run-stamped name prefixes (``regr*``, ``zznet*`` per
     collection).  ``core.registry.is_owned(item, name_prefixes=(...))``
     handles both checks in one call.

Deletion guard (account-wide sweep):
  Only resources satisfying ``is_owned AND (is_expired OR prefix-fallback-only)``
  are deleted.  A resource that carries our owner tag but whose TTL has NOT yet
  passed is a *live resource from a concurrent run* — the reconciler skips it.
  Tag-less resources matched only by prefix are always considered deletable
  (they have no TTL, so they're legacy orphans by definition).

Ported verbatim from ``tools/cleanup_regression_resources.py``:
  * ``_items`` / ``_name_of`` helpers
  * ``_delete`` / ``_wait_gone`` low-level primitives
  * ``_purge_vpc_children`` (name-agnostic VPC child purge by vpc_id)
  * Full dependency-ordered deletion sequence (steps 1-12) including
    - 409-retry loop on VPC deletes
    - dbaas issue-all-then-wait pattern
    - SCR 500-retry
    - SKE nodepool-first teardown
    - servicewatch bulk-delete body
    - secrets waiting_time_ndays body
    - SCF trigger-first teardown

Changed vs legacy:
  * Resource selection uses ``_is_candidate`` which combines tag check +
    prefix fallback instead of a bare ``startswith`` filter.
  * Deletion guard: tagged resources are skipped unless ``is_expired`` returns
    True; prefix-only matches are always deletable (legacy orphans).
  * ``_list`` no longer filters — ``_list_all`` returns raw items, and
    ``_select`` applies the ownership + expiry logic.
  * Imports come from ``core`` (``ApiClient``, ``settings``) instead of
    ``framework`` directly.
  * Safety gate: ``main()`` checks ``settings.allow_destructive`` (maps to
    ``SCP_ALLOW_DESTRUCTIVE=true``) before doing anything.
  * ``__main__`` guard so the module is side-effect-free on import.
"""
from __future__ import annotations

import time

import core
from core.registry import is_owned, is_expired

# ---------------------------------------------------------------------------
# Ownership / expiry helpers
# ---------------------------------------------------------------------------

def _extra_names() -> tuple[str, ...]:
    """SCP_SWEEP_EXTRA_NAMES — comma-separated EXACT resource names the
    operator wants reclaimed once (e.g. the pre-platform 'selftest' VPC that
    matches neither the owner tag nor the regr*/zznet* prefixes).  Set per
    run-request (sweep_extra_names=...), never a standing default."""
    import os
    raw = os.environ.get("SCP_SWEEP_EXTRA_NAMES", "")
    return tuple(n.strip() for n in raw.split(",") if n.strip())


def _is_candidate(item: dict, *, name_prefixes: tuple[str, ...] = ()) -> bool:
    """Return True if the resource is owned (by tag or prefix fallback)."""
    if str(item.get("name") or "") in _extra_names():
        return True
    return is_owned(item, name_prefixes=name_prefixes)


def _is_deletable(item: dict, *, name_prefixes: tuple[str, ...] = ()) -> bool:
    """Return True when the resource is safe to delete in an account-wide sweep.

    Rules:
    * If the resource carries the owner tag AND is not yet expired → live
      resource from a concurrent/ongoing run → SKIP.
    * If the resource carries the owner tag AND is expired → orphan → DELETE.
    * If the resource has no owner tag but matches a name prefix → legacy
      orphan (no TTL concept) → DELETE.

    FORCE override: ``SCP_SWEEP_IGNORE_TTL=true`` treats tagged-but-unexpired
    resources as deletable too. ONLY for explicitly requested cleanup runs
    when the operator knows no mutating run is live (a finished run's orphans
    keep their 6h TTL and would otherwise be protected until it passes).
    """
    import os
    from core.registry import _tag_value, OWNER_KEY, OWNER, RUN_KEY
    if str(item.get("name") or "") in _extra_names():
        return True
    has_tag = _tag_value(item, OWNER_KEY) == OWNER
    if has_tag:
        if os.environ.get("SCP_SWEEP_IGNORE_TTL", "").lower() == "true":
            return True
        # OWN-RUN override (2026-06-11): when the sweep runs, ITS run is over —
        # anything still alive with THIS run id is a failed-teardown leftover by
        # definition. The 6h TTL exists to protect OTHER (possibly live) runs;
        # honoring it for our own run let leftovers poison the NEXT run's VPC
        # cap (runs #3->#4: 10 lifecycles cap-skipped).
        my_run = os.environ.get("APITEST_RUN_ID", "")
        if my_run and _tag_value(item, RUN_KEY) == my_run:
            return True
        return is_expired(item)
    # No owner tag — matched only by prefix. Treat as legacy orphan.
    return bool(name_prefixes) and is_owned(item, name_prefixes=name_prefixes)


# ---------------------------------------------------------------------------
# Low-level HTTP helpers  (ported verbatim from legacy, import source updated)
# ---------------------------------------------------------------------------

def _items(body):
    if isinstance(body, dict):
        for v in body.values():
            if isinstance(v, list) and (not v or isinstance(v[0], dict)):
                return v
    return body if isinstance(body, list) else []


def _name_of(it):
    for k in ("name", "volume_name", "registry_name", "policy_name",
              "log_group_name"):
        if it.get(k):
            return str(it[k])
    return ""


def _list_all(client, service, path):
    """Return all items from a collection (no ownership filter)."""
    try:
        r = client.get(path, service=service)
    except Exception as exc:
        print(f"  list {path} error: {exc}")
        return []
    if not r.ok:
        print(f"  list {path} -> {r.status}")
        return []
    return [it for it in _items(r.body) if isinstance(it, dict)]


def _select(client, service, path, *, name_prefixes: tuple[str, ...] = (),
            match_token: bool = False, force_unnamed: bool = False):
    """List a collection and return only deletable items.

    Prefix fallback matches the item's display name via ``_name_of`` (some
    services use ``log_group_name``/``volume_name``/… instead of ``name``,
    which ``is_owned``'s bare ``name`` check would miss).

    Two extra matchers for platform-AUTO-created derivatives of our resources
    (field report 2026-06-10 — these leaked forever as "0 deletable"):
      * ``match_token`` — a TAG-LESS item also matches when ANY token of its
        name starts with a prefix ("snapshot for regrimggk…", "/scp/ske/regr…"):
        the platform names derivatives AFTER our regr* resource, not WITH it.
      * ``force_unnamed`` — in a FORCE sweep (SCP_SWEEP_IGNORE_TTL=true, the
        explicit post-run cleanup) a TAG-LESS item with NO name at all is ours
        too (dedicated test account; e.g. VM boot volumes / public IPs that
        list without any name key).
    """
    import os
    import re
    from core.registry import _tag_value, OWNER_KEY, OWNER
    force = os.environ.get("SCP_SWEEP_IGNORE_TTL", "").lower() == "true"
    listed = _list_all(client, service, path)
    picked, skipped = [], []
    for it in listed:
        name = _name_of(it)
        has_tag = _tag_value(it, OWNER_KEY) == OWNER
        if _is_deletable(it, name_prefixes=name_prefixes):
            picked.append(it)
            continue
        if name_prefixes and name.startswith(name_prefixes) and not it.get("name"):
            # name lives under an alternate key — apply the same legacy-orphan
            # rule is_owned would have applied to item["name"].
            picked.append(it)
            continue
        if (match_token and not has_tag and name_prefixes
                and any(t.startswith(tuple(name_prefixes))
                        for t in re.split(r"[\s/_,]+", name) if t)):
            picked.append(it)
            continue
        if force_unnamed and force and not has_tag and not name:
            picked.append(it)
            continue
        reason = ("live-ttl" if has_tag
                  else "unnamed" if not name else "name-mismatch")
        skipped.append(f"{name or '<unnamed>'}({reason})")
    if listed:
        print(f"  {path}: {len(listed)} listed / {len(picked)} deletable")
        if skipped:
            print(f"    skipped: {', '.join(skipped[:5])}"
                  + (" …" if len(skipped) > 5 else ""))
    return picked


def _delete(client, service, path, json=None):
    try:
        r = client.delete(path, service=service, json=json)
        return r.status
    except core.MutationBlocked as exc:
        print(f"  blocked: {exc}")
        return None
    except Exception as exc:
        print(f"  delete {path} error: {exc}")
        return None


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


def _purge_vpc_children(client, vid):
    """Delete EVERY child of a known-ours VPC by vpc_id, name-agnostic — for the
    stubborn leaked VPCs whose 409 blocker is NOT a regr/zznet-named resource
    (e.g. a port auto-created by an LB / NAT gateway). LB + NAT + internet gateways
    first (they own ports), then any remaining ports, then subnets after clearing
    their VIPs. Safe because we only call this for our own regr*/zznet* VPCs."""
    n = 0
    for svc, coll in (("loadbalancer", "/v1/loadbalancers"),
                      ("vpc", "/v1/nat-gateways"),
                      ("vpc", "/v1/internet-gateways"),
                      ("vpc", "/v1/ports")):
        try:
            items = _items(client.get(coll, service=svc).body)
        except Exception:
            continue
        for it in items:
            if isinstance(it, dict) and it.get("id") and str(it.get("vpc_id")) == vid:
                if _delete(client, svc, f"{coll}/{it['id']}"):
                    n += 1
                    _wait_gone(client, svc, f"{coll}/{it['id']}", 180, 10)
    try:
        subs = _items(client.get("/v1/subnets", service="vpc").body)
    except Exception:
        subs = []
    for sn in subs:
        if isinstance(sn, dict) and sn.get("id") and str(sn.get("vpc_id")) == vid:
            try:
                for vip in _items(client.get(f"/v1/subnets/{sn['id']}/vips",
                                             service="vpc").body):
                    if isinstance(vip, dict) and vip.get("id"):
                        _delete(client, "vpc",
                                f"/v1/subnets/{sn['id']}/vips/{vip['id']}")
            except Exception:
                pass
            if _delete(client, "vpc", f"/v1/subnets/{sn['id']}"):
                n += 1
                _wait_gone(client, "vpc", f"/v1/subnets/{sn['id']}", 120, 10)
    return n


# ---------------------------------------------------------------------------
# Main sweep
# ---------------------------------------------------------------------------

def run_sweep(client) -> int:
    """Execute the full dependency-ordered sweep. Returns count of deletions."""
    deleted = 0
    c = client

    # 1. servers (virtualserver) — delete then wait gone (frees subnet/sg)
    for it in _select(c, "virtualserver", "/v1/servers",
                      name_prefixes=("regrsrv",)):
        if _delete(c, "virtualserver", f"/v1/servers/{it['id']}"):
            deleted += 1
            _wait_gone(c, "virtualserver", f"/v1/servers/{it['id']}", 300, 15)

    # 2. keypairs + security-groups (independent)
    for it in _select(c, "virtualserver", "/v1/keypairs",
                      name_prefixes=("regrkey",)):
        if _delete(c, "virtualserver",
                   f"/v1/keypairs/{it.get('name')}"):
            deleted += 1
    for it in _select(c, "security-group", "/v1/security-groups",
                      name_prefixes=("regrsg",)):
        if _delete(c, "security-group",
                   f"/v1/security-groups/{it['id']}"):
            deleted += 1

    # 2b. ports (regrport) — subnet children; must go before the subnet pass.
    for it in _select(c, "vpc", "/v1/ports",
                      name_prefixes=("regrport", "zznetport")):
        if it.get("id") and _delete(c, "vpc", f"/v1/ports/{it['id']}"):
            deleted += 1

    # 2c. volume snapshots (regrsnap) then their block volumes (regrvol) —
    # snapshot first so the volume delete isn't blocked.
    for it in _select(c, "virtualserver", "/v1/snapshots",
                      name_prefixes=("regr",), match_token=True):
        if it.get("id") and _delete(
                c, "virtualserver", f"/v1/snapshots/{it['id']}"):
            deleted += 1
            _wait_gone(c, "virtualserver",
                       f"/v1/snapshots/{it['id']}", 300, 15)
    # Broad "regr" prefix on purpose: a VM create's INLINE boot volume is
    # auto-created by the platform — it carries NO registry tag (we only tag
    # what we create directly) and is named after the server (regrsrv*), not
    # regrvol*. delete_on_termination should reap it, but failed runs leave
    # tag-less regr* volumes behind (user-reported: 6 orphans).
    for it in _select(c, "virtualserver", "/v1/volumes",
                      name_prefixes=("regr", "zznet"),
                      match_token=True, force_unnamed=True):
        vid = it.get("id")
        if not vid:
            continue
        st = _delete(c, "virtualserver", f"/v1/volumes/{vid}")
        if st and (200 <= st < 300 or st == 404):
            deleted += 1
        else:
            print(f"  volume {_name_of(it)} ({vid}) delete -> {st}")

    # 2d. dbaas clusters (regr* per engine service) — MUST go before
    # subnets/vpcs. Issue all deletes, then wait each is gone.
    dbaas_deleted = []
    for svc in ("mysql", "postgresql", "mariadb", "epas", "cachestore",
                "eventstreams", "searchengine", "sqlserver", "vertica"):
        for it in _select(c, svc, "/v1/clusters", name_prefixes=("regr",)):
            cid = it.get("id")
            if cid and _delete(c, svc, f"/v1/clusters/{cid}"):
                deleted += 1
                dbaas_deleted.append((svc, cid))
    for svc, cid in dbaas_deleted:
        _wait_gone(c, svc, f"/v1/clusters/{cid}", 900, 20)

    # 3. subnets — delete all, then wait each is gone.
    subnet_ids = []
    for it in _select(c, "vpc", "/v1/subnets",
                      name_prefixes=("regrsub", "zznetsub")):
        if _delete(c, "vpc", f"/v1/subnets/{it['id']}"):
            deleted += 1
            subnet_ids.append(it["id"])
    for sid in subnet_ids:
        _wait_gone(c, "vpc", f"/v1/subnets/{sid}")

    # 3b. internet gateways + public IPs (regr*) — children that would
    # 409-block their VPC; delete them (and wait) before the vpc pass.
    for it in _select(c, "vpc", "/v1/internet-gateways",
                      name_prefixes=("regr", "zznet")):
        if it.get("id") and _delete(
                c, "vpc", f"/v1/internet-gateways/{it['id']}"):
            deleted += 1
            _wait_gone(c, "vpc",
                       f"/v1/internet-gateways/{it['id']}", 300, 15)
    for it in _select(c, "vpc", "/v1/publicips",
                      name_prefixes=("regr",), force_unnamed=True):
        if it.get("id") and _delete(c, "vpc", f"/v1/publicips/{it['id']}"):
            deleted += 1

    # 3b-2. VPC PEERINGS — must go before the VPCs they lock (run #5 evidence:
    # a peering stuck in CREATING blocks BOTH its VPCs with 409
    # related-resource, and a peering only becomes deletable after approval:
    # PUT .../approval {"type": "CREATE_APPROVE"} — the proven body). Approve
    # best-effort, then delete with a short 400/409 retry.
    for it in _select(c, "vpc", "/v1/vpc-peerings",
                      name_prefixes=("regrpeer",)):
        pid = it.get("id")
        if not pid:
            continue
        try:  # approval is a no-op 4xx if already ACTIVE/REJECTED — best-effort
            c.put(f"/v1/vpc-peerings/{pid}/approval", service="vpc",
                  json={"type": "CREATE_APPROVE"})
        except Exception:
            pass
        st = None
        for _ in range(6):
            st = _delete(c, "vpc", f"/v1/vpc-peerings/{pid}")
            if st and (200 <= st < 300 or st == 404):
                deleted += 1
                break
            time.sleep(15)
        if not (st and (200 <= st < 300 or st == 404)):
            print(f"  vpc-peering {pid} delete -> {st}")

    # 3c. shared-networking lifecycle children. private-dns holds quota;
    # transit-gateways and load-balancers would 409-block the vpc.
    for it in _select(c, "dns", "/v1/private-dns",
                      name_prefixes=("regrpdns", "zznetpdns")):
        if it.get("id") and _delete(c, "dns", f"/v1/private-dns/{it['id']}"):
            deleted += 1
            _wait_gone(c, "dns", f"/v1/private-dns/{it['id']}", 300, 15)
    for it in _select(c, "dns", "/v1/hosted-zones",
                      name_prefixes=("regr",)):
        if it.get("id") and _delete(c, "dns", f"/v1/hosted-zones/{it['id']}"):
            deleted += 1
    for it in _select(c, "vpc", "/v1/transit-gateways",
                      name_prefixes=("regrtgw", "zznettgw")):
        if it.get("id") and _delete(
                c, "vpc", f"/v1/transit-gateways/{it['id']}"):
            deleted += 1
            _wait_gone(c, "vpc",
                       f"/v1/transit-gateways/{it['id']}", 300, 15)

    # Load balancers + nat gateways have no regr name; delete any whose
    # vpc_id matches a regr* vpc. These would otherwise 409-block the vpc.
    regr_vpc_ids = {
        v["id"]
        for v in _select(c, "vpc", "/v1/vpcs",
                         name_prefixes=("regrvpc", "zznetvpc"))
        if v.get("id")
    }
    if regr_vpc_ids:
        for svc, coll in (("loadbalancer", "/v1/loadbalancers"),
                          ("vpc", "/v1/nat-gateways")):
            try:
                items = _items(c.get(coll, service=svc).body)
            except Exception:
                items = []
            for it in items:
                if (isinstance(it, dict) and it.get("id")
                        and str(it.get("vpc_id")) in regr_vpc_ids):
                    if _delete(c, svc, f"{coll}/{it['id']}"):
                        deleted += 1
                        _wait_gone(c, svc, f"{coll}/{it['id']}", 300, 15)

    # 4. vpcs — retry on 409 (lingering child), deleting any stray subnets
    deleted_vpc_ids = []
    for it in _select(c, "vpc", "/v1/vpcs",
                      name_prefixes=("regrvpc", "zznetvpc")):
        vid = it["id"]
        for attempt in range(6):
            st = _delete(c, "vpc", f"/v1/vpcs/{vid}")
            print(f"  delete vpc {it.get('name', vid)} ({vid}) -> {st}")
            if st in (200, 202, 204):
                deleted += 1
                deleted_vpc_ids.append(vid)
                break
            if st == 409:
                # Children remain — purge ALL of this vpc's children
                # (name-agnostic, by vpc_id) to catch un-prefixed leaks,
                # then retry.
                deleted += _purge_vpc_children(c, vid)
                time.sleep(10)
                continue
            break
    # VPC deletion is async (202); wait for each to actually disappear so the
    # account's VPC quota is freed before a subsequent CRUD run creates a VPC.
    for vid in deleted_vpc_ids:
        _wait_gone(c, "vpc", f"/v1/vpcs/{vid}", 300, 15)

    # 5. resource-groups
    for it in _select(c, "resourcemanager", "/v1/resource-groups",
                      name_prefixes=("regr-rg",)):
        if _delete(c, "resourcemanager",
                   f"/v1/resource-groups/{it['id']}"):
            deleted += 1

    # 6. container registries (scr) — delete may flaky-500, so retry.
    # repositories (regrrepo) — registry children; delete before the registry.
    for it in _select(c, "scr", "/v1/repositories",
                      name_prefixes=("regrrepo",)):
        if it.get("id") and _delete(c, "scr",
                                    f"/v1/repositories/{it['id']}"):
            deleted += 1
    for it in _select(c, "scr", "/v1/container-registries",
                      name_prefixes=("regrscr",)):
        rid = it.get("id")
        for _ in range(4):
            st = _delete(c, "scr",
                         f"/v1/container-registries/{rid}")
            print(f"  delete registry {_name_of(it)} ({rid}) -> {st}")
            if st in (200, 202, 204):
                deleted += 1
                break
            if st == 500:
                time.sleep(15)
                continue
            break

    # 7. filestorage volumes
    for it in _select(c, "filestorage", "/v1/volumes",
                      name_prefixes=("regrfs",)):
        vid = it.get("volume_id") or it.get("id")
        if vid and _delete(c, "filestorage", f"/v1/volumes/{vid}"):
            deleted += 1

    # 8. ske clusters (regrske) — delete their nodepools first, then cluster
    for it in _select(c, "ske", "/v1/clusters",
                      name_prefixes=("regrske",)):
        cid = it.get("id")
        try:
            nps = _items(c.get(f"/v1/clusters/{cid}/nodepools",
                               service="ske").body)
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
                deleted += 1
                break
            if st in (409, 500):
                time.sleep(30)
                continue
            break

    # 9. light, self-contained resources (no dependencies): certs, queues
    for it in _select(c, "certificatemanager", "/v1/certificatemanager",
                      name_prefixes=("regrcert",)):
        if it.get("id") and _delete(
                c, "certificatemanager",
                f"/v1/certificatemanager/{it['id']}"):
            deleted += 1
    for it in _select(c, "queueservice", "/v1/queues",
                      name_prefixes=("regrq",)):
        if it.get("id") and _delete(
                c, "queueservice", f"/v1/queues/{it['id']}"):
            deleted += 1
    # security groups created standalone (regrsg)
    for it in _select(c, "security-group", "/v1/security-groups",
                      name_prefixes=("regrsg",)):
        if it.get("id") and _delete(
                c, "security-group",
                f"/v1/security-groups/{it['id']}"):
            deleted += 1

    # 10. secrets (regrsec) — delete needs a waiting_time_ndays body. Sweep
    # these before their KMS keys, since a secret references a kms_id.
    for it in _select(c, "secretsmanager", "/v1/secrets",
                      name_prefixes=("regrsec",)):
        if it.get("id") and _delete(
                c, "secretsmanager", f"/v1/secrets/{it['id']}",
                json={"waiting_time_ndays": 7}):
            deleted += 1

    # 11. KMS keys. Lifecycles stamp several shapes (regrkms / regrskms /
    # regrswkms / regrkmsc — field sweep 2026-06-10 showed 15 skipped as
    # name-mismatch under the old two-prefix loop), so match the broad regr*
    # prefix in ONE pass like the other collections.
    for it in _select(c, "kms", "/v1/kms/transit",
                      name_prefixes=("regr",)):
        if it.get("id") and _delete(
                c, "kms", f"/v1/kms/transit/{it['id']}"):
            deleted += 1

    # 12. light create->read lifecycle types (scf, apigateway, iam, servicewatch).
    # scf cloud functions (regrscf): delete each function's triggers first,
    # then the function itself.
    for it in _select(c, "scf", "/v1/cloud-functions",
                      name_prefixes=("regrscf",)):
        fid = it.get("id")
        if not fid:
            continue
        try:
            trs = _items(c.get(
                f"/v1/triggers?cloud_function_id={fid}",
                service="scf").body)
        except Exception:
            trs = []
        for tr in trs:
            if isinstance(tr, dict) and tr.get("id"):
                _delete(c, "scf", f"/v1/triggers/{tr['id']}",
                        json={"cloud_function_id": fid,
                              "trigger_type": (tr.get("trigger_type")
                                               or "cronjob")})
        if _delete(c, "scf", f"/v1/cloud-functions/{fid}"):
            deleted += 1

    # apigateway apis (regrapi) — deleting the api removes its child resources.
    for it in _select(c, "apigateway", "/v1/apis",
                      name_prefixes=("regrapi",)):
        if it.get("id") and _delete(
                c, "apigateway", f"/v1/apis/{it['id']}"):
            deleted += 1

    # iam groups (regrgrp) + policies (regrpol)
    for it in _select(c, "iam", "/v1/groups",
                      name_prefixes=("regrgrp",)):
        if it.get("id") and _delete(c, "iam", f"/v1/groups/{it['id']}"):
            deleted += 1
    for it in _select(c, "iam", "/v1/policies",
                      name_prefixes=("regrpol",)):
        if it.get("id") and _delete(c, "iam", f"/v1/policies/{it['id']}"):
            deleted += 1

    # servicewatch alerts / dashboards / event-rules (regralert / regrdash /
    # regrevtrule) — same bulk-delete-by-ids shape as log groups. Their
    # lifecycles delete inline, but failed runs orphan them (user-reported).
    for path, prefix in (("/v1/alerts", "regralert"),
                         ("/v1/dashboards", "regrdash"),
                         ("/v1/event-rules", "regrevtrule")):
        for it in _select(c, "servicewatch", path, name_prefixes=(prefix,)):
            if not it.get("id"):
                continue
            st = _delete(c, "servicewatch", path, json={"ids": [it["id"]]})
            if st and (200 <= st < 300 or st == 404):
                deleted += 1
                continue
            # field 2026-06-10: 3 regrdash dashboards 400 on EVERY round with
            # the {ids:[…]} bulk body (shape unproven, ledger note). Log the
            # response body for diagnosis and try the one plausible alternate
            # envelope once before giving up.
            try:
                r = c.delete(path, service="servicewatch",
                             json={"dashboard_ids" if "dashboards" in path
                                   else "alert_ids" if "alerts" in path
                                   else "event_rule_ids": [it["id"]]})
                if r.ok or r.status == 404:
                    deleted += 1
                    print(f"  {path} {it['id']} deleted with alternate body key")
                    continue
                print(f"  {path} {it['id']} delete -> {st}; alt-key -> "
                      f"{r.status}: {(r.raw_text or '')[:200]}")
            except Exception as exc:
                print(f"  {path} {it['id']} delete -> {st}; alt-key error: {exc}")

    # servicewatch log groups (regrlg + service-auto-created). Gotchas found in
    # the field:
    #   * a group delete is REJECTED while the group still has log streams —
    #     and the custom-ingest lifecycle creates an implicit regrlg* group +
    #     stream with NO teardown of its own, so orphans always have streams;
    #   * _delete returns the raw HTTP status, and `if _delete(...)` is truthy
    #     even on 400/409 — the old bulk delete counted rejected deletes as
    #     deleted. Delete streams first, then groups one-by-one, and only
    #     count 2xx (404 = already gone);
    #   * services AUTO-CREATE log groups for our regr* resources with PATH
    #     names (`/scp/ske/regrske...`, `/scp/mysql/regry.../slowlog`) — they
    #     carry no owner tag and the name does not START with regr, so the
    #     plain prefix fallback skipped them forever (sweep logs: "20 listed /
    #     0 deletable"). Owner decision 2026-06-10: a log group whose name has
    #     ANY path segment starting with `regr` is ours — delete it.
    def _regr_log_group(it):
        name = _name_of(it)
        return name.startswith("regrlg") or any(
            seg.startswith("regr") for seg in name.split("/") if seg)

    _lg_listed = _list_all(c, "servicewatch", "/v1/log-groups")
    _lg_picked = [it for it in _lg_listed
                  if _is_deletable(it, name_prefixes=("regrlg",))
                  or _regr_log_group(it)]
    if _lg_listed:
        print(f"  /v1/log-groups: {len(_lg_listed)} listed / "
              f"{len(_lg_picked)} deletable (incl. auto-created /scp/*/regr*)")
    for it in _lg_picked:
        gid = it.get("id")
        if not gid:
            continue
        try:
            streams = _items(c.get(
                f"/v1/log-groups/{gid}/log-streams",
                service="servicewatch").body)
        except Exception:
            streams = []
        s_ids = [s["id"] for s in streams
                 if isinstance(s, dict) and s.get("id")]
        if s_ids:
            st = _delete(c, "servicewatch",
                         f"/v1/log-groups/{gid}/log-streams",
                         json={"ids": s_ids})
            if not (st and (200 <= st < 300 or st == 404)):
                print(f"  log-streams of {gid} delete -> {st}")
        st = _delete(c, "servicewatch", "/v1/log-groups",
                     json={"ids": [gid]})
        if st and (200 <= st < 300 or st == 404):
            deleted += 1
        else:
            print(f"  log-group {gid} delete -> {st}")

    print(f"sweep done: {deleted} resource(s) deleted")
    return deleted


def main() -> int:
    """Entry point for the account-wide reconciler sweep.

    Requires ``SCP_ALLOW_DESTRUCTIVE=true`` (maps to
    ``settings.allow_destructive``). Without it the sweep prints a
    dry-run notice and exits safely — no network calls are made.
    """
    if not core.settings.allow_destructive:
        print(
            "Reconciler: SCP_ALLOW_DESTRUCTIVE is not set — "
            "no deletions will be performed.\n"
            "Set SCP_ALLOW_DESTRUCTIVE=true to run a real sweep."
        )
        return 0

    core.settings.require_credentials()
    client = core.ApiClient(core.settings)
    # Run to a FIXED POINT (bounded): list endpoints may paginate, so one pass
    # can only reap the first page's worth — repeat until a full pass deletes
    # nothing (or 5 rounds).
    for rnd in range(1, 6):
        print(f"--- sweep round {rnd} ---", flush=True)
        if run_sweep(client) == 0:
            break
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
