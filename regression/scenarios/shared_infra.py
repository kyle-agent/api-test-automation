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
def shared_needs(only_ids=None) -> dict:
    """이 선택이 필요로 하는 공유 인프라를 ONE PASS로 스캔 — adopt 마커 기반.

    반환 키: ``main``(메인 공유 VPC+서브넷 — vpc/subnet adopter, 또는 메인 VPC에
    붙는 db/tgw/igw가 있으면 True) · ``db``(subnet#db) · ``net``(('a','b')의
    부분집합) · ``tgw`` · ``igw`` · ``any``(하나라도 필요).

    오너 2026-07-15: adopt 마커가 하나도 없는 self-create 전용 선택
    (networking-vpc-subnet 단독 런)이 공유 VPC+서브넷 2개까지 세우는 낭비 지적 —
    "subnet이 필요한(의존관계 있는) 시나리오면 공용 subnet을 만들어서 활용하면
    되는데, 모든 시나리오에 무조건 만드는 게 맞지는 않다". db-subnet 게이트
    (2026-07-08)와 같은 원리를 메인 공유 인프라 전체로 확장한 것.

    ``only_ids=None``이면 ``SCP_CRUD_IDS`` env(CLI 계약, 빈 값 = 전체 선택),
    아니면 그 id 집합으로 제한 (native 러너가 명시 선택을 넘긴다).
    Fail-open: 판정 오류 → 전부 필요(종전 동작 — 만들어도 무해, 스윕이 회수)."""
    import os
    try:
        if only_ids is None:
            only_ids = {x.strip() for x in
                        os.environ.get("SCP_CRUD_IDS", "").split(",")
                        if x.strip()}
        only = {str(x) for x in only_ids if str(x)}
        vpc_or_subnet = db = tgw = igw = False
        net: set = set()
        for lc in engine.active_lifecycles():
            if only and lc.get("id") not in only:
                continue
            for s in lc.get("steps", []):
                ad = s.get("adopt") or ""
                if ad in ("vpc", "subnet"):
                    vpc_or_subnet = True
                elif ad == "subnet#db":
                    db = True
                elif ad == "tgw":
                    tgw = True
                elif ad == "igw":
                    igw = True
                elif ad == "vpc#a":
                    net.add("a")
                elif ad == "vpc#b":
                    net.add("b")
        main = vpc_or_subnet or db or tgw or igw
        net_tags = tuple(sorted(net))
        return {"main": main, "db": db, "net": net_tags, "tgw": tgw,
                "igw": igw, "any": bool(main or net_tags)}
    except Exception:  # noqa: BLE001 — 스킵 최적화가 provision 실패 원인이 되면 안 됨
        return {"main": True, "db": True, "net": ("a", "b"), "tgw": True,
                "igw": True, "any": True}


def _needs_db_subnet() -> bool:
    """True iff this run's selection (``SCP_CRUD_IDS``; unset = every enabled
    lifecycle) contains at least one ``{"adopt": "subnet#db"}`` step. Without
    this check a VS-only interactive run serialized ~1-2 min behind a DB-lane
    subnet nothing would adopt (owner 2026-07-08: "db subnet 만들어지기 전까지
    아무것도 안 하고 있네.. 이게 맞나?"). Fail-open: any error → True (create
    the subnet; harmless, just slower). ``shared_needs()``의 db 필드 위임."""
    return shared_needs()["db"]


def _needed_net_vpc_tags() -> tuple:
    """이 선택이 필요로 하는 네트워킹 공유 VPC 태그 — vpc#a 어댑터가 있으면 'a',
    vpc#b 어댑터가 있으면 'b' (오너 2026-07-13 세분화). peering은 vpc#a·vpc#b를
    둘 다 가지므로 자동으로 ('a','b'); vip-nat만이면 ('a',); dc/fw만이면 ('b',).
    없는 것은 만들지 않아 슬롯(각 1)·프로비저닝 시간 낭비를 막는다. Fail-open:
    판정 오류 → ('a','b') (만들어도 무해, 약간 느릴 뿐). ``shared_needs()``의
    net 필드 위임."""
    return shared_needs()["net"]


def _needs_shared_tgw() -> bool:
    """선택에 ``{"adopt": "tgw"}`` 스텝이 하나라도 있으면 True — 공유 TGW 1개 필요
    (오너 2026-07-13). 없으면 공유 TGW는 순수 낭비(슬롯 1). Fail-open: 판정 오류
    → True (만들어도 무해, 잔재는 스윕 회수). ``shared_needs()``의 tgw 필드 위임."""
    return shared_needs()["tgw"]


def _needs_logsink() -> bool:
    """선택에 logsink 버킷(apitest-logsink)을 참조하는 스텝이 있으면 True —
    network-logging/loggingaudit 계열. OBS 버킷 생성은 SCP REST 카탈로그에
    없어(S3 프로토콜 전용, /v1/buckets CRUD는 Archive Storage) 시나리오 스텝으로
    못 만들므로, provision이 테스트 키로 ensure한다(멱등·상주 — 오너 2026-07-15
    "시나리오에 생성 step이 있으면 되는거 아닌가?"의 플랫폼-제약 절충).
    Fail-open: 판정 오류 → True (ensure는 멱등이라 무해)."""
    import os
    try:
        only = {x.strip() for x in os.environ.get("SCP_CRUD_IDS", "").split(",")
                if x.strip()}
        for lc in engine.active_lifecycles():
            if only and lc.get("id") not in only:
                continue
            for s in lc.get("steps", []):
                if "apitest-logsink" in str(s.get("json", "")):
                    return True
        return False
    except Exception:  # noqa: BLE001
        return True


def _needs_image_asset() -> bool:
    """선택에 ``{env:SCP_QCOW2_ASSET_URL}`` 토큰을 쓰는 스텝이 있으면 True —
    createimage/importimage용 상비 qcow2 자산 (오너 2026-07-15: 이미지는 git에
    상비, 최초 1회 버킷+객체 자동 생성). Fail-open: 판정 오류 → True (ensure는
    멱등이라 무해)."""
    import os
    try:
        only = {x.strip() for x in os.environ.get("SCP_CRUD_IDS", "").split(",")
                if x.strip()}
        for lc in engine.active_lifecycles():
            if only and lc.get("id") not in only:
                continue
            for s in lc.get("steps", []):
                if "SCP_QCOW2_ASSET_URL" in str(s.get("json", "")):
                    return True
        return False
    except Exception:  # noqa: BLE001
        return True


def _ensure_image_asset_env() -> None:
    """상비 qcow2 자산을 ensure하고 그 public URL을 SCP_QCOW2_ASSET_URL env로
    노출한다 — 엔진 _fill의 {env:...} 토큰이 소비. 실패는 best-effort (해당
    스텝이 4xx로 표면화, 원인은 stderr에)."""
    import contextlib
    import os
    try:
        with contextlib.redirect_stdout(sys.stderr):
            from core import oplog as _oplog
            url = _oplog.ensure_image_asset()
        if url:
            os.environ["SCP_QCOW2_ASSET_URL"] = url
        else:
            _eprint("[shared_infra] image asset ensure 실패(계속) — "
                    "createimage/importimage 스텝은 4xx로 표면화")
    except Exception as exc:  # noqa: BLE001
        _eprint(f"[shared_infra] image asset ensure 오류(계속): {exc}")


def _needs_shared_igw() -> bool:
    """선택에 ``{"adopt": "igw"}`` 스텝이 하나라도 있으면 True — 메인 공유 VPC에
    IGW 1개 상주 필요 (오너 2026-07-14). 없으면 공유 IGW는 순수 낭비. Fail-open:
    판정 오류 → True (만들어도 무해, 잔재는 스윕 회수). ``shared_needs()``의
    igw 필드 위임."""
    return shared_needs()["igw"]


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
    needs = shared_needs()   # ONE PASS: main/db/net/tgw/igw — 선택 기반 세분화
    # 기본 no-wait (2026-07-13, 오너 풀런에서 conftest 인라인 경로가 구식 대기를
    # 타는 것 관측 후 전 경로 기본 승격): 서브넷 create+track 후 ACTIVE 대기 없이
    # 반환 — run-543a 실측처럼 백엔드가 같은 VPC 서브넷 ACTIVE 전이를 직렬화해
    # head 대기가 4.3분이었다. ACTIVE는 adopt 시점의 engine._ensure_adopted_active
    # 게이트가 보장(라이브 검증 2026-07-13 5/5 pass). 구식 대기가 필요하면
    # SCP_PROVISION_SUBNET_WAIT=true 로 강제.
    nowait = os.environ.get("SCP_PROVISION_SUBNET_WAIT", "").strip().lower() != "true"
    # logsink 자동 부트스트랩 (새 계정 자기충족): stdout은 KEY=VALUE 계약이므로
    # ensure의 진단 print는 stderr로 돌린다. 실패해도 런은 계속 — 해당 시나리오가
    # 4xx로 표면화하고, 원인은 stderr 로그에 남는다. 공유-VPC 게이트보다 먼저:
    # logsink 참조와 adopt 마커는 독립이다.
    if _needs_logsink():
        try:
            with contextlib.redirect_stdout(sys.stderr):
                from core import oplog as _oplog
                _oplog.ensure_logsink()
        except Exception as exc:  # noqa: BLE001 — best-effort bootstrap
            _eprint(f"[shared_infra] logsink ensure 실패(계속): {exc}")
    if _needs_image_asset():
        _ensure_image_asset_env()
        _img_url = os.environ.get("SCP_QCOW2_ASSET_URL", "")
        if _img_url:
            # KEY=VALUE 계약: CI 경로에서 pytest 워커들이 $GITHUB_ENV로 받게.
            print(f"SCP_QCOW2_ASSET_URL={_img_url}")
    if not needs["any"]:
        # 오너 2026-07-15: adopt 마커 없는 self-create 전용 선택이 공유 인프라를
        # 세우는 낭비 — 아무도 adopt하지 않으면 프로비저닝 자체를 스킵한다.
        _eprint("[shared_infra] 선택에 공유 인프라 adopter가 없음(self-create "
                "전용) — 공유 VPC/서브넷 프로비저닝 스킵. NO SCP_SHARED_* emitted.")
        return 0
    try:
        with contextlib.redirect_stdout(sys.stderr):
            shared_ctx, _teardown = engine.provision_shared_vpc(
                client, cfg, need_db_subnet=needs["db"],
                wait_subnets_active=not nowait, need_net_vpcs=needs["net"],
                need_tgw=needs["tgw"], need_igw=needs["igw"])
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
    igw = shared_ctx.get("shared_igw_id")
    if igw:
        print(f"SCP_SHARED_IGW_ID={igw}")
    _eprint(f"[shared_infra] provisioned vpc={vpc_id} subnet={subnet_id} "
            f"db_subnet={db_subnet_id} net_a={net_a} net_b={net_b} tgw={tgw} "
            f"igw={igw}")
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
    # 공유 TGW/IGW (2026-07-15 실측 수리): 이 CLI 경로가 console2 run-end의 실제
    # teardown인데 종전엔 둘 다 안 읽어서 — 공유 TGW는 통째로 스윕行, 공유 IGW는
    # 메인 VPC DELETE를 409×5로 막고 VPC까지 스윕行이었다 (run-eac8 직렬 관측:
    # 서브넷 240s 대기 동안 TGW/net-A/B 미착수). engine closure의 병렬 체인과
    # 동일 구조로 수리: [main: 서브넷→IGW→VPC] · [tgw] · [net-a] · [net-b] 병렬.
    igw_id = os.environ.get(engine._ENV_SHARED_IGW, "").strip()
    tgw_id = os.environ.get(engine._ENV_SHARED_TGW, "").strip()
    if not any((vpc_id, subnet_id, db_subnet_id, net_a_id, net_b_id,
                igw_id, tgw_id)):
        _eprint("[shared_infra] no SCP_SHARED_VPC_ID / SCP_SHARED_SUBNET_ID set — "
                "nothing to tear down.")
        return 0
    # 병렬 teardown (2026-07-15): 독립 체인 4개 — [main: 서브넷→gone-wait→IGW→
    # VPC] · [tgw] · [net-a] · [net-b]. 체인 내부는 자식→부모 순서 그대로, 체인
    # 사이만 병렬 → wall ≈ 최장 체인 1개 (종전 직렬 합산 최악 ~8분+). 실패는
    # 어느 체인이든 스윕이 회수(종전과 동일한 백스톱).
    def _ladder(path: str, rid: str, label: str, attempts: int = 5) -> None:
        """DELETE + 409/4xx 사다리 (attempts×15s) — 자식의 비동기 소멸을 흡수."""
        for attempt in range(attempts):
            try:
                r = client.request("DELETE", f"{path}/{rid}", service="vpc")
                st = getattr(r, "status", None)
            except Exception as exc:
                _eprint(f"[shared_infra] {label} {rid} delete failed ({exc}); "
                        f"sweep will reclaim")
                return
            if st in (200, 202, 204, 404):
                _eprint(f"[shared_infra] {label} {rid} delete -> {st}")
                return
            _eprint(f"[shared_infra] {label} {rid} delete -> {st} "
                    f"(attempt {attempt + 1}/{attempts}); retrying in 15s")
            time.sleep(15)
        _eprint(f"[shared_infra] {label} {rid} still not deleted; "
                f"sweep will reclaim")

    def _chain_main() -> None:
        # subnets THEN vpc (children before parent). Subnet delete is ASYNC
        # (DELETING lingers 30s~3min); firing the VPC delete immediately gets a
        # 409 (관측 2026-07-12: 두 런 연속 VPC만 잔존 → 다음 런 admission 블록).
        issued: list[str] = []
        for sid, label in ((db_subnet_id, "DB subnet"), (subnet_id, "subnet")):
            if not sid:
                continue
            try:
                r = client.request("DELETE",
                                   f"{engine._SUBNET_CREATE_PATH}/{sid}",
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
                        pass  # read hiccup — next round decides
                pending = still
                if pending:
                    _eprint(f"[shared_infra] waiting subnet(s) gone: {pending}")
                    time.sleep(10)
        # 공유 IGW는 메인 VPC의 자식 — VPC보다 먼저 (attached면 VPC DELETE 409;
        # 종전엔 이 경로가 IGW를 몰라 VPC가 409×5 후 통째로 스윕行).
        if igw_id:
            _ladder(engine._IGW_CREATE_PATH, igw_id, "shared IGW")
        if vpc_id:
            _ladder(engine._VPC_CREATE_PATH, vpc_id, "shared VPC")

    def _chain_tgw() -> None:
        # account-level — VPC와 독립. adopter의 vpc-connection 잔재로 EDITING일
        # 수 있어 짧은 settle 대기 후 사다리 (engine closure와 동일 근거).
        deadline = time.time() + 300
        while time.time() < deadline:
            try:
                g = client.get(f"{engine._TGW_CREATE_PATH}/{tgw_id}",
                               service="vpc")
                if getattr(g, "status", None) == 404:
                    return
                stt = str((getattr(g, "body", None) or {})
                          .get("transit_gateway", {}).get("state", "")).upper()
                if stt in ("", "ACTIVE", "RUNNING", "CREATED", "AVAILABLE",
                           "ERROR"):
                    break
            except Exception:  # noqa: BLE001 — read hiccup; try the delete
                break
            _eprint(f"[shared_infra] shared TGW {tgw_id} state={stt} — "
                    f"settle 대기 후 삭제")
            time.sleep(10)
        _ladder(engine._TGW_CREATE_PATH, tgw_id, "shared TGW")

    chains = [("main", _chain_main)]
    if tgw_id:
        chains.append(("tgw", _chain_tgw))
    if net_a_id:
        chains.append(("net-a", lambda: _ladder(engine._VPC_CREATE_PATH,
                                                net_a_id, "shared net VPC A")))
    if net_b_id:
        chains.append(("net-b", lambda: _ladder(engine._VPC_CREATE_PATH,
                                                net_b_id, "shared net VPC B")))
    if len(chains) == 1:
        _chain_main()
        return 0
    from concurrent.futures import ThreadPoolExecutor
    with ThreadPoolExecutor(max_workers=len(chains),
                            thread_name_prefix="shared-td") as ex:
        futs = [(name, ex.submit(fn)) for name, fn in chains]
        for name, fut in futs:
            try:
                fut.result()
            except Exception as exc:  # noqa: BLE001 — sweep backstop per chain
                _eprint(f"[shared_infra] teardown chain {name} failed ({exc}); "
                        f"sweep will reclaim")
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
