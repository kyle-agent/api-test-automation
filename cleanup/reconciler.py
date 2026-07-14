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

import json
import os
import re
import threading
import time
from pathlib import Path

import core
from core.registry import is_owned, is_expired

# Guards the module-level per-campaign counters/caches now that independent
# passes can run on worker threads (sweep parallelization). The boxed-int
# ``+= 1`` counters and the check-then-add dedup in ``_delete`` go through
# this lock. The remaining compound mutations (_STUCK two-step writes,
# _CONVERGED check-then-add in _select) stay unlocked because of a KEYSPACE
# INVARIANT the parallel design relies on: concurrently-running passes touch
# DISJOINT collections — every _TAIL_PASSES entry and every dbaas engine owns
# its own (service, path) and id namespace, so no two threads ever race on
# the same key. If you add a pass that shares a collection/id family with
# another concurrent pass, route its shared-state writes through this lock.
_STATE_LOCK = threading.Lock()

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
    """Items list of a collection response. The FIRST non-empty list-of-dicts
    wins; an empty list is only a FALLBACK. The old \"first list that is empty
    or dict-list\" rule broke on bodies whose pagination ``links: []`` precedes
    the real items key — live PF 2026-07-11: SKE ``GET /clusters/{id}/nodepools``
    returns ``{"count":1, "links":[], "nodepools":[…]}``, so the sweep saw 0
    nodepools, skipped nodepool teardown, and the cluster delete 409-looped
    (owner: "노드풀은 왜 삭제안해?"). ``links`` never carries collection items,
    so it is excluded from the fallback too."""
    if isinstance(body, dict):
        fallback = None
        for k, v in body.items():
            if not isinstance(v, list):
                continue
            if v and isinstance(v[0], dict):
                return v
            if not v and fallback is None and k != "links":
                fallback = v
        return fallback if fallback is not None else []
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

# 마지막 관측 스냅샷 (pick-기반 leftover report, 2026-07-14 wall-time 최적화).
# ``_select``가 패스마다 자신이 고른 deletable 아이템의 (id, name)을
# (service, path)별로 덮어쓴다. ``_leftover_report``는 스윕 종료 시 이 관측을
# 요약해 full dry-scan(``verify_clean.scan_owned`` — 전 컬렉션 ~30개 재나열,
# 수 분)을 대체한다: 리포트는 genuine=0으로 끝난 마지막 라운드의 픽이므로
# "방금 스윕이 지우지 못하고 남긴 것" 그 자체다. converged로 스킵된 패스는
# 마지막으로 실제 나열했던 관측을 유지한다 (그 사이 변할 수 있는 건
# pending-deletion 아이템의 자연 소멸뿐 — 리포트는 advisory). 소유권 판정은
# 건드리지 않는다: 여기 담기는 것은 이미 _is_deletable 게이트를 통과한
# 아이템의 기록뿐이다. 픽 정보가 비어 있으면 _leftover_report가 기존
# scan_owned로 폴백한다.
_LAST_PICKED: dict = {}   # (service, path) -> [(item_id, name), ...]


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
    _LAST_PICKED.clear()
    _PROGRESS_THIS_ROUND[0] = 0
    _INPROGRESS_THIS_ROUND[0] = 0


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
    # 마지막 관측 기록 (pick-기반 leftover report): 이 패스가 이번에 고른
    # deletable 아이템을 (service, path)별로 덮어쓴다 — 소유권 게이트
    # (_is_deletable) 통과 후의 기록일 뿐, 선택/삭제 로직에는 영향 없음.
    # converged 스킵으로 이 함수가 일찍 반환한 패스는 갱신되지 않아 마지막
    # 실제 관측이 유지된다. 스레드 규약: _TAIL_PASSES/dbaas 병렬 패스는
    # 서로 다른 (service, path) 키만 만지지만(_STATE_LOCK 모듈 주석의
    # keyspace invariant), dict 갱신은 규약대로 락을 잡는다.
    with _STATE_LOCK:
        _LAST_PICKED[(service, path)] = [
            (_item_id(it), _name_of(it)) for it in picked]
    if listed:
        print(f"  {path}: {len(listed)} listed / {len(picked)} deletable")
        if skipped:
            print(f"    skipped: {', '.join(skipped[:5])}"
                  + (" …" if len(skipped) > 5 else ""))
    # In-progress accounting (2026-07-03 TGW incident): an item in a
    # TRANSITIONAL deleting state (DELETING/TERMINATING/…) is mid-async-removal
    # — it will vanish on its own, but until it does it may 409-block parents
    # (a DELETING transit-gateway blocks its VPC). Count it so main() grants
    # another bounded round instead of declaring convergence.
    inprog = [it for it in picked if _is_async_deleting(it)]
    if inprog:
        _bump_inprog(len(inprog))
        print(f"  {path}: {len(inprog)} item(s) mid-async-deletion "
              f"(transitional state) — waiting, not converging")
    # Convergence (Task C, change 1): this pass yields no further progress when
    # it picked nothing deletable, OR everything it picked is already in a
    # terminal pending-deletion state (PF-09) that the delete site skips. Either
    # way a later round would re-list the same un-actionable items, so cache the
    # pass as converged and skip re-listing it next round. NEVER cache while a
    # picked item is mid-ASYNC-deletion — that collection WILL change (the item
    # drops out) and later rounds must re-observe it to keep the in-progress
    # signal alive until the chain (e.g. TGW → VPC) actually clears.
    if _converge_enabled() and not inprog and (
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
_INPROGRESS_THIS_ROUND = [0]  # owned items observed mid-async-deletion (state
                              # DELETING/TERMINATING/…) or deferred behind such a
                              # holder this round. NOT progress, NOT convergence:
                              # main() grants another bounded round while > 0.
                              # (2026-07-03 incident: a 202-accepted transit-
                              # gateway lists as DELETING for minutes; counting it
                              # as non-progress converged the sweep while it still
                              # 409-blocked its VPC — regrtgw*/regrvpcsh* leak.)


def _bump_inprog(n: int = 1) -> None:
    """Locked increment of the async-in-flight counter (threaded passes)."""
    with _STATE_LOCK:
        _INPROGRESS_THIS_ROUND[0] += n


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
        with _STATE_LOCK:
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


# TRANSITIONAL async-deletion states — distinct from the PF-09 SCHEDULED family
# above. A KMS key in "To_Be_Terminated" sits in the list for its whole waiting
# window (days): re-listing it is NOT progress and the sweep must CONVERGE past
# it. A transit-gateway in "DELETING" is the opposite: its 202 DELETE is landing
# within minutes, and while it lists it still 409-blocks its VPC — the sweep must
# WAIT (grant another bounded round), not converge (2026-07-03 incident, CI run
# 28648339307 + console2 FORCE log: TGW enum CREATING/ACTIVE/DELETING/DELETED/
# ERROR/EDITING; the sweep stopped "converged" with the TGW mid-deletion and the
# shared VPC leaked ~1 day). Deliberately EXCLUDES terminal spellings
# ("deleted"/"terminated") and CDN's "stopping" (that pass defers by design).
_ASYNC_DELETING_STATES = frozenset({
    "deleting",
    "terminating",
    "releasing",
    "deallocating",
    "removing",
    "destroying",
    "purging",
})


def _is_async_deleting(item: dict) -> bool:
    """True when the item reports a TRANSITIONAL deleting state — its removal is
    in flight and it will drop out of the list on its own within minutes. Such an
    item counts toward ``_INPROGRESS_THIS_ROUND`` (grants another bounded round)
    and must never converge-cache its collection. Ownership-neutral, like
    ``_is_pending_deletion``."""
    for field in ("state", "status"):
        v = item.get(field)
        if not isinstance(v, str):
            continue
        norm = v.lower().replace(" ", "").replace("_", "").replace("-", "")
        if norm in _ASYNC_DELETING_STATES:
            return True
    return False


# A transit-gateway's own DELETE is only accepted while its state is ACTIVE or
# ERROR — live evidence (HB4b repair-log, run 28827996068): "Transit Gateway
# state is not deletable state(Active, Error)" on a TGW sitting in CREATING/
# EDITING (the same transitional-non-active class that blocks child writes,
# CAMPAIGN-C3-100-repair-log.md #HB4b-2 item 2 — creating/updating a
# vpc-connection flips an ACTIVE TGW back to EDITING for a settle window that
# HAS measured >300s live). Unlike _ASYNC_DELETING_STATES (DELETING/…), this
# CREATING/EDITING window was NOT counted toward _INPROGRESS_THIS_ROUND by the
# TGW delete call below (only a 2xx/404 counted as genuine, nothing counted
# the 400) — so a sweep whose only remaining owned item was a transiently-
# EDITING TGW (no vpc-connection left for _vpc_409_holder to detect either)
# could report genuine=0/inprog=0 and converge ("stop") one round before the
# TGW would have settled, stranding it (and, transitively, its VPC) for a
# human FORCE re-sweep. _TGW_DELETABLE_STATES is the allow-list from that same
# live error string.
_TGW_DELETABLE_STATES = frozenset({"active", "error"})


def _is_tgw_settling(item: dict) -> bool:
    """True when a transit-gateway item's own ``state`` is present but is
    neither a already-``_is_async_deleting`` state nor a DELETE-acceptable one
    (ACTIVE/ERROR) — i.e. CREATING/EDITING. Callers should treat this the same
    as ``_is_async_deleting``: count toward ``_INPROGRESS_THIS_ROUND`` and skip
    the doomed-to-400 DELETE attempt this round rather than silently letting it
    fail uncounted."""
    v = item.get("state")
    if not isinstance(v, str):
        return False
    norm = v.lower().replace(" ", "").replace("_", "").replace("-", "")
    if norm in _ASYNC_DELETING_STATES:
        return False  # already handled by _is_async_deleting
    return norm not in _TGW_DELETABLE_STATES


def _delete_resp(client, service, path, json=None):
    """``_delete``와 같은 가드/예외 처리로 DELETE를 발행하되 ``(status, body)``
    를 반환한다 — VPC 409 본문의 ``related_resources``(SRN 홀더 목록)를 읽어야
    하는 홀더 자동회수 경로용. 2xx dedup 캐시는 안 태운다 (VPC/홀더 삭제는
    pending-deletion 재나열 클래스가 아니다)."""
    try:
        r = client.delete(path, service=service, json=json)
        return r.status, (r.body if isinstance(r.body, dict) else {})
    except core.MutationBlocked as exc:
        print(f"  blocked: {exc}")
        return None, {}
    except Exception as exc:
        print(f"  delete {path} error: {exc}")
        return None, {}


def _delete(client, service, path, json=None):
    key = (service, path, str(sorted((json or {}).items())))
    try:
        r = client.delete(path, service=service, json=json)
        if r.status and 200 <= r.status < 300:
            with _STATE_LOCK:   # atomic check-then-add (threaded passes)
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


def _wait_all_gone(client, pairs, timeout=150, interval=10):
    """Pass-level BARRIER for async deletes: poll every ``(service, path)`` in
    ``pairs`` until ALL 404 or one SHARED deadline passes.

    Replaces the per-item ``_wait_gone`` chains where blocking waits added up
    serially — a pass with N slow items cost up to N×timeout wall time (e.g.
    dbaas: 900s PER cluster) — with one shared window: the deletes were all
    issued before the barrier, so they drain concurrently server-side and the
    wall cost is ≈ the slowest single item. Dependency semantics are unchanged:
    every caller sits between "issue this pass's deletes" and "start the pass
    that needs them gone", exactly where the old per-item waits sat.

    Mirrors ``_wait_gone``'s contract per item: a GET error counts as gone, and
    ``SCP_SWEEP_NOWAIT=true`` skips the barrier entirely (the round loop
    resolves dependencies by retrying)."""
    if not pairs:
        return True
    if os.environ.get("SCP_SWEEP_NOWAIT", "").lower() == "true":
        return True
    remaining = list(pairs)
    deadline = time.monotonic() + timeout
    while remaining:
        still = []
        for service, path in remaining:
            try:
                if client.get(path, service=service).status != 404:
                    still.append((service, path))
            except Exception:
                pass  # like _wait_gone: an unreadable item counts as gone
        remaining = still
        if not remaining or time.monotonic() >= deadline:
            break
        time.sleep(interval)
    if remaining:
        print(f"  wait-all: {len(remaining)} item(s) still present at the "
              f"{timeout}s barrier — left to the next round")
    return not remaining


def _reap_lb_static_nat(client, lb_id: str) -> bool:
    """Delete an owned load balancer's static-NAT BEFORE the LB itself —
    children-first, same pattern as the TGW vpc-connection reaping above.

    REPAIR 2026-07-07 (HB4d, run 28835929967, OFFLINE repair — see
    CAMPAIGN-C3-100-repair-log.md §HB4d item 2): live incident chain —
    static-nat-create (201) fires, then a delete attempted immediately 400s
    `StaticNatNotDeletableState` (still CREATING) — the scenario's own
    lifecycle fix is a settle-poll before its explicit static-nat-delete step
    (see networking__loadbalancer.json wait-static-nat-active), but an
    INTERRUPTED run (crash / kill between static-nat-create and
    static-nat-delete) leaks a static-NAT that the lifecycle's own teardown
    never reaches — only this account-wide sweep sees it, and until now the
    sweep deleted the LB directly, which 409s ("associated") while the NAT is
    still attached, stranding the LB + its publicip (ATTACHED, not deletable)
    + (transitively) the shared VPC.

    GET `/v1/loadbalancers/{id}/static-nats` (networking/loadbalancer/
    showloadbalancerpublicnatip, response wraps `$.static_nat.state`,
    CONFIRMED from `data/api_docs.json`) always 200s for a valid LB — an LB
    with NO static-NAT reports `state: ""` (empty), one WITH a static-NAT
    reports a real state (e.g. "ACTIVE"/"CREATING"). Only issue the DELETE
    when a real state is present, so a nat-less LB isn't given a no-op DELETE
    call. No request body is sent (mirrors the orchestrator's own manual
    unwind for this exact incident: GET -> confirm ACTIVE -> bare
    `DELETE .../static-nats` -> 204; the documented DELETE has no path/body
    parameters other than loadbalancer_id, per
    networking/loadbalancer/deleteloadbalancerpublicnatip). Retries on 400
    (CREATING not yet settled) so a nat still mid-create gets a few chances
    to reach ACTIVE before the LB delete is attempted; a 404/403 (already
    gone / not entitled) is treated as done. Returns True if a delete was
    issued (2xx) so the caller can count it as sweep progress.
    """
    try:
        body = client.get(f"/v1/loadbalancers/{lb_id}/static-nats",
                           service="loadbalancer").body
    except Exception:
        return False
    nat = (body or {}).get("static_nat") if isinstance(body, dict) else None
    state = (nat or {}).get("state") if isinstance(nat, dict) else None
    if not state:
        return False  # no static-NAT attached — nothing to reap
    for attempt in range(6):
        st = _delete(client, "loadbalancer",
                     f"/v1/loadbalancers/{lb_id}/static-nats")
        if _is_2xx_or_gone(st):
            print(f"  lb-static-nat {lb_id} (state={state}) delete -> {st}")
            return True
        if st != 400:
            print(f"  lb-static-nat {lb_id} (state={state}) delete -> {st} "
                  f"(giving up, not a settle-state 400)")
            return False
        time.sleep(15)
    print(f"  lb-static-nat {lb_id} (state={state}) still not deletable "
          f"after retries — leaving for next sweep round")
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
    _pls_wait = []
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
            _pls_wait.append(("vpc", f"/v1/privatelink-services/{psid}"))
    _wait_all_gone(client, _pls_wait, 180, 10)
    # LB/NAT/IGW own ports, so barrier PER COLLECTION: all of one collection's
    # deletes are issued together, then awaited together, before the next
    # collection (ports) lists — the same child→parent ordering the old
    # per-item waits enforced, minus the serial wall time.
    for svc, coll in (("loadbalancer", "/v1/loadbalancers"),
                      ("vpc", "/v1/nat-gateways"),
                      ("vpc", "/v1/internet-gateways"),
                      ("vpc", "/v1/ports")):
        try:
            items = _items(client.get(coll, service=svc).body)
        except Exception:
            continue
        _coll_wait = []
        for it in items:
            if isinstance(it, dict) and it.get("id") and str(it.get("vpc_id")) == vid:
                if _delete(client, svc, f"{coll}/{it['id']}"):
                    n += 1
                    _coll_wait.append((svc, f"{coll}/{it['id']}"))
        _wait_all_gone(client, _coll_wait, 180, 10)
    subs = []
    for _sc in ("/v1/subnets", "/v1/subnets?type=VPC_ENDPOINT"):  # PF-47
        try:
            subs.extend(_items(client.get(_sc, service="vpc").body))
        except Exception:
            pass
    _pv_seen: set = set()
    subs = [s for s in subs if isinstance(s, dict) and s.get("id")
            and s["id"] not in _pv_seen and not _pv_seen.add(s["id"])]
    _sub_wait = []
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
                _sub_wait.append(("vpc", f"/v1/subnets/{sn['id']}"))
    _wait_all_gone(client, _sub_wait, 120, 10)
    return n


# VPC DELETE 409 본문이 명시하는 홀더 SRN — 형식 실측(run-892a):
#   srn:e::<acct>:<region>::<service>:<type>/<id>
# 플랫폼이 홀더를 직접 알려주므로 목록 스캔/추정 없이 그 id를 삭제한다.
# (run_scoped의 서브넷 전용 SRN 폴백을 일반화한 것 — 2026-07-11, DC 홀더가
# 어떤 목록 패스/holder 탐지에도 안 잡힌 채 VPC를 12회 409로 잡은 실증.)
_SRN_HOLDER = re.compile(
    r"srn:[^\s\"']*?::([a-z0-9-]+):([a-z0-9-]+)/([0-9a-f-]{8,})")
# type -> (service, collection). 매핑에 없는 타입은 보고만 하고 건너뛴다
# (소유 판단이 불가한 미지 타입을 지우지 않는다 — Hard Rule 3의 정신).
_SRN_DELETE_MAP = {
    "direct-connect": ("direct-connect", "/v1/direct-connects"),
    "subnet": ("vpc", "/v1/subnets"),
    "port": ("vpc", "/v1/ports"),
    "internet-gateway": ("vpc", "/v1/internet-gateways"),
    "nat-gateway": ("vpc", "/v1/nat-gateways"),
    "transit-gateway": ("vpc", "/v1/transit-gateways"),
    "loadbalancer": ("loadbalancer", "/v1/loadbalancers"),
    "load-balancer": ("loadbalancer", "/v1/loadbalancers"),
    "vpc-endpoint": ("vpc", "/v1/vpc-endpoints"),
    "vpc-peering": ("vpc", "/v1/vpc-peerings"),
    "privatelink-service": ("vpc", "/v1/privatelink-services"),
}


def _purge_409_holders(client, body: dict) -> int:
    """소유 VPC의 DELETE 409 본문 ``related_resources``(SRN)가 명시한 홀더를
    직접 삭제하고 삭제 발행 수를 반환한다. 안전 근거는 ``_purge_vpc_children``
    과 동일 — 이미 소유가 확인된 VPC의 삭제를 막는 자식만 대상이고, id는
    플랫폼이 명시한 것이다. direct-connect는 자식 routing-rules를 먼저 비운다
    (run-892a: rule이 남은 DC는 DELETE 409)."""
    srns = " ".join(
        str(x) for err in (body.get("errors") or [])
        for x in (err.get("related_resources") or []))
    n = 0
    for m in _SRN_HOLDER.finditer(srns):
        rtype, rid = m.group(2), m.group(3)
        svc_coll = _SRN_DELETE_MAP.get(rtype)
        if not svc_coll:
            print(f"  vpc-409 holder {rtype}/{rid}: 삭제 매핑 없는 타입 — 보고만")
            continue
        svc, coll = svc_coll
        if rtype == "direct-connect":
            try:
                for rr in _items(client.get(f"{coll}/{rid}/routing-rules",
                                            service=svc).body):
                    if isinstance(rr, dict) and rr.get("id"):
                        _delete(client, svc,
                                f"{coll}/{rid}/routing-rules/{rr['id']}")
            except Exception:
                pass
        st = _delete(client, svc, f"{coll}/{rid}")
        print(f"  vpc-409 holder {rtype} {rid} delete -> {st}")
        if _note_progress(st):   # 홀더 소멸 = genuine 진행 (라운드 유지 근거)
            n += 1
            _wait_all_gone(client, [(svc, f"{coll}/{rid}")], 180, 10)
    return n


_HOLDER_COLLS = (("loadbalancer", "/v1/loadbalancers", "loadbalancer"),
                 ("vpc", "/v1/nat-gateways", "nat-gateway"))


def _vpc_409_holder(client, vid, cache: dict | None = None) -> str | None:
    """Best-effort: NAME the still-present resource that 409-blocks a VPC delete
    (all read-only GETs). When a holder is detectable the VPC pass burns ONE
    attempt + prints "blocked by <holder>" and defers to the next round, instead
    of 6 identical noisy 409s against a dependency that only time (or a later
    pass/round) clears — the 2026-07-03 console2 FORCE log shape.

    Detectable holders:
      * an owned transit-gateway with a vpc-connection into this VPC. The
        connection must be enumerated via the NESTED per-TGW list
        (``GET /v1/transit-gateways/{id}/vpc-connections`` — 200, live-verified
        2026-07-04); the FLAT ``/v1/transit-gateway-vpc-connections`` is 403 for
        this account (knowledge/validated-facts.md 2026-07-04 block).
      * a loadbalancer / NAT gateway whose ``vpc_id`` matches (they are reaped by
        the pre-pass, but a mid-drain one still holds the VPC).
    Returns a human-readable description, or None (caller falls back to the
    blind purge-children + retry loop).

    ``cache`` (optional dict, scoped by the caller to ONE VPC pass of ONE
    round) memoises the TGW/connection/LB/NAT listings: with several blocked
    VPCs the old code re-listed all four collections PER VPC. Staleness is
    handled in BOTH directions: a cache HIT (holder named) may be a holder
    that has since drained — worst case the VPC defers to the next round,
    which re-lists fresh (never deletes anything extra). A cache MISS
    re-lists ONCE and rescans before answering None, so a holder that
    APPEARED after the cache was populated is never masked — the blind
    purge-children fallback only runs on listings exactly as fresh as the
    pre-cache behavior."""
    if cache is None:
        cache = {}

    def _populate():
        pairs = []
        for t in _list_all(client, "vpc", "/v1/transit-gateways"):
            if not (t.get("id") and _is_candidate(
                    t, name_prefixes=("regrtgw", "zznettgw"))):
                continue
            try:
                conns = _items(client.get(
                    f"/v1/transit-gateways/{t['id']}/vpc-connections",
                    service="vpc").body)
            except Exception:
                conns = []
            pairs.append((t, conns))
        cache["tgw_conns"] = pairs
        for svc, coll, _label in _HOLDER_COLLS:
            cache[coll] = _list_all(client, svc, coll)

    def _scan():
        for t, conns in cache["tgw_conns"]:
            for cn in conns:
                if isinstance(cn, dict) and str(cn.get("vpc_id")) == str(vid):
                    return (f"transit-gateway {_name_of(t) or t['id']} "
                            f"vpc-connection {cn.get('id')} "
                            f"(state={cn.get('state')})")
        for _svc, coll, label in _HOLDER_COLLS:
            for x in cache.get(coll, []):
                if isinstance(x, dict) and x.get("id") \
                        and str(x.get("vpc_id")) == str(vid):
                    return (f"{label} {_name_of(x) or x['id']} "
                            f"(state={x.get('state')})")
        return None

    fresh = "tgw_conns" not in cache
    if fresh:
        _populate()
    holder = _scan()
    if holder or fresh:
        return holder
    # MISS against possibly-stale listings — refresh once and rescan so a
    # holder that appeared mid-round is reported instead of silently missed.
    _populate()
    return _scan()


# ---------------------------------------------------------------------------
# filestorage replication teardown (Bug 2b) — pause + delete from the REPLICA
# ---------------------------------------------------------------------------
# OWNER-GRANTED PROCEDURE (2026-07-09, live-proven end-to-end the same day —
# every step 2xx): "파일스토리지는 다른 리전으로 복제를 한 경우 해당(상대)
# 리전에서 복제 정책: 일시중지 → 삭제 로 두 번 변경한 후 snapshot 등이 정리
# 되어야 west/east 둘 다 정리 가능". I.e. from the COUNTERPART (replica) region:
#   ① PUT    /v1/replications/{rid}?volume_id={replica_id}
#            body {"replication_update_type":"policy","replication_policy":"paused"} -> 202
#            (enum is use|paused — docs ReplicationUpdateRequest; NOT PAUSE/SUSPEND)
#   ② DELETE /v1/replications/{rid}?volume_id={replica_id}                            -> 202
#            (record vanishes from BOTH regions' lists within ~20s)
#   ③ clean up snapshots etc. on BOTH volumes (replication auto-accrues
#            `snapmirror.*` SYSTEM snapshots on both sides — the replica's only
#            become listable AFTER ②; DELETE /v1/snapshots/{sid}?volume_id= —
#            the volume_id query is REQUIRED)
#   ④ only then are BOTH volumes deletable: replica (kr-east1) 202->404, source
#            (kr-west1) purpose reverts original->none then 202->404.
#
# RETROACTIVE ROOT CAUSE of the historical replica/volume delete 400
# (filestorage.BadRequest.Invalid.volume.purpose / "Check the volume purpose",
# observed 2026-06-24 + 2026-07-08): the PAUSE (①) was never issued and/or the
# calls were made from the SOURCE side — set/delete only take from the replica
# region host with the replica volume_id; the source side always 400s while
# purpose=original.
#
# NOTE ON DIRECTIONALITY: listvolumereplications (GET /v1/replications?volume_id=)
# returns the pair for EITHER endpoint, but the destructive PUT/DELETE only take
# from the replica. This helper is therefore called for the volume the sweep is
# CURRENTLY looking at; when that volume is the replica it tears the pair down,
# when it is the source the replica-side calls 400 harmlessly and the pair is
# reaped on the kr-east1 (replica-region) pass instead (SCP_SWEEP_REGIONS=kr-east1
# builds that pass — see _sweep_regions). Owned-only: the caller only invokes
# this for a volume already selected as ours by _select.
#
# RESOLVED (was TODO verify-live 2026-06-22; both points settled by the
# 2026-07-09 live teardown): (1) the REPLICA-id field on a list/show record is
# `replication_volume_id` (paired with `replication_volume_region`; same name as
# the create response field) — now the FIRST candidate in _replica_id_of; the
# legacy guesses are kept as fallbacks only. (2) one pause+delete DOES clear the
# pair — but per owner step ③ the snapmirror.* system snapshots must also be
# reaped before the volume deletes (handled in _sweep_filestorage_volumes).

def _replica_id_of(rep: dict):
    """Best-effort: the REPLICA (destination) volume id in a replication record.
    `replication_volume_id` is the live-confirmed field (2026-07-09; matches the
    docs Replication model + create response); the rest are legacy fallbacks."""
    for k in ("replication_volume_id", "replica_volume_id",
              "destination_volume_id", "target_volume_id",
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


def _reap_filestorage_snapshots(client, volume_id: str) -> None:
    """Owner step ③ (2026-07-09): delete the snapshots of an OWNED filestorage
    volume before the volume delete. Replication auto-accrues ``snapmirror.*``
    SYSTEM snapshots on both the source and the replica (the replica's only list
    once the replication is deleted); they are not regr-prefixed, so they are
    only reachable here — scoped strictly through ``?volume_id=`` of a volume
    _select already confirmed as ours (no name-guessing). The volume_id query is
    REQUIRED on both the list and the delete (docs + live 400 without it).
    Best-effort: any error just leaves the volume delete to 4xx and retry."""
    try:
        snaps = _items(client.get(f"/v1/snapshots?volume_id={volume_id}",
                                  service="filestorage").body)
    except Exception as exc:
        print(f"  list snapshots for {volume_id} error: {exc}")
        return
    for s in snaps:
        if not isinstance(s, dict):
            continue
        sid = s.get("id") or s.get("snapshot_id")
        if not sid:
            continue
        st = _delete(client, "filestorage",
                     f"/v1/snapshots/{sid}?volume_id={volume_id}")
        print(f"  fs snapshot {s.get('name') or sid} (vol {volume_id}) "
              f"delete -> {st}")


def _sweep_filestorage_volumes(client) -> int:
    """Reap owned (regrfs*) filestorage volumes, replication-aware. For each
    owned volume, follow the owner-granted order (2026-07-09, live-proven):
    ①② tear down its replication FROM THE REPLICA SIDE (pause + delete), ③ reap
    its snapshots (snapmirror.* system ones included), ④ then DELETE the volume
    — counting ONLY a genuine 2xx/404 as deleted (a 400 'volume.purpose' /
    replication-in-use is NOT progress and feeds the stuck detector). The async
    replication delete races the volume delete, so a same-round volume delete
    may still 400; the round loop retries next pass once the replication delete
    has settled. Returns this collection's deletion count.
    """
    deleted = 0
    for it in _select(client, "filestorage", "/v1/volumes",
                      name_prefixes=("regrfs",)):
        vid = it.get("volume_id") or it.get("id")
        if not vid:
            continue
        # ①② Pause + delete any replication this volume is in (replica-side);
        # makes the volume deletable. Best-effort; safe (owned volume only).
        _teardown_filestorage_replication(client, str(vid))
        # ③ snapshots must be cleaned before the volume can go (owner 2026-07-09).
        _reap_filestorage_snapshots(client, str(vid))
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
# Parallel execution of INDEPENDENT passes
# ---------------------------------------------------------------------------
# The sweep's wall time was dominated by strictly-serial listing + deleting of
# ~30 collections even though most of the tail (steps 5-12: resource-groups,
# SCR, filestorage, SKE, certs/queues, secrets→KMS, SCF, apigateway, CDN, IAM,
# servicewatch) has NO cross-collection dependency — only the networking chain
# (servers → … → VPCs) must stay ordered. Independent passes now run on a
# small thread pool; each pass keeps its INTERNAL ordering (repos before
# registries, secrets before KMS, nodepools before clusters, …) untouched.
#
# SAFETY: parallelism changes only WHEN a pass runs, never WHAT it selects —
# ownership gating (_select / _is_deletable) is per-pass and unchanged. The
# shared ApiClient is thread-safe (requests.Session + stateless per-request
# signing; the pool is sized for ~60 hosts), and the module-level campaign
# counters take _STATE_LOCK. A pass that raises is reported and costs only its
# own pass (the others complete) — same round-retry recovery as before.

def _sweep_workers() -> int:
    """SCP_SWEEP_PARALLEL — thread-pool size for independent sweep passes.
    Default 6 (modest vs. the API gateway; xdist test runs push it harder).
    Set 1 to force the legacy fully-serial sweep."""
    try:
        return max(1, int(os.environ.get("SCP_SWEEP_PARALLEL", "6")))
    except ValueError:
        return 6


def _map_parallel(fn, items):
    """Map ``fn`` over ``items`` on the sweep pool (ordered results). Falls back
    to a plain loop when the pool is disabled or pointless (≤1 item)."""
    items = list(items)
    workers = min(_sweep_workers(), len(items))
    if workers <= 1:
        return [fn(x) for x in items]
    from concurrent.futures import ThreadPoolExecutor
    with ThreadPoolExecutor(max_workers=workers,
                            thread_name_prefix="sweep") as ex:
        return list(ex.map(fn, items))


def _run_passes(passes, client) -> int:
    """Run ``(name, fn)`` passes — concurrently when the pool allows — and
    return the summed deletion count. A pass's exception is contained (printed,
    counted 0) so one broken service can't abort the rest of the sweep."""
    def _one(named):
        name, fn = named
        try:
            return fn(client) or 0
        except Exception as exc:  # noqa: BLE001 — isolate pass failures
            print(f"  pass {name} error: {exc}")
            return 0
    return sum(_map_parallel(_one, passes))


# ---------------------------------------------------------------------------
# Independent tail passes (steps 5-12) — one function per dependency island.
# Bodies are moved verbatim from the old inline run_sweep tail; each returns
# its own deletion count.
# ---------------------------------------------------------------------------

def _pass_resource_groups(c) -> int:
    # 5. resource-groups
    deleted = 0
    for it in _select(c, "resourcemanager", "/v1/resource-groups",
                      name_prefixes=("regr-rg",)):
        if _delete(c, "resourcemanager",
                   f"/v1/resource-groups/{it['id']}"):
            deleted += 1
    return deleted


def _pass_scr(c) -> int:
    # 6. container registries (scr) — delete may flaky-500, so retry.
    # repositories (regrrepo) — registry children; delete before the registry.
    deleted = 0
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
    return deleted


def _pass_filestorage(c) -> int:
    # 7. filestorage volumes (replication-aware; primary region + any extra
    # SCP_SWEEP_REGIONS). Tears down a volume's replication from the replica side
    # before deleting it, and NEVER counts a 4xx delete as success (Bug 2).
    deleted = _sweep_filestorage_volumes(c)
    for extra in _extra_region_clients(c):
        deleted += _sweep_filestorage_volumes(extra)
    return deleted


def _pass_ske(c) -> int:
    # 8. ske clusters (regrske) — delete their nodepools first, then cluster
    deleted = 0
    for it in _select(c, "ske", "/v1/clusters",
                      name_prefixes=("regrske",)):
        cid = it.get("id")
        try:
            nps = _items(c.get(f"/v1/clusters/{cid}/nodepools",
                               service="ske").body)
        except Exception:
            nps = []
        _np_wait = []
        for np in nps:
            npid = np.get("id") if isinstance(np, dict) else None
            if npid:
                _delete(c, "ske", f"/v1/nodepools/{npid}")
                _np_wait.append(("ske", f"/v1/nodepools/{npid}"))
        _wait_all_gone(c, _np_wait, 600, 30)
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
    return deleted


# Ledger-reclaim: consume the durable create-manifest the ENGINE already writes
# (reports/registry/*.jsonl, one ResourceRecord per create with a RESOLVED
# delete_path like /v1/queues/<real-id>). registry.py's docstring long claimed
# "the reconciler globs reports/registry/*.jsonl" but NO pass ever read it — the
# manifest was dead weight. That gap is exactly why an ABORTED run leaks: create
# succeeded (id captured + tracked) but the run died before its delete step, and
# the tag/name sweep can't reclaim resources the LIST API won't return by id.
# The exemplar is queueservice: listqueue (v1.1) returns only queue_urls (names),
# delete needs a 32-char id, and there is NO name→id resolver — so a queue whose
# id we didn't persist is un-reclaimable via the API (2026-07-13 오너 관측: 콘솔에
# regrq* 5개 잔존, 스위퍼 사각지대). Reading the ledger deletes them by their
# recorded id. (id-based DELETE /v1/queues/<id> VALIDATED live — 404 on a gone id,
# not the 400/403 the name/account-path forms hit.)
_REGISTRY_DIR = Path(__file__).resolve().parents[1] / "reports" / "registry"
# Skip ledger shards younger than this — an ACTIVE run appends to its shard right
# now, and deleting its in-flight resources would trample a concurrent run
# (Hard Rule 4). 15 min >> any single lifecycle's create→delete gap.
_LEDGER_MIN_AGE_S = float(os.environ.get("SCP_LEDGER_RECLAIM_MIN_AGE_S", "900"))


def _pass_ledger_reclaim(c) -> int:
    """Reclaim orphaned id-addressed resources from the engine's create-manifest.

    For each ``reports/registry/*.jsonl`` shard older than ``_LEDGER_MIN_AGE_S``,
    delete every recorded resource by its RESOLVED ``delete_path`` (skips records
    with an unresolved ``{token}`` or no id). 404 = already gone (success, prune).
    A shard whose every record is confirmed gone is removed so it isn't re-scanned.
    The manifest holds ONLY resources WE created, so this is owner-scoped by
    construction — no live-listing / tag match needed (that's the whole point:
    it reclaims what listing can't surface)."""
    try:
        shards = sorted(_REGISTRY_DIR.glob("*.jsonl"))
    except OSError:
        return 0
    now = time.time()
    deleted = 0
    for shard in shards:
        try:
            if now - shard.stat().st_mtime < _LEDGER_MIN_AGE_S:
                continue  # active/recent run's shard — do not touch (Hard Rule 4)
            lines = shard.read_text().splitlines()
        except OSError:
            continue
        recs = []
        for ln in lines:
            ln = ln.strip()
            if not ln:
                continue
            try:
                recs.append(json.loads(ln))
            except ValueError:
                continue
        # newest-first == children before parents (create order is parent→child)
        all_gone = True
        for rec in reversed(recs):
            dp = rec.get("delete_path") or ""
            svc = rec.get("service") or ""
            if not dp or "{" in dp or not rec.get("resource_id"):
                all_gone = False   # can't address it — keep the shard for audit
                continue
            st = _delete(c, svc, dp)
            if st is None:
                # dedup (already 2xx this sweep) or a transport error — treat as
                # progress-neutral; 404 comes back as a real status below.
                continue
            if 200 <= st < 300:
                deleted += 1
                print(f"  ledger-reclaim {svc} {dp} -> {st}")
            elif st == 404:
                pass  # already gone — confirmed reclaimed
            else:
                all_gone = False
                print(f"  ledger-reclaim {svc} {dp} -> {st} (retry next round)")
        if all_gone:
            try:
                shard.unlink()  # every record gone → prune so we stop re-scanning
            except OSError:
                pass
    return deleted


def _pass_certs_queues_sgs(c) -> int:
    # 9. light, self-contained resources (no dependencies): certs, queues
    deleted = 0
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
    return deleted


def _pass_secrets_kms(c) -> int:
    # 10. secrets (regrsec) — delete needs a waiting_time_ndays body. Sweep
    # these before their KMS keys, since a secret references a kms_id.
    deleted = 0
    _sec_pending = 0
    for it in _select(c, "secretsmanager", "/v1/secrets",
                      name_prefixes=("regrsec",)):
        if _is_pending_deletion(it):
            _sec_pending += 1
            continue  # PF-09: already scheduled — re-DELETE is a no-op
        if it.get("id") and _delete(
                c, "secretsmanager", f"/v1/secrets/{it['id']}",
                json={"waiting_time_ndays": 7}):
            deleted += 1
    if _sec_pending:
        print(f"  /v1/secrets: {_sec_pending} already scheduled-for-deletion "
              f"(PF-09 대기 소멸) — 재삭제 안 함")

    # 11. KMS keys. Lifecycles stamp several shapes (regrkms / regrskms /
    # regrswkms / regrkmsc — field sweep 2026-06-10 showed 15 skipped as
    # name-mismatch under the old two-prefix loop), so match the broad regr*
    # prefix in ONE pass like the other collections.
    _kms_pending = 0
    for it in _select(c, "kms", "/v1/kms/transit",
                      name_prefixes=("regr",)):
        if _is_pending_deletion(it):
            _kms_pending += 1
            continue  # PF-09: already scheduled — re-DELETE is a no-op
        if it.get("id") and _delete(
                c, "kms", f"/v1/kms/transit/{it['id']}"):
            deleted += 1
    if _kms_pending:
        print(f"  /v1/kms/transit: {_kms_pending} already scheduled-for-deletion "
              f"(To_Be_Terminated, PF-09) — 재삭제 안 함")
    return deleted


def _pass_scf(c) -> int:
    # 12. light create->read lifecycle types (scf, apigateway, iam, servicewatch).
    # scf cloud functions (regrscf + wave5의 regrw5scf/regrw5trg — 2026-07-10
    # 오너 실측: regrw5* 4건이 name-mismatch로 영구 스킵되던 커버리지 갭):
    # delete each function's triggers first,
    # then the function itself.
    deleted = 0
    for it in _select(c, "scf", "/v1/cloud-functions",
                      name_prefixes=("regrscf", "regrw5scf", "regrw5trg")):
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
        # PF-46 (2026-07-11 live): a function whose PrivateLink SERVICE is
        # enabled rejects DELETE (scp-cloud-function.function-not-deletable-
        # error) — disable it first via PUT …/configurations/privatelink-
        # services {"privatelink_service_enabled": false}. A service stuck in
        # CREATING rejects the deactivation too ("not allowed when Creating
        # state", observed stuck 3 weeks on regrw5trg*) — that function is
        # un-deletable until the backend settles; the 400 below feeds the
        # stuck-tracker so the sweep converges instead of retrying forever.
        plink = f"/v1/cloud-functions/{fid}/configurations/privatelink-services"
        try:
            pl = c.get(plink, service="scf").body or {}
            if isinstance(pl, dict) and pl.get("privatelink_service_enabled"):
                pr = c.put(plink, service="scf",
                           json={"privatelink_service_enabled": False})
                print(f"  scf {fid} privatelink-service disable -> {pr.status}")
        except core.MutationBlocked as exc:
            print(f"  blocked: {exc}")
        except Exception as exc:
            print(f"  scf {fid} privatelink-service check -> {exc}")
        st = _delete(c, "scf", f"/v1/cloud-functions/{fid}")
        if _note_progress(st, it):   # a 400 is NOT a deletion (Bug-2a class)
            deleted += 1
        else:
            print(f"  cloud-function {_name_of(it)} ({fid}) delete -> {st}")
    return deleted


def _pass_apigateway(c) -> int:
    # apigateway apis (regrapi) — deleting the api removes its child resources.
    deleted = 0
    for it in _select(c, "apigateway", "/v1/apis",
                      name_prefixes=("regrapi",)):
        if it.get("id") and _delete(
                c, "apigateway", f"/v1/apis/{it['id']}"):
            deleted += 1
    return deleted


def _pass_cdn(c) -> int:
    # cdn distributions (regr{ualpha}, networking__cdn lifecycle) — VPC-free
    # control-plane resources; the collection was never in this map, so 7 leaked
    # ACTIVE distributions accumulated (full-inventory sweep 2026-07-02). The
    # /v1/cdns collection holds ONLY CDN distributions and nothing in this
    # account names one regr* but us (same family-root argument as
    # _VPC_NAME_PREFIXES).
    #
    # DISABLE-BEFORE-DELETE QUIRKS (all live-proven 2026-07-02):
    #   * DELETE on an ACTIVE/STOPPING distribution -> **404 ResourceNotFound**
    #     even though GET/PUT/stop on the same id work — a MASKED state error.
    #     A CDN DELETE 404 therefore must NEVER be trusted as "already gone".
    #   * DELETE on STOPPED while activation is still PENDING_DEACTIVATION ->
    #     400 scp-network.cdn.service.property-invalid-state-delete.
    #   * Only a FULLY deactivated distribution deletes (202). stop (POST
    #     /v1/cdns/{id}/stop, body-less, 202) -> STOPPING for ~10-15 min ->
    #     STOPPED, then deactivation settles a few more minutes.
    # So this pass is a state machine, not a delete+retry: ACTIVE -> issue stop
    # and move on (a later round / the next sweep reaps it once deactivation
    # completes); STOPPING -> wait (skip); anything else -> attempt DELETE and
    # count ONLY a 2xx (the 404 trap). A 400 invalid-state is transitional and
    # deliberately NOT fed to the stuck-tracker.
    deleted = 0
    for it in _select(c, "cdn", "/v1/cdns", name_prefixes=("regr", "zznet")):
        cid = it.get("id")
        if not cid:
            continue
        state = str(it.get("cdn_service_state") or "").upper()
        if state in ("ACTIVE", "DEPLOYING", "UPDATING"):
            try:  # stop takes no body (start/stop documented body-less)
                r = c.post(f"/v1/cdns/{cid}/stop", service="cdn")
                print(f"  cdn {_name_of(it)} ({cid}) {state} -> stop "
                      f"{r.status}; delete deferred to a later round/sweep")
            except core.MutationBlocked as exc:
                print(f"  blocked: {exc}")
            except Exception as exc:
                print(f"  cdn stop {cid} -> {exc}")
            continue
        if state == "STOPPING":
            print(f"  cdn {_name_of(it)} ({cid}) STOPPING — not yet deletable")
            continue
        st = _delete(c, "cdn", f"/v1/cdns/{cid}")
        if st and 200 <= st < 300:
            _note_progress(st)      # genuine teardown (no stuck-marking arg)
            deleted += 1
            # Deliberately per-item _wait_gone (NOT the _wait_all_gone
            # barrier): CDN teardown is a per-item state machine (stop →
            # deactivation settles → delete) and the pass runs on its own
            # worker thread, so this wait blocks nobody else.
            _wait_gone(c, "cdn", f"/v1/cdns/{cid}", 300, 15)
        else:
            # 404 here is the masked state error (resource persists) and 400
            # is the transitional invalid-state — both resolve with time, so
            # report and let a later round / the next sweep retry.
            print(f"  cdn {_name_of(it)} ({cid}) state={state} delete -> {st}")
    return deleted


def _pass_iam(c) -> int:
    # iam groups (regrgrp) + policies (regrpol)
    deleted = 0
    for it in _select(c, "iam", "/v1/groups",
                      name_prefixes=("regrgrp",)):
        if it.get("id") and _delete(c, "iam", f"/v1/groups/{it['id']}"):
            deleted += 1
    # policy name families: regrpol*/regrpolx* (canonical), regrgrpbpol*
    # (group-binding test), regrrolepol* (role-binding test) — the narrow
    # ("regrpol",) list left 2 regrgrpbpol* policies behind (full-inventory
    # sweep 2026-07-02). Account built-ins (BillingplanFullAccess, …) never
    # carry a regr* name, so the family roots stay safe.
    for it in _select(c, "iam", "/v1/policies",
                      name_prefixes=("regrpol", "regrgrpbpol", "regrrolepol")):
        if it.get("id") and _delete(c, "iam", f"/v1/policies/{it['id']}"):
            deleted += 1
    return deleted


def _pass_servicewatch(c) -> int:
    # servicewatch alerts / dashboards / event-rules (regralert / regrdash /
    # regrevtrule) — same bulk-delete-by-ids shape as log groups. Their
    # lifecycles delete inline, but failed runs orphan them (user-reported).
    deleted = 0
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
    return deleted


# The independent tail groups (steps 5-12). Order within each pass is a real
# dependency (repos→registries, secrets→KMS, nodepools→cluster); order BETWEEN
# entries is not — they may run in any order / concurrently.
_TAIL_PASSES = (
    ("ledger-reclaim", _pass_ledger_reclaim),
    ("resource-groups", _pass_resource_groups),
    ("scr", _pass_scr),
    ("filestorage", _pass_filestorage),
    ("ske", _pass_ske),
    ("certs-queues-sgs", _pass_certs_queues_sgs),
    ("secrets-kms", _pass_secrets_kms),
    ("scf", _pass_scf),
    ("apigateway", _pass_apigateway),
    ("cdn", _pass_cdn),
    ("iam", _pass_iam),
    ("servicewatch", _pass_servicewatch),
)


# ---------------------------------------------------------------------------
# Main sweep
# ---------------------------------------------------------------------------

def run_sweep(client) -> int:
    """Execute the full dependency-ordered sweep. Returns count of deletions."""
    deleted = 0
    c = client

    # 0. SKE teardown starts FIRST, on its own worker (owner feedback
    # 2026-07-11 "노드풀은 왜 삭제안해?"): the SKE pass historically sat in
    # the tail (step 8) — AFTER the VPC pass — yet SKE node ports PIN the
    # shared subnet, so round 1 always left the subnet+VPC pair to round 2
    # while nodepool teardown (≤10 min) hadn't even started. Kicking it off
    # here overlaps SKE teardown with the entire networking chain; it is
    # JOINED (bounded) right before the subnet retry below, so a round can
    # reap subnet→VPC as soon as the nodes are gone. Serial mode
    # (SCP_SWEEP_PARALLEL=1) keeps the legacy tail placement.
    ske_future = None
    if _sweep_workers() > 1:
        from concurrent.futures import ThreadPoolExecutor
        _ske_ex = ThreadPoolExecutor(max_workers=1,
                                     thread_name_prefix="sweep-ske")
        ske_future = _ske_ex.submit(_run_passes, (("ske", _pass_ske),), c)
        _ske_ex.shutdown(wait=False)

    # 1. servers (virtualserver) — issue every delete, then ONE barrier until
    # all are gone (frees subnet/sg). Server teardown drains concurrently
    # server-side, so the old delete→wait→delete→wait chain (300s PER server)
    # collapses to ≈ the slowest single server.
    _srv_wait = []
    for it in _select(c, "virtualserver", "/v1/servers",
                      name_prefixes=("regrsrv",)):
        if _delete(c, "virtualserver", f"/v1/servers/{it['id']}"):
            deleted += 1
            _srv_wait.append(("virtualserver", f"/v1/servers/{it['id']}"))
    _wait_all_gone(c, _srv_wait, 300, 15)

    # 2. launch-configurations, then keypairs + security-groups.
    # 2-lc. launch-configurations (regrlc*/regrasglc*) — full-inventory sweep
    # 2026-07-02 found a leaked ``regrlc371da604`` (compute__virtualserver /
    # wave4 lifecycles create them; the collection was never in this map).
    # Delete BEFORE keypairs: an LC pins a platform-derived keypair named
    # ``regrlckp{run}-{lc_id}`` that is only freed with the LC.
    for it in _select(c, "virtualserver", "/v1/launch-configurations",
                      name_prefixes=("regrlc", "regrasglc")):
        if it.get("id") and _note_progress(
                _delete(c, "virtualserver",
                        f"/v1/launch-configurations/{it['id']}"), it):
            deleted += 1

    # 2-sg. server groups (regrsgrp*, wave1 lifecycle) — same 2026-07-02 sweep
    # found 4 leaked; the collection was never in this map. No dependencies
    # once the servers (step 1) are gone.
    for it in _select(c, "virtualserver", "/v1/server-groups",
                      name_prefixes=("regrsgrp",)):
        if it.get("id") and _note_progress(
                _delete(c, "virtualserver",
                        f"/v1/server-groups/{it['id']}"), it):
            deleted += 1

    # 2. keypairs + security-groups (independent). Keypair name families:
    # regrkey* (canonical), regrlckp* (launch-config lifecycle + its
    # platform-derived ``regrlckp{run}-{lc_id}``), regraskp*/regraskpc*
    # (auto-scaling lifecycles) — the narrow ("regrkey",) list left a
    # regrlckp* keypair behind (full-inventory sweep 2026-07-02).
    for it in _select(c, "virtualserver", "/v1/keypairs",
                      name_prefixes=("regrkey", "regrlckp", "regraskp")):
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
    _img_wait = []
    for it in _select(c, "virtualserver", "/v1/images",
                      name_prefixes=("regrimg",), match_token=True):
        iid = it.get("id") or it.get("image_id")
        if not iid:
            continue
        st = _delete(c, "virtualserver", f"/v1/images/{iid}")
        if _note_progress(st, it):
            deleted += 1
            _img_wait.append(("virtualserver", f"/v1/images/{iid}"))
        else:
            print(f"  image {_name_of(it)} ({iid}) delete -> {st}")
    _wait_all_gone(c, _img_wait, 300, 15)

    # 2c. volume snapshots (regrsnap) then their block volumes (regrvol) —
    # snapshot first so the volume delete isn't blocked.
    _snap_wait = []
    for it in _select(c, "virtualserver", "/v1/snapshots",
                      name_prefixes=("regr",), match_token=True):
        if it.get("id") and _note_progress(
                _delete(c, "virtualserver", f"/v1/snapshots/{it['id']}"), it):
            deleted += 1
            _snap_wait.append(("virtualserver", f"/v1/snapshots/{it['id']}"))
    _wait_all_gone(c, _snap_wait, 300, 15)
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
    # subnets/vpcs. The nine engines are independent services (separate hosts,
    # separate collections), so list+issue runs per-engine on the sweep pool,
    # then ONE shared barrier until every issued cluster delete lands.
    def _dbaas_engine(svc):
        out = []
        try:
            for it in _select(c, svc, "/v1/clusters", name_prefixes=("regr",)):
                cid = it.get("id")
                if cid and _delete(c, svc, f"/v1/clusters/{cid}"):
                    out.append((svc, cid))
        except Exception as exc:  # isolate one engine's failure
            print(f"  dbaas {svc} pass error: {exc}")
        return out
    dbaas_deleted = [pair for res in _map_parallel(_dbaas_engine, (
        "mysql", "postgresql", "mariadb", "epas", "cachestore",
        "eventstreams", "searchengine", "sqlserver", "vertica"))
        for pair in res]
    deleted += len(dbaas_deleted)
    # NOTE: the dbaas barrier does NOT sit here any more (owner feedback
    # 2026-07-11, live run: "SUBNET 3개 중 연관 리소스 없는 것들은 바로
    # 지워도 되었을텐데"). It moved BELOW the first subnet attempt so a
    # subnet with no dbaas/VM tenant is reaped immediately instead of
    # stalling up to 900s behind cluster teardown it doesn't depend on.

    # 2z. VPC endpoints (regrvpce) — VPC children that 409-block their VPC.
    # COVERAGE GAP found 2026-07-09 (run-2b): the sweep had NO vpc-endpoints
    # pass at all, so an endpoint whose lifecycle delete 400'd (CREATING —
    # gen-vpc-endpoint) pinned the session-shared VPC (`regrvpcsh…` 8cdd0e0c)
    # ACTIVE for hours while every scan reported the collection converged.
    # Must run BEFORE the subnet/vpc passes.
    vpce_ids = []
    for it in _select(c, "vpc", "/v1/vpc-endpoints",
                      name_prefixes=("regrvpce",)):
        if it.get("id") and _delete(c, "vpc", f"/v1/vpc-endpoints/{it['id']}"):
            deleted += 1
            vpce_ids.append(it["id"])
    _wait_all_gone(c, [("vpc", f"/v1/vpc-endpoints/{e}") for e in vpce_ids])

    # 3. subnets — OPTIMISTIC first attempt BEFORE the dbaas barrier: a subnet
    # with no tenant deletes right away; one whose port is still held by a
    # draining dbaas cluster 409s (cheap, harmless) and is retried once below,
    # after the cluster barrier frees it. Only a REAL 2xx/404 joins the wait
    # list / progress tally — the old `if _delete(...)` also treated a truthy
    # 409 as deleted and would have parked the barrier on a subnet that was
    # never accepted for deletion.
    # PF-47 (2026-07-11 live): the bare /v1/subnets list HIDES subnets whose
    # type is VPC_ENDPOINT (enum GENERAL/LOCAL/VPC_ENDPOINT; only the ?type=
    # query reveals them — live: bare list 2, ?type=VPC_ENDPOINT +1 more). A
    # leaked endpoint-type subnet (regrsubb*/regrsubc*, gen-vpc-endpoint) was
    # therefore INVISIBLE to every sweep pass while it 409-held its VPC — the
    # morning "no detectable holder" strandings. Sweep BOTH collections; ids
    # deduped in case a future API change folds them together.
    subnet_ids, subnet_retry = [], []
    _seen_sub_ids: set = set()
    for _sub_coll in ("/v1/subnets", "/v1/subnets?type=VPC_ENDPOINT"):
        for it in _select(c, "vpc", _sub_coll,
                          name_prefixes=("regrsub", "zznetsub")):
            sid_ = it["id"]
            if sid_ in _seen_sub_ids:
                continue
            _seen_sub_ids.add(sid_)
            st = _delete(c, "vpc", f"/v1/subnets/{sid_}")
            if _is_2xx_or_gone(st):
                _note_progress(st)
                deleted += 1
                subnet_ids.append(sid_)
            elif st:
                print(f"  subnet {_name_of(it)} ({sid_}) delete -> {st} "
                      f"(tenant still draining — retried after the dbaas barrier)")
                subnet_retry.append(sid_)
    # dbaas barrier (moved from 2d): clusters release their subnet ports here.
    _wait_all_gone(c, [(svc, f"/v1/clusters/{cid}") for svc, cid in
                       dbaas_deleted], 900, 20)
    # SKE join (bounded): node ports must be gone before the subnet retry can
    # succeed. On timeout the teardown keeps draining in the background — the
    # retry below 409s cheaply and the in-progress grant hands it to the next
    # round.
    if ske_future is not None:
        from concurrent.futures import TimeoutError as _FTimeout
        try:
            deleted += ske_future.result(timeout=900)
        except _FTimeout:
            _bump_inprog()
            print("  ske teardown still in flight at the subnet-retry join — "
                  "deferring its subnets to the next round")
    for sid in subnet_retry:
        st = _delete(c, "vpc", f"/v1/subnets/{sid}")
        if _is_2xx_or_gone(st):
            _note_progress(st)
            deleted += 1
            subnet_ids.append(sid)
        else:
            print(f"  subnet {sid} delete -> {st} (still held — next round)")
    _wait_all_gone(c, [("vpc", f"/v1/subnets/{s}") for s in subnet_ids])

    # 3b. internet gateways + public IPs (regr*) — children that would
    # 409-block their VPC; delete them (and wait) before the vpc pass.
    _igw_wait = []
    for it in _select(c, "vpc", "/v1/internet-gateways",
                      name_prefixes=("regr", "zznet")):
        if it.get("id") and _delete(
                c, "vpc", f"/v1/internet-gateways/{it['id']}"):
            deleted += 1
            _igw_wait.append(("vpc", f"/v1/internet-gateways/{it['id']}"))
    _wait_all_gone(c, _igw_wait, 300, 15)
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

    # 3c-0. direct-connects (regrdc*) — run-892a 실증: 어떤 목록 패스에도,
    # holder 탐지(TGW/LB/NAT)에도 없던 DC가 공유 VPC를 12회 409로 잡았다
    # (run-scoped reap은 자식 routing-rule보다 DC를 먼저 시도해 409로 남김).
    # 자식 routing-rules를 먼저 비우고 DC 삭제 → 배리어, VPC 패스 전에.
    _dc_wait = []
    for it in _select(c, "direct-connect", "/v1/direct-connects",
                      name_prefixes=("regrdc",)):
        did = it.get("id")
        if not did:
            continue
        try:
            for rr in _items(c.get(f"/v1/direct-connects/{did}/routing-rules",
                                   service="direct-connect").body):
                if isinstance(rr, dict) and rr.get("id"):
                    _delete(c, "direct-connect",
                            f"/v1/direct-connects/{did}/routing-rules/{rr['id']}")
        except Exception:
            pass
        st = _delete(c, "direct-connect", f"/v1/direct-connects/{did}")
        if _note_progress(st, it):
            deleted += 1
            _dc_wait.append(("direct-connect", f"/v1/direct-connects/{did}"))
        else:
            print(f"  direct-connect {_name_of(it)} ({did}) delete -> {st}")
    _wait_all_gone(c, _dc_wait, 300, 15)

    # 3c. shared-networking lifecycle children. private-dns holds quota;
    # transit-gateways and load-balancers would 409-block the vpc.
    _pdns_wait = []
    for it in _select(c, "dns", "/v1/private-dns",
                      name_prefixes=("regrpdns", "zznetpdns")):
        if it.get("id") and _delete(c, "dns", f"/v1/private-dns/{it['id']}"):
            deleted += 1
            _pdns_wait.append(("dns", f"/v1/private-dns/{it['id']}"))
    _wait_all_gone(c, _pdns_wait, 300, 15)
    for it in _select(c, "dns", "/v1/hosted-zones",
                      name_prefixes=("regr",)):
        if it.get("id") and _delete(c, "dns", f"/v1/hosted-zones/{it['id']}"):
            deleted += 1
    # transit-gateways + their vpc-connections. Live evidence (2026-07-03/04
    # incident, run 28648339307 + console2 FORCE log): a TGW with a
    # vpc-connection does NOT reliably cascade on DELETE — the connection sits
    # DELETING (hours observed) while the TGW reports EDITING, and the pair
    # 409-blocks the shared VPC. The FLAT /v1/transit-gateway-vpc-connections
    # list is 403 for this account, but the NESTED per-TGW list is 200
    # (live-verified 2026-07-04) — so enumerate + delete each owned TGW's
    # connections FIRST, then the TGW. Transitional (DELETING) items count as
    # in-progress (main() grants another bounded round) instead of converging;
    # a rejected TGW delete is transitional (its connection is draining) and is
    # retried next round, never stuck-marked.
    _tgw_wait = []
    for it in _select(c, "vpc", "/v1/transit-gateways",
                      name_prefixes=("regrtgw", "zznettgw")):
        tid = it.get("id")
        if not tid:
            continue
        # 1) reap this owned TGW's vpc-connections (children; they block both
        #    the TGW delete and the connected VPC's delete). Owned-safe: only
        #    children of a TGW that already passed _select's ownership gate.
        try:
            conns = _items(c.get(
                f"/v1/transit-gateways/{tid}/vpc-connections",
                service="vpc").body)
        except Exception:
            conns = []
        for cn in conns:
            cnid = cn.get("id") if isinstance(cn, dict) else None
            if not cnid:
                continue
            if _is_async_deleting(cn):
                _bump_inprog()
                print(f"  tgw-vpc-connection {cnid} (tgw {tid}) already "
                      f"{cn.get('state')} — waiting, not re-deleting")
                continue
            cst = _delete(
                c, "vpc", f"/v1/transit-gateways/{tid}/vpc-connections/{cnid}")
            if _note_progress(cst):
                deleted += 1
            print(f"  tgw-vpc-connection {cnid} (tgw {tid}) delete -> {cst}")
        # 2) the TGW itself. Mid-deletion → skip the no-op re-DELETE (already
        #    counted in-progress by _select's async check). A first 202 IS
        #    genuine progress (_note_progress) — the old bare `if _delete(...)`
        #    also counted a truthy 409 as a deletion, which inflated `reported`
        #    and helped the premature "converged" stop.
        if _is_async_deleting(it):
            continue
        # REPAIR 2026-07-07 (HB4b-2, offline, see CAMPAIGN-C3-100-repair-log.md
        # #HB4b-2 item 5): a TGW still CREATING/EDITING (settling after its own
        # create or after a vpc-connection create/delete) 400s "not deletable
        # state(Active, Error)" on DELETE — that failure was never counted
        # in-progress, so a sweep whose only remaining item was such a TGW
        # converged one round early instead of granting the settle time. Skip
        # the doomed DELETE and count it as in-progress instead, same as
        # _is_async_deleting.
        if _is_tgw_settling(it):
            _bump_inprog()
            print(f"  transit-gateway {_name_of(it) or tid} ({tid}) state="
                  f"{it.get('state')} — not yet settled (CREATING/EDITING), "
                  f"deferring delete to next round")
            continue
        st = _delete(c, "vpc", f"/v1/transit-gateways/{tid}")
        if _is_2xx_or_gone(st):
            _note_progress(st)
            deleted += 1
            _tgw_wait.append(("vpc", f"/v1/transit-gateways/{tid}"))
        elif st is not None:
            print(f"  transit-gateway {_name_of(it)} ({tid}) delete -> {st} "
                  f"(transitional while its vpc-connection drains — "
                  f"retried next round)")
    _wait_all_gone(c, _tgw_wait, 300, 15)

    # Load balancers + nat gateways have no regr name; delete any whose
    # vpc_id matches a regr* vpc. These would otherwise 409-block the vpc.
    regr_vpc_ids = {
        v["id"]
        for v in _select(c, "vpc", "/v1/vpcs",
                         name_prefixes=_VPC_NAME_PREFIXES)
        if v.get("id")
    }
    if regr_vpc_ids:
        _lbnat_wait = []
        for svc, coll in (("loadbalancer", "/v1/loadbalancers"),
                          ("vpc", "/v1/nat-gateways")):
            try:
                items = _items(c.get(coll, service=svc).body)
            except Exception:
                items = []
            for it in items:
                if (isinstance(it, dict) and it.get("id")
                        and str(it.get("vpc_id")) in regr_vpc_ids):
                    lb_id = it["id"]
                    if svc == "loadbalancer":
                        _reap_lb_static_nat(c, lb_id)
                    if _delete(c, svc, f"{coll}/{it['id']}"):
                        deleted += 1
                        _lbnat_wait.append((svc, f"{coll}/{it['id']}"))
        _wait_all_gone(c, _lbnat_wait, 300, 15)

    # 4. vpcs — 409 handling is HOLDER-AWARE (2026-07-03 incident): when the
    # blocker is detectable (an owned TGW's vpc-connection into this VPC, or a
    # mid-drain LB/NAT gateway — _vpc_409_holder), burn ONE attempt, print
    # "blocked by <holder>", count the VPC as deferred-in-progress (grants the
    # next round) and move on — the old loop burned 6 identical noisy 409s per
    # round against a dependency only time clears. Only when NO holder is
    # detectable fall back to the blind purge-children + retry loop
    # (un-prefixed child leaks).
    deleted_vpc_ids = []
    holder_cache: dict = {}   # per-round TGW/LB/NAT listings for holder lookup
    for it in _select(c, "vpc", "/v1/vpcs",
                      name_prefixes=_VPC_NAME_PREFIXES):
        vid = it["id"]
        for attempt in range(6):
            st, _body409 = _delete_resp(c, "vpc", f"/v1/vpcs/{vid}")
            print(f"  delete vpc {it.get('name', vid)} ({vid}) -> {st}")
            if st in (200, 202, 204):
                deleted += 1
                deleted_vpc_ids.append(vid)
                break
            if st == 409:
                # 1) 플랫폼이 409 본문에 홀더 SRN을 명시했으면 그것부터 직접
                #    회수 후 즉시 재시도 — run-892a의 direct-connect처럼 어떤
                #    목록/탐지에도 없는 홀더에 6회 헛시도하는 낭비 제거.
                if _purge_409_holders(c, _body409):
                    continue
                # 2) 나열 기반 탐지(드레인 중 TGW/LB/NAT) → 다음 라운드 유예
                holder = _vpc_409_holder(c, vid, cache=holder_cache)
                if holder:
                    _bump_inprog()
                    print(f"    blocked by {holder} — deferring to next round")
                    break
                # 3) 폴백: 자식 일괄 정리(이름 무관, by vpc_id) 후 재시도.
                deleted += _purge_vpc_children(c, vid)
                time.sleep(10)
                continue
            break
    # VPC deletion is async (202); wait until all actually disappear so the
    # account's VPC quota is freed before a subsequent CRUD run creates a VPC.
    _wait_all_gone(c, [("vpc", f"/v1/vpcs/{v}") for v in deleted_vpc_ids],
                   300, 15)

    # 5-12. independent tail passes — see _TAIL_PASSES. Each keeps its
    # internal child→parent ordering (repos→registries, secrets→KMS,
    # nodepools→cluster); the groups themselves share no dependency, so they
    # run concurrently on the sweep pool (SCP_SWEEP_PARALLEL, 1 = serial).
    # SKE is excluded here when it already ran early (step 0).
    tail = (_TAIL_PASSES if ske_future is None else
            tuple(p for p in _TAIL_PASSES if p[0] != "ske"))
    deleted += _run_passes(tail, c)

    print(f"sweep done: {deleted} resource(s) deleted")
    return deleted


def _round_verdict(genuine: int, reported: int, inprog: int) -> str:
    """Decide how main()'s fixed-point loop proceeds after a round.

    * ``"continue"``     — genuine teardown (2xx/404) happened; keep sweeping.
    * ``"grant-inprog"`` — nothing genuinely reaped THIS round, but ≥1 owned
      resource was observed mid-ASYNC-deletion (state DELETING/…) or a VPC was
      deferred behind such a holder. Stopping now would strand its 409-blocked
      dependents — the 2026-07-03 incident ("no genuinely-removed resource this
      round (reported=1); converged — stopping" while the regrtgw* TGW was
      mid-deletion and regrvpcsh6a47724b stayed 409-blocked). Grant another
      round, bounded by the existing SCP_SWEEP_ROUNDS cap.
    * ``"stop"``         — nothing genuine, nothing in flight: converged. A
      PF-09 scheduled-deletion re-list (KMS/secrets pending their waiting
      window) contributes to neither counter, so it still converges here —
      the behaviour the existing offline tests lock.
    """
    if genuine > 0:
        return "continue"
    if inprog > 0:
        return "grant-inprog"
    return "stop"


def _owned_vpcs_present(client) -> int:
    """Read-only count of the owned VPCs the sweep still WANTS gone.

    The reconciler's PRIMARY goal is a clear account VPC cap (5). Once this
    hits zero, no owned network-critical resource remains, and any still-
    draining LEAF — a dbaas cluster in LATE internal drain whose subnet/VPC is
    already gone (mariadb ~90min; its subnet port was released long before the
    cluster fully vanishes), or an orphan transit-gateway — BLOCKS nothing: it
    disappears on its own and belongs in a REPORT, not in another 90-minute
    wait (owner 2026-07-14: "vpc 모두 삭제되고 부산물 정리되면 끝내는게 맞고 ..
    이슈로 남은 자원 리포트").

    Uses the same ``_is_deletable`` gate the sweep deletes by, so a LIVE
    OTHER-run VPC (owner-tagged but unexpired, not this run) is NOT counted —
    the sweep could not delete it anyway, and holding OUR run's end open for
    it would be wrong. Reads via ``_list_all`` (not ``_select``) so the
    convergence cache never hides a VPC from this probe. A list FAILURE
    (exception or non-2xx) returns 1 (assume present) so a transient error
    keeps the SAFE legacy behaviour (grant another round) instead of declaring
    the cap clear prematurely — ``_list_all`` swallows errors to ``[]``, which
    would look like "cap clear", so the LIST is issued directly here to tell a
    genuine empty from a failed list."""
    try:
        r = client.get("/v1/vpcs", service="vpc")
    except Exception as exc:  # noqa: BLE001 — never wedge the round loop
        print(f"  owned-vpc probe failed ({exc}) — assuming present (safe)")
        return 1
    if not getattr(r, "ok", False):
        print(f"  owned-vpc probe: /v1/vpcs -> {getattr(r, 'status', None)} "
              f"— assuming present (safe)")
        return 1
    vpcs = [it for it in _items(r.body) if isinstance(it, dict)]
    return sum(1 for v in vpcs
               if _is_deletable(v, name_prefixes=_VPC_NAME_PREFIXES))


def _leftover_report(client) -> None:
    """The "이슈로 남은 자원 리포트" (owner 2026-07-14): when the sweep STOPS with
    resources still present — an async-deleting leaf mid-drain, or a genuinely
    stuck item — report exactly what is left instead of the sweep ending
    silently. The next run's end-sweep (or a manual FORCE cleanup) converges
    them.

    Wall-time 최적화 (2026-07-14): 종전에는 여기서 ``verify_clean.scan_owned``
    full dry-scan(전 컬렉션 ~30개 재나열, 수 분)을 다시 돌았다. 스윕이 방금
    돈 라운드의 ``_select`` 픽(``_LAST_PICKED``)이 곧 생존자다 — 리포트가
    호출되는 모든 경로는 genuine=0으로 끝난 라운드 뒤이므로, 그 라운드에
    픽되고도 genuinely-gone(2xx/404) 하지 못한 아이템들이다. 그 관측을
    새 LIST 0회로 요약한다. 픽 정보가 비어 있으면(예: 외부에서 캠페인 상태가
    초기화됐거나, 생존자가 _select를 안 타는 bespoke 패스에만 있는 경우)
    기존 read-only dry-scan으로 폴백한다. Best-effort: a scan failure never
    fails the sweep or the run."""
    from collections import Counter
    with _STATE_LOCK:
        observed = {k: list(v) for k, v in _LAST_PICKED.items() if v}
    if observed:
        survivors = [{"service": svc, "path": path, "id": iid, "name": name}
                     for (svc, path), items in sorted(observed.items())
                     for iid, name in items]
        by_svc = Counter(o["service"] for o in survivors)
        print(f"--- leftover report: {len(survivors)} owned resource(s) STILL "
              f"present — draining/stuck, REPORTED not waited on "
              f"(next end-sweep converges them; source: this sweep's own "
              f"last-round observations, 0 extra LIST) ---")
        for svc, n in by_svc.most_common():
            paths = Counter(o["path"] for o in survivors if o["service"] == svc)
            print(f"  {svc:18} {n:3}  ({dict(paths)})")
        for o in survivors[:40]:
            print(f"    {o['service']} {o['path']}: {o['name'] or o['id']}")
        return
    # 폴백: 픽 정보 없음 — 기존 deleteless dry-scan (verify_clean.scan_owned;
    # snapshots+restores the reconciler's campaign state, no footprint).
    try:
        from cleanup.verify_clean import scan_owned
        survivors = scan_owned(client=client)
    except Exception as exc:  # noqa: BLE001 — report is advisory, never fatal
        print(f"  leftover-report scan failed (ignored): {exc}")
        return
    if not survivors:
        print("--- leftover report: 0 owned resources remain ✅ ---")
        return
    by_svc = Counter(o["service"] for o in survivors)
    print(f"--- leftover report: {len(survivors)} owned resource(s) STILL "
          f"present — draining/stuck, REPORTED not waited on "
          f"(next end-sweep converges them) ---")
    for svc, n in by_svc.most_common():
        paths = Counter(o["path"] for o in survivors if o["service"] == svc)
        print(f"  {svc:18} {n:3}  ({dict(paths)})")


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
    report_leftovers = False
    inprog_grants = 0   # consecutive grant-inprog rounds (drives the backoff)
    for rnd in range(1, rounds + 1):
        print(f"--- sweep round {rnd} ---", flush=True)
        _PROGRESS_THIS_ROUND[0] = 0          # reset genuine-teardown counter
        _INPROGRESS_THIS_ROUND[0] = 0        # reset async-in-flight counter
        reported = run_sweep(client)
        genuine = _PROGRESS_THIS_ROUND[0]
        inprog = _INPROGRESS_THIS_ROUND[0]
        # Machine-readable genuine tally per round: consumers (console2 클린업
        # 요약 등) must count only genuinely-removed resources — ``reported`` is
        # inflated by deceptive 2xx deletes that re-list next round (신규7).
        print(f"sweep round {rnd} genuine-removed: {genuine}", flush=True)
        # Convergence stop (Bug 3): end the sweep as soon as a round makes no
        # REAL progress — i.e. nothing genuinely-gone (2xx/404) was reaped. This
        # is stricter than the legacy ``reported == 0`` because ``reported`` can
        # be inflated by passes that still tally a deceptive status; ``genuine``
        # counts only items that actually went away. Items that re-list after a
        # delete are now marked stuck (logged once) and not retried, so a sweep
        # with only stuck/un-deletable owned items left converges here instead
        # of looping to max rounds. EXCEPTION (2026-07-03 incident): a round
        # that observed an owned item mid-ASYNC-deletion (or a VPC deferred
        # behind one) is NOT converged — the deletion is landing and its
        # 409-blocked dependents need the next round; grant it (still bounded
        # by the SCP_SWEEP_ROUNDS cap).
        verdict = _round_verdict(genuine, reported, inprog)
        if verdict == "continue":
            inprog_grants = 0   # real progress — reset the in-progress backoff
        if verdict == "stop":
            if reported:
                print(f"no genuinely-removed resource this round "
                      f"(reported={reported}); converged — stopping.")
                report_leftovers = True   # deceptive re-list = survivors remain
            break
        if verdict == "grant-inprog":
            # Terminal policy (owner 2026-07-14): the sweep's PRIMARY goal is a
            # clear account VPC cap. Once no owned VPC remains, an async-
            # deleting LEAF (a dbaas cluster in late internal drain whose
            # subnet/VPC is already gone — mariadb ~90min; an orphan TGW) blocks
            # nothing and must NOT buy the remaining full rounds waiting for a
            # ~90-min drain. Stop and REPORT it as a leftover ("vpc 모두 삭제되고
            # 부산물 정리되면 끝내는게 맞고 .. 이슈로 남은 자원 리포트"). A still-
            # present owned VPC keeps the grant — there the in-progress item may
            # BE what 409-blocks the VPC (2026-07-03 TGW-mid-deletion incident).
            if _owned_vpcs_present(client) == 0:
                print(f"{inprog} owned resource(s) still draining, but no owned "
                      f"VPC remains — a leaf drain blocks nothing; stopping and "
                      f"reporting (they vanish on their own).")
                report_leftovers = True
                break
            if rnd == rounds:
                print(f"{inprog} owned resource(s) still mid-async-deletion at "
                      f"the round cap ({rounds}) — reporting, not forcing; "
                      f"re-run the sweep once they settle.")
                report_leftovers = True
                break
            # 라운드 간 backoff (2026-07-14 wall-time 튜닝): async-deleting
            # 아이템의 실제 소멸은 보통 수십초~분 단위라 고정 30s는 너무 짧아
            # "아직 DELETING" 라운드(전 컬렉션 재나열 아님이라도 LIST 비용)만
            # 늘렸다. 연속 grant-inprog마다 30→60→120s로 늘리고 상한은
            # SCP_SWEEP_INPROGRESS_SLEEP_MAX_S(기본 120)로 캡. genuine 진행이
            # 있는 라운드가 나오면 리셋. 의미 보존: 라운드를 부여하는 조건
            # (grant-inprog — 소유 VPC 잔존 시 계속 부여, 2026-07-03 TGW
            # 인시던트 보호)은 그대로고, 라운드 사이 대기 길이만 바뀐다.
            base = int(os.environ.get("SCP_SWEEP_INPROGRESS_SLEEP_S", "30"))
            cap = int(os.environ.get("SCP_SWEEP_INPROGRESS_SLEEP_MAX_S", "120"))
            pause = min(base * (2 ** inprog_grants), max(base, cap))
            inprog_grants += 1
            print(f"{inprog} owned resource(s) mid-async-deletion (or deferred "
                  f"behind one) — granting another round after {pause}s")
            time.sleep(pause)
            continue
        # In FAST (no-wait) mode rounds fire back-to-back; pause briefly so
        # async deletes issued this round actually disappear before the next
        # pass retries their now-unblocked dependents.
        if nowait and rnd < rounds:
            time.sleep(round_sleep)
    # 이슈로 남은 자원 리포트 (owner 2026-07-14): the sweep stopped with
    # resources still present — a leaf mid-drain (VPC cap already clear) or a
    # deceptive re-list — so enumerate what survives instead of ending silently.
    if report_leftovers or _STUCK:
        _leftover_report(client)
    if _STUCK:
        print(f"--- {len(_STUCK)} owned item(s) could not be deleted "
              f"(reported, not forced) ---")
        for iid, reason in _STUCK.items():
            print(f"  stuck: {iid} ({reason})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
