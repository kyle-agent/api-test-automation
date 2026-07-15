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


# 스윕 비용 계측 (2026-07-15 teardown 최소화 — run-ddbf: NOWAIT·2라운드인데
# 스윕 1117s. 배리어 0인데 시간이 리스팅/삭제/슬립 어디에 숨었는지 라운드별
# 집계·요약해 다음 병목을 로그만으로 특정한다). _STATE_LOCK 하에 갱신.
_COST_LIST: dict = {}     # (service, path) -> seconds (이번 라운드 누적)
_COST_DELETE = [0.0, 0]   # [seconds, count]


def _cost_reset() -> None:
    with _STATE_LOCK:
        _COST_LIST.clear()
        _COST_DELETE[0] = 0.0
        _COST_DELETE[1] = 0


def _cost_report() -> None:
    with _STATE_LOCK:
        lists = sorted(_COST_LIST.items(), key=lambda kv: -kv[1])
        lt = sum(v for _, v in lists)
        dt, dn = _COST_DELETE
    top = ", ".join(f"{p}={s:.0f}s" for (_, p), s in lists[:6] if s >= 2)
    print(f"  [cost] listing {lt:.0f}s ({len(lists)} collections"
          + (f"; top: {top}" if top else "")
          + f") · deletes {dt:.0f}s/{dn}건", flush=True)


def _list_all(client, service, path):
    """Return all items from a collection (no ownership filter).

    라운드 시작 프리스캔(``_prescan``)이 채운 1회용 캐시가 있으면 그걸 소비한다
    (pop — 같은 컬렉션의 패스 중간 재나열은 종전대로 라이브). TTL을 넘긴
    엔트리는 버리고 라이브로 간다: 배리어 뒤에 소비되는 늦은 패스가 state 필드
    (async-deleting/PF-09 판정)의 과도한 staleness를 보지 않게."""
    key = (id(client), service, path)
    with _STATE_LOCK:
        cached = _LIST_CACHE.pop(key, None)
    if cached is not None:
        ts, items = cached
        if time.monotonic() - ts <= _PRESCAN_TTL_S:
            return items
    _t0 = time.monotonic()
    try:
        r = client.get(path, service=service)
    except Exception as exc:
        print(f"  list {path} error: {exc}")
        return []
    finally:
        with _STATE_LOCK:
            _COST_LIST[(service, path)] = _COST_LIST.get((service, path), 0.0) \
                + (time.monotonic() - _t0)
    if not r.ok:
        print(f"  list {path} -> {r.status}")
        return []
    return [it for it in _items(r.body) if isinstance(it, dict)]


# --------------------------------------------------------------------------- #
# 라운드 시작 병렬 프리스캔 (오너 2026-07-15: "클린업이 너무너무 느리다 —
# 전체 리소스 리스트 조회(병렬)하고 있는놈만 역방향 순서 생각해서 지우면
# 될 것 같은데"). 스윕 패스들은 역방향(자식→부모) 순서로 직렬 실행되는데,
# 각 패스가 자기 컬렉션을 그때그때 LIST하므로 라운드 wall-time에 컬렉션 수 ×
# LIST 지연이 직렬로 쌓인다 — 특히 거의-빈 계정의 런 종료 스윕은 나열이
# 시간의 대부분이다. 프리스캔은 라운드 시작 시 (수렴 안 된) 전 컬렉션을
# 병렬로 미리 나열해 1회용 캐시에 담고, 각 패스의 첫 _list_all이 이를
# 소비한다 → 직렬 나열 합계가 max(지연) 하나로 줄어든다. 삭제 순서(역방향
# 패스 순서)와 소유권 게이트는 전혀 건드리지 않는다.
# --------------------------------------------------------------------------- #
# 스윕 패스들이 _list_all로 나열하는 컬렉션의 레지스트리. 여기 없는 컬렉션은
# 프리스캔만 안 될 뿐 종전대로 라이브 나열된다 (fail-safe). 신규 패스 추가 시
# 여기도 추가 — tests/offline/test_sweep_prescan.py가 소스를 파싱해 드리프트를
# 잡는다.
_SWEEP_COLLECTIONS: tuple = (
    ("apigateway", "/v1/apis"),
    ("cdn", "/v1/cdns"),
    ("certificatemanager", "/v1/certificatemanager"),
    ("direct-connect", "/v1/direct-connects"),
    ("dns", "/v1/hosted-zones"),
    ("dns", "/v1/private-dns"),
    ("filestorage", "/v1/volumes"),
    ("iam", "/v1/groups"),
    ("iam", "/v1/policies"),
    ("kms", "/v1/kms/transit"),
    ("loadbalancer", "/v1/loadbalancers"),      # holder 탐지(_HOLDER_COLLS)
    ("queueservice", "/v1/queues"),
    ("resourcemanager", "/v1/resource-groups"),
    ("scf", "/v1/cloud-functions"),
    ("scr", "/v1/container-registries"),
    ("scr", "/v1/repositories"),
    ("secretsmanager", "/v1/secrets"),
    ("security-group", "/v1/security-groups"),
    ("servicewatch", "/v1/log-groups"),
    ("ske", "/v1/clusters"),
    ("virtualserver", "/v1/images"),
    ("virtualserver", "/v1/keypairs"),
    ("virtualserver", "/v1/launch-configurations"),
    ("virtualserver", "/v1/server-groups"),
    ("virtualserver", "/v1/servers"),
    ("virtualserver", "/v1/snapshots"),
    ("virtualserver", "/v1/volumes"),
    ("vpc", "/v1/internet-gateways"),
    ("vpc", "/v1/nat-gateways"),                # holder 탐지(_HOLDER_COLLS)
    ("vpc", "/v1/ports"),
    ("vpc", "/v1/publicips"),
    ("vpc", "/v1/subnets"),
    ("vpc", "/v1/subnets?type=VPC_ENDPOINT"),   # PF-47 숨은 서브넷
    ("vpc", "/v1/transit-gateways"),
    ("vpc", "/v1/vpc-endpoints"),
    ("vpc", "/v1/vpc-peerings"),
    ("vpc", "/v1/vpcs"),
    # dbaas 엔진들 — 같은 path, 서비스 호스트만 다르다 (_dbaas_engine)
    ("mysql", "/v1/clusters"),
    ("postgresql", "/v1/clusters"),
    ("mariadb", "/v1/clusters"),
    ("epas", "/v1/clusters"),
    ("cachestore", "/v1/clusters"),
    ("eventstreams", "/v1/clusters"),
    ("searchengine", "/v1/clusters"),
    ("sqlserver", "/v1/clusters"),
    ("vertica", "/v1/clusters"),
)
_LIST_CACHE: dict = {}     # (id(client), service, path) -> (monotonic_ts, items)
_PRESCAN_TTL_S = 120.0     # 배리어 뒤 늦은 소비의 state-staleness 상한

# --------------------------------------------------------------------------- #
# 태그 인벤토리 스코프 축소 (오너 2026-07-15 아이디어: "resourcemanager
# listresources로 우리가 생성한 tag로 안 지워진 게 있는지 조회가 가능하다.
# 이걸로 범위를 좁히고, 나머지는 부산물(서비스와치 로그그룹 같은 거) 종류별로
# 일괄 리스트 조회"). GET /v1/resources 는 계정 전 리소스를 service ·
# resource_type · resource_name · tags 와 함께 한 컬렉션으로 반환한다
# (management/resourcemanager/listresources) — 몇 페이지만 받아 owner 태그로
# 거르면 "우리 잔존물이 어느 컬렉션에 있는지"가 나오고, 그 밖의 컬렉션은
# 이 라운드에서 나열 자체를 건너뛴다.
#
# SAFETY (스코프를 좁히는 최적화이므로 놓침=잔존이다 — 삼중 방어):
#   1. 부산물 컬렉션(_DERIVATIVE_COLLS)은 무조건 종전대로 나열: 태그가 목록/
#      레지스트리에 안 잡히는 플랫폼 파생물들 (IGW는 2026-07-15 실측 태그
#      미노출, 로그그룹 /scp/*, 파생 snapshot/image/boot-volume, unnamed
#      publicip, PF-47 숨은 VPC_ENDPOINT 서브넷 뷰, LB/NAT 파생 port,
#      LC 파생 keypair, 추가-리전 필터가 도는 filestorage).
#   2. 인벤토리에 매핑 모르는 (service, type)의 소유 아이템이 하나라도 있으면
#      이 라운드의 축소를 통째로 포기하고 전체 나열 (미지 타입이 자기 컬렉션
#      스킵을 유발하지 않게).
#   3. 인벤토리 호출 실패/의심(0건인데 규모 판단 불가) → 전체 나열 (fail-open).
# 끄기: SCP_SWEEP_TAG_SCOPE=false. 소유권 게이트/삭제 순서는 불변 — 이 축소는
# "어느 컬렉션을 나열하나"만 정한다 (각 패스의 _is_deletable 판정은 그대로).
# --------------------------------------------------------------------------- #
_DERIVATIVE_COLLS: frozenset = frozenset({
    ("servicewatch", "/v1/log-groups"),
    ("vpc", "/v1/internet-gateways"),
    ("vpc", "/v1/publicips"),
    ("vpc", "/v1/ports"),
    ("vpc", "/v1/subnets"),
    ("vpc", "/v1/subnets?type=VPC_ENDPOINT"),
    ("vpc", "/v1/nat-gateways"),
    ("loadbalancer", "/v1/loadbalancers"),
    ("virtualserver", "/v1/images"),
    ("virtualserver", "/v1/snapshots"),
    ("virtualserver", "/v1/volumes"),
    ("virtualserver", "/v1/keypairs"),
    ("filestorage", "/v1/volumes"),     # 추가-리전 필터는 인벤토리 밖
})

# 인벤토리 (service, resource_type) -> 스윕 컬렉션. 인벤토리 항목이 여기 없으면
# 그 라운드는 축소 포기 (SAFETY 2). dbaas 엔진들은 service명이 곧 스윕의
# 서비스 키다 (mysql/postgresql/… -> /v1/clusters).
_TYPE_TO_COLL: dict = {
    ("virtualserver", "virtual-server"): ("virtualserver", "/v1/servers"),
    ("virtualserver", "server-group"): ("virtualserver", "/v1/server-groups"),
    ("virtualserver", "launch-configuration"):
        ("virtualserver", "/v1/launch-configurations"),
    ("virtualserver", "keypair"): ("virtualserver", "/v1/keypairs"),
    ("virtualserver", "image"): ("virtualserver", "/v1/images"),
    ("virtualserver", "snapshot"): ("virtualserver", "/v1/snapshots"),
    ("virtualserver", "volume"): ("virtualserver", "/v1/volumes"),
    ("filestorage", "volume"): ("filestorage", "/v1/volumes"),
    ("vpc", "vpc"): ("vpc", "/v1/vpcs"),
    ("vpc", "subnet"): ("vpc", "/v1/subnets"),
    ("vpc", "port"): ("vpc", "/v1/ports"),
    ("vpc", "publicip"): ("vpc", "/v1/publicips"),
    ("vpc", "public-ip"): ("vpc", "/v1/publicips"),
    ("vpc", "internet-gateway"): ("vpc", "/v1/internet-gateways"),
    ("vpc", "nat-gateway"): ("vpc", "/v1/nat-gateways"),
    ("vpc", "transit-gateway"): ("vpc", "/v1/transit-gateways"),
    ("vpc", "vpc-endpoint"): ("vpc", "/v1/vpc-endpoints"),
    ("vpc", "vpc-peering"): ("vpc", "/v1/vpc-peerings"),
    ("loadbalancer", "loadbalancer"): ("loadbalancer", "/v1/loadbalancers"),
    ("loadbalancer", "load-balancer"): ("loadbalancer", "/v1/loadbalancers"),
    ("security-group", "security-group"):
        ("security-group", "/v1/security-groups"),
    ("ske", "cluster"): ("ske", "/v1/clusters"),
    ("mysql", "cluster"): ("mysql", "/v1/clusters"),
    ("postgresql", "cluster"): ("postgresql", "/v1/clusters"),
    ("mariadb", "cluster"): ("mariadb", "/v1/clusters"),
    ("epas", "cluster"): ("epas", "/v1/clusters"),
    ("cachestore", "cluster"): ("cachestore", "/v1/clusters"),
    ("eventstreams", "cluster"): ("eventstreams", "/v1/clusters"),
    ("searchengine", "cluster"): ("searchengine", "/v1/clusters"),
    ("sqlserver", "cluster"): ("sqlserver", "/v1/clusters"),
    ("vertica", "cluster"): ("vertica", "/v1/clusters"),
    ("scr", "container-registry"): ("scr", "/v1/container-registries"),
    ("scr", "repository"): ("scr", "/v1/repositories"),
    ("scf", "cloud-function"): ("scf", "/v1/cloud-functions"),
    ("apigateway", "api"): ("apigateway", "/v1/apis"),
    ("cdn", "cdn"): ("cdn", "/v1/cdns"),
    ("dns", "hosted-zone"): ("dns", "/v1/hosted-zones"),
    ("dns", "private-dns"): ("dns", "/v1/private-dns"),
    ("direct-connect", "direct-connect"):
        ("direct-connect", "/v1/direct-connects"),
    ("certificatemanager", "certificate"):
        ("certificatemanager", "/v1/certificatemanager"),
    ("kms", "key"): ("kms", "/v1/kms/transit"),
    ("kms", "transit"): ("kms", "/v1/kms/transit"),
    ("secretsmanager", "secret"): ("secretsmanager", "/v1/secrets"),
    ("queueservice", "queue"): ("queueservice", "/v1/queues"),
    ("resourcemanager", "resource-group"):
        ("resourcemanager", "/v1/resource-groups"),
    ("iam", "group"): ("iam", "/v1/groups"),
    ("iam", "policy"): ("iam", "/v1/policies"),
}


def _tag_scope_enabled() -> bool:
    return os.environ.get("SCP_SWEEP_TAG_SCOPE", "").lower() != "false"


def _item_tags(it: dict):
    """listresources 아이템의 tags — [{key,value}] 또는 문자열화된 JSON."""
    tags = it.get("tags")
    if isinstance(tags, str):
        try:
            import json as _json
            tags = _json.loads(tags)
        except ValueError:
            return []
    return tags if isinstance(tags, list) else []


def _tag_inventory(client):
    """GET /v1/resources 를 페이지로 받아 소유(owner 태그 또는 regr*/zznet*
    이름) 아이템만 돌려준다. 실패하면 None (호출측이 전체 나열로 폴백)."""
    from core.registry import OWNER_KEY, OWNER
    size, max_pages = 100, 30
    owned, total_seen = [], 0
    try:
        for page in range(max_pages):
            r = client.get(f"/v1/resources?size={size}&page={page}",
                           service="resourcemanager")
            if not r.ok:
                print(f"  tag-inventory: list /v1/resources -> {r.status} — "
                      "전체 나열로 폴백")
                return None
            items = [it for it in _items(r.body) if isinstance(it, dict)]
            total_seen += len(items)
            for it in items:
                name = str(it.get("resource_name") or "")
                has_tag = any(isinstance(t, dict) and t.get("key") == OWNER_KEY
                              and t.get("value") == OWNER
                              for t in _item_tags(it))
                if has_tag or name.startswith(("regr", "zznet")):
                    owned.append(it)
            if len(items) < size:
                break
        else:
            # max_pages 소진 = 계정이 비정상적으로 큼 — 축소 신뢰 불가
            print("  tag-inventory: 페이지 상한 도달 — 전체 나열로 폴백")
            return None
    except Exception as exc:  # noqa: BLE001 — 축소 실패가 스윕 실패면 안 됨
        print(f"  tag-inventory error: {exc} — 전체 나열로 폴백")
        return None
    print(f"  tag-inventory: {total_seen} resource(s) seen, "
          f"{len(owned)} owned")
    return owned


def _tag_scope_collections(client):
    """이 라운드에서 나열할 컬렉션 집합을 돌려준다 — 태그 인벤토리 기반
    축소가 가능하면 (매핑된 소유 컬렉션 ∪ 부산물 컬렉션), 아니면 None
    (= 전체 나열)."""
    if not _tag_scope_enabled():
        return None
    inv = _tag_inventory(client)
    if not inv:
        # None(호출 실패) 또는 소유 0건 — 0건이어도 축소하지 않는다: 태그
        # 인덱싱 사각지대(태그 미노출 파생물)가 있는 한 "깨끗함"의 최종 확인은
        # 전체 나열이 해야 한다. 축소의 가치는 "일부가 남았을 때 나머지 스킵".
        if inv is not None:
            print("  tag-scope: 소유 잔존 0건 — 축소 없이 전체 나열로 최종 확인")
        return None
    needed = set(_DERIVATIVE_COLLS)
    for it in inv:
        key = (str(it.get("service") or ""), str(it.get("resource_type") or ""))
        coll = _TYPE_TO_COLL.get(key)
        if coll is None:
            # SAFETY 2: 미지 타입의 소유 잔존 — 축소를 통째로 포기 (그 타입의
            # 실제 컬렉션이 스킵되는 것을 막는다) + PF 후보로 보고.
            print(f"  tag-scope: 미지 타입 {key} (이름 "
                  f"{it.get('resource_name')!r}) — 축소 포기, 전체 나열")
            return None
        needed.add(coll)
    skipped = [p for p in _SWEEP_COLLECTIONS if p not in needed]
    print(f"  tag-scope: owned {len(inv)}건 → 컬렉션 {len(needed & set(_SWEEP_COLLECTIONS))}개"
          f" 나열, {len(skipped)}개 스킵")
    return needed


def _prescan_enabled() -> bool:
    return os.environ.get("SCP_SWEEP_NO_PRESCAN", "").lower() != "true"


def _prescan(client) -> None:
    """`_SWEEP_COLLECTIONS` 중 아직 수렴 안 된 컬렉션을 병렬 LIST해서
    ``_LIST_CACHE``를 채운다. 실패한 컬렉션은 캐시에 안 담겨 해당 패스가
    종전대로 라이브 나열한다 (fail-open — 프리스캔이 스윕 실패의 원인이
    되면 안 됨)."""
    if not _prescan_enabled() or _sweep_workers() <= 1:
        return
    # 태그 인벤토리 스코프 축소 — 소유 잔존이 있는 컬렉션 + 부산물 컬렉션만
    # 나열하고, 나머지는 converged로 마킹해 패스 자체를 스킵시킨다 (오너
    # 2026-07-15). 축소 불가(None)면 전체 나열 (fail-open).
    scope = _tag_scope_collections(client)
    if scope is not None and _converge_enabled():
        for p in _SWEEP_COLLECTIONS:
            if p not in scope:
                _CONVERGED.add(p)
    pairs = [(svc, path) for svc, path in _SWEEP_COLLECTIONS
             if not (_converge_enabled() and (svc, path) in _CONVERGED)]
    if not pairs:
        return
    _t0 = time.monotonic()

    def _one(pair):
        svc, path = pair
        try:
            items = _list_all(client, svc, path)
        except Exception:  # noqa: BLE001 — 실패 = 캐시 미적재 = 라이브 폴백
            return 0
        with _STATE_LOCK:
            _LIST_CACHE[(id(client), svc, path)] = (time.monotonic(), items)
        return 1

    # LIST 전용이라 삭제 풀(_sweep_workers, 기본 6)보다 넓게 돌려도 안전 —
    # 읽기 46개를 순간 병렬로 흘리는 정도는 xdist 테스트 런이 이미 더 세게
    # 미는 수준이다.
    from concurrent.futures import ThreadPoolExecutor
    with ThreadPoolExecutor(max_workers=min(16, len(pairs)),
                            thread_name_prefix="prescan") as ex:
        warmed = sum(ex.map(_one, pairs))
    print(f"  prescan: {warmed}/{len(pairs)} collection(s) listed in parallel "
          f"({time.monotonic() - _t0:.1f}s)")


# --------------------------------------------------------------------------- #
# 블라스트 삭제 (오너 설계 2026-07-16: "1단계 리소스 조회, 2단계 해당 리소스
# 전체 삭제 api 호출 — 병렬로, 동시에. 그러고 409로 안지워지는 놈들만
# 시나리오에 따라 정리. 부산물 동시에 전체 삭제. 이러면 끝 아닌가?")
# --------------------------------------------------------------------------- #
_BLAST_EXCLUDE: frozenset = frozenset({
    ("servicewatch", "/v1/log-groups"),   # bulk-body DELETE — 전용 병렬 패스
    ("secretsmanager", "/v1/secrets"),    # waiting_time_ndays body 필요
    # 부모/특수-순서 컬렉션 — 자식이 살아 있는 동안 DELETE는 어차피 409라
    # 블라스트 이득이 0이고, 전용 패스가 시나리오 순서(룰→FW→IGW, 노드풀→
    # 클러스터, static-nat→LB, 연결→TGW, 스냅샷→이미지→볼륨, 복제해제→볼륨)
    # 를 보증한다. 블라스트는 리프 전용.
    ("vpc", "/v1/vpcs"),
    ("vpc", "/v1/subnets"),
    ("vpc", "/v1/subnets?type=VPC_ENDPOINT"),
    ("vpc", "/v1/internet-gateways"),
    ("vpc", "/v1/transit-gateways"),
    ("loadbalancer", "/v1/loadbalancers"),
    ("direct-connect", "/v1/direct-connects"),
    ("virtualserver", "/v1/images"),
    ("virtualserver", "/v1/volumes"),
    ("virtualserver", "/v1/snapshots"),
    ("filestorage", "/v1/volumes"),
    ("ske", "/v1/clusters"),
})
_BLAST_PFX = ("regr", "zznet")


def _blast_enabled() -> bool:
    return os.environ.get("SCP_SWEEP_BLAST", "").lower() != "false"


def _blast_delete(c) -> int:
    """2단계 동시 발사: 프리스캔이 채운 _LIST_CACHE의 모든 컬렉션에서 소유
    아이템을 골라 일반형 ``DELETE {collection}/{id}`` 를 스윕 풀 전체로
    동시에 발사한다. 독립 리소스는 이 라운드 하나에 병렬로 다 죽고,
    비-2xx(409 의존 홀더, 특수-삭제 4xx)는 조용히 남아 뒤의 의존순서 패스
    체인이 시나리오대로(룰→FW→IGW 등) 줍는다. 실패를 세지도 기다리지도
    않는다 — 목표는 wall-time 하나다.

    소유 게이트는 패스와 동일한 _is_deletable (owner 태그 + TTL/자기-런
    규칙) + regr/zznet 접두 (태그 인벤토리와 같은 계정 소유 규칙, Hard Rule
    3 불변). 태그가 살아 있는(미만료·타-런) 아이템은 여기서도 보호된다.
    2xx로 죽은 아이템은 캐시에서도 걷어내 뒤 패스의 중복 DELETE를 막는다."""
    if not _blast_enabled():
        return 0
    from core.registry import _tag_value, OWNER_KEY, OWNER
    targets = []
    with _STATE_LOCK:
        cached = [(svc, path, items)
                  for (cid, svc, path), (_ts, items) in _LIST_CACHE.items()
                  if cid == id(c)]
    for svc, path, items in cached:
        if (svc, path) in _BLAST_EXCLUDE:
            continue
        base = path.split("?")[0]
        for it in items:
            if not isinstance(it, dict) or not it.get("id"):
                continue
            if _is_pending_deletion(it):
                continue  # 이미 삭제 진행/예약 상태 — 재-DELETE는 소음일 뿐
            has_tag = _tag_value(it, OWNER_KEY) == OWNER
            ok = _is_deletable(it, name_prefixes=_BLAST_PFX) or (
                # 대체 name 키 폴백 (_select와 동일) — 단 태그가 있는데
                # _is_deletable이 거른 것(미만료 보호)은 되살리지 않는다.
                not has_tag and not it.get("name")
                and _name_of(it).startswith(_BLAST_PFX))
            if ok:
                targets.append((svc, path, base, it["id"]))
    if not targets:
        return 0
    print(f"  blast: {len(targets)} owned item(s) — 전체 동시 삭제 발사")

    def _fire(t):
        svc, path, base, rid = t
        st = _delete(c, svc, f"{base}/{rid}")
        return t if (st is not None and 200 <= st < 300) else None

    killed = [t for t in _map_parallel(_fire, targets) if t]
    # 죽은 아이템은 프리스캔 캐시에서도 제거 — 뒤 패스가 같은 id에 중복
    # DELETE를 직렬로 다시 쏘는 낭비 방지.
    dead: dict = {}
    for svc, path, _base, rid in killed:
        dead.setdefault((svc, path), set()).add(str(rid))
    with _STATE_LOCK:
        for (cid, svc, path), (ts, items) in list(_LIST_CACHE.items()):
            gone = dead.get((svc, path))
            if cid != id(c) or not gone:
                continue
            _LIST_CACHE[(cid, svc, path)] = (
                ts, [it for it in items
                     if not (isinstance(it, dict)
                             and str(it.get("id")) in gone)])
    print(f"  blast: {len(killed)}/{len(targets)} deleted in round 0 "
          f"(잔여 {len(targets) - len(killed)}건은 의존순서 패스가 정리)")
    return len(killed)


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
    _LIST_CACHE.clear()
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
    _t0 = time.monotonic()
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
    finally:
        with _STATE_LOCK:
            _COST_DELETE[0] += time.monotonic() - _t0
            _COST_DELETE[1] += 1


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
                if coll == "/v1/internet-gateways":
                    # rule 잔존 IGW는 DELETE 409 — 직접 패스/409-holder 경로와
                    # 동일하게 implicit firewall의 rule부터 비운다.
                    _reap_igw_firewall_rules(client, it["id"])
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


def _reap_igw_firewall_rules(client, igw_id: str) -> int:
    """IGW 삭제 전, 그 IGW의 implicit firewall(``FW_IGW_*``)에 남은 rule을
    비운다. firewall 본체는 create/delete API가 없는 암묵 리소스(carrier와
    함께 소멸, ``knowledge/formal/resources/networking__firewall.yaml``)라 스윕
    대상이 아닌데, **rule 잔존이 carrier IGW DELETE를 409로 잡는다** — 실측
    2026-07-15: 중단 런이 delete-firewall-rule 전에 죽자 ``FW_IGW_regrvpcnb…``
    의 rule이 남아 IGW→VPC 삭제 체인이 6회 409 루프. firewall→IGW 연결은
    목록 응답의 ``fw_resource_id``(carrier id, api_docs firewalllistresponse).
    안전 근거: 이미 소유가 확인된 IGW의 id로만 firewall을 매치한다."""
    n = 0
    try:
        fws = _items(client.get("/v1/firewalls", service="firewall").body)
    except Exception:
        return 0
    # 라이브 실측 2026-07-15: 목록 응답의 fw_resource_id가 문서 예시와 달리
    # None으로 온다 (FW_IGW_regrvpcnb6a578e6d 케이스) — id 매치가 항상
    # 실패하므로 플랫폼 명명 규칙(FW_<IGW이름>, IGW이름=IGW_<vpc명>)으로
    # 폴백한다. IGW 이름은 show로 1회 조회 (실패해도 id 매치는 시도).
    igw_name = ""
    try:
        _g = client.get(f"/v1/internet-gateways/{igw_id}", service="vpc").body
        igw_name = str(((_g or {}).get("internet_gateway") or {}).get("name")
                       or "")
    except Exception:
        pass
    for fw in fws:
        if not isinstance(fw, dict):
            continue
        linked = (fw.get("fw_resource_id") == igw_id
                  or (igw_name and fw.get("name") == f"FW_{igw_name}"))
        if not linked:
            continue
        fid = fw.get("id")
        if not fid:
            continue
        try:
            rules = _items(client.get(
                f"/v1/firewalls/rules?firewall_id={fid}&fetch_all=true",
                service="firewall").body)
        except Exception:
            continue
        for r in rules:
            if isinstance(r, dict) and r.get("id"):
                st = _delete(client, "firewall",
                             f"/v1/firewalls/rules/{r['id']}")
                print(f"  igw {igw_id} firewall {fid} rule {r['id']} "
                      f"delete -> {st}")
                if st and (200 <= st < 300 or st == 404):
                    n += 1
    return n


def _purge_409_holders(client, body: dict) -> int:
    """소유 VPC의 DELETE 409 본문 ``related_resources``(SRN)가 명시한 홀더를
    직접 삭제하고 삭제 발행 수를 반환한다. 안전 근거는 ``_purge_vpc_children``
    과 동일 — 이미 소유가 확인된 VPC의 삭제를 막는 자식만 대상이고, id는
    플랫폼이 명시한 것이다. direct-connect는 자식 routing-rules를 먼저 비운다
    (run-892a: rule이 남은 DC는 DELETE 409). internet-gateway는 implicit
    firewall의 rule을 먼저 비운다 (2026-07-15: rule 잔존 IGW는 DELETE 409)."""
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
        if rtype == "internet-gateway":
            # rule 잔존 → IGW DELETE 409 (firewall rule 먼저, 그 다음 IGW)
            _reap_igw_firewall_rules(client, rid)
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
            _mtime = shard.stat().st_mtime
            if now - _mtime < _LEDGER_MIN_AGE_S:
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
        # PER-RECORD pruning (2026-07-16 오너: "아래것들을 왜 조회하는거야?" —
        # gone 확정을 찍고도 샤드 단위 all-or-nothing 프룬이라, 처리불가 레코드
        # 하나가 샤드를 불멸로 만들어 이미 사라진 apigateway/scf 항목을 매 스윕
        # DELETE+GET 재시도하던 낭비 수리). 처리된 레코드는 그 자리에서 샤드에서
        # 빠지고, 재시도 가치가 있는 것(실존 200 / unknown 5xx)만 남는다.
        drop: set[int] = set()
        # newest-first == children before parents (create order is parent→child)
        for idx in range(len(recs) - 1, -1, -1):
            rec = recs[idx]
            dp = rec.get("delete_path") or ""
            svc = rec.get("service") or ""
            if not dp or "{" in dp or not rec.get("resource_id"):
                # can never be addressed via the API — park it in the audit file
                # (NOT the shard: keeping it made the shard immortal) and drop.
                try:
                    with (_REGISTRY_DIR / "unreclaimable-audit.log").open(
                            "a", encoding="utf-8") as f:
                        f.write(json.dumps(
                            {"shard": shard.name, **rec}, ensure_ascii=False)
                            + "\n")
                except OSError:
                    pass
                drop.add(idx)
                continue
            st = _delete(c, svc, dp)
            if st is None:
                # None = 이번 스윕 이미 2xx(dedup) 또는 blocked/transport 오류.
                # dedup은 gone이므로 프룬, 나머지는 unknown이라 보수적으로 유지.
                if (svc, dp, "[]") in _DELETED_THIS_SWEEP:
                    drop.add(idx)
                continue
            if 200 <= st < 300:
                deleted += 1
                drop.add(idx)
                print(f"  ledger-reclaim {svc} {dp} -> {st}")
            elif st == 404:
                drop.add(idx)  # already gone — confirmed reclaimed
            else:
                # 403/400 거절 — '없는 리소스'에 404 대신 403을 주는 서비스가
                # 있다 (apigateway 실측 2026-07-15: 삭제완료된 API의 GET/DELETE
                # 모두 403, LIST 0건 — 콘솔에도 없음). 404만 gone으로 치면 이
                # 유령 항목을 매 라운드·매 런 영원히 재시도한다 (오너: "console
                # 에는 자원 남은거 안보이는데 왜 삭제 시도를 하는거지?").
                # GET으로 실존 확인: 200이면 진짜 잔존(재시도 유지), 403/404/410
                # 이면 이 자격증명으로는 관측 불가 = gone 확정(프룬). 5xx/429는
                # unknown이라 보수적으로 유지.
                _g_st = None
                try:
                    _g_st = getattr(c.get(dp, service=svc), "status", None)
                except Exception:  # noqa: BLE001 — 확인 실패 = unknown, 유지
                    pass
                if _g_st in (403, 404, 410):
                    drop.add(idx)
                    print(f"  ledger-reclaim {svc} {dp} -> {st} "
                          f"(GET {_g_st}: 실존 안함 — gone 확정, 프룬)")
                else:
                    print(f"  ledger-reclaim {svc} {dp} -> {st} "
                          f"(GET {_g_st}: retry next round)")
        if not drop:
            continue
        kept = [r for i, r in enumerate(recs) if i not in drop]
        try:
            if not kept:
                shard.unlink()  # every record resolved → stop re-scanning
            else:
                shard.write_text(
                    "\n".join(json.dumps(r, ensure_ascii=False) for r in kept)
                    + "\n", encoding="utf-8")
                # rewrite resets mtime → the min-age gate would mistake this old
                # shard for an ACTIVE run's and skip retries for 15 min. Restore.
                os.utime(shard, (now, _mtime))
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
    # 오너 2026-07-16: "서비스와치 로그그룹은 그냥 리스트 조회해보고 다 지워 —
    # 부산물들." 전용 테스트 계정에서 로그그룹은 전부 플랫폼 자동 파생물
    # (/scp/<svc>/... — 이름에 우리 흔적이 없는 것도 우리 리소스의 부산물)이라
    # 이름/태그 게이트 없이 전량 삭제한다. Hard Rule 3의 name-guessing 금지는
    # '남의 것일 수 있는 자원'용인데, 이 컬렉션은 오너가 전량 부산물로 확정.
    _lg_picked = [it for it in _lg_listed if isinstance(it, dict)]
    if _lg_listed:
        print(f"  /v1/log-groups: {len(_lg_listed)} listed / "
              f"{len(_lg_picked)} deletable (부산물 전량 — 오너 2026-07-16)")
    # 로그그룹은 서로 독립(부산물) — 직렬로 돌면 50개 × (스트림 GET + DELETE
    # ×2) ≈ 수 분이 걸리던 게 이 패스의 최대 벽시계 낭비였다 (오너 2026-07-16
    # "여전히 너무 느리다"). 그룹별 작업을 스윕 풀로 병렬화한다; _STUCK /
    # _DELETE_ISSUED 는 GIL-원자적 set/dict 연산만 쓴다 (_delete 는 자체 락).
    def _reap_log_group(it) -> int:
        gid = it.get("id")
        if not gid:
            return 0
        if _converge_enabled() and str(gid) in _STUCK:
            return 0  # known-stuck (e.g. IAM-gated stream) — don't retry
        if _converge_enabled() and str(gid) in _DELETE_ISSUED:
            # we deleted it a prior round yet it persists → IAM-gated / un-reapable
            _STUCK[str(gid)] = ("log-group persists after delete (IAM-gated child "
                               "log-stream: needs the log-stream IAM action)")
            print(f"  stuck: {gid} ({_name_of(it)}) — log-group still listed "
                  f"after delete (IAM-gated child stream); not retrying")
            return 0
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
            return 1
        print(f"  log-group {gid} delete -> {st}")
        return 0

    deleted += sum(_map_parallel(_reap_log_group, _lg_picked))
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

    # 0b. 병렬 프리스캔 — 이 라운드의 (미수렴) 컬렉션 전체를 미리 나열해
    # 각 패스의 첫 _list_all이 소비할 캐시를 채운다. 직렬 나열 wall-time
    # 제거 (오너 2026-07-15). SKE 퓨처 뒤: 최장 극인 SKE teardown을 먼저
    # 굴려 놓고 나열을 겹친다.
    _prescan(c)

    # 0c. 블라스트 — 프리스캔 캐시의 소유 아이템 전체에 삭제를 동시 발사
    # (오너 2026-07-16). 독립 리소스는 여기서 끝나고, 아래 의존순서 패스들은
    # 409/특수-삭제 생존자만 상대한다 (캐시가 pruned 되어 재나열 없이 얇아짐).
    deleted += _blast_delete(c)

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
    # 2c-hex. 공유-이미지 생성용 임시 볼륨 (오너 실측 2026-07-16, run a690):
    # createsharingimage가 플랫폼 측에 순수 hex-이름(32자) 볼륨을 파생시키는데
    # 태그도 regr 토큰도 없어 위 게이트가 전부 놓친다. 전용 테스트 계정에서
    # '태그 없음 + 32-hex 이름 + 미부착(servers 비어 있음)'은 우리 테스트의
    # 부산물뿐(오너 확인: "어제 테스트에서 발생한 건임")이라 픽한다. 공유
    # 파이프라인이 잡고 있는 동안은 400 Volume.VolumeForSharingImageDelete 로
    # 거부되며(백엔드 플래그), 라운드/차기 런 재시도로 수렴한다.
    from core.registry import _tag_value as _tv, OWNER_KEY as _OK, OWNER as _OW
    import re as _re
    for it in _list_all(c, "virtualserver", "/v1/volumes"):
        if not isinstance(it, dict):
            continue
        vid, name = it.get("id"), str(it.get("name") or "")
        if (not vid or it.get("servers") or _tv(it, _OK) == _OW
                or not _re.fullmatch(r"[0-9a-f]{32}", name)):
            continue
        st = _delete(c, "virtualserver", f"/v1/volumes/{vid}")
        if _note_progress(st, it):
            deleted += 1
        else:
            print(f"  hex-orphan volume {name} ({vid}) delete -> {st} "
                  f"(sharing-image 파생)")

    # 2d. dbaas clusters (regr* per engine service) — MUST go before
    # subnets/vpcs. The nine engines are independent services (separate hosts,
    # separate collections), so list+issue runs per-engine on the sweep pool,
    # then ONE shared barrier until every issued cluster delete lands.
    def _dbaas_engine(svc):
        out = []
        try:
            for it in _select(c, svc, "/v1/clusters", name_prefixes=("regr",)):
                cid = it.get("id")
                if not cid:
                    continue
                # 이미 DELETING인 클러스터에 재-DELETE 금지 (2026-07-15 teardown
                # 최소화 감사): 라이프사이클이 방금 지운 클러스터(drain ~90분,
                # mariadb)가 여기 걸리는데, 재-DELETE가 2xx로 접수되면 아래
                # dbaas 배리어(900s)가 그 drain을 인질로 잡아 최대 15분을 선다.
                # _select가 이미 in-progress로 집계했으므로(라운드 유예 근거)
                # 스킵만 하면 된다 — TGW 패스의 동일 가드와 같은 규약.
                if _is_async_deleting(it):
                    continue
                if _delete(c, svc, f"/v1/clusters/{cid}"):
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
    # match_token (2026-07-15 오너 실측, 신규계정 첫 런): IGW create 바디에
    # name이 없어 플랫폼이 `IGW_<vpc이름>`으로 자동 명명 — "IGW_regrvpcnb…"는
    # regr* 프리픽스에 안 걸려 영구 스킵됐다(구 계정에도 IGW_regrvpcnb(ERROR)
    # 동일 잔존). 토큰 분해(IGW / regrvpcnb…)로 regr* 토큰을 잡는다 — 남의
    # IGW는 토큰이 자기 VPC명이라 안전. 파생 방화벽(FW_IGW_*)은 DELETE API가
    # 없어(카탈로그: GET/PUT뿐) IGW 삭제에 따라가는데, **rule이 남아 있으면
    # carrier IGW DELETE 자체가 409** (FW_IGW_regrvpcnb… 실측) — rule부터
    # 비운다 (_reap_igw_firewall_rules).
    for it in _select(c, "vpc", "/v1/internet-gateways",
                      name_prefixes=("regr", "zznet"), match_token=True):
        if not it.get("id"):
            continue
        _reap_igw_firewall_rules(c, it["id"])
        if _delete(c, "vpc", f"/v1/internet-gateways/{it['id']}"):
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


def _rm_ghost_report(client) -> None:
    """resourcemanager 인덱스의 '유령 레코드' 분리 보고 (오너 2026-07-16:
    "/v1/resources로 조회하면 남아 있다는데 실제 자원은 없는 경우가 있다 —
    버그로 기록해 두고 나중에 데이터 고치라고 해야 하고, 전체 정리할 때
    헷갈리지 않게 해야 함").

    스윕 종료 시 1회: 태그 인벤토리의 소유 항목을 실제 컬렉션 목록과 대조해
    (매핑 가능한 타입만) 실자원이 없는 항목을 GHOST로 표시 — 잔존 리포트와
    분리해 혼동을 막고, conformance finding(resourcemanager.stale-index-entry)
    으로 기록해 플랫폼 데이터 수정 요청의 증거를 남긴다. 삭제 시도는 하지
    않는다 (실자원이 없으므로). Fail-open: 판정 불가 항목은 GHOST로 단정하지
    않고 '미확인'으로 보고만."""
    if not _tag_scope_enabled():
        return
    inv = _tag_inventory(client)
    if not inv:
        return
    by_coll: dict = {}
    ghosts, unknown = [], []
    for it in inv:
        key = (str(it.get("service") or ""), str(it.get("resource_type") or ""))
        coll = _TYPE_TO_COLL.get(key)
        if coll is None:
            unknown.append(it)
            continue
        if coll not in by_coll:
            try:
                by_coll[coll] = {str(x.get("id")) for x in
                                 _list_all(client, coll[0], coll[1])
                                 if isinstance(x, dict)}
            except Exception:  # noqa: BLE001
                by_coll[coll] = None
        ids = by_coll[coll]
        if ids is None:
            unknown.append(it)
        elif str(it.get("id")) not in ids:
            ghosts.append((it, coll))
    if ghosts:
        print(f"  RM 유령 레코드 {len(ghosts)}건 — /v1/resources엔 있는데 "
              "실제 컬렉션엔 없음 (플랫폼 인덱스 버그, 삭제 시도 안 함):")
        for it, coll in ghosts[:10]:
            print(f"    ghost {it.get('service')}/{it.get('resource_type')} "
                  f"{it.get('resource_name')} id={str(it.get('id'))[:12]} "
                  f"(실컬렉션 {coll[1]}에 부재)")
        try:
            from core import results as _results
            for it, coll in ghosts:
                _results.record_finding(_results.Finding(
                    endpoint_key="GET /v1/resources",
                    rule_id="resourcemanager.stale-index-entry",
                    severity="yellow", source="runtime",
                    detail=(f"stale index entry: {it.get('service')}/"
                            f"{it.get('resource_type')} "
                            f"name={it.get('resource_name')!r} "
                            f"id={it.get('id')} srn={it.get('srn')} — real "
                            f"collection {coll[1]} does not contain it")))
        except Exception:  # noqa: BLE001
            pass
    if unknown:
        print(f"  RM 잔존 미확인 {len(unknown)}건 (타입 매핑/목록 불가 — "
              "유령 여부 판정 보류)")


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
        _cost_reset()
        _rnd_t0 = time.monotonic()
        reported = run_sweep(client)
        print(f"  [cost] round {rnd} wall {time.monotonic() - _rnd_t0:.0f}s",
              flush=True)
        _cost_report()
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
    # RM 인덱스 유령 레코드 분리 보고 — "잔존"과 헷갈리지 않게 (오너 2026-07-16)
    try:
        _rm_ghost_report(client)
    except Exception as exc:  # noqa: BLE001 — 보고 실패가 스윕을 깨면 안 됨
        print(f"  rm-ghost report error: {exc}")
    if _STUCK:
        print(f"--- {len(_STUCK)} owned item(s) could not be deleted "
              f"(reported, not forced) ---")
        for iid, reason in _STUCK.items():
            print(f"  stuck: {iid} ({reason})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
