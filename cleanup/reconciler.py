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

import os
import time

import core
from core.registry import is_owned, is_expired

# ---------------------------------------------------------------------------
# Ownership / expiry helpers
# ---------------------------------------------------------------------------

# ---------------------------------------------------------------------------
# Per-collection name-prefix families
# ---------------------------------------------------------------------------
# The /v1/vpcs collection holds ONLY VPCs, and every VPC a run creates is named
# from one of our run-stamped families. The old narrow ("regrvpc","zznetvpc")
# list missed shapes that don't put "vpc" right after "regr": the wave-5
# privatelink/firewall chains name their VPC ``regrw5vpc{unique}`` (field sweep,
# Wave E: ``regrw5vpc6a2d542e`` skipped as "name-mismatch" → never reclaimed →
# occupied the 5-VPC account cap → provision-failure cascade, IB-051).
#
# Broaden to the ``regr``/``zznet`` FAMILY roots so ALL our VPC name shapes
# (regrvpc*, regrvpcb*, regrw5vpc*, zznetvpc*, …) are recognised as owned +
# reclaimable. This stays SAFE against reclaiming non-owned VPCs because:
#   * the owner TAG is still the primary signal (is_owned checks it first), and
#   * SCP account built-ins (BillingplanFullAccess policy, "Cloud Functions" /
#     "File Storage" dashboards, …) are NOT VPCs and never carry a regr/zznet
#     name — nothing in this account names a VPC ``regr*``/``zznet*`` but us.
_VPC_NAME_PREFIXES = ("regr", "zznet")


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


# Per-campaign convergence cache (Task C, change 1). A (service, path) pass that
# lists ZERO deletable-owned items — either nothing of ours is left, or only
# un-deletable items remain (live-ttl, name-mismatch, or PF-09 pending-deletion
# items that _is_pending_deletion will skip anyway) — cannot produce a deletion
# in a later round either: nothing it would re-list is going to flip to deletable
# mid-sweep. Re-listing all ~30 collections every one of 5-8 rounds is the bulk
# of a 55-min sweep, so once a pass converges we skip RE-LISTING it next round.
#
# SAFETY: a collection is marked converged ONLY when _select picked 0 deletable
# items, so skipping it can never skip a real deletion. The very first round
# always lists every collection (the set starts empty), and the gating
# (is_owned/is_expired in _is_deletable) is untouched — convergence is decided
# AFTER ownership scoping, never instead of it. Opt out with
# SCP_SWEEP_NO_CONVERGE=true to force a full re-list every round.
_CONVERGED: set = set()


def _converge_enabled() -> bool:
    return os.environ.get("SCP_SWEEP_NO_CONVERGE", "").lower() != "true"


def _reset_campaign_state() -> None:
    """Clear all per-campaign convergence caches so a fresh sweep starts clean.
    Called at the top of ``main()``; also useful for hermetic tests that exercise
    multiple independent sweeps in one process (the module-level sets otherwise
    persist across calls)."""
    _CONVERGED.clear()
    _DELETED_THIS_SWEEP.clear()
    _DELETE_ISSUED.clear()
    _STUCK.clear()
    _REGION_CLIENTS.clear()
    _PROGRESS_THIS_ROUND[0] = 0


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
    # Converged-collection skip (Task C, change 1): a pass that picked nothing
    # deletable in an earlier round of THIS campaign is skipped — don't re-list.
    if _converge_enabled() and (service, path) in _CONVERGED:
        return []
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
    # Persistent-after-delete ("stuck") filtering — convergence fix. Drop any
    # owned item we ALREADY issued a delete for in a prior round but that is
    # STILL listed (same id): it is un-deletable with this credential/shape
    # (filestorage replication source 400; IAM-gated SKE log-group 200-but-stays),
    # so re-attempting it cannot progress and would re-arm the round loop. Mark
    # it stuck (report once) and exclude it from this pass. SAFETY: only items
    # that already passed the is_owned/is_expired gate above reach here, so this
    # never deletes/keeps anything based on a weakened ownership rule — it only
    # SUPPRESSES a known-futile retry of an owned item.
    if _converge_enabled():
        still: list = []
        for it in picked:
            iid = _item_id(it)
            if iid and iid in _STUCK:
                continue  # already known-stuck this campaign — silent skip
            if iid and iid in _DELETE_ISSUED:
                _STUCK[iid] = "persists after delete (un-deletable: dependency "
                _STUCK[iid] += "or IAM-gated child)"
                print(f"  stuck: {iid} ({_name_of(it) or path}) — "
                      f"deleted in a prior round but still listed; not retrying")
                continue
            still.append(it)
        picked = still
    if listed:
        print(f"  {path}: {len(listed)} listed / {len(picked)} deletable")
        if skipped:
            print(f"    skipped: {', '.join(skipped[:5])}"
                  + (" …" if len(skipped) > 5 else ""))
    # Convergence (Task C, change 1): this pass yields no further progress when
    # it picked nothing deletable, OR everything it picked is already in a
    # terminal pending-deletion state (PF-09) that the delete site skips. Either
    # way a later round would re-list the same un-actionable items, so cache the
    # pass as converged and skip re-listing it next round.
    if _converge_enabled() and (
            not picked or all(_is_pending_deletion(it) for it in picked)):
        _CONVERGED.add((service, path))
    return picked


# KMS/Secrets deletion is SCHEDULED (pending-deletion stays in lists for the
# whole window — service quirk, run 27401527554): a re-delete of something we
# already 2xx-deleted this sweep is not progress, or the round loop never
# reaches its fixed point and burns ~10 minutes re-deleting the same 40.
_DELETED_THIS_SWEEP: set = set()


# ---------------------------------------------------------------------------
# Persistent-after-delete ("stuck") tracking — convergence fix (8-round loop)
# ---------------------------------------------------------------------------
# Field 2026-06-22: a multi-round sweep ran to its MAX rounds every time because
# some owned items report a truthy DELETE status yet RE-LIST every round:
#   * filestorage replication source volumes — DELETE 400 "volume.purpose"
#     ("replication is in use"); the old code counted any truthy status as
#     "deleted" (logged deleted, nothing gone) → re-listed → looped.
#   * the IAM-blocked SKE log-group ``/scp/ske/regr*`` — bulk DELETE returns 200
#     but the group persists because a child log-stream sits behind a 403 IAM
#     gate this credential lacks → re-listed → looped.
# These items are un-deletable WITH THIS CREDENTIAL/SHAPE; re-attempting them
# every round can never make progress. So: remember the id of every owned item
# we ISSUED a delete for; if the SAME id is still listed in a LATER round, mark
# it STUCK and stop re-attempting it (report it once). Per-id, additive to the
# per-collection ``_CONVERGED`` cache — never widens ownership (selection still
# goes through is_owned/is_expired first); it only suppresses a known-futile
# retry so the sweep CONVERGES instead of looping.
_DELETE_ISSUED: set = set()   # ids we have issued a DELETE for this campaign
_STUCK: dict = {}             # id -> reason, for items still listed after delete
_PROGRESS_THIS_ROUND = [0]    # genuinely-gone deletions in the current round
                              # (boxed so run_sweep can reset/read it per round)


def _item_id(it: dict):
    """Stable id for stuck-tracking — covers the id-field variants the API uses
    across collections (id / volume_id / image_id / replication_id / name)."""
    for k in ("id", "volume_id", "image_id", "replication_id"):
        v = it.get(k)
        if v:
            return str(v)
    return _name_of(it) or None


def _is_2xx_or_gone(st) -> bool:
    """A DELETE outcome that represents real teardown: a 2xx (accepted) or 404
    (already gone). A 4xx (other than 404) / 409 / 5xx is NOT teardown — the
    item is still there. Centralised so no pass mistakes a rejection for success.
    """
    return bool(st) and ((200 <= st < 300) or st == 404)


def _mark_issued(it: dict) -> None:
    """Record that a delete for this owned item did NOT achieve teardown (its
    status was not 2xx/404), so if the SAME id is still listed next round we can
    mark it stuck and stop retrying. We mark on FAILURE only — a clean 2xx/404 is
    not recorded, so a legitimately-async delete (VPC/snapshot/dbaas; gone by the
    next round via _wait_gone or the round pause) is never falsely called stuck.
    """
    iid = _item_id(it)
    if iid:
        _DELETE_ISSUED.add(iid)


def _note_progress(st, it: dict | None = None) -> bool:
    """Decide whether a DELETE "counted" as real teardown, with stuck-tracking.

    Returns True (and counts one unit of genuine progress for the round's
    no-progress stop) iff ``st`` is a 2xx/404. A 4xx delete is NEVER tallied as
    deleted (Bug 2a). For stuck-tracking (Bug 3) we record the id ONLY for HARD,
    non-retryable rejections so a later re-list marks it stuck — but NOT for a
    ``409``: a 409 means "a child/dependency is still present", which the sweep
    is actively clearing in dependency order, so it must keep retrying across
    rounds (e.g. a volume that 409s while its snapshot/image is reaped this round
    deletes cleanly next round). Recording a 409 as stuck would strand a resource
    that is merely waiting on an in-flight dependency."""
    if _is_2xx_or_gone(st):
        _PROGRESS_THIS_ROUND[0] += 1
        return True
    if it is not None and st != 409:
        _mark_issued(it)
    return False


# PF-09: KMS keys and secrets enter a SCHEDULED ("pending deletion") state and
# linger in the list for the whole waiting-time window. They never disappear
# mid-sweep, so re-issuing DELETE to them every round keeps the fixed-point loop
# alive for nothing (~10 min wasted re-deleting the same 40). The list field
# observed live (2026-06-18):
#   * /v1/kms/transit item -> state="To_Be_Terminated" (delete_target_yn="Y")
#   * /v1/secrets    item -> state="To be terminated"   (deleted_at set)
# Normalise (lower-case, strip spaces/underscores/hyphens) and match a family of
# terminal-deletion tokens so vocabulary drift between services/regions is
# tolerated. This is a TERMINAL-STATE check only — it never affects ownership
# scoping (is_owned/is_expired still gate selection); it only suppresses a
# no-op re-DELETE of an item that is already on its way out.
_PENDING_DELETE_STATES = frozenset({
    "tobeterminated",      # kms transit:  "To_Be_Terminated"
    "toberterminated",     # tolerate a typo'd variant seen in some payloads
    "pendingdeletion",
    "pendingdelete",
    "scheduledfordeletion",
    "scheduleddeletion",
    "scheduled",
    "deleting",
    "deleted",
    "terminating",
    "terminated",
    "deletescheduled",
})


def _is_pending_deletion(item: dict) -> bool:
    """True when a KMS/secret item is already in a scheduled/pending-deletion
    terminal state (PF-09). Such an item never vanishes mid-sweep, so issuing
    DELETE again is a no-op that only keeps the round loop alive.

    Ownership-neutral: callers still go through ``_select`` (is_owned/is_expired
    gating) to decide the item is OURS; this only suppresses the redundant
    re-DELETE on a terminal item we already scheduled.
    """
    for field in ("state", "status"):
        v = item.get(field)
        if not isinstance(v, str):
            continue
        norm = v.lower().replace(" ", "").replace("_", "").replace("-", "")
        if norm in _PENDING_DELETE_STATES:
            return True
    return False


def _delete(client, service, path, json=None):
    key = (service, path, str(sorted((json or {}).items())))
    try:
        r = client.delete(path, service=service, json=json)
        if r.status and 200 <= r.status < 300:
            if key in _DELETED_THIS_SWEEP:
                # already 2xx-deleted this sweep — pending-deletion listing,
                # not progress (falsy return so no caller counts it)
                return None
            _DELETED_THIS_SWEEP.add(key)
        return r.status
    except core.MutationBlocked as exc:
        print(f"  blocked: {exc}")
        return None
    except Exception as exc:
        print(f"  delete {path} error: {exc}")
        return None


def _wait_gone(client, service, path, timeout=150, interval=10):
    # FAST sweep (SCP_SWEEP_NOWAIT=true): skip the per-resource blocking wait
    # entirely — issue every delete and let the fixed-point round loop (main)
    # retry whatever still 409s (dependency) on the next pass. Dependencies
    # resolve through retries instead of serial waits, so a sweep finishes in
    # rounds, not in sum(per-resource teardown). Async deletes complete in the
    # background; we only need to have ISSUED the delete.
    if os.environ.get("SCP_SWEEP_NOWAIT", "").lower() == "true":
        return True
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
    # 0. privatelink-services in this VPC must go FIRST — they own a customer
    # `prvlink-*` port that blocks every subnet/VPC delete, and the service itself
    # 409s while it has a connected endpoint. An AUTO-approved connection is ACTIVE
    # (not REJECT-able), so DISCONNECT each connected endpoint (provider side) then
    # delete the service; its customer port is reaped with it. (Learned reaping the
    # leaked wave5 privatelink VPC, 2026-06-18.)
    try:
        pls_items = _items(client.get("/v1/privatelink-services", service="vpc").body)
    except Exception:
        pls_items = []
    for pls in pls_items:
        if not (isinstance(pls, dict) and pls.get("id") and str(pls.get("vpc_id")) == vid):
            continue
        psid = pls["id"]
        try:
            eps = _items(client.get(f"/v1/privatelink-services/{psid}/connected-endpoints",
                                    service="vpc").body)
        except Exception:
            eps = []
        for ep in eps:
            eid = ep.get("id") if isinstance(ep, dict) else None
            if not eid:
                continue
            try:  # provider-side disconnect (PUT needs SCP_ALLOW_MUTATIONS)
                client.put(f"/v1/privatelink-endpoints/{eid}/connection",
                           service="vpc", json={"type": "DISCONNECT"})
            except Exception as exc:
                print(f"  privatelink disconnect {eid} -> {exc}")
        if _delete(client, "vpc", f"/v1/privatelink-services/{psid}"):
            n += 1
            _wait_gone(client, "vpc", f"/v1/privatelink-services/{psid}", 180, 10)
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
# filestorage replication teardown (Bug 2b) — pause + delete from the REPLICA
# ---------------------------------------------------------------------------
# A filestorage volume that participates in cross-region replication CANNOT be
# deleted while the replication is live: DELETE /v1/volumes/{id} -> 400
# filestorage.BadRequest.Invalid.volume.purpose ("Check the volume purpose";
# replication is in use). The replication must be PAUSED then DELETED, and that
# is only accepted from the REPLICA side (the source side always 400s "Check the
# volume purpose"). Proven call sequence (field 2026-06-22), all against the
# REPLICA volume's region/host:
#   PUT    /v1/replications/{rid}?volume_id={replica_id}
#          body {"replication_update_type":"policy","replication_policy":"paused"} -> 202
#   DELETE /v1/replications/{rid}?volume_id={replica_id}                            -> 202
#   then DELETE the source + replica volumes (retry after the async replication
#   delete finishes; an immediate volume delete still 400s on the race).
#
# NOTE ON DIRECTIONALITY: listvolumereplications (GET /v1/replications?volume_id=)
# returns the pair for EITHER endpoint, but the destructive PUT/DELETE only take
# from the replica. This helper is therefore called for the volume the sweep is
# CURRENTLY looking at; when that volume is the replica it tears the pair down,
# when it is the source the replica-side calls 400 harmlessly and the pair is
# reaped on the kr-east1 (replica-region) pass instead. Owned-only: the caller
# only invokes this for a volume already selected as ours by _select.
#
# TODO(verify-live, 2026-06-22): the account was clean when this was written, so
# this teardown is built from the hand-resolved live evidence above but NOT
# re-run end-to-end here. The two things to confirm on the next live filestorage
# replication leak: (1) the REPLICA-id field name on a listvolumereplications
# record — _replica_id_of tries several (replica_volume_id / destination_… /
# target_… / dst_… / secondary_…); if none match it falls back to addressing the
# volume the sweep is looking at, which still works because the kr-east1 pass
# hits the replica directly. (2) That a single pause+delete clears it (the source
# side should keep 400ing "Check the volume purpose"). The calls themselves are
# proven: PUT/DELETE /v1/replications/{rid}?volume_id={replica_id} (pause body
# {"replication_update_type":"policy","replication_policy":"paused"}), then the
# volume deletes. Best-effort + owned-only, so a wrong-side call just 4xxs
# harmlessly — safe to ship un-re-verified.

def _replica_id_of(rep: dict):
    """Best-effort: the REPLICA (destination) volume id in a replication record.
    Field shapes vary; try the documented/observed keys, newest-first."""
    for k in ("replica_volume_id", "destination_volume_id", "target_volume_id",
              "dst_volume_id", "secondary_volume_id"):
        v = rep.get(k)
        if v:
            return str(v)
    return None


def _teardown_filestorage_replication(client, volume_id: str) -> bool:
    """Pause + delete any replication this filestorage volume participates in,
    from the REPLICA side, so the underlying volumes become deletable. Returns
    True if a replication delete was ISSUED (caller should retry the volume
    delete after the async replication delete settles). Best-effort and
    idempotent: a wrong-side / already-gone call 4xxs harmlessly.

    Owned-neutral: invoked only for a volume the sweep already scoped as ours."""
    try:
        reps = _items(client.get(f"/v1/replications?volume_id={volume_id}",
                                 service="filestorage").body)
    except Exception as exc:
        print(f"  list replications for {volume_id} error: {exc}")
        return False
    issued = False
    for rep in reps:
        if not isinstance(rep, dict):
            continue
        rid = (rep.get("replication_id") or rep.get("id"))
        if not rid:
            continue
        # the destructive calls only succeed from the replica side; pick the
        # replica id when the record exposes it, else address this volume.
        side = _replica_id_of(rep) or volume_id
        base = f"/v1/replications/{rid}?volume_id={side}"
        try:  # PAUSE the replication policy (replica side) — 202 on success
            client.put(base, service="filestorage",
                       json={"replication_update_type": "policy",
                             "replication_policy": "paused"})
        except core.MutationBlocked as exc:
            print(f"  blocked: {exc}")
            return False
        except Exception as exc:
            print(f"  pause replication {rid} -> {exc}")
        # DELETE the replication. The replica-scoping ``?volume_id=`` must ride
        # on the path, so issue through the client directly (it still honours the
        # destructive safety gate via client._guard) rather than via _delete,
        # which keys its dedup cache on (service, path, json).
        st = None
        try:
            r = client.delete(base, service="filestorage")
            st = r.status
        except core.MutationBlocked as exc:
            print(f"  blocked: {exc}")
            return False
        except Exception as exc:
            print(f"  delete replication {rid} -> {exc}")
            st = None
        if _is_2xx_or_gone(st):
            issued = True
            print(f"  replication {rid} (replica {side}) pause+delete -> {st}")
        else:
            print(f"  replication {rid} (replica {side}) delete -> {st}")
    return issued


def _sweep_filestorage_volumes(client) -> int:
    """Reap owned (regrfs*) filestorage volumes, replication-aware. For each
    owned volume: tear down its replication FROM THE REPLICA SIDE first (pause +
    delete), then DELETE the volume — counting ONLY a genuine 2xx/404 as deleted
    (a 400 'volume.purpose' / replication-in-use is NOT progress and feeds the
    stuck detector). The async replication delete races the volume delete, so a
    same-round volume delete may still 400; the round loop retries next pass once
    the replication delete has settled. Returns this collection's deletion count.
    """
    deleted = 0
    for it in _select(client, "filestorage", "/v1/volumes",
                      name_prefixes=("regrfs",)):
        vid = it.get("volume_id") or it.get("id")
        if not vid:
            continue
        # Pause + delete any replication this volume is in (replica-side); makes
        # the volume deletable. Best-effort; safe (owned volume only).
        _teardown_filestorage_replication(client, str(vid))
        st = _delete(client, "filestorage", f"/v1/volumes/{vid}")
        if _note_progress(st, it):
            deleted += 1
        else:
            print(f"  filestorage volume {_name_of(it)} ({vid}) delete -> {st}")
    return deleted


# Cache the per-region clients so a multi-round sweep doesn't rebuild them.
_REGION_CLIENTS: dict = {}


def _sweep_regions() -> tuple[str, ...]:
    """SCP_SWEEP_REGIONS — comma-separated EXTRA regions to also sweep for
    region-scoped leaks the primary-region sweep can't see. The motivating case
    (field 2026-06-22): filestorage cross-region replication leaves a REPLICA
    volume in kr-east1 while the source lives in kr-west1; the sweep historically
    only visited the primary region, so the kr-east1 replica leaked (billable)
    forever. Set e.g. SCP_SWEEP_REGIONS=kr-east1 for an explicit account-wide
    cleanup. Empty by default — no behaviour change unless opted in.
    """
    raw = os.environ.get("SCP_SWEEP_REGIONS", "")
    return tuple(r.strip() for r in raw.split(",") if r.strip())


def _extra_region_clients(primary) -> list:
    """Build (and cache) an ApiClient per SCP_SWEEP_REGIONS entry that differs
    from the primary client's region, reusing the primary's credentials/config
    with only the region overridden. Returns [] when none are configured (the
    default), so the standard single-region sweep is unchanged. A client that
    can't be built (no usable region/host config) is skipped, never fatal.
    """
    import dataclasses
    cfg = getattr(primary, "cfg", None)
    if cfg is None:
        return []
    primary_region = getattr(cfg, "region", "")
    out = []
    for region in _sweep_regions():
        if not region or region == primary_region:
            continue
        if region in _REGION_CLIENTS:
            out.append(_REGION_CLIENTS[region])
            continue
        try:
            sub_cfg = dataclasses.replace(cfg, region=region)
            sub = core.ApiClient(sub_cfg)
        except Exception as exc:
            print(f"  extra-region client {region} skipped: {exc}")
            continue
        _REGION_CLIENTS[region] = sub
        out.append(sub)
        print(f"  also sweeping region {region} for filestorage replicas")
    return out


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

    # 2c-0. virtualserver CUSTOM IMAGES (regrimg*) — MUST go before the volume
    # pass. A custom image created from a VM volume PINS its source volume:
    #   DELETE /v1/volumes/{id} -> 400 Snapshot.InvalidSnapshotDeleteRequest
    #     "Volume linked to the Server Custom Image cannot be deleted."
    # so a leaked regrimg* image makes its source volume un-reapable forever
    # (8-round loop, field 2026-06-22). Dependency order is image -> snapshot ->
    # volume, so reap the image first; DELETE /v1/images/{id} -> 204 clears it.
    # Owned-only (regrimg prefix / owner tag) — never touches platform base
    # images (those carry no regr* name and no owner tag).
    for it in _select(c, "virtualserver", "/v1/images",
                      name_prefixes=("regrimg",), match_token=True):
        iid = it.get("id") or it.get("image_id")
        if not iid:
            continue
        st = _delete(c, "virtualserver", f"/v1/images/{iid}")
        if _note_progress(st, it):
            deleted += 1
            _wait_gone(c, "virtualserver", f"/v1/images/{iid}", 300, 15)
        else:
            print(f"  image {_name_of(it)} ({iid}) delete -> {st}")

    # 2c. volume snapshots (regrsnap) then their block volumes (regrvol) —
    # snapshot first so the volume delete isn't blocked.
    for it in _select(c, "virtualserver", "/v1/snapshots",
                      name_prefixes=("regr",), match_token=True):
        if it.get("id") and _note_progress(
                _delete(c, "virtualserver", f"/v1/snapshots/{it['id']}"), it):
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
        if _note_progress(st, it):
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
                         name_prefixes=_VPC_NAME_PREFIXES)
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
                      name_prefixes=_VPC_NAME_PREFIXES):
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

    # 7. filestorage volumes (replication-aware; primary region + any extra
    # SCP_SWEEP_REGIONS). Tears down a volume's replication from the replica side
    # before deleting it, and NEVER counts a 4xx delete as success (Bug 2).
    deleted += _sweep_filestorage_volumes(c)
    for extra in _extra_region_clients(c):
        deleted += _sweep_filestorage_volumes(extra)

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
        if _is_pending_deletion(it):
            continue  # PF-09: already scheduled — re-DELETE is a no-op
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
        if _is_pending_deletion(it):
            continue  # PF-09: already scheduled — re-DELETE is a no-op
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
            # dashboards' bulk body is dashboard_ids (DashboardBulkDeleteRequest
            # — live-confirmed runs 27398084089/27421363609); alerts/event-rules
            # keep ids with the alternate-key fallback below.
            primary = ({"dashboard_ids": [it["id"]]} if "dashboards" in path
                       else {"ids": [it["id"]]})
            st = _delete(c, "servicewatch", path, json=primary)
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

    # This pass does NOT go through _select (it has a bespoke path-segment owner
    # rule, _regr_log_group), so apply the same persistent-after-delete (stuck)
    # convergence guard here by hand. The motivating leak (field 2026-06-22): the
    # IAM-blocked SKE log-group `/scp/ske/regr*` — its bulk DELETE returns 200 but
    # the group PERSISTS because a child log-stream sits behind a 403 IAM gate
    # this credential lacks. The 200 looks like success, so the old code counted
    # it deleted, it re-listed next round, and the sweep ran to its max rounds.
    # Fix: a group we already issued a delete for and that is STILL listed is
    # stuck → report once, skip. Un-deletable (IAM-gated) items are reported, not
    # forced. Ownership is untouched (_is_deletable/_regr_log_group still gate).
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
        if _converge_enabled() and str(gid) in _STUCK:
            continue  # known-stuck (e.g. IAM-gated stream) — don't retry
        if _converge_enabled() and str(gid) in _DELETE_ISSUED:
            # we deleted it a prior round yet it persists → IAM-gated / un-reapable
            _STUCK[str(gid)] = "log-group persists after delete (IAM-gated child "
            _STUCK[str(gid)] += "log-stream: needs the log-stream IAM action)"
            print(f"  stuck: {gid} ({_name_of(it)}) — log-group still listed "
                  f"after delete (IAM-gated child stream); not retrying")
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
            if not _is_2xx_or_gone(st):
                print(f"  log-streams of {gid} delete -> {st}")
        st = _delete(c, "servicewatch", "/v1/log-groups",
                     json={"ids": [gid]})
        # Record the issued delete for stuck-detection regardless of status: the
        # bulk endpoint returns a deceptive 200 even when the group persists, so
        # persistence (re-listing next round) — not the status — is the signal.
        if _converge_enabled():
            _DELETE_ISSUED.add(str(gid))
        if _note_progress(st, it):
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
    _reset_campaign_state()   # fresh convergence/stuck caches for this campaign
    # Run to a FIXED POINT (bounded): list endpoints may paginate, so one pass
    # can only reap the first page's worth — repeat until a full pass deletes
    # nothing (or 5 rounds).
    nowait = os.environ.get("SCP_SWEEP_NOWAIT", "").lower() == "true"
    rounds = int(os.environ.get("SCP_SWEEP_ROUNDS", "8" if nowait else "5"))
    round_sleep = int(os.environ.get("SCP_SWEEP_ROUND_SLEEP_S", "12"))
    for rnd in range(1, rounds + 1):
        print(f"--- sweep round {rnd} ---", flush=True)
        _PROGRESS_THIS_ROUND[0] = 0          # reset genuine-teardown counter
        reported = run_sweep(client)
        genuine = _PROGRESS_THIS_ROUND[0]
        # Convergence stop (Bug 3): end the sweep as soon as a round makes no
        # REAL progress — i.e. nothing genuinely-gone (2xx/404) was reaped. This
        # is stricter than the legacy ``reported == 0`` because ``reported`` can
        # be inflated by passes that still tally a deceptive status; ``genuine``
        # counts only items that actually went away. Items that re-list after a
        # delete are now marked stuck (logged once) and not retried, so a sweep
        # with only stuck/un-deletable owned items left converges here instead of
        # looping to max rounds. Fall back to ``reported`` for any pass that
        # hasn't been routed through _note_progress yet (still ends on a 0-round).
        if genuine == 0 and reported == 0:
            break
        if genuine == 0:
            print(f"no genuinely-removed resource this round "
                  f"(reported={reported}); converged — stopping.")
            break
        # In FAST (no-wait) mode rounds fire back-to-back; pause briefly so
        # async deletes issued this round actually disappear before the next
        # pass retries their now-unblocked dependents.
        if nowait and rnd < rounds:
            time.sleep(round_sleep)
    if _STUCK:
        print(f"--- {len(_STUCK)} owned item(s) could not be deleted "
              f"(reported, not forced) ---")
        for iid, reason in _STUCK.items():
            print(f"  stuck: {iid} ({reason})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
