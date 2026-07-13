"""CLI entrypoint for the shared VPC+subnet used by the parallel-adopt CRUD run.

Provisions ONE shared VPC + ONE shared subnet ONCE (out-of-band of pytest) so
that every pytest-xdist worker can ADOPT the same live infra via env ids, and
the few VPC-creating lifecycles still self-create within the VPC cap.

Subcommands (used by .github/workflows/api-test.yml):

  python -m regression.scenarios.shared_infra --provision
      Create the shared VPC+subnet and print ``SCP_SHARED_VPC_ID=..`` /
      ``SCP_SHARED_SUBNET_ID=..`` to STDOUT (for ``>> $GITHUB_ENV``); all human
      diagnostics go to STDERR so stdout stays machine-parseable. No-op (prints
      nothing) unless SCP_ALLOW_MUTATIONS=true.

  python -m regression.scenarios.shared_infra --teardown
      Delete the shared subnet THEN vpc named by SCP_SHARED_SUBNET_ID /
      SCP_SHARED_VPC_ID. No-op without SCP_ALLOW_DESTRUCTIVE=true.

  python -m regression.scenarios.shared_infra --print-filters
      Print ``ADOPT_K=..`` / ``VPC_CRUD_K=..`` / ``PARALLEL_K=..`` pytest ``-k``
      expressions derived from dependencies.json (adopt vs vpc-crud
      classification) to STDOUT (for ``>> $GITHUB_ENV``). Does NOT need a client.

Import-safe: the API client is only built inside the provision/teardown paths,
so importing this module (and --print-filters) never requires credentials.
"""
from __future__ import annotations

import argparse
import sys
import time

from regression.scenarios import engine


def _eprint(*a, **k):
    """Diagnostics -> stderr so stdout stays GITHUB_ENV-clean."""
    print(*a, file=sys.stderr, **k)


def _build_client():
    """Build the live API client (only here, so import + --print-filters are
    credential-free)."""
    from core.http_client import ApiClient
    from core.config import settings
    settings.require_credentials()
    return settings, ApiClient(settings)


# --------------------------------------------------------------------------- #
# filter derivation (data-driven from dependencies.json)
# --------------------------------------------------------------------------- #
def _crud_classification():
    """Return (adopt_ids, vpc_crud_ids) from dependencies.json, restricted to
    lifecycles that are actually ENABLED so the -k partition matches collection."""
    sched = engine.DEPENDENCIES.get("vpc_schedule", {})
    adopt = list(sched.get("adopt_lifecycles", []))
    vpc_crud = list(sched.get("vpc_crud_lifecycles", []))
    enabled = {lc["id"] for lc in engine.active_lifecycles()}
    adopt = [i for i in adopt if i in enabled]
    vpc_crud = [i for i in vpc_crud if i in enabled]
    return adopt, vpc_crud


def _k_or(ids):
    """Join lifecycle ids into a pytest -k OR expression. pytest matches each
    bare (hyphenated) term as a substring of the test node id; the enabled ids
    have no substring collisions (verified offline), so each term selects exactly
    its own test_crud_lifecycle[<id>] case."""
    return " or ".join(ids)


def print_filters():
    adopt, vpc_crud = _crud_classification()
    vpc_crud_k = _k_or(vpc_crud)
    adopt_k = _k_or(adopt)
    # PARALLEL_K selects everything that is NOT a vpc-crud lifecycle (i.e. the
    # adopt-class PLUS every other enabled CRUD lifecycle that touches no VPC).
    # This guarantees the two -k selections PARTITION all enabled CRUD cases:
    # VPC_CRUD_K and PARALLEL_K are exact complements.
    parallel_k = f"not ({vpc_crud_k})" if vpc_crud_k else ""
    print(f"ADOPT_K={adopt_k}")
    print(f"VPC_CRUD_K={vpc_crud_k}")
    print(f"PARALLEL_K={parallel_k}")
    _eprint(f"[shared_infra] {len(adopt)} adopt-class, {len(vpc_crud)} vpc-crud "
            f"lifecycle(s); PARALLEL_K is the complement of VPC_CRUD_K")
    return 0


# --------------------------------------------------------------------------- #
# provision / teardown
# --------------------------------------------------------------------------- #
def _needs_db_subnet() -> bool:
    """True iff this run's selection (``SCP_CRUD_IDS``; unset = every enabled
    lifecycle) contains at least one ``{"adopt": "subnet#db"}`` step. Without
    this check a VS-only interactive run serialized ~1-2 min behind a DB-lane
    subnet nothing would adopt (owner 2026-07-08: "db subnet 만들어지기 전까지
    아무것도 안 하고 있네.. 이게 맞나?"). Fail-open: any error → True (create
    the subnet; harmless, just slower)."""
    import os
    try:
        only = {x.strip() for x in os.environ.get("SCP_CRUD_IDS", "").split(",")
                if x.strip()}
        for lc in engine.active_lifecycles():
            if only and lc.get("id") not in only:
                continue
            if any((s.get("adopt") or "") == "subnet#db"
                   for s in lc.get("steps", [])):
                return True
        return False
    except Exception:  # noqa: BLE001 — 스킵 최적화가 provision 실패 원인이 되면 안 됨
        return True


def _needed_net_vpc_tags() -> tuple:
    """이 선택이 필요로 하는 네트워킹 공유 VPC 태그 — vpc#a 어댑터가 있으면 'a',
    vpc#b 어댑터가 있으면 'b' (오너 2026-07-13 세분화). peering은 vpc#a·vpc#b를
    둘 다 가지므로 자동으로 ('a','b'); vip-nat만이면 ('a',); dc/fw만이면 ('b',).
    없는 것은 만들지 않아 슬롯(각 1)·프로비저닝 시간 낭비를 막는다. Fail-open:
    판정 오류 → ('a','b') (만들어도 무해, 약간 느릴 뿐)."""
    import os
    try:
        only = {x.strip() for x in os.environ.get("SCP_CRUD_IDS", "").split(",")
                if x.strip()}
        tags = set()
        for lc in engine.active_lifecycles():
            if only and lc.get("id") not in only:
                continue
            for s in lc.get("steps", []):
                ad = s.get("adopt") or ""
                if ad == "vpc#a":
                    tags.add("a")
                elif ad == "vpc#b":
                    tags.add("b")
        return tuple(sorted(tags))
    except Exception:  # noqa: BLE001 — 스킵 최적화가 provision 실패 원인이 되면 안 됨
        return ("a", "b")


def _needs_shared_tgw() -> bool:
    """선택에 ``{"adopt": "tgw"}`` 스텝이 하나라도 있으면 True — 공유 TGW 1개 필요
    (오너 2026-07-13). 없으면 공유 TGW는 순수 낭비(슬롯 1). Fail-open: 판정 오류
    → True (만들어도 무해, 잔재는 스윕 회수)."""
    import os
    try:
        only = {x.strip() for x in os.environ.get("SCP_CRUD_IDS", "").split(",")
                if x.strip()}
        for lc in engine.active_lifecycles():
            if only and lc.get("id") not in only:
                continue
            if any((s.get("adopt") or "") == "tgw" for s in lc.get("steps", [])):
                return True
        return False
    except Exception:  # noqa: BLE001
        return True


def provision():
    cfg, client = _build_client()
    if not getattr(cfg, "allow_mutations", False):
        _eprint("[shared_infra] SCP_ALLOW_MUTATIONS not set — nothing to provision "
                "(adopters self-create); printing no env ids.")
        return 0
    # engine.provision_shared_vpc emits human diagnostics via plain print() ->
    # STDOUT. This entrypoint's STDOUT is redirected to $GITHUB_ENV by the
    # workflow, where ONLY well-formed `KEY=VALUE` lines are legal — a stray
    # "  shared VPC provisioned: ..." line makes the runner fail the step with
    # "Invalid format". So capture the engine's stdout onto STDERR and let ONLY
    # our explicit SCP_SHARED_*= lines below reach STDOUT.
    import contextlib
    import os
    shared_ctx = {}
    need_db = _needs_db_subnet()
    # 기본 no-wait (2026-07-13, 오너 풀런에서 conftest 인라인 경로가 구식 대기를
    # 타는 것 관측 후 전 경로 기본 승격): 서브넷 create+track 후 ACTIVE 대기 없이
    # 반환 — run-543a 실측처럼 백엔드가 같은 VPC 서브넷 ACTIVE 전이를 직렬화해
    # head 대기가 4.3분이었다. ACTIVE는 adopt 시점의 engine._ensure_adopted_active
    # 게이트가 보장(라이브 검증 2026-07-13 5/5 pass). 구식 대기가 필요하면
    # SCP_PROVISION_SUBNET_WAIT=true 로 강제.
    nowait = os.environ.get("SCP_PROVISION_SUBNET_WAIT", "").strip().lower() != "true"
    net_tags = _needed_net_vpc_tags()   # ('a','b') / ('a',) / ('b',) / () — 세분화
    need_tgw = _needs_shared_tgw()       # adopt:tgw 시나리오 있으면 공유 TGW 1개
    try:
        with contextlib.redirect_stdout(sys.stderr):
            shared_ctx, _teardown = engine.provision_shared_vpc(
                client, cfg, need_db_subnet=need_db,
                wait_subnets_active=not nowait, need_net_vpcs=net_tags,
                need_tgw=need_tgw)
    except Exception as exc:
        # Wave D root cause: provision_shared_vpc CREATED the VPC (slot won,
        # counts against the 5-cap) but a *post-create* step inside it raised —
        # e.g. the poll-to-ACTIVE or a subnet create blew up. Without this guard
        # the exception propagated out of provision(), the subprocess died, the
        # workflow's `> shared_ids.txt` was left EMPTY, `|| true` swallowed the
        # non-zero exit, the step still "concluded SUCCESS" (a VPC existed), but
        # NO `SCP_SHARED_VPC_ID=` line ever reached stdout → never reached
        # $GITHUB_ENV → every {"adopt":"vpc"} lifecycle hit the IB-049 guard and
        # skipped. Demote to a diagnostic so the (already-created) id, if any, is
        # still emitted below instead of being lost to a silent crash.
        _eprint(f"[shared_infra] provision_shared_vpc raised after entry "
                f"({type(exc).__name__}: {exc}); recovering any captured ids.")
    if not shared_ctx:
        # allow_mutations is ON here (checked above) yet we got nothing back. This
        # is NOT the benign "mutations off" no-op: either the create lost the
        # 5-VPC slot, or it succeeded async (202) without an id in the body. Make
        # the distinction loud in the runner log; adopters will self-create (or,
        # under xdist, IB-049-skip — which is the correct failure-path behavior).
        _eprint("[shared_infra] could not provision shared VPC (empty ctx with "
                "mutations ON — VPC-cap loss or async-create without id); adopters "
                "will self-create / IB-049-skip. NO SCP_SHARED_* emitted.")
        return 0
    vpc_id = shared_ctx.get("shared_vpc_id")
    subnet_id = shared_ctx.get("shared_subnet_id")
    db_subnet_id = shared_ctx.get("shared_db_subnet_id")
    # ONLY well-formed `KEY=VALUE` lines may reach stdout (the workflow appends
    # them to $GITHUB_ENV, which rejects anything else with "Invalid format").
    if vpc_id:
        print(f"SCP_SHARED_VPC_ID={vpc_id}")
    if subnet_id:
        print(f"SCP_SHARED_SUBNET_ID={subnet_id}")
    if db_subnet_id:
        print(f"SCP_SHARED_DB_SUBNET_ID={db_subnet_id}")
    net_a = shared_ctx.get("shared_net_vpc_a_id")
    net_b = shared_ctx.get("shared_net_vpc_b_id")
    if net_a:
        print(f"SCP_SHARED_NET_VPC_A_ID={net_a}")
        if shared_ctx.get("net_a_vpc_name"):
            print(f"SCP_SHARED_NET_VPC_A_NAME={shared_ctx['net_a_vpc_name']}")
    if net_b:
        print(f"SCP_SHARED_NET_VPC_B_ID={net_b}")
        if shared_ctx.get("net_b_vpc_name"):
            print(f"SCP_SHARED_NET_VPC_B_NAME={shared_ctx['net_b_vpc_name']}")
    tgw = shared_ctx.get("shared_tgw_id")
    if tgw:
        print(f"SCP_SHARED_TGW_ID={tgw}")
    _eprint(f"[shared_infra] provisioned vpc={vpc_id} subnet={subnet_id} "
            f"db_subnet={db_subnet_id} net_a={net_a} net_b={net_b} tgw={tgw}")
    if not vpc_id:
        # ctx came back truthy but with no vpc id (should not happen) — be loud so
        # the missing $GITHUB_ENV export is diagnosable from the runner log.
        _eprint("[shared_infra] WARNING: shared ctx has NO shared_vpc_id; "
                "nothing emitted for SCP_SHARED_VPC_ID.")
    return 0


def teardown():
    import os
    cfg, client = _build_client()
    if not getattr(cfg, "allow_destructive", False):
        _eprint("[shared_infra] SCP_ALLOW_DESTRUCTIVE not set — skipping teardown "
                "(tag-scoped reconciler sweep is the backstop).")
        return 0
    vpc_id = os.environ.get(engine._ENV_SHARED_VPC, "").strip()
    subnet_id = os.environ.get(engine._ENV_SHARED_SUBNET, "").strip()
    db_subnet_id = os.environ.get(engine._ENV_SHARED_DB_SUBNET, "").strip()
    net_a_id = os.environ.get(engine._ENV_SHARED_NET_VPC_A, "").strip()
    net_b_id = os.environ.get(engine._ENV_SHARED_NET_VPC_B, "").strip()
    if not any((vpc_id, subnet_id, db_subnet_id, net_a_id, net_b_id)):
        _eprint("[shared_infra] no SCP_SHARED_VPC_ID / SCP_SHARED_SUBNET_ID set — "
                "nothing to tear down.")
        return 0
    # subnets THEN vpc (children before parent). Subnet delete is ASYNC
    # (DELETING lingers 30s~3min); firing the VPC delete immediately gets a 409
    # and the shared VPC survives every aborted run (관측 2026-07-12: 두 런
    # 연속 VPC만 잔존 → 다음 런이 admission 큐에 걸림). So: issue both subnet
    # deletes, WAIT for them to be gone, then delete the VPC with a short
    # 409-retry ladder. Budget ≤ ~5min total; on timeout the sweep reclaims.
    issued: list[str] = []
    for sid, label in ((db_subnet_id, "DB subnet"), (subnet_id, "subnet")):
        if not sid:
            continue
        try:
            r = client.request("DELETE", f"{engine._SUBNET_CREATE_PATH}/{sid}",
                               service="vpc")
            st = getattr(r, "status", None)
            if st in (200, 202, 204, 404):
                issued.append(sid)
                _eprint(f"[shared_infra] shared {label} {sid} delete -> {st}")
            else:
                _eprint(f"[shared_infra] shared {label} {sid} delete -> {st}; "
                        f"sweep will reclaim")
        except Exception as exc:
            _eprint(f"[shared_infra] shared {label} {sid} delete failed "
                    f"({exc}); sweep will reclaim")
    if vpc_id and issued:
        deadline = time.time() + 240
        pending = list(issued)
        while pending and time.time() < deadline:
            still = []
            for sid in pending:
                try:
                    g = client.get(f"{engine._SUBNET_CREATE_PATH}/{sid}",
                                   service="vpc")
                    if getattr(g, "status", None) != 404:
                        still.append(sid)
                except Exception:
                    pass  # read hiccup — treat as gone next round decides
            pending = still
            if pending:
                _eprint(f"[shared_infra] waiting subnet(s) gone: {pending}")
                time.sleep(10)
    def _vpc_delete_ladder(vid: str, label: str) -> None:
        """VPC 삭제 + 409 사다리 (5×15s) — 자식의 비동기 소멸(DELETING 30s~3min)
        을 흡수한다. 실패는 스윕이 회수."""
        for attempt in range(5):
            try:
                r = client.request("DELETE", f"{engine._VPC_CREATE_PATH}/{vid}",
                                   service="vpc")
                st = getattr(r, "status", None)
            except Exception as exc:
                _eprint(f"[shared_infra] {label} {vid} delete failed ({exc}); "
                        f"sweep will reclaim")
                return
            if st in (200, 202, 204, 404):
                _eprint(f"[shared_infra] {label} {vid} delete -> {st}")
                return
            _eprint(f"[shared_infra] {label} {vid} delete -> {st} "
                    f"(attempt {attempt + 1}/5); retrying in 15s")
            time.sleep(15)
        _eprint(f"[shared_infra] {label} {vid} still not deleted; "
                f"sweep will reclaim")

    if vpc_id:
        _vpc_delete_ladder(vpc_id, "shared VPC")
    # net-VPC A/B: 자식(vip-nat 서브넷·IGW·DC)은 lifecycle이 지웠고, 비동기
    # 소멸 잔재는 사다리가 흡수한다.
    if net_a_id:
        _vpc_delete_ladder(net_a_id, "shared net VPC A")
    if net_b_id:
        _vpc_delete_ladder(net_b_id, "shared net VPC B")
    return 0


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__)
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument("--provision", action="store_true",
                   help="create shared VPC+subnet; print SCP_SHARED_* to stdout")
    g.add_argument("--teardown", action="store_true",
                   help="delete shared subnet then vpc named by SCP_SHARED_* env")
    g.add_argument("--print-filters", action="store_true",
                   help="print ADOPT_K/VPC_CRUD_K/PARALLEL_K pytest -k expressions")
    args = ap.parse_args(argv)
    if args.print_filters:
        return print_filters()
    if args.provision:
        return provision()
    if args.teardown:
        return teardown()
    return 1


if __name__ == "__main__":
    sys.exit(main())
